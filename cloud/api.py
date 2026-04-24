"""cloud 域后台 API。"""

from decimal import Decimal, InvalidOperation

from asgiref.sync import async_to_sync
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models import Case, CharField, Count, Q, Value, When
from django.db.models.functions import Cast
from django.db.utils import ProgrammingError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from cloud.services import ensure_cloud_server_pricing, refresh_custom_plan_cache
from cloud.models import AddressMonitor, CloudAsset, CloudServerOrder, CloudServerPlan, Server, ServerPrice
from cloud.provisioning import provision_cloud_server
from dashboard_api.views import (
    _apply_keyword_filter,
    _days_left,
    _decimal_to_str,
    _get_keyword,
    _iso,
    _error,
    _ok,
    _parse_decimal,
    _provider_label,
    _read_payload,
    _region_label,
    _server_source_label,
    _status_label,
    _user_payload,
    dashboard_login_required,
    update_cloud_asset,
)


def _asset_payload(asset):
    user = asset.user
    order = asset.order
    user_payload = None
    if user:
        usernames = user.usernames
        user_payload = _user_payload({
            'id': user.id,
            'tg_user_id': user.tg_user_id,
            'username': user.username,
            'first_name': user.first_name,
            'usernames': usernames,
            'primary_username': usernames[0] if usernames else '',
        })
    expires_at = asset.actual_expires_at or getattr(order, 'service_expires_at', None)
    return {
        'id': asset.id,
        'kind': asset.kind,
        'source': asset.source,
        'source_label': _server_source_label(asset.source),
        'provider': asset.provider,
        'provider_label': _provider_label(asset.provider),
        'region_label': _region_label(getattr(asset, 'region_code', None), asset.region_name),
        'region_name': asset.region_name,
        'asset_name': asset.asset_name,
        'instance_id': asset.instance_id,
        'provider_resource_id': asset.provider_resource_id,
        'public_ip': asset.public_ip,
        'mtproxy_link': asset.mtproxy_link,
        'mtproxy_port': asset.mtproxy_port,
        'mtproxy_secret': asset.mtproxy_secret,
        'mtproxy_host': asset.mtproxy_host,
        'note': asset.note,
        'actual_expires_at': _iso(expires_at),
        'days_left': _days_left(expires_at),
        'status_countdown': f"剩余 {_days_left(expires_at)} 天" if _days_left(expires_at) is not None else '-',
        'price': _decimal_to_str(asset.price if asset.price is not None else (order.total_amount if order and order.total_amount is not None else None), 2),
        'currency': asset.currency or (order.currency if order else ''),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'order_id': order.id if order else None,
        'order_no': order.order_no if order else '',
        'status': asset.status,
        'status_label': _status_label(asset.status, CloudAsset.STATUS_CHOICES),
        'provider_status': '已删除' if asset.status == CloudAsset.STATUS_DELETED else asset.provider_status,
        'is_active': asset.is_active,
        'updated_at': _iso(asset.updated_at),
    }


@dashboard_login_required
@require_GET
def cloud_assets_list(request):
    keyword = _get_keyword(request)
    grouped = (request.GET.get('grouped') or '').lower() in {'1', 'true', 'yes'}
    try:
        queryset = CloudAsset.objects.select_related('user', 'order')
        queryset = _apply_keyword_filter(
            queryset,
            keyword,
            [
                'asset_name', 'public_ip', 'mtproxy_link', 'user__tg_user_id',
                'user__username', 'order__order_no',
            ],
        ).distinct().order_by('actual_expires_at', '-updated_at', '-id')
        items = [_asset_payload(asset) for asset in queryset[:200]]
    except ProgrammingError:
        return _ok({'groups': [], 'items': []} if grouped else [])

    if not grouped:
        return _ok(items)

    groups = {}
    for item in items:
        key = str(item['tg_user_id'] or 'unbound')
        group = groups.setdefault(key, {
            'user_key': key,
            'tg_user_id': item['tg_user_id'],
            'user_display_name': item['user_display_name'],
            'username_label': item['username_label'],
            'default_expanded': True,
            'items': [],
        })
        group['items'].append(item)
    ordered_groups = list(groups.values())
    ordered_groups.sort(key=lambda group: (
        min((row['actual_expires_at'] or '9999-12-31T23:59:59') for row in group['items']),
        str(group['tg_user_id'] or 'zzzz'),
    ))
    return _ok({'groups': ordered_groups, 'items': items})


@dashboard_login_required
@require_GET
def tasks_overview(request):
    orders = CloudServerOrder.objects.order_by('-updated_at')[:50]
    items = []
    for order in orders:
        if order.status not in {'paid', 'provisioning', 'renew_pending', 'expiring', 'suspended', 'deleting', 'failed'}:
            continue
        items.append({
            'id': order.id,
            'order_no': order.order_no,
            'task_type': 'cloud_order',
            'task_label': '云服务器任务',
            'status': order.status,
            'status_label': dict(CloudServerOrder.STATUS_CHOICES).get(order.status, order.status),
            'provider': order.provider,
            'provider_label': _provider_label(order.provider),
            'plan_name': order.plan_name,
            'public_ip': order.public_ip,
            'note': order.provision_note,
            'created_at': _iso(order.created_at),
            'updated_at': _iso(order.updated_at),
            'related_path': f'/admin/cloud-orders/{order.id}',
        })
    return _ok(items)


def _cloud_order_detail_payload(order):
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
    return {
        'id': order.id,
        'order_no': order.order_no,
        'provider': order.provider,
        'region_code': order.region_code,
        'region_label': _region_label(order.region_code, order.region_name),
        'region_name': order.region_name,
        'plan_name': order.plan_name,
        'quantity': order.quantity,
        'currency': order.currency,
        'total_amount': _decimal_to_str(order.total_amount),
        'pay_amount': _decimal_to_str(order.pay_amount) if order.pay_amount is not None else None,
        'pay_method': order.pay_method,
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'tx_hash': order.tx_hash,
        'image_name': order.image_name,
        'server_name': order.server_name,
        'lifecycle_days': order.lifecycle_days,
        'service_started_at': _iso(order.service_started_at),
        'service_expires_at': _iso(order.service_expires_at),
        'renew_grace_expires_at': _iso(order.renew_grace_expires_at),
        'suspend_at': _iso(order.suspend_at),
        'delete_at': _iso(order.delete_at),
        'ip_recycle_at': _iso(order.ip_recycle_at),
        'last_renewed_at': _iso(order.last_renewed_at),
        'last_user_id': order.last_user_id,
        'mtproxy_port': order.mtproxy_port,
        'mtproxy_link': order.mtproxy_link,
        'mtproxy_secret': order.mtproxy_secret,
        'mtproxy_host': order.mtproxy_host,
        'instance_id': order.instance_id,
        'provider_resource_id': order.provider_resource_id,
        'static_ip_name': order.static_ip_name,
        'public_ip': order.public_ip,
        'previous_public_ip': order.previous_public_ip,
        'login_user': order.login_user,
        'login_password': order.login_password,
        'provision_note': order.provision_note,
        'created_at': _iso(order.created_at),
        'paid_at': _iso(order.paid_at),
        'expired_at': _iso(order.expired_at),
        'completed_at': _iso(order.completed_at),
        'updated_at': _iso(order.updated_at),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'plan_id': order.plan_id,
    }


@dashboard_login_required
@require_GET
def cloud_orders_list(request):
    keyword = _get_keyword(request)
    queryset = CloudServerOrder.objects.select_related('user', 'plan').order_by('-created_at')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['order_no', 'provider', 'region_name', 'plan_name', 'status', 'public_ip', 'user__tg_user_id', 'user__username'],
    )
    items = [_cloud_order_detail_payload(item) for item in queryset[:100]]
    now = timezone.now()
    for item in items:
        status = item.get('status')
        service_expires_at = item.get('service_expires_at')
        renew_grace_expires_at = item.get('renew_grace_expires_at')
        delete_at = item.get('delete_at')
        auto_renew_enabled = bool(item.get('auto_renew_enabled'))

        service_expires_dt = parse_datetime(service_expires_at) if isinstance(service_expires_at, str) and service_expires_at else None
        renew_grace_dt = parse_datetime(renew_grace_expires_at) if isinstance(renew_grace_expires_at, str) and renew_grace_expires_at else None
        delete_dt = parse_datetime(delete_at) if isinstance(delete_at, str) and delete_at else None
        if service_expires_dt is not None and timezone.is_naive(service_expires_dt):
            service_expires_dt = timezone.make_aware(service_expires_dt, timezone.get_current_timezone())
        if renew_grace_dt is not None and timezone.is_naive(renew_grace_dt):
            renew_grace_dt = timezone.make_aware(renew_grace_dt, timezone.get_current_timezone())
        if delete_dt is not None and timezone.is_naive(delete_dt):
            delete_dt = timezone.make_aware(delete_dt, timezone.get_current_timezone())

        if status in {'pending', 'cancelled', 'failed'}:
            item['renew_status'] = 'unpaid'
            item['renew_status_label'] = '未付款'
        elif status in {'paid', 'provisioning'}:
            item['renew_status'] = 'paid'
            item['renew_status_label'] = '已付款'
        elif status in {'completed', 'renew_pending', 'expiring', 'suspended', 'deleting', 'deleted', 'expired'}:
            item['renew_status'] = 'completed'
            item['renew_status_label'] = '已完成'
        elif service_expires_dt and service_expires_dt <= now:
            item['renew_status'] = 'completed'
            item['renew_status_label'] = '已完成'
        else:
            item['renew_status'] = 'unpaid'
            item['renew_status_label'] = '未付款'

        item['can_renew'] = item['renew_status'] != 'unpaid' and status not in {'cancelled', 'failed'}
        item['auto_renew_enabled'] = auto_renew_enabled
        item['expired_by_time'] = bool(service_expires_dt and service_expires_dt <= now)
        item['grace_expired'] = bool(renew_grace_dt and renew_grace_dt <= now)
        item['delete_scheduled'] = bool(delete_dt and delete_dt > now)
        item['is_expired'] = status in {'deleted', 'expired'} or item['grace_expired']
        item['expires_in_days'] = _days_left(service_expires_dt) if service_expires_dt else None
        item['grace_expires_in_days'] = _days_left(renew_grace_dt) if renew_grace_dt else None
    return _ok(items)


def _server_payload(server):
    user = server.user
    user_payload = None
    if user:
        usernames = user.usernames
        user_payload = _user_payload({
            'id': user.id,
            'tg_user_id': user.tg_user_id,
            'username': user.username,
            'first_name': user.first_name,
            'usernames': usernames,
            'primary_username': usernames[0] if usernames else '',
        })
    order = server.order
    return {
        'id': server.id,
        'status': server.status,
        'status_label': _status_label(server.status, Server.STATUS_CHOICES),
        'source': server.source,
        'source_label': _server_source_label(server.source),
        'provider': server.provider,
        'provider_label': _provider_label(server.provider),
        'account_label': server.account_label,
        'region_label': _region_label(server.region_code, server.region_name),
        'region_name': server.region_name,
        'server_name': server.server_name,
        'instance_id': server.instance_id,
        'provider_resource_id': server.provider_resource_id,
        'public_ip': server.public_ip,
        'login_user': server.login_user,
        'expires_at': _iso(server.expires_at),
        'days_left': _days_left(server.expires_at),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'order_id': order.id if order else None,
        'order_no': order.order_no if order else '',
        'order_detail_path': f'/admin/cloud-orders/{order.id}' if order else '',
        'provider_status': '已删除' if server.status == Server.STATUS_DELETED else server.provider_status,
        'is_active': server.is_active,
        'updated_at': _iso(server.updated_at),
    }


@dashboard_login_required
@require_GET
def servers_list(request):
    keyword = _get_keyword(request)
    dedup_raw = (request.GET.get('dedup') or '').lower()
    dedup = dedup_raw not in {'0', 'false', 'no', 'off'}
    queryset = Server.objects.select_related('user', 'order').order_by('expires_at', '-updated_at', '-id')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['server_name', 'instance_id', 'public_ip', 'account_label', 'provider', 'region_name', 'user__tg_user_id', 'user__username', 'order__order_no'],
    )
    provider = (request.GET.get('provider') or '').strip()
    region_code = (request.GET.get('region_code') or '').strip()
    if provider:
        queryset = queryset.filter(provider=provider)
    if region_code:
        queryset = queryset.filter(region_code=region_code)
    items = [_server_payload(server) for server in queryset[:500]]
    if dedup:
        seen = set()
        deduped = []
        for item in items:
            dedup_key = (item.get('provider') or '', item.get('instance_id') or '', item.get('public_ip') or '', item.get('server_name') or '')
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            deduped.append(item)
        items = deduped
    return _ok(items)


def _append_provision_note(order, note):
    if not note:
        return order.provision_note
    return '\n'.join(filter(None, [order.provision_note, note]))


@transaction.atomic
def _apply_cloud_order_status(order, new_status):
    now = timezone.now()
    old_status = order.status
    allowed_statuses = {choice[0] for choice in CloudServerOrder.STATUS_CHOICES}
    if new_status not in allowed_statuses:
        raise ValueError('订单状态不正确')
    if new_status == old_status:
        return order

    note = None
    trigger_provision = False
    active_statuses = {'completed', 'renew_pending', 'expiring'}
    inactive_statuses = {'failed', 'cancelled', 'expired', 'deleted', 'suspended', 'deleting', 'pending'}

    if new_status in {'paid', 'provisioning', 'completed'} and not order.paid_at:
        order.paid_at = now

    if new_status == 'completed':
        if not order.completed_at:
            order.completed_at = now
        if not order.last_renewed_at:
            order.last_renewed_at = now
        note = '后台手动改状态为已完成。'
    elif new_status == 'paid':
        order.completed_at = None
        note = '后台手动改状态为已支付。'
        if not (order.instance_id or order.provider_resource_id or order.public_ip):
            trigger_provision = True
    elif new_status == 'provisioning':
        order.completed_at = None
        note = '后台手动改状态为创建中。'
        if not (order.instance_id or order.provider_resource_id or order.public_ip):
            trigger_provision = True
    elif new_status == 'renew_pending':
        order.completed_at = None
        if order.service_expires_at and order.service_expires_at > now:
            order.last_renewed_at = order.last_renewed_at or now
        note = '后台手动改状态为待续费。'
    elif new_status == 'expiring':
        order.completed_at = None
        note = '后台手动改状态为即将到期。'
    elif new_status in inactive_statuses:
        if new_status == 'pending':
            order.paid_at = None
        order.completed_at = None
        note = f"后台手动改状态为{dict(CloudServerOrder.STATUS_CHOICES).get(new_status, new_status)}。"

    order.status = new_status
    order.provision_note = _append_provision_note(order, note)
    order.save()

    if new_status in active_statuses:
        CloudAsset.objects.filter(order=order).update(
            actual_expires_at=order.service_expires_at,
            is_active=True,
            note=order.provision_note,
            updated_at=now,
        )
        Server.objects.filter(order=order).update(
            expires_at=order.service_expires_at,
            is_active=True,
            status=Server.STATUS_RUNNING if new_status == 'completed' else Server.STATUS_PENDING,
            note=order.provision_note,
            updated_at=now,
        )
    elif new_status in inactive_statuses:
        CloudAsset.objects.filter(order=order).update(
            is_active=False,
            note=order.provision_note,
            updated_at=now,
        )
        Server.objects.filter(order=order).update(
            is_active=False,
            status=Server.STATUS_DELETED if new_status == 'deleted' else Server.STATUS_STOPPED,
            note=order.provision_note,
            updated_at=now,
        )

    if trigger_provision:
        async_to_sync(provision_cloud_server)(order.id)
        order.refresh_from_db()

    return order


@dashboard_login_required
@require_GET
def cloud_order_detail(request, order_id):
    order = CloudServerOrder.objects.select_related('user', 'plan').filter(pk=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    return _ok(_cloud_order_detail_payload(order))


@csrf_exempt
@dashboard_login_required
@require_POST
def update_cloud_order_status(request, order_id):
    order = CloudServerOrder.objects.select_related('user', 'plan').filter(pk=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    payload = _read_payload(request)
    new_status = str(payload.get('status') or '').strip()
    if not new_status:
        return _error('订单状态不能为空')
    try:
        order = _apply_cloud_order_status(order, new_status)
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f'更新订单状态失败: {exc}', status=500)
    return _ok(_cloud_order_detail_payload(order))


@dashboard_login_required
@require_POST
def delete_server(request, server_id: int):
    server = Server.objects.select_related('order').filter(id=server_id).first()
    if not server:
        return _error('服务器不存在', status=404)
    now = timezone.now()
    note = f'后台手动删除服务器记录；时间: {now.isoformat()}'
    previous_public_ip = server.public_ip or server.previous_public_ip
    order = server.order
    current_instance_id = server.instance_id
    current_provider_resource_id = server.provider_resource_id
    server.status = Server.STATUS_DELETED
    server.provider_status = '已删除'
    server.is_active = False
    server.previous_public_ip = previous_public_ip
    server.public_ip = None
    server.instance_id = None
    server.provider_resource_id = None
    server.note = '\n'.join(filter(None, [server.note, note]))
    server.save(update_fields=['status', 'provider_status', 'is_active', 'previous_public_ip', 'public_ip', 'instance_id', 'provider_resource_id', 'note', 'updated_at'])
    asset_filter = Q()
    if order:
        asset_filter |= Q(order=order)
    if current_instance_id:
        asset_filter |= Q(instance_id=current_instance_id)
    if current_provider_resource_id:
        asset_filter |= Q(provider_resource_id=current_provider_resource_id)
    if asset_filter:
        CloudAsset.objects.filter(asset_filter).update(
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
            previous_public_ip=previous_public_ip,
            public_ip=None,
            instance_id=None,
            provider_resource_id=None,
            note=note,
            updated_at=now,
        )
    if order:
        order.status = 'deleted'
        order.previous_public_ip = previous_public_ip
        order.public_ip = ''
        order.instance_id = ''
        order.provider_resource_id = ''
        order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
        order.save(update_fields=['status', 'previous_public_ip', 'public_ip', 'instance_id', 'provider_resource_id', 'provision_note', 'updated_at'])
    return _ok(True)


@dashboard_login_required
@require_GET
def servers_statistics(request):
    keyword = _get_keyword(request)
    queryset = Server.objects.all()
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['region_code', 'region_name', 'provider', 'account_label', 'server_name', 'instance_id', 'public_ip'],
    )
    rows = list(
        queryset
        .values('provider', 'region_code', 'region_name', 'account_label')
        .annotate(total_count=Count('id'))
        .order_by('account_label', 'provider', 'region_name')
    )

    region_pairs = []
    region_seen = set()
    for row in rows:
        region_code = row['region_code'] or ''
        region_label = _region_label(region_code, row['region_name'])
        key = (region_code, region_label)
        if key not in region_seen:
            region_seen.add(key)
            region_pairs.append({'region_code': region_code, 'region_label': region_label})
    region_pairs.sort(key=lambda item: (item['region_label'], item['region_code']))

    account_map = {}
    for row in rows:
        account_id = row['account_label'] or '-'
        entry = account_map.setdefault(
            account_id,
            {
                'account_id': account_id,
                'account_label': account_id,
                'provider_label': _provider_label(row['provider']),
                'regions': {},
                'total_count': 0,
            },
        )
        region_code = row['region_code'] or ''
        region_label = _region_label(region_code, row['region_name'])
        region_key = region_code or region_label
        count = row['total_count']
        entry['regions'][region_key] = entry['regions'].get(region_key, 0) + count
        entry['total_count'] += count

    items = []
    totals = {'account_id': '合计', 'account_label': '合计', 'provider_label': '-', 'regions': {}, 'total_count': 0}
    for account_id in sorted(account_map.keys()):
        entry = account_map[account_id]
        row_payload = {
            'account_id': entry['account_id'],
            'account_label': entry['account_label'],
            'provider_label': entry['provider_label'],
            'total_count': entry['total_count'],
        }
        for region in region_pairs:
            region_key = region['region_code'] or region['region_label']
            value = entry['regions'].get(region_key, 0)
            row_payload[region_key] = value
            totals['regions'][region_key] = totals['regions'].get(region_key, 0) + value
        totals['total_count'] += entry['total_count']
        items.append(row_payload)

    total_row = {
        'account_id': totals['account_id'],
        'account_label': totals['account_label'],
        'provider_label': totals['provider_label'],
        'total_count': totals['total_count'],
    }
    for region in region_pairs:
        region_key = region['region_code'] or region['region_label']
        total_row[region_key] = totals['regions'].get(region_key, 0)

    return _ok({
        'regions': region_pairs,
        'items': items,
        'summary': total_row,
    })


@csrf_exempt
@dashboard_login_required
@require_http_methods(['POST'])
def create_cloud_plan(request):
    data = _read_payload(request)
    provider = (data.get('provider') or '').strip()
    region_code = (data.get('region_code') or '').strip()
    region_name = (data.get('region_name') or '').strip()
    plan_name = (data.get('plan_name') or '').strip()
    if not provider or not region_code or not region_name or not plan_name:
        return _error('云厂商、地区代码、地区名称、套餐名不能为空')
    try:
        plan = CloudServerPlan.objects.create(
            provider=provider,
            region_code=region_code,
            region_name=region_name,
            plan_name=plan_name,
            plan_description=(data.get('plan_description') or '').strip(),
            cpu=(data.get('cpu') or '').strip(),
            memory=(data.get('memory') or '').strip(),
            storage=(data.get('storage') or '').strip(),
            bandwidth=(data.get('bandwidth') or '').strip(),
            cost_price=_parse_decimal(data.get('cost_price') or 0, '进货价').quantize(Decimal('0.01')),
            price=_parse_decimal(data.get('price') or 0, '出售价').quantize(Decimal('0.01')),
            currency=(data.get('currency') or 'USDT').strip() or 'USDT',
            sort_order=int(data.get('sort_order') or 0),
            is_active=str(data.get('is_active', True)).lower() in {'1', 'true', 'yes', 'on'},
        )
    except IntegrityError:
        return _error('同地区下已存在同名套餐', status=400)
    except (InvalidOperation, TypeError, ValueError):
        return _error('提交的套餐数据格式不正确', status=400)
    async_to_sync(refresh_custom_plan_cache)()
    return _ok(_cloud_plan_payload(plan))


@csrf_exempt
@dashboard_login_required
@require_http_methods(['POST'])
def delete_cloud_plan(request, plan_id: int):
    plan = CloudServerPlan.objects.filter(id=plan_id).first()
    if not plan:
        return _error('套餐不存在', status=404)
    if CloudServerOrder.objects.filter(plan_id=plan_id).exists():
        return _error('该套餐已有订单引用，无法删除，请改为停用', status=400)
    plan.delete()
    async_to_sync(refresh_custom_plan_cache)()
    return _ok({'id': plan_id, 'deleted': True})


@csrf_exempt
@dashboard_login_required
@require_http_methods(['POST'])
def update_cloud_plan(request, plan_id: int):
    plan = CloudServerPlan.objects.filter(id=plan_id).first()
    if not plan:
        return _error('套餐不存在', status=404)
    data = request.POST or request.GET
    plan_name = (data.get('plan_name') or '').strip()
    plan_description = (data.get('plan_description') or '').strip()
    price = data.get('price')
    cost_price = data.get('cost_price')
    sort_order = data.get('sort_order')
    is_active = data.get('is_active')
    try:
        if plan_name:
            plan.plan_name = plan_name
        if 'provider' in data:
            plan.provider = (data.get('provider') or '').strip() or plan.provider
        if 'region_code' in data:
            plan.region_code = (data.get('region_code') or '').strip() or plan.region_code
        if 'region_name' in data:
            plan.region_name = (data.get('region_name') or '').strip() or plan.region_name
        if 'cpu' in data:
            plan.cpu = (data.get('cpu') or '').strip()
        if 'memory' in data:
            plan.memory = (data.get('memory') or '').strip()
        if 'storage' in data:
            plan.storage = (data.get('storage') or '').strip()
        if 'bandwidth' in data:
            plan.bandwidth = (data.get('bandwidth') or '').strip()
        if 'currency' in data:
            plan.currency = (data.get('currency') or 'USDT').strip() or 'USDT'
        plan.plan_description = plan_description
        if price not in (None, ''):
            plan.price = Decimal(str(price))
        if cost_price not in (None, ''):
            plan.cost_price = Decimal(str(cost_price))
        if sort_order not in (None, ''):
            plan.sort_order = int(sort_order)
        if is_active not in (None, ''):
            plan.is_active = str(is_active).lower() in {'1', 'true', 'yes', 'on'}
        plan.save()
    except IntegrityError:
        return _error('同地区下已存在同名套餐', status=400)
    except (InvalidOperation, ValueError):
        return _error('提交的套餐数据格式不正确')
    async_to_sync(refresh_custom_plan_cache)()
    return _ok(_cloud_plan_payload(plan))


def _apply_server_missing_state(provider, region, existing_instance_ids):
    now = timezone.now()
    existing_instance_ids = {str(item) for item in existing_instance_ids if item}
    queryset = Server.objects.filter(provider=provider, region_code=region).exclude(instance_id__isnull=True).exclude(instance_id='')
    legacy_queryset = queryset.filter(provider_status='missing')
    legacy_updated = legacy_queryset.update(
        status=Server.STATUS_DELETED,
        provider_status='已删除',
        is_active=False,
        note=Case(
            When(note__isnull=True, then=Value(f'历史状态修正：服务器不存在，已统一标记为已删除；检查时间: {now.isoformat()}')),
            When(note='', then=Value(f'历史状态修正：服务器不存在，已统一标记为已删除；检查时间: {now.isoformat()}')),
            default=Cast('note', output_field=CharField()),
            output_field=CharField(),
        ),
        updated_at=now,
    )
    queryset = queryset.filter(is_active=True)
    if existing_instance_ids:
        queryset = queryset.exclude(instance_id__in=existing_instance_ids)
    missing_servers = list(queryset.select_related('order'))
    missing_note = f'云平台同步未发现该服务器，已标记为已删除；检查时间: {now.isoformat()}'
    updated = queryset.update(
        status=Server.STATUS_DELETED,
        provider_status='已删除',
        is_active=False,
        note=missing_note,
        updated_at=now,
    )
    order_ids = [item.order_id for item in missing_servers if item.order_id]
    instance_ids = [item.instance_id for item in missing_servers if item.instance_id]
    if order_ids:
        CloudServerOrder.objects.filter(id__in=order_ids).exclude(status='deleted').update(
            status='deleted',
            provision_note=missing_note,
            updated_at=now,
        )
        CloudAsset.objects.filter(order_id__in=order_ids).update(
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
            note=missing_note,
            updated_at=now,
        )
    if instance_ids:
        CloudAsset.objects.filter(instance_id__in=instance_ids).update(
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
            note=missing_note,
            updated_at=now,
        )
    return legacy_updated + updated


@csrf_exempt
@dashboard_login_required
@require_POST
def sync_servers(request):
    aliyun_region = (request.POST.get('region') or request.GET.get('region') or 'cn-hongkong').strip() or 'cn-hongkong'
    aws_region = (request.POST.get('aws_region') or request.GET.get('aws_region') or '').strip()
    errors = []
    synced = {'aliyun': False, 'aws': False}
    missing = {'aliyun': 0, 'aws': 0}
    aws_regions = []
    try:
        aliyun_command = call_command('sync_aliyun_assets', region=aliyun_region)
        synced['aliyun'] = True
        missing['aliyun'] = _apply_server_missing_state('aliyun_simple', aliyun_region, getattr(aliyun_command, 'synced_instance_ids', None) or [])
    except Exception as exc:
        errors.append(f'阿里云同步失败: {exc}')
    try:
        if aws_region:
            aws_command = call_command('sync_aws_assets', region=aws_region)
            aws_regions = [aws_region]
        else:
            aws_command = call_command('sync_aws_assets')
            aws_regions = getattr(aws_command, 'synced_regions', None) or []
        synced['aws'] = True
        synced_map = getattr(aws_command, 'synced_instance_ids_by_region', None) or {}
        if aws_regions:
            missing['aws'] = sum(
                _apply_server_missing_state('aws_lightsail', region, synced_map.get(region, []))
                for region in aws_regions
            )
    except Exception as exc:
        errors.append(f'AWS 同步失败: {exc}')
    if errors and not any(synced.values()):
        return _error('；'.join(errors), status=500)
    return _ok({'synced': synced, 'missing': missing, 'aliyun_region': aliyun_region, 'aws_region': aws_region or 'all', 'aws_regions': aws_regions, 'errors': errors})


@csrf_exempt
@dashboard_login_required
@require_POST
def sync_cloud_assets(request):
    aliyun_region = (request.POST.get('region') or request.GET.get('region') or 'cn-hongkong').strip() or 'cn-hongkong'
    aws_region = (request.POST.get('aws_region') or request.GET.get('aws_region') or '').strip()
    errors = []
    synced = {'aliyun': False, 'aws': False}
    aws_regions = []
    try:
        call_command('sync_aliyun_assets', region=aliyun_region)
        synced['aliyun'] = True
    except Exception as exc:
        errors.append(f'阿里云代理同步失败: {exc}')
    try:
        if aws_region:
            call_command('sync_aws_assets', region=aws_region)
            aws_regions = [aws_region]
        else:
            aws_command = call_command('sync_aws_assets')
            aws_regions = getattr(aws_command, 'synced_regions', None) or []
        synced['aws'] = True
    except Exception as exc:
        errors.append(f'AWS 代理同步失败: {exc}')
    if errors and not any(synced.values()):
        return _error('；'.join(errors), status=500)
    return _ok({'synced': synced, 'aliyun_region': aliyun_region, 'aws_region': aws_region or 'all', 'aws_regions': aws_regions, 'errors': errors})


@csrf_exempt
@dashboard_login_required
@require_POST
def sync_cloud_plans(request):
    before_pricing_count = ServerPrice.objects.filter(is_active=True).count()
    before_regions = list(
        ServerPrice.objects.filter(is_active=True)
        .values('provider', 'region_code', 'region_name')
        .distinct()
        .order_by('provider', 'region_code')
    )
    try:
        async_to_sync(ensure_cloud_server_pricing)()
    except Exception as exc:
        return _error(f'同步价格配置失败: {exc}', status=500)
    active_pricing_queryset = ServerPrice.objects.filter(is_active=True)
    after_pricing_count = active_pricing_queryset.count()
    after_regions = list(
        active_pricing_queryset
        .values('provider', 'region_code', 'region_name')
        .distinct()
        .order_by('provider', 'region_code')
    )
    provider_region_summary = list(
        active_pricing_queryset
        .values('provider', 'region_code', 'region_name')
        .annotate(pricing_count=Count('id'))
        .order_by('provider', 'region_code')
    )
    return _ok({
        'synced': True,
        'refreshed_regions': len(after_regions),
        'summary': {
            'before_plan_count': CloudServerPlan.objects.filter(is_active=True).count(),
            'after_plan_count': CloudServerPlan.objects.filter(is_active=True).count(),
            'before_pricing_count': before_pricing_count,
            'after_pricing_count': after_pricing_count,
            'region_count': len(after_regions),
        },
        'regions': after_regions,
        'before_regions': before_regions,
        'provider_region_summary': provider_region_summary,
    })


def _cloud_plan_payload(plan):
    return {
        'id': plan.id,
        'provider': plan.provider,
        'provider_label': _provider_label(plan.provider),
        'region_code': plan.region_code,
        'region_name': plan.region_name,
        'region_label': _region_label(plan.region_code, plan.region_name),
        'plan_name': plan.plan_name,
        'plan_description': plan.plan_description,
        'cpu': plan.cpu,
        'memory': plan.memory,
        'storage': plan.storage,
        'bandwidth': plan.bandwidth,
        'cost_price': _decimal_to_str(getattr(plan, 'cost_price', 0)),
        'price': _decimal_to_str(plan.price),
        'currency': plan.currency,
        'sort_order': plan.sort_order,
        'is_active': plan.is_active,
        'updated_at': _iso(plan.updated_at),
    }


def _server_price_payload(price):
    return {
        'id': price.id,
        'provider': price.provider,
        'region_code': price.region_code,
        'region_name': price.region_name,
        'bundle_code': price.bundle_code,
        'plan_name': price.server_name,
        'server_name': price.server_name,
        'plan_description': price.server_description or '',
        'server_description': price.server_description or '',
        'cpu': price.cpu,
        'memory': price.memory,
        'storage': price.storage,
        'bandwidth': price.bandwidth,
        'cost_price': _decimal_to_str(getattr(price, 'cost_price', 0)),
        'price': _decimal_to_str(price.price),
        'currency': price.currency,
        'sort_order': price.sort_order,
        'is_active': price.is_active,
        'updated_at': _iso(price.updated_at),
    }


@dashboard_login_required
@require_GET
def cloud_pricing_list(request):
    keyword = _get_keyword(request)
    queryset = ServerPrice.objects.filter(is_active=True).order_by('provider', 'region_code', '-sort_order', 'id')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['provider', 'region_code', 'region_name', 'bundle_code', 'server_name', 'server_description', 'cpu', 'memory', 'storage', 'bandwidth'],
    )
    provider = (request.GET.get('provider') or '').strip()
    region_code = (request.GET.get('region_code') or '').strip()
    if provider:
        queryset = queryset.filter(provider=provider)
    if region_code:
        queryset = queryset.filter(region_code=region_code)
    return _ok([_server_price_payload(item) for item in queryset])


@dashboard_login_required
@require_GET
def cloud_plans_list(request):
    keyword = _get_keyword(request)
    queryset = CloudServerPlan.objects.filter(is_active=True).order_by('provider', 'region_code', '-sort_order', 'id')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['provider', 'region_code', 'region_name', 'plan_name', 'plan_description', 'cpu', 'memory', 'storage', 'bandwidth'],
    )
    provider = (request.GET.get('provider') or '').strip()
    region_code = (request.GET.get('region_code') or '').strip()
    if provider:
        queryset = queryset.filter(provider=provider)
    if region_code:
        queryset = queryset.filter(region_code=region_code)
    return _ok([_cloud_plan_payload(item) for item in queryset])


@dashboard_login_required
@require_GET
def monitors_list(request):
    keyword = _get_keyword(request)
    queryset = AddressMonitor.objects.select_related('user').order_by('-created_at')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['address', 'remark', 'daily_income_currency', 'daily_expense_currency', 'user__tg_user_id', 'user__username'],
    )
    items = list(
        queryset[:100].values(
            'id', 'address', 'remark', 'monitor_transfers', 'monitor_resources',
            'daily_income', 'daily_expense', 'daily_income_currency', 'daily_expense_currency',
            'stats_date', 'is_active', 'created_at', 'resource_checked_at', 'user__tg_user_id', 'user__username'
        )
    )
    return _ok([
        {
            **item,
            'daily_income': _decimal_to_str(item['daily_income']),
            'daily_expense': _decimal_to_str(item['daily_expense']),
            'created_at': _iso(item['created_at']),
            'resource_checked_at': _iso(item['resource_checked_at']),
            'tg_user_id': item.pop('user__tg_user_id', None),
            'username': item.pop('user__username', None),
        }
        for item in items
    ])


__all__ = [
    'cloud_assets_list',
    'cloud_order_detail',
    'cloud_orders_list',
    'tasks_overview',
    'cloud_plans_list',
    'cloud_pricing_list',
    'create_cloud_plan',
    'delete_cloud_plan',
    'delete_server',
    'monitors_list',
    'servers_list',
    'servers_statistics',
    'sync_cloud_assets',
    'sync_cloud_plans',
    'sync_servers',
    'update_cloud_asset',
    'update_cloud_order_status',
    'update_cloud_plan',
]
