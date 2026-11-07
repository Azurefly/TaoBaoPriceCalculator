"""Readiness checks, bounded strategy comparison, and manual payment bookkeeping."""
from __future__ import annotations
from .models import Project, PaymentRecord, SolveOptions, utcnow
from .optimizer import optimize


def record_payment(project: Project, record: PaymentRecord) -> Project:
    existing = next((p for p in project.payments if p.id == record.id), None)
    if existing:
        if existing != record:
            raise ValueError('相同付款记录ID已存在但内容不一致')
        return project.model_copy(deep=True)
    if not set(record.item_ids) <= {i.id for i in project.items}:
        raise ValueError('付款记录包含未知商品')
    if set(record.item_ids) & project.paid_item_ids():
        raise ValueError('商品已登记付款，不能重复扣减权益')
    resources = {r.id: r for r in project.resources}
    if not set(record.consumed.keys() | record.granted.keys()) <= set(resources):
        raise ValueError('付款记录含未知权益；请先录入实际获得的权益实例')
    for rid, units in record.consumed.items():
        if units > resources[rid].available:
            raise ValueError(f'权益 {rid} 的实际消耗超过账本余额，请先核对余额')
    updated = project.model_copy(deep=True)
    for r in updated.resources:
        r.available += record.granted.get(r.id, 0) - record.consumed.get(r.id, 0)
    updated.payments.append(record)
    updated.audit_notes.append(f'已手工登记付款 {record.id}；旧报价因状态改变失效，剩余订单需重新核价。')
    return Project.model_validate(updated.model_dump())


def diagnose(project: Project) -> dict:
    paid = project.paid_item_ids()
    remaining = [x for x in project.items if x.id not in paid]
    required = [x for x in remaining if x.required]
    fingerprint = project.fingerprint()
    at = utcnow()
    issues = []
    def issue(code, level, title, ids, action):
        if ids:
            issues.append({'code':code, 'level':level, 'title':title, 'ids':ids, 'action':action})
    issue('missing_base', 'model_blocker', '规则模拟缺少可靠基数',
          [x.id for x in remaining if x.pricing_base_cents is None or not x.pricing_base_evidence],
          '无需倒推展示价；补计价来源，或直接录入真实结算报价。')
    issue('missing_deposit', 'model_blocker', '定金口径未核对',
          [x.id for x in remaining if not x.deposit_confirmed or x.deposit_paid_cents is None],
          '现货明确填0；预售按订单核实，仍需支付金额不能再减定金。')
    current = [q for q in project.quotes if q.context_fingerprint == fingerprint and not set(q.item_ids)&paid]
    valid = [q for q in current if q.source in ('manual','checkout') and q.ledger_complete
             and (not q.valid_until or q.valid_until > at)
             and 0 <= (at-q.captured_at).total_seconds() <= 600]
    covered = {i for q in valid for i in q.item_ids}
    issue('missing_checkout', 'strict_blocker', '没有近期已核对的真实报价',
          [x.id for x in required if x.id not in covered],
          '采集这些商品的单品/候选组合应付金额及完整权益消耗，再运行严格优化。')
    issue('stale_context', 'warning', '项目变更后的旧报价已隔离',
          [q.id for q in project.quotes if q.context_fingerprint != fingerprint],
          '重新核价，不要将旧报价直接标成有效。')
    issue('draft_ledger', 'warning', '报价尚缺完整优惠账本',
          [q.id for q in current if not q.ledger_complete or q.source == 'draft'],
          '逐张确认券/红包/购物金消耗；只有应付总额不足以证明拆单可兑现。')
    issue('unknown_shipping', 'model_warning', '模拟未配置这些店铺的运费',
          sorted({x.shop_id or '未标注店铺' for x in remaining} - {s.shop_id for s in project.shipping_rules}),
          '真实报价需已含运费；模拟可在项目JSON配置shipping_rules，未配置不等于确认免邮。')
    issue('expired_resource', 'warning', '这些权益已过期',
          [r.id for r in project.resources if r.available and r.ends_at and r.ends_at <= at],
          '优化器会检查每笔完成时间；过期权益不可再消耗。')
    return {'remaining_required': len(required), 'paid_lines': len(paid), 'issues': issues,
            'current_checkout_quotes': len(valid), 'project_fingerprint': fingerprint,
            'note': '覆盖诊断不等于可执行方案；共享资源、付款顺序与有效期仍须完整搜索。'}


def compare_strategies(project: Project, opts: SolveOptions) -> dict:
    """Every baseline uses the SAME resource/time constraints. Never sums isolated minima."""
    rows = []
    pending = [i.id for i in project.items if i.required and i.id not in project.paid_item_ids()]
    options = opts.model_copy(update={'max_seconds': min(opts.max_seconds, 5)})
    for key, name in [('optimized','自由拆合与顺序优化'), ('single','逐件付款'), ('merged','一次合并付款')]:
        p = project.model_copy(deep=True)
        if key == 'single':
            p.quotes = [q for q in p.quotes if len(q.item_ids) == 1]
        elif key == 'merged':
            p.quotes = [q for q in p.quotes if set(pending) <= set(q.item_ids)]
        result = optimize(p, options.model_copy(update={'max_orders': 1}) if key=='merged' else options)
        rows.append({'key':key, 'name':name, 'result':result})
    feasible = [r for r in rows if r['result']['economic_cost_cents'] is not None]
    recommended = min(feasible, key=lambda r:(r['result']['economic_cost_cents'],
                        r['result']['cash_due_cents'], r['result']['order_count'])) if feasible else None
    return {'strategies': rows, 'recommended_key': recommended['key'] if recommended else None,
            'note': '同一报价集合、相同权益约束下比较；截断搜索只代表已找到方案，未知对照不填写虚构节省。'}
