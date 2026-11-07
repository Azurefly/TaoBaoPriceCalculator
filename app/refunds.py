"""Refund scenario accounting only. No refund submission or speculative coupon recovery."""
from .models import StrictModel, Money

class RefundScenario(StrictModel):
    cash_paid_total_cents: Money
    cash_refund_cents: Money
    stored_principal_spent_cents: Money = 0
    stored_principal_returned_cents: Money = 0
    return_shipping_cents: Money = 0
    additional_clawback_cents: Money = 0
    refund_amount_confirmed: bool = False
    all_adjustments_confirmed: bool = False
    baseline_keep_economic_cost_cents: Money | None = None


def evaluate_refund(s: RefundScenario) -> dict:
    if s.cash_refund_cents > s.cash_paid_total_cents:
        raise ValueError('退款现金不能大于已付现金；补偿/赔偿请另行核算，不作为凑单收益')
    if s.stored_principal_returned_cents > s.stored_principal_spent_cents:
        raise ValueError('退回购物金本金不能大于已消耗本金')
    net_cash = s.cash_paid_total_cents - s.cash_refund_cents + s.return_shipping_cents + s.additional_clawback_cents
    economic = net_cash+s.stored_principal_spent_cents-s.stored_principal_returned_cents
    return {
        'saving_vs_baseline_cents': None if s.baseline_keep_economic_cost_cents is None else s.baseline_keep_economic_cost_cents-economic,
        'net_cash_cost_cents': net_cash,
        'net_economic_cost_cents': net_cash+s.stored_principal_spent_cents-s.stored_principal_returned_cents,
        'status':'confirmed_accounting' if s.refund_amount_confirmed and s.all_adjustments_confirmed else 'scenario_only',
        'note':'只做费用核算，不执行退款，不推定平台允许套利；退回券/红包不按等额现金计价。',
        'warning':'额外追回优惠只填未体现在退款额中的部分，避免扣减两次。运费、赠品、税费等未确认时不得称确定收益。'
    }
