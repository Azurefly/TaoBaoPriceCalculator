"""Exact branch-and-bound over observed/configured quote transitions, not display prices.

The claim of optimality is restricted to the supplied admissible quote graph. Never
claim global Taobao optimality. Each move purchases at least one previously unbought
line, so the graph is finite even when a purchase grants a new coupon.
"""
from __future__ import annotations

import time
from datetime import timedelta
from .models import Project, SolveOptions, Quote


def optimize(project: Project, opts: SolveOptions) -> dict:
    started = time.monotonic()
    ids = [x.id for x in project.items]
    index = {v: i for i, v in enumerate(ids)}
    rid = [x.id for x in project.resources]
    rindex = {v: i for i, v in enumerate(rid)}
    initial = tuple(x.available for x in project.resources)
    paid = project.paid_item_ids()
    initial_mask = sum(1 << index[i] for i in paid)
    required = sum(1 << index[x.id] for x in project.items if x.required and x.id not in paid)
    fingerprint = project.fingerprint()
    excluded: list[dict] = []
    candidates: list[dict] = []
    for q in project.quotes:
        reason = ''
        if set(q.item_ids) & paid:
            reason = '报价包含已经记录付款的商品'
        elif q.context_fingerprint != fingerprint:
            reason = '上下文已变化，需重新核价（账号/地址/支付方式/商品/权益）'
        elif not q.ledger_complete:
            reason = '优惠资源消耗清单未确认，不能排除同一券/红包被重复使用'
        elif q.source == 'draft' or (opts.mode == 'strict' and q.source not in ('checkout', 'manual')):
            reason = '严格模式只接受已核对的真实结算报价；演示/规则估算不是实时报价'
        elif not q.portable_state and set(q.observed_balances) != set(rid):
            reason = '非可移植报价缺少完整的观察时权益余额'
        elif q.valid_until and q.valid_until < opts.starts_at:
            reason = '报价已过期'
        elif q.source in ('checkout', 'manual') and q.captured_at > opts.starts_at:
            # 只有真实抓取的结账报价才谈得上“抓取时间晚于计划开始时间”。
            # 规则估算（source='model'）的 captured_at 只是引擎算它的时刻，
            # 若也按此过滤，任何 starts_at 略早于生成时刻的调用都会被静默判为无解。
            reason = '报价时间在计划开始时间之后，需检查时间设置'
        elif q.source in ('checkout', 'manual') and not opts.replay and (opts.starts_at - q.captured_at).total_seconds() > opts.max_quote_age_seconds:
            reason = '实时报价超过允许缓存时长，需重新核价'
        if reason:
            excluded.append({'id': q.id, 'reason': reason})
            continue
        mask = sum(1 << index[i] for i in q.item_ids)
        need = sum(1 << index[i] for i in q.requires_done)
        consume = tuple(q.consumed.get(i, 0) for i in rid)
        grants = tuple(q.granted.get(i, 0) for i in rid)
        cost = q.cash_due_cents + q.extra_cost_cents + sum(c * r.opportunity_cost_cents for c, r in zip(consume, project.resources))
        deadline = q.valid_until
        if q.source in ('checkout', 'manual') and not opts.replay:
            ttl_end = q.captured_at + timedelta(seconds=opts.max_quote_age_seconds)
            deadline = min(deadline, ttl_end) if deadline else ttl_end
        earliest = q.valid_from
        for resource in project.resources:
            if q.consumed.get(resource.id, 0):
                if resource.ends_at:
                    deadline = min(deadline, resource.ends_at) if deadline else resource.ends_at
                if resource.starts_at:
                    earliest = max(earliest, resource.starts_at) if earliest else resource.starts_at
        candidates.append({'q': q, 'deadline': deadline, 'earliest': earliest, 'mask': mask, 'need': need, 'consume': consume, 'grants': grants,
                           'cost': cost, 'required_count': (mask & required).bit_count()})
    # A conservative additive relaxation: assign each item <= its fraction of each quote cost.
    floors = {}
    for i in range(len(ids)):
        if required & (1 << i):
            values = [c['cost'] // c['required_count'] for c in candidates if c['mask'] & (1 << i) and c['required_count']]
            floors[i] = min(values) if values else 0
    lower_bound = sum(floors.values())
    covered_union = 0
    for c in candidates:
        covered_union |= c['mask']
    missing = [ids[i] for i in range(len(ids)) if required & (1 << i) and not covered_union & (1 << i)]
    candidates.sort(key=lambda c: (c['cost'] / max(c['required_count'], 1), -c['required_count'], c['q'].id))
    by_item = {i: [c for c in candidates if c['mask'] & (1 << i)]
               for i in floors}
    latest_static_finish = opts.starts_at + timedelta(seconds=opts.step_seconds * min(opts.max_orders, len(ids)-len(paid)))
    static_order_independent = all(
        c['q'].portable_state and not c['q'].granted and not c['q'].requires_done
        and not c['q'].requires_zero and not c['q'].requires_less_than and (not c['earliest'] or c['earliest'] <= opts.starts_at)
        and (not c['deadline'] or c['deadline'] >= latest_static_finish)
        for c in candidates)
    best: tuple[int, int, int] | None = None  # economic cost, cash, number of orders
    best_path: list[dict] = []
    labels: dict[tuple, list[tuple]] = {}
    nodes = 0
    truncated = False

    def dfs(mask, balances, at, cost, cash, path):
        nonlocal nodes, truncated, best, best_path
        nodes += 1
        if nodes > opts.max_nodes or time.monotonic() - started > opts.max_seconds:
            truncated = True
            return
        if mask & required == required:
            score = (cost, cash, len(path))
            if best is None or score < best:
                best, best_path = score, path.copy()
            return
        if len(path) >= opts.max_orders:
            return
        lb = cost + sum(v for i, v in floors.items() if not mask & (1 << i))
        if best and lb > best[0]:
            return
        # Earlier finish, lower economic cost/cash and fewer orders dominate the same state.
        key = (mask, balances)
        label = (at, cost, cash, len(path))
        previous = labels.setdefault(key, [])
        if any(all(a <= b for a, b in zip(old, label)) for old in previous):
            return
        labels[key] = [old for old in previous if not all(a <= b for a, b in zip(label, old))] + [label]
        options = candidates
        if static_order_independent:
            remaining_ids = [i for i in floors if not mask & (1 << i)]
            pivot = min(remaining_ids, key=lambda i: len(by_item[i]))
            options = by_item[pivot]
        for position, c in enumerate(options):
            if position % 64 == 0 and time.monotonic() - started > opts.max_seconds:
                truncated = True
            if truncated:
                break
            q: Quote = c['q']
            if mask & c['mask'] or c['need'] & mask != c['need']:
                continue
            if any(b < n for b, n in zip(balances, c['consume'])):
                continue
            if any(balances[rindex[r]] for r in q.requires_zero):
                continue
            if any(balances[rindex[r]] >= ceiling for r, ceiling in q.requires_less_than.items()):
                continue
            if not q.portable_state and any(q.observed_balances[r] != balances[j] for j, r in enumerate(rid)):
                continue
            begin = max(at, c['earliest']) if c['earliest'] else at
            finish = begin + timedelta(seconds=opts.step_seconds)
            if c['deadline'] and finish > c['deadline']:
                continue
            new_cost = cost + c['cost']
            if best and new_cost > best[0]:
                continue
            new_balances = tuple(b - n + g for b, n, g in zip(balances, c['consume'], c['grants']))
            step = {'quote_id': q.id, 'item_ids': q.item_ids, 'cash_due_cents': q.cash_due_cents + q.extra_cost_cents,
                    'checkout_cash_due_cents': q.cash_due_cents, 'extra_cash_cost_cents': q.extra_cost_cents,
                    'economic_cost_cents': c['cost'], 'consumed': q.consumed, 'granted': q.granted,
                    'earliest_start': begin.isoformat(), 'estimated_finish': finish.isoformat(),
                    'effective_deadline': c['deadline'].isoformat() if c['deadline'] else None,
                    'source': q.source, 'notes': q.notes, 'evidence_note': q.evidence_note,
                    'must_revalidate_before_payment': True}
            dfs(mask | c['mask'], new_balances, finish, new_cost, cash + q.cash_due_cents + q.extra_cost_cents, path + [step])

    if required and not missing:
        dfs(initial_mask, initial, opts.starts_at, 0, 0, [])
    elif not required:
        best = (0, 0, 0)
    warnings = [
        '结论仅覆盖本次已输入且满足约束的报价；未测试的组合、活动、入口不在最优性证明范围内。',
        '每付一笔会改变券/红包/库存状态：必须核对实际消耗、更新项目并重新核算剩余订单。',
        '商品按一条SKU及数量整体分组；需要拆数量时应先拆为独立行并重新采价。',
        '计划允许在报价有效期内等待，但不预测未来价格、库存、名额或未确定到账的奖励。',
    ]
    if opts.mode == 'model':
        warnings.append('规则/演示模式结果不是淘宝实时报价，请用结算页面复核。')
    if any(x.pricing_base_cents is None for x in project.items):
        warnings.append('存在未知计价基数；程序没有把券后展示价倒推成原价，也不会重复减券。')
    if any(not x.deposit_confirmed for x in project.items):
        warnings.append('部分商品定金口径未确认；已观察的cash_due始终按还需支付的金额使用，不再减定金。')
    status = 'candidate_optimal' if best and not truncated else 'best_found' if best else 'search_incomplete' if truncated else 'no_feasible_plan'
    return {
        'status': status, 'scope': '已输入有效报价集合内，不是全淘宝全局最优',
        'context_fingerprint': fingerprint, 'mode': opts.mode,
        'cash_due_cents': best[1] if best else None,
        'economic_cost_cents': best[0] if best else None,
        'order_count': best[2] if best else None,
        'lower_bound_cents': best[0] if best and not truncated else lower_bound,
        'gap_cents': (best[0] - lower_bound) if best and truncated else 0 if best else None,
        'steps': best_path, 'searched_nodes': nodes, 'search_exhausted': not truncated,
        'search_strategy': 'canonical_partition' if static_order_independent else 'ordered_state_search',
        'paid_item_ids': sorted(paid),
        'runtime_ms': round((time.monotonic() - started) * 1000),
        'accepted_quotes': len(candidates), 'excluded_quotes': excluded,
        'missing_item_ids': missing, 'warnings': warnings,
        'recorded_payments_cash_cents': sum(p.cash_paid_cents + p.extra_cost_cents for p in project.payments),
        'known_paid_deposits_cents': sum(x.deposit_paid_cents or 0 for x in project.items if x.required and x.deposit_confirmed),
    }
