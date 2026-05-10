import logging

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from bot.api import _provider_status_label
from core.cloud_accounts import cloud_account_label, list_active_cloud_accounts
from core.persistence import record_external_sync_log
from cloud.models import CloudAsset, CloudServerOrder, Server, _runtime_int_config, _with_runtime_time
from cloud.note_utils import append_note
from cloud.services import record_cloud_ip_log
from cloud.sync_safety import get_missing_confirmation_threshold, mark_missing_confirmation_pending, with_missing_confirmation_note


logger = logging.getLogger(__name__)

_ACTIVE_ORDER_STATUSES = {'pending', 'provisioning', 'completed', 'expiring', 'renew_pending', 'suspended', 'deleting'}
_TRACEABLE_ORDER_STATUSES = _ACTIVE_ORDER_STATUSES | {'deleted'}
_SYNC_EXCLUDED_ASSET_STATUSES = {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED}
_SYNC_EXCLUDED_SERVER_STATUSES = {Server.STATUS_DELETED, Server.STATUS_TERMINATED}
_SYNC_EXCLUDED_ORDER_STATUSES = {'deleted', 'expired', 'cancelled'}
_MISSING_PENDING_STATUS = '云上未找到实例/IP-待确认'


def _mask_secret(secret):
    text = str(secret or '').strip()
    if not text:
        return '-'
    if len(text) <= 8:
        return '***'
    return f'{text[:4]}…{text[-4:]}'


def _resolve_order_for_ip(public_ip, account=None):
    normalized_ip = str(public_ip or '').strip()
    if not normalized_ip:
        return None
    queryset = CloudServerOrder.objects.filter(
        Q(public_ip=normalized_ip) | Q(previous_public_ip=normalized_ip),
        status__in=_TRACEABLE_ORDER_STATUSES,
    )
    if account:
        label = cloud_account_label(account)
        queryset = queryset.filter(Q(cloud_account=account) | Q(account_label=label))
    return queryset.filter(Q(status__in=_ACTIVE_ORDER_STATUSES) | Q(ip_recycle_at__gt=timezone.now())).order_by('-created_at', '-id').first()


NORMAL_AWS_STATES = {'running', 'pending', 'starting'}


def _fmt_dt(value):
    return value.isoformat() if value else '-'


def _mark_account_error(account, message: str):
    if account:
        account.mark_status(account.STATUS_ERROR, message)


def _is_region_skippable_auth_error(exc) -> bool:
    text = str(exc or '')
    return 'UnrecognizedClientException' in text or 'The security token included in the request is invalid' in text


def _aws_credentials(account):
    if not account:
        raise CommandError('未添加启用的 AWS 云账号，拒绝使用环境变量同步。请先在后台「云账号」添加 AWS 账号。')
    ak = (account.access_key_plain or '').strip()
    sk = (account.secret_key_plain or '').strip()
    # AWS secret key 正常长度 40，access key 20；明显截断的不可用
    if not ak or not sk or len(ak) < 16 or len(sk) < 36:
        message = f'AWS 云账号#{account.id} 凭据缺失或疑似截断，请在后台「云账号」重新保存。'
        _mark_account_error(account, message)
        raise CommandError(message)
    return ak, sk


def _aws_credential_source(account) -> str:
    return f'后台账号#{account.id}'


def _aws_account_identity(account) -> str:
    try:
        import boto3
        access_key, secret_key = _aws_credentials(account)
        identity = boto3.client(
            'sts',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        ).get_caller_identity()
        return str(identity.get('Account') or identity.get('Arn') or '')
    except Exception as exc:
        _mark_account_error(account, f'AWS STS 身份识别失败: {exc}')
        return f'未知({exc})'


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
            account=asset.cloud_account,
        )
        asset.status = CloudAsset.STATUS_DELETED
        asset.provider_status = '未附加固定IP-已到期删除'
        asset.is_active = False
        asset.note = append_note(
            asset.note,
            f"状态: 未附加固定IP；公网IP: {public_ip or '缺失'}；固定IP名: {release_name}；"
            f"已到15天回收时间，系统已调用 AWS API 真实删除；删除时间: {timezone.now().isoformat()}",
        )
        asset.save(update_fields=['status', 'provider_status', 'is_active', 'note', 'updated_at'])
        record_cloud_ip_log(
            event_type='recycled',
            order=getattr(asset, 'order', None),
            asset=asset,
            server=None,
            previous_public_ip=public_ip or None,
            public_ip=None,
            note=f'AWS 同步删除未附加固定 IP：IP={public_ip or "缺失"}；固定IP名={release_name}；资产#{asset.id}；已调用 AWS release_static_ip。',
        )
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
            account=asset.cloud_account,
        )
        asset.note = append_note(
            asset.note,
            f"状态: 未附加固定IP；公网IP: {public_ip or '缺失'}；固定IP名: {release_name}；"
            f"已到15天回收时间，但 AWS API 删除失败: {exc}；最近同步: {timezone.now().isoformat()}",
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


def _order_status_from_cloud_sync(order, status):
    cloud_status = _order_status_from_cloud_status(status)
    if cloud_status in {'deleted', 'expired', 'provisioning'} or not order:
        return cloud_status
    now = timezone.now()
    if order.delete_at and order.delete_at <= now:
        return 'deleting'
    if order.status in {'suspended', 'deleting'}:
        return order.status
    if order.suspend_at and order.suspend_at <= now:
        return 'suspended'
    if order.service_expires_at and order.service_expires_at <= now:
        return 'expiring'
    return cloud_status


def _asset_resolve_ordering():
    return [
        Case(
            When(status=CloudAsset.STATUS_DELETING, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
        Case(
            When(previous_public_ip__isnull=False, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
        '-updated_at',
        '-id',
    ]


def _server_resolve_ordering():
    return [
        Case(
            When(status=Server.STATUS_DELETING, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
        Case(
            When(previous_public_ip__isnull=False, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
        '-updated_at',
        '-id',
    ]


def _resolve_asset(instance_name, instance_arn, public_ip, order, account=None):
    lookup = Q(kind=CloudAsset.KIND_SERVER)
    if account:
        label = cloud_account_label(account)
        lookup &= (Q(cloud_account=account) | Q(account_label=label) | (Q(cloud_account__isnull=True) & (Q(account_label='') | Q(account_label__isnull=True))))
    if order:
        order_asset_q = Q()
        if instance_name:
            order_asset_q |= Q(instance_id=instance_name) | Q(asset_name=instance_name)
        if instance_arn:
            order_asset_q |= Q(provider_resource_id=instance_arn)
        if public_ip:
            order_asset_q |= Q(public_ip=public_ip) | Q(previous_public_ip=public_ip)
        if order_asset_q:
            order_asset = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, order=order).filter(order_asset_q).order_by(*_asset_resolve_ordering()).first()
            if order_asset:
                return order_asset
    base_queryset = CloudAsset.objects.filter(lookup).filter(Q(order__isnull=True) | ~Q(order__status__in=_SYNC_EXCLUDED_ORDER_STATUSES)).exclude(status__in=_SYNC_EXCLUDED_ASSET_STATUSES)
    direct_candidates = Q()
    if instance_name:
        direct_candidates |= Q(instance_id=instance_name) | Q(asset_name=instance_name)
    if instance_arn:
        direct_candidates |= Q(provider_resource_id=instance_arn)
    if direct_candidates:
        asset = base_queryset.filter(direct_candidates).order_by(*_asset_resolve_ordering()).first()
        if asset:
            return asset
    if public_ip:
        public_ip_queryset = base_queryset.filter(Q(public_ip=public_ip) | Q(previous_public_ip=public_ip))
        if order:
            public_ip_queryset = public_ip_queryset.filter(Q(order__isnull=True) | Q(order=order))
        return public_ip_queryset.order_by(*_asset_resolve_ordering()).first()
    return None


def _append_unique_line(text: str | None, line: str) -> str:
    current = str(text or '')
    if line and line not in current:
        return '\n'.join(filter(None, [current, line]))
    return current


def _sync_order_deleted_from_cloud(order, old_public_ip, *, source: str = 'AWS 同步校验', asset=None, server=None):
    before = {
        'status': order.status,
        'service_expires_at': order.service_expires_at,
        'renew_grace_expires_at': order.renew_grace_expires_at,
        'suspend_at': order.suspend_at,
        'delete_at': order.delete_at,
        'ip_recycle_at': order.ip_recycle_at,
        'migration_due_at': order.migration_due_at,
    }
    now = timezone.now()
    asset_id = getattr(asset, 'id', None)
    server_id = getattr(server, 'id', None)
    instance_name = str(getattr(asset, 'instance_id', None) or getattr(server, 'instance_id', None) or order.instance_id or order.server_name or '').strip()
    note = f'{source}: 云端未找到实例/IP，订单随同步链标记删除；IP={old_public_ip or "缺失"}；Asset={asset_id or "-"}；Server={server_id or "-"}；实例={instance_name or "-"}；时间={now.isoformat()}。'
    order.status = 'deleted'
    order.previous_public_ip = old_public_ip or order.previous_public_ip
    order.public_ip = None
    order.expired_at = order.expired_at or now
    order.provision_note = _append_unique_line(order.provision_note, note)
    update_fields = ['status', 'previous_public_ip', 'public_ip', 'expired_at', 'provision_note', 'updated_at']
    if order.migration_due_at and (not order.service_expires_at or order.service_expires_at > order.migration_due_at):
        order.service_expires_at = order.migration_due_at
        update_fields.extend(['service_expires_at', 'renew_grace_expires_at', 'suspend_at', 'delete_at', 'ip_recycle_at'])
    order.save(update_fields=update_fields)
    logger.info(
        'AWS_SYNC_ORDER_DELETED_DATE_CHANGE order_id=%s order_no=%s old_public_ip=%s status=%s->%s service_expires_at=%s->%s renew_grace_expires_at=%s->%s suspend_at=%s->%s delete_at=%s->%s ip_recycle_at=%s->%s migration_due_at=%s->%s note=%s',
        order.id,
        order.order_no,
        old_public_ip,
        before['status'],
        order.status,
        _fmt_dt(before['service_expires_at']),
        _fmt_dt(order.service_expires_at),
        _fmt_dt(before['renew_grace_expires_at']),
        _fmt_dt(order.renew_grace_expires_at),
        _fmt_dt(before['suspend_at']),
        _fmt_dt(order.suspend_at),
        _fmt_dt(before['delete_at']),
        _fmt_dt(order.delete_at),
        _fmt_dt(before['ip_recycle_at']),
        _fmt_dt(order.ip_recycle_at),
        _fmt_dt(before['migration_due_at']),
        _fmt_dt(order.migration_due_at),
        note,
    )


def _resolve_server(instance_name, instance_arn, public_ip, order, account=None):
    base = Q()
    if account:
        label = cloud_account_label(account)
        base &= (Q(account_label=label) | Q(account_label='') | Q(account_label__isnull=True))
    if order:
        order_server_q = Q()
        if instance_name:
            order_server_q |= Q(instance_id=instance_name) | Q(server_name=instance_name)
        if instance_arn:
            order_server_q |= Q(provider_resource_id=instance_arn)
        if public_ip:
            order_server_q |= Q(public_ip=public_ip) | Q(previous_public_ip=public_ip)
        if order_server_q:
            order_server = Server.objects.filter(order=order).filter(order_server_q).order_by(*_server_resolve_ordering()).first()
            if order_server:
                return order_server
    base_queryset = Server.objects.filter(base).filter(Q(order__isnull=True) | ~Q(order__status__in=_SYNC_EXCLUDED_ORDER_STATUSES)).exclude(status__in=_SYNC_EXCLUDED_SERVER_STATUSES)
    direct_candidates = Q()
    if instance_name:
        direct_candidates |= Q(instance_id=instance_name) | Q(server_name=instance_name)
    if instance_arn:
        direct_candidates |= Q(provider_resource_id=instance_arn)
    if direct_candidates:
        server = base_queryset.filter(direct_candidates).order_by(*_server_resolve_ordering()).first()
        if server:
            return server
    if public_ip:
        public_ip_queryset = base_queryset.filter(Q(public_ip=public_ip) | Q(previous_public_ip=public_ip))
        if order:
            public_ip_queryset = public_ip_queryset.filter(Q(order__isnull=True) | Q(order=order))
        return public_ip_queryset.order_by(*_server_resolve_ordering()).first()
    return None


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
        asset = CloudAsset.objects.filter(lookup & exact_candidates).order_by(
            Case(
                When(status=CloudAsset.STATUS_UNKNOWN, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
            '-updated_at',
            '-id',
        ).first()
        if asset:
            return asset

    if public_ip:
        return (
            CloudAsset.objects.filter(lookup & (Q(public_ip=public_ip) | Q(previous_public_ip=public_ip)))
            .filter(Q(instance_id__isnull=True) | Q(instance_id='') | Q(provider_status='未附加固定IP') | Q(provider_resource_id__contains='StaticIp'))
            .order_by(
                Case(
                    When(status=CloudAsset.STATUS_UNKNOWN, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
                '-updated_at',
                '-id',
            )
            .first()
        )
    return None


def _mark_ip_retained_as_unattached(public_ip, static_ip_name, retained_order, account, region, note, now, release_at=None):
    if not public_ip:
        return []
    label = cloud_account_label(account) if account else ''
    lookup = Q(kind=CloudAsset.KIND_SERVER, provider='aws_lightsail') & (Q(public_ip=public_ip) | Q(previous_public_ip=public_ip))
    if account:
        lookup &= (Q(cloud_account=account) | Q(account_label=label))
    updated = []
    for related in CloudAsset.objects.filter(lookup).exclude(provider_status__contains='未附加固定IP'):
        if str(getattr(related, 'asset_name', '') or '') == str(static_ip_name or '') and not getattr(related, 'instance_id', None):
            continue
        related.previous_public_ip = related.previous_public_ip or related.public_ip or public_ip
        related.public_ip = related.public_ip or public_ip
        related.status = CloudAsset.STATUS_UNKNOWN
        related.is_active = False
        related.provider_status = '固定IP仍存在但未附加'
        if release_at and not related.actual_expires_at:
            related.actual_expires_at = release_at
        related.note = append_note(related.note, f'状态: 固定IP仍存在但未附加；公网IP: {public_ip}；固定IP名: {static_ip_name or "-"}；发现时间: {now.isoformat()}；计划释放时间: {related.actual_expires_at.isoformat() if related.actual_expires_at else "-"}；最近同步: {now.isoformat()}')
        if retained_order and not related.order_id:
            related.order = retained_order
            related.user = retained_order.user
        related.save(update_fields=['previous_public_ip', 'public_ip', 'actual_expires_at', 'status', 'is_active', 'provider_status', 'note', 'order', 'user', 'updated_at'])
        trace = record_cloud_ip_log(
            event_type='changed',
            order=retained_order or getattr(related, 'order', None),
            asset=related,
            server=None,
            public_ip=public_ip,
            previous_public_ip=related.previous_public_ip,
            note=f'AWS 同步覆盖：固定 IP 仍存在但未附加；IP={public_ip}；固定IP名={static_ip_name or "-"}；发现时间={now.isoformat()}；计划释放时间={release_at.isoformat() if release_at else "-"}；不标记删除。',
            trigger_label='AWS同步',
        )
        if trace and trace.note:
            cleaned_lines = []
            for line in trace.note.split('\n'):
                if public_ip in line and ('云上不存在' in line or '已标记删除' in line):
                    continue
                cleaned_lines.append(line)
            cleaned_note = '\n'.join(line for line in cleaned_lines if line.strip())
            if cleaned_note != trace.note:
                trace.note = cleaned_note
                trace.save(update_fields=['note'])
        updated.append(f'{related.id}:{public_ip}:{related.asset_name}')
    server_lookup = Q(provider='aws_lightsail') & (Q(public_ip=public_ip) | Q(previous_public_ip=public_ip))
    if account:
        server_lookup &= Q(account_label=label)
    for server in Server.objects.filter(server_lookup):
        server.previous_public_ip = server.previous_public_ip or server.public_ip or public_ip
        server.public_ip = server.public_ip or public_ip
        server.status = Server.STATUS_UNKNOWN
        server.is_active = False
        server.provider_status = '固定IP仍存在但未附加'
        if release_at:
            server.expires_at = release_at
        server.note = append_note(server.note, f'状态: 固定IP仍存在但未附加；公网IP: {public_ip}；固定IP名: {static_ip_name or "-"}；发现时间: {now.isoformat()}；计划释放时间: {release_at.isoformat() if release_at else "-"}；覆盖同步时间: {now.isoformat()}')
        if retained_order and not server.order_id:
            server.order = retained_order
            server.user = retained_order.user
        server.save(update_fields=['previous_public_ip', 'public_ip', 'expires_at', 'status', 'is_active', 'provider_status', 'note', 'order', 'user', 'updated_at'])
    if retained_order:
        retained_order.public_ip = retained_order.public_ip or public_ip
        retained_order.previous_public_ip = retained_order.previous_public_ip or public_ip
        retained_order.static_ip_name = retained_order.static_ip_name or static_ip_name
        retained_order.ip_recycle_at = release_at or retained_order.ip_recycle_at
        retained_order.provision_note = _append_unique_line(retained_order.provision_note, note)
        retained_order.save(update_fields=['public_ip', 'previous_public_ip', 'static_ip_name', 'ip_recycle_at', 'status', 'provision_note', 'updated_at'])
    return updated


def _mark_deleted_when_missing_in_aws(region, existing_instance_names, existing_public_ips, stdout, account=None):
    verification_deleted_items = []
    queryset = CloudAsset.objects.filter(
        kind=CloudAsset.KIND_SERVER,
        provider='aws_lightsail',
    ).exclude(status__in=_SYNC_EXCLUDED_ASSET_STATUSES)
    if account:
        label = cloud_account_label(account)
        queryset = queryset.filter(Q(cloud_account=account) | Q(account_label=label))
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
        if public_ip and public_ip in existing_public_ips:
            continue
        old_public_ip = public_ip or str(asset.previous_public_ip or '').strip()
        pending_count, threshold = mark_missing_confirmation_pending(
            asset,
            old_public_ip=old_public_ip,
            now_iso=now_iso,
            provider_status='云上未找到实例/IP',
            pending_status=_MISSING_PENDING_STATUS,
        )
        if pending_count < threshold:
            asset.save(update_fields=['provider_status', 'note', 'updated_at'])
            stdout.stdout.write(stdout.style.WARNING(
                f'IP校验 待确认 资产#{asset.id} IP={old_public_ip or "缺失"} 云上不存在 第{pending_count}/{threshold}次'
            ))
            continue
        asset.status = CloudAsset.STATUS_DELETED
        asset.is_active = False
        asset.previous_public_ip = old_public_ip or asset.previous_public_ip
        asset.public_ip = None
        asset.provider_status = '云上未找到实例/IP'
        asset.note = with_missing_confirmation_note(append_note(asset.note, f'状态: 云上未找到实例/IP；公网IP: {old_public_ip or "缺失"}；最近同步: {now_iso}'), pending_count)
        asset.save(update_fields=['status', 'is_active', 'previous_public_ip', 'public_ip', 'provider_status', 'note', 'updated_at'])
        server_queryset = Server.objects.filter(
            Q(instance_id=instance_name) | Q(provider_resource_id=asset.provider_resource_id) | Q(public_ip=public_ip) | Q(previous_public_ip=old_public_ip),
            provider='aws_lightsail',
        )
        if account:
            server_queryset = server_queryset.filter(account_label=cloud_account_label(account))
        server = server_queryset.order_by('-updated_at', '-id').first()
        if server:
            server.status = Server.STATUS_DELETED
            server.is_active = False
            server.previous_public_ip = old_public_ip or server.previous_public_ip
            server.public_ip = None
            server.provider_status = '云上未找到实例/IP'
            server.note = with_missing_confirmation_note(append_note(server.note, f'状态: 云上未找到实例/IP；公网IP: {old_public_ip or "缺失"}；最近同步: {now_iso}'), pending_count)
            server.save(update_fields=['status', 'is_active', 'previous_public_ip', 'public_ip', 'provider_status', 'note', 'updated_at'])
        order = getattr(asset, 'order', None) or _resolve_order_for_ip(old_public_ip, account)
        if order:
            _sync_order_deleted_from_cloud(order, old_public_ip, source='AWS 同步资产校验删除', asset=asset, server=server)
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
    server_queryset = Server.objects.filter(provider='aws_lightsail').exclude(status__in=_SYNC_EXCLUDED_SERVER_STATUSES)
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
        if public_ip and public_ip in existing_public_ips:
            continue
        old_public_ip = public_ip or str(server.previous_public_ip or '').strip()
        pending_count, threshold = mark_missing_confirmation_pending(
            server,
            old_public_ip=old_public_ip,
            now_iso=now_iso,
            provider_status='云上未找到实例/IP',
            pending_status=_MISSING_PENDING_STATUS,
        )
        if pending_count < threshold:
            server.save(update_fields=['provider_status', 'note', 'updated_at'])
            stdout.stdout.write(stdout.style.WARNING(
                f'服务器校验 待确认 Server#{server.id} IP={old_public_ip or "缺失"} 云上不存在 第{pending_count}/{threshold}次'
            ))
            continue
        server.status = Server.STATUS_DELETED
        server.is_active = False
        server.previous_public_ip = old_public_ip or server.previous_public_ip
        server.public_ip = None
        server.provider_status = '云上未找到实例/IP'
        server.note = with_missing_confirmation_note(append_note(server.note, f'状态: 云上未找到实例/IP；公网IP: {old_public_ip or "缺失"}；最近同步: {now_iso}'), pending_count)
        server.save(update_fields=['status', 'is_active', 'previous_public_ip', 'public_ip', 'provider_status', 'note', 'updated_at'])
        order = getattr(server, 'order', None) or _resolve_order_for_ip(old_public_ip, account)
        if order:
            _sync_order_deleted_from_cloud(order, old_public_ip, source='AWS 同步 Server 校验删除', server=server)
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
        parser.add_argument('--account-id', default='', help='只同步指定后台云账号 ID')

    def handle(self, *args, **options):
        accounts = list_active_cloud_accounts('aws')
        account_id = str(options.get('account_id') or '').strip()
        if account_id:
            accounts = [account for account in accounts if str(account.id) == account_id]
            if not accounts:
                raise CommandError(f'未找到启用的 AWS 云账号 #{account_id}')
        if not accounts:
            raise CommandError('未添加启用的 AWS 云账号，拒绝使用环境变量同步。请先在后台「云账号」添加 AWS 账号。')
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
        synced_public_ips_by_region = {}
        verification_deleted_items = []
        account_summary_lines = []
        sync_errors = []

        synced_regions = []
        for account in accounts:
            regions = _list_regions(options['region'], account)
            account_label = cloud_account_label(account) or 'aws'
            account_stats = {
                'label': account_label,
                'source': _aws_credential_source(account),
                'aws_account': _aws_account_identity(account),
                'regions': regions,
                'count': 0,
                'created': 0,
                'updated': 0,
                'unattached': 0,
                'deleted_by_missing_ip': 0,
                'ips': [],
                'errors': [],
            }
            for region in regions:
                client = _lightsail_client(region, account)
                region_failed = False
                region_instance_ids = []
                region_public_ips = set()
                next_page_token = None

                attached_instance_names = set()
                existing_static_ips = set()
                existing_public_ips = set()
                attached_static_ip_by_instance = {}
                static_ip_cache = []
                static_ip_next_page_token = None
                while True:
                    static_ip_kwargs = {}
                    if static_ip_next_page_token:
                        static_ip_kwargs['pageToken'] = static_ip_next_page_token
                    try:
                        static_ip_response = client.get_static_ips(**static_ip_kwargs)
                    except Exception as exc:
                        if _is_region_skippable_auth_error(exc):
                            message = f'AWS 云账号 {account_label} 地区 {region} 暂不可用，已跳过固定 IP 同步: {exc}'
                            account_stats['errors'].append(message)
                            self.stdout.write(self.style.WARNING(message))
                            region_failed = True
                            break
                        message = f'AWS 云账号 {account_label} 地区 {region} 获取固定 IP 失败: {exc}'
                        _mark_account_error(account, message)
                        account_stats['errors'].append(message)
                        sync_errors.append(message)
                        self.stdout.write(self.style.WARNING(message))
                        region_failed = True
                        break
                    static_ips = static_ip_response.get('staticIps') or []
                    static_ip_cache.extend(static_ips)
                    for static_ip_item in static_ips:
                        static_public_ip = static_ip_item.get('ipAddress') or ''
                        static_attached_to = static_ip_item.get('attachedTo') or ''
                        if static_public_ip:
                            existing_static_ips.add(static_public_ip)
                            existing_public_ips.add(static_public_ip)
                            region_public_ips.add(static_public_ip)
                        if static_attached_to and static_public_ip:
                            attached_static_ip_by_instance[static_attached_to] = {
                                'ip': static_public_ip,
                                'name': static_ip_item.get('name') or '',
                                'arn': static_ip_item.get('arn') or '',
                            }
                    static_ip_next_page_token = static_ip_response.get('nextPageToken')
                    if not static_ip_next_page_token:
                        break
                if region_failed:
                    continue

                while True:
                    kwargs = {}
                    if next_page_token:
                        kwargs['pageToken'] = next_page_token
                    try:
                        response = client.get_instances(**kwargs)
                    except Exception as exc:
                        if _is_region_skippable_auth_error(exc):
                            message = f'AWS 云账号 {account_label} 地区 {region} 暂不可用，已跳过实例同步: {exc}'
                            account_stats['errors'].append(message)
                            self.stdout.write(self.style.WARNING(message))
                            region_failed = True
                            break
                        message = f'AWS 云账号 {account_label} 地区 {region} 获取实例失败: {exc}'
                        _mark_account_error(account, message)
                        account_stats['errors'].append(message)
                        sync_errors.append(message)
                        self.stdout.write(self.style.WARNING(message))
                        region_failed = True
                        break
                    record_external_sync_log(
                        source='aws_lightsail',
                        action='get_instances',
                        target=region,
                        request_payload=kwargs,
                        response_payload={'count': len(response.get('instances') or []), 'nextPageToken': response.get('nextPageToken')},
                        is_success=True,
                        account=account,
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
                        attached_static_ip = attached_static_ip_by_instance.get(instance_name) or {}
                        instance_public_ip = item.get('publicIpAddress') or ''
                        public_ip = attached_static_ip.get('ip') or instance_public_ip
                        if public_ip:
                            existing_public_ips.add(public_ip)
                            region_public_ips.add(public_ip)
                        normalized_status = _elevate_deleted_when_ip_missing(normalized_status, public_ip)
                        bundle_id = item.get('bundleId') or '-'
                        blueprint_id = item.get('blueprintId') or '-'
                        order_scope = CloudServerOrder.objects.filter(provider='aws_lightsail', status__in=_ACTIVE_ORDER_STATUSES)
                        traceable_order_scope = CloudServerOrder.objects.filter(provider='aws_lightsail', status__in=_TRACEABLE_ORDER_STATUSES)
                        if account:
                            account_filter = Q(cloud_account=account) | Q(account_label=account_label)
                            order_scope = order_scope.filter(account_filter)
                            traceable_order_scope = traceable_order_scope.filter(account_filter)
                        exact_order_q = Q(instance_id=instance_name) | Q(provider_resource_id=instance_arn) | Q(server_name=instance_name)
                        order = order_scope.filter(exact_order_q).first()
                        if not order and public_ip:
                            order = order_scope.filter(public_ip=public_ip).first()
                        if not order:
                            order = traceable_order_scope.filter(exact_order_q).first()
                        expires_at = order.service_expires_at if order else None
                        order_user = None
                        if order and order.user_id:
                            order_user = getattr(order, '_state', None) and None
                            try:
                                order_user = order.user
                            except Exception:
                                order_user = None

                        provider_status = _provider_status_label(state_name or None)
                        asset = _resolve_asset(instance_name, instance_arn, public_ip, order, account)
                        old_status = asset.status if asset else None
                        preserve_lifecycle_status = bool(
                            (order and order.status == 'deleting')
                            or old_status in {CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATING}
                        )
                        if (order and order.status == 'deleting') or old_status == CloudAsset.STATUS_DELETING:
                            provider_status = f'旧机保留期，等待删除（云端{provider_status}）'
                        elif order and order.status == 'suspended':
                            provider_status = f'已到期关机，等待删除（云端{provider_status}）'
                        static_ip_name = attached_static_ip.get('name') or ''
                        static_ip_note = f"；固定IP名: {static_ip_name}" if static_ip_name else ''
                        note = f"状态: {provider_status}；公网IP: {public_ip or '缺失'}{static_ip_note}；套餐: {bundle_id}；镜像: {blueprint_id}；到期: {expires_at or '-'}；最近同步: {now_iso}"
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
                        rebound_unattached_ip = bool(
                            asset
                            and '未附加' in str(getattr(asset, 'provider_status', '') or '')
                            and str(instance_name or '').strip()
                        )
                        if asset:
                            asset_defaults['user'] = asset.user or order_user
                            if not asset.order_id and order:
                                asset_defaults['order'] = order
                                asset_defaults['actual_expires_at'] = expires_at
                            if rebound_unattached_ip:
                                rebound_at = timezone.now()
                                rebound_note = f'未附加IP已重新绑定到实例，已清空临时到期时间：{rebound_at.isoformat()}；等待人工添加真实到期时间。'
                                asset_defaults['actual_expires_at'] = None
                                asset_defaults['provider_status'] = '已重新绑定实例-待人工添加时间'
                                asset_defaults['note'] = append_note(asset.note, _append_unique_line(note, rebound_note))
                                asset_defaults['is_active'] = True
                            else:
                                asset_defaults['actual_expires_at'] = asset.actual_expires_at
                                asset_defaults['note'] = append_note(asset.note, note)
                        asset_signature = f'{instance_name or "-"}|{instance_arn or "-"}|{public_ip or "缺失"}'
                        old_public_ip = asset.public_ip if asset else None
                        ip_changed = bool(asset and old_public_ip and old_public_ip != public_ip)
                        created_asset_from_sync = asset is None
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
                                if preserve_lifecycle_status and old_status:
                                    asset.status = old_status
                                    asset.is_active = old_status not in {CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATING}
                                if rebound_unattached_ip and asset.status == CloudAsset.STATUS_UNKNOWN:
                                    asset.status = CloudAsset.STATUS_RUNNING
                                if original_due_at and not rebound_unattached_ip:
                                    manual_expiry_preserved_items.append(f'{asset.id}:{public_ip or "缺失"}:{instance_name or instance_arn}:{original_due_at}')
                                asset.save()
                                updated_count += 1
                                account_stats['updated'] += 1
                                updated_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{instance_name or asset.asset_name}')
                        else:
                            asset = CloudAsset.objects.create(**asset_defaults)
                            claimed_assets[asset.id] = asset_signature
                            created_count += 1
                            account_stats['created'] += 1
                            created_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{instance_name or asset.asset_name}')
                        if old_status is not None and old_status != normalized_status:
                            status_changed_items.append(f'{asset.id}:{public_ip or "缺失"}:{old_status}->{normalized_status}')
                        if normalized_status == CloudAsset.STATUS_DELETED and not str(public_ip or '').strip():
                            deleted_by_missing_ip_count += 1
                            account_stats['deleted_by_missing_ip'] += 1
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
                        if asset:
                            server_defaults['user'] = asset.user or order_user
                            server_defaults['expires_at'] = asset.actual_expires_at or expires_at
                            if not asset.order_id and order:
                                server_defaults['order'] = order
                            if rebound_unattached_ip:
                                server_defaults['expires_at'] = asset_defaults['actual_expires_at']
                                server_defaults['provider_status'] = asset_defaults['provider_status']
                                server_defaults['note'] = asset_defaults['note']
                                server_defaults['is_active'] = True
                        server = _resolve_server(instance_name, instance_arn, public_ip, order, account)
                        old_server_status = server.status if server else None
                        if server:
                            server_defaults['user'] = server.user or order_user or getattr(asset, 'user', None)
                            server_defaults['expires_at'] = server.expires_at if not rebound_unattached_ip else asset_defaults['actual_expires_at']
                            server_defaults['note'] = append_note(server.note, server_defaults['note'])
                            if not server.order_id and order:
                                server_defaults['order'] = order
                        old_server_public_ip = server.public_ip if server else None
                        if server:
                            if old_server_public_ip and old_server_public_ip != public_ip:
                                server.previous_public_ip = old_server_public_ip
                            for key, value in server_defaults.items():
                                setattr(server, key, value)
                            if preserve_lifecycle_status and old_server_status:
                                server.status = old_server_status
                                server.is_active = old_server_status not in {Server.STATUS_DELETING, Server.STATUS_TERMINATING}
                            if rebound_unattached_ip and server.status == Server.STATUS_UNKNOWN:
                                server.status = Server.STATUS_RUNNING
                            server.save()
                        else:
                            server = Server.objects.create(**server_defaults)

                        if created_asset_from_sync:
                            record_cloud_ip_log(
                                event_type='created',
                                order=order,
                                asset=asset,
                                server=server,
                                public_ip=public_ip or None,
                                previous_public_ip=None,
                                note=f'AWS 同步发现新实例：账号={account_label}；地区={region}；实例={instance_name or instance_arn}；IP={public_ip or "缺失"}；固定IP名={static_ip_name or "-"}。',
                            )

                        if order:
                            original_order_status = order.status
                            if not preserve_lifecycle_status:
                                order.status = _order_status_from_cloud_sync(order, normalized_status)
                            if original_order_status in _SYNC_EXCLUDED_ORDER_STATUSES and order.status != original_order_status:
                                order.provision_note = _append_unique_line(
                                    order.provision_note,
                                    f'AWS 同步发现云端实例仍存在，已恢复订单状态：{original_order_status}->{order.status}；实例={instance_name or instance_arn or "-"}；IP={public_ip or "缺失"}；时间={timezone.now().isoformat()}。',
                                )
                            order.cloud_account = account
                            order.account_label = account_label
                            order.region_code = region
                            order.region_name = location.get('regionName') or region
                            order.server_name = instance_name
                            order.instance_id = instance_name
                            order.provider_resource_id = instance_arn
                            order.static_ip_name = static_ip_name or order.static_ip_name
                            if public_ip and order.public_ip != public_ip:
                                order.previous_public_ip = order.public_ip or order.previous_public_ip
                            order.public_ip = public_ip or None
                            order.save(update_fields=[
                                'status', 'cloud_account', 'account_label', 'region_code', 'region_name',
                                'server_name', 'instance_id', 'provider_resource_id', 'static_ip_name',
                                'previous_public_ip', 'public_ip', 'provision_note', 'updated_at',
                            ])

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
                        account_stats['ips'].append(f'{region}:{public_ip or "缺失"}:{instance_name or instance_arn}')
                        count += 1
                        account_stats['count'] += 1
                    next_page_token = response.get('nextPageToken')
                    if not next_page_token:
                        break
                if region_failed:
                    continue
                synced_regions.append(region)

                for item in static_ip_cache:
                    attached_to = item.get('attachedTo') or ''
                    public_ip = item.get('ipAddress') or ''
                    if public_ip:
                        existing_public_ips.add(public_ip)
                        region_public_ips.add(public_ip)
                    if attached_to and attached_to in attached_instance_names:
                        continue
                    static_ip_name = item.get('name') or public_ip or 'aws-static-ip'
                    static_ip_arn = item.get('arn') or static_ip_name
                    location = item.get('location') or {}
                    provider_status = '未附加固定IP'
                    discovered_at = timezone.now()
                    recycle_due_at = _with_runtime_time(
                        discovered_at + timezone.timedelta(days=max(1, _runtime_int_config('cloud_unattached_ip_delete_after_days', 15))),
                        'cloud_unattached_ip_delete_time',
                    )
                    note = (
                        f"状态: {provider_status}；公网IP: {public_ip or '缺失'}；固定IP名: {static_ip_name}；"
                        f"发现时间: {discovered_at.isoformat()}；计划删除时间: {recycle_due_at.isoformat()}；最近同步: {discovered_at.isoformat()}"
                    )
                    retained_order = _resolve_order_for_ip(public_ip, account) if public_ip else None
                    if (
                        retained_order
                        and not str(retained_order.instance_id or '').strip()
                        and retained_order.delete_at
                        and retained_order.delete_at > discovered_at
                        and retained_order.ip_recycle_at
                        and retained_order.ip_recycle_at > recycle_due_at
                    ):
                        retained_order.ip_recycle_at = recycle_due_at
                        if retained_order.status == 'completed':
                            retained_order.status = 'deleted'
                        retained_order.provision_note = append_note(
                            retained_order.provision_note,
                            f'AWS 同步校正未附加固定 IP 生命周期：实例已提前删除，固定 IP 从发现未附加时间重新计算 {max(1, _runtime_int_config("cloud_unattached_ip_delete_after_days", 15))} 天；计划释放时间={recycle_due_at.isoformat()}。',
                        )
                        retained_order.save(update_fields=['status', 'ip_recycle_at', 'provision_note', 'updated_at'])
                        status_changed_items.append(f'{retained_order.id}:{public_ip}:unattached_lifecycle_rebased:{recycle_due_at.isoformat()}')
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
                    if retained_order:
                        if retained_order.ip_recycle_at:
                            asset_defaults['actual_expires_at'] = retained_order.ip_recycle_at
                            recycle_due_at = retained_order.ip_recycle_at
                        asset_defaults.update({
                            'order': retained_order,
                            'user': retained_order.user,
                            'mtproxy_port': retained_order.mtproxy_port,
                            'mtproxy_link': retained_order.mtproxy_link,
                            'proxy_links': retained_order.proxy_links or [],
                            'mtproxy_secret': retained_order.mtproxy_secret,
                            'mtproxy_host': retained_order.mtproxy_host,
                        })
                    asset = _resolve_asset_for_static_ip(static_ip_name, static_ip_arn, public_ip, account)
                    created_unattached_asset_from_sync = asset is None
                    if asset:
                        if asset.user_id:
                            asset_defaults['user'] = asset.user
                        if asset.actual_expires_at:
                            asset_defaults['actual_expires_at'] = asset.actual_expires_at
                        asset_defaults['note'] = append_note(asset.note, note)
                    asset_signature = f'{static_ip_name or "-"}|{static_ip_arn or "-"}|{public_ip or "缺失"}'
                    old_status = asset.status if asset else None
                    if asset:
                        claimed_signature = claimed_assets.get(asset.id)
                        if claimed_signature and claimed_signature != asset_signature:
                            occupied_ip = claimed_signature.split('|')[-1]
                            current_ip = asset_signature.split('|')[-1]
                            conflict_skipped_items.append(f'{asset.id}:{occupied_ip}->{current_ip}')
                            self.stdout.write(self.style.WARNING(f'冲突已跳过 资产#{asset.id} 已占IP={occupied_ip} 当前IP={current_ip}'))
                        else:
                            claimed_assets[asset.id] = asset_signature
                            original_due_at = asset.actual_expires_at
                            for key, value in asset_defaults.items():
                                setattr(asset, key, value)
                            due_changed = bool(original_due_at and asset.actual_expires_at and original_due_at != asset.actual_expires_at)
                            asset.save()
                            if due_changed:
                                manual_expiry_preserved_items.append(f'{asset.id}:{public_ip or "缺失"}:{static_ip_name or static_ip_arn}:{original_due_at}')
                            updated_count += 1
                            account_stats['updated'] += 1
                            updated_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{static_ip_name or static_ip_arn}')
                    else:
                        asset = CloudAsset.objects.create(**asset_defaults)
                        claimed_assets[asset.id] = asset_signature
                        created_count += 1
                        account_stats['created'] += 1
                        created_asset_ids.append(f'{asset.id}:{public_ip or "缺失"}:{static_ip_name or static_ip_arn}')
                    if created_unattached_asset_from_sync:
                        record_cloud_ip_log(
                            event_type='created',
                            order=retained_order,
                            asset=asset,
                            server=None,
                            public_ip=public_ip or None,
                            previous_public_ip=None,
                            note=(
                                f'AWS 同步发现未附加固定 IP：账号={account_label}；地区={region}；'
                                f'IP={public_ip or "缺失"}；固定IP名={static_ip_name}；计划删除时间={asset.actual_expires_at.isoformat() if asset.actual_expires_at else "-"}。'
                            ),
                        )
                    elif old_status is not None and old_status != CloudAsset.STATUS_UNKNOWN:
                        record_cloud_ip_log(
                            event_type='changed',
                            order=retained_order or getattr(asset, 'order', None),
                            asset=asset,
                            server=None,
                            public_ip=public_ip or None,
                            previous_public_ip=None,
                            note=(
                                f'AWS 同步确认固定 IP 已分离为未附加状态：账号={account_label}；地区={region}；'
                                f'IP={public_ip or "缺失"}；固定IP名={static_ip_name}；原状态={old_status}。'
                            ),
                        )

                    if retained_order:
                        order_note = (
                            f'AWS 同步确认固定 IP 未附加但仍保留；IP={public_ip or "缺失"}；固定IP名={static_ip_name}；'
                            f'端口={retained_order.mtproxy_port or "-"}；secret={_mask_secret(retained_order.mtproxy_secret)}；'
                            f'服务到期={retained_order.service_expires_at.isoformat() if retained_order.service_expires_at else "-"}；'
                            f'实例删除={retained_order.delete_at.isoformat() if retained_order.delete_at else "-"}；'
                            f'IP计划回收={retained_order.ip_recycle_at.isoformat() if retained_order.ip_recycle_at else recycle_due_at.isoformat()}；'
                            f'用户续费/重装时必须对照旧 IP、端口、secret。'
                        )
                        retained_order.ip_recycle_at = retained_order.ip_recycle_at or recycle_due_at
                        covered_items = _mark_ip_retained_as_unattached(public_ip, static_ip_name, retained_order, account, region, order_note, discovered_at, recycle_due_at)
                        if covered_items:
                            status_changed_items.extend([f'{item}:static_ip_retained_unattached' for item in covered_items])
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
                            duplicate.note = append_note(duplicate.note, f'状态: 重复未附加固定IP记录；原公网IP: {public_ip}；保留资产#{asset.id}；最近同步: {timezone.now().isoformat()}')
                            duplicate.save(update_fields=['status', 'is_active', 'previous_public_ip', 'public_ip', 'provider_status', 'note', 'updated_at'])
                            status_changed_items.append(f'{duplicate.id}:{public_ip}:duplicate_static_ip_deleted')
                    if old_status is not None and old_status != CloudAsset.STATUS_UNKNOWN:
                        status_changed_items.append(f'{asset.id}:{public_ip or "缺失"}:{old_status}->{CloudAsset.STATUS_UNKNOWN}')
                    unattached_ip_count += 1
                    account_stats['unattached'] += 1
                    unattached_ip_items.append(f'{asset.id}:{public_ip or "缺失"}:{static_ip_name or static_ip_arn}')
                    account_stats['ips'].append(f'{region}:{public_ip or "缺失"}:{static_ip_name or static_ip_arn}')
                    count += 1
                    account_stats['count'] += 1

                verification_deleted_items.extend(
                    _mark_deleted_when_missing_in_aws(
                        region=region,
                        existing_instance_names=set(region_instance_ids),
                        existing_public_ips=existing_public_ips,
                        stdout=self,
                        account=account,
                    )
                )
                synced_instance_ids_by_region.setdefault(region, set()).update(region_instance_ids)
                synced_public_ips_by_region.setdefault(region, set()).update(region_public_ips)
            error_text = f"；错误={account_stats['errors'][:5]}" if account_stats['errors'] else ''
            ip_preview = account_stats['ips'][:20]
            ip_text = f"{len(account_stats['ips'])} 个，前20={ip_preview}" if account_stats['ips'] else '无'
            account_summary_lines.append(
                f"账号={account_stats['label'] or account_stats['aws_account']}；凭据来源={account_stats['source']}；AWS账号ID={account_stats['aws_account'] or '-'}；地区={','.join(account_stats['regions']) or '-'}；扫描={account_stats['count']}；新增={account_stats['created']}；更新={account_stats['updated']}；未附加IP={account_stats['unattached']}；缺IP删除={account_stats['deleted_by_missing_ip']}；IP={ip_text}{error_text}"
            )
            if account_stats['count'] or account_stats['created'] or account_stats['updated']:
                account.mark_status(
                    account.STATUS_OK,
                    f"AWS 同步完成，账号ID {account_stats['aws_account'] or '-'}，地区 {','.join(account_stats['regions']) or '-'}，扫描 {account_stats['count']} 台。{error_text}",
                )
            elif account_stats['errors']:
                account.mark_status(account.STATUS_ERROR, '；'.join(account_stats['errors'][:5]))
            else:
                account.mark_status(
                    account.STATUS_OK,
                    f"AWS 同步完成，账号ID {account_stats['aws_account'] or '-'}，地区 {','.join(account_stats['regions']) or '-'}，扫描 0 台。",
                )

        after_asset_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
        self.stdout.write(self.style.SUCCESS(
            f'AWS 同步汇总：代理列表原有 {before_asset_total} 条；扫描实例/未附加IP 共 {count} 条；新增 {created_count} 条，更新 {updated_count} 条；其中未附加IP {unattached_ip_count} 条，因公网IP缺失抬为已删除 {deleted_by_missing_ip_count} 条；同步后代理列表共 {after_asset_total} 条；覆盖 {len(set(synced_regions))} 个地区/{len(accounts)} 个账号。'
        ))
        if account_summary_lines:
            self.stdout.write(f'AWS 按账号同步详情：{" || ".join(account_summary_lines)}')
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
        self.sync_errors = sync_errors
        return None
