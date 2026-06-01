import logging

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from bot.api import _provider_status_label
from cloud.models import CloudAsset, CloudServerOrder
from cloud.server_records import Server
from cloud.aliyun_simple import _build_client, _region_endpoint, _runtime_options
from core.cloud_accounts import cloud_account_label, cloud_account_label_variants, list_active_cloud_accounts
from core.persistence import record_external_sync_log
from cloud.lifecycle_schedule import compute_order_lifecycle_fields
from cloud.services import record_cloud_ip_log, sync_cloud_asset_user_binding


_ACTIVE_ORDER_STATUSES = {'pending', 'provisioning', 'completed', 'expiring', 'renew_pending', 'suspended', 'deleting'}
_SYNC_EXCLUDED_ASSET_STATUSES = {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_TERMINATING}
_SYNC_EXCLUDED_SERVER_STATUSES = {Server.STATUS_DELETED, Server.STATUS_DELETING, Server.STATUS_TERMINATED, Server.STATUS_TERMINATING}
_SYNC_EXCLUDED_ORDER_STATUSES = {'deleted', 'deleting', 'expired', 'cancelled'}
_MISSING_DELETED_STATUS = '云上未找到实例'

logger = logging.getLogger(__name__)


def _visible_asset_total():
    from cloud.api import _cloud_assets_base_queryset, _dedupe_cloud_asset_rows
    return len(_dedupe_cloud_asset_rows(list(_cloud_assets_base_queryset())))


def _resolve_order_for_ip(public_ip, account=None):
    normalized_ip = str(public_ip or '').strip()
    if not normalized_ip:
        return None
    queryset = CloudServerOrder.objects.filter(
        Q(public_ip=normalized_ip) | Q(previous_public_ip=normalized_ip),
        status__in=_ACTIVE_ORDER_STATUSES,
    )
    if account:
        labels = cloud_account_label_variants(account)
        queryset = queryset.filter(Q(cloud_account=account) | Q(account_label__in=labels))
    return queryset.order_by('-created_at', '-id').first()


NORMAL_ALIYUN_STATES = {'running', 'starting', 'pending'}


def _parse_datetime_value(value):
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed:
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed
    return None


def _parse_expire_time(item):
    for key in ('ExpiredTime', 'ExpireTime', 'ExpirationTime', 'EndTime'):
        parsed = _parse_datetime_value(item.get(key))
        if parsed:
            return parsed

    charge_info = item.get('ChargeData') or item.get('ChargeConfig') or item.get('ChargeInfo') or {}
    if isinstance(charge_info, dict):
        for key in ('ExpiredTime', 'ExpireTime', 'ExpirationTime', 'EndTime'):
            parsed = _parse_datetime_value(charge_info.get(key))
            if parsed:
                return parsed

    return None


def _resolve_account_id(item):
    for key in ('OwnerId', 'AccountId', 'ResourceOwnerId', 'UserId'):
        value = item.get(key)
        if value:
            return str(value)
    return 'aliyun'


def _resolve_aliyun_status(item, expires_at=None):
    raw_status = str(item.get('Status') or '').strip().lower()
    business_status = str(item.get('BusinessStatus') or '').strip().lower()
    disable_reason = str(item.get('DisableReason') or '').strip().lower()
    if business_status == 'expired' or disable_reason == 'expired':
        return CloudAsset.STATUS_EXPIRED, raw_status, business_status, disable_reason
    if raw_status == 'running' and business_status in {'', 'normal'}:
        return CloudAsset.STATUS_RUNNING, raw_status, business_status, disable_reason
    if raw_status == 'starting':
        return CloudAsset.STATUS_STARTING, raw_status, business_status, disable_reason
    if raw_status == 'pending':
        return CloudAsset.STATUS_PENDING, raw_status, business_status, disable_reason
    if raw_status == 'stopping':
        return CloudAsset.STATUS_STOPPING, raw_status, business_status, disable_reason
    if raw_status == 'stopped':
        return CloudAsset.STATUS_STOPPED, raw_status, business_status, disable_reason
    if raw_status == 'disabled':
        return CloudAsset.STATUS_SUSPENDED, raw_status, business_status, disable_reason
    if raw_status == 'deleting':
        return CloudAsset.STATUS_DELETING, raw_status, business_status, disable_reason
    if raw_status == 'deleted':
        return CloudAsset.STATUS_DELETED, raw_status, business_status, disable_reason
    return CloudAsset.STATUS_UNKNOWN, raw_status, business_status, disable_reason


def _status_label(status):
    return dict(CloudAsset.STATUS_CHOICES).get(status, status or '-')


def _elevate_deleted_when_ip_missing(status, public_ip):
    return status


def _order_status_from_cloud_status(status):
    if status in {CloudAsset.STATUS_PENDING, CloudAsset.STATUS_STARTING}:
        return 'provisioning'
    if status in {CloudAsset.STATUS_STOPPED, CloudAsset.STATUS_STOPPING, CloudAsset.STATUS_SUSPENDED}:
        return 'suspended'
    if status in {CloudAsset.STATUS_TERMINATING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_DELETED}:
        return 'deleted'
    if status == CloudAsset.STATUS_EXPIRED:
        return 'expired'
    return 'completed'


def _order_status_from_cloud_sync(order, status, expires_at=None):
    cloud_status = _order_status_from_cloud_status(status)
    if cloud_status in {'deleted', 'expired', 'provisioning'} or not order:
        return cloud_status
    effective_expires_at = expires_at or getattr(order, 'service_expires_at', None)
    if cloud_status == 'completed' and effective_expires_at and effective_expires_at <= timezone.now():
        return 'expiring'
    return cloud_status


def _resolve_asset(instance_id, public_ip, account=None, region_code=''):
    lookup = Q(kind=CloudAsset.KIND_SERVER)
    if account:
        labels = cloud_account_label_variants(account)
        lookup &= (Q(cloud_account=account) | Q(account_label__in=labels) | (Q(cloud_account__isnull=True) & (Q(account_label='') | Q(account_label__isnull=True))))
    region_code = str(region_code or '').strip()
    if region_code:
        lookup &= Q(region_code=region_code)
    base_queryset = CloudAsset.objects.filter(lookup).filter(Q(order__isnull=True) | ~Q(order__status__in=_SYNC_EXCLUDED_ORDER_STATUSES)).exclude(status__in=_SYNC_EXCLUDED_ASSET_STATUSES)
    if public_ip:
        asset = base_queryset.filter(Q(public_ip=public_ip) | Q(previous_public_ip=public_ip)).order_by(*_asset_resolve_ordering(public_ip)).first()
        if asset:
            return asset
    if instance_id:
        asset = base_queryset.filter(Q(instance_id=instance_id) | Q(provider_resource_id=instance_id) | Q(asset_name=instance_id)).order_by('-updated_at', '-id').first()
        if asset:
            return asset
    return None


def _resolve_server(instance_id, public_ip, account=None, region_code=''):
    base = Q()
    if account:
        base &= Q(account_label__in=cloud_account_label_variants(account))
    region_code = str(region_code or '').strip()
    if region_code:
        base &= Q(region_code=region_code)
    base_queryset = Server.objects.filter(base).filter(Q(order__isnull=True) | ~Q(order__status__in=_SYNC_EXCLUDED_ORDER_STATUSES)).exclude(status__in=_SYNC_EXCLUDED_SERVER_STATUSES)
    if public_ip:
        server = base_queryset.filter(Q(public_ip=public_ip) | Q(previous_public_ip=public_ip)).order_by(*_server_resolve_ordering(public_ip)).first()
        if server:
            return server
    if instance_id:
        server = base_queryset.filter(Q(instance_id=instance_id) | Q(provider_resource_id=instance_id) | Q(server_name=instance_id)).order_by('-updated_at', '-id').first()
        if server:
            return server
    return None


def _asset_resolve_ordering(public_ip=''):
    ordering = []
    if public_ip:
        ordering.append(Case(
            When(public_ip=public_ip, then=Value(0)),
            When(previous_public_ip=public_ip, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        ))
    ordering.extend(['-updated_at', '-id'])
    return ordering


def _server_resolve_ordering(public_ip=''):
    ordering = []
    if public_ip:
        ordering.append(Case(
            When(public_ip=public_ip, then=Value(0)),
            When(previous_public_ip=public_ip, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        ))
    ordering.extend(['-updated_at', '-id'])
    return ordering


def _aliyun_order_updates_from_sync(linked_order, *, normalized_status, expires_at, account, account_label, region, item, asset_name, instance_id, public_ip):
    previous_public_ip = linked_order.previous_public_ip
    if public_ip and linked_order.public_ip != public_ip:
        previous_public_ip = linked_order.public_ip or linked_order.previous_public_ip
    order_updates = {
        'status': _order_status_from_cloud_sync(linked_order, normalized_status, expires_at),
        'provider': 'aliyun_simple',
        'cloud_account': account,
        'account_label': account_label,
        'region_code': region,
        'region_name': item.get('RegionId') or region,
        'server_name': asset_name,
        'instance_id': instance_id,
        'provider_resource_id': instance_id,
        'previous_public_ip': previous_public_ip,
        'public_ip': public_ip or None,
        'updated_at': timezone.now(),
    }
    if expires_at:
        expiry_changed = linked_order.service_expires_at != expires_at
        order_updates['service_expires_at'] = expires_at
        order_updates.update(compute_order_lifecycle_fields(expires_at))
        if expiry_changed:
            order_updates.update({
                'renew_notice_sent_at': None,
                'auto_renew_notice_sent_at': None,
                'auto_renew_failure_notice_sent_at': None,
                'delete_notice_sent_at': None,
                'recycle_notice_sent_at': None,
            })
    return order_updates



def _mark_deleted_when_missing_in_aliyun(region, existing_instance_ids, stdout, account=None):
    verification_deleted_items = []
    queryset = CloudAsset.objects.filter(
        kind=CloudAsset.KIND_SERVER,
        provider='aliyun_simple',
    )
    if account:
        labels = cloud_account_label_variants(account)
        queryset = queryset.filter(Q(cloud_account=account) | Q(account_label__in=labels))
    queryset = queryset.exclude(status__in=_SYNC_EXCLUDED_ASSET_STATUSES)
    queryset = queryset.filter(
        Q(region_code=region) | Q(region_code='') | Q(region_code__isnull=True)
    ).order_by('-updated_at', '-id')
    now_iso = timezone.now().isoformat()
    for asset in queryset:
        instance_id = str(asset.instance_id or '').strip()
        public_ip = str(asset.public_ip or '').strip()
        if instance_id and instance_id in existing_instance_ids:
            continue
        old_public_ip = public_ip or str(asset.previous_public_ip or '').strip()
        asset.status = CloudAsset.STATUS_DELETED
        asset.is_active = False
        asset.previous_public_ip = old_public_ip or asset.previous_public_ip
        asset.public_ip = None
        asset.provider_status = _MISSING_DELETED_STATUS
        asset.save(update_fields=['status', 'is_active', 'previous_public_ip', 'public_ip', 'provider_status', 'updated_at'])
        server_lookup = Q()
        provider_resource_id = str(asset.provider_resource_id or '').strip()
        if instance_id:
            server_lookup |= Q(instance_id=instance_id)
        if provider_resource_id:
            server_lookup |= Q(provider_resource_id=provider_resource_id)
        if public_ip:
            server_lookup |= Q(public_ip=public_ip)
        if old_public_ip:
            server_lookup |= Q(previous_public_ip=old_public_ip)
        server = None
        if server_lookup:
            server_queryset = Server.objects.filter(
                server_lookup,
                provider='aliyun_simple',
                region_code=region,
            )
            if account:
                server_queryset = server_queryset.filter(account_label__in=cloud_account_label_variants(account))
            server = server_queryset.order_by('-updated_at', '-id').first()
        if server:
            server.status = Server.STATUS_DELETED
            server.is_active = False
            server.previous_public_ip = old_public_ip or server.previous_public_ip
            server.public_ip = None
            server.provider_status = _MISSING_DELETED_STATUS
            server.save(update_fields=['status', 'is_active', 'previous_public_ip', 'public_ip', 'provider_status', 'updated_at'])
        order = getattr(asset, 'order', None) or _resolve_order_for_ip(old_public_ip, account)
        if order:
            order.status = 'deleted'
            order.previous_public_ip = old_public_ip or order.previous_public_ip
            order.public_ip = None
            order.save(update_fields=['status', 'previous_public_ip', 'public_ip', 'updated_at'])
        record_cloud_ip_log(
            event_type='deleted',
            order=order,
            asset=asset,
            server=server,
            previous_public_ip=old_public_ip or None,
            public_ip=None,
            note='IP校验发现云上不存在，已标记删除',
        )
        verification_deleted_items.append(f'{asset.id}:{old_public_ip or "缺失"}:{instance_id or asset.asset_name or "-"}')
        stdout.stdout.write(stdout.style.WARNING(
            f'IP校验 已删除 资产#{asset.id} IP={old_public_ip or "缺失"} 云上不存在'
        ))
    return verification_deleted_items


class Command(BaseCommand):
    help = '同步阿里云轻量应用服务器到统一云资产表'

    def add_arguments(self, parser):
        parser.add_argument('--region', default='cn-hongkong', help='阿里云地域代码，默认 cn-hongkong')
        parser.add_argument('--account-id', default='', help='只同步指定后台云账号 ID')
        parser.add_argument('--asset-id', default='', help='只处理指定资产 ID 对应资源')
        parser.add_argument('--instance-id', default='', help='只处理指定实例 ID')
        parser.add_argument('--public-ip', default='', help='只处理指定公网 IP')

    def handle(self, *args, **options):
        region = options['region']
        accounts = list_active_cloud_accounts('aliyun', region)
        account_id = str(options.get('account_id') or '').strip()
        if account_id:
            accounts = [account for account in accounts if account and str(account.id) == account_id]
            if not accounts:
                raise CommandError(f'未找到启用的阿里云云账号 #{account_id}')
        if not accounts:
            raise CommandError('未添加启用的阿里云云账号，拒绝使用环境变量同步。请先在后台「云账号」添加阿里云账号。')
        target_asset_id = str(options.get('asset_id') or '').strip()
        target_instance_id = str(options.get('instance_id') or '').strip()
        target_public_ip = str(options.get('public_ip') or '').strip()
        target_scope_enabled = bool(target_asset_id or target_instance_id or target_public_ip)
        if target_asset_id and not (target_instance_id or target_public_ip):
            target_asset = CloudAsset.objects.filter(pk=target_asset_id).first()
            if target_asset:
                target_instance_id = target_asset.instance_id or target_asset.provider_resource_id or ''
                target_public_ip = target_asset.public_ip or target_asset.previous_public_ip or ''
        target_scope = {
            'asset_id': target_asset_id,
            'instance_id': target_instance_id,
            'public_ip': target_public_ip,
        }
        self.stdout.write(
            f'阿里云同步开始：账号数={len(accounts)}；region={region}；目标={target_scope if any(target_scope.values()) else "全部"}'
        )
        logger.info(
            'ALIYUN_SYNC_START accounts=%s region=%s target_scope=%s',
            [getattr(account, 'id', None) for account in accounts if account],
            region,
            target_scope,
        )

        def target_matches(*values):
            if not target_scope_enabled:
                return True
            normalized_values = {str(value or '').strip() for value in values if str(value or '').strip()}
            return bool(
                (target_instance_id and target_instance_id in normalized_values)
                or (target_public_ip and target_public_ip in normalized_values)
            )

        before_asset_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
        before_visible_asset_total = _visible_asset_total()

        from alibabacloud_swas_open20200601 import models as swas_models
        count = 0
        created_count = 0
        updated_count = 0
        deleted_by_missing_ip_count = 0
        created_asset_ids = []
        updated_asset_ids = []
        status_changed_items = []
        deleted_by_missing_ip_items = []
        conflict_skipped_items = []
        claimed_assets = {}
        synced_instance_ids = []
        verification_deleted_items = []
        account_summary_lines = []
        sync_errors = []
        for account in accounts:
            account_label = cloud_account_label(account) or 'aliyun'
            self.stdout.write(f'阿里云账号同步开始：账号={account_label}；账号ID={getattr(account, "id", "-")}；地区={region}')
            logger.info('ALIYUN_SYNC_ACCOUNT_START account_id=%s account_label=%s region=%s', getattr(account, 'id', None), account_label, region)
            account_stats = {
                'label': account_label,
                'region': region,
                'count': 0,
                'created': 0,
                'updated': 0,
                'deleted_by_missing_ip': 0,
                'ips': [],
            }
            client = _build_client(_region_endpoint(region), account=account)
            if not client:
                message = f'跳过阿里云账号 {account_label}：无法创建客户端'
                self.stdout.write(self.style.WARNING(message))
                logger.warning('ALIYUN_SYNC_CLIENT_SKIPPED account_id=%s account_label=%s region=%s reason=%s', getattr(account, 'id', None), account_label, region, message)
                sync_errors.append(message)
                if account:
                    account.mark_status(account.STATUS_ERROR, message)
                record_external_sync_log(
                    source='aliyun',
                    action='list_instances',
                    target=region,
                    request_payload={'region_id': region, 'account_id': getattr(account, 'id', None)},
                    response_payload={'error': message},
                    is_success=False,
                    error_message=message,
                    account=account,
                )
                continue
            instances = []
            page_number = 1
            while True:
                try:
                    response = client.list_instances_with_options(
                        swas_models.ListInstancesRequest(region_id=region, page_size=100, page_number=page_number),
                        _runtime_options(),
                    )
                except Exception as exc:
                    logger.exception('ALIYUN_SYNC_LIST_INSTANCES_FAILED account_id=%s account_label=%s region=%s page=%s error=%s', getattr(account, 'id', None), account_label, region, page_number, exc)
                    message = f'阿里云账号 {account_label} 地区 {region} 第 {page_number} 页实例同步失败: {exc}'
                    sync_errors.append(message)
                    if account:
                        account.mark_status(account.STATUS_ERROR, message)
                    record_external_sync_log(
                        source='aliyun',
                        action='list_instances',
                        target=region,
                        request_payload={'region_id': region, 'page_size': 100, 'page_number': page_number},
                        response_payload={'error': str(exc)},
                        is_success=False,
                        error_message=str(exc),
                        account=account,
                    )
                    raise
                body = response.body.to_map()
                page_items = body.get('Instances', []) or []
                record_external_sync_log(
                    source='aliyun',
                    action='list_instances',
                    target=region,
                    request_payload={'region_id': region, 'page_size': 100, 'page_number': page_number},
                    response_payload={
                        'count': len(page_items),
                        'total': body.get('TotalCount') or body.get('Total') or 0,
                    },
                    is_success=True,
                    account=account,
                )
                logger.info(
                    'ALIYUN_SYNC_INSTANCE_PAGE account_id=%s account_label=%s region=%s page=%s count=%s total=%s',
                    getattr(account, 'id', None),
                    account_label,
                    region,
                    page_number,
                    len(page_items),
                    body.get('TotalCount') or body.get('Total') or 0,
                )
                instances.extend(page_items)
                total_count = int(body.get('TotalCount') or body.get('Total') or 0)
                if len(page_items) < 100 or (total_count and len(instances) >= total_count):
                    break
                page_number += 1
            account_instance_ids = []
            for item in instances:
                instance_id = item.get('InstanceId') or ''
                public_ip = item.get('PublicIpAddress') or item.get('PublicIp') or ''
                if not target_matches(instance_id, public_ip):
                    continue
                expires_at = _parse_expire_time(item)
                account_id = _resolve_account_id(item)
                normalized_status, raw_status, business_status, disable_reason = _resolve_aliyun_status(item, expires_at)
                normalized_status = _elevate_deleted_when_ip_missing(normalized_status, public_ip)
                provider_status = _provider_status_label(' / '.join([part for part in [raw_status or None, business_status or None, disable_reason or None] if part]) or None)
                note = (
                    f"状态: {_provider_status_label(item.get('Status') or '-')}；"
                    f"业务状态: {_provider_status_label(item.get('BusinessStatus') or '-')}；"
                    f"禁用原因: {_provider_status_label(item.get('DisableReason') or '-')}；"
                    f"公网IP: {public_ip or '缺失'}；"
                    f"套餐: {item.get('PlanId') or '-'}；到期: {expires_at or '-'}；最近同步: {timezone.now().isoformat()}"
                )
                asset_name = item.get('InstanceName') or instance_id
                asset_defaults = {
                    'kind': CloudAsset.KIND_SERVER,
                    'source': CloudAsset.SOURCE_ALIYUN,
                    'provider': 'aliyun_simple',
                    'cloud_account': account,
                    'account_label': account_label,
                    'region_code': region,
                    'region_name': item.get('RegionId') or region,
                    'asset_name': asset_name,
                    'instance_id': instance_id,
                    'provider_resource_id': instance_id,
                    'public_ip': public_ip,
                    'actual_expires_at': expires_at,
                    'currency': 'USDT',
                    'status': normalized_status,
                    'provider_status': provider_status,
                    'is_active': normalized_status in CloudAsset.ACTIVE_STATUSES,
                }
                asset = _resolve_asset(instance_id, public_ip, account, region)
                linked_order = getattr(asset, 'order', None) if asset else None
                if linked_order and linked_order.status not in _ACTIVE_ORDER_STATUSES:
                    linked_order = None
                linked_order = linked_order or _resolve_order_for_ip(public_ip, account)
                if linked_order:
                    asset_defaults['order'] = linked_order
                    if not asset:
                        asset_defaults['user'] = linked_order.user
                    asset_defaults['actual_expires_at'] = expires_at or linked_order.service_expires_at
                if asset:
                    asset_defaults['user'] = asset.user
                    asset_defaults['actual_expires_at'] = expires_at or asset.actual_expires_at
                asset_signature = f'{instance_id or "-"}|{public_ip or "缺失"}'
                old_status = asset.status if asset else None
                old_public_ip = asset.public_ip if asset else None
                ip_changed = bool(asset and old_public_ip and old_public_ip != public_ip)
                if asset:
                    claimed_signature = claimed_assets.get(asset.id)
                    if claimed_signature and claimed_signature != asset_signature:
                        occupied_ip = claimed_signature.split('|')[-1]
                        current_ip = asset_signature.split('|')[-1]
                        conflict_skipped_items.append(f'{asset.id}:{occupied_ip}->{current_ip}')
                        self.stdout.write(
                            self.style.WARNING(
                                f'冲突已跳过 资产#{asset.id} 已占IP={occupied_ip} 当前IP={current_ip}'
                            )
                        )
                    else:
                        claimed_assets[asset.id] = asset_signature
                        if ip_changed:
                            asset.previous_public_ip = old_public_ip
                        for key, value in asset_defaults.items():
                            setattr(asset, key, value)
                        if not getattr(asset, 'user_id', None):
                            sync_cloud_asset_user_binding(asset, persist=False)
                        asset.save()
                        updated_count += 1
                        account_stats['updated'] += 1
                        updated_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{instance_id or asset_name}')
                else:
                    asset = CloudAsset.objects.create(**asset_defaults)
                    sync_cloud_asset_user_binding(asset)
                    claimed_assets[asset.id] = asset_signature
                    created_count += 1
                    account_stats['created'] += 1
                    created_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{instance_id or asset_name}')
                if old_status is not None and old_status != normalized_status:
                    status_changed_items.append(f'{asset.id}:{public_ip or "缺失"}:{old_status}->{normalized_status}')
                if normalized_status == CloudAsset.STATUS_DELETED and not str(public_ip or '').strip():
                    deleted_by_missing_ip_count += 1
                    account_stats['deleted_by_missing_ip'] += 1
                    deleted_by_missing_ip_items.append(f'{asset.id}:{public_ip or "缺失"}:{asset_name or instance_id}')

                server = _resolve_server(instance_id, public_ip, account, region)
                server_defaults = {
                    'source': Server.SOURCE_ALIYUN,
                    'provider': 'aliyun_simple',
                    'account_label': account_label or account_id,
                    'region_code': region,
                    'region_name': item.get('RegionId') or region,
                    'server_name': asset_name,
                    'instance_id': instance_id,
                    'provider_resource_id': instance_id,
                    'public_ip': public_ip,
                    'expires_at': expires_at,
                    'status': normalized_status,
                    'provider_status': provider_status,
                    'is_active': normalized_status in Server.ACTIVE_STATUSES,
                }
                if linked_order:
                    server_defaults['order'] = linked_order
                    if not server:
                        server_defaults['user'] = linked_order.user
                    server_defaults['expires_at'] = expires_at or linked_order.service_expires_at
                if asset:
                    server_defaults['user'] = asset.user
                    server_defaults['expires_at'] = expires_at or asset.actual_expires_at
                if server:
                    server_defaults['user'] = server.user
                    server_defaults['expires_at'] = expires_at or server.expires_at
                old_server_public_ip = server.public_ip if server else None
                if server:
                    if old_server_public_ip and old_server_public_ip != public_ip:
                        server.previous_public_ip = old_server_public_ip
                    for key, value in server_defaults.items():
                        setattr(server, key, value)
                    if not getattr(server, 'user_id', None) and getattr(asset, 'user_id', None):
                        server.user = asset.user
                    server.save()
                else:
                    Server.objects.create(**server_defaults)

                if linked_order:
                    order_updates = _aliyun_order_updates_from_sync(
                        linked_order,
                        normalized_status=normalized_status,
                        expires_at=expires_at,
                        account=account,
                        account_label=account_label,
                        region=region,
                        item=item,
                        asset_name=asset_name,
                        instance_id=instance_id,
                        public_ip=public_ip,
                    )
                    CloudServerOrder.objects.filter(pk=linked_order.pk).update(**order_updates)

                if ip_changed:
                    refreshed_server = _resolve_server(instance_id, public_ip, account, region)
                    record_cloud_ip_log(
                        event_type='changed',
                        asset=asset,
                        server=refreshed_server,
                        public_ip=public_ip,
                        previous_public_ip=old_public_ip,
                        note=f'自动同步发现 IP 变化：{old_public_ip} -> {public_ip}',
                    )
                synced_instance_ids.append(instance_id)
                account_instance_ids.append(instance_id)
                account_stats['ips'].append(f'{public_ip or "缺失"}:{asset_name or instance_id}')
                count += 1
                account_stats['count'] += 1
            if not target_scope_enabled:
                verification_deleted_items.extend(
                    _mark_deleted_when_missing_in_aliyun(
                        region=region,
                        existing_instance_ids=set(account_instance_ids),
                        stdout=self,
                        account=account,
                    )
                )
            account_summary_lines.append(
                f"账号={account_stats['label']}；地区={account_stats['region']}；扫描={account_stats['count']}；新增={account_stats['created']}；更新={account_stats['updated']}；缺IP删除={account_stats['deleted_by_missing_ip']}；IP={account_stats['ips'] or ['无']}"
            )
            if account:
                account.mark_status(
                    account.STATUS_OK,
                    f"阿里云同步完成，地区 {region}，扫描 {account_stats['count']} 台，新增 {account_stats['created']} 条，更新 {account_stats['updated']} 条。",
                )
            logger.info('ALIYUN_SYNC_ACCOUNT_DONE account_id=%s account_label=%s stats=%s', getattr(account, 'id', None), account_label, account_stats)
        after_asset_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
        after_visible_asset_total = _visible_asset_total()
        self.stdout.write(self.style.SUCCESS(
            f'阿里云同步汇总：资产总记录 {before_asset_total}->{after_asset_total} 条；当前可见代理 {before_visible_asset_total}->{after_visible_asset_total} 条；扫描服务器 {count} 条；覆盖 {len(accounts)} 个账号；新增 {created_count} 条，更新 {updated_count} 条；因公网IP缺失抬为已删除 {deleted_by_missing_ip_count} 条。'
        ))
        if account_summary_lines:
            self.stdout.write(f'阿里云按账号同步详情：{" || ".join(account_summary_lines)}')
        detail_parts = []
        if created_asset_ids:
            detail_parts.append(f'新增ID={created_asset_ids[:20]}')
        if status_changed_items:
            detail_parts.append(f'状态变更={status_changed_items[:20]}')
        if deleted_by_missing_ip_items:
            detail_parts.append(f'缺IP删状态={deleted_by_missing_ip_items[:20]}')
        if conflict_skipped_items:
            detail_parts.append(f'冲突跳过={conflict_skipped_items[:20]}')
        if verification_deleted_items:
            detail_parts.append(f'IP校验删除={verification_deleted_items[:20]}')
        if detail_parts:
            self.stdout.write(f'阿里云同步详情：{"；".join(detail_parts)}')
        self.synced_instance_ids = synced_instance_ids
        self.summary = {
            'count': count,
            'created': created_count,
            'updated': updated_count,
            'deleted_by_missing_ip': deleted_by_missing_ip_count,
            'region': region,
        }
        self.sync_errors = sync_errors
        logger.info('ALIYUN_SYNC_DONE summary=%s errors=%s detail_parts=%s', self.summary, sync_errors, detail_parts)
        return None
