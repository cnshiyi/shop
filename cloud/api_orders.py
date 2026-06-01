"""云订单后台 API。"""

import logging

from asgiref.sync import async_to_sync
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from cloud.api_assets import _parse_iso_datetime, _resolve_telegram_user, _sync_telegram_username
from cloud.lifecycle_schedule import compute_order_lifecycle_fields
from cloud.lifecycle_state import primary_record_updates_for_order_status
from cloud.models import CloudAsset, CloudIpLog, CloudServerOrder
from cloud.note_utils import prepend_note
from cloud.provisioning import provision_cloud_server
from cloud.services import _update_order_primary_records
from core.dashboard_api import _apply_keyword_filter, _days_left, _decimal_to_str, _error, _get_keyword, _iso, _ok, _parse_decimal, _provider_label, _read_payload, _region_label, _status_label, _user_payload, dashboard_login_required, dashboard_superuser_required
from core.runtime_config import get_runtime_config

logger = logging.getLogger(__name__)


def _cloud_execution_status(note: str | None):
    text = str(note or '').strip()
    if not text:
        return '', ''
    if '阿里云真实续费失败' in text:
        return 'aliyun_renew_failed', '阿里云续费失败，待重试'
    if '关机失败' in text:
        return 'suspend_failed', '关机失败，待重试'
    if '删除失败' in text:
        return 'delete_failed', '删机失败，待重试'
    if '旧实例删除失败' in text or '旧服务器删除失败' in text:
        return 'migration_delete_failed', '迁移旧机删除失败，待重试'
    return '', ''


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _mask_secret(value, keep=4):
    text = str(value or '')
    if not text:
        return ''
    if len(text) <= keep * 2:
        return '*' * len(text)
    return f'{text[:keep]}***{text[-keep:]}'


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_order_source_tags(order):
    note = str(getattr(order, 'provision_note', '') or '')
    order_no = str(getattr(order, 'order_no', '') or '')
    tags: list[tuple[str, str]] = []
    seen = set()

    # 功能：处理 后台 API 接口 中的 add 业务流程。
    def add(tag_key: str, tag_label: str):
        if tag_key in seen:
            return
        seen.add(tag_key)
        tags.append((tag_key, tag_label))

    if '人工编辑' in note or order_no.startswith('SRVMANUAL') or order_no.startswith('SRVADMIN'):
        if '所属人' in note or '用户' in note:
            add('manual_owner_change', '人工改用户')
        if '到期时间' in note:
            add('manual_expiry_change', '人工改时间')
        if '价格' in note:
            add('manual_price_change', '人工改价格')
        if ('所属人' in note or '用户' in note) and '时间' in note and not tags:
            add('manual_owner_expiry_change', '人工改用户+时间')
    if not tags:
        if getattr(order, 'replacement_for_id', None):
            add('renewal_rebuild', '续费恢复')
        elif getattr(order, 'last_renewed_at', None) or getattr(order, 'status', '') == 'renew_pending' or '续费' in note:
            add('renewal', '续费')
        else:
            add('new', '新购')
    return tags


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_order_source_label(order):
    tags = _cloud_order_source_tags(order)
    first_tag = tags[0] if tags else ('new', '新购')
    return first_tag[0], first_tag[1]


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_order_summary_payload(order):
    if not order:
        return None
    order_source, order_source_label = _cloud_order_source_label(order)
    order_source_tags = _cloud_order_source_tags(order)
    return {
        'id': order.id,
        'order_id': order.id,
        'order_no': order.order_no,
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'order_source': order_source,
        'order_source_label': order_source_label,
        'order_source_tags': [item[0] for item in order_source_tags],
        'order_source_tag_labels': [item[1] for item in order_source_tags],
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'public_ip': order.public_ip,
        'previous_public_ip': order.previous_public_ip,
        'service_started_at': _iso(order.service_started_at),
        'service_expires_at': _iso(order.service_expires_at),
        'created_at': _iso(order.created_at),
        'updated_at': _iso(order.updated_at),
        'replacement_for_id': order.replacement_for_id,
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
    }


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _order_lineage_ids(order):
    if not order:
        return set()
    seen = set()
    queue = [order.id]
    while queue:
        current_id = queue.pop(0)
        if not current_id or current_id in seen:
            continue
        seen.add(current_id)
        parent_id = CloudServerOrder.objects.filter(id=current_id).values_list('replacement_for_id', flat=True).first()
        if parent_id and parent_id not in seen:
            queue.append(parent_id)
        child_ids = list(CloudServerOrder.objects.filter(replacement_for_id=current_id).values_list('id', flat=True))
        for child_id in child_ids:
            if child_id not in seen:
                queue.append(child_id)
    return seen


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_detail_log_queryset(asset, order):
    order_ids = _order_lineage_ids(order)
    asset_names = {str(asset.asset_name or '').strip(), str(asset.instance_id or '').strip()}
    ip_values = {str(asset.public_ip or '').strip(), str(asset.previous_public_ip or '').strip()}
    order_nos = set()
    if order_ids:
        for item in CloudServerOrder.objects.filter(id__in=order_ids).only('order_no', 'server_name', 'instance_id', 'public_ip', 'previous_public_ip'):
            order_nos.add(str(item.order_no or '').strip())
            asset_names.add(str(item.server_name or '').strip())
            asset_names.add(str(item.instance_id or '').strip())
            ip_values.add(str(item.public_ip or '').strip())
            ip_values.add(str(item.previous_public_ip or '').strip())
    asset_names.discard('')
    ip_values.discard('')
    order_nos.discard('')
    related_asset_ids = set([asset.id])
    asset_lookup = Q()
    if order_ids:
        asset_lookup |= Q(order_id__in=order_ids)
    if asset_names:
        asset_lookup |= Q(asset_name__in=asset_names) | Q(instance_id__in=asset_names)
    if ip_values:
        asset_lookup |= Q(public_ip__in=ip_values) | Q(previous_public_ip__in=ip_values)
    if asset_lookup:
        related_asset_ids.update(CloudAsset.objects.filter(asset_lookup).values_list('id', flat=True)[:200])
    log_lookup = Q(asset_id__in=related_asset_ids)
    if order_ids:
        log_lookup |= Q(order_id__in=order_ids)
    if asset_names:
        log_lookup |= Q(asset_name__in=asset_names) | Q(instance_id__in=asset_names)
    if ip_values:
        log_lookup |= Q(public_ip__in=ip_values) | Q(previous_public_ip__in=ip_values)
    for order_no in order_nos:
        log_lookup |= Q(order_no=order_no) | Q(note__icontains=order_no)
    return CloudIpLog.objects.filter(log_lookup).distinct().order_by('-created_at', '-id')


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _related_order_history_payload(order):
    if not order:
        return []
    root = order
    seen = set()
    while root.replacement_for_id and root.replacement_for_id not in seen:
        seen.add(root.id)
        parent = CloudServerOrder.objects.select_related('user', 'plan').filter(id=root.replacement_for_id).first()
        if not parent:
            break
        root = parent
    chain = list(
        CloudServerOrder.objects.select_related('user', 'plan')
        .filter(Q(id=root.id) | Q(replacement_for_id=root.id) | Q(replacement_for__replacement_for_id=root.id) | Q(replacement_for__replacement_for__replacement_for_id=root.id))
        .order_by('-created_at', '-id')[:20]
    )
    if order.id not in {item.id for item in chain}:
        chain.insert(0, order)
    deduped = []
    seen_ids = set()
    for item in chain:
        if item.id in seen_ids:
            continue
        seen_ids.add(item.id)
        deduped.append(item)
    deduped.sort(key=lambda item: (0 if item.id == order.id else 1, -(item.created_at.timestamp() if item.created_at else 0), -item.id))
    return [_cloud_order_summary_payload(item) for item in deduped]


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
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
    order_source, order_source_label = _cloud_order_source_label(order)
    payload = {
        'id': order.id,
        'order_no': order.order_no,
        'provider': order.provider,
        'cloud_account_id': order.cloud_account_id,
        'account_label': order.account_label,
        'region_code': order.region_code,
        'region_label': _region_label(order.region_code, order.region_name),
        'region_name': order.region_name,
        'plan_name': order.plan_name,
        'quantity': order.quantity,
        'currency': order.currency,
        'total_amount': _decimal_to_str(order.total_amount),
        'pay_amount': _decimal_to_str(order.pay_amount) if order.pay_amount is not None else None,
        'pay_method': order.pay_method,
        'order_source': order_source,
        'order_source_label': order_source_label,
        'order_source_tags': [item[0] for item in _cloud_order_source_tags(order)],
        'order_source_tag_labels': [item[1] for item in _cloud_order_source_tags(order)],
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'tx_hash': order.tx_hash,
        'payer_address': order.payer_address,
        'receive_address': order.receive_address,
        'tronscan_url': f'https://tronscan.org/#/transaction/{order.tx_hash}' if order.tx_hash else '',
        'image_name': order.image_name,
        'server_name': order.server_name,
        'lifecycle_days': order.lifecycle_days,
        'service_started_at': _iso(order.service_started_at),
        'service_expires_at': _iso(order.service_expires_at),
        'renew_grace_expires_at': _iso(order.renew_grace_expires_at),
        'suspend_at': _iso(order.suspend_at),
        'delete_at': _iso(order.delete_at),
        'ip_recycle_at': _iso(order.ip_recycle_at),
        'suspend_time_config': str(get_runtime_config('cloud_suspend_time', '15:00') or '15:00').strip() or '15:00',
        'delete_time_config': str(get_runtime_config('cloud_delete_time', '15:00') or '15:00').strip() or '15:00',
        'last_renewed_at': _iso(order.last_renewed_at),
        'auto_renew_enabled': order.auto_renew_enabled,
        'last_user_id': order.last_user_id,
        'mtproxy_port': order.mtproxy_port,
        'mtproxy_link': order.mtproxy_link,
        'proxy_links': order.proxy_links or [],
        'mtproxy_secret': _mask_secret(order.mtproxy_secret),
        'has_mtproxy_secret': bool(order.mtproxy_secret),
        'mtproxy_host': order.mtproxy_host,
        'instance_id': order.instance_id,
        'provider_resource_id': order.provider_resource_id,
        'static_ip_name': order.static_ip_name,
        'public_ip': order.public_ip,
        'previous_public_ip': order.previous_public_ip,
        'login_user': order.login_user,
        'login_password': _mask_secret(order.login_password),
        'has_login_password': bool(order.login_password),
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
        'execution_status': _cloud_execution_status(order.provision_note)[0],
        'execution_status_label': _cloud_execution_status(order.provision_note)[1],
    }
    payload.update({
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
        'replacement_for_detail_path': f'/admin/cloud-orders/{order.replacement_for_id}' if order.replacement_for_id else '',
        'history_orders': _related_order_history_payload(order),
    })
    return payload


# 功能：处理 后台 API 接口 中的 cloud orders list 业务流程。
@dashboard_login_required
@require_GET
def cloud_orders_list(request):
    keyword = _get_keyword(request)
    queryset = (
        CloudServerOrder.objects.select_related('user', 'plan')
        .exclude(Q(order_no__startswith='SRVMANUAL'))
        .annotate(
            deleted_rank=Case(
                When(status='deleted', then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .order_by('deleted_rank', '-created_at', '-id')
    )
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

        if status == 'renew_pending':
            item['renew_status'] = 'renew_pending'
            item['renew_status_label'] = '续费待支付'
        elif status == 'expiring':
            item['renew_status'] = 'expiring'
            item['renew_status_label'] = '已到期待处理'
        elif status == 'suspended':
            item['renew_status'] = 'suspended'
            item['renew_status_label'] = '已关机待续费'
        elif status == 'deleting':
            item['renew_status'] = 'deleting'
            item['renew_status_label'] = '删除中'
        elif status == 'deleted':
            item['renew_status'] = 'deleted'
            item['renew_status_label'] = '实例已删除'
        elif status == 'expired':
            item['renew_status'] = 'expired'
            item['renew_status_label'] = '已过期'
        elif status in {'pending', 'cancelled', 'failed'}:
            item['renew_status'] = 'unpaid'
            item['renew_status_label'] = '未付款'
        elif status in {'paid', 'provisioning'}:
            item['renew_status'] = 'paid'
            item['renew_status_label'] = '已付款'
        elif status == 'completed' and service_expires_dt and service_expires_dt <= now:
            item['renew_status'] = 'expiring'
            item['renew_status_label'] = '已到期待处理'
        elif status == 'completed':
            item['renew_status'] = 'completed'
            item['renew_status_label'] = '已完成'
        else:
            item['renew_status'] = 'unknown'
            item['renew_status_label'] = '状态未知'

        item['can_renew'] = status not in {'pending', 'cancelled', 'failed', 'paid', 'provisioning'}
        item['auto_renew_enabled'] = auto_renew_enabled
        item['expired_by_time'] = bool(service_expires_dt and service_expires_dt <= now)
        item['grace_expired'] = bool(renew_grace_dt and renew_grace_dt <= now)
        item['delete_scheduled'] = bool(delete_dt and delete_dt > now)
        item['is_expired'] = status in {'deleted', 'expired'} or item['grace_expired']
        item['expires_in_days'] = _days_left(service_expires_dt) if service_expires_dt else None
        item['grace_expires_in_days'] = _days_left(renew_grace_dt) if renew_grace_dt else None
    return _ok(items)


# 功能：删除或标记删除相关业务对象；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_superuser_required
@require_POST
def delete_cloud_order(request, order_id):
    order = CloudServerOrder.objects.filter(id=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    linked_asset_count = CloudAsset.objects.filter(order=order).count()
    cloud_identity_values = [
        order.public_ip,
        order.previous_public_ip,
        order.instance_id,
        order.provider_resource_id,
        order.server_name,
        order.static_ip_name,
    ]
    if linked_asset_count or any(str(value or '').strip() for value in cloud_identity_values):
        logger.warning(
            'DASHBOARD_CLOUD_ORDER_DELETE_BLOCKED order_id=%s order_no=%s assets=%s user=%s',
            order_id,
            order.order_no,
            linked_asset_count,
            getattr(request.user, 'id', None),
        )
        return _error(
            '订单已关联云资源，已阻止物理删除；请先在订单详情里改状态，或处理关联资产后再删除。',
            status=409,
        )
    order_no = order.order_no
    order.delete()
    logger.info('DASHBOARD_CLOUD_ORDER_DELETE order_id=%s order_no=%s user=%s', order_id, order_no, getattr(request.user, 'id', None))
    return _ok(True)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _append_provision_note(order, note):
    if not note:
        return order.provision_note
    return prepend_note(order.provision_note, note)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _primary_record_updates_for_order_status(order_status: str, note: str | None = None):
    return primary_record_updates_for_order_status(order_status)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
@transaction.atomic
def _apply_cloud_order_status(order, new_status):
    now = timezone.now()
    allowed_statuses = {choice[0] for choice in CloudServerOrder.STATUS_CHOICES}
    if new_status not in allowed_statuses:
        raise ValueError('订单状态不正确')
    order = CloudServerOrder.objects.select_related('user', 'plan').select_for_update().get(pk=order.pk)
    old_status = order.status
    if new_status == old_status:
        return order

    note = None
    trigger_provision = False
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

    asset_updates, server_updates = _primary_record_updates_for_order_status(new_status, order.provision_note)
    if asset_updates or server_updates:
        _update_order_primary_records(
            order,
            asset_updates=asset_updates,
            server_updates=server_updates,
            now=now,
        )

    if trigger_provision:
        async_to_sync(provision_cloud_server)(order.id)
        order.refresh_from_db()

    logger.info(
        'DASHBOARD_CLOUD_ORDER_STATUS_APPLIED order_id=%s order_no=%s old_status=%s new_status=%s trigger_provision=%s user_id=%s',
        order.id,
        order.order_no,
        old_status,
        new_status,
        trigger_provision,
        getattr(getattr(order, 'user', None), 'id', None),
    )
    return order


# 功能：处理 后台 API 接口 中的 cloud order detail 业务流程。
@csrf_exempt
@dashboard_superuser_required
@require_http_methods(['GET', 'POST', 'PUT', 'PATCH'])
def cloud_order_detail(request, order_id):
    order = CloudServerOrder.objects.select_related('user', 'plan').filter(pk=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    if request.method == 'GET':
        return _ok(_cloud_order_detail_payload(order))

    payload = _read_payload(request)
    try:
        with transaction.atomic():
            order = CloudServerOrder.objects.select_for_update().select_related('user', 'plan').get(pk=order_id)
            changed_fields = set()
            user_lookup = payload.get('user_query') or payload.get('user_id') or payload.get('tg_user_id') or payload.get('username')
            clear_user = str(payload.get('clear_user') or '').lower() in {'1', 'true', 'yes', 'on'}
            if clear_user:
                return _error('订单必须绑定用户，不能清空所属用户', status=400)
            elif user_lookup not in (None, ''):
                user = _resolve_telegram_user(user_lookup)
                if not user:
                    return _error('未找到匹配的 Telegram 用户', status=404)
                order.user = user
                order.last_user_id = user.tg_user_id
                changed_fields.update({'user', 'last_user_id'})
                _sync_telegram_username(user, user_lookup)

            original_public_ip = order.public_ip
            for field in ('server_name', 'public_ip', 'previous_public_ip', 'instance_id', 'provider_resource_id', 'static_ip_name', 'mtproxy_host', 'mtproxy_link', 'provision_note'):
                if field in payload:
                    setattr(order, field, payload.get(field) or None)
                    changed_fields.add(field)
            if 'public_ip' in payload and original_public_ip and original_public_ip != order.public_ip and 'previous_public_ip' not in payload:
                order.previous_public_ip = original_public_ip
                changed_fields.add('previous_public_ip')
            if 'mtproxy_port' in payload:
                mtproxy_port = payload.get('mtproxy_port')
                order.mtproxy_port = int(mtproxy_port) if mtproxy_port not in (None, '') else None
                changed_fields.add('mtproxy_port')
            if 'total_amount' in payload:
                order.total_amount = _parse_decimal(payload.get('total_amount'), '总金额')
                changed_fields.add('total_amount')
            if 'pay_amount' in payload:
                pay_amount = payload.get('pay_amount')
                order.pay_amount = _parse_decimal(pay_amount, '应付金额') if pay_amount not in (None, '') else None
                changed_fields.add('pay_amount')
            if 'status' in payload:
                status = str(payload.get('status') or '').strip()
                if status and status not in {choice[0] for choice in CloudServerOrder.STATUS_CHOICES}:
                    return _error('订单状态不正确', status=400)
                if status:
                    order.status = status
                    changed_fields.add('status')
            for field, label in (
                ('service_started_at', '服务开始时间'),
                ('service_expires_at', '服务到期时间'),
                ('renew_grace_expires_at', '续费宽限到期'),
                ('suspend_at', '计划关机时间'),
                ('delete_at', '计划删机时间'),
                ('ip_recycle_at', 'IP保留到期'),
            ):
                if field in payload:
                    setattr(order, field, _parse_iso_datetime(payload.get(field), label) if payload.get(field) else None)
                    changed_fields.add(field)
            if 'service_expires_at' in changed_fields and 'service_expires_at' in payload:
                lifecycle_updates = compute_order_lifecycle_fields(order.service_expires_at) if order.service_expires_at else {
                    'renew_grace_expires_at': None,
                    'suspend_at': None,
                    'delete_at': None,
                    'ip_recycle_at': None,
                }
                for field, value in lifecycle_updates.items():
                    if field not in payload:
                        setattr(order, field, value)
                        changed_fields.add(field)
            if changed_fields:
                update_values = {field: getattr(order, field) for field in changed_fields}
                update_values['updated_at'] = timezone.now()
                CloudServerOrder.objects.filter(pk=order.pk).update(**update_values)
                order.refresh_from_db()
                logger.info(
                    'DASHBOARD_CLOUD_ORDER_DETAIL_UPDATED order_id=%s order_no=%s changed_fields=%s user_id=%s',
                    order.id,
                    order.order_no,
                    sorted(changed_fields),
                    getattr(getattr(order, 'user', None), 'id', None),
                )
                asset_updates = {}
                server_updates = {}
                if 'user' in changed_fields:
                    asset_updates['user'] = order.user
                    server_updates['user'] = order.user
                if 'public_ip' in changed_fields:
                    asset_updates['public_ip'] = order.public_ip
                    server_updates['public_ip'] = order.public_ip
                if 'previous_public_ip' in changed_fields:
                    asset_updates['previous_public_ip'] = order.previous_public_ip
                    server_updates['previous_public_ip'] = order.previous_public_ip
                if 'server_name' in changed_fields:
                    asset_updates['asset_name'] = order.server_name
                    server_updates['server_name'] = order.server_name
                if 'instance_id' in changed_fields:
                    asset_updates['instance_id'] = order.instance_id
                    server_updates['instance_id'] = order.instance_id
                if 'provider_resource_id' in changed_fields:
                    asset_updates['provider_resource_id'] = order.provider_resource_id
                    server_updates['provider_resource_id'] = order.provider_resource_id
                for mtproxy_field in ('mtproxy_host', 'mtproxy_link', 'mtproxy_port'):
                    if mtproxy_field in changed_fields:
                        asset_updates[mtproxy_field] = getattr(order, mtproxy_field)
                if 'service_expires_at' in changed_fields:
                    asset_updates['actual_expires_at'] = order.service_expires_at
                    server_updates['expires_at'] = order.service_expires_at
                if 'status' in changed_fields:
                    status_asset_updates, status_server_updates = _primary_record_updates_for_order_status(order.status, order.provision_note)
                    asset_updates.update(status_asset_updates)
                    server_updates.update(status_server_updates)
                if asset_updates or server_updates:
                    _update_order_primary_records(order, asset_updates=asset_updates, server_updates=server_updates)
    except ValueError as exc:
        return _error(str(exc), status=400)
    order.refresh_from_db()
    return _ok(_cloud_order_detail_payload(order))


# 功能：更新相关业务对象；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_superuser_required
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
