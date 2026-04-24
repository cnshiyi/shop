"""orders 域后台 API。"""

from django.db import transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from bot.api import (
    _apply_keyword_filter,
    _decimal_to_str,
    _error,
    _get_keyword,
    _iso,
    _ok,
    _read_payload,
    _status_label,
    _user_payload,
    dashboard_login_required,
)
from orders.models import Order, Recharge


def _order_payload(order):
    user = order.user
    usernames = user.usernames if user else []
    user_payload = _user_payload({
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'username': user.username,
        'first_name': user.first_name,
        'usernames': usernames,
        'primary_username': usernames[0] if usernames else '',
    }) if user else None
    product = getattr(order, 'product', None)
    return {
        'id': order.id,
        'order_no': order.order_no,
        'product_id': order.product_id,
        'product_name': order.product_name,
        'product_label': product.name if product else order.product_name,
        'product_description': product.description if product else None,
        'quantity': order.quantity,
        'currency': order.currency,
        'total_amount': _decimal_to_str(order.total_amount),
        'pay_amount': _decimal_to_str(order.pay_amount) if order.pay_amount is not None else None,
        'pay_method': order.pay_method,
        'status': order.status,
        'status_label': _status_label(order.status, Order.STATUS_CHOICES),
        'tx_hash': order.tx_hash,
        'created_at': _iso(order.created_at),
        'paid_at': _iso(order.paid_at),
        'expired_at': _iso(order.expired_at),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
    }


@dashboard_login_required
@require_GET
def orders_list(request):
    keyword = _get_keyword(request)
    queryset = Order.objects.select_related('user', 'product').order_by('-created_at')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['order_no', 'product_name', 'status', 'tx_hash', 'user__tg_user_id', 'user__username', 'product__name'],
    )
    items = [_order_payload(item) for item in queryset[:100]]
    return _ok(items)


def _recharge_detail_payload(recharge):
    user = recharge.user
    usernames = user.usernames if user else []
    user_payload = _user_payload({
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'username': user.username,
        'first_name': user.first_name,
        'usernames': usernames,
        'primary_username': usernames[0] if usernames else '',
    }) if user else None
    return {
        'id': recharge.id,
        'amount': _decimal_to_str(recharge.amount),
        'currency': recharge.currency,
        'status': recharge.status,
        'status_label': _status_label(recharge.status, Recharge.STATUS_CHOICES),
        'tx_hash': recharge.tx_hash,
        'pay_amount': _decimal_to_str(recharge.pay_amount) if getattr(recharge, 'pay_amount', None) is not None else None,
        'receive_address': getattr(recharge, 'receive_address', None),
        'created_at': _iso(recharge.created_at),
        'completed_at': _iso(recharge.completed_at),
        'updated_at': _iso(getattr(recharge, 'updated_at', None)),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
    }


@transaction.atomic
def _apply_recharge_status(recharge, new_status):
    now = timezone.now()
    old_status = recharge.status
    allowed_statuses = {choice[0] for choice in Recharge.STATUS_CHOICES}
    if new_status not in allowed_statuses:
        raise ValueError('充值订单状态不正确')
    if new_status == old_status:
        return recharge

    user = recharge.user
    balance_field = 'balance_trx' if recharge.currency == 'TRX' else 'balance'

    if old_status == 'completed' and new_status != 'completed':
        current_balance = getattr(user, balance_field)
        if current_balance < recharge.amount:
            raise ValueError('用户余额不足，无法从已完成回退状态')
        setattr(user, balance_field, current_balance - recharge.amount)
        user.save(update_fields=[balance_field, 'updated_at'])
        recharge.completed_at = None

    if new_status == 'completed' and old_status != 'completed':
        setattr(user, balance_field, getattr(user, balance_field) + recharge.amount)
        user.save(update_fields=[balance_field, 'updated_at'])
        recharge.completed_at = recharge.completed_at or now
    elif new_status in {'pending', 'expired'}:
        recharge.completed_at = None

    recharge.status = new_status
    recharge.save(update_fields=['status', 'completed_at'])
    return recharge


@dashboard_login_required
@require_GET
def recharges_list(request):
    keyword = _get_keyword(request)
    queryset = Recharge.objects.select_related('user').order_by('-created_at')
    queryset = _apply_keyword_filter(queryset, keyword, ['id', 'currency', 'status', 'tx_hash', 'user__tg_user_id', 'user__username'])
    items = [_recharge_detail_payload(item) for item in queryset[:50]]
    return _ok(items)


@dashboard_login_required
@require_GET
def recharge_detail(request, recharge_id):
    recharge = Recharge.objects.select_related('user').filter(pk=recharge_id).first()
    if not recharge:
        return _error('充值订单不存在', status=404)
    return _ok(_recharge_detail_payload(recharge))


@csrf_exempt
@dashboard_login_required
@require_POST
def update_recharge_status(request, recharge_id):
    recharge = Recharge.objects.select_related('user').filter(pk=recharge_id).first()
    if not recharge:
        return _error('充值订单不存在', status=404)
    payload = _read_payload(request)
    new_status = str(payload.get('status') or '').strip()
    if not new_status:
        return _error('充值订单状态不能为空')
    try:
        recharge = _apply_recharge_status(recharge, new_status)
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f'更新充值订单状态失败: {exc}', status=500)
    return _ok(_recharge_detail_payload(recharge))


__all__ = [
    'orders_list',
    'recharge_detail',
    'recharges_list',
    'update_recharge_status',
]
