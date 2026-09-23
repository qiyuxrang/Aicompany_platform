from decimal import Decimal, InvalidOperation, localcontext

from django.conf import settings


class BudgetError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _text(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError
    return value


def _amount(value, *, zero=False):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError from None
    if not amount.is_finite() or amount < 0 or (not zero and amount == 0):
        raise ValueError
    return amount


def _sum(left, right):
    left_exponent = left.as_tuple().exponent
    right_exponent = right.as_tuple().exponent
    common_exponent = min(left_exponent, right_exponent)
    precision = max(
        len(left.as_tuple().digits) + left_exponent - common_exponent,
        len(right.as_tuple().digits) + right_exponent - common_exponent,
    ) + 1
    with localcontext() as context:
        context.prec = max(precision, 1)
        return left + right


def _policy(route):
    policy = getattr(settings, "PRODUCT_COST_POLICY", {})
    try:
        if not isinstance(policy, dict) or not isinstance(route, str) or not route or route != route.strip():
            raise ValueError
        approval_ref = _text(policy["approval_ref"])
        currency = _text(policy["currency"])
        maximum = _amount(policy["max_task_cost"])
        caps = policy["route_cost_caps"]
        if not isinstance(caps, dict) or not caps:
            raise ValueError
        parsed_caps = {}
        for name, value in caps.items():
            parsed_caps[_text(name)] = _amount(value)
        cap = parsed_caps[route]
    except (KeyError, TypeError, ValueError):
        raise BudgetError("budget_authorization_required") from None
    return approval_ref, currency, maximum, cap


def _ledger(checkpoint):
    ledger = checkpoint.get("budget")
    if ledger is None:
        return None, Decimal("0"), []
    try:
        if not isinstance(ledger, dict) or ledger.get("version") != 1:
            raise ValueError
        approval_ref = _text(ledger["approval_ref"])
        currency = _text(ledger["currency"])
        maximum = _amount(ledger["max_task_cost"])
        reserved = _amount(ledger["reserved"])
        evidence = ledger["evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise ValueError
        running = Decimal("0")
        for sequence, item in enumerate(evidence, 1):
            if not isinstance(item, dict) or item.get("sequence") != sequence:
                raise ValueError
            _text(item["approval_ref"])
            _text(item["route"])
            if _text(item["currency"]) != currency:
                raise ValueError
            amount = _amount(item["amount"])
            item_maximum = _amount(item["max_task_cost"])
            running = _sum(running, amount)
            if _amount(item["reserved"]) != running or running > item_maximum:
                raise ValueError
        if (running != reserved or reserved > maximum
                or evidence[-1]["approval_ref"] != approval_ref
                or _amount(evidence[-1]["max_task_cost"]) != maximum):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise BudgetError("budget_ledger_invalid") from None
    return ledger, reserved, evidence


def reserve_call(task, route):
    approval_ref, currency, maximum, cap = _policy(route)
    if not isinstance(task.checkpoint, dict):
        raise BudgetError("budget_ledger_invalid")
    ledger, reserved, prior_evidence = _ledger(task.checkpoint)
    if ledger is not None and ledger["currency"] != currency:
        raise BudgetError("budget_currency_mismatch")
    if reserved > maximum:
        raise BudgetError("budget_exceeded")
    updated_reserved = _sum(reserved, cap)
    if updated_reserved > maximum:
        raise BudgetError("budget_exceeded")

    evidence = {
        "sequence": len(prior_evidence) + 1,
        "approval_ref": approval_ref,
        "route": route,
        "currency": currency,
        "amount": str(cap),
        "reserved": str(updated_reserved),
        "max_task_cost": str(maximum),
    }
    budget = {
        "version": 1,
        "approval_ref": approval_ref,
        "currency": currency,
        "max_task_cost": str(maximum),
        "reserved": str(updated_reserved),
        "evidence": [*prior_evidence, evidence],
    }
    task.checkpoint = {**task.checkpoint, "budget": budget}
    task.save(update_fields=["checkpoint", "updated_at"])
    return {
        "remaining": str(_sum(maximum, -updated_reserved)),
        "reserved": str(updated_reserved),
        "evidence": {key: evidence[key] for key in ("sequence", "route", "currency", "amount", "reserved")},
    }
