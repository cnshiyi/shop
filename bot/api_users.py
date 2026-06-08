"""Dashboard API views for Telegram users and balances."""

from decimal import Decimal

from django.db import ProgrammingError, transaction
from django.db.models import CharField, Q
from django.db.models.functions import Cast
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from bot.user_stats import active_proxy_counts_by_user as _active_proxy_counts_by_user
from core.dashboard_api import (
    _apply_keyword_filter,
    _decimal_to_str,
    _error,
    _get_keyword,
    _iso,
    _ok,
    _parse_decimal,
    _read_payload,
    _status_label,
    _user_payload,
    dashboard_login_required,
    dashboard_superuser_required,
)
from bot.models import DeletedTelegramUserSlot, TelegramUser
from cloud.models import (
    AddressMonitor,
    CloudAsset,
    CloudAssetDashboardSnapshot,
    CloudAutoRenewPatrolLog,
    CloudAutoRenewRetryTask,
    CloudIpLog,
    CloudLifecycleTask,
    CloudNoticeTask,
    CloudServerOrder,
    CloudUserNoticeLog,
    DailyAddressStat,
)
from orders.models import BalanceLedger, CartItem, Order, Recharge


def _record_balance_ledger(user, *, currency, old_balance, new_balance, ledger_type='manual_adjust', related_type=None, related_id=None, description='', operator=None):
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


def _ledger_payload(ledger):
    related_path = None
    if ledger.related_type == 'recharge' and ledger.related_id:
        related_path = f'/admin/recharges/{ledger.related_id}'
    elif ledger.related_type == 'cloud_order' and ledger.related_id:
        related_path = f'/admin/cloud-orders/{ledger.related_id}'
    return {
        'id': f'ledger-{ledger.id}',
        'type': ledger.type,
        'type_label': _status_label(ledger.type, BalanceLedger.TYPE_CHOICES),
        'currency': ledger.currency,
        'direction': ledger.direction,
        'direction_label': _status_label(ledger.direction, BalanceLedger.DIRECTION_CHOICES),
        'amount': _decimal_to_str(ledger.amount),
        'before_balance': _decimal_to_str(ledger.before_balance),
        'after_balance': _decimal_to_str(ledger.after_balance),
        'balance_field': 'balance_trx' if ledger.currency == 'TRX' else 'balance',
        'title': _status_label(ledger.type, BalanceLedger.TYPE_CHOICES),
        'description': ledger.description or _status_label(ledger.type, BalanceLedger.TYPE_CHOICES),
        'related_id': ledger.related_id,
        'related_type': ledger.related_type,
        'related_path': related_path,
        'created_at': _iso(ledger.created_at),
    }


@dashboard_login_required
@require_GET
def users_list(request):
    keyword = _get_keyword(request)
    try:
        queryset = TelegramUser.objects.order_by('-id')
        if keyword and keyword.isdigit():
            queryset = queryset.annotate(tg_user_id_text=Cast('tg_user_id', output_field=CharField()))
            queryset = queryset.filter(
                Q(id=int(keyword))
                | Q(tg_user_id=int(keyword))
                | Q(tg_user_id_text__icontains=keyword)
                | Q(username__icontains=keyword)
                | Q(first_name__icontains=keyword)
            )
        else:
            queryset = _apply_keyword_filter(queryset, keyword, ['username', 'first_name'])
        users = list(queryset.distinct())
    except ProgrammingError:
        queryset = TelegramUser.objects.order_by('-id')
        if keyword and keyword.isdigit():
            queryset = queryset.annotate(tg_user_id_text=Cast('tg_user_id', output_field=CharField()))
            queryset = queryset.filter(
                Q(id=int(keyword)) | Q(tg_user_id=int(keyword)) | Q(tg_user_id_text__icontains=keyword)
            )
        else:
            queryset = _apply_keyword_filter(queryset, keyword, ['username', 'first_name'])
        users = list(queryset.distinct())
    proxy_counts = _active_proxy_counts_by_user([user.id for user in users])
    users.sort(key=lambda user: (proxy_counts.get(user.id, 0), user.id), reverse=True)
    users = users[:50]
    return _ok([
        {
            **_user_payload({
                'id': user.id,
                'tg_user_id': user.tg_user_id,
                'username': user.username,
                'first_name': user.first_name,
                'balance': user.balance,
                'balance_trx': user.balance_trx,
                'cloud_discount_rate': user.cloud_discount_rate,
                'created_at': user.created_at,
                'usernames': user.usernames,
                'primary_username': user.usernames[0] if user.usernames else '',
            }),
            'balance': _decimal_to_str(user.balance),
            'balance_trx': _decimal_to_str(user.balance_trx),
            'cloud_discount_rate': _decimal_to_str(user.cloud_discount_rate),
            'created_at': _iso(user.created_at),
            'proxy_count': proxy_counts.get(user.id, 0),
        }
        for user in users
    ])


@csrf_exempt
@dashboard_superuser_required
@require_POST
def update_user_balance(request, user_id):
    payload = _read_payload(request)
    try:
        balance = _parse_decimal(payload.get('balance'), 'USDT余额')
        balance_trx = _parse_decimal(payload.get('balance_trx'), 'TRX余额')
        discount = _parse_decimal(payload.get('cloud_discount_rate'), '云服务器折扣') if 'cloud_discount_rate' in payload else None
    except ValueError as exc:
        return _error(str(exc), status=400)
    if balance < 0 or balance_trx < 0:
        return _error('余额不能为负数', status=400)
    if discount is not None and (discount <= 0 or discount > 100):
        return _error('云服务器折扣必须大于 0 且小于等于 100', status=400)

    try:
        with transaction.atomic():
            user = TelegramUser.objects.select_for_update().get(pk=user_id)
            old_balance = user.balance
            old_balance_trx = user.balance_trx
            user.balance = balance
            user.balance_trx = balance_trx
            update_fields = ['balance', 'balance_trx', 'updated_at']
            if discount is not None:
                user.cloud_discount_rate = discount
                update_fields.append('cloud_discount_rate')
            user.save(update_fields=update_fields)
            operator = getattr(request.user, 'username', '') or str(getattr(request.user, 'id', '') or '')
            _record_balance_ledger(
                user,
                currency='USDT',
                old_balance=old_balance,
                new_balance=balance,
                description='Dashboard 手动编辑 USDT 余额',
                operator=operator,
            )
            _record_balance_ledger(
                user,
                currency='TRX',
                old_balance=old_balance_trx,
                new_balance=balance_trx,
                description='Dashboard 手动编辑 TRX 余额',
                operator=operator,
            )
    except TelegramUser.DoesNotExist:
        return _error('用户不存在', status=404)

    return _ok({
        'id': user.id,
        'balance': _decimal_to_str(user.balance),
        'balance_trx': _decimal_to_str(user.balance_trx),
        'cloud_discount_rate': _decimal_to_str(user.cloud_discount_rate),
    })


@csrf_exempt
@dashboard_superuser_required
@require_POST
def update_user_discount(request, user_id):
    payload = _read_payload(request)
    try:
        discount = _parse_decimal(payload.get('cloud_discount_rate'), '云服务器折扣')
    except ValueError as exc:
        return _error(str(exc), status=400)
    if discount <= 0 or discount > 100:
        return _error('云服务器折扣必须大于 0 且小于等于 100', status=400)
    user = TelegramUser.objects.filter(pk=user_id).first()
    if not user:
        return _error('用户不存在', status=404)
    user.cloud_discount_rate = discount
    user.save(update_fields=['cloud_discount_rate', 'updated_at'])
    return _ok({
        'id': user.id,
        'cloud_discount_rate': _decimal_to_str(user.cloud_discount_rate),
    })


def _user_delete_blockers(user_id: int) -> list[str]:
    checks = [
        ('云服务器订单', CloudServerOrder.objects.filter(user_id=user_id)),
        ('商品订单', Order.objects.filter(user_id=user_id)),
        ('充值记录', Recharge.objects.filter(user_id=user_id)),
        ('余额流水', BalanceLedger.objects.filter(user_id=user_id)),
        ('购物车', CartItem.objects.filter(user_id=user_id)),
        ('地址监控', AddressMonitor.objects.filter(user_id=user_id)),
        ('每日地址统计', DailyAddressStat.objects.filter(user_id=user_id)),
    ]
    return [label for label, queryset in checks if queryset.exists()]


@csrf_exempt
@dashboard_superuser_required
@require_POST
def delete_user(request, user_id):
    try:
        with transaction.atomic():
            user = TelegramUser.objects.select_for_update().get(pk=user_id)
            blockers = _user_delete_blockers(user.id)
            if blockers:
                return _error(f'该用户存在业务记录，不能直接删除：{"、".join(blockers)}', status=400)
            deleted_snapshot = {
                'deleted_tg_user_id': user.tg_user_id,
                'deleted_username': user.username,
                'deleted_first_name': user.first_name,
            }
            unbound = {
                'assets': CloudAsset.objects.filter(user_id=user.id).update(user=None),
                'asset_snapshots': CloudAssetDashboardSnapshot.objects.filter(user_id=user.id).update(user=None),
                'ip_logs': CloudIpLog.objects.filter(user_id=user.id).update(user=None),
                'lifecycle_tasks': CloudLifecycleTask.objects.filter(user_id=user.id).update(user=None),
                'notice_tasks': CloudNoticeTask.objects.filter(user_id=user.id).update(user=None),
                'auto_renew_retry_tasks': CloudAutoRenewRetryTask.objects.filter(user_id=user.id).update(user=None),
                'auto_renew_patrol_logs': CloudAutoRenewPatrolLog.objects.filter(user_id=user.id).update(user=None),
                'notice_logs': CloudUserNoticeLog.objects.filter(user_id=user.id).update(user=None),
            }
            reusable_user_id = user.id
            user.delete()
            DeletedTelegramUserSlot.objects.update_or_create(
                reusable_user_id=reusable_user_id,
                defaults={
                    **deleted_snapshot,
                    'note': 'Dashboard 删除用户后释放 ID，供新 Telegram 用户优先复用',
                },
            )
    except TelegramUser.DoesNotExist:
        return _error('用户不存在', status=404)
    return _ok({
        'deleted': True,
        'reusable_user_id': reusable_user_id,
        'unbound': unbound,
    })


@dashboard_login_required
@require_GET
def user_balance_details(request, user_id):
    user = TelegramUser.objects.filter(pk=user_id).first()
    if not user:
        return _error('用户不存在', status=404)

    items = []

    recharges = Recharge.objects.filter(user_id=user_id, status='completed').order_by('-completed_at', '-created_at')[:200]
    for recharge in recharges:
        items.append({
            'id': f'recharge-{recharge.id}',
            'type': 'recharge',
            'type_label': '充值入账',
            'currency': recharge.currency,
            'direction': 'in',
            'direction_label': '收入',
            'amount': _decimal_to_str(recharge.amount),
            'balance_field': 'balance_trx' if recharge.currency == 'TRX' else 'balance',
            'title': f'充值 #{recharge.id}',
            'description': f'充值订单已完成，余额增加 {_decimal_to_str(recharge.amount)} {recharge.currency}',
            'related_id': recharge.id,
            'related_path': f'/admin/recharges/{recharge.id}',
            'created_at': _iso(recharge.completed_at or recharge.created_at),
        })

    orders = Order.objects.filter(user_id=user_id, pay_method='balance').exclude(status='pending').order_by('-paid_at', '-created_at')[:200]
    for order in orders:
        amount = order.pay_amount if order.pay_amount is not None else order.total_amount
        items.append({
            'id': f'order-{order.id}',
            'type': 'order_balance_pay',
            'type_label': '商品余额支付',
            'currency': order.currency,
            'direction': 'out',
            'direction_label': '支出',
            'amount': _decimal_to_str(amount),
            'balance_field': 'balance_trx' if order.currency == 'TRX' else 'balance',
            'title': f'商品订单 #{order.order_no}',
            'description': f'余额支付商品：{order.product_name}',
            'related_id': order.id,
            'related_path': None,
            'created_at': _iso(order.paid_at or order.created_at),
        })

    cloud_orders = CloudServerOrder.objects.filter(user_id=user_id, pay_method='balance').exclude(status='pending').order_by('-paid_at', '-created_at')[:200]
    for order in cloud_orders:
        amount = order.pay_amount if order.pay_amount is not None else order.total_amount
        items.append({
            'id': f'cloud-order-{order.id}',
            'type': 'cloud_order_balance_pay',
            'type_label': '云服务器余额支付',
            'currency': order.currency,
            'direction': 'out',
            'direction_label': '支出',
            'amount': _decimal_to_str(amount),
            'balance_field': 'balance_trx' if order.currency == 'TRX' else 'balance',
            'title': f'云订单 #{order.order_no}',
            'description': f'余额支付云服务器：{order.plan_name}',
            'related_id': order.id,
            'related_path': f'/admin/cloud-orders/{order.id}',
            'created_at': _iso(order.paid_at or order.created_at),
        })

    ledger_items = [_ledger_payload(ledger) for ledger in BalanceLedger.objects.filter(user_id=user_id).order_by('-created_at', '-id')[:300]]

    items.sort(key=lambda item: item['created_at'] or '', reverse=True)
    combined_items = [*ledger_items, *items]
    combined_items.sort(key=lambda item: item['created_at'] or '', reverse=True)

    return _ok({
        'user': {
            **_user_payload({
                'id': user.id,
                'tg_user_id': user.tg_user_id,
                'username': user.username,
                'first_name': user.first_name,
                'balance': user.balance,
                'balance_trx': user.balance_trx,
                'created_at': user.created_at,
                'usernames': user.usernames,
                'primary_username': user.usernames[0] if user.usernames else '',
            }),
            'balance': _decimal_to_str(user.balance),
            'balance_trx': _decimal_to_str(user.balance_trx),
            'created_at': _iso(user.created_at),
        },
        'items': combined_items[:300],
    })
