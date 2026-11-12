"""对拍：把示意图《淘宝购物车优惠如何计算》里的逻辑，逐条喂给本项目的引擎验证。

示意图给出两件事：
  A. 四层漏斗：商品优惠 → 店铺优惠（部分互斥择优）→ 平台优惠（看适用范围）→ 红包与会员权益（看门槛和互斥）
  B. 拆单算例：2 件各 500 元、「满 500 减 50」每单限 1 张共 2 张
       合并 1 单 → 950      拆 2 单 → 450 + 450 = 900
     并注明「不重复扣已包含的优惠；核对每批券、红包、运费和应付款」。

用法：.venv/bin/python scripts/check_image_logic.py
这里验证的是「引擎口径是否符合示意图与官方规则」，不是淘宝实时价。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Project, Item, Rule, ShippingRule, SolveOptions, utcnow
from app.pricing import simulate_project
from app.workflow import compare_strategies


def item(ident, cents, qty=1, shop='s1'):
    return Item(id=ident, title=ident, shop_id=shop, quantity=qty,
                pricing_base_cents=cents, pricing_base_evidence='示意图算例',
                deposit_paid_cents=0, deposit_confirmed=True)


def rule(rid, **kw):
    base = dict(id=rid, name=rid, phase=20, threshold_basis='base', discount_basis='base',
                confirmed=True, source_note='示意图算例')
    base.update(kw)
    return Rule(**base)


def run(project):
    """走真实链路：生成报价 → 三策略对比。"""
    p, info = simulate_project(project, 300, None, 6)
    cmp = compare_strategies(p, SolveOptions(mode='model', starts_at=utcnow(),
                                             step_seconds=60, max_seconds=6))
    rows = {s['key']: s['result'] for s in cmp['strategies']}
    return rows, cmp, info


CASES = []


def case(name, expect, note):
    def deco(fn):
        CASES.append((name, expect, note, fn))
        return fn
    return deco


# ---------------- B. 示意图主算例：拆单多用券 ----------------

@case('B1 合并1单：1000 元用 1 张满500减50 → 950', 95000,
      '示意图左栏：订单金额1000，使用满500减50券(1张)，实付950')
def _():
    p = Project(items=[item('a', 50000), item('b', 50000)],
                rules=[rule('c50', kind='fixed', threshold_cents=50000, amount_cents=5000)])
    p, _ = simulate_project(p, 300, None, 6)
    merged = [q for q in p.quotes if set(q.item_ids) >= {'a', 'b'}]
    return min(q.cash_due_cents for q in merged)


@case('B2 拆2单：各 500 各用 1 张券 → 450+450 = 900', 90000,
      '示意图右栏：第1单500→450，第2单500→450，合计900')
def _():
    p = Project(items=[item('a', 50000), item('b', 50000)],
                rules=[rule('c50', kind='fixed', threshold_cents=50000, amount_cents=5000)])
    rows, _, _ = run(p)
    return rows['optimized']['cash_due_cents']


@case('B3 引擎必须自己发现"拆单更省 50 元"', 5000,
      '示意图结论：900 < 950。引擎的自由拆合策略应优于一次合并')
def _():
    p = Project(items=[item('a', 50000), item('b', 50000)],
                rules=[rule('c50', kind='fixed', threshold_cents=50000, amount_cents=5000)])
    rows, _, _ = run(p)
    return rows['merged']['cash_due_cents'] - rows['optimized']['cash_due_cents']


@case('B4 但门槛不足时不能靠拆单变便宜（反例）', 0,
      '券是满500减50：拆成 2×250 后两单都不达门槛，拆单不得凭空省钱')
def _():
    p = Project(items=[item('a', 25000), item('b', 25000)],
                rules=[rule('c50', kind='fixed', threshold_cents=50000, amount_cents=5000)])
    rows, _, _ = run(p)
    return rows['merged']['cash_due_cents'] - rows['optimized']['cash_due_cents']


@case('B5 每单限1张的券不会在一单里重复减', 95000,
      '示意图注："每单限一张"。1000 元一单只减 50，不是减 100')
def _():
    p = Project(items=[item('a', 50000), item('b', 50000)],
                rules=[rule('c50', kind='fixed', threshold_cents=50000, amount_cents=5000)])
    p, _ = simulate_project(p, 300, None, 6)
    merged = [q for q in p.quotes if set(q.item_ids) >= {'a', 'b'}]
    return min(q.cash_due_cents for q in merged)


@case('B6 拆单省券但多付运费，要净算', 2000,
      '示意图注「假设运费不变」。这里每家店每单30元运费：拆两单省券50、多付运费30 → 净省20')
def _():
    p = Project(items=[item('a', 50000), item('b', 50000)],
                rules=[rule('c50', kind='fixed', threshold_cents=50000, amount_cents=5000)],
                shipping_rules=[ShippingRule(shop_id='s1', fee_cents=3000,
                                             free_threshold_cents=None, confirmed=True,
                                             evidence_note='示意图算例：每家店每单30元')])
    rows, _, _ = run(p)
    return rows['merged']['economic_cost_cents'] - rows['optimized']['economic_cost_cents']


# ---------------- A. 四层漏斗 ----------------

@case('A1 四层顺序：商品→店铺→平台→红包，逐层递减', 7000,
      '标价10000：商品券-1000，店铺满5000减500，平台满3000减500，红包-1000 → 实付7000')
def _():
    # 用一个可控的四层叠加，验证最终实付
    p = Project(items=[item('a', 10000)], rules=[
        rule('unit', kind='fixed', phase=10, layer='unit', threshold_cents=0, amount_cents=1000),
        rule('shop', kind='fixed', phase=20, layer='shop', threshold_cents=5000, amount_cents=500),
        rule('cross', kind='fixed', phase=30, layer='cross', threshold_cents=3000, amount_cents=500),
        rule('asset', kind='fixed', phase=40, layer='asset', threshold_cents=0, amount_cents=1000),
    ])
    p, _ = simulate_project(p, 300, None, 6)
    q = min(p.quotes, key=lambda x: x.cash_due_cents)
    return q.cash_due_cents


@case('A2 平行门槛：店铺/平台门槛按单品优惠价判，不逐层递减', 8000,
      '标价10000：商品券-1000；店铺9折门槛按10000判成立、折扣也按标价=1000 → 实付8000')
def _():
    p = Project(items=[item('a', 10000)], rules=[
        rule('unit', kind='fixed', phase=10, layer='unit', threshold_cents=0, amount_cents=1000),
        rule('shoppct', kind='percent', phase=20, layer='shop', threshold_cents=10000, discount_bps=1000),
    ])
    p, _ = simulate_project(p, 300, None, 6)
    q = min(p.quotes, key=lambda x: x.cash_due_cents)
    return q.cash_due_cents


@case('A3 店铺层"部分互斥择优"：同组只取最优一个', 1000,
      '店铺券减1000 与 店铺满减减500 互斥同组 → 取1000，不是1500')
def _():
    p = Project(items=[item('a', 10000)], rules=[
        rule('big', kind='fixed', phase=20, layer='shop', threshold_cents=10000,
             amount_cents=1000, selection_mode='best', exclusive_group='shop_g'),
        rule('small', kind='fixed', phase=20, layer='shop', threshold_cents=10000,
             amount_cents=500, selection_mode='best', exclusive_group='shop_g'),
    ])
    p, _ = simulate_project(p, 300, None, 6)
    q = min(p.quotes, key=lambda x: x.cash_due_cents)
    return 10000 - q.cash_due_cents


@case('A4 平台券"看适用范围"：品类券只作用在指定商品上', 2000,
      '品类券减2000 只适用 a（3000）；b（3000）不在范围 → 合计只减2000')
def _():
    p = Project(items=[item('a', 3000), item('b', 3000)], rules=[
        rule('cat', kind='fixed', phase=30, layer='cross', threshold_cents=2000,
             amount_cents=2000, item_ids=['a']),
    ])
    p, _ = simulate_project(p, 300, None, 6)
    # 必须取"包含两件商品"的那一单，不能取只买一件的更便宜报价
    q = min((x for x in p.quotes if set(x.item_ids) >= {'a', 'b'}),
            key=lambda x: x.cash_due_cents)
    return 6000 - q.cash_due_cents


@case('A5 红包"看门槛"：未达门槛的红包不扣', 0,
      '红包满500可用：订单只有300 → 不生效，不得硬扣')
def _():
    p = Project(items=[item('a', 3000)], rules=[
        rule('rp', kind='fixed', phase=40, layer='asset', threshold_cents=5000, amount_cents=1000),
    ])
    p, _ = simulate_project(p, 300, None, 6)
    q = min(p.quotes, key=lambda x: x.cash_due_cents)
    return 3000 - q.cash_due_cents


@case('A5b 红包"看门槛"：达到门槛才扣，且按剩余实付判', 5000,
      '标价6000、红包满5000减1000 → 实付5000（红包门槛按剩余实付判）')
def _():
    p = Project(items=[item('a', 6000)], rules=[
        rule('rp', kind='fixed', phase=40, layer='asset', threshold_cents=5000, amount_cents=1000),
    ])
    p, _ = simulate_project(p, 300, None, 6)
    q = min(p.quotes, key=lambda x: x.cash_due_cents)
    return q.cash_due_cents


@case('A6 红包"互斥"：两个红包同组只用一个', 1000,
      '红包A减1000、红包B减800 同组互斥 → 只减1000')
def _():
    p = Project(items=[item('a', 10000)], rules=[
        rule('rpa', kind='fixed', phase=40, layer='asset', threshold_cents=0,
             amount_cents=1000, selection_mode='best', exclusive_group='rp_g'),
        rule('rpb', kind='fixed', phase=40, layer='asset', threshold_cents=0,
             amount_cents=800, selection_mode='best', exclusive_group='rp_g'),
    ])
    p, _ = simulate_project(p, 300, None, 6)
    q = min(p.quotes, key=lambda x: x.cash_due_cents)
    return 10000 - q.cash_due_cents


@case('A7 店铺优惠"部分与商品优惠互斥、按规则择优"（店铺券更优）', 5000,
      '商品券减3000 与 店铺券减5000 同组互斥 → 择优取店铺券，实付5000而不是7000。'
      '注意：引擎要求同一择优组同阶段，跨层择优需把两条规则放在同一 phase')
def _():
    p = Project(items=[item('a', 10000)], rules=[
        # 同一择优组必须同阶段：跨层择优就把两条放在同一个 phase
        rule('unitc', kind='fixed', phase=20, layer='unit', amount_cents=3000,
             selection_mode='best', exclusive_group='unit_vs_shop'),
        rule('shopc', kind='fixed', phase=20, layer='shop', threshold_cents=1000,
             amount_cents=5000, selection_mode='best', exclusive_group='unit_vs_shop'),
    ])
    p, _ = simulate_project(p, 300, None, 6)
    return min(q.cash_due_cents for q in p.quotes)


@case('A7b 同一条互斥规则反向：商品券更优时取商品券', 4000,
      '商品券减6000 与 店铺券减5000 同组互斥 → 择优取商品券，实付4000')
def _():
    p = Project(items=[item('a', 10000)], rules=[
        rule('unitc', kind='fixed', phase=20, layer='unit', amount_cents=6000,
             selection_mode='best', exclusive_group='unit_vs_shop'),
        rule('shopc', kind='fixed', phase=20, layer='shop', threshold_cents=1000,
             amount_cents=5000, selection_mode='best', exclusive_group='unit_vs_shop'),
    ])
    p, _ = simulate_project(p, 300, None, 6)
    return min(q.cash_due_cents for q in p.quotes)


# ---------------- C. 示意图底部三条叮嘱 ----------------

@case('C1 "不重复扣已包含的优惠"：叠加不超订单金额', 0,
      '多层叠加后实付不得为负；封顶在剩余实付')
def _():
    p = Project(items=[item('a', 5000)], rules=[
        rule('x1', kind='fixed', phase=20, threshold_cents=1000, amount_cents=4000),
        rule('x2', kind='fixed', phase=30, threshold_cents=1000, amount_cents=4000),
    ])
    p, _ = simulate_project(p, 300, None, 6)
    q = min(p.quotes, key=lambda x: x.cash_due_cents)
    return q.cash_due_cents


@case('C2 "核对每批应付款"：分批之和 = 各行实付之和', 90000,
      '拆两单时，两单金额相加应等于各行实付合计，不出现尾差')
def _():
    p = Project(items=[item('a', 50000), item('b', 50000)],
                rules=[rule('c50', kind='fixed', threshold_cents=50000, amount_cents=5000)])
    rows, _, _ = run(p)
    steps = rows['optimized']['steps']
    return sum(s['cash_due_cents'] for s in steps)


def main():
    ok = 0
    fails = []
    for name, expect, note, fn in CASES:
        try:
            got = fn()
        except Exception as error:  # noqa: BLE001
            got = f'ERR {type(error).__name__}: {error}'
        good = got == expect
        ok += good
        mark = 'PASS' if good else 'FAIL'
        print(f'[{mark}] {name}\n        期望 {expect}  实际 {got}\n        {note}')
        if not good:
            fails.append(name)
    print(f'\n{ok}/{len(CASES)} 通过')
    if fails:
        print('未通过：' + '、'.join(fails))
    return 0 if not fails else 1


if __name__ == '__main__':
    raise SystemExit(main())
