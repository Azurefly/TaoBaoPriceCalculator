"""购物车底部合计 / 优惠明细的解析与三元对账（只读，不改价）。

真实页面（2026-11）读取器给出两段原始文本：
  summary.raw           "合计:￥954.86"
  summary.discount_raw  "含消费券共减￥601.04优惠明细"
  detail_raw            手动点开“优惠明细”浮层后的全文

淘宝把优惠只分成「店铺优惠（含官方立减）」与「平台优惠」两类；88VIP 折扣、
淘金币、红包、首单礼金都归在“平台优惠”里。这是界面口径，不等于规则层级，
不能反推层级，只用来对账。
"""
from __future__ import annotations
import re
from decimal import Decimal, InvalidOperation

MONEY = re.compile(r'(?<![\d.])(\d+(?:\.\d{1,2})?)')
SPLIT_DECIMAL = re.compile(r'(?<=\d)\s*\.\s*(?=\d{1,2}(?!\d))')
NOISE = re.compile(r'取消选择|优惠明细')


def to_cents(text: str) -> int | None:
    try:
        return int((Decimal(str(text)) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


def _flat(text: str) -> str:
    text = (text or '').replace('¥', ' ').replace('￥', ' ').replace('\u00a5', ' ')
    text = SPLIT_DECIMAL.sub('.', text)      # "1555 . 90" -> "1555.90"
    text = NOISE.sub(' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _money_lines(raw: str) -> list[tuple[str, int]]:
    """按行识别金额：本页每个金额都是独占的 "¥ / 1555 / . / 90" 行序列。

    不能直接用正则扫全文：像 "88VIP专享9.5折" 这种标签里自带数字，会把 88、9.5
    误当成金额。只有以 ¥ 开头的行才当作金额开始。
    """
    lines = [ln.strip() for ln in (raw or '').splitlines() if ln.strip()]
    out: list[tuple[str, int]] = []
    label = ''
    i = 0
    while i < len(lines):
        line = lines[i]
        if line in ('¥', '￥', '\u00a5'):
            j = i + 1
            digits = ''
            while j < len(lines) and re.fullmatch(r'[\d.]+', lines[j]):
                digits += lines[j].replace(' ', '')
                j += 1
            cents = to_cents(digits) if digits else None
            if cents is not None:
                out.append((label.strip(' -–—:：'), cents))
            i = j
            continue
        if re.fullmatch(r'[-–—]+', line):
            i += 1
            continue
        label = line
        i += 1
    return out


def parse_discount_detail(raw: str) -> dict | None:
    """把优惠明细浮层正文解析成结构化金额；结构不认识时返回 None。"""
    parsed = _money_lines(raw)
    if not parsed:
        return None
    out = {'goods_total_cents': None, 'shop_subtotal_cents': None, 'shop_lines': [],
           'platform_subtotal_cents': None, 'platform_lines': [],
           'total_discount_cents': None, 'payable_cents': None}
    section = 'head'
    for label, cents in parsed:
        if '商品总价' in label:
            out['goods_total_cents'] = cents; section = 'head'; continue
        if '店铺优惠' in label:
            section = 'shop'; continue
        if '平台优惠' in label:
            out['platform_subtotal_cents'] = cents; section = 'platform'; continue
        if '优惠共减' in label:
            out['total_discount_cents'] = cents; section = 'total'; continue
        if '合计' in label:
            out['payable_cents'] = cents; section = 'done'; continue
        if section in ('shop', 'head') and out['shop_subtotal_cents'] is None and '含官方立减' in label:
            # “店铺优惠”这行只有标题没有金额，不会出现在配对结果里；
            # 它的小计挂在“含官方立减”这行，所以在这里一并切换分区。
            out['shop_subtotal_cents'] = cents; section = 'shop'
        elif section == 'shop':
            out['shop_lines'].append({'label': label, 'cents': cents})
        elif section == 'platform':
            out['platform_lines'].append({'label': label, 'cents': cents})
    out['parsed_count'] = len(parsed)
    return out


def _check(name: str, ok: bool | None, detail: str = '') -> dict:
    return {'check': name, 'ok': ok, 'detail': detail}


def reconcile(snapshot: dict) -> list[dict]:
    """三元对账：行价之和 / 共减 / 商品总价 三方互相验证。

    ok=None 表示数据不足，不能判通过也不能判失败（不把“没读到”当成“对”）。
    """
    summary = snapshot.get('summary') or {}
    detail = summary.get('detail') or {}
    payable = summary.get('payable_cents')
    discount = summary.get('discount_cents')
    goods = detail.get('goods_total_cents')
    rows = snapshot.get('items') or []
    checks = []

    # 0 是合法值（未勾选商品时合计就是 0），只有 None 才算"没读到"
    hint = '（未勾选商品时合计为 0）' if payable == 0 else ''
    checks.append(_check('读到购物车底部合计', payable is not None,
                         f'合计={payable if payable is not None else "未读到"}{hint}'))
    checks.append(_check('读到已优惠金额', discount is not None,
                         f'共减={discount if discount is not None else "未读到"}'))

    if payable is not None and discount is not None and goods is not None:
        checks.append(_check('合计 + 共减 = 商品总价',
                             payable + discount == goods,
                             f'{payable} + {discount} = {payable + discount} vs {goods}'))
    else:
        checks.append(_check('合计 + 共减 = 商品总价', None, '缺少优惠明细，请手动点开“优惠明细”后再读取'))

    shop = detail.get('shop_subtotal_cents')
    plat = detail.get('platform_subtotal_cents')
    if shop is not None and plat is not None and discount is not None:
        checks.append(_check('店铺优惠 + 平台优惠 = 共减', shop + plat == discount,
                             f'{shop} + {plat} = {shop + plat} vs {discount}'))

    for key, sub, label in (('shop_lines', shop, '店铺优惠'), ('platform_lines', plat, '平台优惠')):
        lines = detail.get(key) or []
        if lines and sub is not None:
            total = sum(x['cents'] for x in lines)
            checks.append(_check(f'{label}明细相加等于小计', total == sub,
                                 f'{total} vs {sub}（{len(lines)} 项）'))

    selected = [r for r in rows if r.get('selected')]
    priced = [r for r in selected if r.get('display_price_cents') is not None and r.get('quantity')]
    if selected and payable is not None and len(priced) == len(selected):
        row_sum = sum(r['display_price_cents'] * r['quantity'] for r in selected)
        checks.append(_check('Σ(已选行价×数量) = 底部合计', row_sum == payable,
                             f'{row_sum} vs {payable}（{len(selected)} 行）'))
    elif selected:
        checks.append(_check('Σ(已选行价×数量) = 底部合计', None,
                             '有已选行缺价格或数量，需人工核对'))
    return checks
