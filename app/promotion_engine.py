"""Scoped, evidence-backed promotion simulation; never a live checkout quote.

Amounts are integer cents. A line is an indivisible SKU/quantity purchase.
Shop-level best-of rules are evaluated together, not greedily in rule-id order.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import itertools
import time
from .models import Project, Item, Rule, Quote, DiscountBreakdown, utcnow


def distribute(amount: int, weights: dict[str, int]) -> dict[str, int]:
    total = sum(weights.values())
    amount = min(amount, total)
    if not total:
        return dict.fromkeys(weights, 0)
    out = {key: amount * value // total for key, value in weights.items()}
    order = sorted(weights, key=lambda key: (-(amount * weights[key] % total), key))
    for key in order[:amount - sum(out.values())]:
        out[key] += 1
    return out


def eligible(rule: Rule, item: Item, *, qualifying: bool = False) -> bool:
    ids = rule.qualifying_item_ids if qualifying and rule.qualifying_item_ids else rule.item_ids
    return (item.id not in rule.excluded_item_ids
            and (not ids or item.id in ids)
            and (not rule.shop_ids or item.shop_id in rule.shop_ids)
            and (not rule.item_tags or bool(set(rule.item_tags) & set(item.tags))))


@dataclass
class State:
    remaining: dict[str, int]
    consumed: dict[str, int]
    used: frozenset
    applied: list[str]
    expiry: datetime
    breakdown: list[DiscountBreakdown]


def conflict_keys(rule: Rule, qualifiers: list[Item], targets: list[Item]) -> frozenset:
    if not rule.exclusive_group:
        return frozenset()
    ids = {x.id for x in qualifiers + targets} if rule.exclusive_scope == 'item' else {'*'}
    return frozenset((rule.exclusive_group, ident) for ident in ids)


def offer(project: Project, rule: Rule, items: list[Item], base: dict[str, int], state: State, at: datetime):
    """Return evaluated option and readable failure, without changing any inputs."""
    if not rule.confirmed:
        return None, '优惠范围和规则尚未核对'
    if rule.channels and project.channel not in rule.channels:
        return None, '当前购买入口不适用这张券'
    if (rule.starts_at and at < rule.starts_at) or (rule.ends_at and at >= rule.ends_at):
        return None, '优惠尚未生效或已经过期'
    selected = {x.id: x for x in items}
    missing = [f'{ident}×{qty}' for ident, qty in rule.required_item_quantities.items()
               if ident not in selected or selected[ident].quantity < qty]
    if missing:
        return None, '需要一起购买：' + '、'.join(missing)
    targets = [x for x in items if eligible(rule, x)]
    qualifiers = [x for x in items if eligible(rule, x, qualifying=True)]
    if rule.kind == 'bundle_price' and any(selected[k].quantity != v for k,v in rule.required_item_quantities.items()):
        return None, '套餐数量与配置不一致；多套或额外数量需单独核价，不能按一套价格计算'
    if not targets or not qualifiers:
        return None, '这些商品不在优惠适用范围内'
    quantity = sum(x.quantity for x in qualifiers)
    if quantity < rule.min_quantity or len(qualifiers) < rule.min_distinct_items:
        return None, f'指定商品需至少{rule.min_quantity}件、{rule.min_distinct_items}种'
    conflicts = conflict_keys(rule, qualifiers, targets)
    if conflicts & state.used:
        return None, '与已采用的优惠互斥，不能重复叠加'
    basis = base if rule.threshold_basis == 'base' else state.remaining
    qualify = sum(basis[x.id] for x in qualifiers)
    if qualify < rule.threshold_cents:
        return None, f'指定商品距门槛还差{(rule.threshold_cents-qualify)/100:.2f}元'
    resource = next((x for x in project.resources if x.id == rule.resource_id), None)
    if rule.resource_id:
        if resource is None:
            return None, '对应优惠券不存在'
        if state.consumed.get(resource.id, 0) + rule.resource_units > resource.available:
            return None, '这张券的剩余次数不足'
        if (resource.starts_at and at < resource.starts_at) or (resource.ends_at and at >= resource.ends_at):
            return None, '对应券已失效或尚未生效'
    db = base if rule.discount_basis == 'base' else state.remaining
    total = sum(db[x.id] for x in targets)
    if rule.kind in ('fixed', 'quantity_fixed'):
        discount = rule.amount_cents
    elif rule.kind == 'percent':
        discount = (total * rule.discount_bps + 5000) // 10000
    elif rule.kind == 'repeat_threshold':
        discount = (qualify // rule.threshold_cents) * rule.amount_cents
    elif rule.kind == 'bundle_price':
        discount = max(0, sum(state.remaining[x.id] for x in targets) - rule.amount_cents)
    elif rule.kind == 'tiered':
        valid = [t.amount_cents for t in rule.tiers if qualify >= t.threshold_cents and quantity >= t.min_quantity]
        if not valid:
            return None, '尚未满足任何一档阶梯优惠'
        discount = max(valid)
    else:
        return None, '暂不支持的规则类型'
    if rule.cap_cents is not None:
        discount = min(discount, rule.cap_cents)
    discount = min(discount, sum(state.remaining[x.id] for x in targets))
    if not discount:
        return None, '符合范围，但本次不会减少应付金额'
    allocation = distribute(discount, {x.id: state.remaining[x.id] for x in targets})
    record = DiscountBreakdown(rule_id=rule.id, name=rule.name,
        qualifying_item_ids=[x.id for x in qualifiers], discounted_item_ids=[x.id for x in targets],
        qualifying_cents=qualify, discount_cents=discount, allocations=allocation)
    return (record, conflicts, resource), ''


def apply(project, rule, items, base, state, at):
    option, reason = offer(project, rule, items, base, state, at)
    if option is None:
        return None
    record, conflicts, resource = option
    ledger = state.consumed.copy()
    if resource:
        ledger[resource.id] = ledger.get(resource.id, 0) + rule.resource_units
    expiry = min([state.expiry] + [t for t in (rule.ends_at, resource.ends_at if resource else None) if t])
    return State({k: v-record.allocations.get(k, 0) for k,v in state.remaining.items()}, ledger,
                 state.used | conflicts, state.applied + [rule.id], expiry, state.breakdown + [record])


def unique_states(states, limit, warnings):
    unique = {}
    for s in states:
        key = (tuple(sorted(s.remaining.items())), tuple(sorted(s.consumed.items())), tuple(sorted(s.used)), s.expiry)
        unique.setdefault(key, s)
    result = list(unique.values())
    if len(result) > limit:
        result.sort(key=lambda s: (sum(s.remaining.values()), sum(s.consumed.values())))
        result = result[:limit]
        warnings.append('组内优惠变体达到上限；结果不构成全部优惠组合的最优证明')
    return result


def _simulate_group(project: Project, item_ids: list[str], at: datetime | None = None, max_states=2048, deadline=None):
    at = at or utcnow()
    if set(item_ids) & project.paid_item_ids():
        return [], ['组合含已付款商品，不能重复购买']
    items = [x for x in project.items if x.id in item_ids]
    if not items or len(items) != len(item_ids) or len(item_ids) != len(set(item_ids)):
        return [], ['组合含未知商品、重复商品或为空']
    blocked = [x.id for x in items if x.pricing_base_cents is None or not x.pricing_base_evidence.strip()
               or not x.deposit_confirmed or x.deposit_paid_cents is None]
    if blocked:
        return [], ['缺少可靠计价基数或定金口径：' + ', '.join(blocked)]
    base = {x.id: x.pricing_base_cents for x in items}
    active = sorted([r for r in project.rules if r.confirmed], key=lambda r: (r.phase, r.id))
    states = [State(base.copy(), {}, frozenset(), [], at+timedelta(minutes=10), [])]
    warnings, done = [], set()
    for r in active:
        if deadline and time.monotonic() > deadline: return [], ['优惠模拟达到时间上限，未完成的组合已丢弃']
        if r.id in done:
            continue
        if r.selection_mode == 'best':
            block = [x for x in active if x.selection_mode == 'best' and x.exclusive_group == r.exclusive_group]
            done.update(x.id for x in block)
            next_states = []
            for initial in states:
                if deadline and time.monotonic() > deadline: return [], ['优惠模拟达到时间上限，未完成的组合已丢弃']
                choices = [initial]
                for option in block:
                    applied = [apply(project, option, items, base, s, at) for s in choices]
                    choices = unique_states(choices + [s for s in applied if s is not None], max_states, warnings)
                lowest = min(sum(s.remaining.values()) for s in choices)
                # Keep equal-price resource variants: consuming two different physical
                # coupons can change the optimum for later payment batches.
                next_states += [s for s in choices if sum(s.remaining.values()) == lowest]
            states = unique_states(next_states, max_states, warnings)
        else:
            next_states = []
            for s in states:
                candidate = apply(project, r, items, base, s, at)
                if candidate is None or r.selection_mode == 'optional':
                    next_states.append(s)
                if candidate is not None:
                    next_states.append(candidate)
            states = unique_states(next_states, max_states, warnings)
    best = {}
    for s in states:
        if any(s.remaining[x.id] < x.deposit_paid_cents for x in items):
            warnings.append('单品优惠后货款低于其定金，不跨商品抵扣；请核对实际结算价')
            continue
        shipping = 0
        for fee in project.shipping_rules:
            matched = [x for x in items if x.shop_id == fee.shop_id]
            if not matched:
                continue
            if not fee.confirmed or not fee.evidence_note.strip():
                warnings.append('存在未确认运费配置，相关组不生成模拟报价：'+fee.shop_id)
                shipping = None
                break
            basis = base if fee.threshold_basis == 'base' else s.remaining
            amount = sum(basis[x.id] for x in matched)
            if fee.free_threshold_cents is None or amount < fee.free_threshold_cents:
                shipping += fee.fee_cents
        if shipping is None:
            continue
        total = sum(s.remaining.values()) + shipping
        key = (tuple(sorted(s.consumed.items())), s.expiry)
        if key not in best or total < best[key][2]:
            best[key] = s, shipping, total
    quotes = []
    for s, shipping, total in best.values():
        raw = json.dumps([sorted(item_ids), s.applied], ensure_ascii=False, separators=(',', ':'))
        quotes.append(Quote(id='model-'+hashlib.sha256(raw.encode()).hexdigest()[:16],
            item_ids=sorted(item_ids), cash_due_cents=total-sum(x.deposit_paid_cents for x in items),
            consumed=s.consumed, source='model', ledger_complete=True, portable_state=True,
            context_fingerprint=project.fingerprint(), captured_at=at, valid_from=at, valid_until=s.expiry,
            applied_rules=s.applied, discount_cents=sum(base.values())-sum(s.remaining.values()),
            discount_breakdown=s.breakdown,
            evidence_note='按已核对的计价基数和优惠规则估算；不是淘宝实时应付价',
            notes=['只对已配置规则计算，未识别权益不得视为不存在',
                   f'明确配置运费{shipping}分；其他店铺运费、税费需结算页核对',
                   '同一SKU行的数量不拆到多笔订单；赠品价值和未确认退款不计作现金收益']))
    return quotes, sorted(set(warnings))


def simulate_group(project: Project, item_ids: list[str], at=None, max_states=2048, deadline=None):
    """Include guarded fallbacks after an earlier payment exhausts best-of coupons.

    A lower automatic price is never marked unconditionally portable when it
    only exists after another coupon has been consumed. Guard thresholds are
    checked against the optimizer's actual resource balance at every step.
    """
    at = at or utcnow()
    deadline = deadline or time.monotonic()+5
    initial, warnings = _simulate_group(project, item_ids, at, max_states, deadline)
    resources = {r.id:r for r in project.resources}
    selected = [x for x in project.items if x.id in item_ids]
    costs = {}
    for rule in project.rules:
        if rule.confirmed and rule.selection_mode == 'best' and rule.resource_id and any(eligible(rule,x) for x in selected):
            costs[rule.resource_id] = costs.get(rule.resource_id,0)+rule.resource_units
    if not costs:
        return initial, warnings
    # Only best-of resources need state-dependent fallback prices. Larger
    # carts are budgeted and explicitly described as non-exhaustive.
    dimensions=[]
    for rid in sorted(costs):
        available=resources[rid].available
        levels=[available]+list(range(min(available,costs[rid],16)))
        if min(available,costs[rid])>16:
            warnings.append('自动择优券余额状态过多，部分余额情景未枚举')
        dimensions.append((rid,levels))
    total=1
    for _,levels in dimensions:total*=len(levels)
    for levels in itertools.islice(itertools.product(*(v for _,v in dimensions)),1,64):
        limits={rid:level for (rid,_),level in zip(dimensions,levels) if level<resources[rid].available}
        if not limits:continue
        if time.monotonic()>deadline:
            warnings.append('券余额情景达到时间上限，仅保留完整计算的情景');break
        scenario=project.model_copy(deep=True)
        for resource in scenario.resources:
            if resource.id in limits:resource.available=limits[resource.id]
        variants, extra=_simulate_group(scenario,item_ids,at,max_states,deadline)
        warnings.extend(extra)
        for quote in variants:
            guard={rid:level+1 for rid,level in limits.items()}
            suffix=hashlib.sha256(json.dumps(guard,sort_keys=True).encode()).hexdigest()[:8]
            initial.append(quote.model_copy(update={'id':quote.id+'-'+suffix,
                'context_fingerprint':project.fingerprint(),'requires_less_than':guard,
                'notes':quote.notes+['此报价仅在指定优惠券余额不足后生效，已计入付款顺序']}))
    if total>64:
        warnings.append('自动择优券余额情景达到64组上限，不构成全部付款状态的最优证明')
    return initial, sorted(set(warnings))


def explain_group(project: Project, item_ids: list[str]):
    quotes, warnings = simulate_group(project, item_ids)
    balances = {r.id:r.available for r in project.resources}
    usable = [q for q in quotes if all(balances[rid] < ceiling for rid,ceiling in q.requires_less_than.items())]
    chosen = min(usable, key=lambda q: q.cash_due_cents, default=None)
    items = [x for x in project.items if x.id in item_ids]
    base = {x.id: x.pricing_base_cents or 0 for x in items}
    state = State(base.copy(), {}, frozenset(), [], utcnow()+timedelta(minutes=10), [])
    applied = set(chosen.applied_rules if chosen else [])
    rows = []
    for rule in sorted(project.rules, key=lambda x:(x.phase,x.id)):
        option, reason = offer(project, rule, items, base, state, utcnow())
        used = rule.id in applied
        if used:
            result = apply(project, rule, items, base, state, utcnow())
            if result is not None:
                state = result
        rows.append({'id': rule.id, 'name': rule.name, 'applied': used,
                     'reason': '已采用' if used else (reason or '本组未采用：另一优惠更省、互斥或需留给其他付款组'),
                     'possible_discount_cents': option[0].discount_cents if option else 0})
    return {'quote': chosen.model_dump(mode='json') if chosen else None, 'rules': rows, 'warnings': warnings,
            'model_only': True}
