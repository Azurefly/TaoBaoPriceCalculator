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


from .promotion_engine import distribute, eligible, simulate_group


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
    for rule in [r for r in project.rules if r.confirmed]:
        required = set(rule.required_item_quantities)
        relevant = [i for i,x in enumerate(items) if eligible(rule,x,qualifying=True) or x.id in required]
        required_mask = sum(1 << i for i,x in enumerate(items) if x.id in required)
        scope_mask = sum(1 << i for i in relevant)
        add(required_mask, '必选搭配')
        add(scope_mask, '优惠适用商品')
        add(full ^ scope_mask, '优惠范围补集')
        if required_mask:
            for i in relevant:
                add(required_mask | (1 << i), '搭配补足')
        # Quantity-only offers need candidates even without a monetary threshold.
        if rule.min_quantity > 1 or rule.min_distinct_items > 1:
            for reverse in (False,True):
                qty, mask, distinct = 0, required_mask, 0
                qty = sum(x.quantity for i,x in enumerate(items) if mask & (1 << i))
                distinct = mask.bit_count()
                for i in sorted(relevant, key=lambda j:(items[j].pricing_base_cents or 0,j), reverse=reverse):
                    if mask & (1 << i): continue
                    mask |= 1 << i; qty += items[i].quantity; distinct += 1
                    if qty >= rule.min_quantity and distinct >= rule.min_distinct_items:
                        add(mask, '满件邻近'); add(full ^ mask, '满件补集'); break
    rules = []
    for r in project.rules:
        if not r.confirmed or (r.channels and project.channel not in r.channels): continue
        if r.threshold_cents > 0: rules.append(r)
        for tier in r.tiers:
            if tier.threshold_cents > 0:
                rules.append(r.model_copy(update={'threshold_cents': tier.threshold_cents}))
    pools = []
    # At most 24 distinct threshold scopes per call; remaining scopes are explicitly reported.
    seen_targets = set()
    for rule in rules:
        relevant = [(i, x.pricing_base_cents) for i,x in enumerate(items)
                    if eligible(rule,x,qualifying=True) and x.pricing_base_cents is not None and x.pricing_base_evidence]
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
        q, w = simulate_group(project, group, at, max_states=1024, deadline=started+max_seconds)
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
