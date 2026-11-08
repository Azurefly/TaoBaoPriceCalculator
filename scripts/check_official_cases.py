"""对拍：把官方条文算例喂给本项目的优惠引擎，逐条比对期望值。

用法：.venv/bin/python scripts/check_official_cases.py
不是淘宝实时价，只验证"引擎口径是否符合官方规则"。来源见 docs/01_规则调研.md 与本轮实测。
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.models import Project, Item, Rule
from app.promotion_engine import simulate_group

def item(ident, cents, qty=1):
    return Item(id=ident, title=ident, shop_id='s1', quantity=qty,
                pricing_base_cents=cents, pricing_base_evidence='官方算例',
                deposit_paid_cents=0, deposit_confirmed=True)

def rule(rid, **kw):
    base = dict(id=rid, name=rid, phase=20, threshold_basis='base', discount_basis='base',
                confirmed=True, source_note='官方算例')
    base.update(kw); return Rule(**base)

def cash(p, ids):
    quotes, warns = simulate_group(p, ids)
    if not quotes: return None, warns, None
    best = min(quotes, key=lambda q: q.cash_due_cents)
    return best.cash_due_cents, warns, best

def alloc(quote, rid):
    for b in quote.discount_breakdown:
        if b.rule_id == rid:
            return {k: v / 100 for k, v in b.allocations.items()}
    return {}

CASES = []
def case(name, expected, note):
    def deco(fn): CASES.append((name, expected, note, fn)); return fn
    return deco

@case('G1 平行门槛：210 + 券201-200', 1000, '官方例：门槛按 210 判，实付 10 元')
def _():
    return Project(items=[item('a', 21000)], rules=[rule('c', kind='fixed', threshold_cents=20100, amount_cents=20000)]), ['a']

@case('G2 官方立减：100→前N 90→立减9折', 8100, '立减门槛与金额都取"其他单品优惠后"90')
def _():
    return Project(items=[item('a', 10000)], rules=[
        rule('pren', kind='percent', phase=10, discount_bps=1000, threshold_basis='remaining', discount_basis='remaining'),
        rule('live', kind='percent', phase=15, discount_bps=1000, threshold_basis='remaining', discount_basis='remaining')]), ['a']

@case('G3 立减 + 店铺折扣（平行口径）', 6800, '官方：店铺折扣按标价 80×5%=4 元 → 68.00')
def _():
    return Project(items=[item('a', 8000)], rules=[
        rule('live', kind='percent', phase=15, discount_bps=1000, threshold_basis='remaining', discount_basis='remaining'),
        # 不显式声明 discount_basis：验证默认口径是否已是官方平行
        rule('shop', kind='percent', phase=20, discount_bps=500, threshold_cents=8000)]), ['a']

@case('G4 封顶：400 −380 券 −50 满减', 0, '官方例：满减门槛成立但只抵 20，实付 0')
def _():
    return Project(items=[item('a', 40000)], rules=[
        rule('c', kind='fixed', threshold_cents=40000, amount_cents=38000),
        rule('x', kind='fixed', phase=30, threshold_cents=40000, amount_cents=5000)]), ['a']

@case('G5 打穿重摊：A剩10 / B剩100，本层减100', 1000, '行级应为 10.00 / 90.00（按剩余摊会得 9.09 / 90.91）')
def _():
    return Project(items=[item('A', 6000), item('B', 34000)], rules=[
        rule('cA', kind='fixed', amount_cents=5000, item_ids=['A']),
        rule('cB', kind='fixed', amount_cents=24000, item_ids=['B']),
        rule('x', kind='fixed', phase=30, threshold_cents=0, amount_cents=10000)]), ['A', 'B']

@case('G6 互斥择优：券满200减10 vs 满2件9折', 18000, '2026-03-10 起只生效力度大的一个 → 180')
def _():
    return Project(items=[item('a', 20000, qty=2)], rules=[
        rule('c', kind='fixed', selection_mode='best', exclusive_group='shop', threshold_cents=20000, amount_cents=1000),
        rule('p', kind='percent', selection_mode='best', exclusive_group='shop', discount_bps=1000, min_quantity=2)]), ['a']


@case('G7 品类券：同一商品一单只生效 1 张', 7000, '两张同族券(各减30)只生效一张')
def _():
    return Project(items=[item('a', 10000)], rules=[
        rule('cat1', kind='fixed', amount_cents=3000, layer='cross', family='category_coupon'),
        rule('cat2', kind='fixed', amount_cents=3000, layer='cross', family='category_coupon')]), ['a']

@case('G8 跨店满减：不同档位不能合并', 35000, '满300减50 与 满200减20 只生效一个')
def _():
    return Project(items=[item('a', 40000)], rules=[
        rule('b300', kind='repeat_threshold', threshold_cents=30000, amount_cents=5000, layer='cross'),
        rule('b200', kind='repeat_threshold', threshold_cents=20000, amount_cents=2000, layer='cross')]), ['a']

@case('G9 个人资产类不参与门槛', 1000, '红包不能把跨店满减的门槛"凑"上去')
def _():
    return Project(items=[item('a', 6000)], rules=[
        rule('cross', kind='fixed', threshold_cents=10000, amount_cents=5000, layer='cross'),
        rule('red', kind='fixed', amount_cents=5000, layer='asset', phase=60,
             threshold_basis='remaining', discount_basis='remaining')]), ['a']

@case('G10 88VIP 折扣按商品维度', 19500, '只对参与商品打折：500 而不是 1000')
def _():
    return Project(items=[item('a', 10000), item('b', 10000)], rules=[
        rule('vip', kind='percent', discount_bps=500, layer='shop', item_ids=['a'])]), ['a', 'b']

def main():
    ok = 0
    print(f"{'算例':46} {'期望':>9} {'实际':>9}  结果  说明")
    print('-' * 118)
    for name, expected, note, fn in CASES:
        p, ids = fn()
        got, warns, quote = cash(p, ids)
        got = -1 if got is None else got
        good = got == expected
        ok += good
        extra = ''
        if name.startswith('G5') and quote:
            a = alloc(quote, 'x')
            good_alloc = a.get('A') == 10.0 and a.get('B') == 90.0
            extra = f"  行级A={a.get('A')} B={a.get('B')} → {'对' if good_alloc else '偏差(官方10.00/90.00)'}"
        print(f"{name:46} {expected/100:>9.2f} {got/100:>9.2f}  {'PASS' if good else 'FAIL':6} {note}{extra}")
        if not good and name.startswith('G3'):
            print(f"{'':46} {'':>9} {'':>9}         ← 差 {abs(got-expected)/100:.2f} 元：折扣按 remaining 算，官方按标价")
    print('-' * 118)
    print(f"通过 {ok}/{len(CASES)}")
    print("\n已内置的原语（G7–G10 覆盖）：")
    for item_txt in ['layer：unit/shop/cross/asset；unit 与 asset 禁止带门槛（模型校验）',
                     'band：跨店档位，同一单只允许一个档位生效（自动从门槛-金额推导）',
                     'family + per_item_limit：同族在同一商品上只生效一个（默认 1）',
                     'item_ids：按商品维度限定优惠范围（88VIP 只对参与商品打折）']:
        print('  -', item_txt)
    print("\n仍待实测：消费券门槛基数（真实页面提示“再实付524.95 享满1500减180”，"
          "954.86/1555.90 均已过门槛却仍提示差钱，需确认它按哪个子集算）")
    return 0 if ok == len(CASES) else 1

if __name__ == '__main__':
    sys.exit(main())
