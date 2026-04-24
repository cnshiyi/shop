from decimal import Decimal

from orders.models import BalanceLedger


def record_balance_ledger(user, *, ledger_type, currency, old_balance, new_balance, related_type=None, related_id=None, description=None, operator=None):
    old_balance = Decimal(str(old_balance or 0))
    new_balance = Decimal(str(new_balance or 0))
    delta = new_balance - old_balance
    if delta == 0:
        return None
    return BalanceLedger.objects.create(
        user=user,
        type=ledger_type,
        direction=BalanceLedger.DIRECTION_IN if delta > 0 else BalanceLedger.DIRECTION_OUT,
        currency=currency,
        amount=abs(delta),
        before_balance=old_balance,
        after_balance=new_balance,
        related_type=related_type,
        related_id=related_id,
        description=description,
        operator=operator,
    )
