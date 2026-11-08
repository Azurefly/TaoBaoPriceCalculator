import asyncio
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.models import Item, Rule, Project, Resource, SolveOptions, DiscountTier, utcnow
from app.pricing import simulate_group, simulate_project, generate_groups
from app.promotion_engine import explain_group
from app.optimizer import optimize
from app.shopping import demo_project
from app.cart import normalized_capture, filter_rows, imported_project, ImportInput


def item(id, price=10000, shop='s1', qty=1):
    return Item(id=id,title=id,shop_id=shop,quantity=qty,pricing_base_cents=price,
                pricing_base_evidence='test',deposit_paid_cents=0,deposit_confirmed=True)


def cost(p, ids=None):
    q,w=simulate_group(p,ids or [x.id for x in p.items]); assert q,w
    return min(q,key=lambda x:x.cash_due_cents)


def rule(**kwargs):
    return Rule(id=kwargs.pop('id','r'),name='test',confirmed=True,source_note='test',**kwargs)


def test_shop_threshold_does_not_count_other_shop():
    p=Project(items=[item('a',6000),item('b',6000,'s2')],rules=[rule(shop_ids=['s1'],threshold_cents=10000,amount_cents=2000)])
    assert cost(p).cash_due_cents==12000


def test_specified_subset_discount_never_reduces_excluded_items():
    p=Project(items=[item('a'),item('b')],rules=[rule(item_ids=['a'],amount_cents=3000)])
    q=cost(p);assert q.cash_due_cents==17000 and q.discount_breakdown[0].allocations=={'a':3000}


def test_combo_requires_all_items_and_quantities():
    r=rule(item_ids=['b'],qualifying_item_ids=['a','b'],required_item_quantities={'a':2,'b':1},threshold_cents=20000,amount_cents=4000)
    p=Project(items=[item('a'),item('b')],rules=[r]);assert cost(p).cash_due_cents==20000
    p.items[0].quantity=2;assert cost(p).cash_due_cents==16000
    assert cost(p,['b']).cash_due_cents==10000


def test_distinct_sku_is_not_total_units():
    p=Project(items=[item('a',30000,qty=3)],rules=[rule(min_quantity=3,min_distinct_items=2,amount_cents=5000)])
    assert cost(p).cash_due_cents==30000


def test_excluded_rows_cannot_qualify():
    p=Project(items=[item('a',6000),item('b',6000)],rules=[rule(excluded_item_ids=['b'],threshold_cents=10000,amount_cents=2000)])
    assert cost(p).cash_due_cents==12000


def test_tiers_choose_valid_best_discount():
    p=Project(items=[item('a',45000)],rules=[rule(kind='tiered',tiers=[DiscountTier(threshold_cents=20000,amount_cents=2000),DiscountTier(threshold_cents=40000,amount_cents=6000)])])
    assert cost(p).cash_due_cents==39000


def test_bundle_price_and_no_negative_discount():
    p=Project(items=[item('a'),item('b')],rules=[rule(kind='bundle_price',item_ids=['a','b'],required_item_quantities={'a':1,'b':1},amount_cents=15000)])
    assert cost(p).cash_due_cents==15000
    p.rules[0].amount_cents=25000;assert cost(p).cash_due_cents==20000


def test_automatic_best_is_not_greedy_rule_order():
    a=rule(id='a-small',amount_cents=1000,exclusive_group='shop:s1',exclusive_scope='item',selection_mode='best')
    b=rule(id='z-large',kind='percent',discount_bps=1000,min_quantity=2,exclusive_group='shop:s1',exclusive_scope='item',selection_mode='best')
    p=Project(items=[item('x',20000,qty=2)],rules=[a,b]);q=cost(p)
    assert q.cash_due_cents==18000 and q.applied_rules==['z-large']


def test_disjoint_item_level_shop_promotions_can_coexist():
    p=Project(items=[item('a'),item('b')],rules=[rule(id='a',item_ids=['a'],amount_cents=1000,selection_mode='best',exclusive_group='same-shop',exclusive_scope='item'),rule(id='b',item_ids=['b'],amount_cents=2000,selection_mode='best',exclusive_group='same-shop',exclusive_scope='item')])
    assert cost(p).cash_due_cents==17000


def test_overlapping_item_discounts_cannot_stack():
    p=Project(items=[item('a'),item('b')],rules=[rule(id='a',item_ids=['a','b'],amount_cents=1000,selection_mode='best',exclusive_group='g',exclusive_scope='item'),rule(id='b',item_ids=['b'],amount_cents=2000,selection_mode='best',exclusive_group='g',exclusive_scope='item')])
    assert cost(p).cash_due_cents==18000


def test_platform_threshold_uses_after_shop_price():
    p=Project(items=[item('a',10500)],rules=[rule(id='shop',phase=20,amount_cents=1000,selection_mode='mandatory'),rule(id='platform',phase=40,threshold_cents=10000,amount_cents=2000,threshold_basis='remaining')])
    assert cost(p).cash_due_cents==9500


def test_best_fallback_after_coupon_exhaustion_changes_optimum():
    p=Project(items=[item('a',20000),item('b',20000)],resources=[Resource(id='c',name='single use')],rules=[rule(id='coupon',resource_id='c',amount_cents=2000,selection_mode='best',exclusive_group='g'),rule(id='auto',amount_cents=1000,selection_mode='best',exclusive_group='g')])
    now=utcnow();sim,_=simulate_project(p,100,now)
    assert any(q.requires_less_than=={'c':1} for q in sim.quotes)
    result=optimize(sim,SolveOptions(mode='model',starts_at=now,step_seconds=0))
    assert result['cash_due_cents']==37000
    assert sum(s['consumed'].get('c',0) for s in result['steps'])==1
    assert result['steps'][0]['consumed']=={'c':1}


def test_less_than_guard_not_usable_with_high_balance():
    p=Project(items=[item('a')],resources=[Resource(id='c',name='c',available=1)])
    from app.models import Quote
    p.quotes=[Quote(id='q',item_ids=['a'],cash_due_cents=1,requires_less_than={'c':1},source='model',ledger_complete=True,portable_state=True,context_fingerprint=p.fingerprint())]
    assert optimize(p,SolveOptions(mode='model'))['status']=='no_feasible_plan'


def test_guard_references_must_exist():
    from app.models import Quote
    with pytest.raises(ValueError):Project(items=[item('a')],quotes=[Quote(id='q',item_ids=['a'],cash_due_cents=1,requires_less_than={'missing':1})])


def test_unknown_display_price_never_becomes_base():
    p=Project(items=[Item(id='a',title='a',display_price_cents=1)],rules=[rule(amount_cents=5000)])
    q,w=simulate_group(p,['a']);assert not q and '计价基数' in w[0]


def test_rounding_allocations_sum_to_discount():
    p=Project(items=[item('a',103),item('b',107),item('c',109)],rules=[rule(amount_cents=101)])
    q=cost(p);assert q.cash_due_cents==218
    assert sum(q.discount_breakdown[0].allocations.values())==101


def test_outside_channel_or_expired_does_not_apply():
    now=utcnow();p=Project(items=[item('a')],rules=[rule(channels=['app'],amount_cents=2000)])
    assert cost(p).cash_due_cents==10000
    p.rules[0]=rule(ends_at=now-timedelta(seconds=1),amount_cents=2000);assert cost(p).cash_due_cents==10000


@pytest.mark.parametrize('quantity',[0,-1,1000,True,1.5])
def test_invalid_required_quantity_rejected(quantity):
    with pytest.raises(ValueError):rule(required_item_quantities={'a':quantity})


def test_best_group_cannot_mix_phases_or_modes():
    with pytest.raises(ValueError):Project(rules=[rule(id='a',phase=20,selection_mode='best',exclusive_group='g'),rule(id='b',phase=30,selection_mode='best',exclusive_group='g')])
    with pytest.raises(ValueError):Project(rules=[rule(id='a',selection_mode='best',exclusive_group='g'),rule(id='b',selection_mode='optional',exclusive_group='g')])


def test_candidate_generator_seeds_explicit_combos():
    p=Project(items=[item(str(i)) for i in range(10)],rules=[rule(required_item_quantities={'1':1,'7':1,'9':1},amount_cents=5000)])
    groups,meta=generate_groups(p,60)
    assert {'1','7','9'} in [set(g) for g in groups]


def test_demo_has_all_layers_and_known_result():
    p=demo_project();q=cost(p)
    assert q.cash_due_cents==56000
    assert set(q.applied_rules)=={'bundle-r','serum-r','platform-r'}
    assert sum(d.discount_cents for d in q.discount_breakdown)==20000


def snapshot():
    return normalized_capture({'items':[{'line_id':'a','title':'A','shop_id':'s1','shop_name':'店1','quantity':2,'display_price_text':'¥12.00','price_scope':'unit','group_ids':['g1'],'group_names':['自定义组']},
        {'line_id':'b','title':'B','shop_id':'s2','quantity':1,'display_price_text':'¥20','price_scope':'line','group_ids':['g1']}],
        'groups':[{'id':'g1','name':'自定义组','active':True},{'id':'g2','name':'另一组','active':False}],
        'active_group_id':'g1'},'scope1')


def test_cart_display_unit_is_line_total_but_not_base():
    s=snapshot();assert s['items'][0]['display_price_cents']==2400
    assert s['items'][0]['pricing_base_cents'] is None and s['complete'] is False


def test_unknown_price_scope_not_guessed():
    s=normalized_capture({'items':[{'line_id':'x','title':'X','quantity':1,'display_price_text':'88','price_scope':'unknown'}]},'c')
    assert s['items'][0]['display_price_cents'] is None


def test_cart_group_provenance_and_no_fallback():
    s=snapshot();assert len(filter_rows(s,'shop:s1'))==1
    assert len(filter_rows(s,'taobao:g1'))==2
    with pytest.raises(ValueError):filter_rows(s,'taobao:g2')
    with pytest.raises(ValueError):filter_rows(s,'shop:missing')


def test_import_only_selected_group_and_idempotent():
    s=snapshot();body=ImportInput(snapshot_id=s['snapshot_id'],line_ids=['a'],group_id='shop:s1',account_confirmed=True)
    p=imported_project(Project(),s,body)
    assert len(p.items)==1 and p.items[0].title=='A' and p.items[0].quantity==2
    assert p.items[0].deposit_paid_cents is None
    assert imported_project(p,s,body)==p
    with pytest.raises(ValueError):imported_project(Project(),s,body.model_copy(update={'line_ids':['b']}))


def test_import_does_not_silently_truncate():
    s=snapshot();p=Project(items=[item(str(i)) for i in range(28)])
    with pytest.raises(ValueError):imported_project(p,s,ImportInput(snapshot_id=s['snapshot_id'],line_ids=['a'],account_confirmed=True))


def test_ambiguous_cart_rows_prevent_import():
    s=snapshot();s['errors']=['duplicate'];
    with pytest.raises(ValueError):imported_project(Project(),s,ImportInput(snapshot_id=s['snapshot_id'],line_ids=['a'],account_confirmed=True))


def test_invalid_quantity_row_not_importable():
    for q in (None,True,0,1000):
        s=normalized_capture({'items':[{'line_id':'x','title':'X','quantity':q}]},'c')
        assert s['items'][0]['can_import'] is False


def test_import_requires_account_confirmation():
    s=snapshot()
    with pytest.raises(ValueError):imported_project(Project(),s,ImportInput(snapshot_id=s['snapshot_id'],line_ids=['a']))


def test_quantity_update_invalidates_base_not_restores_price():
    s=snapshot();b=ImportInput(snapshot_id=s['snapshot_id'],line_ids=['a'],account_confirmed=True)
    p=imported_project(Project(),s,b);p.items[0].pricing_base_cents=4000;p.items[0].pricing_base_evidence='checked';p.items[0].deposit_confirmed=True;p.items[0].deposit_paid_cents=0
    s['items'][0]['quantity']=3
    updated=imported_project(p,s,b.model_copy(update={'replace_existing':True}))
    assert updated.items[0].pricing_base_cents is None and not updated.items[0].deposit_confirmed


@pytest.fixture
def client(tmp_path,monkeypatch):
    from app import main
    from app.store import Store
    # Routers capture the original Store, so replace only its directory via a
    # fresh isolated app instance assembled with the same middleware contract.
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from app.shopping import create_shopping_router
    from app.adapters.browser import BrowserAdapter
    store=Store(tmp_path)
    app=FastAPI();app.include_router(create_shopping_router(store,BrowserAdapter(tmp_path),Path(__file__).resolve().parents[1]))
    @app.exception_handler(ValueError)
    async def error(request,exc):return JSONResponse({'detail':str(exc)},status_code=400)
    return TestClient(app),store


def test_demo_and_estimate_never_write_store(client):
    c,store=client;p=demo_project();store.save(p)
    before=store.storage_hash();data=c.post('/api/shopping/demo').json()
    assert data['demo'] and data['comparison']['strategies'][0]['result']['cash_due_cents']==56000
    assert store.storage_hash()==before
    result=c.post('/api/promotions/estimate').json();assert result['model_only']
    assert store.storage_hash()==before


def test_promotion_api_requires_evidence_and_revision(client):
    c,store=client;store.save(Project(items=[item('a')]))
    data={'rule':rule(amount_cents=1000).model_dump(mode='json')}
    assert c.post('/api/promotions',json=data).status_code==428
    data['rule']['source_note']=''
    assert c.post('/api/promotions',headers={'x-project-revision':store.snapshot()[1]},json=data).status_code==400


def test_reuse_physical_coupon_without_resetting_balance(client):
    c,store=client;store.save(Project(items=[item('a')],resources=[Resource(id='existing',name='c',available=0)]))
    data={'rule':rule(amount_cents=1000,resource_id='existing').model_dump(mode='json'),'available':9}
    r=c.post('/api/promotions',headers={'x-project-revision':store.snapshot()[1]},json=data)
    assert r.status_code==200
    assert len(store.load().resources)==1 and store.load().resources[0].available==0


def test_cart_connection_uses_local_consent_without_platform_reference(client,monkeypatch):
    from app.adapters.browser import BrowserAdapter
    calls=[]
    async def launch(self, *, local_consent=False):
        calls.append(local_consent)
        return {'opened':True}
    monkeypatch.setattr(BrowserAdapter,'launch',launch)
    monkeypatch.delenv('TAOBAO_PLATFORM_PERMISSION_REFERENCE',raising=False)
    c,_=client
    assert c.post('/api/cart/connect',json={}).status_code==400
    assert not calls
    assert c.post('/api/cart/connect',json={'local_consent':True}).status_code==200
    assert calls==[True]


def test_shopping_route_and_mutations_require_local_token():
    from app.main import app,TOKEN
    with TestClient(app) as c:
        assert c.get('/shopping-tools').status_code==200
        for path in ['/api/cart/read','/api/cart/import','/api/promotions','/api/promotions/estimate','/api/shopping/demo']:
            assert c.post(path,json={}).status_code==403
            assert c.post(path,headers={'x-local-token':TOKEN,'origin':'https://evil.example'},json={}).status_code==403


def test_bundle_price_never_prices_two_sets_as_one():
    p=Project(items=[item('a',20000,qty=2),item('b',20000,qty=2)],rules=[rule(kind='bundle_price',item_ids=['a','b'],required_item_quantities={'a':1,'b':1},amount_cents=15000)])
    assert cost(p).cash_due_cents==40000
    assert '套餐数量' in explain_group(p,['a','b'])['rules'][0]['reason']

def test_bundle_without_exact_scope_rejected():
    with pytest.raises(ValueError):rule(kind='bundle_price',amount_cents=10000)
    with pytest.raises(ValueError):rule(kind='bundle_price',item_ids=['a','b'],required_item_quantities={'a':1},amount_cents=10000)

def test_read_failure_invalidates_old_snapshot(tmp_path):
    from app.cart import CartService
    class Browser:
        data_dir=tmp_path
        lock=asyncio.Lock()
        async def page(self):raise ValueError('login expired')
    service=CartService(Browser(),Path(__file__).resolve().parents[1]);service.snapshot=snapshot()
    with pytest.raises(ValueError):asyncio.run(service.read())
    assert service.snapshot is None

def test_guided_home_includes_new_shopping_entry_without_replacing_demo():
    from app.main import app
    with TestClient(app) as c:
        assert '/static/shopping-entry.js' in c.get('/').text
        assert c.get('/static/shopping-entry.js').status_code==200


def test_browser_maps_local_ids_to_original_cart_lines():
    from app.adapters.browser import cart_line_map
    p=Project(items=[item('local-a').model_copy(update={'source_line_id':'cart-17'}),item('b')])
    assert cart_line_map(p)=={'local-a':'cart-17','b':'b'}


def test_browser_refuses_duplicate_cart_source_ids():
    from app.adapters.browser import cart_line_map
    p=Project(items=[item('a').model_copy(update={'source_line_id':'same'}),item('b').model_copy(update={'source_line_id':'same'})])
    with pytest.raises(ValueError,match='重复'):
        cart_line_map(p)
