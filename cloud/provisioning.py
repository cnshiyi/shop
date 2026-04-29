from asgiref.sync import sync_to_async
import logging
import re
from django.utils import timezone

from django.db.models import Q

from cloud.models import CloudAsset, Server
from cloud.services import build_cloud_server_name, ensure_unique_cloud_server_name, record_cloud_ip_log
from core.cloud_accounts import choose_cloud_account_for_order, cloud_account_label, list_cloud_accounts_by_server_load
from cloud.aliyun_simple import create_instance as create_aliyun_instance
from cloud.aws_lightsail import create_instance as create_aws_instance, get_instance_public_ip, move_static_ip_to_instance
from cloud.bootstrap import build_mtproxy_links, install_bbr, install_mtproxy
from cloud.models import CloudServerOrder
from cloud.ports import get_mtproxy_port_label, get_mtproxy_port_plan

logger = logging.getLogger(__name__)

_PROVISION_PROGRESS: dict[int, dict[str, object]] = {}


def _fmt_dt(value):
    return value.isoformat() if value else '-'


def set_provision_progress(order_id: int, stage: str):
    now = timezone.now()
    _PROVISION_PROGRESS[int(order_id)] = {
        'stage': stage,
        'stage_started_at': now,
        'updated_at': now,
    }
    logger.info('[PROVISION][PROGRESS] order_id=%s stage=%s', order_id, stage)


def clear_provision_progress(order_id: int):
    _PROVISION_PROGRESS.pop(int(order_id), None)


def get_provision_progress(order_id: int) -> dict[str, object]:
    return dict(_PROVISION_PROGRESS.get(int(order_id), {}))


def _extract_proxy_links(note: str) -> list[dict[str, str]]:
    links = []
    seen = set()
    main_port_match = re.search(r'端口:\s*(\d+)', note or '')
    main_port = main_port_match.group(1) if main_port_match else None
    for raw_link in re.findall(r'tg://proxy\?[^"\'\s<>]+', note or ''):
        link = raw_link.rstrip(',.，。')
        if not link or link in seen:
            continue
        seen.add(link)
        port = ''
        secret = ''
        server = ''
        if 'port=' in link:
            port = link.split('port=', 1)[1].split('&', 1)[0].strip()
        if 'secret=' in link:
            secret = link.split('secret=', 1)[1].split('&', 1)[0].strip()
        if 'server=' in link:
            server = link.split('server=', 1)[1].split('&', 1)[0].strip()
        mode = get_mtproxy_port_label(main_port or port, port) if port else 'MTProxy'
        links.append({'name': mode, 'server': server, 'port': port, 'secret': secret, 'url': link})
    return links


def _filter_duplicate_main_port_links(proxy_links: list[dict[str, str]], mtproxy_link: str, main_port: int | str | None) -> list[dict[str, str]]:
    if not mtproxy_link:
        return proxy_links
    main_port_text = str(main_port or '').strip()
    if not main_port_text:
        return proxy_links
    filtered = []
    for item in proxy_links:
        if not isinstance(item, dict):
            continue
        if str(item.get('port') or '').strip() == main_port_text and item.get('url') != mtproxy_link:
            continue
        filtered.append(item)
    return filtered


def _extract_backup_secret_from_links(proxy_links, main_port: int | str | None = None) -> str:
    backup_port = ''
    try:
        backup_port = str(get_mtproxy_port_plan(main_port or 9528)['backup'])
    except Exception:
        backup_port = ''
    for item in proxy_links or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or item.get('label') or '').lower()
        port = str(item.get('port') or '').strip()
        if backup_port and port != backup_port and 'backup' not in name and '备用' not in name and 'mtprotoproxy' not in name:
            continue
        secret = str(item.get('secret') or '').strip()
        if not secret and 'secret=' in str(item.get('url') or ''):
            secret = str(item.get('url')).split('secret=', 1)[1].split('&', 1)[0].strip()
        if secret:
            return secret
    return ''


def _extract_mtproxy_fields(note: str) -> tuple[str, str, str]:
    link = ''
    secret = ''
    host = ''
    for item in _extract_proxy_links(note):
        link = item.get('url', '')
        secret = item.get('secret', '')
        host = item.get('server', '')
        if link:
            break
    return link, secret, host


def _upsert_server_record(order: CloudServerOrder, note: str):
    try:
        order_user = order.user
    except Exception:
        order_user = None
    lookup = Q()
    if order.instance_id:
        lookup |= Q(instance_id=order.instance_id)
    if order.provider_resource_id:
        lookup |= Q(provider_resource_id=order.provider_resource_id)
    if order.public_ip:
        lookup |= Q(public_ip=order.public_ip)
    if order.id:
        lookup |= Q(order=order)
    if lookup:
        server_record = Server.objects.filter(lookup).order_by('-updated_at', '-id').first()
    else:
        server_record = None
    defaults = {
            'source': Server.SOURCE_ORDER,
            'provider': order.provider,
            'account_label': order.account_label or order.provider,
            'region_code': order.region_code,
            'region_name': order.region_name,
            'server_name': order.server_name,
            'instance_id': order.instance_id,
            'provider_resource_id': order.provider_resource_id or order.instance_id,
            'public_ip': order.public_ip,
            'previous_public_ip': order.previous_public_ip,
            'login_user': order.login_user,
            'login_password': order.login_password,
            'expires_at': order.service_expires_at,
            'order': order,
            'user': order_user,
            'note': note,
            'status': Server.STATUS_RUNNING if order.status in {'completed', 'expiring', 'renew_pending', 'suspended'} else Server.STATUS_PENDING,
            'is_active': order.status in {'provisioning', 'completed', 'expiring', 'renew_pending', 'suspended'},
        }
    if server_record:
        for key, value in defaults.items():
            setattr(server_record, key, value)
        server_record.save()
        return server_record
    return Server.objects.create(**defaults)


@sync_to_async
def _build_unique_server_name(tg_user_id: int | None, pay_amount):
    return ensure_unique_cloud_server_name(build_cloud_server_name(tg_user_id, pay_amount))


@sync_to_async
def _get_order_tg_user_id(order: CloudServerOrder):
    try:
        return getattr(order.user, 'tg_user_id', None)
    except Exception:
        return None


def _aws_order_payload(order: CloudServerOrder, *, static_ip_name: str | None = None, skip_static_ip: bool = False):
    plan = getattr(order, 'plan', None)
    return {
        'order_no': order.order_no,
        'provider': order.provider,
        'region_code': order.region_code,
        'plan_name': order.plan_name,
        'plan_id': order.plan_id,
        'config_id': getattr(plan, 'config_id', '') or '',
        'provider_plan_id': getattr(plan, 'provider_plan_id', '') or '',
        'bundle_code': getattr(plan, 'provider_plan_id', '') or '',
        'mtproxy_port': order.mtproxy_port,
        'static_ip_name': order.static_ip_name if static_ip_name is None else static_ip_name,
        'skip_static_ip': skip_static_ip,
        'cloud_account_id': order.cloud_account_id,
        'account_label': order.account_label,
    }


@sync_to_async
def _get_aws_order_payload(order: CloudServerOrder):
    return _aws_order_payload(order)


@sync_to_async
def _get_aws_create_payload(order_id: int):
    order = CloudServerOrder.objects.select_related('replacement_for').filter(id=order_id).first()
    if not order:
        return {}
    if order.replacement_for_id:
        source = order.replacement_for
        if not order.static_ip_name:
            return _aws_order_payload(order, static_ip_name='', skip_static_ip=False)
        payload = _aws_order_payload(order, static_ip_name='', skip_static_ip=True)
        payload['cloud_account_id'] = source.cloud_account_id
        payload['account_label'] = source.account_label
        return payload
    return _aws_order_payload(order)


@sync_to_async
def _get_rebuild_static_ip_context(order_id: int):
    order = CloudServerOrder.objects.select_related('replacement_for').filter(id=order_id).first()
    source = order.replacement_for if order and order.replacement_for_id else None
    if not order or not source or order.provider != 'aws_lightsail' or not order.static_ip_name or not source.static_ip_name:
        return {'is_rebuild': False}
    return {
        'is_rebuild': True,
        'source_order_id': source.id,
        'source_order_no': source.order_no,
        'original_static_ip_name': source.static_ip_name,
        'temp_static_ip_name': '',
        'source_server_name': source.server_name,
        'source_instance_id': source.instance_id,
        'payload': _aws_order_payload(order, static_ip_name='', skip_static_ip=True),
    }


@sync_to_async
def _mark_rebuild_source_pending_deletion(order_id: int, replacement_order_id: int, note: str, source_temp_public_ip: str = ''):
    now = timezone.now()
    replacement = CloudServerOrder.objects.select_related('replacement_for').filter(id=replacement_order_id).first()
    source = replacement.replacement_for if replacement and replacement.replacement_for_id else None
    if not source or source.id != order_id:
        return None
    migration_due_at = source.migration_due_at or (now + timezone.timedelta(days=3))
    previous_public_ip = source.public_ip or source.previous_public_ip
    source_temp_public_ip = str(source_temp_public_ip or '').strip()
    if not str(source.instance_id or source.provider_resource_id or '').strip():
        source.previous_public_ip = previous_public_ip
        source.public_ip = previous_public_ip
        source.mtproxy_host = previous_public_ip or source.mtproxy_host
        source.provision_note = '\n'.join(filter(None, [source.provision_note, note, f'固定 IP 保留期恢复完成，新实例订单: {replacement.order_no}，原订单保持追溯记录。']))
        source.save(update_fields=['previous_public_ip', 'public_ip', 'mtproxy_host', 'provision_note', 'updated_at'])
        CloudAsset.objects.filter(order=source).update(previous_public_ip=previous_public_ip, public_ip=previous_public_ip, note=source.provision_note, updated_at=now)
        Server.objects.filter(order=source).update(previous_public_ip=previous_public_ip, public_ip=previous_public_ip, note=source.provision_note, updated_at=now)
        record_cloud_ip_log(event_type='renewed', order=source, previous_public_ip=previous_public_ip, public_ip=previous_public_ip, note=f'固定 IP 保留期恢复完成，新实例订单: {replacement.order_no}')
        return source
    before_dates = {
        'service_expires_at': source.service_expires_at,
        'renew_grace_expires_at': source.renew_grace_expires_at,
        'suspend_at': source.suspend_at,
        'delete_at': source.delete_at,
        'ip_recycle_at': source.ip_recycle_at,
        'migration_due_at': source.migration_due_at,
    }
    source.status = 'deleting'
    source.previous_public_ip = previous_public_ip
    source.public_ip = source_temp_public_ip
    source.mtproxy_host = ''
    source.migration_due_at = migration_due_at
    source.service_expires_at = migration_due_at
    source.renew_grace_expires_at = migration_due_at
    source.suspend_at = migration_due_at
    source.delete_at = migration_due_at
    source.ip_recycle_at = migration_due_at + timezone.timedelta(days=15)
    source.provision_note = '\n'.join(filter(None, [source.provision_note, note]))
    source.save(update_fields=['status', 'previous_public_ip', 'public_ip', 'mtproxy_host', 'migration_due_at', 'service_expires_at', 'renew_grace_expires_at', 'suspend_at', 'delete_at', 'ip_recycle_at', 'provision_note', 'updated_at'])
    after_dates = {
        'service_expires_at': source.service_expires_at,
        'renew_grace_expires_at': source.renew_grace_expires_at,
        'suspend_at': source.suspend_at,
        'delete_at': source.delete_at,
        'ip_recycle_at': source.ip_recycle_at,
        'migration_due_at': source.migration_due_at,
    }
    logger.info(
        '[PROVISION][REBUILD_SOURCE_DATE_CHANGE] source_order_id=%s source_order_no=%s replacement_order_id=%s replacement_order_no=%s source_temp_public_ip=%s previous_public_ip=%s service_expires_at=%s->%s renew_grace_expires_at=%s->%s suspend_at=%s->%s delete_at=%s->%s ip_recycle_at=%s->%s migration_due_at=%s->%s',
        source.id,
        source.order_no,
        replacement.id,
        replacement.order_no,
        source_temp_public_ip,
        previous_public_ip,
        _fmt_dt(before_dates['service_expires_at']),
        _fmt_dt(after_dates['service_expires_at']),
        _fmt_dt(before_dates['renew_grace_expires_at']),
        _fmt_dt(after_dates['renew_grace_expires_at']),
        _fmt_dt(before_dates['suspend_at']),
        _fmt_dt(after_dates['suspend_at']),
        _fmt_dt(before_dates['delete_at']),
        _fmt_dt(after_dates['delete_at']),
        _fmt_dt(before_dates['ip_recycle_at']),
        _fmt_dt(after_dates['ip_recycle_at']),
        _fmt_dt(before_dates['migration_due_at']),
        _fmt_dt(after_dates['migration_due_at']),
    )
    asset = CloudAsset.objects.filter(order=source).order_by('-updated_at', '-id').first()
    server = Server.objects.filter(order=source).order_by('-updated_at', '-id').first()
    CloudAsset.objects.filter(order=source).update(
        status=CloudAsset.STATUS_DELETING,
        provider_status='旧机保留期，等待删除',
        public_ip=source_temp_public_ip or None,
        previous_public_ip=previous_public_ip,
        mtproxy_host=None,
        actual_expires_at=migration_due_at,
        is_active=False,
        note=source.provision_note,
        updated_at=now,
    )
    Server.objects.filter(order=source).update(
        status=CloudAsset.STATUS_DELETING,
        public_ip=source_temp_public_ip or None,
        previous_public_ip=previous_public_ip,
        provider_status='旧机保留期，等待删除',
        expires_at=migration_due_at,
        is_active=False,
        note=source.provision_note,
        updated_at=now,
    )
    date_note = (
        f'{note} 日期调整: '
        f'旧机临时IP {source_temp_public_ip or "未获取"}；原固定/旧IP {previous_public_ip or "-"}；'
        f'旧端口 {source.mtproxy_port or "-"}；旧secret {source.mtproxy_secret or "-"}；'
        f'服务到期 {_fmt_dt(before_dates["service_expires_at"])} -> {_fmt_dt(after_dates["service_expires_at"])}；'
        f'宽限到期 {_fmt_dt(before_dates["renew_grace_expires_at"])} -> {_fmt_dt(after_dates["renew_grace_expires_at"])}；'
        f'删机时间 {_fmt_dt(before_dates["delete_at"])} -> {_fmt_dt(after_dates["delete_at"])}；'
        f'IP保留到期 {_fmt_dt(before_dates["ip_recycle_at"])} -> {_fmt_dt(after_dates["ip_recycle_at"])}。'
    )
    record_cloud_ip_log(event_type='changed', order=source, asset=asset, server=server, previous_public_ip=previous_public_ip, public_ip=source_temp_public_ip or replacement.public_ip, note=date_note)
    return source


@sync_to_async
def _mark_instance_created(order_id: int, server_name: str, instance_id: str, public_ip: str, login_user: str, login_password: str, note: str):
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'provisioning'
    order.server_name = server_name or order.server_name
    order.instance_id = instance_id or order.instance_id
    order.provider_resource_id = instance_id or order.provider_resource_id
    order.public_ip = public_ip or order.public_ip
    order.login_user = login_user or order.login_user
    order.login_password = login_password or order.login_password
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    order.save(update_fields=['status', 'server_name', 'instance_id', 'provider_resource_id', 'public_ip', 'login_user', 'login_password', 'provision_note', 'updated_at'])
    try:
        order_user = order.user
    except Exception:
        order_user = None
    server_asset, _ = CloudAsset.objects.update_or_create(
        order=order,
        kind=CloudAsset.KIND_SERVER,
        defaults={
            'source': CloudAsset.SOURCE_ORDER,
            'provider': order.provider,
            'cloud_account': order.cloud_account,
            'account_label': order.account_label or order.provider,
            'region_code': order.region_code,
            'region_name': order.region_name,
            'asset_name': order.server_name,
            'instance_id': order.instance_id,
            'provider_resource_id': order.provider_resource_id or order.instance_id,
            'public_ip': order.public_ip,
            'login_user': order.login_user,
            'login_password': order.login_password,
            'mtproxy_port': order.mtproxy_port,
            'actual_expires_at': order.service_expires_at,
            'price': order.total_amount,
            'currency': order.currency,
            'order': order,
            'user': order_user,
            'note': order.provision_note,
            'status': CloudAsset.STATUS_PENDING,
            'is_active': True,
        },
    )
    server_record = _upsert_server_record(order, order.provision_note)
    record_cloud_ip_log(event_type='created', order=order, asset=server_asset, server=server_record, public_ip=order.public_ip, note=f'云端实例已创建：{order.server_name}')
    return order


@sync_to_async
def _mark_provisioning_start(order_id: int, server_name: str):
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'provisioning'
    order.server_name = server_name
    order.provision_note = '\n'.join(filter(None, [order.provision_note, f'开始创建服务器：{server_name}']))
    order.save(update_fields=['status', 'server_name', 'provision_note', 'updated_at'])
    try:
        order_user = order.user
    except Exception:
        order_user = None
    server_asset, _ = CloudAsset.objects.update_or_create(
        order=order,
        kind=CloudAsset.KIND_SERVER,
        defaults={
            'source': CloudAsset.SOURCE_ORDER,
            'provider': order.provider,
            'cloud_account': order.cloud_account,
            'account_label': order.account_label or order.provider,
            'region_code': order.region_code,
            'region_name': order.region_name,
            'asset_name': server_name,
            'instance_id': order.instance_id,
            'provider_resource_id': order.provider_resource_id,
            'public_ip': order.public_ip,
            'mtproxy_port': order.mtproxy_port,
            'mtproxy_link': order.mtproxy_link,
            'proxy_links': order.proxy_links or [],
            'mtproxy_secret': order.mtproxy_secret,
            'mtproxy_host': order.mtproxy_host,
            'actual_expires_at': order.service_expires_at,
            'price': order.total_amount,
            'currency': order.currency,
            'order': order,
            'user': order_user,
            'note': order.provision_note,
            'status': CloudAsset.STATUS_PENDING,
            'is_active': True,
        },
    )
    server_record = _upsert_server_record(order, order.provision_note)
    record_cloud_ip_log(event_type='created', order=order, asset=server_asset, server=server_record, public_ip=order.public_ip, note=f'服务器开始创建：{server_name}')
    return order


def _is_transient_create_failure(note: str) -> bool:
    text = str(note or '').lower()
    transient_markers = [
        'read timed out',
        'connect timeout',
        'connection timeout',
        'timeout',
        'temporarily unavailable',
        'internalerror',
        'service unavailable',
        'too many requests',
    ]
    return any(marker in text for marker in transient_markers)


async def provision_cloud_server(order_id: int):
    started_at = timezone.now()
    logger.info('云服务器开通开始: order_id=%s', order_id)
    try:
        order = await _ensure_order_cloud_account(order_id)
        if not order:
            logger.warning('云服务器开通失败: 订单不存在 order_id=%s', order_id)
            return None

        order_tg_user_id = await _get_order_tg_user_id(order)

        server_name = await _build_unique_server_name(order_tg_user_id, order.pay_amount)
        logger.info(
            '云服务器开通准备完成: order_id=%s order_no=%s status=%s provider=%s region=%s plan=%s qty=%s currency=%s pay_amount=%s mtproxy_port=%s server_name=%s user_id=%s tg_user_id=%s',
            order.id,
            order.order_no,
            order.status,
            order.provider,
            order.region_code,
            order.plan_name,
            order.quantity,
            order.currency,
            order.pay_amount,
            order.mtproxy_port,
            server_name,
            order.user_id,
            order_tg_user_id,
        )

        set_provision_progress(order.id, '准备创建云服务器')
        await _mark_provisioning_start(order.id, server_name)

        account_ids = await _candidate_cloud_account_ids(order.id)
        if not account_ids:
            account_ids = [None]
        result = None
        login_user = 'root'
        attempted_notes = []
        for attempt_index, account_id in enumerate(account_ids, start=1):
            order = await _set_order_cloud_account(order.id, account_id)
            await _mark_provisioning_start(order.id, server_name)
            account_label = order.account_label or order.provider
            create_attempts = 2
            for create_attempt in range(1, create_attempts + 1):
                if order.provider == 'aws_lightsail':
                    set_provision_progress(order.id, '创建 AWS Lightsail 实例')
                    logger.info('云服务器创建开始: order=%s provider=AWS Lightsail account=%s account_attempt=%s/%s create_attempt=%s/%s server_name=%s', order.order_no, account_label, attempt_index, len(account_ids), create_attempt, create_attempts, server_name)
                    result = await create_aws_instance(await _get_aws_create_payload(order.id), server_name)
                    login_user = 'admin'
                else:
                    set_provision_progress(order.id, '创建云服务器实例')
                    logger.info('云服务器创建开始: order=%s provider=%s account=%s account_attempt=%s/%s create_attempt=%s/%s server_name=%s', order.order_no, order.provider, account_label, attempt_index, len(account_ids), create_attempt, create_attempts, server_name)
                    result = await create_aliyun_instance(order, server_name)
                    login_user = 'root'
                logger.info(
                    '云服务器创建结果: order=%s account=%s ok=%s instance_id=%s public_ip=%s login_user=%s note=%s',
                    order.order_no,
                    account_label,
                    result.ok,
                    result.instance_id,
                    result.public_ip,
                    result.login_user or login_user,
                    (result.note or '')[:1000],
                )
                if result.ok:
                    break
                if create_attempt < create_attempts and _is_transient_create_failure(result.note):
                    logger.warning('云服务器创建遇到临时错误，先重试当前账号: order=%s account=%s note=%s', order.order_no, account_label, (result.note or '')[:1000])
                    continue
                break
            if result.ok:
                break
            attempted_notes.append(f'{account_label}: {result.note}')
            if attempt_index < len(account_ids):
                logger.warning('云服务器创建失败，切换下一个账号重试: order=%s account=%s note=%s', order.order_no, account_label, (result.note or '')[:1000])

        if result and not result.ok and attempted_notes:
            result.note = '多账号创建均失败：\n' + '\n'.join(attempted_notes)

        if result.ok:
            bootstrap_user = result.login_user or login_user
            order = await _mark_instance_created(
                order.id,
                server_name,
                result.instance_id,
                result.public_ip,
                bootstrap_user,
                result.login_password,
                result.note,
            )
            final_public_ip = result.public_ip
            final_static_ip_name = getattr(result, 'static_ip_name', '') or ''
            rebuild_context = await _get_rebuild_static_ip_context(order.id)
            move_note = ''
            source_temp_public_ip = ''
            if rebuild_context.get('is_rebuild'):
                set_provision_progress(order.id, '迁移固定公网 IP')
                ok_move, moved_ip, move_note = await move_static_ip_to_instance(
                    rebuild_context['payload'],
                    result.instance_id,
                    rebuild_context['original_static_ip_name'],
                    rebuild_context.get('temp_static_ip_name') or final_static_ip_name,
                )
                if not ok_move:
                    note = '\n'.join(part for part in [result.note, move_note] if part)
                    logger.warning('云服务器重建失败: order=%s reason=static_ip_move_failed note=%s', order.order_no, move_note)
                    saved = await _mark_failed(order_id, note)
                    clear_provision_progress(order_id)
                    print('[PROVISION_RESULT]', {'order_id': saved.id, 'order_no': saved.order_no, 'status': saved.status, 'error': saved.provision_note})
                    return saved
                final_public_ip = moved_ip or final_public_ip
                final_static_ip_name = rebuild_context['original_static_ip_name']
                source_instance_name = rebuild_context.get('source_server_name') or rebuild_context.get('source_instance_id') or ''
                source_temp_public_ip = await get_instance_public_ip(rebuild_context['payload'], source_instance_name) if source_instance_name else ''
                logger.info('云服务器重建固定 IP 已先迁移，后续安装使用正式 IP: order=%s install_ip=%s instance_id=%s source_instance=%s source_temp_public_ip=%s', order.order_no, final_public_ip, result.instance_id, source_instance_name, source_temp_public_ip)

            set_provision_progress(order.id, '安装 BBR')
            logger.info('开始执行 BBR 初始化: order=%s public_ip=%s user=%s requested_user=%s', order.order_no, final_public_ip, bootstrap_user, bootstrap_user)
            private_key_path = getattr(result, 'private_key_path', '') or ''
            bbr_ok, bbr_note = await install_bbr(final_public_ip, bootstrap_user, result.login_password, private_key_path, use_key_setup=bool(private_key_path) or order.provider == 'aws_lightsail')
            logger.info('BBR 初始化结果: order=%s ok=%s note=%s', order.order_no, bbr_ok, (bbr_note or '')[:1000])

            set_provision_progress(order.id, '安装 MTProxy 主/备用/Telemt')
            logger.info('开始执行 MTProxy 安装: order=%s public_ip=%s user=%s port=%s requested_user=%s', order.order_no, final_public_ip, bootstrap_user, order.mtproxy_port, bootstrap_user)
            mtproxy_ok, mtproxy_note = await install_mtproxy(final_public_ip, bootstrap_user, result.login_password, order.mtproxy_port, order.mtproxy_secret or '', order.mtproxy_secret or '')
            logger.info('MTProxy 安装结果: order=%s ok=%s note=%s', order.order_no, mtproxy_ok, (mtproxy_note or '')[:1000])

            bbr_warning = '' if bbr_ok else 'BBR 初始化失败，但 MTProxy 已安装成功，订单按代理可用处理。'
            note = '\n'.join(part for part in [result.note, move_note, bbr_warning, bbr_note, mtproxy_note] if part)
            if not mtproxy_ok:
                logger.warning(
                    '云服务器开通失败: order=%s reason=mtproxy_failed bbr_ok=%s mtproxy_ok=%s elapsed_seconds=%s',
                    order.order_no,
                    bbr_ok,
                    mtproxy_ok,
                    (timezone.now() - started_at).total_seconds(),
                )
                saved = await _mark_failed(order_id, note)
                clear_provision_progress(order_id)
                logger.warning('云服务器开通结束: order=%s status=%s note=%s', saved.order_no, saved.status, (saved.provision_note or '')[:1500])
                print('[PROVISION_RESULT]', {'order_id': saved.id, 'order_no': saved.order_no, 'status': saved.status, 'error': saved.provision_note})
                return saved

            set_provision_progress(order.id, '保存开通结果')
            saved = await _mark_success(
                order_id,
                server_name,
                result.instance_id,
                final_public_ip,
                result.login_user or login_user,
                result.login_password,
                note,
                final_static_ip_name,
            )
            if rebuild_context.get('is_rebuild'):
                await _mark_rebuild_source_pending_deletion(
                    rebuild_context['source_order_id'],
                    saved.id,
                    f'重建成功，新实例订单: {saved.order_no}，固定 IP 已迁移到新实例；旧机临时 IP {source_temp_public_ip or "未获取"}，按新到期时间保留并在宽限期后删除。',
                    source_temp_public_ip,
                )
            logger.info(
                '云服务器开通成功: order=%s status=%s provider=%s region=%s server_name=%s instance_id=%s public_ip=%s mtproxy_host=%s mtproxy_port=%s mtproxy_link=%s expires_at=%s elapsed_seconds=%s',
                saved.order_no,
                saved.status,
                saved.provider,
                saved.region_code,
                saved.server_name,
                saved.instance_id,
                saved.public_ip,
                saved.mtproxy_host,
                saved.mtproxy_port,
                saved.mtproxy_link,
                saved.service_expires_at,
                (timezone.now() - started_at).total_seconds(),
            )
            clear_provision_progress(order_id)
            print(
                '[PROVISION_RESULT]',
                {
                    'order_id': saved.id,
                    'order_no': saved.order_no,
                    'status': saved.status,
                    'provider': saved.provider,
                    'region': saved.region_code,
                    'server_name': saved.server_name,
                    'instance_id': saved.instance_id,
                    'public_ip': saved.public_ip,
                    'mtproxy_port': saved.mtproxy_port,
                    'mtproxy_link': saved.mtproxy_link,
                    'service_expires_at': saved.service_expires_at.isoformat() if saved.service_expires_at else None,
                },
            )
            return saved

        logger.warning(
            '云服务器开通失败: order=%s reason=create_failed note=%s elapsed_seconds=%s',
            order.order_no,
            (result.note or '')[:1500],
            (timezone.now() - started_at).total_seconds(),
        )
        saved = await _mark_failed(order_id, result.note)
        clear_provision_progress(order_id)
        logger.warning('云服务器开通结束: order=%s status=%s note=%s', saved.order_no, saved.status, (saved.provision_note or '')[:1500])
        print('[PROVISION_RESULT]', {'order_id': saved.id, 'order_no': saved.order_no, 'status': saved.status, 'error': saved.provision_note})
        return saved
    except Exception as exc:
        logger.exception('云服务器开通异常: order_id=%s error=%s', order_id, exc)
        try:
            saved = await _mark_failed(order_id, f'云服务器开通异常: {exc}')
            clear_provision_progress(order_id)
            logger.warning('云服务器开通异常结束: order=%s status=%s note=%s', saved.order_no, saved.status, (saved.provision_note or '')[:1500])
            print('[PROVISION_RESULT]', {'order_id': saved.id, 'order_no': saved.order_no, 'status': saved.status, 'error': saved.provision_note})
            return saved
        except Exception:
            logger.exception('云服务器开通异常后回写失败: order_id=%s', order_id)
            raise


async def reprovision_cloud_server_bootstrap(order_id: int):
    order = await _get_order(order_id)
    if not order:
        return None
    if not order.public_ip or not order.login_password:
        saved = await _mark_failed(order_id, '重试初始化失败：缺少公网 IP 或登录密码。')
        clear_provision_progress(order_id)
        return saved
    bootstrap_user = order.login_user or 'root'
    logger.info('[PROVISION][RETRY] start order=%s public_ip=%s user=%s port=%s', order.order_no, order.public_ip, bootstrap_user, order.mtproxy_port)
    set_provision_progress(order.id, '安装 BBR')
    bbr_ok, bbr_note = await install_bbr(order.public_ip, bootstrap_user, order.login_password, use_key_setup=order.provider == 'aws_lightsail')
    logger.info('[PROVISION][RETRY] bbr_result order=%s ok=%s note=%s', order.order_no, bbr_ok, (bbr_note or '')[:1000])
    set_provision_progress(order.id, '安装 MTProxy 主/备用/Telemt')
    mtproxy_ok, mtproxy_note = await install_mtproxy(order.public_ip, bootstrap_user, order.login_password, order.mtproxy_port, order.mtproxy_secret or '', order.mtproxy_secret or '')
    logger.info('[PROVISION][RETRY] mtproxy_result order=%s ok=%s note=%s', order.order_no, mtproxy_ok, (mtproxy_note or '')[:1000])
    bbr_warning = '' if bbr_ok else 'BBR 初始化失败，但 MTProxy 已安装成功，订单按代理可用处理。'
    note = '\n'.join(part for part in [order.provision_note, '已执行重试初始化。', bbr_warning, bbr_note, mtproxy_note] if part)
    if not mtproxy_ok:
        saved = await _mark_failed(order_id, note)
        clear_provision_progress(order_id)
        return saved
    set_provision_progress(order.id, '保存初始化结果')
    saved = await _mark_success(order_id, order.server_name or order.instance_id or '', order.instance_id or order.provider_resource_id or '', order.public_ip, order.login_user or 'root', order.login_password, note, order.static_ip_name or '')
    clear_provision_progress(order_id)
    return saved


@sync_to_async
def _candidate_cloud_account_ids(order_id: int):
    order = CloudServerOrder.objects.select_related('replacement_for').filter(id=order_id).first()
    if not order:
        return []
    if order.replacement_for_id:
        account_id = getattr(order.replacement_for, 'cloud_account_id', None) or order.cloud_account_id
        return [account_id] if account_id else []
    accounts = list_cloud_accounts_by_server_load(order.provider, order.region_code)
    ids = [account.id for account in accounts]
    if order.cloud_account_id and order.cloud_account_id not in ids:
        ids.insert(0, order.cloud_account_id)
    return ids


@sync_to_async
def _set_order_cloud_account(order_id: int, account_id: int | None):
    order = CloudServerOrder.objects.filter(id=order_id).first()
    if not order:
        return None
    if account_id:
        from core.models import CloudAccountConfig
        account = CloudAccountConfig.objects.filter(id=account_id, is_active=True).first()
    else:
        account = None
    order.cloud_account = account
    order.account_label = cloud_account_label(account) or order.provider
    order.save(update_fields=['cloud_account', 'account_label', 'updated_at'])
    return order


@sync_to_async
def _ensure_order_cloud_account(order_id: int):
    order = CloudServerOrder.objects.filter(id=order_id).first()
    if not order:
        return None
    if not order.cloud_account_id:
        account = choose_cloud_account_for_order(order.provider, order.region_code)
        if account:
            order.cloud_account = account
            order.account_label = cloud_account_label(account) or order.provider
            order.save(update_fields=['cloud_account', 'account_label', 'updated_at'])
    elif not order.account_label:
        order.account_label = cloud_account_label(order.cloud_account) or order.provider
        order.save(update_fields=['account_label', 'updated_at'])
    return order


@sync_to_async
def _get_order(order_id: int):
    return CloudServerOrder.objects.filter(id=order_id).first()


@sync_to_async
def _mark_success(order_id: int, server_name: str, instance_id: str, public_ip: str, login_user: str, login_password: str, note: str, static_ip_name: str = ''):
    logger.info('[PROVISION] mark_success_start order_id=%s server_name=%s instance_id=%s public_ip=%s', order_id, server_name, instance_id, public_ip)
    order = CloudServerOrder.objects.get(id=order_id)
    existing_mtproxy_link = order.mtproxy_link
    existing_mtproxy_secret = order.mtproxy_secret
    existing_proxy_links = list(order.proxy_links or [])
    mtproxy_link, mtproxy_secret, mtproxy_host = _extract_mtproxy_fields(note)
    proxy_links = _extract_proxy_links(note)
    mtproxy_secret = mtproxy_secret or existing_mtproxy_secret
    mtproxy_link = mtproxy_link or existing_mtproxy_link
    if not mtproxy_link and mtproxy_secret:
        mtproxy_link, _ = build_mtproxy_links(public_ip, order.mtproxy_port or 9528, mtproxy_secret)
    if not proxy_links:
        proxy_links = existing_proxy_links
    proxy_links = _filter_duplicate_main_port_links(proxy_links, mtproxy_link, order.mtproxy_port or 9528)
    if mtproxy_link and not any(isinstance(item, dict) and item.get('url') == mtproxy_link for item in proxy_links):
        proxy_links.insert(0, {
            'name': '主代理 mtg',
            'server': public_ip,
            'port': str(order.mtproxy_port or 9528),
            'secret': mtproxy_secret or '',
            'url': mtproxy_link,
        })
    order.status = 'completed'
    order.server_name = server_name
    order.instance_id = instance_id
    order.provider_resource_id = instance_id
    order.public_ip = public_ip
    order.mtproxy_host = mtproxy_host or public_ip
    order.mtproxy_link = mtproxy_link
    order.proxy_links = proxy_links
    order.mtproxy_secret = mtproxy_secret
    order.login_user = login_user
    order.login_password = login_password
    order.provision_note = note
    order.static_ip_name = static_ip_name or order.static_ip_name
    order.completed_at = timezone.now()
    if not order.service_started_at:
        order.service_started_at = order.completed_at
    if not order.service_expires_at:
        order.service_expires_at = order.completed_at + timezone.timedelta(days=order.lifecycle_days or 31)
    try:
        order.last_user_id = order.user.tg_user_id
    except Exception:
        order.last_user_id = order.user_id or 0
    order.save(update_fields=['status', 'server_name', 'instance_id', 'provider_resource_id', 'public_ip', 'mtproxy_host', 'mtproxy_link', 'proxy_links', 'mtproxy_secret', 'static_ip_name', 'login_user', 'login_password', 'provision_note', 'completed_at', 'service_started_at', 'service_expires_at', 'last_user_id', 'updated_at'])
    logger.info('[PROVISION] order_saved order=%s status=%s service_started_at=%s service_expires_at=%s mtproxy_host=%s mtproxy_link=%s', order.order_no, order.status, order.service_started_at, order.service_expires_at, order.mtproxy_host, order.mtproxy_link)

    try:
        try:
            order_user = order.user
        except Exception:
            order_user = None
        server_asset, _ = CloudAsset.objects.update_or_create(
            order=order,
            kind=CloudAsset.KIND_SERVER,
            defaults={
                'source': CloudAsset.SOURCE_ORDER,
                'provider': order.provider,
                'cloud_account': order.cloud_account,
                'account_label': order.account_label or order.provider,
                'region_code': order.region_code,
                'region_name': order.region_name,
                'asset_name': server_name,
                'instance_id': instance_id,
                'provider_resource_id': instance_id,
                'public_ip': public_ip,
                'login_user': login_user,
                'login_password': login_password,
                'mtproxy_port': order.mtproxy_port,
                'mtproxy_link': mtproxy_link,
                'proxy_links': proxy_links,
                'mtproxy_secret': mtproxy_secret,
                'mtproxy_host': mtproxy_host or public_ip,
                'actual_expires_at': order.service_expires_at,
                'price': order.total_amount,
                'currency': order.currency,
                'order': order,
                'user': order_user,
                'note': note,
                'status': CloudAsset.STATUS_RUNNING,
                'is_active': True,
            },
        )
        server_record = _upsert_server_record(order, note)
        record_cloud_ip_log(
            event_type='created',
            order=order,
            asset=server_asset,
            server=server_record,
            public_ip=public_ip,
            note=f'服务器创建并分配IP：{public_ip or "未分配"}',
        )
        logger.info('[PROVISION] server_asset_saved order=%s asset_id=%s server_record_id=%s expires_at=%s host=%s port=%s link=%s', order.order_no, server_asset.id, getattr(server_record, 'id', None), order.service_expires_at, mtproxy_host or public_ip, order.mtproxy_port, mtproxy_link)
    except Exception as exc:
        logger.exception('[PROVISION] asset_sync_failed order=%s error=%s', order.order_no, exc)
    return order


@sync_to_async
def _mark_failed(order_id: int, note: str):
    logger.info('[PROVISION] mark_failed_start order_id=%s note=%s', order_id, (note or '')[:1500])
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'failed'
    order.provision_note = note
    order.save(update_fields=['status', 'provision_note', 'updated_at'])
    server_record = _upsert_server_record(order, note)
    CloudAsset.objects.filter(order=order).update(note=note, status=CloudAsset.STATUS_UNKNOWN, is_active=False, updated_at=timezone.now())
    Server.objects.filter(order=order).update(note=note, status=Server.STATUS_UNKNOWN, is_active=False, updated_at=timezone.now())
    logger.info('[PROVISION] failed_server_record_synced order=%s server_record_id=%s', order.order_no, getattr(server_record, 'id', None))
    logger.info('[PROVISION] mark_failed_done order=%s status=%s', order.order_no, order.status)
    return order
