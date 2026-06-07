"""云资产编辑、删除与自动续费后台 API。"""

import logging
import re
from decimal import Decimal

from asgiref.sync import async_to_sync
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods

from bot.models import TelegramGroupFilter
from cloud.api_assets import (
    _asset_payload,
    _infer_asset_order,
    _is_unattached_ip_asset,
    _parse_iso_datetime,
    _resolve_telegram_user,
    _sanitize_deleted_asset_payload,
    _sync_telegram_username,
    _unattached_ip_delete_due_at,
)
from cloud.api_orders import _cloud_order_summary_payload, _proxy_link_item, _proxy_links_with_main_link, _related_order_history_payload
from cloud.dashboard_snapshots import _refresh_dashboard_plan_snapshots, _refresh_dashboard_plan_snapshots_deferred, _refresh_lifecycle_plan_view
from cloud.lifecycle_schedule import compute_order_lifecycle_fields
from cloud.models import CloudAsset, CloudIpLog, CloudServerOrder, CloudServerPlan
from cloud.note_utils import append_note
from cloud.services import ensure_cloud_asset_operation_order, ensure_manual_expiry_operation_order, ensure_manual_owner_operation_order, ensure_manual_price_operation_order, record_cloud_ip_log, replace_cloud_asset_order_by_admin, scoped_server_match_for_asset, set_cloud_server_auto_renew_admin, sync_cloud_asset_user_binding
from core.dashboard_api import _error, _iso, _ok, _parse_decimal, _read_payload, _status_label, dashboard_login_required, dashboard_superuser_required

logger = logging.getLogger(__name__)


def _related_cloud_asset_records(asset: CloudAsset, *, order=None, previous_public_ip=None):
    query = Q()
    order_id = getattr(order, 'id', None) or getattr(asset, 'order_id', None)
    if order_id:
        query |= Q(order_id=order_id)
    for field in ('instance_id', 'provider_resource_id', 'asset_name'):
        value = str(getattr(asset, field, '') or '').strip()
        if value:
            query |= Q(**{field: value})
    ip_values = {
        str(getattr(asset, 'public_ip', '') or '').strip(),
        str(getattr(asset, 'previous_public_ip', '') or '').strip(),
        str(previous_public_ip or '').strip(),
    }
    ip_values.discard('')
    if ip_values:
        query |= Q(public_ip__in=ip_values) | Q(previous_public_ip__in=ip_values)
    if not query:
        return CloudAsset.objects.none()
    return CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).filter(query).exclude(id=asset.id).distinct()


@csrf_exempt
@dashboard_login_required
@require_http_methods(['GET', 'POST', 'PUT', 'PATCH'])
def update_cloud_asset(request, asset_id):
    if request.method == 'GET':
        asset = CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').filter(pk=asset_id).first()
        if not asset:
            return _error('云资产不存在', status=404)
        payload = _asset_payload(asset)
        order = _infer_asset_order(asset)
        ip_values = {str(asset.public_ip or '').strip(), str(asset.previous_public_ip or '').strip()}
        ip_values.discard('')
        log_lookup = Q(asset=asset)
        name_lookup = Q()
        if asset.asset_name:
            name_lookup |= Q(asset_name=asset.asset_name)
        if asset.instance_id:
            name_lookup |= Q(instance_id=asset.instance_id)
        if name_lookup and ip_values:
            log_lookup |= name_lookup & (Q(public_ip__in=ip_values) | Q(previous_public_ip__in=ip_values))
        logs = list(CloudIpLog.objects.filter(log_lookup).distinct().order_by('-created_at', '-id')[:100])
        lifecycle_order_nos = set()
        for log_item in logs:
            if log_item.order_no:
                lifecycle_order_nos.add(log_item.order_no)
            for matched_order_no in re.findall(r'订单号：([^；\n]+)|旧机订单\s+([^；\n]+)|新实例订单\s+([^；\n]+)', log_item.note or ''):
                for value in matched_order_no:
                    value = str(value or '').strip().rstrip('。')
                    if value and value != '-':
                        lifecycle_order_nos.add(value)
        lifecycle_order_links = {
            item.order_no: f'/admin/cloud-orders/{item.id}'
            for item in CloudServerOrder.objects.filter(order_no__in=lifecycle_order_nos).only('id', 'order_no')
        }
        payload.update({
            'order_status': getattr(order, 'status', '') or '',
            'order_status_label': _status_label(getattr(order, 'status', ''), CloudServerOrder.STATUS_CHOICES) if order else '',
            'service_started_at': _iso(getattr(order, 'service_started_at', None)),
            'renew_grace_expires_at': _iso(getattr(order, 'renew_grace_expires_at', None)),
            'suspend_at': _iso(getattr(order, 'suspend_at', None)),
            'delete_at': _iso(getattr(order, 'delete_at', None)),
            'ip_recycle_at': _iso(getattr(order, 'ip_recycle_at', None)),
            'last_renewed_at': _iso(getattr(order, 'last_renewed_at', None)),
            'provision_note': getattr(order, 'provision_note', '') or '',
            'created_at': _iso(asset.created_at),
            'related_order': _cloud_order_summary_payload(order),
            'history_orders': _related_order_history_payload(order),
            'ip_logs': [
                {
                    'id': item.id,
                    'event_type': item.event_type,
                    'event_label': dict(CloudIpLog.EVENT_CHOICES).get(item.event_type, item.event_type),
                    'order_no': item.order_no,
                    'asset_name': item.asset_name,
                    'public_ip': item.public_ip,
                    'previous_public_ip': item.previous_public_ip,
                    'note': item.note,
                    'created_at': _iso(item.created_at),
                    'order_detail_path': lifecycle_order_links.get(item.order_no, ''),
                    'order_link_path': lifecycle_order_links.get(item.order_no, ''),
                }
                for item in logs
            ],
            'lifecycle_order_links': lifecycle_order_links,
        })
        if payload.get('status') in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED}:
            _sanitize_deleted_asset_payload(payload)
        return _ok(payload)
    if not getattr(request.user, 'is_superuser', False):
        return _error('需要超级管理员权限', status=403)
    payload = _read_payload(request)
    owner_change_requested = False
    expiry_change_requested = False
    owner_target_after_commit = None
    previous_owner = None
    previous_expires_at = None
    previous_price = None
    price_change_requested = False
    public_ip_changed = False
    changed_public_ip_before = None
    changed_public_ip_after = None
    is_unattached_ip = False
    linked_order_id = None
    pending_order_updates = {}
    refresh_snapshots_needed = False
    refresh_unattached_delete_due = False
    refreshed_due_at = None
    related_server_sync_updates = {}
    try:
        with transaction.atomic():
            asset = CloudAsset.objects.select_for_update().select_related('order', 'user', 'cloud_account', 'telegram_group').get(pk=asset_id)
            is_unattached_ip = _is_unattached_ip_asset(asset)
            previous_owner = asset.user
            previous_expires_at = asset.actual_expires_at
            previous_price = asset.price if asset.price is not None else getattr(asset.order, 'total_amount', None)
            linked_order_id = asset.order_id

            user_lookup = payload.get('user_query') or payload.get('user_id') or payload.get('tg_user_id') or payload.get('username')
            username_raw = payload.get('user_query') or payload.get('username')
            clear_user = str(payload.get('clear_user') or '').lower() in {'1', 'true', 'yes', 'on'}
            owner_changed = clear_user or user_lookup not in (None, '')
            owner_change_requested = owner_changed and not is_unattached_ip
            owner_target = asset.user
            if clear_user:
                owner_target = None
                asset.user = None
                if asset.order_id and not is_unattached_ip:
                    pending_order_updates['user_id'] = None
                    pending_order_updates['last_user_id'] = None
            elif user_lookup not in (None, ''):
                owner_target = _resolve_telegram_user(user_lookup)
                if not owner_target:
                    return _error('未找到匹配的 Telegram 用户', status=404)
                asset.user = owner_target
                _sync_telegram_username(owner_target, username_raw)
                if asset.order_id and not is_unattached_ip:
                    pending_order_updates['user_id'] = owner_target.id
                    pending_order_updates['last_user_id'] = getattr(owner_target, 'tg_user_id', None)

            group_lookup_provided = 'telegram_group_query' in payload or 'telegram_group_id' in payload
            if group_lookup_provided:
                refresh_snapshots_needed = True
            group_lookup = payload.get('telegram_group_query')
            if group_lookup is None and 'telegram_group_id' in payload:
                group_lookup = payload.get('telegram_group_id')
            if group_lookup_provided:
                if group_lookup in (None, ''):
                    asset.telegram_group = None
                else:
                    group_lookup_text = str(group_lookup).strip().lstrip('@')
                    group_query = Q(username__iexact=group_lookup_text) | Q(title__icontains=group_lookup_text)
                    try:
                        numeric_group_id = int(group_lookup_text)
                        group_query |= Q(id=numeric_group_id) | Q(chat_id=numeric_group_id)
                    except (TypeError, ValueError):
                        pass
                    group = TelegramGroupFilter.objects.filter(group_query, collapsed=False).order_by('-updated_at', '-id').first()
                    if not group:
                        return _error('未找到匹配的 Telegram 群组，或该群组已在绑定页隐藏', status=404)
                    asset.telegram_group = group

            if 'price' in payload:
                try:
                    price = _parse_decimal(payload.get('price'), '价格').quantize(Decimal('0.01'))
                except ValueError as exc:
                    return _error(str(exc), status=400)
                asset.price = price
                price_change_requested = previous_price != price
                refresh_snapshots_needed = refresh_snapshots_needed or price_change_requested
                if asset.order_id and not str(getattr(asset.order, 'order_no', '') or '').startswith('SRVMANUAL'):
                    pending_order_updates['total_amount'] = price
                    if getattr(asset.order, 'auto_renew_enabled', False):
                        pending_order_updates['auto_renew_failure_notice_sent_at'] = None
                        if getattr(asset.order, 'status', '') == 'renew_pending' and not getattr(asset.order, 'paid_at', None):
                            pending_order_updates['pay_amount'] = price

            if 'currency' in payload:
                asset.currency = (payload.get('currency') or 'USDT').strip() or 'USDT'
                refresh_snapshots_needed = True
                if asset.order_id and asset.order.currency != asset.currency:
                    pending_order_updates['currency'] = asset.currency

            manual_expires_at = None
            if 'actual_expires_at' in payload:
                try:
                    manual_expires_at = _parse_iso_datetime(payload.get('actual_expires_at'), '到期时间')
                    asset.actual_expires_at = manual_expires_at
                except ValueError as exc:
                    return _error(str(exc), status=400)
                if asset.order_id and not is_unattached_ip:
                    refresh_snapshots_needed = True
                    same_order_active_assets = CloudAsset.objects.filter(
                        order_id=asset.order_id,
                        kind=CloudAsset.KIND_SERVER,
                    ).exclude(status__in=[
                        CloudAsset.STATUS_DELETED,
                        CloudAsset.STATUS_DELETING,
                        CloudAsset.STATUS_TERMINATED,
                        CloudAsset.STATUS_TERMINATING,
                    ]).count()
                    if same_order_active_assets <= 1:
                        lifecycle_updates = compute_order_lifecycle_fields(manual_expires_at) if manual_expires_at else {
                            'renew_grace_expires_at': None,
                            'suspend_at': None,
                            'delete_at': None,
                            'ip_recycle_at': None,
                        }
                        pending_order_updates.update({
                            'renew_notice_sent_at': None,
                            'auto_renew_notice_sent_at': None,
                            'auto_renew_failure_notice_sent_at': None,
                            'delete_notice_sent_at': None,
                            'recycle_notice_sent_at': None,
                            **lifecycle_updates,
                        })

            if asset.order_id:
                if 'mtproxy_link' in payload:
                    refresh_snapshots_needed = True
                    mtproxy_link = str(payload.get('mtproxy_link') or '').strip()
                    pending_order_updates['mtproxy_link'] = mtproxy_link or None
                if 'mtproxy_secret' in payload:
                    mtproxy_secret = str(payload.get('mtproxy_secret') or '').strip()
                    if mtproxy_secret:
                        pending_order_updates['mtproxy_secret'] = mtproxy_secret
                if 'mtproxy_host' in payload:
                    pending_order_updates['mtproxy_host'] = payload.get('mtproxy_host') or None
                if 'mtproxy_port' in payload:
                    mtproxy_port = payload.get('mtproxy_port')
                    normalized_port = int(mtproxy_port) if mtproxy_port not in (None, '') else None
                    pending_order_updates['mtproxy_port'] = normalized_port
                if 'provider_resource_id' in payload:
                    pending_order_updates['provider_resource_id'] = payload.get('provider_resource_id') or None
                if 'public_ip' in payload:
                    refresh_snapshots_needed = True
                    pending_order_updates['public_ip'] = payload.get('public_ip') or None
                if 'asset_name' in payload:
                    pending_order_updates['server_name'] = payload.get('asset_name') or None

            old_public_ip = asset.public_ip
            old_order_public_ip = getattr(asset.order, 'public_ip', None)
            old_provider_status = str(asset.provider_status or '')
            new_public_ip = payload.get('public_ip') or None if 'public_ip' in payload else asset.public_ip
            public_ip_previous_value = None
            if 'public_ip' in payload:
                for candidate in (old_public_ip, asset.previous_public_ip, old_order_public_ip):
                    candidate = str(candidate or '').strip() or None
                    if candidate and candidate != new_public_ip:
                        public_ip_previous_value = candidate
                        break
                if public_ip_previous_value:
                    asset.previous_public_ip = public_ip_previous_value
                    if asset.order_id and not is_unattached_ip:
                        pending_order_updates['previous_public_ip'] = public_ip_previous_value

            for field in ('asset_name', 'public_ip', 'provider_resource_id', 'instance_id', 'mtproxy_link', 'mtproxy_host', 'note'):
                if field in payload:
                    setattr(asset, field, payload.get(field) or None)
                    if field in {'asset_name', 'public_ip', 'provider_resource_id', 'instance_id', 'note'}:
                        related_server_sync_updates[field] = getattr(asset, field)
            if 'mtproxy_secret' in payload:
                mtproxy_secret = str(payload.get('mtproxy_secret') or '').strip()
                if mtproxy_secret:
                    asset.mtproxy_secret = mtproxy_secret
            if 'mtproxy_port' in payload:
                mtproxy_port = payload.get('mtproxy_port')
                asset.mtproxy_port = int(mtproxy_port) if mtproxy_port not in (None, '') else None
            if 'mtproxy_link' in payload:
                main_item = _proxy_link_item(asset.mtproxy_link)
                if main_item:
                    if main_item.get('server'):
                        asset.mtproxy_host = main_item['server']
                    if main_item.get('port'):
                        try:
                            asset.mtproxy_port = int(main_item['port'])
                        except (TypeError, ValueError):
                            pass
                    if main_item.get('secret'):
                        asset.mtproxy_secret = main_item['secret']
                asset.proxy_links = _proxy_links_with_main_link(asset.proxy_links or [], asset.mtproxy_link, asset.mtproxy_port)
                if asset.order_id:
                    pending_order_updates.update({
                        'mtproxy_link': asset.mtproxy_link,
                        'mtproxy_host': asset.mtproxy_host,
                        'mtproxy_port': asset.mtproxy_port,
                        'proxy_links': asset.proxy_links,
                    })
                    if asset.mtproxy_secret:
                        pending_order_updates['mtproxy_secret'] = asset.mtproxy_secret
            for field in ('provider', 'region_name', 'region_code'):
                if field in payload:
                    setattr(asset, field, payload.get(field) or None)
            if 'is_active' in payload:
                refresh_snapshots_needed = True
                asset.is_active = str(payload.get('is_active')).lower() in {'1', 'true', 'yes', 'on'}
            if 'shutdown_enabled' in payload:
                refresh_snapshots_needed = True
                asset.shutdown_enabled = str(payload.get('shutdown_enabled')).lower() in {'1', 'true', 'yes', 'on'}
            if 'server_delete_enabled' in payload:
                refresh_snapshots_needed = True
                asset.server_delete_enabled = str(payload.get('server_delete_enabled')).lower() in {'1', 'true', 'yes', 'on'}
            if 'ip_delete_enabled' in payload:
                refresh_snapshots_needed = True
                asset.ip_delete_enabled = str(payload.get('ip_delete_enabled')).lower() in {'1', 'true', 'yes', 'on'}

            if 'sort_order' in payload:
                sort_order = payload.get('sort_order')
                try:
                    asset.sort_order = int(sort_order) if sort_order not in (None, '') else 99
                except (TypeError, ValueError):
                    return _error('排序必须是数字', status=400)
            rebound_to_instance = bool(
                old_provider_status and '未附加' in old_provider_status and str(asset.instance_id or '').strip()
            )
            refresh_unattached_delete_due = bool(is_unattached_ip and payload and 'actual_expires_at' not in payload and not rebound_to_instance)
            if rebound_to_instance:
                asset.actual_expires_at = None
                asset.provider_status = '已重新绑定实例-待人工添加时间'
                asset.is_active = True
                if asset.status == CloudAsset.STATUS_UNKNOWN:
                    asset.status = CloudAsset.STATUS_RUNNING

            if refresh_unattached_delete_due:
                refreshed_due_at = _unattached_ip_delete_due_at()
                asset.actual_expires_at = refreshed_due_at
                if linked_order_id:
                    pending_order_updates['ip_recycle_at'] = refreshed_due_at
                    pending_order_updates['recycle_notice_sent_at'] = None

            asset.save()
            owner_target_after_commit = owner_target
            expiry_change_requested = manual_expires_at is not None and not is_unattached_ip
            refresh_snapshots_needed = refresh_snapshots_needed or owner_change_requested or expiry_change_requested
            public_ip_changed = 'public_ip' in payload and bool(public_ip_previous_value)
            refresh_snapshots_needed = refresh_snapshots_needed or public_ip_changed or bool(pending_order_updates)
            changed_public_ip_before = public_ip_previous_value
            changed_public_ip_after = new_public_ip
    except CloudAsset.DoesNotExist:
        return _error('云资产不存在', status=404)

    manual_replace_requested = owner_change_requested or expiry_change_requested
    manual_replace_authoritative = bool(
        manual_replace_requested
        and asset.provider == CloudServerPlan.PROVIDER_AWS_LIGHTSAIL
    )
    if linked_order_id and pending_order_updates and not manual_replace_authoritative:
        try:
            CloudServerOrder.objects.filter(pk=linked_order_id).update(**pending_order_updates, updated_at=timezone.now())
            linked_asset_updates = {}
            if 'public_ip' in pending_order_updates:
                linked_asset_updates['public_ip'] = pending_order_updates.get('public_ip')
            if 'previous_public_ip' in pending_order_updates:
                linked_asset_updates['previous_public_ip'] = pending_order_updates.get('previous_public_ip')
            if linked_asset_updates:
                CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, order_id=linked_order_id).update(**linked_asset_updates, updated_at=timezone.now())
        except Exception as exc:
            logger.warning('CLOUD_ASSET_MANUAL_ORDER_SYNC_SKIPPED asset_id=%s order_id=%s fields=%s error=%s', asset_id, linked_order_id, sorted(pending_order_updates), exc)

    asset = CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').get(pk=asset_id)
    if related_server_sync_updates:
        related_asset_updates = dict(related_server_sync_updates)
        if 'public_ip' in payload and changed_public_ip_before:
            related_asset_updates['previous_public_ip'] = changed_public_ip_before
        try:
            CloudAsset.objects.filter(
                scoped_server_match_for_asset(asset, include_order=False, include_ip=True),
                kind=CloudAsset.KIND_SERVER,
            ).exclude(id=asset.id).update(**related_asset_updates, updated_at=timezone.now())
        except Exception as exc:
            logger.warning('CLOUD_ASSET_RELATED_ASSET_SYNC_SKIPPED asset_id=%s fields=%s error=%s', asset_id, sorted(related_asset_updates), exc)
    if refresh_unattached_delete_due and refreshed_due_at:
        related_ids = list(
            _related_cloud_asset_records(asset, order=asset.order, previous_public_ip=changed_public_ip_before)
            .order_by('id')
            .values_list('id', flat=True)
        )
        if related_ids:
            updated_count = CloudAsset.objects.filter(id__in=related_ids).update(actual_expires_at=refreshed_due_at, updated_at=timezone.now())
            logger.info(
                'CLOUD_UNATTACHED_IP_DELETE_DUE_REFRESHED asset_id=%s order_id=%s due_at=%s related_asset_ids=%s related_updated=%s actor_id=%s',
                asset_id,
                linked_order_id,
                refreshed_due_at.isoformat(),
                related_ids,
                updated_count,
                getattr(request.user, 'id', None),
            )
    if manual_replace_authoritative:
        try:
            order, err = replace_cloud_asset_order_by_admin(
                asset,
                new_user=owner_target_after_commit,
                new_expires_at=asset.actual_expires_at if expiry_change_requested else None,
                new_price=asset.price if price_change_requested else None,
                previous_user=previous_owner,
                previous_expires_at=previous_expires_at,
                previous_price=previous_price,
            )
            if err:
                logger.warning('CLOUD_ASSET_MANUAL_REPLACE_ORDER_SKIPPED asset_id=%s error=%s', asset_id, err)
                if owner_change_requested:
                    fallback_order, fallback_err = ensure_manual_owner_operation_order(asset, owner_target_after_commit, previous_user=previous_owner, previous_expires_at=previous_expires_at)
                    if fallback_err:
                        logger.warning('CLOUD_ASSET_MANUAL_OWNER_ORDER_SKIPPED asset_id=%s error=%s', asset_id, fallback_err)
                if expiry_change_requested:
                    fallback_order, fallback_err = ensure_manual_expiry_operation_order(asset, asset.actual_expires_at, previous_expires_at=previous_expires_at)
                    if fallback_err:
                        logger.warning('CLOUD_ASSET_MANUAL_EXPIRY_ORDER_SKIPPED asset_id=%s error=%s', asset_id, fallback_err)
        except Exception as exc:
            logger.exception('CLOUD_ASSET_MANUAL_REPLACE_ORDER_FAILED asset_id=%s error=%s', asset_id, exc)
    elif owner_change_requested:
        try:
            owner_order, owner_err = ensure_manual_owner_operation_order(
                asset,
                owner_target_after_commit,
                previous_user=previous_owner,
                previous_expires_at=previous_expires_at,
            )
            if owner_err:
                logger.warning('CLOUD_ASSET_MANUAL_OWNER_AUDIT_ORDER_SKIPPED asset_id=%s error=%s', asset_id, owner_err)
        except Exception as exc:
            logger.exception('CLOUD_ASSET_MANUAL_OWNER_AUDIT_ORDER_FAILED asset_id=%s error=%s', asset_id, exc)
    if price_change_requested and asset.price is not None and not manual_replace_authoritative:
        try:
            price_order, price_err = ensure_manual_price_operation_order(
                asset,
                asset.price,
                previous_price=previous_price,
            )
            if price_err:
                logger.warning('CLOUD_ASSET_MANUAL_PRICE_AUDIT_ORDER_SKIPPED asset_id=%s error=%s', asset_id, price_err)
        except Exception as exc:
            logger.exception('CLOUD_ASSET_MANUAL_PRICE_AUDIT_ORDER_FAILED asset_id=%s error=%s', asset_id, exc)
    if public_ip_changed:
        record_cloud_ip_log(
            event_type='changed',
            order=asset.order,
            asset=asset,
            previous_public_ip=changed_public_ip_before,
            public_ip=changed_public_ip_after,
            note=f'后台手动更新IP：{changed_public_ip_before or "未分配"} → {changed_public_ip_after or "未分配"}',
        )
    if 'actual_expires_at' in payload:
        _refresh_lifecycle_plan_view(f'cloud_asset_expiry:{asset_id}', lifecycle_limit=1000)
    if refresh_snapshots_needed:
        _refresh_dashboard_plan_snapshots_deferred(f'cloud_asset:{asset_id}', cloud_asset_ids=[asset_id])
    asset = CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').get(pk=asset_id)
    return _ok(_asset_payload(asset))


# 功能：处理 后台 API 接口 中的 toggle cloud asset auto renew 业务流程。
@csrf_exempt
@dashboard_superuser_required
@require_POST
def toggle_cloud_asset_auto_renew(request, asset_id):
    asset = CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'telegram_group').filter(pk=asset_id).first()
    if not asset:
        return _error('云资产不存在', status=404)
    payload = _read_payload(request)
    enabled = str(payload.get('enabled')).lower() in {'1', 'true', 'yes', 'on'}
    if not asset.user_id:
        sync_cloud_asset_user_binding(asset)
    if not asset.user_id:
        return _error('该代理未绑定用户，无法设置自动续费', status=400)
    if not asset.order_id:
        order, err = async_to_sync(ensure_cloud_asset_operation_order)(asset.id, asset.user_id, True)
        if err:
            return _error(err, status=400)
        if not order:
            return _error('该代理无法生成操作订单，无法设置自动续费', status=400)
        asset.order = order
        asset.order_id = order.id
    order = async_to_sync(set_cloud_server_auto_renew_admin)(asset.order_id, enabled)
    if order is False:
        return _error('当前状态不可开启自动续费', status=400)
    if not order:
        return _error('订单不存在', status=404)
    _refresh_dashboard_plan_snapshots(f'cloud_asset_auto_renew:{asset_id}')
    asset = CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').get(pk=asset_id)
    return _ok(_asset_payload(asset))


# 功能：删除或标记删除相关业务对象；当前函数属于 云资产后台 API。
@csrf_exempt
@dashboard_superuser_required
@require_http_methods(['POST', 'DELETE'])
def delete_cloud_asset(request, asset_id: int):
    asset = CloudAsset.objects.select_related('order').filter(id=asset_id).first()
    if not asset:
        return _error('代理记录不存在', status=404)
    now = timezone.now()
    before_status = asset.status
    previous_public_ip = asset.public_ip or asset.previous_public_ip
    note = f'后台手动删除代理列表记录；原IP={previous_public_ip or "-"}；后续云同步按全新资源处理，不再继承本订单状态；时间: {now.isoformat()}'
    order = asset.order
    residual_qs = _related_cloud_asset_records(asset, order=order, previous_public_ip=previous_public_ip)
    removed_server_ids = list(residual_qs.order_by('id').values_list('id', flat=True))

    # 功能：提供 云资产后台 API 的内部辅助逻辑，供同模块流程复用。
    def _clear_order_cloud_binding(target_order):
        if not target_order:
            return False
        target_order.server_name = ''
        target_order.instance_id = ''
        target_order.provider_resource_id = ''
        target_order.public_ip = None
        target_order.previous_public_ip = None
        target_order.static_ip_name = ''
        target_order.mtproxy_host = ''
        target_order.mtproxy_port = 0
        target_order.mtproxy_secret = ''
        target_order.mtproxy_link = ''
        target_order.proxy_links = []
        target_order.login_user = ''
        target_order.login_password = ''
        target_order.provision_note = append_note(
            target_order.provision_note,
            f'后台代理列表删除已清除云资源绑定；原IP={previous_public_ip or "-"}；后续云同步按全新资源处理，不再继承本订单状态；时间: {now.isoformat()}。',
        )
        target_order.save(update_fields=[
            'server_name', 'instance_id', 'provider_resource_id', 'public_ip', 'previous_public_ip',
            'static_ip_name', 'mtproxy_host', 'mtproxy_port', 'mtproxy_secret', 'mtproxy_link',
            'proxy_links', 'login_user', 'login_password', 'provision_note', 'updated_at',
        ])
        return True

    if not CloudIpLog.objects.filter(asset_id=asset.id, event_type=CloudIpLog.EVENT_DELETED, note__contains='后台手动删除代理列表记录').exists():
        record_cloud_ip_log(event_type=CloudIpLog.EVENT_DELETED, order=order, asset=asset, previous_public_ip=previous_public_ip, public_ip=None, note=note)
    order_status_changed = _clear_order_cloud_binding(order)
    if removed_server_ids:
        CloudAsset.objects.filter(id__in=removed_server_ids).delete()
        logger.info(
            'DASHBOARD_CLOUD_ASSET_RESIDUAL_RECORDS_DELETED asset_id=%s order_id=%s residual_asset_ids=%s actor_id=%s',
            asset_id,
            getattr(order, 'id', None),
            removed_server_ids,
            getattr(request.user, 'id', None),
        )
    asset.delete()
    logger.info(
        'DASHBOARD_CLOUD_ASSET_DELETED asset_id=%s order_id=%s before_status=%s previous_public_ip=%s order_binding_cleared=%s removed_server_ids=%s actor_id=%s',
        asset_id,
        getattr(order, 'id', None),
        before_status,
        previous_public_ip,
        order_status_changed,
        removed_server_ids,
        getattr(request.user, 'id', None),
    )
    return _ok({
        'target_type': 'cloud_asset',
        'target_id': asset_id,
        'before_status': before_status,
        'after_status': None,
        'hard_deleted': True,
        'exists_after': CloudAsset.objects.filter(id=asset_id).exists(),
        'removed_servers': len(removed_server_ids),
        'removed_server_ids': removed_server_ids,
        'order_status_changed': order_status_changed,
    })
