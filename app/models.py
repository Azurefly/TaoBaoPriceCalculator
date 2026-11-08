from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]
Units = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Item(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=400)
    shop_id: str = ''
    shop_name: str = ''
    source_line_id: str = ''
    product_id: str = ''
    cart_group_ids: list[str] = Field(default_factory=list)
    cart_group_names: list[str] = Field(default_factory=list)
    cart_scope: str = ''
    sku: str = ''
    quantity: int = Field(default=1, ge=1, le=999)
    required: bool = True
    tags: list[str] = Field(default_factory=list)
    display_price_cents: Money | None = None
    display_price_kind: Literal['unknown', 'estimated_total', 'sale_total', 'payable_now'] = 'unknown'
    # This is the line total for the selected quantity, NOT a unit price.
    pricing_base_cents: Money | None = None
    pricing_base_evidence: str = ''
    deposit_paid_cents: Money | None = None
    deposit_confirmed: bool = False
    notes: str = ''


class Resource(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    name: str
    kind: Literal['coupon', 'redpacket', 'coins', 'stored_value', 'quota', 'other'] = 'coupon'
    available: Units = 1
    # Monetary opportunity cost PER UNIT. Coupons are normally 0; stored value cents normally 1.
    opportunity_cost_cents: Money = 0
    notes: str = ''
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @model_validator(mode='after')
    def resource_window(self):
        for value in (self.starts_at, self.ends_at):
            if value is not None and value.tzinfo is None:
                raise ValueError('权益时间必须包含时区')
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValueError('权益有效区间为空')
        return self


class DiscountTier(StrictModel):
    threshold_cents: Money = 0
    min_quantity: int = Field(default=1, ge=1, le=999)
    amount_cents: Money = 0


class Rule(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    name: str
    kind: Literal['fixed', 'percent', 'repeat_threshold', 'quantity_fixed', 'tiered', 'bundle_price'] = 'fixed'
    phase: int = Field(default=10, ge=0, le=999)
    # 官方四层：单品级 / 店铺级 / 跨店级 / 个人资产类。空字符串=未标注，按旧行为处理。
    layer: Literal['', 'unit', 'shop', 'cross', 'asset'] = ''
    # 跨店满减的档位（如 "300-50"）。不同档位不能在同一单里合并凑单。
    band: str = ''
    # 优惠族（如 "category_coupon"）。同族在同一件商品上只生效一个。
    family: str = ''
    # 与 family 配合：同一商品最多能用几个同族优惠。family 非空时默认 1。
    per_item_limit: int | None = Field(default=None, ge=1, le=5)
    selection_mode: Literal['optional', 'mandatory', 'best'] = 'optional'
    # 官方口径默认“平行”：店铺级/跨店级门槛按单品优惠价（base）判。
    # 只有官方立减与个人资产类（红包/淘金币）才显式改成 remaining。
    threshold_basis: Literal['base', 'remaining'] = 'base'
    discount_basis: Literal['base', 'remaining'] = 'base'
    threshold_cents: Money = 0
    amount_cents: Money = 0
    discount_bps: int = Field(default=0, ge=0, le=10000)
    cap_cents: Money | None = None
    min_quantity: int = Field(default=1, ge=1)
    item_ids: list[str] = Field(default_factory=list)
    # Qualifying scope and discounted scope are separate. Empty qualifying scope
    # inherits the discounted scope; it NEVER implicitly includes other stores.
    qualifying_item_ids: list[str] = Field(default_factory=list)
    excluded_item_ids: list[str] = Field(default_factory=list)
    required_item_quantities: dict[str, Annotated[int, Field(strict=True, ge=1, le=999)]] = Field(default_factory=dict)
    min_distinct_items: int = Field(default=1, ge=1, le=28)
    tiers: list[DiscountTier] = Field(default_factory=list, max_length=20)
    exclusive_scope: Literal['payment', 'item'] = 'payment'
    shop_ids: list[str] = Field(default_factory=list)
    item_tags: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    resource_id: str | None = None
    resource_units: Units = 1
    # Rules in the SAME non-empty group are mutually exclusive within one payment.
    exclusive_group: str = ''
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    confirmed: bool = False
    source_note: str = ''

    @model_validator(mode='after')
    def valid_rule(self):
        if self.kind == 'bundle_price' and (not self.item_ids or set(self.item_ids) != set(self.required_item_quantities)):
            raise ValueError('套餐一口价必须明确每一种套餐商品及其精确数量；多套或拆数量请单独核价')
        if self.kind == 'tiered' and not self.tiers:
            raise ValueError('阶梯优惠至少需要一档门槛和优惠金额')
        if self.selection_mode == 'best' and not self.exclusive_group:
            raise ValueError('自动择优优惠必须指定互斥组')
        if any(type(v) is not int or not 1 <= v <= 999 for v in self.required_item_quantities.values()):
            raise ValueError('组合中每件商品的要求数量必须是1至999的整数')
        if set(self.excluded_item_ids) & (set(self.required_item_quantities) | set(self.item_ids)):
            raise ValueError('指定或必选商品不能同时被排除')
        if self.kind == 'repeat_threshold' and not self.threshold_cents:
            raise ValueError('repeat_threshold 必须设置大于0的门槛')
        # 单品级与个人资产类不参与门槛判定：门槛字段必须为 0，否则规则写错了。
        if self.layer in ('unit', 'asset') and self.threshold_cents:
            raise ValueError('单品级/个人资产类优惠不参与门槛判定，门槛必须为 0')
        # 跨店满减没有档位就无法判断能否与其他档位合并；能推则自动补上。
        if self.layer == 'cross' and self.kind in ('repeat_threshold', 'tiered') and not self.band:
            if self.kind == 'repeat_threshold':
                self.band = f'{self.threshold_cents}-{self.amount_cents}'
            else:
                raise ValueError('跨店阶梯优惠必须写明档位 band')
        if self.family and self.per_item_limit is None:
            self.per_item_limit = 1
        if self.resource_id and not self.resource_units:
            raise ValueError('用券必须消耗至少一个资源单位')
        for dt in (self.starts_at, self.ends_at):
            if dt and dt.tzinfo is None:
                raise ValueError('时间必须包含时区，例如 +08:00')
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError('规则结束时间必须晚于开始时间')
        return self


class DiscountBreakdown(StrictModel):
    rule_id: str
    name: str
    qualifying_item_ids: list[str]
    discounted_item_ids: list[str]
    qualifying_cents: Money
    discount_cents: Money
    allocations: dict[str, Money]


class Quote(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    item_ids: list[str] = Field(min_length=1)
    # ALWAYS the incremental amount still payable for this group; deposits are not subtracted again.
    cash_due_cents: Money
    consumed: dict[str, Units] = Field(default_factory=dict)
    granted: dict[str, Units] = Field(default_factory=dict)
    requires_done: list[str] = Field(default_factory=list)
    requires_zero: list[str] = Field(default_factory=list)
    # Conditional fallback prices: resource balance must be below this bound.
    requires_less_than: dict[str, Units] = Field(default_factory=dict)
    extra_cost_cents: Money = 0
    source: Literal['checkout', 'manual', 'model', 'draft', 'demo'] = 'draft'
    ledger_complete: bool = False
    portable_state: bool = False
    # When state is NOT portable, balance equality is mandatory (no independent-quote assumption).
    observed_balances: dict[str, Units] = Field(default_factory=dict)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    captured_at: datetime = Field(default_factory=utcnow)
    context_fingerprint: str = ''
    evidence_note: str = ''
    applied_rules: list[str] = Field(default_factory=list)
    discount_cents: Money = 0
    discount_breakdown: list[DiscountBreakdown] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def valid_quote(self):
        if len(self.item_ids) != len(set(self.item_ids)):
            raise ValueError('报价内商品ID不能重复')
        if len(self.requires_done) != len(set(self.requires_done)):
            raise ValueError('前置商品ID不能重复')
        if set(self.requires_done) & set(self.item_ids):
            raise ValueError('同一报价不能购买其前置已购商品')
        if len(self.requires_zero) != len(set(self.requires_zero)):
            raise ValueError('余额归零条件不能重复')
        for dt in (self.valid_from, self.valid_until, self.captured_at):
            if dt and dt.tzinfo is None:
                raise ValueError('报价时间必须包含时区')
        if self.valid_from and self.valid_until and self.valid_from >= self.valid_until:
            raise ValueError('报价有效区间为空')
        return self


class ShippingRule(StrictModel):
    """Explicit per-checkout per-shop flat shipping. No automatic platform assumptions."""
    shop_id: str = Field(min_length=1, max_length=160)
    fee_cents: Money = 0
    free_threshold_cents: Money | None = None
    threshold_basis: Literal['base', 'remaining'] = 'remaining'
    confirmed: bool = False
    evidence_note: str = ''


class PaymentRecord(StrictModel):
    """User-entered completed payment, never an instruction to submit an order."""
    id: str = Field(min_length=1, max_length=160)
    quote_id: str | None = None
    item_ids: list[str] = Field(min_length=1)
    cash_paid_cents: Money
    extra_cost_cents: Money = 0
    consumed: dict[str, Units] = Field(default_factory=dict)
    granted: dict[str, Units] = Field(default_factory=dict)
    paid_at: datetime = Field(default_factory=utcnow)
    evidence_note: str = Field(min_length=1)

    @model_validator(mode='after')
    def payment_valid(self):
        if len(self.item_ids) != len(set(self.item_ids)):
            raise ValueError('付款记录商品重复')
        if self.paid_at.tzinfo is None:
            raise ValueError('实际付款时间必须包含时区')
        if not self.evidence_note.strip():
            raise ValueError('须填写已完成付款的核对备注，不填写账号或订单隐私')
        return self


class Project(StrictModel):
    schema_version: int = Field(default=2, ge=1, le=2)
    name: str = '我的购物计划'
    channel: str = 'web'
    # Opaque local labels only: do not enter full addresses or account credentials.
    account_context: str = 'local-account-1'
    delivery_context: str = 'address-1'
    payment_context: str = 'default'
    items: list[Item] = Field(default_factory=list, max_length=28)
    resources: list[Resource] = Field(default_factory=list, max_length=80)
    rules: list[Rule] = Field(default_factory=list, max_length=80)
    quotes: list[Quote] = Field(default_factory=list, max_length=25000)
    candidate_groups: list[list[str]] = Field(default_factory=list, max_length=5000)
    audit_notes: list[str] = Field(default_factory=list)
    shipping_rules: list[ShippingRule] = Field(default_factory=list, max_length=28)
    payments: list[PaymentRecord] = Field(default_factory=list, max_length=28)

    @model_validator(mode='after')
    def references(self):
        for label, seq in [('商品', self.items), ('资源', self.resources), ('规则', self.rules), ('报价', self.quotes), ('付款记录', self.payments)]:
            ids = [x.id for x in seq]
            if len(ids) != len(set(ids)):
                raise ValueError(f'{label}ID重复；同面额但不同张券必须具有不同ID')
        items = {x.id for x in self.items}
        resources = {x.id for x in self.resources}
        for rule in self.rules:
            if rule.resource_id and rule.resource_id not in resources:
                raise ValueError(f'规则{rule.id}引用了不存在的资源')
            if not (set(rule.item_ids + rule.qualifying_item_ids + rule.excluded_item_ids) | set(rule.required_item_quantities)) <= items:
                raise ValueError(f'规则{rule.id}引用了不存在的商品')
        best_groups = {}
        for rule in self.rules:
            if rule.selection_mode == 'best':
                key = rule.exclusive_group
                policy = (rule.phase, rule.exclusive_scope)
                if key in best_groups and best_groups[key] != policy:
                    raise ValueError('同一自动择优组的阶段和互斥范围必须一致')
                best_groups[key] = policy
        if any(r.exclusive_group in best_groups and r.selection_mode != 'best' for r in self.rules):
            raise ValueError('自动择优组不能混入手动或强制叠加规则')
        for q in self.quotes:
            if not set(q.item_ids + q.requires_done) <= items:
                raise ValueError(f'报价{q.id}引用了不存在的商品')
            if not set(q.consumed.keys() | q.granted.keys() | q.observed_balances.keys() | set(q.requires_zero) | q.requires_less_than.keys()) <= resources:
                raise ValueError(f'报价{q.id}引用了不存在的券/红包资源')
        paid = set()
        for payment in self.payments:
            if not set(payment.item_ids) <= items:
                raise ValueError('付款记录包含未知商品')
            if paid & set(payment.item_ids):
                raise ValueError('同一商品不能重复记录付款')
            if not set(payment.consumed.keys() | payment.granted.keys()) <= resources:
                raise ValueError('付款记录引用了不存在的权益')
            paid.update(payment.item_ids)
        shops = [r.shop_id for r in self.shipping_rules]
        if len(shops) != len(set(shops)):
            raise ValueError('每店仅允许一条运费配置；更复杂条件请使用真实结算报价')
        if any(not set(group) <= items or len(group) != len(set(group)) for group in self.candidate_groups):
            raise ValueError('候选组包含未知或重复商品')
        return self

    def paid_item_ids(self) -> set[str]:
        return {item_id for payment in self.payments for item_id in payment.item_ids}

    def fingerprint(self) -> str:
        # Quotes and candidate order are not pricing inputs. All other economics are versioned.
        data = self.model_dump(mode='json', exclude={'quotes', 'candidate_groups', 'audit_notes'})
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        return hashlib.sha256(raw.encode()).hexdigest()[:24]


class SolveOptions(StrictModel):
    mode: Literal['strict', 'model'] = 'strict'
    starts_at: datetime = Field(default_factory=utcnow)
    step_seconds: int = Field(default=60, ge=0, le=3600)
    max_orders: int = Field(default=28, ge=1, le=28)
    max_nodes: int = Field(default=150000, ge=100, le=2_000_000)
    max_seconds: float = Field(default=8, ge=0.05, le=30)
    max_quote_age_seconds: int = Field(default=600, ge=1, le=86400)
    # Historical replay freezes validity at its explicit time, not at today's date.
    replay: bool = False

    @model_validator(mode='after')
    def timezone(self):
        if self.starts_at.tzinfo is None:
            raise ValueError('计划开始时间必须包含时区')
        return self
