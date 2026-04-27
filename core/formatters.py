from decimal import Decimal, ROUND_DOWN


_MAX_DECIMAL = Decimal('0.001')


def fmt_amount(value) -> str:
    if value is None:
        return ''
    amount = Decimal(str(value)).quantize(_MAX_DECIMAL, rounding=ROUND_DOWN)
    text = format(amount, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def fmt_pay_amount(value) -> str:
    return fmt_amount(value)
