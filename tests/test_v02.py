"""0.2 regressions: actual observed failures, new workflows, and exactness cross-checks."""
from datetime import datetime, timedelta, timezone
import json
import random
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.models import (Project, Item, Resource, Rule, Quote, SolveOptions, ShippingRule, PaymentRecord, utcnow)
from app.optimizer import optimize
from app.pricing import generate_groups, simulate_group, simulate_project
from app.store import Store, ConflictError, parse_coupon_text
from app.workflow import record_payment, compare_strategies, diagnose
from app.refunds import RefundScenario, evaluate_refund

NOW = datetime(2026, 11, 7, 3, tzinfo=timezone.utc)


def item(id, base=10000, deposit=0, **kw):
    return Item(id=id,title=id,pricing_base_cents=base,pricing_base_evidence='测试用基数',
                deposit_paid_cents=deposit,deposit_confirmed=True,**kw)


def q(p, id, ids, cash=100, **kw):
    return Quote(**dict(dict(id=id,item_ids=ids,cash_due_cents=cash,source='manual',
                            ledger_complete=True,portable_state=True,captured_at=NOW,
                            context_fingerprint=p.fingerprint(),evidence_note='合成测试证据'),**kw))


def solve(p, **kw):
    return optimize(p, SolveOptions(starts_at=NOW,step_seconds=0,**kw))


def test_threshold_candidates_not_starved_by_pairs():
    p=Project(items=[item(str(i)) for i in range(28)],rules=[Rule(id='r',name='满500',threshold_cents=50000,amount_cents=10000,confirmed=True)])
    groups,info=generate_groups(p,160)
    assert any(len(g)==5 for g in groups)
    assert len(groups)==len(set(tuple(g) for g in groups))==160
    assert len([g for g in groups if len(g)==1])==28
    assert info['strategy_counts']['门槛邻近']>0
    assert groups==generate_groups(p,160)[0]


def test_candidates_do_not_infer_bases_from_display_prices():
    p=Project(items=[Item(id=str(i),title=str(i),display_price_cents=10000) for i in range(12)],
              rules=[Rule(id='r',name='满500',threshold_cents=50000,confirmed=True)])
    _, info=generate_groups(p,100)
    assert info['strategy_counts'].get('门槛邻近',0)==0


def test_age_checked_at_end_of_each_payment():
    p=Project(items=[item('a'),item('b')])
    p.quotes=[q(p,'a',['a']),q(p,'b',['b'],requires_done=['a'])]
    r=optimize(p,SolveOptions(starts_at=NOW,step_seconds=60,max_quote_age_seconds=90))
    assert r['status']=='no_feasible_plan'
    r=optimize(p,SolveOptions(starts_at=NOW,step_seconds=60,max_quote_age_seconds=120))
    assert r['cash_due_cents']==200


def test_resource_expiry_applies_even_without_quote_expiry():
    p=Project(items=[item('a')],resources=[Resource(id='c',name='c',ends_at=NOW+timedelta(seconds=10))])
    p.quotes=[q(p,'a',['a'],consumed={'c':1})]
    assert optimize(p,SolveOptions(starts_at=NOW,step_seconds=11))['status']=='no_feasible_plan'


def test_resource_activation_can_require_waiting():
    p=Project(items=[item('a')],resources=[Resource(id='c',name='c',starts_at=NOW+timedelta(seconds=30))])
    p.quotes=[q(p,'a',['a'],consumed={'c':1})]
    assert solve(p)['steps'][0]['earliest_start']==(NOW+timedelta(seconds=30)).isoformat()


def test_replay_does_not_allow_quote_not_yet_captured():
    p=Project(items=[item('a')]);p.quotes=[q(p,'a',['a'],captured_at=NOW+timedelta(minutes=2))]
    assert solve(p,replay=True)['accepted_quotes']==0


def test_deposit_per_line_not_offset_against_another_item():
    p=Project(items=[item('a',deposit=8000),item('b')],rules=[Rule(id='r',name='r',amount_cents=9000,
        item_ids=['a'],selection_mode='mandatory',confirmed=True)])
    qs,w=simulate_group(p,['a','b'],NOW)
    assert not qs and '单品' in w[0]


def test_confirmed_but_null_deposit_is_not_zero():
    a=item('a');a.deposit_paid_cents=None
    assert not simulate_group(Project(items=[a]),['a'],NOW)[0]


def test_quote_id_encoding_cannot_collide_at_pipe_separator():
    p=Project(items=[item(x) for x in ['a|b','c','a','b|c']])
    qa,_=simulate_group(p,['a|b','c'],NOW)
    qb,_=simulate_group(p,['a','b|c'],NOW)
    assert qa[0].id!=qb[0].id


def test_final_shipping_reverses_cheapest_goods_variant():
    p=Project(items=[item('a',shop_id='A'),item('b',shop_id='B')],rules=[
        Rule(id='x',name='a减20',amount_cents=2000,item_ids=['a'],exclusive_group='one',confirmed=True),
        Rule(id='y',name='b减10',amount_cents=1000,item_ids=['b'],exclusive_group='one',confirmed=True)],
        shipping_rules=[ShippingRule(shop_id='A',fee_cents=3000,free_threshold_cents=10000,
                                    confirmed=True,evidence_note='测试')])
    qs,_=simulate_group(p,['a','b'],NOW)
    assert min(x.cash_due_cents for x in qs)==19000
    assert min(qs,key=lambda x:x.cash_due_cents).applied_rules==['y']


def test_shipping_counts_once_per_shop_per_group():
    p=Project(items=[item('a',shop_id='A'),item('b',shop_id='A')],shipping_rules=[
        ShippingRule(shop_id='A',fee_cents=800,free_threshold_cents=15000,confirmed=True,evidence_note='测试')])
    assert simulate_group(p,['a'],NOW)[0][0].cash_due_cents==10800
    assert simulate_group(p,['a','b'],NOW)[0][0].cash_due_cents==20000


def test_unconfirmed_shipping_blocks_simulation():
    p=Project(items=[item('a',shop_id='A')],shipping_rules=[ShippingRule(shop_id='A',fee_cents=800)])
    qs,w=simulate_group(p,['a'],NOW)
    assert not qs and '未确认运费' in w[0]


def test_expired_resource_excluded_from_model():
    p=Project(items=[item('a')],resources=[Resource(id='c',name='c',ends_at=NOW)],
              rules=[Rule(id='r',name='r',amount_cents=3000,resource_id='c',confirmed=True)])
    assert simulate_group(p,['a'],NOW)[0][0].cash_due_cents==10000


def test_duplicate_prerequisites_and_self_dependency_rejected():
    for requires in [['a','a'],['b']]:
        with pytest.raises(ValueError):
            Quote(id='q',item_ids=['b'],cash_due_cents=10,requires_done=requires)


def test_resource_invalid_timezones_rejected():
    with pytest.raises(ValueError):Resource(id='r',name='r',ends_at=datetime(2027,1,1))
    with pytest.raises(ValueError):Resource(id='r',name='r',starts_at=NOW,ends_at=NOW)


def test_store_cas_detects_quote_only_changes(tmp_path):
    store=Store(tmp_path);p=Project(items=[item('a')]);store.save(p)
    original,rev=store.snapshot()
    current=store.load();current.quotes=[q(current,'a',['a'])];store.save(current)
    assert current.fingerprint()==original.fingerprint()
    with pytest.raises(ConflictError):store.save(original,rev)
    assert store.load().quotes[0].id=='a'


def test_store_rotates_and_restores_backups(tmp_path):
    store=Store(tmp_path)
    for i in range(23):store.save(Project(name=str(i)))
    assert len(store.list_backups())==20
    name=store.list_backups()[0]['name']
    store.restore(name,store.snapshot()[1])
    assert store.load().name=='21'
    with pytest.raises(ValueError):store.restore('../project.json')


def test_corrupt_project_recovery_preserves_damaged_file(tmp_path):
    store=Store(tmp_path);store.save(Project(name='before'));store.save(Project(name='after'))
    name=store.list_backups()[0]['name'];store.path.write_text('{broken')
    with pytest.raises(ValueError):store.load()
    store.restore(name,expected_storage_hash=store.storage_hash())
    assert store.load().name=='before'
    assert len(list(tmp_path.glob('corrupt-project-*')))==1


def test_restore_rejects_changed_storage_hash(tmp_path):
    store=Store(tmp_path);store.save(Project(name='a'));store.save(Project(name='b'))
    name=store.list_backups()[0]['name'];h=store.storage_hash();store.save(Project(name='c'))
    with pytest.raises(ConflictError):store.restore(name,expected_storage_hash=h)


def test_record_payment_updates_resources_and_invalidates_old_quotes():
    p=Project(items=[item('a'),item('b')],resources=[Resource(id='c',name='c')])
    p.quotes=[q(p,'a',['a'],consumed={'c':1}),q(p,'b',['b'])]
    old_fingerprint=p.fingerprint()
    r=PaymentRecord(id='paid-a',item_ids=['a'],cash_paid_cents=100,consumed={'c':1},evidence_note='实付已核对',paid_at=NOW)
    new=record_payment(p,r)
    assert p.resources[0].available==1 and new.resources[0].available==0
    assert new.paid_item_ids()=={'a'} and new.fingerprint()!=old_fingerprint
    assert solve(new)['cash_due_cents'] is None
    new.quotes.append(q(new,'b-current',['b'],requires_done=['a']))
    result=solve(new)
    assert result['cash_due_cents']==100 and result['recorded_payments_cash_cents']==100
    assert generate_groups(new)[0]==[['b']]


def test_payment_is_idempotent_and_double_purchase_is_rejected():
    p=Project(items=[item('a')],resources=[Resource(id='r',name='r')])
    record=PaymentRecord(id='p',item_ids=['a'],cash_paid_cents=100,consumed={'r':1},evidence_note='actual')
    p=record_payment(p,record)
    assert record_payment(p,record)==p
    with pytest.raises(ValueError):record_payment(p,record.model_copy(update={'id':'p2'}))
    with pytest.raises(ValueError):record_payment(p,record.model_copy(update={'cash_paid_cents':99}))


def test_payment_cannot_overdraw_or_reference_unknown_resources():
    p=Project(items=[item('a')],resources=[Resource(id='r',name='r',available=0)])
    for consumed in [{'r':1},{'unknown':1}]:
        with pytest.raises(ValueError):record_payment(p,PaymentRecord(id='p',item_ids=['a'],cash_paid_cents=100,consumed=consumed,evidence_note='actual'))


def test_completed_project_needs_zero_future_cash():
    p=record_payment(Project(items=[item('a')]),PaymentRecord(id='p',item_ids=['a'],cash_paid_cents=123,evidence_note='actual'))
    assert solve(p)['cash_due_cents']==0 and generate_groups(p)[0]==[]


def test_compare_singleton_baseline_does_not_reuse_coupon():
    p=Project(items=[item('a'),item('b')],resources=[Resource(id='c',name='c')])
    p.quotes=[q(p,'ac',['a'],50,consumed={'c':1}),q(p,'bc',['b'],50,consumed={'c':1}),
              q(p,'a',['a'],100),q(p,'b',['b'],100),q(p,'ab',['a','b'],140)]
    result=compare_strategies(p,SolveOptions(starts_at=NOW,step_seconds=0))
    rows={x['key']:x['result'] for x in result['strategies']}
    assert rows['single']['cash_due_cents']==150
    assert rows['merged']['cash_due_cents']==140 and rows['optimized']['cash_due_cents']==140


def test_diagnose_history_does_not_pretend_data_ready():
    p=Project.model_validate_json((Path(__file__).parents[1]/'examples/historical.json').read_text())
    result=diagnose(p)
    assert result['remaining_required']==16
    assert {'missing_base','missing_checkout'} <= {x['code'] for x in result['issues']}


def test_draft_coupon_parser_keeps_repeat_and_cap_semantics():
    drafts=parse_coupon_text('每满300减50；满1000减125；9折最高减50元')
    assert len(drafts)==3
    assert drafts[0]['kind']=='repeat_threshold'
    assert drafts[1]['amount_cents']==12500
    assert drafts[2]['discount_bps']==1000 and drafts[2]['cap_cents']==5000
    assert not any(x['confirmed'] for x in drafts)


def test_refund_comparison_can_show_negative_savings():
    r=evaluate_refund(RefundScenario(cash_paid_total_cents=90000,cash_refund_cents=9000,
                       return_shipping_cents=2000,baseline_keep_economic_cost_cents=80000))
    assert r['saving_vs_baseline_cents']==-3000 and r['status']=='scenario_only'


def test_static_fast_path_and_dynamic_fallback():
    p=Project(items=[item('a'),item('b')]);p.quotes=[q(p,'a',['a']),q(p,'b',['b'])]
    assert solve(p)['search_strategy']=='canonical_partition'
    p.quotes[1].requires_done=['a']
    assert solve(p)['search_strategy']=='ordered_state_search'


@pytest.mark.parametrize('seed',range(30))
def test_state_search_matches_independent_timed_exhaustion(seed):
    rng=random.Random(seed)
    p=Project(items=[item(str(i)) for i in range(4)],resources=[Resource(id='c',name='c')])
    for mask in range(1,16):
        ids=[str(i) for i in range(4) if mask>>i&1]
        p.quotes.append(q(p,str(mask),ids,rng.randint(1,1000),consumed={'c':1} if rng.random()<.3 else {},
                          valid_until=NOW+timedelta(seconds=rng.choice([20,40,100]))))
    def brute(done,bal,elapsed):
        if len(done)==4:return 0
        best=10**9
        for quote in p.quotes:
            group=set(quote.item_ids);used=quote.consumed.get('c',0)
            if group&done or used>bal or NOW+timedelta(seconds=elapsed+10)>quote.valid_until:continue
            best=min(best,quote.cash_due_cents+brute(done|group,bal-used,elapsed+10))
        return best
    expected=brute(set(),1,0)
    actual=optimize(p,SolveOptions(starts_at=NOW,step_seconds=10))
    assert actual['cash_due_cents']==(expected if expected<10**9 else None)


@pytest.fixture
def local_api(tmp_path,monkeypatch):
    import app.main as main
    monkeypatch.setattr(main,'store',Store(tmp_path))
    return TestClient(main.app), {'x-local-token':main.TOKEN}, main


def put(client,headers,p):
    h={**headers,'x-project-revision':client.get('/api/project',headers=headers).json()['revision']}
    return client.put('/api/project',headers=h,json=p.model_dump(mode='json'))


def test_api_requires_version_and_rejects_stale_tabs(local_api):
    client,h,main=local_api
    assert client.put('/api/project',headers=h,json={}).status_code==428
    rev=client.get('/api/project',headers=h).json()['revision']
    assert put(client,h,Project(name='new')).status_code==200
    r=client.put('/api/project',headers={**h,'x-project-revision':rev},json={})
    assert r.status_code==409 and main.store.load().name=='new'


def test_simulate_does_not_overwrite_concurrent_new_quote(local_api,monkeypatch):
    client,h,main=local_api
    p=Project(items=[item('a')]);put(client,h,p)
    real=main.simulate_project
    def concurrent(*args):
        old,argsrev=main.store.snapshot()
        old.quotes.append(q(old,'new-quote',['a'],captured_at=utcnow()))
        main.store.save(old,argsrev)
        return real(*args)
    monkeypatch.setattr(main,'simulate_project',concurrent)
    r=client.post('/api/simulate',headers=h,json={'budget':10})
    assert r.status_code==409 and main.store.load().quotes[0].id=='new-quote'


def test_new_live_quote_requires_observation_ack(local_api):
    client,h,main=local_api
    p=Project(items=[item('a')]);put(client,h,p)
    d=client.get('/api/project',headers=h).json()
    quote=q(p,'a',['a'],captured_at=utcnow())
    body={'quote':quote.model_dump(mode='json'),'expected_fingerprint':d['fingerprint']}
    headers={**h,'x-project-revision':d['revision']}
    assert client.post('/api/quotes',headers=headers,json=body).status_code==400
    body['checkout_observed']=True
    assert client.post('/api/quotes',headers=headers,json=body).status_code==200


def test_api_payment_requires_confirmation_and_is_idempotent(local_api):
    client,h,main=local_api
    p=Project(items=[item('a')],resources=[Resource(id='c',name='c')]);put(client,h,p)
    record=PaymentRecord(id='p',item_ids=['a'],cash_paid_cents=100,consumed={'c':1},evidence_note='checked')
    d=client.get('/api/project',headers=h).json();headers={**h,'x-project-revision':d['revision']}
    body={'record':record.model_dump(mode='json')}
    assert client.post('/api/payments/record',headers=headers,json=body).status_code==400
    body['actual_payment_confirmed']=True
    assert client.post('/api/payments/record',headers=headers,json=body).status_code==200
    r=client.post('/api/payments/record',headers=headers,json=body)
    assert r.status_code==200 and r.json()['idempotent'] is True
    assert main.store.load().resources[0].available==0


def test_api_restore_after_corruption(local_api):
    client,h,main=local_api
    put(client,h,Project(name='before'));put(client,h,Project(name='after'))
    main.store.path.write_text('broken')
    data=client.get('/api/backups',headers=h).json()
    r=client.post('/api/backups/restore',headers=h,json={'name':data['backups'][0]['name'],
            'acknowledged':True,'expected_storage_hash':data['storage_hash']})
    assert r.status_code==200 and main.store.load().name=='before'


def test_no_solution_with_cutoff_is_not_claimed_infeasible(monkeypatch):
    import app.optimizer as module
    p=Project(items=[item('a')]);p.quotes=[q(p,'a',['a'])]
    calls=[0]
    def clock():
        calls[0]+=1
        return 0 if calls[0]==1 else 100
    monkeypatch.setattr(module.time,'monotonic',clock)
    result=solve(p,max_seconds=.05)
    assert result['status']=='search_incomplete' and not result['search_exhausted']


def test_diverse_threshold_groups_reach_synthetic_28_item_optimum():
    p=Project(items=[item(str(i)) for i in range(28)],rules=[Rule(id='r',name='per order threshold',
            threshold_cents=50000,amount_cents=10000,confirmed=True)])
    p,info=simulate_project(p,160,NOW)
    assert info['quote_count']<=25000
    assert solve(p,mode='model')['cash_due_cents']==230000


def test_upgrade_preserves_user_data_and_environment(tmp_path):
    from scripts.upgrade import upgrade
    target=tmp_path/'old';(target/'app').mkdir(parents=True)
    (target/'app'/'main.py').write_text('old')
    (target/'run.py').write_text('old')
    for folder in ('.data','.venv'):
        (target/folder).mkdir();(target/folder/'keep.txt').write_text('keep')
    (target/'.env').write_text('USER_CONFIGURATION=keep')
    upgrade(target)
    for folder in ('.data','.venv'):
        assert (target/folder/'keep.txt').read_text()=='keep'
    assert (target/'.env').read_text()=='USER_CONFIGURATION=keep'
    assert 'version=\'0.2.0\'' in (target/'app'/'main.py').read_text()


def test_upgrade_rejects_symlink_destination_before_copy(tmp_path):
    from scripts.upgrade import upgrade
    target=tmp_path/'old';(target/'app').mkdir(parents=True)
    (target/'app'/'main.py').write_text('old');(target/'run.py').write_text('old')
    elsewhere=tmp_path/'elsewhere';elsewhere.mkdir()
    (target/'app'/'static').symlink_to(elsewhere,target_is_directory=True)
    with pytest.raises(ValueError):upgrade(target)
    assert (target/'app'/'main.py').read_text()=='old'


def test_bootstrap_skips_pip_when_dependency_versions_match(tmp_path,monkeypatch):
    import scripts.bootstrap as bootstrap
    from types import SimpleNamespace
    monkeypatch.setattr(bootstrap,'ROOT',tmp_path)
    exe=tmp_path/'.venv'/('Scripts/python.exe' if bootstrap.os.name=='nt' else 'bin/python')
    exe.parent.mkdir(parents=True);exe.write_text('placeholder')
    (tmp_path/'requirements.txt').write_text('fastapi==0.128.2\n')
    commands=[]
    def run(command,**kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bootstrap.subprocess,'run',run)
    assert bootstrap.ensure_environment()==exe
    assert len(commands)==1 and 'pip' not in commands[0]
