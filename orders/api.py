"""orders 域后台 API。"""

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from dashboard_api.views import (
    _apply_keyword_filter,
    _apply_recharge_status,
    _error,
    _get_keyword,
    _ok,
    _order_payload,
    _read_payload,
    _recharge_detail_payload,
    dashboard_login_required,
)
from orders.models import Order, Recharge


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
