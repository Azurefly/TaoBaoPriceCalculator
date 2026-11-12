from datetime import datetime, timedelta, timezone
from itertools import permutations
import random
import pytest
from app.models import Project, Item, Resource, Rule, Quote, SolveOptions
from app.optimizer import optimize
from app.pricing import simulate_group, generate_groups, distribute
from app.adapters.browser import money, allowed_url, BrowserAdapter

NOW = datetime(2026,11,7,12,tzinfo=timezone.utc)

def item(id,base=10000,required=True,deposit=0):
    return Item(id=id,title=id,required=required,pricing_base_cents=base,pricing_base_evidence='test',
                deposit_paid_cents=deposit,deposit_confirmed=True)

def quote(p,id,items,cash,consumed=None,**kwargs):
    data=dict(id=id,item_ids=items,cash_due_cents=cash,consumed=consumed or {},source='manual',
              ledger_complete=True,portable_state=True,captured_at=NOW,context_fingerprint=p.fingerprint())
    data.update(kwargs)
    return Quote(**data)

def solve(p,**kwargs):
    return optimize(p,SolveOptions(starts_at=NOW,step_seconds=0,**kwargs))

def test_shared_coupon_is_not_reused():
    p=Project(items=[item('a'),item('b')],resources=[Resource(id='c',name='one coupon')])
    p.quotes=[quote(p,'ac',['a'],5000,{'c':1}),quote(p,'bc',['b'],5000,{'c':1}),
              quote(p,'a',['a'],10000),quote(p,'b',['b'],10000),quote(p,'ab',['a','b'],14000,{'c':1})]
    r=solve(p)
    assert r['cash_due_cents']==14000
    assert r['order_count']==1
    assert r['status']=='candidate_optimal'

def test_not_greedy_largest_discount():
    p=Project(items=[item('a'),item('b')],resources=[Resource(id='x',name='shared'),Resource(id='y',name='other')])
    p.quotes=[quote(p,'ax',['a'],6000,{'x':1}),quote(p,'ay',['a'],6500,{'y':1}),
              quote(p,'bx',['b'],1000,{'x':1}),quote(p,'b',['b'],10000)]
    assert solve(p)['cash_due_cents']==7500

def test_payable_cash_not_deposit_subtracted_twice():
    p=Project(items=[item('a',50000,deposit=10000)])
    p.quotes=[quote(p,'a',['a'],35000)]
    r=solve(p)
    assert r['cash_due_cents']==35000 and r['known_paid_deposits_cents']==10000

def test_unknown_display_price_cannot_become_base():
    p=Project(items=[Item(id='a',title='estimated',display_price_cents=8000,display_price_kind='estimated_total',deposit_confirmed=True)])
    q,w=simulate_group(p,['a'],NOW)
    assert not q and '缺少' in w[0]

def test_parallel_vs_sequential_thresholds():
    p=Project(items=[item('a',10000)],rules=[
        Rule(id='first',name='first',amount_cents=2000,confirmed=True,phase=1),
        Rule(id='second',name='second',amount_cents=1000,threshold_cents=10000,threshold_basis='base',confirmed=True,phase=2)])
    qs,_=simulate_group(p,['a'],NOW)
    assert min(q.cash_due_cents for q in qs)==7000
    p.rules[1].threshold_basis='remaining'
    qs,_=simulate_group(p,['a'],NOW)
    assert min(q.cash_due_cents for q in qs)==8000

def test_percent_cap_integer_rounding():
    p=Project(items=[item('a',60001)],rules=[Rule(id='p',name='9折',kind='percent',discount_bps=1000,cap_cents=5000,confirmed=True)])
    qs,_=simulate_group(p,['a'],NOW)
    assert min(q.cash_due_cents for q in qs)==55001
    assert sum(distribute(2,{'a':1,'b':1,'c':1}).values())==2

def test_exclusive_group():
    p=Project(items=[item('a')],rules=[Rule(id='1',name='1',amount_cents=2000,exclusive_group='shop',confirmed=True),
        Rule(id='2',name='2',amount_cents=3000,exclusive_group='shop',confirmed=True)])
    qs,_=simulate_group(p,['a'],NOW)
    assert min(q.cash_due_cents for q in qs)==7000

def test_repeat_threshold_and_quantity():
    p=Project(items=[item('a',69000)],rules=[Rule(id='r',name='每满300减30',kind='repeat_threshold',threshold_cents=30000,amount_cents=3000,confirmed=True)])
    qs,_=simulate_group(p,['a'],NOW)
    assert min(q.cash_due_cents for q in qs)==63000

def test_expiration_changes_payment_order():
    p=Project(items=[item('a'),item('b')])
    p.quotes=[quote(p,'a',['a'],1000),quote(p,'b',['b'],1000,valid_until=NOW+timedelta(seconds=70))]
    r=optimize(p,SolveOptions(starts_at=NOW,step_seconds=60))
    assert [s['quote_id'] for s in r['steps']]==['b','a']

def test_wait_for_known_window():
    p=Project(items=[item('a')])
    p.quotes=[quote(p,'a',['a'],1000,valid_from=NOW+timedelta(minutes=1),valid_until=NOW+timedelta(minutes=3))]
    r=optimize(p,SolveOptions(starts_at=NOW,step_seconds=60))
    assert r['steps'][0]['earliest_start']==(NOW+timedelta(minutes=1)).isoformat()

def test_order_grants_resource():
    p=Project(items=[item('a'),item('b')],resources=[Resource(id='earned',name='earned',available=0)])
    p.quotes=[quote(p,'a',['a'],5000,granted={'earned':1}),quote(p,'b',['b'],2000,{'earned':1},requires_done=['a'])]
    r=solve(p)
    assert r['cash_due_cents']==7000 and r['steps'][0]['quote_id']=='a'

def test_independent_quote_assumption_is_not_automatic():
    p=Project(items=[item('a'),item('b')],resources=[Resource(id='c',name='one')])
    p.quotes=[quote(p,'a',['a'],100,{'c':1},portable_state=False,observed_balances={'c':1}),
              quote(p,'b',['b'],100,portable_state=False,observed_balances={'c':1})]
    r=solve(p)
    assert [s['quote_id'] for s in r['steps']]==['b','a']
    p.quotes[1].requires_done=['a']
    assert solve(p)['status']=='no_feasible_plan'

def test_context_change_invalidates_quotes():
    p=Project(items=[item('a')])
    p.quotes=[quote(p,'a',['a'],100)]
    p.delivery_context='changed-address'
    r=solve(p)
    assert not r['steps'] and '上下文' in r['excluded_quotes'][0]['reason']

def test_old_quote_is_not_reused_silently():
    p=Project(items=[item('a')])
    p.quotes=[quote(p,'a',['a'],100,captured_at=NOW-timedelta(minutes=20))]
    assert solve(p)['accepted_quotes']==0
    assert solve(p,replay=True)['accepted_quotes']==1

def test_strict_excludes_model():
    p=Project(items=[item('a')]);p.quotes=[quote(p,'a',['a'],100,source='model')]
    assert solve(p)['status']=='no_feasible_plan'
    assert solve(p,mode='model')['cash_due_cents']==100

def test_incomplete_ledger_excluded():
    p=Project(items=[item('a')]);p.quotes=[quote(p,'a',['a'],100,ledger_complete=False)]
    assert solve(p)['accepted_quotes']==0

def test_optional_filler_cost_counts():
    p=Project(items=[item('a'),item('f',required=False)])
    p.quotes=[quote(p,'a',['a'],900),quote(p,'af',['a','f'],850)]
    assert solve(p)['cash_due_cents']==850
    p.quotes[1].cash_due_cents=950
    assert solve(p)['cash_due_cents']==900

def test_stored_value_not_mistaken_for_free_savings():
    p=Project(items=[item('a')],resources=[Resource(id='gift',name='principal',kind='stored_value',available=10000,opportunity_cost_cents=1)])
    p.quotes=[quote(p,'cash',['a'],9000),quote(p,'gift',['a'],0,{'gift':10000})]
    r=solve(p);assert r['cash_due_cents']==9000 and r['economic_cost_cents']==9000

def test_generator_coverage_honest():
    p=Project(items=[item(str(i)) for i in range(5)])
    g,c=generate_groups(p,100);assert c['exhaustive'] and len(g)==31
    p=Project(items=[item(str(i)) for i in range(16)])
    g,c=generate_groups(p,100);assert not c['exhaustive'] and len(g)<=100

def test_duplicate_and_invalid_ids_rejected():
    with pytest.raises(ValueError):Project(items=[item('a'),item('a')])
    with pytest.raises(ValueError):Project(items=[item('a')],quotes=[Quote(id='q',item_ids=['b'],cash_due_cents=1)])
    with pytest.raises(ValueError):Resource(id='x',name='x',available=-1)
    with pytest.raises(ValueError):Quote(id='q',item_ids=['a'],cash_due_cents=1.1)

def test_money_and_allowed_hosts():
    assert money('¥1,000.10')==100010
    for s in ['原价100 到手80','-1','价格待定','9.999','sku123','1,00.00']:
        with pytest.raises(ValueError):money(s)
    assert allowed_url('https://cart.taobao.com/cart.htm')
    assert not allowed_url('https://taobao.com.evil.example')
    assert not allowed_url('http://cart.taobao.com')
    assert not allowed_url('https://example.com@evil.example')

def test_browser_permission_default_closed(tmp_path,monkeypatch):
    monkeypatch.delenv('TAOBAO_PLATFORM_PERMISSION_REFERENCE',raising=False)
    a=BrowserAdapter(tmp_path)
    with pytest.raises(ValueError):a.require_permission()

def test_historical_sample_cannot_be_repriced():
    from pathlib import Path
    p=Project.model_validate_json((Path(__file__).parents[1]/'examples/historical.json').read_text())
    assert sum(x.display_price_cents for x in p.items[:14])==231000
    assert sum(x.deposit_paid_cents for x in p.items)==16000
    assert solve(p)['status']=='no_feasible_plan'
    q,_=simulate_group(p,[p.items[0].id],NOW);assert not q

# Small random problems are exhaustively cross-checked against an independent brute-force implementation.
@pytest.mark.parametrize('seed',range(20))
def test_optimizer_matches_exhaustive_enumeration(seed):
    rng=random.Random(seed)
    p=Project(items=[item(str(i)) for i in range(4)],resources=[Resource(id='c',name='one')])
    for mask in range(1,16):
        items=[str(i) for i in range(4) if mask>>i&1]
        cost=rng.randrange(1,1000)
        p.quotes.append(quote(p,'q'+str(mask),items,cost,{'c':1} if rng.random()<.35 else {}))
    def brute(remaining,used):
        if not remaining:return 0
        best=10**9
        for q in p.quotes:
            group=set(q.item_ids)
            n=q.consumed.get('c',0)
            if group<=remaining and used+n<=1:
                best=min(best,q.cash_due_cents+brute(remaining-group,used+n))
        return best
    expected=brute(set(str(i) for i in range(4)),0)
    got=solve(p,max_seconds=3)
    assert got['cash_due_cents']==(expected if expected<10**9 else None)

def test_mandatory_automatic_reduction_cannot_be_skipped():
    p=Project(items=[item('a',10000)],rules=[
        Rule(id='auto',name='自动减20',amount_cents=2000,phase=1,selection_mode='mandatory',confirmed=True),
        Rule(id='threshold',name='剩余100减50',amount_cents=5000,threshold_cents=10000,
             threshold_basis='remaining',phase=2,confirmed=True)])
    qs,_=simulate_group(p,['a'],NOW)
    assert min(q.cash_due_cents for q in qs)==8000
    assert all('auto' in q.applied_rules for q in qs)

def test_default_threshold_basis_is_official_parallel():
    # 官方：店铺级/跨店级门槛按单品优惠价（base）判；先减 20 后门槛仍按 100 判 → 成立
    assert Rule(id='r',name='r').threshold_basis=='base'
    assert Rule(id='r',name='r').discount_basis=='base'
    p=Project(items=[item('a',10000)],rules=[
        Rule(id='auto',name='自动减20',amount_cents=2000,phase=1,selection_mode='mandatory',confirmed=True),
        Rule(id='th',name='满100减50',amount_cents=5000,threshold_cents=10000,phase=2,confirmed=True)])
    qs,_=simulate_group(p,['a'],NOW)
    assert min(q.cash_due_cents for q in qs)==3000


def test_refund_not_the_original_list_price():
    from app.refunds import RefundScenario, evaluate_refund
    # Keep 900, add 100, use 100-off coupon: pay 900, actual filler refund 90 => retain cost 810.
    r=evaluate_refund(RefundScenario(cash_paid_total_cents=90000,cash_refund_cents=9000))
    assert r['net_economic_cost_cents']==81000
    assert r['status']=='scenario_only'

def test_refund_stored_principal_and_clawback():
    from app.refunds import RefundScenario, evaluate_refund
    r=evaluate_refund(RefundScenario(cash_paid_total_cents=90000,cash_refund_cents=9000,
        stored_principal_spent_cents=2000,stored_principal_returned_cents=200,
        return_shipping_cents=800,additional_clawback_cents=500,
        refund_amount_confirmed=True,all_adjustments_confirmed=True))
    assert r['net_economic_cost_cents']==84100
    assert r['status']=='confirmed_accounting'

def test_refund_rejects_phantom_cashback():
    from app.refunds import RefundScenario, evaluate_refund
    with pytest.raises(ValueError):evaluate_refund(RefundScenario(cash_paid_total_cents=10,cash_refund_cents=100))

def test_channel_specific_coupon_not_assumed_valid_on_web():
    p=Project(items=[item('a')],channel='web',rules=[Rule(id='live',name='live',channels=['live'],amount_cents=9000,confirmed=True)])
    qs,_=simulate_group(p,['a'],NOW)
    assert min(q.cash_due_cents for q in qs)==10000

def test_extra_fees_are_included_in_cash_total():
    p=Project(items=[item('a')])
    p.quotes=[quote(p,'a',['a'],1000,extra_cost_cents=800)]
    r=solve(p)
    assert r['cash_due_cents']==1800 and r['economic_cost_cents']==1800
    assert r['steps'][0]['checkout_cash_due_cents']==1000
    assert r['steps'][0]['extra_cash_cost_cents']==800


def test_layer_validations_and_implicit_constraints():
    import pytest
    from pydantic import ValidationError
    # 单品级优惠已并入单品优惠价，不允许再带门槛
    with pytest.raises(ValidationError):
        Rule(id='x', name='x', layer='unit', threshold_cents=100, confirmed=True)
    # 个人资产类（红包/淘金币/会员价）确实有使用门槛，允许带门槛，
    # 且门槛一律按店铺级/跨店级优惠后的剩余实付判，不是按标价。
    asset = Rule(id='rp', name='rp', layer='asset', threshold_cents=50000,
                 amount_cents=1000, confirmed=True)
    assert asset.threshold_basis == 'remaining'
    # 跨店每满减自动补档位
    band_rule = Rule(id='b', name='b', kind='repeat_threshold', layer='cross',
                     threshold_cents=30000, amount_cents=5000, confirmed=True)
    assert band_rule.band == '30000-5000'
    # 优惠族默认每件商品限 1 个
    fam = Rule(id='f', name='f', family='category_coupon', confirmed=True)
    assert fam.per_item_limit == 1


def test_family_limits_one_coupon_per_item():
    p=Project(items=[item('a',10000)],rules=[
        Rule(id='c1',name='品类券1',amount_cents=3000,family='category_coupon',layer='cross',confirmed=True),
        Rule(id='c2',name='品类券2',amount_cents=3000,family='category_coupon',layer='cross',confirmed=True)])
    qs,_=simulate_group(p,['a'],NOW)
    assert min(q.cash_due_cents for q in qs)==7000


def test_cross_band_cannot_merge():
    p=Project(items=[item('a',40000)],rules=[
        Rule(id='b3',name='满300减50',kind='repeat_threshold',threshold_cents=30000,amount_cents=5000,layer='cross',confirmed=True),
        Rule(id='b2',name='满200减20',kind='repeat_threshold',threshold_cents=20000,amount_cents=2000,layer='cross',confirmed=True)])
    qs,_=simulate_group(p,['a'],NOW)
    assert min(q.cash_due_cents for q in qs)==35000


def test_asset_layer_cannot_raise_threshold():
    p=Project(items=[item('a',6000)],rules=[
        Rule(id='cross',name='满100减50',amount_cents=5000,threshold_cents=10000,layer='cross',confirmed=True),
        Rule(id='red',name='红包',amount_cents=5000,layer='asset',phase=60,
             threshold_basis='remaining',discount_basis='remaining',confirmed=True)])
    qs,_=simulate_group(p,['a'],NOW)
    assert min(q.cash_due_cents for q in qs)==1000
