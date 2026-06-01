from asgiref.sync import sync_to_async
import logging
import re
from urllib.parse import urlparse

from django.utils import timezone

from django.db.models import Q

from cloud.models import CloudAsset
from cloud.note_utils import append_note, prepend_note, with_note_time
from cloud.services import _cloud_log_trigger_label, _resolve_aws_static_ip_name_for_order, _update_order_primary_records, build_cloud_server_name, drop_asset_note_update, ensure_unique_cloud_server_name, is_cloud_asset_renewal_order, record_cloud_ip_log
from core.cloud_accounts import choose_cloud_account_for_order, cloud_account_label, list_cloud_accounts_by_server_load
from cloud.aliyun_simple import create_instance as create_aliyun_instance
from cloud.aws_lightsail import create_instance as create_aws_instance, get_instance_public_ip, move_static_ip_to_instance, public_ip_exists
from cloud.bootstrap import build_mtproxy_links, install_bbr, install_mtproxy
from cloud.ip_guard import validate_server_connection_ip, validate_server_connection_ip_with_retry
from cloud.models import CloudServerOrder
from cloud.ports import get_mtproxy_port_label, get_mtproxy_port_plan

logger = logging.getLogger(__name__)

_PROVISION_PROGRESS: dict[int, dict[str, object]] = {}


def _mask_log_value(value, visible=4):
    text = str(value or '')
    if not text:
        return ''
    if len(text) <= visible * 2:
        return '*' * len(text)
    return f'{text[:visible]}****{text[-visible:]}'


def _log_provision_result(order, *, level=logging.INFO, **extra):
    payload = {
        'order_id': getattr(order, 'id', None),
        'order_no': getattr(order, 'order_no', ''),
        'status': getattr(order, 'status', ''),
        'provider': getattr(order, 'provider', ''),
        'region': getattr(order, 'region_code', ''),
        'server_name': getattr(order, 'server_name', ''),
        'instance_id': getattr(order, 'instance_id', ''),
        'public_ip': getattr(order, 'public_ip', ''),
        'mtproxy_port': getattr(order, 'mtproxy_port', None),
        'mtproxy_host': getattr(order, 'mtproxy_host', ''),
        'mtproxy_link_preview': _mask_log_value(getattr(order, 'mtproxy_link', ''), visible=12),
        'service_expires_at': getattr(order, 'service_expires_at', None).isoformat() if getattr(order, 'service_expires_at', None) else None,
        **extra,
    }
    logger.log(
        level,
        '[PROVISION_RESULT] order_id=%s order_no=%s status=%s provider=%s region=%s instance_id=%s public_ip=%s mtproxy_port=%s error=%s',
        payload['order_id'],
        payload['order_no'],
        payload['status'],
        payload['provider'],
        payload['region'],
        payload['instance_id'],
        payload['public_ip'],
        payload['mtproxy_port'],
        str(payload.get('error') or '')[:1500],
        extra={'provision_result': payload},
    )


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
    for raw_link in re.findall(r'socks5://[^"\'\s<>]+', note or ''):
        link = raw_link.rstrip(',.，。')
        if not link or link in seen:
            continue
        parsed = urlparse(link)
        port = str(parsed.port or '')
        server = parsed.hostname or ''
        username = parsed.username or ''
        password = parsed.password or ''
        seen.add(link)
        links.append({
            'name': get_mtproxy_port_label(main_port or port, port) if port else 'SOCKS5',
            'server': server,
            'port': port,
            'username': username,
            'password': password,
            'secret': password or username,
            'url': link,
        })
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
        if not str(link).startswith('tg://proxy?'):
            continue
        secret = item.get('secret', '')
        host = item.get('server', '')
        if link:
            break
    return link, secret, host


def _compact_proxy_install_note(note: str, proxy_links: list[dict[str, str]], main_port: int | str | None = None) -> str:
    if (
        'MTProxy 安装完成' not in str(note or '')
        and 'SOCKS5链接:' not in str(note or '')
        and 'socks5://' not in str(note or '')
        and 'tg://proxy?' not in str(note or '')
    ):
        return note
    prefix = str(note or '').split('MTProxy 安装完成', 1)[0].strip()
    mtproxy_ports = []
    socks5_port = ''
    for item in proxy_links or []:
        if not isinstance(item, dict):
            continue
        port = str(item.get('port') or '').strip()
        url = str(item.get('url') or '')
        if not port:
            continue
        if url.startswith('socks5://'):
            socks5_port = port
        elif port not in mtproxy_ports:
            mtproxy_ports.append(port)
    lines = [
        'MTProxy/SOCKS5 安装完成',
        f'主代理端口: {main_port or (mtproxy_ports[0] if mtproxy_ports else "-")}',
    ]
    extra_mtproxy_ports = [port for port in mtproxy_ports if str(port) != str(main_port or '')]
    if extra_mtproxy_ports:
        lines.append(f'备用/Telemt端口: {", ".join(extra_mtproxy_ports)}')
    if socks5_port:
        lines.append(f'SOCKS5端口: {socks5_port}')
    lines.append('代理链接已保存到代理链路列表。')
    compact = '\n'.join(lines)
    return '\n'.join(part for part in [prefix, compact] if part)


def _strip_raw_proxy_link_lines(note: str | None) -> str:
    lines = []
    for raw_line in str(note or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if 'tg://proxy?' in line or 'socks5://' in line:
            continue
        if line.startswith(('TG链接:', '分享链接:', '扩展链接:', 'SOCKS5链接:')):
            continue
        lines.append(line)
    return '\n'.join(lines)


def _append_cloud_asset_note(existing: str | None, addition: str | None, proxy_links: list[dict[str, str]], main_port: int | str | None = None) -> str:
    clean_addition = _compact_proxy_install_note(addition or '', proxy_links, main_port)
    clean_addition = _strip_raw_proxy_link_lines(clean_addition)
    return append_note(existing, clean_addition)


def _upsert_server_asset(order: CloudServerOrder, note: str):
    try:
        order_user = order.user
    except Exception:
        order_user = None
    server_asset = None
    if order.id:
        same_order_lookup = Q(order=order)
        if order.instance_id:
            same_order_lookup |= Q(instance_id=order.instance_id)
        if order.provider_resource_id:
            same_order_lookup |= Q(provider_resource_id=order.provider_resource_id)
        if order.server_name:
            same_order_lookup |= Q(asset_name=order.server_name)
        server_asset = (
            CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).filter(same_order_lookup)
            .filter(Q(order__isnull=True) | Q(order=order))
            .order_by('-updated_at', '-id')
            .first()
        )
    if not server_asset and order.public_ip:
        server_asset = (
            CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).filter(Q(public_ip=order.public_ip) | Q(previous_public_ip=order.public_ip))
            .filter(Q(order__isnull=True) | Q(order=order))
            .order_by('-updated_at', '-id')
            .first()
        )
    defaults = {
            'kind': CloudAsset.KIND_SERVER,
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
            'previous_public_ip': order.previous_public_ip,
            'login_user': order.login_user,
            'login_password': order.login_password,
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
            'status': CloudAsset.STATUS_RUNNING if order.status in {'completed', 'expiring', 'renew_pending', 'suspended'} else CloudAsset.STATUS_PENDING,
            'is_active': order.status in {'provisioning', 'completed', 'expiring', 'renew_pending', 'suspended'},
        }
    if server_asset:
        preserved_values = {
            'user': server_asset.user if server_asset.user_id else defaults.get('user'),
            'actual_expires_at': server_asset.actual_expires_at or defaults.get('actual_expires_at'),
            'mtproxy_link': server_asset.mtproxy_link or defaults.get('mtproxy_link'),
            'proxy_links': server_asset.proxy_links or defaults.get('proxy_links'),
            'mtproxy_secret': server_asset.mtproxy_secret or defaults.get('mtproxy_secret'),
            'mtproxy_host': server_asset.mtproxy_host or defaults.get('mtproxy_host'),
            'mtproxy_port': server_asset.mtproxy_port or defaults.get('mtproxy_port'),
            'price': server_asset.price if server_asset.price is not None else defaults.get('price'),
            'currency': server_asset.currency or defaults.get('currency'),
        }
        for key, value in defaults.items():
            if key in preserved_values:
                value = preserved_values[key]
            setattr(server_asset, key, value)
        server_asset.save()
        return server_asset
    return CloudAsset.objects.create(**defaults)


@sync_to_async
def _build_unique_server_name(tg_user_id: int | None, pay_amount, order_id: int | None = None):
    unique_tag = f'o{order_id}' if order_id else None
    return ensure_unique_cloud_server_name(build_cloud_server_name(tg_user_id, pay_amount, unique_tag))


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
        'public_ip': order.public_ip,
        'previous_public_ip': order.previous_public_ip,
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
    original_static_ip_name = source.static_ip_name
    resolved_static_ip_name = _resolve_aws_static_ip_name_for_order(source)
    if resolved_static_ip_name and resolved_static_ip_name != original_static_ip_name:
        source.static_ip_name = resolved_static_ip_name
        source.save(update_fields=['static_ip_name', 'updated_at'])
        order.static_ip_name = resolved_static_ip_name
        order.save(update_fields=['static_ip_name', 'updated_at'])
        original_static_ip_name = resolved_static_ip_name
    payload = _aws_order_payload(order, static_ip_name='', skip_static_ip=True)
    payload['original_public_ip'] = source.public_ip or source.previous_public_ip
    return {
        'is_rebuild': True,
        'source_order_id': source.id,
        'source_order_no': source.order_no,
        'original_static_ip_name': original_static_ip_name,
        'temp_static_ip_name': '',
        'source_server_name': source.server_name,
        'source_instance_id': source.instance_id,
        'payload': payload,
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
    if not str(source.instance_id or source.provider_resource_id or '').strip() and ('未附加固定 IP' in (source.provision_note or '') or '固定 IP 保留' in (source.provision_note or '')):
        source.previous_public_ip = previous_public_ip
        source.public_ip = previous_public_ip
        source.mtproxy_host = previous_public_ip or source.mtproxy_host
        source.provision_note = '\n'.join(filter(None, [source.provision_note, note, f'固定 IP 保留期恢复完成，新实例订单: {replacement.order_no}，原订单保持追溯记录。']))
        source.save(update_fields=['previous_public_ip', 'public_ip', 'mtproxy_host', 'provision_note', 'updated_at'])
        _update_order_primary_records(
            source,
            asset_updates={'previous_public_ip': previous_public_ip, 'public_ip': previous_public_ip},
            server_updates={'previous_public_ip': previous_public_ip, 'public_ip': previous_public_ip},
            now=now,
        )
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
    delete_at = migration_due_at + timezone.timedelta(days=3)
    source.status = 'deleting'
    source.previous_public_ip = previous_public_ip
    source.public_ip = source_temp_public_ip
    source.mtproxy_host = ''
    source.migration_due_at = migration_due_at
    source.service_expires_at = migration_due_at
    source.renew_grace_expires_at = delete_at
    source.suspend_at = delete_at
    source.delete_at = delete_at
    source.ip_recycle_at = delete_at + timezone.timedelta(days=15)
    source.provision_note = '\n'.join(filter(None, [source.provision_note, note]))
    CloudServerOrder.objects.filter(id=source.id).update(
        status=source.status,
        previous_public_ip=source.previous_public_ip,
        public_ip=source.public_ip,
        mtproxy_host=source.mtproxy_host,
        migration_due_at=source.migration_due_at,
        service_expires_at=source.service_expires_at,
        renew_grace_expires_at=source.renew_grace_expires_at,
        suspend_at=source.suspend_at,
        delete_at=source.delete_at,
        ip_recycle_at=source.ip_recycle_at,
        provision_note=source.provision_note,
        updated_at=now,
    )
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
    asset, server = _update_order_primary_records(
        source,
        asset_updates={
            'status': CloudAsset.STATUS_DELETING,
            'provider_status': '旧机保留期，等待删除',
            'public_ip': source_temp_public_ip or None,
            'previous_public_ip': previous_public_ip,
            'mtproxy_host': None,
            'actual_expires_at': migration_due_at,
            'is_active': False,
        },
        server_updates={
            'status': CloudAsset.STATUS_DELETING,
            'public_ip': source_temp_public_ip or None,
            'previous_public_ip': previous_public_ip,
            'provider_status': '旧机保留期，等待删除',
            'expires_at': migration_due_at,
            'is_active': False,
        },
        now=now,
    )
    action_label = _cloud_log_trigger_label(replacement)
    date_note = (
        f'{action_label}成功后旧服务器生命周期已更新：'
        f'旧机订单 {source.order_no}；新实例订单 {replacement.order_no}；'
        f'旧机临时IP {source_temp_public_ip or "未获取"}；原固定/旧IP {previous_public_ip or "-"}；'
        f'旧端口 {source.mtproxy_port or "-"}；旧secret {source.mtproxy_secret or "-"}；'
        f'处理结果：固定 IP 已迁移到新实例，旧服务器进入保留期，等待宽限期后删除；'
        f'服务到期 {_fmt_dt(before_dates["service_expires_at"])} -> {_fmt_dt(after_dates["service_expires_at"])}；'
        f'宽限到期 {_fmt_dt(before_dates["renew_grace_expires_at"])} -> {_fmt_dt(after_dates["renew_grace_expires_at"])}；'
        f'删机时间 {_fmt_dt(before_dates["delete_at"])} -> {_fmt_dt(after_dates["delete_at"])}；'
        f'IP保留到期 {_fmt_dt(before_dates["ip_recycle_at"])} -> {_fmt_dt(after_dates["ip_recycle_at"])}。'
    )
    record_cloud_ip_log(event_type='changed', order=source, asset=asset, previous_public_ip=previous_public_ip, public_ip=source_temp_public_ip or replacement.public_ip, note=date_note, trigger_label=action_label)
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
    current_note = with_note_time(note)
    order.provision_note = prepend_note(order.provision_note, current_note)
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
            'status': CloudAsset.STATUS_PENDING,
            'is_active': True,
        },
    )
    trigger_label = _cloud_log_trigger_label(order)
    record_cloud_ip_log(event_type='created', order=order, asset=server_asset, public_ip=order.public_ip, note=f'{trigger_label}触发创建云端实例：{order.server_name}')
    return order


@sync_to_async
def _mark_provisioning_start(order_id: int, server_name: str):
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'provisioning'
    order.server_name = server_name
    current_note = with_note_time(f'开始创建服务器：{server_name}')
    order.provision_note = prepend_note(order.provision_note, current_note)
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
            'status': CloudAsset.STATUS_PENDING,
            'is_active': True,
        },
    )
    record_cloud_ip_log(event_type='created', order=order, asset=server_asset, public_ip=order.public_ip, note=f'服务器开始创建：{server_name}')
    return order


FAILED_INSTANCE_CLEANUP_DELAY = timezone.timedelta(days=1)


def _failed_instance_cleanup_note(cleanup_at):
    return f'创建流程未完成，已计划在 {timezone.localtime(cleanup_at):%Y-%m-%d %H:%M} 自动删除失败新实例。'


def _cloud_created_server_name(provider: str, requested_server_name: str, result) -> str:
    if provider == 'aws_lightsail':
        return str(getattr(result, 'instance_id', '') or requested_server_name or '').strip()
    return str(requested_server_name or '').strip()


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

        server_name = await _build_unique_server_name(order_tg_user_id, order.pay_amount, order.id)
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
            if is_cloud_asset_renewal_order(order):
                note = '未附加固定 IP 恢复失败：原固定 IP 所属云账号不可用，已停止自动创建。'
                logger.warning('云服务器开通失败: order=%s reason=asset_recovery_missing_cloud_account', order.order_no)
                saved = await _mark_failed(order.id, note)
                return saved
            if order.provider == 'aws_lightsail':
                note = 'AWS 创建失败：没有可用的后台云账号，已拒绝回退默认账号或环境变量创建实例。'
                logger.warning('云服务器开通失败: order=%s reason=aws_missing_cloud_account', order.order_no)
                saved = await _mark_failed(order.id, note)
                return saved
            if order.provider == 'aliyun_simple':
                note = '阿里云创建失败：没有可用的后台云账号，已拒绝回退默认账号或环境变量创建实例。'
                logger.warning('云服务器开通失败: order=%s reason=aliyun_missing_cloud_account', order.order_no)
                saved = await _mark_failed(order.id, note)
                return saved
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
                if getattr(result, 'instance_id', ''):
                    logger.warning('云服务器实例已创建但流程未完成，停止账号轮询并等待失败清理: order=%s account=%s instance_id=%s note=%s', order.order_no, account_label, result.instance_id, (result.note or '')[:1000])
                    break
                if create_attempt < create_attempts and _is_transient_create_failure(result.note):
                    logger.warning('云服务器创建遇到临时错误，先重试当前账号: order=%s account=%s note=%s', order.order_no, account_label, (result.note or '')[:1000])
                    continue
                break
            if result.ok or getattr(result, 'instance_id', ''):
                break
            attempted_notes.append(f'{account_label}: {result.note}')
            if attempt_index < len(account_ids):
                logger.warning('云服务器创建失败，切换下一个账号重试: order=%s account=%s note=%s', order.order_no, account_label, (result.note or '')[:1000])

        if result and not result.ok and attempted_notes:
            result.note = '多账号创建均失败：\n' + '\n'.join(attempted_notes)

        if result.ok:
            bootstrap_user = result.login_user or login_user
            recovery_expected_ips = [order.public_ip, order.previous_public_ip] if is_cloud_asset_renewal_order(order) else []
            created_server_name = _cloud_created_server_name(order.provider, server_name, result)
            order = await _mark_instance_created(
                order.id,
                created_server_name,
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
                    cleanup_at = timezone.now() + FAILED_INSTANCE_CLEANUP_DELAY
                    note = '\n'.join(part for part in [result.note, move_note, _failed_instance_cleanup_note(cleanup_at)] if part)
                    logger.warning('云服务器重建失败: order=%s reason=static_ip_move_failed cleanup_at=%s note=%s', order.order_no, cleanup_at, move_note)
                    saved = await _mark_failed(order_id, note, cleanup_at=cleanup_at)
                    clear_provision_progress(order_id)
                    _log_provision_result(saved, level=logging.WARNING, error=saved.provision_note)
                    return saved
                final_public_ip = moved_ip or final_public_ip
                final_static_ip_name = rebuild_context['original_static_ip_name']
                source_instance_name = rebuild_context.get('source_server_name') or rebuild_context.get('source_instance_id') or ''
                source_temp_public_ip = await get_instance_public_ip(rebuild_context['payload'], source_instance_name) if source_instance_name else ''
                logger.info('云服务器重建固定 IP 已先迁移，后续安装使用正式 IP: order=%s install_ip=%s instance_id=%s source_instance=%s source_temp_public_ip=%s', order.order_no, final_public_ip, result.instance_id, source_instance_name, source_temp_public_ip)

            if rebuild_context.get('is_rebuild'):
                expected_connection_ips = [rebuild_context.get('payload', {}).get('original_public_ip')]
            elif is_cloud_asset_renewal_order(order):
                expected_connection_ips = recovery_expected_ips
            else:
                expected_connection_ips = [final_public_ip]
            aws_payload = await _get_aws_create_payload(order.id) if order.provider == 'aws_lightsail' else None
            if aws_payload:
                ip_exists, ip_exists_note = await public_ip_exists(aws_payload, expected_connection_ips)
                if not ip_exists:
                    cleanup_at = timezone.now() + FAILED_INSTANCE_CLEANUP_DELAY
                    note = '\n'.join(part for part in [ip_exists_note, _failed_instance_cleanup_note(cleanup_at)] if part)
                    saved = await _mark_failed(order_id, note, cleanup_at=cleanup_at)
                    clear_provision_progress(order_id)
                    logger.warning('云服务器开通失败: order=%s reason=expected_ip_not_found cleanup_at=%s note=%s', saved.order_no, cleanup_at, ip_exists_note)
                    _log_provision_result(saved, level=logging.WARNING, error=saved.provision_note)
                    return saved
                logger.info('云服务器预期 IP 云端存在性确认通过: order=%s note=%s', order.order_no, ip_exists_note)

            async def _refresh_guard_target_ip():
                if not aws_payload or not result.instance_id:
                    return ''
                return await get_instance_public_ip(aws_payload, result.instance_id)

            guard_ok, guard_note, guarded_public_ip = await validate_server_connection_ip_with_retry(
                final_public_ip,
                expected_connection_ips,
                context=f'provision_order:{order.id}',
                attempts=3,
                delay_seconds=5,
                refresh_target=_refresh_guard_target_ip,
            )
            if not guard_ok:
                cleanup_at = timezone.now() + FAILED_INSTANCE_CLEANUP_DELAY
                note = '\n'.join(part for part in [guard_note, _failed_instance_cleanup_note(cleanup_at)] if part)
                saved = await _mark_failed(order_id, note, cleanup_at=cleanup_at)
                clear_provision_progress(order_id)
                logger.warning('云服务器开通失败: order=%s reason=connection_ip_guard cleanup_at=%s note=%s', saved.order_no, cleanup_at, guard_note)
                _log_provision_result(saved, level=logging.WARNING, error=saved.provision_note)
                return saved
            final_public_ip = guarded_public_ip or final_public_ip

            set_provision_progress(order.id, '安装 BBR')
            logger.info('开始执行 BBR 初始化: order=%s public_ip=%s user=%s requested_user=%s', order.order_no, final_public_ip, bootstrap_user, bootstrap_user)
            private_key_path = getattr(result, 'private_key_path', '') or ''
            bbr_ok, bbr_note = await install_bbr(final_public_ip, bootstrap_user, result.login_password, private_key_path, use_key_setup=bool(private_key_path) or order.provider == 'aws_lightsail')
            logger.info('BBR 初始化结果: order=%s ok=%s note=%s', order.order_no, bbr_ok, (bbr_note or '')[:1000])

            set_provision_progress(order.id, '安装 MTProxy 主/备用/Telemt')
            backup_secret = _extract_backup_secret_from_links(getattr(order, 'proxy_links', None), order.mtproxy_port) or order.mtproxy_secret or ''
            logger.info('开始执行 MTProxy 安装: order=%s public_ip=%s user=%s port=%s requested_user=%s preserve_backup_secret=%s', order.order_no, final_public_ip, bootstrap_user, order.mtproxy_port, bootstrap_user, bool(backup_secret and backup_secret != (order.mtproxy_secret or '')))
            mtproxy_ok, mtproxy_note = await install_mtproxy(final_public_ip, bootstrap_user, result.login_password, order.mtproxy_port, order.mtproxy_secret or '', backup_secret)
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
                cleanup_at = timezone.now() + FAILED_INSTANCE_CLEANUP_DELAY
                note = '\n'.join(part for part in [note, _failed_instance_cleanup_note(cleanup_at)] if part)
                saved = await _mark_failed(order_id, note, cleanup_at=cleanup_at)
                clear_provision_progress(order_id)
                logger.warning('云服务器开通结束: order=%s status=%s note=%s', saved.order_no, saved.status, (saved.provision_note or '')[:1500])
                _log_provision_result(saved, level=logging.WARNING, error=saved.provision_note)
                return saved

            set_provision_progress(order.id, '保存开通结果')
            saved = await _mark_success(
                order_id,
                created_server_name,
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
            _log_provision_result(saved)
            return saved

        cleanup_at = None
        if getattr(result, 'instance_id', ''):
            order = await _mark_instance_created(
                order.id,
                result.instance_id,
                result.instance_id,
                result.public_ip,
                result.login_user or login_user,
                result.login_password,
                result.note,
            )
            cleanup_at = timezone.now() + FAILED_INSTANCE_CLEANUP_DELAY
        note = '\n'.join(part for part in [result.note, _failed_instance_cleanup_note(cleanup_at) if cleanup_at else ''] if part)
        logger.warning(
            '云服务器开通失败: order=%s reason=create_failed cleanup_at=%s note=%s elapsed_seconds=%s',
            order.order_no,
            cleanup_at,
            (note or '')[:1500],
            (timezone.now() - started_at).total_seconds(),
        )
        saved = await _mark_failed(order_id, note, cleanup_at=cleanup_at)
        clear_provision_progress(order_id)
        logger.warning('云服务器开通结束: order=%s status=%s note=%s', saved.order_no, saved.status, (saved.provision_note or '')[:1500])
        _log_provision_result(saved, level=logging.WARNING, error=saved.provision_note)
        return saved
    except Exception as exc:
        logger.exception('云服务器开通异常: order_id=%s error=%s', order_id, exc)
        try:
            saved = await _mark_failed(order_id, f'云服务器开通异常: {exc}')
            clear_provision_progress(order_id)
            logger.warning('云服务器开通异常结束: order=%s status=%s note=%s', saved.order_no, saved.status, (saved.provision_note or '')[:1500])
            _log_provision_result(saved, level=logging.WARNING, error=saved.provision_note)
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
    guard_ok, guard_note = validate_server_connection_ip(order.public_ip, [order.public_ip, order.previous_public_ip], context=f'reprovision_order:{order.id}')
    if not guard_ok:
        saved = await _mark_failed(order_id, guard_note)
        clear_provision_progress(order_id)
        return saved
    logger.info('[PROVISION][RETRY] start order=%s public_ip=%s user=%s port=%s', order.order_no, order.public_ip, bootstrap_user, order.mtproxy_port)
    set_provision_progress(order.id, '安装 BBR')
    bbr_ok, bbr_note = await install_bbr(order.public_ip, bootstrap_user, order.login_password, use_key_setup=order.provider == 'aws_lightsail')
    logger.info('[PROVISION][RETRY] bbr_result order=%s ok=%s note=%s', order.order_no, bbr_ok, (bbr_note or '')[:1000])
    set_provision_progress(order.id, '安装 MTProxy 主/备用/Telemt')
    mtproxy_ok, mtproxy_note = await install_mtproxy(order.public_ip, bootstrap_user, order.login_password, order.mtproxy_port, order.mtproxy_secret or '', order.mtproxy_secret or '')
    logger.info('[PROVISION][RETRY] mtproxy_result order=%s ok=%s note=%s', order.order_no, mtproxy_ok, (mtproxy_note or '')[:1000])
    bbr_warning = '' if bbr_ok else 'BBR 初始化失败，但 MTProxy 已安装成功，订单按代理可用处理。'
    note = '\n'.join(part for part in ['已执行重试初始化。', bbr_warning, bbr_note, mtproxy_note] if part)
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
    if is_cloud_asset_renewal_order(order):
        return [order.cloud_account_id] if order.cloud_account_id else []
    accounts = list_cloud_accounts_by_server_load(order.provider, order.region_code)
    ids = [account.id for account in accounts]
    if order.cloud_account_id:
        return [order.cloud_account_id, *[account_id for account_id in ids if account_id != order.cloud_account_id]]
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
        if is_cloud_asset_renewal_order(order):
            return order
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
    compact_note = _compact_proxy_install_note(note, proxy_links, order.mtproxy_port or 9528)
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
    order.provision_note = prepend_note(order.provision_note, with_note_time(compact_note))
    order.static_ip_name = static_ip_name or order.static_ip_name
    order.completed_at = timezone.now()
    if is_cloud_asset_renewal_order(order):
        order.service_started_at = order.completed_at
        order.service_expires_at = order.completed_at + timezone.timedelta(days=order.lifecycle_days or 31)
    else:
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
        server_asset = _upsert_server_asset(order, compact_note)
        record_cloud_ip_log(
            event_type='created',
            order=order,
            asset=server_asset,
            public_ip=public_ip,
            note=f'服务器创建并分配IP：{public_ip or "未分配"}',
        )
        logger.info('[PROVISION] server_asset_saved order=%s asset_id=%s expires_at=%s host=%s port=%s link=%s', order.order_no, server_asset.id, order.service_expires_at, mtproxy_host or public_ip, order.mtproxy_port, mtproxy_link)
    except Exception as exc:
        logger.exception('[PROVISION] asset_sync_failed order=%s error=%s', order.order_no, exc)
    return order


@sync_to_async
def _mark_failed(order_id: int, note: str, cleanup_at=None):
    logger.info('[PROVISION] mark_failed_start order_id=%s cleanup_at=%s note=%s', order_id, cleanup_at, (note or '')[:1500])
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'failed'
    order.provision_note = prepend_note(order.provision_note, with_note_time(note))
    order.save(update_fields=['status', 'provision_note', 'updated_at'])
    if cleanup_at and (order.server_name or order.instance_id):
        CloudServerOrder.objects.filter(id=order.id).update(delete_at=cleanup_at, updated_at=timezone.now())
        order.delete_at = cleanup_at
    server_asset = _upsert_server_asset(order, note)
    _update_order_primary_records(
        order,
        asset_updates=drop_asset_note_update({'note': note, 'status': CloudAsset.STATUS_UNKNOWN, 'is_active': False}),
        server_updates=drop_asset_note_update({'note': note, 'status': CloudAsset.STATUS_UNKNOWN, 'is_active': False}),
        now=timezone.now(),
    )
    logger.info('[PROVISION] failed_server_asset_synced order=%s asset_id=%s', order.order_no, getattr(server_asset, 'id', None))
    logger.info('[PROVISION] mark_failed_done order=%s status=%s', order.order_no, order.status)
    return order
