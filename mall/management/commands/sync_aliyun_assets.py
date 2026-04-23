from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from mall.models import CloudAsset, Server
from cloud.aliyun_simple import _build_client, _region_endpoint, _runtime_options


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
    import os

    for key in ('OwnerId', 'AccountId', 'ResourceOwnerId', 'UserId'):
        value = item.get(key)
        if value:
            return str(value)
    return os.getenv('ALIBABA_CLOUD_ACCOUNT_ID', '') or os.getenv('ALIYUN_ACCOUNT_ID', '') or 'aliyun'


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


class Command(BaseCommand):
    help = '同步阿里云轻量应用服务器到统一云资产表'

    def add_arguments(self, parser):
        parser.add_argument('--region', default='cn-hongkong', help='阿里云地域代码，默认 cn-hongkong')

    def handle(self, *args, **options):
        region = options['region']
        client = _build_client(_region_endpoint(region))
        if not client:
            raise CommandError('未配置阿里云 AccessKey，无法同步。')

        from alibabacloud_swas_open20200601 import models as swas_models

        response = client.list_instances_with_options(
            swas_models.ListInstancesRequest(region_id=region, page_size=100),
            _runtime_options(),
        )
        instances = response.body.to_map().get('Instances', [])
        count = 0
        synced_instance_ids = []
        for item in instances:
            instance_id = item.get('InstanceId') or ''
            public_ip = item.get('PublicIpAddress') or item.get('PublicIp') or ''
            expires_at = _parse_expire_time(item)
            account_id = _resolve_account_id(item)
            normalized_status, raw_status, business_status, disable_reason = _resolve_aliyun_status(item, expires_at)
            provider_status = ' / '.join([part for part in [raw_status or None, business_status or None, disable_reason or None] if part]) or None
            asset, _ = CloudAsset.objects.update_or_create(
                kind=CloudAsset.KIND_SERVER,
                instance_id=instance_id,
                defaults={
                    'source': CloudAsset.SOURCE_ALIYUN,
                    'provider': 'aliyun_simple',
                    'region_code': region,
                    'region_name': item.get('RegionId') or region,
                    'asset_name': item.get('InstanceName') or instance_id,
                    'provider_resource_id': instance_id,
                    'public_ip': public_ip,
                    'actual_expires_at': expires_at,
                    'currency': 'USDT',
                    'note': f"状态: {item.get('Status') or '-'}；业务状态: {item.get('BusinessStatus') or '-'}；禁用原因: {item.get('DisableReason') or '-'}；套餐: {item.get('PlanId') or '-'}；到期: {expires_at or '-'}；最近同步: {timezone.now().isoformat()}",
                    'status': normalized_status,
                    'provider_status': provider_status,
                    'is_active': normalized_status in CloudAsset.ACTIVE_STATUSES,
                },
            )
            Server.objects.update_or_create(
                instance_id=instance_id,
                defaults={
                    'source': Server.SOURCE_ALIYUN,
                    'provider': 'aliyun_simple',
                    'account_label': account_id,
                    'region_code': region,
                    'region_name': item.get('RegionId') or region,
                    'server_name': item.get('InstanceName') or instance_id,
                    'provider_resource_id': instance_id,
                    'public_ip': public_ip,
                    'expires_at': expires_at,
                    'note': f"状态: {item.get('Status') or '-'}；业务状态: {item.get('BusinessStatus') or '-'}；禁用原因: {item.get('DisableReason') or '-'}；套餐: {item.get('PlanId') or '-'}；到期: {expires_at or '-'}；最近同步: {timezone.now().isoformat()}",
                    'status': normalized_status,
                    'provider_status': provider_status,
                    'is_active': normalized_status in Server.ACTIVE_STATUSES,
                },
            )
            synced_instance_ids.append(instance_id)
            count += 1
            self.stdout.write(self.style.SUCCESS(f'已同步 {asset.instance_id or asset.asset_name}'))
        self.stdout.write(self.style.SUCCESS(f'完成，共同步 {count} 台阿里云服务器。'))
        self.synced_instance_ids = synced_instance_ids
        return None
