from decimal import Decimal


def fmt_amount(value) -> str:
    if value is None:
        return ''
    text = str(Decimal(value))
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def fmt_pay_amount(value) -> str:
    if value is None:
        return ''
    return str(Decimal(value).quantize(Decimal('0.001')))
