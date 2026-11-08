"""购物车合计/优惠明细解析与三元对账。

样本取自 2026-11-08 真实账号购物车（11 行、88VIP、消费券、预售定金）的
「优惠明细」浮层原文；金额关系已用页面自身数字闭合验证。
"""
from app.cart_reconcile import parse_discount_detail, reconcile, to_cents
from app.cart import normalized_capture

DETAIL = """取消选择
取消选择
取消选择
商品总价
¥
1555
.
90
店铺优惠
含官方立减
-
¥
448
.
92
官方立减
-
¥
430
.
00
店铺满减
-
¥
18
.
92
平台优惠
-
¥
152
.
12
超市补贴
-
¥
25
.
92
88VIP专享9.5折
-
¥
38
.
05
淘金币
-
¥
27
.
15
红包
-
¥
60
.
00
首单礼金
-
¥
1
.
00
优惠共减
¥
601
.
04
合计
¥
954
.
86"""


def test_to_cents():
    assert to_cents('954.86') == 95486
    assert to_cents('1555.90') == 155590
    assert to_cents('') is None
    assert to_cents('abc') is None


def test_parse_real_detail():
    d = parse_discount_detail(DETAIL)
    assert d['goods_total_cents'] == 155590
    assert d['shop_subtotal_cents'] == 44892
    assert d['platform_subtotal_cents'] == 15212
    assert d['total_discount_cents'] == 60104
    assert d['payable_cents'] == 95486
    assert [x['label'] for x in d['shop_lines']] == ['官方立减', '店铺满减']
    # 标签里自带数字（88VIP、9.5折）不能被当成金额
    assert [x['cents'] for x in d['platform_lines']] == [2592, 3805, 2715, 6000, 100]
    assert sum(x['cents'] for x in d['shop_lines']) == d['shop_subtotal_cents']
    assert sum(x['cents'] for x in d['platform_lines']) == d['platform_subtotal_cents']


def test_parse_unknown_structure_returns_none():
    assert parse_discount_detail('') is None
    assert parse_discount_detail('优惠明细\n没有金额') is None


def _snap(payable=95486, discount=60104, detail=DETAIL, rows=None):
    return {
        'summary': {'payable_cents': payable, 'discount_cents': discount,
                    'detail': parse_discount_detail(detail) if detail else None},
        'items': rows or [],
    }


def test_reconcile_all_green():
    checks = reconcile(_snap())
    assert checks, '至少应有一项'
    assert all(c['ok'] is True for c in checks), [c for c in checks if c['ok'] is not True]


def test_reconcile_zero_payable_is_read_not_missing():
    checks = reconcile({'summary': {'payable_cents': 0, 'discount_cents': None, 'detail': None}, 'items': []})
    first = [c for c in checks if '合计' in c['check'] and '商品总价' not in c['check']][0]
    assert first['ok'] is True and '未勾选' in first['detail']
    second = [c for c in checks if '已优惠' in c['check']][0]
    assert second['ok'] is False


def test_reconcile_detects_mismatch():
    checks = reconcile(_snap(payable=95485))
    failed = [c['check'] for c in checks if c['ok'] is False]
    assert '合计 + 共减 = 商品总价' in failed


def test_reconcile_without_detail_is_pending_not_pass():
    checks = reconcile(_snap(detail=None))
    pending = [c for c in checks if c['ok'] is None]
    assert pending and any('商品总价' in c['check'] for c in pending)


def test_reconcile_row_sum_matches_payable():
    # display_price_cents 是行合计（单价×数量），对账时不再乘数量
    rows = [{'line_id': 'a', 'selected': True, 'quantity': 1, 'display_price_cents': 3981, 'can_import': True},
            {'line_id': 'b', 'selected': True, 'quantity': 2, 'display_price_cents': 91505, 'can_import': True}]
    check = [c for c in reconcile(_snap(rows=rows)) if '行价' in c['check']][0]
    assert check['ok'] is True, check
    rows[1]['display_price_cents'] = 91504
    assert [c for c in reconcile(_snap(rows=rows)) if '行价' in c['check']][0]['ok'] is None


def test_capture_carries_summary_and_reconcile():
    raw = {'profile': 'div.trade-cart-item-info',
           'items': [{'line_id': 'sku:1:2', 'title': 'A', 'shop_id': 's1', 'quantity': 2,
                      'display_price_text': '39.81', 'price_scope': 'unit', 'selected': True}],
           'groups': [], 'summary': {'payable_text': '954.86', 'discount_text': '601.04',
                                     'raw': '合计:￥954.86', 'discount_raw': '含消费券共减￥601.04优惠明细'},
           'detail_raw': DETAIL}
    snap = normalized_capture(raw, 'conn')
    assert snap['summary']['payable_cents'] == 95486
    assert snap['summary']['detail']['goods_total_cents'] == 155590
    assert snap['items'][0]['display_price_cents'] == 2 * 3981
    assert any(c['ok'] is True for c in snap['reconcile'])


def test_capture_without_detail_has_note():
    raw = {'profile': 'div.trade-cart-item-info', 'groups': [], 'items': [],
           'summary': {'payable_text': '10.00', 'discount_text': '', 'raw': '', 'discount_raw': ''}}
    snap = normalized_capture(raw, 'conn')
    assert snap['summary']['goods_total_cents'] is None
    assert '优惠明细' in snap['summary']['note']


def test_deposit_row_is_not_treated_as_goods_price():
    raw = {'profile': 'div.trade-cart-item-info', 'groups': [],
           'items': [{'line_id': 'sku:1:2', 'title': '预售商品', 'quantity': 3,
                      'display_price_text': '30', 'price_scope': 'unit', 'selected': True,
                      'price_label': '定金', 'reference_price_text': '163.00'}],
           'summary': {}}
    row = normalized_capture(raw, 'conn')['items'][0]
    assert row['display_price_cents'] is None, '定金不能按 30×3=90 当货款'
    assert '定金' in row['display_note'] and '163.00' in row['display_note']
    assert row['can_import'] is True


def test_price_label_and_reference_are_kept():
    raw = {'profile': 'div.trade-cart-item-info', 'groups': [],
           'items': [{'line_id': 'sku:9:9', 'title': '加补商品', 'quantity': 1,
                      'display_price_text': '39.81', 'price_scope': 'unit',
                      'price_label': '平台加补后', 'reference_price_text': '59.9'}],
           'summary': {}}
    row = normalized_capture(raw, 'conn')['items'][0]
    assert row['display_price_cents'] == 3981
    assert row['price_label'] == '平台加补后'
    assert row['reference_price_text'] == '59.9'
