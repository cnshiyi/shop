from django import template
from django.db.models import Sum
from django.utils import timezone

from accounts.models import TelegramUser
from finance.models import Recharge
from mall.models import CloudServerOrder, Order
from monitoring.models import AddressMonitor

register = template.Library()


def _sum_or_zero(qs, field: str):
    value = qs.aggregate(total=Sum(field)).get('total')
    return value or 0


@register.simple_tag
def admin_metric(name: str):
    today = timezone.localdate()
    mapping = {
        'users_total': lambda: TelegramUser.objects.count(),
        'users_today': lambda: TelegramUser.objects.filter(created_at__date=today).count(),
        'cloud_total': lambda: CloudServerOrder.objects.count(),
        'cloud_pending': lambda: CloudServerOrder.objects.filter(status__in=['pending', 'paid', 'provisioning', 'renew_pending']).count(),
        'recharge_total': lambda: Recharge.objects.count(),
        'recharge_pending': lambda: Recharge.objects.filter(status='pending').count(),
        'monitor_total': lambda: AddressMonitor.objects.count(),
        'monitor_active': lambda: AddressMonitor.objects.filter(is_active=True).count(),
        'order_total': lambda: Order.objects.count(),
        'income_today': lambda: _sum_or_zero(Recharge.objects.filter(status='completed', completed_at__date=today), 'amount'),
        'expense_today': lambda: _sum_or_zero(Order.objects.filter(status__in=['paid', 'delivered'], paid_at__date=today), 'total_amount') + _sum_or_zero(CloudServerOrder.objects.filter(status__in=['completed', 'paid', 'provisioning'], paid_at__date=today), 'total_amount'),
        'profit_today': lambda: (_sum_or_zero(Recharge.objects.filter(status='completed', completed_at__date=today), 'amount') - (_sum_or_zero(Order.objects.filter(status__in=['paid', 'delivered'], paid_at__date=today), 'total_amount') + _sum_or_zero(CloudServerOrder.objects.filter(status__in=['completed', 'paid', 'provisioning'], paid_at__date=today), 'total_amount'))),
    }
    func = mapping.get(name)
    return func() if func else '-'
