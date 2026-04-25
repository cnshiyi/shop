from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from bot.api import _provider_status_label
from core.cloud_accounts import get_active_cloud_account
from core.persistence import record_external_sync_log
from cloud.models import CloudAsset, CloudServerOrder, Server
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


NORMAL_AWS_STATES = {'running', 'pending', 'starting'}


def _aws_credentials():
    import os

    account = get_active_cloud_account('aws')
    if account:
        ak = (account.access_key_plain or '').strip()
        sk = (account.secret_key_plain or '').strip()
        # AWS secret key 正常长度 40，access key 20；明显截断的不可用
        if ak and sk and len(ak) >= 16 and len(sk) >= 36:
            return ak, sk
    # 后台账号无效/截断/缺失，回退环境变量
    access_key = os.getenv('AWS_ACCESS_KEY_ID', '')
    secret_key = os.getenv('AWS_SECRET_ACCESS_KEY', '')
    if not access_key or not secret_key:
        raise CommandError('未配置 AWS 凭据（后台账号无效且环境变量也未设置），无法同步 AWS。')
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


def _status_label(status):
    return dict(CloudAsset.STATUS_CHOICES).get(status, status or '-')


def _elevate_deleted_when_ip_missing(status, public_ip):
    if str(public_ip or '').strip():
        return status
    return CloudAsset.STATUS_DELETED


def _resolve_asset(instance_name, instance_arn, public_ip, order):
    lookup = Q(kind=CloudAsset.KIND_SERVER)
    candidates = Q(instance_id=instance_name) | Q(provider_resource_id=instance_arn)
    if order:
        candidates |= Q(order=order)
    if public_ip:
        candidates |= Q(public_ip=public_ip)
    return CloudAsset.objects.filter(lookup & candidates).order_by('-updated_at', '-id').first()


def _resolve_server(instance_name, instance_arn, public_ip, order):
    candidates = Q(instance_id=instance_name) | Q(provider_resource_id=instance_arn)
    if order:
        candidates |= Q(order=order)
    if public_ip:
        candidates |= Q(public_ip=public_ip)
    return Server.objects.filter(candidates).order_by('-updated_at', '-id').first()


def _resolve_asset_for_static_ip(static_ip_name, static_ip_arn, public_ip):
    lookup = Q(kind=CloudAsset.KIND_SERVER)
    candidates = Q()
    if static_ip_arn:
        candidates |= Q(provider_resource_id=static_ip_arn)
    if public_ip:
        candidates |= Q(public_ip=public_ip)
    if static_ip_name:
        candidates |= Q(asset_name=static_ip_name)
    if not candidates:
        return None
    return CloudAsset.objects.filter(lookup & candidates).order_by('-updated_at', '-id').first()


def _mark_deleted_when_missing_in_aws(region, existing_instance_names, existing_static_ips, stdout):
    verification_deleted_items = []
    queryset = CloudAsset.objects.filter(
        kind=CloudAsset.KIND_SERVER,
        provider='aws_lightsail',
    ).filter(
        Q(region_code=region) | Q(region_code='') | Q(region_code__isnull=True)
    ).order_by('-updated_at', '-id')
    now_iso = timezone.now().isoformat()
    for asset in queryset:
        instance_name = str(asset.instance_id or '').strip()
        public_ip = str(asset.public_ip or '').strip()
        if instance_name and instance_name in existing_instance_names:
            continue
        if public_ip and public_ip in existing_static_ips:
            continue
        if asset.status == CloudAsset.STATUS_DELETED and asset.provider_status == '云上未找到实例/IP':
            continue
        old_public_ip = public_ip or str(asset.previous_public_ip or '').strip()
        asset.status = CloudAsset.STATUS_DELETED
        asset.is_active = False
        asset.previous_public_ip = old_public_ip or asset.previous_public_ip
        asset.public_ip = None
        asset.provider_status = '云上未找到实例/IP'
        asset.note = f'状态: 云上未找到实例/IP；公网IP: {old_public_ip or "缺失"}；最近同步: {now_iso}'
        asset.save(update_fields=['status', 'is_active', 'previous_public_ip', 'public_ip', 'provider_status', 'note', 'updated_at'])
        server = Server.objects.filter(
            Q(instance_id=instance_name) | Q(provider_resource_id=asset.provider_resource_id) | Q(public_ip=public_ip) | Q(previous_public_ip=old_public_ip)
        ).order_by('-updated_at', '-id').first()
        if server:
            server.status = Server.STATUS_DELETED
            server.is_active = False
            server.previous_public_ip = old_public_ip or server.previous_public_ip
            server.public_ip = None
            server.provider_status = '云上未找到实例/IP'
            server.note = f'状态: 云上未找到实例/IP；公网IP: {old_public_ip or "缺失"}；最近同步: {now_iso}'
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
        verification_deleted_items.append(f'{asset.id}:{old_public_ip or "缺失"}:{instance_name or asset.asset_name or "-"}')
        stdout.stdout.write(stdout.style.WARNING(
            f'IP校验 已删除 资产#{asset.id} IP={old_public_ip or "缺失"} 云上不存在'
        ))
    return verification_deleted_items


class Command(BaseCommand):
    help = '同步 AWS Lightsail 服务器到统一云资产表与 servers 表'

    def add_arguments(self, parser):
        parser.add_argument('--region', default='', help='AWS Lightsail 地域；留空则同步全部可用地区')

    def handle(self, *args, **options):
        regions = _list_regions(options['region'])
        before_asset_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
        count = 0
        created_count = 0
        updated_count = 0
        unattached_ip_count = 0
        deleted_by_missing_ip_count = 0
        created_asset_ids = []
        updated_asset_ids = []
        status_changed_items = []
        deleted_by_missing_ip_items = []
        unattached_ip_items = []
        conflict_skipped_items = []
        claimed_assets = {}
        synced_instance_ids = []
        synced_instance_ids_by_region = {}
        verification_deleted_items = []

        for region in regions:
            client = _lightsail_client(region)
            region_instance_ids = []
            next_page_token = None

            attached_instance_names = set()
            existing_static_ips = set()
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
                    attached_instance_names.add(instance_name)
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
                    normalized_status = _elevate_deleted_when_ip_missing(normalized_status, public_ip)
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

                    provider_status = _provider_status_label(state_name or None)
                    note = f"状态: {provider_status}；公网IP: {public_ip or '缺失'}；套餐: {bundle_id}；镜像: {blueprint_id}；到期: {expires_at or '-'}；最近同步: {now_iso}"
                    is_active = normalized_status in Server.ACTIVE_STATUSES

                    asset_defaults = {
                        'kind': CloudAsset.KIND_SERVER,
                        'source': CloudAsset.SOURCE_AWS_SYNC,
                        'provider': 'aws_lightsail',
                        'region_code': region,
                        'region_name': location.get('regionName') or region,
                        'asset_name': instance_name,
                        'instance_id': instance_name,
                        'provider_resource_id': instance_arn,
                        'public_ip': public_ip,
                        'actual_expires_at': expires_at,
                        'currency': 'USDT',
                        'order': order,
                        'user': order_user,
                        'note': note,
                        'status': normalized_status,
                        'provider_status': provider_status,
                        'is_active': is_active,
                    }
                    asset = _resolve_asset(instance_name, instance_arn, public_ip, order)
                    asset_signature = f'{instance_name or "-"}|{instance_arn or "-"}|{public_ip or "缺失"}'
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
                            asset.save()
                            updated_count += 1
                            updated_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{instance_name or asset.asset_name}')
                    else:
                        asset = CloudAsset.objects.create(**asset_defaults)
                        claimed_assets[asset.id] = asset_signature
                        created_count += 1
                        created_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{instance_name or asset.asset_name}')
                    if old_status is not None and old_status != normalized_status:
                        status_changed_items.append(f'{asset.id}:{public_ip or "缺失"}:{old_status}->{normalized_status}')
                    if normalized_status == CloudAsset.STATUS_DELETED and not str(public_ip or '').strip():
                        deleted_by_missing_ip_count += 1
                        deleted_by_missing_ip_items.append(f'{asset.id}:{public_ip or "缺失"}:{instance_name or instance_arn}')

                    server_defaults = {
                        'source': Server.SOURCE_AWS_SYNC,
                        'provider': 'aws_lightsail',
                        'account_label': account_id,
                        'region_code': region,
                        'region_name': location.get('regionName') or region,
                        'server_name': instance_name,
                        'instance_id': instance_name,
                        'provider_resource_id': instance_arn,
                        'public_ip': public_ip,
                        'expires_at': expires_at,
                        'order': order,
                        'user': order_user,
                        'note': note,
                        'status': normalized_status,
                        'provider_status': provider_status,
                        'is_active': is_active,
                    }
                    server = _resolve_server(instance_name, instance_arn, public_ip, order)
                    old_server_public_ip = server.public_ip if server else None
                    if server:
                        if old_server_public_ip and old_server_public_ip != public_ip:
                            server.previous_public_ip = old_server_public_ip
                        for key, value in server_defaults.items():
                            setattr(server, key, value)
                        server.save()
                    else:
                        server = Server.objects.create(**server_defaults)

                    if ip_changed:
                        record_cloud_ip_log(
                            event_type='changed',
                            order=order,
                            asset=asset,
                            server=server,
                            public_ip=public_ip,
                            previous_public_ip=old_public_ip,
                            note=f'自动同步发现 IP 变化：{old_public_ip} -> {public_ip}',
                        )
                    region_instance_ids.append(instance_name)
                    synced_instance_ids.append(instance_name)
                    count += 1
                    expires_text = timezone.localtime(expires_at).strftime('%Y-%m-%d %H:%M') if expires_at else '-'
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'已同步 AWS {region} IP={public_ip or "缺失"} 实例={instance_name or "-"} 名称={asset.asset_name or "-"} '
                            f'状态={_status_label(normalized_status)} '
                            f'厂商状态={provider_status} '
                            f'到期={expires_text}'
                        )
                    )

                next_page_token = response.get('nextPageToken')
                if not next_page_token:
                    break

            static_ip_next_page_token = None
            while True:
                static_ip_kwargs = {}
                if static_ip_next_page_token:
                    static_ip_kwargs['pageToken'] = static_ip_next_page_token
                static_ip_response = client.get_static_ips(**static_ip_kwargs)
                static_ips = static_ip_response.get('staticIps') or []
                for item in static_ips:
                    attached_to = item.get('attachedTo') or ''
                    public_ip = item.get('ipAddress') or ''
                    if public_ip:
                        existing_static_ips.add(public_ip)
                    if attached_to and attached_to in attached_instance_names:
                        continue
                    static_ip_name = item.get('name') or public_ip or 'aws-static-ip'
                    static_ip_arn = item.get('arn') or static_ip_name
                    location = item.get('location') or {}
                    provider_status = '未附加固定IP'
                    note = f"状态: {provider_status}；公网IP: {public_ip or '缺失'}；固定IP名: {static_ip_name}；最近同步: {timezone.now().isoformat()}"
                    asset_defaults = {
                        'kind': CloudAsset.KIND_SERVER,
                        'source': CloudAsset.SOURCE_AWS_SYNC,
                        'provider': 'aws_lightsail',
                        'region_code': region,
                        'region_name': location.get('regionName') or region,
                        'asset_name': static_ip_name,
                        'instance_id': None,
                        'provider_resource_id': static_ip_arn,
                        'public_ip': public_ip,
                        'note': note,
                        'status': CloudAsset.STATUS_UNKNOWN,
                        'provider_status': provider_status,
                        'is_active': False,
                    }
                    asset = _resolve_asset_for_static_ip(static_ip_name, static_ip_arn, public_ip)
                    asset_signature = f'{static_ip_name or "-"}|{static_ip_arn or "-"}|{public_ip or "缺失"}'
                    old_status = asset.status if asset else None
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
                            for key, value in asset_defaults.items():
                                setattr(asset, key, value)
                            asset.save()
                            updated_count += 1
                            updated_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{static_ip_name or static_ip_arn}')
                    else:
                        asset = CloudAsset.objects.create(**asset_defaults)
                        claimed_assets[asset.id] = asset_signature
                        created_count += 1
                        created_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{static_ip_name or static_ip_arn}')
                    if old_status is not None and old_status != CloudAsset.STATUS_UNKNOWN:
                        status_changed_items.append(f'{asset.id}:{public_ip or "缺失"}:{old_status}->{CloudAsset.STATUS_UNKNOWN}')
                    unattached_ip_count += 1
                    unattached_ip_items.append(f'{asset.id}:{public_ip or "缺失"}:{static_ip_name or static_ip_arn}')
                    count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'已同步 AWS {region} 未附加IP={asset.public_ip or "缺失"} 名称={static_ip_name or "-"} 状态={_status_label(asset.status)} 厂商状态={provider_status}'
                        )
                    )
                static_ip_next_page_token = static_ip_response.get('nextPageToken')
                if not static_ip_next_page_token:
                    break

            verification_deleted_items.extend(
                _mark_deleted_when_missing_in_aws(
                    region=region,
                    existing_instance_names=set(region_instance_ids),
                    existing_static_ips=existing_static_ips,
                    stdout=self,
                )
            )
            synced_instance_ids_by_region[region] = region_instance_ids

        after_asset_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
        self.stdout.write(self.style.SUCCESS(
            f'AWS 同步汇总：代理列表原有 {before_asset_total} 条；扫描实例/未附加IP 共 {count} 条；新增 {created_count} 条，更新 {updated_count} 条；其中未附加IP {unattached_ip_count} 条，因公网IP缺失抬为已删除 {deleted_by_missing_ip_count} 条；同步后代理列表共 {after_asset_total} 条；覆盖 {len(regions)} 个地区。'
        ))
        self.stdout.write(
            f'AWS 同步详情：新增ID={created_asset_ids[:20] or []}；更新ID={updated_asset_ids[:20] or []}；状态变更={status_changed_items[:20] or []}；缺IP删状态={deleted_by_missing_ip_items[:20] or []}；未附加IP={unattached_ip_items[:20] or []}；冲突跳过={conflict_skipped_items[:20] or []}；IP校验删除={verification_deleted_items[:20] or []}'
        )
        self.synced_regions = regions
        self.synced_instance_ids = synced_instance_ids
        self.synced_instance_ids_by_region = synced_instance_ids_by_region
        return None
