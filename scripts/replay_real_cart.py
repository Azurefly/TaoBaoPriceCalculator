"""用真实购物车数据做一次不付款的计算回放。

数据来源：2026-11-08 本机真实账号购物车（3 件已选）的「优惠明细」原文：
  商品总价 1555.90 = 店铺优惠 448.92（官方立减 430.00 + 店铺满减 18.92）
                   + 平台优惠 152.12（超市补贴 25.92 + 88VIP 9.5折 38.05
                                     + 淘金币 27.15 + 红包 60.00 + 首单礼金 1.00）
  共减 601.04，合计 954.86
页面自己给的数字互相闭合（954.86 + 601.04 = 1555.90）。

本脚本只做本地计算：不连接淘宝、不下单、不付款。
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.models import Project, Item, Rule, Resource
from app.promotion_engine import simulate_group, explain_group
from app.workflow import compare_strategies
from app.pricing import simulate_project
from app.models import SolveOptions, utcnow

GOODS = 155590
REAL_LINES = [
    ('官方立减',   43000, dict(layer='unit', phase=10)),
    ('店铺满减',    1892, dict(layer='shop', phase=20)),
    ('超市补贴',    2592, dict(layer='cross', phase=30)),
    ('88VIP专享9.5折', 3805, dict(layer='shop', phase=25)),
    ('淘金币',      2715, dict(layer='asset', phase=50, threshold_basis='remaining', discount_basis='remaining')),
    ('红包',        6000, dict(layer='asset', phase=55, threshold_basis='remaining', discount_basis='remaining')),
    ('首单礼金',     100, dict(layer='asset', phase=60, threshold_basis='remaining', discount_basis='remaining')),
]
EXPECT_DISCOUNT = 60104
EXPECT_CASH = 95486


def base_project() -> Project:
    rules = [Rule(id=f'r{i}', name=name, kind='fixed', amount_cents=cents,
                  confirmed=True, source_note='2026-11-08 真实购物车优惠明细', **meta)
             for i, (name, cents, meta) in enumerate(REAL_LINES)]
    return Project(items=[Item(id='cart', title='真实购物车（3件合计）', shop_id='real',
                               pricing_base_cents=GOODS, pricing_base_evidence='页面「商品总价」',
                               deposit_paid_cents=0, deposit_confirmed=True)],
                   rules=rules)


def cash_of(project, ids=('cart',)):
    quotes, warns = simulate_group(project, list(ids))
    if not quotes:
        return None, warns
    return min(quotes, key=lambda q: q.cash_due_cents), warns


def main() -> int:
    print('=== 第 1 步：用真实优惠明细回放 ===')
    p = base_project()
    q, warns = cash_of(p)
    if q is None:
        print('回放失败：', warns); return 1
    discount = GOODS - q.cash_due_cents
    ok_d = discount == EXPECT_DISCOUNT
    ok_c = q.cash_due_cents == EXPECT_CASH
    print(f'  商品总价      {GOODS/100:>9.2f}')
    for line in q.discount_breakdown:
        print(f'  - {line.name:<14}{line.discount_cents/100:>9.2f}')
    print(f'  共减          {discount/100:>9.2f}   期望 {EXPECT_DISCOUNT/100:.2f}  {"✓" if ok_d else "✗"}')
    print(f'  合计（实付）   {q.cash_due_cents/100:>9.2f}   期望 {EXPECT_CASH/100:.2f}  {"✓" if ok_c else "✗"}')

    print('\n=== 第 2 步：逐条解释每一层是否生效 ===')
    exp = explain_group(p, ['cart'])
    for row in exp['rules']:
        print(f'  {"生效" if row["applied"] else "未用"} · {row["name"]:<14} {row["reason"]}')

    print('\n=== 第 3 步：把页面上那张没达成的消费券放进来算 ===')
    for basis in ('base', 'remaining'):
        p2 = p.model_copy(deep=True)
        p2.resources = [Resource(id='hqb', name='消费券 满1500减180', available=1)]
        p2.rules.append(Rule(id='hqb-r', name='消费券 满1500减180', kind='fixed', layer='cross',
                             threshold_cents=150000, amount_cents=18000,
                             threshold_basis=basis, discount_basis=basis,
                             resource_id='hqb', confirmed=True, phase=40))
        q2, _ = cash_of(p2)
        applied = bool(q2 and 'hqb-r' in q2.applied_rules)
        print(f'  门槛按 {basis:<9} → {"成立" if applied else "不成立"}，实付 {q2.cash_due_cents/100:.2f}')
    page_hint = 150000 - 52495          # 页面：「再实付524.95 享满1500减180」
    print(f'  页面自己暗示的门槛基数 = 1500.00 − 524.95 = {page_hint/100:.2f}；'
          f'实际实付 954.86，差 {page_hint/100 - 954.86:.2f}')
    print('  → 说明该券的门槛基数不是整单实付，也不是整单商品总价，而是"参与该券的商品小计"（待实测确认归属）')

    print('\n=== 第 4 步：付款顺序规划（两笔，仅计算，不下单）===')
    p3 = base_project()
    p3.resources = [Resource(id='hqb', name='消费券 满1500减180', available=1)]
    p3.rules.append(Rule(id='hqb-r', name='消费券 满1500减180', kind='fixed', layer='cross',
                         threshold_cents=150000, amount_cents=18000,
                         threshold_basis='base', discount_basis='base',
                         resource_id='hqb', confirmed=True, phase=40))
    simulated, info = simulate_project(p3, budget=50)
    print(f'  生成候选报价 {info["quote_count"]} 条（{info["processed_groups"]}/{info["total_groups"]} 组）')
    opts = SolveOptions(mode='model', starts_at=utcnow(), step_seconds=0, max_orders=2)
    result = compare_strategies(simulated, opts)
    for s in result['strategies']:
        r = s['result'] or {}
        cash = r.get('cash_due_cents')
        econ = r.get('economic_cost_cents')
        print(f'  {s["name"]:<12} 实付 {"—" if cash is None else format(cash/100, "9.2f")} '
              f'经济成本 {"—" if econ is None else format(econ/100, "9.2f")} '
              f'笔数 {r.get("order_count")} 状态 {r.get("status")}')
        for i, step in enumerate((r.get('steps') or [])[:4], 1):
            ids = step.get('item_ids') or []
            print(f'      第{i}笔 商品 {ids} 实付 {step.get("cash_due_cents", 0)/100:.2f}')

    print('\n说明：以上全部为本地计算。标价用的是页面「商品总价」，未连接淘宝，未提交订单，未付款。')
    return 0 if (ok_d and ok_c) else 1


if __name__ == '__main__':
    sys.exit(main())
