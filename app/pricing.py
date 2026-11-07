"""Configurable simulations. No festival-specific global stacking assumptions."""
from __future__ import annotations
import hashlib
import itertools
import random
import json
import time
from bisect import bisect_left
from datetime import datetime, timedelta
from .models import Item, Project, Rule, Quote, utcnow


def distribute(amount: int, weights: dict[str, int]) -> dict[str, int]:
    """Largest-remainder allocation with deterministic cent rounding."""
    total = sum(weights.values())
    amount = min(amount, total)
    if not total:
        return dict.fromkeys(weights, 0)
    allocation = {k: amount * v // total for k, v in weights.items()}
    remainders = sorted(weights, key=lambda k: (-(amount * weights[k] % total), k))
    for key in remainders[:amount - sum(allocation.values())]:
        allocation[key] += 1
    return allocation


def eligible(rule: Rule, item: Item) -> bool:
    return ((not rule.item_ids or item.id in rule.item_ids)
            and (not rule.shop_ids or item.shop_id in rule.shop_ids)
            and (not rule.item_tags or bool(set(rule.item_tags) & set(item.tags))))


def simulate_group(project: Project, item_ids: list[str], at: datetime | None = None, max_states=2048) -> tuple[list[Quote], list[str]]:
    at = at or utcnow()
    paid = project.paid_item_ids()
    if set(item_ids) & paid:
        return [], ['组合含已付款商品，不能重复购买']
    items = [x for x in project.items if x.id in item_ids]
    if not items or len(items) != len(set(item_ids)) or len(item_ids) != len(set(item_ids)):
        return [], ['组合含未知商品或为空']
    blocked = [x.id for x in items if x.pricing_base_cents is None or not x.pricing_base_evidence or not x.deposit_confirmed or x.deposit_paid_cents is None]
    if blocked:
        return [], ['缺少可靠计价基数或定金口径：' + ', '.join(blocked)]
    base = {x.id: x.pricing_base_cents for x in items}
    resources = {r.id: r for r in project.resources}
    active = sorted([r for r in project.rules if r.confirmed and (not r.channels or project.channel in r.channels)
                     and (not r.starts_at or r.starts_at <= at) and (not r.ends_at or at < r.ends_at)], key=lambda r: (r.phase, r.id))
    # remaining prices, resource ledger, exclusive groups, applied rules, expiration
    states = [(base.copy(), {}, frozenset(), [], at + timedelta(minutes=10))]
    warnings = []
    for r in active:
        more = []
        applied_state_ids = set()
        for state_index, (remaining, consumed, used, applied, expiry) in enumerate(states):
            if r.exclusive_group and r.exclusive_group in used:
                continue
            targets = [x for x in items if eligible(r, x)]
            if not targets or sum(x.quantity for x in targets) < r.min_quantity:
                continue
            basis = base if r.threshold_basis == 'base' else remaining
            qualify = sum(basis[x.id] for x in targets)
            if qualify < r.threshold_cents:
                continue
            res = resources.get(r.resource_id) if r.resource_id else None
            if res and ((res.starts_at and at < res.starts_at) or (res.ends_at and at >= res.ends_at)):
                continue
            if r.resource_id and consumed.get(r.resource_id, 0) + r.resource_units > resources[r.resource_id].available:
                continue
            db = base if r.discount_basis == 'base' else remaining
            total = sum(db[x.id] for x in targets)
            if r.kind in ('fixed', 'quantity_fixed'):
                discount = r.amount_cents
            elif r.kind == 'percent':
                discount = (total * r.discount_bps + 5000) // 10000
            else:
                discount = (qualify // r.threshold_cents) * r.amount_cents
            if r.cap_cents is not None:
                discount = min(discount, r.cap_cents)
            discount = min(discount, sum(remaining[x.id] for x in targets))
            if not discount:
                continue
            allocations = distribute(discount, {x.id: remaining[x.id] for x in targets})
            new = {k: v - allocations.get(k, 0) for k, v in remaining.items()}
            ledger = consumed.copy()
            if r.resource_id:
                ledger[r.resource_id] = ledger.get(r.resource_id, 0) + r.resource_units
            groups = used | ({r.exclusive_group} if r.exclusive_group else set())
            new_expiry = min(expiry, r.ends_at) if r.ends_at else expiry
            if res and res.ends_at:
                new_expiry = min(new_expiry, res.ends_at)
            applied_state_ids.add(state_index)
            more.append((new, ledger, frozenset(groups), applied + [r.id], new_expiry))
        unique = {}
        retained = states if r.selection_mode == 'optional' else [s for i,s in enumerate(states) if i not in applied_state_ids]
        for state in retained + more:
            rem, con, groups, applied, exp = state
            key = (tuple(sorted(rem.items())), tuple(sorted(con.items())), tuple(sorted(groups)), exp)
            unique.setdefault(key, state)
        states = list(unique.values())
        if len(states) > max_states:
            states.sort(key=lambda s: (sum(s[0].values()), sum(s[1].values())))
            states = states[:max_states]
            warnings.append('组内优惠变体达到上限；仅保留当前低价变体，不构成全量最优证明')
    deposit = sum(x.deposit_paid_cents or 0 for x in items)
    fingerprint = project.fingerprint()
    quotes = []
    # Same resource consumption: only retain the cheapest cash variant.
    best = {}
    for remaining, consumed, groups, applied, expiry in states:
        total = sum(remaining.values())
        if any(remaining[x.id] < (x.deposit_paid_cents or 0) for x in items):
            warnings.append('单品优惠后货款低于其定金，规则模型不跨商品抵扣或推断退款，请补录真实结算报价')
            continue
        shipping = 0
        for fee in project.shipping_rules:
            matched = [x for x in items if x.shop_id == fee.shop_id]
            if not matched:
                continue
            if not fee.confirmed or not fee.evidence_note.strip():
                warnings.append('存在未确认运费配置，相关组不生成模拟报价：' + fee.shop_id)
                shipping = None
                break
            fee_basis = base if fee.threshold_basis == 'base' else remaining
            amount = sum(fee_basis[x.id] for x in matched)
            if fee.free_threshold_cents is None or amount < fee.free_threshold_cents:
                shipping += fee.fee_cents
        if shipping is None:
            continue
        key = (tuple(sorted(consumed.items())), expiry)
        landed = total + shipping
        if key not in best or landed < best[key][-1]:
            best[key] = (remaining, consumed, applied, expiry, shipping, landed)
    for remaining, consumed, applied, expiry, shipping, _ in best.values():
        raw = json.dumps([sorted(item_ids), applied], ensure_ascii=False, separators=(',', ':'))
        quotes.append(Quote(id='model-' + hashlib.sha256(raw.encode()).hexdigest()[:16], item_ids=sorted(item_ids),
                            cash_due_cents=sum(remaining.values())-deposit+shipping, consumed=consumed,
                            source='model', ledger_complete=True, portable_state=True,
                            context_fingerprint=fingerprint, captured_at=at, valid_from=at, valid_until=expiry,
                            applied_rules=applied, discount_cents=sum(base.values())-sum(remaining.values()),
                            evidence_note='基于用户确认的基数与规则模拟；运费/税费/未建模权益需真实核价',
                            notes=['模型不自动补全未知规则；不可当作淘宝实时报价', f'显式配置运费 {shipping} 分；未配置店铺运费仍需真实核对']))
    return quotes, sorted(set(warnings))


def _near_threshold(items: list[tuple[int, int]], target: int, limit=12) -> list[int]:
    """Meet-in-the-middle candidate hints, not a payable-price calculation.

    Uses confirmed base totals only. Subsets are ranked by excess over the
    threshold; eligibility after preceding reductions must still be priced.
    """
    if target <= 0 or not items or sum(value for _, value in items) < target:
        return []
    mid = len(items) // 2
    def sums(part):
        out = [(0, 0)]
        for index, value in part:
            out += [(total + value, mask | (1 << index)) for total, mask in out[:]]
        return out
    left = sums(items[:mid])
    right = sorted(sums(items[mid:]))
    right_values = [v for v, _ in right]
    best = set()
    # Include adjacent right subsets; exact candidates plus complements diversify allocation.
    for total, mask in left:
        j = bisect_left(right_values, max(0, target - total))
        for k in range(j, min(j + 2, len(right))):
            value, right_mask = right[k]
            combined = mask | right_mask
            if combined:
                best.add((total + value - target, combined.bit_count(), combined))
    return [mask for _, _, mask in sorted(best)[:limit]]


def generate_groups(project: Project, budget=500, exhaustive_limit=8) -> tuple[list[list[str]], dict]:
    paid = project.paid_item_ids()
    items = [item for item in project.items if item.id not in paid]
    ids = [x.id for x in items]
    n = len(ids)
    if not n:
        return [], {'exhaustive': True, 'total_nonempty_subsets': 0, 'generated': 0}
    if n <= exhaustive_limit and (1 << n)-1 <= budget:
        groups = [[ids[i] for i in range(n) if mask & (1 << i)] for mask in range(1, 1 << n)]
        return groups, {'exhaustive': True, 'total_nonempty_subsets': (1 << n)-1,
                        'generated': len(groups), 'strategy': '全部非空子集'}
    masks, ordered, reasons = set(), [], {}
    full = (1 << n)-1
    def add(mask, reason):
        if mask and mask not in masks and len(ordered) < budget:
            masks.add(mask)
            ordered.append(mask)
            reasons[reason] = reasons.get(reason, 0) + 1
    for i in range(n):
        add(1 << i, '单品')
    add(full, '全部未付商品')
    for shop in sorted({x.shop_id for x in items if x.shop_id}):
        add(sum(1 << i for i,x in enumerate(items) if x.shop_id == shop), '同店')
    rules = [r for r in project.rules if r.confirmed and r.threshold_cents > 0
             and (not r.channels or project.channel in r.channels)]
    pools = []
    # At most 24 distinct threshold scopes per call; remaining scopes are explicitly reported.
    seen_targets = set()
    for rule in rules:
        relevant = [(i, x.pricing_base_cents) for i,x in enumerate(items)
                    if eligible(rule,x) and x.pricing_base_cents is not None and x.pricing_base_evidence]
        key = (tuple(relevant), rule.threshold_cents)
        if key in seen_targets:
            continue
        seen_targets.add(key)
        if len(pools) < 24:
            near = _near_threshold(relevant, rule.threshold_cents)
            # Exact near-miss lists can cluster around the same cheap items.
            # Add disjoint packs so multiple payment batches can actually coexist.
            packs = []
            for reverse in (False, True):
                total, mask = 0, 0
                for index, value in sorted(relevant, key=lambda x:(x[1],x[0]), reverse=reverse):
                    total += value
                    mask |= 1 << index
                    if total >= rule.threshold_cents:
                        packs.append(mask)
                        total, mask = 0, 0
            pools.append(list(dict.fromkeys(packs + near))[:12])
    threshold_end = min(budget, len(ordered) + max(0, int((budget-len(ordered))*.65)))
    # Round robin across thresholds avoids letting the first coupon consume the whole budget.
    for layer in range(12):
        for pool in pools:
            if layer < len(pool) and len(ordered) < threshold_end:
                add(pool[layer], '门槛邻近')
                if len(ordered) < threshold_end:
                    add(full ^ pool[layer], '门槛补集')
    # Diverse deterministic samples are reserved BEFORE pair combinations.
    rng = random.Random(1717)
    sample_end = min(budget, len(ordered) + max(0, (budget-len(ordered))//3))
    for _ in range(budget*4):
        if len(ordered) >= sample_end:
            break
        add(rng.randrange(1,full+1), '分层采样')
    for i,j in itertools.combinations(range(n),2):
        add((1 << i) | (1 << j), '两件组')
    for _ in range(budget*8):
        if len(ordered) >= budget:
            break
        mask = rng.randrange(1, full+1)
        add(mask, '补充采样')
        add(full ^ mask, '补充采样')
    return [[ids[i] for i in range(n) if mask & (1 << i)] for mask in ordered], {
        'exhaustive': len(ordered) == full, 'total_nonempty_subsets': full,
        'generated': len(ordered), 'strategy_counts': reasons,
        'threshold_scopes_processed': len(pools),
        'threshold_scopes_omitted': max(0, len(seen_targets)-len(pools)),
        'note': '门槛邻近候选仅据已确认基数筛选，不是券后价或保证达标；必须真实核价。'
    }


def simulate_project(project: Project, budget=300, at=None, max_seconds=8) -> tuple[Project, dict]:
    started = time.monotonic()
    at = at or utcnow()
    groups, coverage = generate_groups(project, budget)
    quotes, warnings = [], []
    generated_ids = set()
    stopped = False
    processed = 0
    retained = [q for q in project.quotes if q.source != 'model']
    capacity = 25000-len(retained)
    for group in groups:
        if time.monotonic()-started > max_seconds or len(quotes) >= capacity:
            stopped = True
            break
        q, w = simulate_group(project, group, at, max_states=1024)
        for quote in q:
            if len(quotes) >= capacity:
                stopped = True
                break
            if quote.id not in generated_ids:
                quotes.append(quote)
                generated_ids.add(quote.id)
        warnings.extend(w)
        processed += 1
    p = project.model_copy(deep=True)
    p.candidate_groups = groups
    p.quotes = retained + quotes
    if stopped:
        warnings.append('模拟达到时间或报价预算；保留已完成结果，不构成全量模拟证明')
    p = Project.model_validate(p.model_dump())
    return p, {'coverage': coverage, 'quote_count': len(quotes), 'processed_groups': processed,
               'total_groups': len(groups), 'truncated': stopped,
               'runtime_ms': round((time.monotonic()-started)*1000),
               'warnings': sorted(set(warnings)), 'disclaimer': '模拟结果不是实时可支付价格。'}
