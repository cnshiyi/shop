from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from bot.api import _provider_status_label
from core.cloud_accounts import cloud_account_label, get_active_cloud_account, list_active_cloud_accounts
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


def _aws_credentials(account=None):
    import os

    account = account or get_active_cloud_account('aws')
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


def _lightsail_client(region, account=None):
    try:
        import boto3
    except Exception as exc:
        raise CommandError(f'未安装 boto3，无法同步 AWS：{exc}')

    access_key, secret_key = _aws_credentials(account)
    return boto3.client(
        'lightsail',
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _release_static_ip_if_due(client, region, asset, static_ip_name, static_ip_arn, public_ip, stdout):
    due_at = asset.actual_expires_at
    if not due_at or due_at > timezone.now() or asset.status == CloudAsset.STATUS_DELETED:
        return False
    release_name = static_ip_name or asset.asset_name or ''
    if not release_name:
        return False
    try:
        response = client.release_static_ip(staticIpName=release_name)
        record_external_sync_log(
            source='aws_lightsail',
            action='release_static_ip',
            target=f'{region}:{release_name}',
            request_payload={'staticIpName': release_name, 'asset_id': asset.id, 'public_ip': public_ip},
            response_payload=response,
            is_success=True,
        )
        asset.status = CloudAsset.STATUS_DELETED
        asset.provider_status = '未附加固定IP-已到期删除'
        asset.is_active = False
        asset.note = (
            f"状态: 未附加固定IP；公网IP: {public_ip or '缺失'}；固定IP名: {release_name}；"
            f"已到15天回收时间，系统已调用 AWS API 真实删除；删除时间: {timezone.now().isoformat()}"
        )
        asset.save(update_fields=['status', 'provider_status', 'is_active', 'note', 'updated_at'])
        stdout.write(stdout.style.WARNING(
            f'已删除 AWS {region} 未附加IP={public_ip or "缺失"} 名称={release_name} 资产#{asset.id}'
        ))
        return True
    except Exception as exc:
        record_external_sync_log(
            source='aws_lightsail',
            action='release_static_ip',
            target=f'{region}:{release_name}',
            request_payload={'staticIpName': release_name, 'asset_id': asset.id, 'public_ip': public_ip},
            response_payload={'error': str(exc)},
            is_success=False,
            error_message=str(exc),
        )
        asset.note = (
            f"状态: 未附加固定IP；公网IP: {public_ip or '缺失'}；固定IP名: {release_name}；"
            f"已到15天回收时间，但 AWS API 删除失败: {exc}；最近同步: {timezone.now().isoformat()}"
        )
        asset.save(update_fields=['note', 'updated_at'])
        stdout.write(stdout.style.ERROR(
            f'删除 AWS 未附加IP 失败 资产#{asset.id} IP={public_ip or "缺失"} 名称={release_name} err={exc}'
        ))
        return False


def _list_regions(region, account=None):
    normalized = str(region or '').strip().lower()
    if normalized and normalized != 'all':
        return [normalized]
    try:
        import boto3
    except Exception as exc:
        raise CommandError(f'未安装 boto3，无法同步 AWS：{exc}')
    access_key, secret_key = _aws_credentials(account)
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


def _resolve_asset(instance_name, instance_arn, public_ip, order, account=None):
    lookup = Q(kind=CloudAsset.KIND_SERVER)
    if account:
        lookup &= Q(cloud_account=account)
    candidates = Q(instance_id=instance_name) | Q(provider_resource_id=instance_arn)
    if order:
        candidates |= Q(order=order)
    if public_ip:
        candidates |= Q(public_ip=public_ip)
    return CloudAsset.objects.filter(lookup & candidates).order_by('-updated_at', '-id').first()


def _resolve_server(instance_name, instance_arn, public_ip, order, account=None):
    candidates = Q(instance_id=instance_name) | Q(provider_resource_id=instance_arn)
    base = Q()
    if account:
        base &= Q(account_label=cloud_account_label(account))
    if order:
        candidates |= Q(order=order)
    if public_ip:
        candidates |= Q(public_ip=public_ip)
    return Server.objects.filter(base & candidates).order_by('-updated_at', '-id').first()


def _resolve_asset_for_static_ip(static_ip_name, static_ip_arn, public_ip, account=None):
    lookup = Q(kind=CloudAsset.KIND_SERVER)
    if account:
        lookup &= Q(cloud_account=account)

    exact_candidates = Q()
    if static_ip_arn:
        exact_candidates |= Q(provider_resource_id=static_ip_arn)
    if static_ip_name:
        exact_candidates |= Q(asset_name=static_ip_name, instance_id__isnull=True)
    if exact_candidates:
        asset = CloudAsset.objects.filter(lookup & exact_candidates).order_by('-updated_at', '-id').first()
        if asset:
            return asset

    if public_ip:
        return (
            CloudAsset.objects.filter(lookup & Q(public_ip=public_ip))
            .filter(Q(instance_id__isnull=True) | Q(instance_id='') | Q(provider_status='未附加固定IP') | Q(provider_resource_id__contains='StaticIp'))
            .order_by('-updated_at', '-id')
            .first()
        )
    return None


def _mark_deleted_when_missing_in_aws(region, existing_instance_names, existing_static_ips, stdout, account=None):
    verification_deleted_items = []
    queryset = CloudAsset.objects.filter(
        kind=CloudAsset.KIND_SERVER,
        provider='aws_lightsail',
    )
    if account:
        queryset = queryset.filter(cloud_account=account)
    queryset = queryset.filter(
        Q(region_code=region) | Q(region_code='') | Q(region_code__isnull=True)
    ).order_by('-updated_at', '-id')
    now_iso = timezone.now().isoformat()
    for asset in queryset:
        instance_name = str(asset.instance_id or '').strip()
        public_ip = str(asset.public_ip or '').strip()
        is_static_ip_asset = (
            not instance_name
            or asset.provider_status == '未附加固定IP'
            or 'StaticIp' in str(asset.provider_resource_id or '')
        )
        if instance_name and instance_name in existing_instance_names:
            continue
        if is_static_ip_asset and public_ip and public_ip in existing_static_ips:
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
    server_queryset = Server.objects.filter(provider='aws_lightsail')
    if account:
        server_queryset = server_queryset.filter(account_label=cloud_account_label(account))
    server_queryset = server_queryset.filter(
        Q(region_code=region) | Q(region_code='') | Q(region_code__isnull=True)
    ).order_by('-updated_at', '-id')
    for server in server_queryset:
        instance_name = str(server.instance_id or '').strip()
        public_ip = str(server.public_ip or '').strip()
        is_static_ip_record = not instance_name or server.provider_status == '未附加固定IP' or 'StaticIp' in str(server.provider_resource_id or '')
        if instance_name and instance_name in existing_instance_names:
            continue
        if is_static_ip_record and public_ip and public_ip in existing_static_ips:
            continue
        if server.status == Server.STATUS_DELETED and server.provider_status == '云上未找到实例/IP':
            continue
        old_public_ip = public_ip or str(server.previous_public_ip or '').strip()
        server.status = Server.STATUS_DELETED
        server.is_active = False
        server.previous_public_ip = old_public_ip or server.previous_public_ip
        server.public_ip = None
        server.provider_status = '云上未找到实例/IP'
        server.note = f'状态: 云上未找到实例/IP；公网IP: {old_public_ip or "缺失"}；最近同步: {now_iso}'
        server.save(update_fields=['status', 'is_active', 'previous_public_ip', 'public_ip', 'provider_status', 'note', 'updated_at'])
        order = getattr(server, 'order', None) or _resolve_order_for_ip(old_public_ip)
        if order:
            order.previous_public_ip = old_public_ip or order.previous_public_ip
            order.public_ip = None
            order.save(update_fields=['previous_public_ip', 'public_ip', 'updated_at'])
        record_cloud_ip_log(
            event_type='deleted',
            order=order,
            asset=None,
            server=server,
            previous_public_ip=old_public_ip or None,
            public_ip=None,
            note='服务器校验发现云上不存在，已标记删除',
        )
        verification_deleted_items.append(f'server#{server.id}:{old_public_ip or "缺失"}:{instance_name or server.server_name or "-"}')
        stdout.stdout.write(stdout.style.WARNING(
            f'服务器校验 已删除 Server#{server.id} IP={old_public_ip or "缺失"} 云上不存在'
        ))
    return verification_deleted_items


class Command(BaseCommand):
    help = '同步 AWS Lightsail 服务器到统一云资产表与 servers 表'

    def add_arguments(self, parser):
        parser.add_argument('--region', default='', help='AWS Lightsail 地域；留空则同步全部可用地区')

    def handle(self, *args, **options):
        accounts = list_active_cloud_accounts('aws') or [None]
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
        manual_expiry_preserved_items = []
        claimed_assets = {}
        synced_instance_ids = []
        synced_instance_ids_by_region = {}
        verification_deleted_items = []

        synced_regions = []
        for account in accounts:
            regions = _list_regions(options['region'], account)
            account_label = cloud_account_label(account) or 'aws'
            synced_regions.extend(regions)
            for region in regions:
                client = _lightsail_client(region, account)
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
                            'cloud_account': account,
                            'account_label': account_label,
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
                        asset = _resolve_asset(instance_name, instance_arn, public_ip, order, account)
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
                                original_due_at = asset.actual_expires_at
                                for key, value in asset_defaults.items():
                                    setattr(asset, key, value)
                                if not order and original_due_at and expires_at is None:
                                    asset.actual_expires_at = original_due_at
                                    manual_expiry_preserved_items.append(f'{asset.id}:{public_ip or "缺失"}:{instance_name or instance_arn}:{original_due_at}')
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
                            'account_label': account_label or account_id,
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
                        server = _resolve_server(instance_name, instance_arn, public_ip, order, account)
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
                        discovered_at = timezone.now()
                        recycle_due_at = discovered_at + timezone.timedelta(days=15)
                        note = (
                            f"状态: {provider_status}；公网IP: {public_ip or '缺失'}；固定IP名: {static_ip_name}；"
                            f"发现时间: {discovered_at.isoformat()}；计划删除时间: {recycle_due_at.isoformat()}；最近同步: {discovered_at.isoformat()}"
                        )
                        asset_defaults = {
                            'kind': CloudAsset.KIND_SERVER,
                            'source': CloudAsset.SOURCE_AWS_SYNC,
                            'provider': 'aws_lightsail',
                            'cloud_account': account,
                            'account_label': account_label,
                            'region_code': region,
                            'region_name': location.get('regionName') or region,
                            'asset_name': static_ip_name,
                            'instance_id': None,
                            'provider_resource_id': static_ip_arn,
                            'public_ip': public_ip,
                            'actual_expires_at': recycle_due_at,
                            'note': note,
                            'status': CloudAsset.STATUS_UNKNOWN,
                            'provider_status': provider_status,
                            'is_active': False,
                        }
                        asset = _resolve_asset_for_static_ip(static_ip_name, static_ip_arn, public_ip, account)
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
                                original_due_at = asset.actual_expires_at
                                for key, value in asset_defaults.items():
                                    setattr(asset, key, value)
                                if original_due_at:
                                    asset.actual_expires_at = original_due_at
                                if asset.actual_expires_at and asset.actual_expires_at <= timezone.now():
                                    if _release_static_ip_if_due(client, region, asset, static_ip_name, static_ip_arn, public_ip, self.stdout):
                                        updated_count += 1
                                        updated_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{static_ip_name or static_ip_arn}:released')
                                        continue
                                    asset.status = CloudAsset.STATUS_UNKNOWN
                                asset.save()
                                updated_count += 1
                                updated_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{static_ip_name or static_ip_arn}')
                        else:
                            asset = CloudAsset.objects.create(**asset_defaults)
                            claimed_assets[asset.id] = asset_signature
                            created_count += 1
                            created_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{static_ip_name or static_ip_arn}')
                        if public_ip:
                            duplicate_static_assets = CloudAsset.objects.filter(
                                kind=CloudAsset.KIND_SERVER,
                                provider='aws_lightsail',
                                public_ip=public_ip,
                            ).exclude(id=asset.id).filter(
                                Q(instance_id__isnull=True) | Q(instance_id='') | Q(provider_status='未附加固定IP') | Q(provider_resource_id__contains='StaticIp')
                            )
                            for duplicate in duplicate_static_assets:
                                duplicate.status = CloudAsset.STATUS_DELETED
                                duplicate.is_active = False
                                duplicate.previous_public_ip = duplicate.previous_public_ip or duplicate.public_ip
                                duplicate.public_ip = None
                                duplicate.provider_status = '重复未附加固定IP记录'
                                duplicate.note = f'状态: 重复未附加固定IP记录；原公网IP: {public_ip}；保留资产#{asset.id}；最近同步: {timezone.now().isoformat()}'
                                duplicate.save(update_fields=['status', 'is_active', 'previous_public_ip', 'public_ip', 'provider_status', 'note', 'updated_at'])
                                status_changed_items.append(f'{duplicate.id}:{public_ip}:duplicate_static_ip_deleted')
                        if old_status is not None and old_status != CloudAsset.STATUS_UNKNOWN:
                            status_changed_items.append(f'{asset.id}:{public_ip or "缺失"}:{old_status}->{CloudAsset.STATUS_UNKNOWN}')
                        unattached_ip_count += 1
                        unattached_ip_items.append(f'{asset.id}:{public_ip or "缺失"}:{static_ip_name or static_ip_arn}')
                        count += 1
                    static_ip_next_page_token = static_ip_response.get('nextPageToken')
                    if not static_ip_next_page_token:
                        break

                verification_deleted_items.extend(
                    _mark_deleted_when_missing_in_aws(
                        region=region,
                        existing_instance_names=set(region_instance_ids),
                        existing_static_ips=existing_static_ips,
                        stdout=self,
                        account=account,
                    )
                )
                synced_instance_ids_by_region[region] = region_instance_ids

        after_asset_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
        self.stdout.write(self.style.SUCCESS(
            f'AWS 同步汇总：代理列表原有 {before_asset_total} 条；扫描实例/未附加IP 共 {count} 条；新增 {created_count} 条，更新 {updated_count} 条；其中未附加IP {unattached_ip_count} 条，因公网IP缺失抬为已删除 {deleted_by_missing_ip_count} 条；同步后代理列表共 {after_asset_total} 条；覆盖 {len(set(synced_regions))} 个地区/{len(accounts)} 个账号。'
        ))
        detail_parts = []
        if created_asset_ids:
            detail_parts.append(f'新增ID={created_asset_ids[:20]}')
        if status_changed_items:
            detail_parts.append(f'状态变更={status_changed_items[:20]}')
        if deleted_by_missing_ip_items:
            detail_parts.append(f'缺IP删状态={deleted_by_missing_ip_items[:20]}')
        if unattached_ip_items:
            detail_parts.append(f'未附加IP={unattached_ip_items[:20]}')
        if conflict_skipped_items:
            detail_parts.append(f'冲突跳过={conflict_skipped_items[:20]}')
        if manual_expiry_preserved_items:
            detail_parts.append(f'保留手动到期={manual_expiry_preserved_items[:20]}')
        if verification_deleted_items:
            detail_parts.append(f'IP校验删除={verification_deleted_items[:20]}')
        if detail_parts:
            self.stdout.write(f'AWS 同步详情：{"；".join(detail_parts)}')
        self.synced_regions = synced_regions
        self.synced_instance_ids = synced_instance_ids
        self.synced_instance_ids_by_region = synced_instance_ids_by_region
        return None
