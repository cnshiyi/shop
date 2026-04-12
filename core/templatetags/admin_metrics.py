from django import template
from django.utils import timezone

from accounts.models import TelegramUser
from finance.models import Recharge
from mall.models import CloudServerOrder, Order
from monitoring.models import AddressMonitor

register = template.Library()


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
    }
    func = mapping.get(name)
    return func() if func else '-'
