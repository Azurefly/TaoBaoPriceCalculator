"""示意图《淘宝购物车优惠如何计算》的核心逻辑回归。

锁住两件事：
  1. 拆单可以多用券——2 件各 500、满500减50 每单限1张：合并 950，拆两单 900。
  2. 四层漏斗——商品→店铺→平台→红包，门槛平行、同层互斥择优、跨层不重复扣。
算例明细见 scripts/check_image_logic.py。
"""
from __future__ import annotations

from app.models import Project, Item, Rule, SolveOptions, utcnow
from app.pricing import simulate_project
from app.workflow import compare_strategies

NOW = utcnow()


def item(ident, cents, shop='s1'):
    return Item(id=ident, title=ident, shop_id=shop, quantity=1,
                pricing_base_cents=cents, pricing_base_evidence='示意图算例',
                deposit_paid_cents=0, deposit_confirmed=True)


def rule(rid, **kw):
    base = dict(id=rid, name=rid, phase=20, confirmed=True)
    base.update(kw)
    return Rule(**base)


def full_cash(project, ids):
    """包含全部指定商品的那一单的实付（不能取只买一件的最便宜报价）。"""
    p, _ = simulate_project(project, 300, None, 6)
    want = set(ids)
    quotes = [q for q in p.quotes if set(q.item_ids) >= want]
    return min(q.cash_due_cents for q in quotes)


def strategies(project):
    p, _ = simulate_project(project, 300, None, 6)
    cmp = compare_strategies(p, SolveOptions(mode='model', starts_at=NOW,
                                             step_seconds=60, max_seconds=6))
    return {s['key']: s['result'] for s in cmp['strategies']}


def test_splitting_orders_reuses_per_order_coupon():
    """满500减50、每单限1张：合并用1张，拆两单各用1张。"""
    p = Project(items=[item('a', 50000), item('b', 50000)],
                rules=[rule('c50', kind='fixed', threshold_cents=50000, amount_cents=5000)])
    rows = strategies(p)
    assert rows['merged']['cash_due_cents'] == 95000   # 1000 − 50
    assert rows['optimized']['cash_due_cents'] == 90000  # 450 + 450
    assert sum(s['cash_due_cents'] for s in rows['optimized']['steps']) == 90000


def test_splitting_never_invents_savings_below_threshold():
    """拆单不能凭空省钱：200+200，合起来和拆开都够不到满 500。"""
    p = Project(items=[item('a', 20000), item('b', 20000)],
                rules=[rule('c50', kind='fixed', threshold_cents=50000, amount_cents=5000)])
    rows = strategies(p)
    assert rows['optimized']['cash_due_cents'] == rows['merged']['cash_due_cents'] == 40000


def test_four_layers_cascade_in_order():
    """商品券 → 店铺满减 → 跨店满减 → 红包，逐层扣但不重复扣。"""
    p = Project(items=[item('a', 10000)], rules=[
        rule('u', kind='fixed', phase=10, layer='unit', amount_cents=1000),
        rule('s', kind='fixed', phase=20, layer='shop', threshold_cents=5000, amount_cents=500),
        rule('c', kind='fixed', phase=30, layer='cross', threshold_cents=3000, amount_cents=500),
        rule('r', kind='fixed', phase=40, layer='asset', amount_cents=1000),
    ])
    p, _ = simulate_project(p, 300, None, 6)
    assert min(q.cash_due_cents for q in p.quotes) == 7000


def test_shop_layer_picks_best_of_mutually_exclusive():
    """店铺层"部分互斥择优"：同组只取最优一个，不是相加。"""
    p = Project(items=[item('a', 10000)], rules=[
        rule('big', phase=20, layer='shop', threshold_cents=10000, amount_cents=1000,
             selection_mode='best', exclusive_group='shop_g'),
        rule('small', phase=20, layer='shop', threshold_cents=10000, amount_cents=500,
             selection_mode='best', exclusive_group='shop_g'),
    ])
    p, _ = simulate_project(p, 300, None, 6)
    assert 10000 - min(q.cash_due_cents for q in p.quotes) == 1000


def test_platform_coupon_respects_scope():
    """平台券"看适用范围"：品类券只作用在指定商品上。"""
    p = Project(items=[item('a', 3000), item('b', 3000)], rules=[
        rule('cat', phase=30, layer='cross', threshold_cents=2000, amount_cents=2000,
             item_ids=['a']),
    ])
    # 取"包含全部商品"的报价，不能取只买一件的更便宜报价
    assert full_cash(p, ['a', 'b']) == 4000


def test_red_packet_threshold_gates_and_uses_remaining():
    """红包"看门槛"：未达门槛不扣；达门槛按剩余实付判。"""
    below = Project(items=[item('a', 3000)], rules=[
        rule('rp', phase=40, layer='asset', threshold_cents=5000, amount_cents=1000)])
    pb, _ = simulate_project(below, 300, None, 6)
    assert min(q.cash_due_cents for q in pb.quotes) == 3000

    above = Project(items=[item('a', 6000)], rules=[
        rule('rp', phase=40, layer='asset', threshold_cents=5000, amount_cents=1000)])
    pa, _ = simulate_project(above, 300, None, 6)
    assert min(q.cash_due_cents for q in pa.quotes) == 5000


def test_stacking_never_goes_negative():
    """不重复扣已包含的优惠：叠加不超订单金额。"""
    p = Project(items=[item('a', 5000)], rules=[
        rule('x1', phase=20, threshold_cents=1000, amount_cents=4000),
        rule('x2', phase=30, threshold_cents=1000, amount_cents=4000),
    ])
    p, _ = simulate_project(p, 300, None, 6)
    assert min(q.cash_due_cents for q in p.quotes) == 0


def test_cross_layer_best_of_picks_the_better_one():
    """示意图：店铺优惠"部分与商品优惠互斥，按规则择优使用"。"""
    def cash(unit_cents, shop_cents):
        p = Project(items=[item('a', 10000)], rules=[
            # 引擎要求同一择优组同阶段；跨层择优就放在同一 phase
            rule('unitc', phase=20, layer='unit', amount_cents=unit_cents,
                 selection_mode='best', exclusive_group='unit_vs_shop'),
            rule('shopc', phase=20, layer='shop', threshold_cents=1000,
                 amount_cents=shop_cents, selection_mode='best',
                 exclusive_group='unit_vs_shop'),
        ])
        p, _ = simulate_project(p, 300, None, 6)
        return min(q.cash_due_cents for q in p.quotes)

    assert cash(3000, 5000) == 5000   # 店铺券更优
    assert cash(6000, 5000) == 4000   # 商品券更优
