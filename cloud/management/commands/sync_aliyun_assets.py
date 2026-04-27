from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from bot.api import _provider_status_label
from cloud.models import CloudAsset, CloudIpLog, CloudServerOrder, Server
from cloud.aliyun_simple import _build_client, _region_endpoint, _runtime_options
from core.cloud_accounts import cloud_account_label, list_active_cloud_accounts
from cloud.services import record_cloud_ip_log


_ACTIVE_ORDER_STATUSES = {'pending', 'provisioning', 'completed', 'expiring', 'renew_pending', 'suspended'}


def _resolve_order_for_ip(public_ip):
    normalized_ip = str(public_ip or '').strip()
    if not normalized_ip:
        return None
    return CloudServerOrder.objects.filter(
        Q(public_ip=normalized_ip) | Q(previous_public_ip=normalized_ip),
        status__in=_ACTIVE_ORDER_STATUSES,
    ).order_by('-created_at', '-id').first()


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
    now = timezone.now()

    if expires_at and now >= expires_at and now < expires_at + timezone.timedelta(days=15):
        return CloudAsset.STATUS_EXPIRED_GRACE, raw_status, business_status, disable_reason
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
    if raw_status == 'disabled' and (business_status == 'expired' or disable_reason == 'expired'):
        return CloudAsset.STATUS_EXPIRED, raw_status, business_status, disable_reason
    if raw_status == 'deleting':
        return CloudAsset.STATUS_DELETING, raw_status, business_status, disable_reason
    if raw_status == 'deleted':
        return CloudAsset.STATUS_DELETED, raw_status, business_status, disable_reason
    return CloudAsset.STATUS_UNKNOWN, raw_status, business_status, disable_reason


def _status_label(status):
    return dict(CloudAsset.STATUS_CHOICES).get(status, status or '-')


def _elevate_deleted_when_ip_missing(status, public_ip):
    if str(public_ip or '').strip():
        return status
    return CloudAsset.STATUS_DELETED


def _resolve_asset(instance_id, public_ip, account=None):
    lookup = Q(kind=CloudAsset.KIND_SERVER)
    if account:
        lookup &= Q(cloud_account=account)
    candidates = Q(instance_id=instance_id) | Q(provider_resource_id=instance_id)
    if public_ip:
        candidates |= Q(public_ip=public_ip)
    return CloudAsset.objects.filter(lookup & candidates).order_by('-updated_at', '-id').first()


def _resolve_server(instance_id, public_ip, account=None):
    candidates = Q(instance_id=instance_id) | Q(provider_resource_id=instance_id)
    base = Q()
    if account:
        base &= Q(account_label=cloud_account_label(account))
    if public_ip:
        candidates |= Q(public_ip=public_ip)
    return Server.objects.filter(base & candidates).order_by('-updated_at', '-id').first()


def _has_local_delete_tombstone(instance_id, public_ip, region):
    lookup = Q(event_type=CloudIpLog.EVENT_DELETED, provider='aliyun_simple')
    if region:
        lookup &= Q(region_code=region)
    identifiers = Q()
    if instance_id:
        identifiers |= Q(instance_id=instance_id) | Q(provider_resource_id=instance_id)
    if public_ip:
        identifiers |= Q(public_ip=public_ip) | Q(previous_public_ip=public_ip)
    if not identifiers:
        return False
    return CloudIpLog.objects.filter(lookup & identifiers).exists()


def _mark_deleted_when_missing_in_aliyun(region, existing_instance_ids, stdout, account=None):
    verification_deleted_items = []
    queryset = CloudAsset.objects.filter(
        kind=CloudAsset.KIND_SERVER,
        provider='aliyun_simple',
    )
    if account:
        queryset = queryset.filter(cloud_account=account)
    queryset = queryset.filter(
        Q(region_code=region) | Q(region_code='') | Q(region_code__isnull=True)
    ).order_by('-updated_at', '-id')
    now_iso = timezone.now().isoformat()
    for asset in queryset:
        instance_id = str(asset.instance_id or '').strip()
        public_ip = str(asset.public_ip or '').strip()
        if instance_id and instance_id in existing_instance_ids:
            continue
        if asset.status == CloudAsset.STATUS_DELETED and asset.provider_status == '云上未找到实例':
            continue
        old_public_ip = public_ip or str(asset.previous_public_ip or '').strip()
        asset.status = CloudAsset.STATUS_DELETED
        asset.is_active = False
        asset.previous_public_ip = old_public_ip or asset.previous_public_ip
        asset.public_ip = None
        asset.provider_status = '云上未找到实例'
        asset.note = f'状态: 云上未找到实例；公网IP: {old_public_ip or "缺失"}；最近同步: {now_iso}'
        asset.save(update_fields=['status', 'is_active', 'previous_public_ip', 'public_ip', 'provider_status', 'note', 'updated_at'])
        server = Server.objects.filter(
            Q(instance_id=instance_id) | Q(provider_resource_id=asset.provider_resource_id) | Q(public_ip=public_ip) | Q(previous_public_ip=old_public_ip)
        ).order_by('-updated_at', '-id').first()
        if server:
            server.status = Server.STATUS_DELETED
            server.is_active = False
            server.previous_public_ip = old_public_ip or server.previous_public_ip
            server.public_ip = None
            server.provider_status = '云上未找到实例'
            server.note = f'状态: 云上未找到实例；公网IP: {old_public_ip or "缺失"}；最近同步: {now_iso}'
            server.save(update_fields=['status', 'is_active', 'previous_public_ip', 'public_ip', 'provider_status', 'note', 'updated_at'])
        order = getattr(asset, 'order', None) or _resolve_order_for_ip(old_public_ip)
        if order:
            order.previous_public_ip = old_public_ip or order.previous_public_ip
            order.public_ip = None
            order.save(update_fields=['previous_public_ip', 'public_ip', 'updated_at'])
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

    def handle(self, *args, **options):
        region = options['region']
        accounts = list_active_cloud_accounts('aliyun', region) or [None]
        before_asset_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()

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
        deleted_preserved_items = []
        claimed_assets = {}
        synced_instance_ids = []
        verification_deleted_items = []
        for account in accounts:
            account_label = cloud_account_label(account) or 'aliyun'
            client = _build_client(_region_endpoint(region), account=account)
            if not client:
                self.stdout.write(self.style.WARNING(f'跳过阿里云账号 {account_label}：无法创建客户端'))
                continue
            response = client.list_instances_with_options(
                swas_models.ListInstancesRequest(region_id=region, page_size=100),
                _runtime_options(),
            )
            instances = response.body.to_map().get('Instances', [])
            for item in instances:
                instance_id = item.get('InstanceId') or ''
                public_ip = item.get('PublicIpAddress') or item.get('PublicIp') or ''
                expires_at = CloudServerOrder.normalize_expiry_time(_parse_expire_time(item))
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
                    'note': note,
                    'status': normalized_status,
                    'provider_status': provider_status,
                    'is_active': normalized_status in CloudAsset.ACTIVE_STATUSES,
                }
                asset = _resolve_asset(instance_id, public_ip, account)
                asset_signature = f'{instance_id or "-"}|{public_ip or "缺失"}'
                old_status = asset.status if asset else None
                old_public_ip = asset.public_ip if asset else None
                ip_changed = bool(asset and old_public_ip and old_public_ip != public_ip)
                if _has_local_delete_tombstone(instance_id, public_ip, region) and (not asset or not asset.order_id):
                    tombstone_target = asset.id if asset else '缺失'
                    if asset:
                        asset.delete()
                    deleted_preserved_items.append(f'墓碑:{tombstone_target}:{public_ip or "缺失"}:{instance_id or asset_name}')
                    synced_instance_ids.append(instance_id)
                    count += 1
                    continue
                if asset and asset.status == CloudAsset.STATUS_DELETED:
                    claimed_assets[asset.id] = asset_signature
                    server = _resolve_server(instance_id, public_ip, account)
                    if server and server.status != Server.STATUS_DELETED:
                        server.status = Server.STATUS_DELETED
                        server.is_active = False
                        server.provider_status = server.provider_status or '本地已删除'
                        server.note = server.note or '本地已删除，跳过云同步复活。'
                        server.save(update_fields=['status', 'is_active', 'provider_status', 'note', 'updated_at'])
                    deleted_preserved_items.append(f'{asset.id}:{public_ip or "缺失"}:{instance_id or asset_name}')
                    synced_instance_ids.append(instance_id)
                    count += 1
                    continue
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
                        asset.save()
                        updated_count += 1
                        updated_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{instance_id or asset_name}')
                else:
                    asset = CloudAsset.objects.create(**asset_defaults)
                    claimed_assets[asset.id] = asset_signature
                    created_count += 1
                    created_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{instance_id or asset_name}')
                if old_status is not None and old_status != normalized_status:
                    status_changed_items.append(f'{asset.id}:{public_ip or "缺失"}:{old_status}->{normalized_status}')
                if normalized_status == CloudAsset.STATUS_DELETED and not str(public_ip or '').strip():
                    deleted_by_missing_ip_count += 1
                    deleted_by_missing_ip_items.append(f'{asset.id}:{public_ip or "缺失"}:{asset_name or instance_id}')

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
                    'note': note,
                    'status': normalized_status,
                    'provider_status': provider_status,
                    'is_active': normalized_status in Server.ACTIVE_STATUSES,
                }
                server = _resolve_server(instance_id, public_ip, account)
                old_server_public_ip = server.public_ip if server else None
                if server:
                    if old_server_public_ip and old_server_public_ip != public_ip:
                        server.previous_public_ip = old_server_public_ip
                    for key, value in server_defaults.items():
                        setattr(server, key, value)
                    server.save()
                else:
                    Server.objects.create(**server_defaults)

                linked_order = getattr(asset, 'order', None) or _resolve_order_for_ip(public_ip)
                if linked_order and expires_at and linked_order.service_expires_at != expires_at:
                    linked_order.service_expires_at = expires_at
                    linked_order.save(update_fields=['service_expires_at', 'renew_grace_expires_at', 'suspend_at', 'delete_at', 'ip_recycle_at', 'updated_at'])

                if ip_changed:
                    refreshed_server = _resolve_server(instance_id, public_ip, account)
                    record_cloud_ip_log(
                        event_type='changed',
                        asset=asset,
                        server=refreshed_server,
                        public_ip=public_ip,
                        previous_public_ip=old_public_ip,
                        note=f'自动同步发现 IP 变化：{old_public_ip} -> {public_ip}',
                    )
                synced_instance_ids.append(instance_id)
                count += 1
            verification_deleted_items.extend(
                _mark_deleted_when_missing_in_aliyun(
                    region=region,
                    existing_instance_ids=set(synced_instance_ids),
                    stdout=self,
                    account=account,
                )
            )
        after_asset_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
        self.stdout.write(self.style.SUCCESS(
            f'阿里云同步汇总：代理列表原有 {before_asset_total} 条；扫描服务器 {count} 条；覆盖 {len(accounts)} 个账号；新增 {created_count} 条，更新 {updated_count} 条；因公网IP缺失抬为已删除 {deleted_by_missing_ip_count} 条；同步后代理列表共 {after_asset_total} 条。'
        ))
        detail_parts = []
        if created_asset_ids:
            detail_parts.append(f'新增ID={created_asset_ids[:20]}')
        if status_changed_items:
            detail_parts.append(f'状态变更={status_changed_items[:20]}')
        if deleted_by_missing_ip_items:
            detail_parts.append(f'缺IP删状态={deleted_by_missing_ip_items[:20]}')
        if conflict_skipped_items:
            detail_parts.append(f'冲突跳过={conflict_skipped_items[:20]}')
        if deleted_preserved_items:
            detail_parts.append(f'保留本地删除={deleted_preserved_items[:20]}')
        if verification_deleted_items:
            detail_parts.append(f'IP校验删除={verification_deleted_items[:20]}')
        if detail_parts:
            self.stdout.write(f'阿里云同步详情：{"；".join(detail_parts)}')
        self.synced_instance_ids = synced_instance_ids
        return None
