from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.persistence import record_external_sync_log
from cloud.models import CloudAsset, CloudServerOrder, Server


NORMAL_AWS_STATES = {'running', 'pending', 'starting'}


def _aws_credentials():
    import os

    access_key = os.getenv('AWS_ACCESS_KEY_ID', '')
    secret_key = os.getenv('AWS_SECRET_ACCESS_KEY', '')
    if not access_key or not secret_key:
        raise CommandError('未配置 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY，无法同步 AWS。')
    return access_key, secret_key


def _lightsail_client(region):
    try:
        import boto3
    except Exception as exc:
        raise CommandError(f'未安装 boto3，无法同步 AWS：{exc}')

    access_key, secret_key = _aws_credentials()
    return boto3.client(
        'lightsail',
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _list_regions(region):
    normalized = str(region or '').strip().lower()
    if normalized and normalized != 'all':
        return [normalized]
    try:
        import boto3
    except Exception as exc:
        raise CommandError(f'未安装 boto3，无法同步 AWS：{exc}')
    access_key, secret_key = _aws_credentials()
    session = boto3.session.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    regions = sorted(session.get_available_regions('lightsail'))
    if not regions:
        raise CommandError('未发现可用 AWS Lightsail 地区。')
    return regions


def _parse_aws_account_id(arn):
    parts = str(arn or '').split(':')
    if len(parts) > 4 and parts[4]:
        return parts[4]
    return 'aws'


class Command(BaseCommand):
    help = '同步 AWS Lightsail 服务器到统一云资产表与 servers 表'

    def add_arguments(self, parser):
        parser.add_argument('--region', default='', help='AWS Lightsail 地域；留空则同步全部可用地区')

    def handle(self, *args, **options):
        regions = _list_regions(options['region'])
        count = 0
        synced_instance_ids = []
        synced_instance_ids_by_region = {}

        for region in regions:
            client = _lightsail_client(region)
            region_instance_ids = []
            next_page_token = None

            while True:
                kwargs = {}
                if next_page_token:
                    kwargs['pageToken'] = next_page_token
                response = client.get_instances(**kwargs)
                record_external_sync_log(
                    source='aws_lightsail',
                    action='get_instances',
                    target=region,
                    request_payload=kwargs,
                    response_payload={'count': len(response.get('instances') or []), 'nextPageToken': response.get('nextPageToken')},
                    is_success=True,
                )
                instances = response.get('instances') or []
                now_iso = timezone.now().isoformat()
                for item in instances:
                    instance_name = item.get('name') or ''
                    instance_arn = item.get('arn') or instance_name
                    account_id = _parse_aws_account_id(instance_arn)
                    state_name = ((item.get('state') or {}).get('name') or '').lower()
                    normalized_status = {
                        'running': CloudAsset.STATUS_RUNNING,
                        'pending': CloudAsset.STATUS_PENDING,
                        'starting': CloudAsset.STATUS_STARTING,
                        'stopping': CloudAsset.STATUS_STOPPING,
                        'stopped': CloudAsset.STATUS_STOPPED,
                        'shutting-down': CloudAsset.STATUS_TERMINATING,
                        'terminated': CloudAsset.STATUS_TERMINATED,
                        'terminating': CloudAsset.STATUS_TERMINATING,
                    }.get(state_name, CloudAsset.STATUS_UNKNOWN)
                    location = item.get('location') or {}
                    public_ip = item.get('publicIpAddress') or ''
                    bundle_id = item.get('bundleId') or '-'
                    blueprint_id = item.get('blueprintId') or '-'
                    order = CloudServerOrder.objects.filter(
                        provider='aws_lightsail',
                        instance_id=instance_name,
                    ).first() or CloudServerOrder.objects.filter(
                        provider='aws_lightsail',
                        provider_resource_id=instance_arn,
                    ).first() or CloudServerOrder.objects.filter(
                        provider='aws_lightsail',
                        server_name=instance_name,
                    ).first()
                    expires_at = order.service_expires_at if order else None
                    order_user = None
                    if order and order.user_id:
                        order_user = getattr(order, '_state', None) and None
                        try:
                            order_user = order.user
                        except Exception:
                            order_user = None

                    note = f"状态: {state_name or '-'}；套餐: {bundle_id}；镜像: {blueprint_id}；到期: {expires_at or '-'}；最近同步: {now_iso}"
                    is_active = normalized_status in Server.ACTIVE_STATUSES

                    asset, _ = CloudAsset.objects.update_or_create(
                        kind=CloudAsset.KIND_SERVER,
                        instance_id=instance_name,
                        defaults={
                            'source': CloudAsset.SOURCE_AWS_SYNC,
                            'provider': 'aws_lightsail',
                            'region_code': region,
                            'region_name': location.get('regionName') or region,
                            'asset_name': instance_name,
                            'provider_resource_id': instance_arn,
                            'public_ip': public_ip,
                            'actual_expires_at': expires_at,
                            'currency': 'USDT',
                            'order': order,
                            'user': order_user,
                            'note': note,
                            'status': normalized_status,
                            'provider_status': state_name or None,
                            'is_active': is_active,
                        },
                    )
                    Server.objects.update_or_create(
                        instance_id=instance_name,
                        defaults={
                            'source': Server.SOURCE_AWS_SYNC,
                            'provider': 'aws_lightsail',
                            'account_label': account_id,
                            'region_code': region,
                            'region_name': location.get('regionName') or region,
                            'server_name': instance_name,
                            'provider_resource_id': instance_arn,
                            'public_ip': public_ip,
                            'expires_at': expires_at,
                            'order': order,
                            'user': order_user,
                            'note': note,
                            'status': normalized_status,
                            'provider_status': state_name or None,
                            'is_active': is_active,
                        },
                    )
                    region_instance_ids.append(instance_name)
                    synced_instance_ids.append(instance_name)
                    count += 1
                    self.stdout.write(self.style.SUCCESS(f'已同步 AWS {region} {asset.instance_id or asset.asset_name}'))

                next_page_token = response.get('nextPageToken')
                if not next_page_token:
                    break

            synced_instance_ids_by_region[region] = region_instance_ids

        self.stdout.write(self.style.SUCCESS(f'完成，共同步 {count} 台 AWS 服务器，覆盖 {len(regions)} 个地区。'))
        self.synced_regions = regions
        self.synced_instance_ids = synced_instance_ids
        self.synced_instance_ids_by_region = synced_instance_ids_by_region
        return None
