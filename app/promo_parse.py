"""把购物车页面上的优惠文案解析成规则草稿（需人工确认后才用于计算）。

页面给的是“规则文本”（每满X减Y / 满X减Y / X折 / 官方立减N元）和“结果金额”
（优惠明细里的官方立减 430.00 等）。只有前者能还原成规则，后者只能用于对账。
"""
from __future__ import annotations
import re
from decimal import Decimal, InvalidOperation

MONEY = r'(\d+(?:\.\d{1,2})?)'
RE_EVERY = re.compile(rf'每满\s*{MONEY}\s*减\s*{MONEY}')
RE_FULL = re.compile(rf'满\s*{MONEY}\s*减\s*{MONEY}')
RE_PIECES = re.compile(rf'满\s*(\d{{1,3}})\s*件\s*(?:打)?\s*{MONEY}\s*折')
RE_FOLD = re.compile(rf'(?<!满)(?<!减)(?<![\d.]){MONEY}\s*折')
RE_UNIT_REDUCE = re.compile(rf'(?:官方)?立减\s*{MONEY}\s*元')
NOISE = re.compile(r'免息|分期|运费险|退货宝|价保|破损包退|次日达|明日|包邮|已抵|结算|凑单')


def _cents(text: str) -> int:
    try:
        return int((Decimal(text) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return 0


def _bps(fold: str) -> int:
    try:
        return int(round((10 - Decimal(fold)) * 1000))
    except (InvalidOperation, ValueError):
        return 0


def parse_texts(texts: list[str], *, shop_id: str = '', item_ids: list[str] | None = None,
                layer: str = '') -> list[dict]:
    """同一条文案可能同时含多个优惠，逐条抽取；同一档位只保留一条。"""
    out, seen = [], set()
    for raw in texts:
        text = re.sub(r'\s+', '', str(raw or ''))
        if not text or NOISE.search(text) and not re.search(r'满|折|立减', text):
            continue
        for amount, reduce in RE_EVERY.findall(text):
            threshold, cut = _cents(amount), _cents(reduce)
            key = ('every', threshold, cut)
            if threshold and cut and key not in seen:
                seen.add(key)
                out.append(dict(kind='repeat_threshold', layer='cross', name=f'每满{amount}减{reduce}',
                                threshold_cents=threshold, amount_cents=cut,
                                band=f'{threshold}-{cut}', phase=30, scope='cart'))
        for amount, reduce in RE_FULL.findall(text):
            if RE_EVERY.search(text):
                break
            threshold, cut = _cents(amount), _cents(reduce)
            key = ('full', threshold, cut)
            if threshold and cut and key not in seen:
                seen.add(key)
                out.append(dict(kind='fixed', layer=layer or ('shop' if shop_id else 'shop'),
                                name=f'满{amount}减{reduce}', threshold_cents=threshold,
                                amount_cents=cut, phase=20, shop_ids=[shop_id] if shop_id else [],
                                item_ids=list(item_ids or []), scope='shop' if shop_id else 'item'))
        for pieces, fold in RE_PIECES.findall(text):
            bps = _bps(fold)
            key = ('pieces', pieces, bps)
            if bps and key not in seen:
                seen.add(key)
                out.append(dict(kind='percent', layer='shop', name=f'满{pieces}件{fold}折',
                                min_quantity=int(pieces), discount_bps=bps, phase=20,
                                shop_ids=[shop_id] if shop_id else [], scope='shop'))
        for fold in RE_FOLD.findall(text):
            if RE_PIECES.search(text):
                break
            bps = _bps(fold)
            key = ('fold', bps)
            if bps and key not in seen:
                seen.add(key)
                out.append(dict(kind='percent', layer='asset', name=f'{fold}折',
                                discount_bps=bps, phase=50, scope='item',
                                threshold_basis='base', discount_basis='base',
                                item_ids=list(item_ids or [])))
        for reduce in RE_UNIT_REDUCE.findall(text):
            cut = _cents(reduce)
            key = ('unit', cut)
            if cut and key not in seen:
                seen.add(key)
                out.append(dict(kind='fixed', layer='unit', name=f'官方立减{reduce}元',
                                amount_cents=cut, phase=10, scope='item',
                                item_ids=list(item_ids or [])))
    return out


def suggest(rows: list[dict], shop_texts: dict[str, list[str]], cart_texts: list[str]) -> list[dict]:
    """汇总三处来源：行内标签（按店铺归并）、店铺头部文案、底部跨店/消费券提示。"""
    by_shop: dict[str, list[str]] = {k: list(v) for k, v in (shop_texts or {}).items()}
    items_by_shop: dict[str, list[str]] = {}
    for row in rows:
        sid = row.get('shop_id') or ''
        by_shop.setdefault(sid, [])
        by_shop[sid].extend(row.get('observed_promotions') or [])
        items_by_shop.setdefault(sid, []).append(row.get('line_id') or '')
    found: list[dict] = []
    for sid, texts in by_shop.items():
        found.extend(parse_texts(texts, shop_id=sid, item_ids=[x for x in items_by_shop.get(sid, []) if x]))
    found.extend(parse_texts(cart_texts or [], layer='cross'))
    # 同一档位去重（跨店文案常重复出现在多行）
    uniq, seen = [], set()
    for item in found:
        key = (item.get('kind'), item.get('threshold_cents', 0), item.get('amount_cents', 0),
               item.get('discount_bps', 0), tuple(item.get('shop_ids') or []))
        if key in seen:
            continue
        seen.add(key)
        item['confirmed'] = False
        item['source_note'] = '购物车页面文案自动识别，需核对后启用'
        uniq.append(item)
    return uniq


RE_BUNDLE = re.compile(rf'{MONEY}\s*张共计\s*{MONEY}\s*元')
RE_PROGRESS = re.compile(rf'再实付\s*{MONEY}\s*享满\s*{MONEY}\s*减\s*{MONEY}')
RE_USED = re.compile(r'(消费券|红包|淘金币|首单礼金)\s*(?:已抵|已用|抵扣)?\s*(?:￥|¥)?\s*' + MONEY)


def parse_assets(texts: list[str], detail_lines: list[dict] | None = None) -> list[dict]:
    """识别“你手里的券/红包”，而不只是“已经抵掉多少钱”。

    来源：购物车顶部横幅（N 张共计 X 元消费券）、底部进度提示
    （已抵A，再实付B享满C减D）、优惠明细里的个人资产行（红包/淘金币/首单礼金）。
    """
    out, seen = [], set()
    joined = ' '.join(re.sub(r'\s+', '', str(t or '')) for t in texts)
    for count, total in RE_BUNDLE.findall(joined):
        key = ('bundle', count, total)
        if key not in seen:
            seen.add(key)
            out.append({'kind': 'coupon', 'name': f'消费券包 {count} 张共 {total} 元',
                        'available': int(count), 'face_total_cents': _cents(total),
                        'rule': None, 'note': '只知道张数与总额，单张面额需在券包页核对'})
    for remain, threshold, cut in RE_PROGRESS.findall(joined):
        key = ('progress', threshold, cut)
        if key in seen:
            continue
        seen.add(key)
        th, amt = _cents(threshold), _cents(cut)
        out.append({'kind': 'coupon', 'name': f'消费券 满{threshold}减{cut}', 'available': 1,
                    'gap_cents': _cents(remain),
                    'rule': {'kind': 'fixed', 'layer': 'cross', 'name': f'消费券 满{threshold}减{cut}',
                             'threshold_cents': th, 'amount_cents': amt, 'phase': 40,
                             'band': f'{th}-{amt}', 'threshold_basis': 'base', 'discount_basis': 'base'}})
    for label, amount in (x or {} for x in (detail_lines or [])):
        pass
    for line in (detail_lines or []):
        label, cents = str(line.get('label') or ''), int(line.get('cents') or 0)
        if not cents:
            continue
        for word, layer in (('红包', 'asset'), ('淘金币', 'asset'), ('首单礼金', 'asset'), ('购物金', 'asset')):
            if word in label:
                key = ('used', word, cents)
                if key in seen:
                    continue
                seen.add(key)
                out.append({'kind': 'redpacket' if word == '红包' else 'coins', 'name': f'已使用 {label}',
                            'available': 0, 'used_cents': cents, 'rule': None,
                            'note': '本次已抵扣；余额需在券/红包页核对'})
                break
    return out
