import logging
import math
import re
from datetime import datetime as dt_datetime
from decimal import Decimal
from html import escape
from urllib.parse import parse_qs, urlparse, unquote

from asgiref.sync import sync_to_async
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from bot.keyboards import recharge_list as kb_recharge_list
from cloud.bootstrap import _normalize_mtproxy_core_secret
from cloud.ports import get_mtproxy_port_plan
from cloud.services import _order_primary_asset
from core.formatters import fmt_amount, fmt_pay_amount
from core.texts import site_text

logger = logging.getLogger(__name__)


def _tronscan_address_url(address: str) -> str:
    return f'https://tronscan.org/#/address/{address}'


def _tronscan_transfers_url(address: str) -> str:
    return f'https://tronscan.org/#/address/{address}/transfers'


def _tronscan_tx_url(tx_hash: str) -> str:
    return f'https://tronscan.org/#/transaction/{tx_hash}'


def _public_cloud_error_text(error) -> str:
    raw = str(error or '')
    if not raw:
        return '任务暂未完成，请稍后在查询中心查看，或联系人工客服。'
    sensitive_markers = ('account', '账号', 'instance', '实例', 'server_name', 'instance_id', 'arn:', 'aws+', 'aliyun+', 'CloudAccount', 'lightsail', 'aliyun', '阿里云', 'region', 'ap-', 'cn-', 'eu-', 'us-')
    if any(marker.lower() in raw.lower() for marker in sensitive_markers) or re.search(r'\b(?:aws|ali)\b', raw, flags=re.IGNORECASE):
        return '云服务器任务执行失败，内部诊断信息已记录；请联系人工客服处理。'
    text = re.sub(r'aws\+[^\s，；,。)）]+', '云账号', raw)
    text = re.sub(r'aliyun\+[^\s，；,。)）]+', '云账号', text)
    text = re.sub(r'\b\d{12,}\b', '***', text)
    return text[:180]


def _public_cloud_stage_text(stage: str) -> str:
    text = str(stage or '').strip()
    if not text:
        return '正在执行云服务器初始化'
    text = re.sub(r'（账号[^）]*）', '', text)
    text = re.sub(r'\(账号[^)]*\)', '', text)
    text = re.sub(r'账号\s*[^，。；,\s]+', '云账号', text)
    text = re.sub(r'aws\+[^\s，；,。)）]+', '云账号', text)
    text = re.sub(r'aliyun\+[^\s，；,。)）]+', '云账号', text)
    text = re.sub(r'\baws\s*lightsail\b|\blightsail\b|\baws\b', '云服务器', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(?:aliyun|ali)\b|阿里云', '云服务器', text, flags=re.IGNORECASE)
    text = re.sub(r'\b[a-z]{2}-[a-z]+-[a-z]+-\d\b', '节点', text)
    text = re.sub(r'\b(?:cn|ap|eu|us)-[a-z0-9-]+\b', '节点', text)
    text = re.sub(r'\b\d{12,}\b', '***', text)
    if any(marker in text for marker in ['实例名', 'instance_id', 'server_name', 'provider', 'region']):
        return '正在处理云服务器资源'
    if '创建 云服务器 实例' in text or '创建云服务器实例' in text:
        return '正在创建云服务器'
    return text or '正在执行云服务器初始化'


def _public_region_text(value) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    code_names = {
        'ap-southeast-1': '新加坡',
        'cn-hongkong': '香港',
        'ap-northeast-1': '日本',
        'ap-northeast-2': '韩国',
        'us-east-1': '美国',
    }
    if text in code_names:
        return code_names[text]
    if re.fullmatch(r'[a-z]{2}-[a-z]+-[a-z]+-\d', text) or re.fullmatch(r'(?:cn|ap|eu|us)-[a-z0-9-]+', text):
        return ''
    text = re.sub(r'\b(?:aws|lightsail|aliyun|ali)\b|AWS|Lightsail|阿里云', '', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip(' -_/')


def _public_region_line(value) -> str:
    text = _public_region_text(value)
    return f'地区: {escape(text)}\n' if text else ''


def _telegram_socks_link_from_raw(link: str) -> str:
    try:
        parsed = urlparse(str(link or ''))
        if parsed.scheme != 'socks5' or not parsed.hostname or not parsed.port:
            return str(link or '')
        username = unquote(parsed.username or '')
        password = unquote(parsed.password or '')
        return f'tg://socks?server={parsed.hostname}&port={parsed.port}&user={username}&pass={password}'
    except Exception:
        return str(link or '')


def _cloud_server_created_text(order, port: int | None = None, title: str | None = None) -> str:
    mtproxy_link = getattr(order, 'mtproxy_link', '') or ''
    share_link = ''
    extra_links = []
    seen_links = set()
    public_ip = getattr(order, 'public_ip', '') or ''
    actual_port = port or getattr(order, 'mtproxy_port', '') or ''
    raw_secret = getattr(order, 'mtproxy_secret', '') or ''
    display_secret = ''
    def _link_port(link: str) -> str:
        try:
            return (parse_qs(urlparse(str(link or '')).query).get('port') or [''])[0]
        except Exception:
            return ''

    def add_extra_link(link: str):
        link = str(link or '').strip().strip('"\'，。')
        if link.startswith('socks5://'):
            link = _telegram_socks_link_from_raw(link)
        if link.startswith(('tg://proxy?', 'https://t.me/proxy?')) and _link_port(link) == str(get_mtproxy_port_plan(actual_port or 9528)['socks5']):
            return
        if link and link not in seen_links:
            extra_links.append(link)
            seen_links.add(link)

    for item in getattr(order, 'proxy_links', None) or []:
        link = item.get('url') if isinstance(item, dict) else ''
        if link:
            add_extra_link(link)
            if not mtproxy_link and str(link).startswith(('tg://proxy?', 'https://t.me/proxy?')):
                mtproxy_link = link
    note = getattr(order, 'provision_note', '') or ''
    for line in note.splitlines():
        if line.startswith('TG链接: '):
            mtproxy_link = mtproxy_link or line.split(': ', 1)[1].strip()
        elif line.startswith('分享链接: '):
            share_link = line.split(': ', 1)[1].strip()
        elif 'https://t.me/proxy?' in line and not share_link:
            share_link = line[line.find('https://t.me/proxy?'):].strip()
        if 'tg://proxy?' in line:
            link = line[line.find('tg://proxy?'):].strip().strip('"\',，。')
            add_extra_link(link)
            if not mtproxy_link:
                mtproxy_link = link
        if 'socks5://' in line:
            add_extra_link(line[line.find('socks5://'):].strip())
    has_socks5_link = any(str(link).startswith(('socks5://', 'tg://socks?')) for link in extra_links)
    if not has_socks5_link and 'SOCKS5:' in note and public_ip and raw_secret:
        socks5_secret = _normalize_mtproxy_core_secret(raw_secret) or raw_secret
        socks5_port = get_mtproxy_port_plan(actual_port or 9528)['socks5']
        port_match = re.search(r'SOCKS5:\s*[^\n]*?端口\s*(\d+)', note)
        if port_match:
            socks5_port = int(port_match.group(1))
        add_extra_link(f'socks5://{socks5_secret}:{socks5_secret}@{public_ip}:{socks5_port}')
    one_click_link = mtproxy_link or share_link or '-'
    if 'secret=' in one_click_link:
        display_secret = one_click_link.split('secret=', 1)[1].split('&', 1)[0].strip()
    elif mtproxy_link and 'secret=' in mtproxy_link:
        display_secret = mtproxy_link.split('secret=', 1)[1].split('&', 1)[0].strip()
    else:
        display_secret = raw_secret
    lines = [title or _bot_text('bot_cloud_create_success', '✅ 云服务器创建完成')]
    lines.append(f'端口: <code>{escape(str(actual_port or "-"))}</code>')
    lines.append(f'IP: <code>{escape(public_ip or "-")}</code>')
    lines.append(f'密钥: <code>{escape(display_secret or "-")}</code>')
    lines.append(f'一键链接: {escape(one_click_link)}')
    additional_links = [link for link in extra_links if link != mtproxy_link and link != share_link]
    if additional_links:
        lines.append('')
        lines.append('备用链路:')
        socks5_links = [link for link in additional_links if str(link).startswith(('socks5://', 'tg://socks?'))]
        other_links = [link for link in additional_links if not str(link).startswith(('socks5://', 'tg://socks?'))]
        for link in socks5_links:
            lines.append(f'SOCKS5: {escape(link)}')
        for index, link in enumerate(other_links[:8], start=1):
            lines.append(f'{index}. {escape(link)}')
    lines.append('')
    lines.append(_cloud_order_plan_text(order))
    return '\n'.join(lines)


def _cloud_can_refund(order, now=None) -> bool:
    if order.status not in {'paid', 'provisioning', 'failed', 'completed', 'expiring', 'suspended'}:
        return False
    expires_at = getattr(order, 'service_expires_at', None)
    if expires_at and expires_at < (now or timezone.now()) + timezone.timedelta(days=10):
        return False
    return True


def _cloud_order_status_hint(order) -> str:
    has_ip = bool(order.public_ip or order.previous_public_ip)
    if has_ip:
        missing = []
        if order.status in {'paid', 'provisioning'}:
            if not order.login_password:
                missing.append('登录密码')
            if not order.mtproxy_secret:
                missing.append('密钥')
            if not order.mtproxy_link:
                missing.append('代理链接')
        if missing:
            return f'初始化说明: 已分配 IP，但尚未完成初始化，缺少 {"、".join(missing)}。可点“继续初始化”查看处理提示。'
        return ''
    if order.status == 'pending':
        return _bot_text('bot_cloud_unassigned_pending', '未分配IP说明: 订单未付款')
    if order.status in {'paid', 'provisioning'}:
        return _bot_text('bot_cloud_unassigned_paid', '未分配IP说明: 已支付但尚未完成，请联系人工处理')
    if order.status == 'failed':
        return _bot_text('bot_cloud_unassigned_failed', '未分配IP说明: 创建失败，请联系人工处理')
    return f'未分配IP说明: 当前状态为 {order.get_status_display()}'


def _proxy_links_text(order) -> str:
    links = []
    seen = set()
    main_link = str(getattr(order, 'mtproxy_link', '') or '')
    main_port = str(getattr(order, 'mtproxy_port', '') or '')
    if main_link:
        links.append(('主代理', main_link))
        seen.add(main_link)
    for item in getattr(order, 'proxy_links', None) or []:
        if not isinstance(item, dict):
            continue
        link = item.get('url') or ''
        if str(link).startswith('socks5://'):
            link = _telegram_socks_link_from_raw(link)
        if not link or link in seen:
            continue
        if main_link and main_port and str(item.get('port') or '') == main_port:
            continue
        label = item.get('name') or f"端口 {item.get('port') or '-'}"
        links.append((label, link))
        seen.add(link)
    if not links:
        return f'代理链接: {escape(str(main_link or "尚未生成"))}'
    lines = ['代理链路:']
    for label, link in links:
        lines.append(f'- {escape(str(label))}: {escape(link)}')
    return '\n'.join(lines)


def _format_local_dt(value) -> str:
    if not value:
        return '未设置'
    try:
        return timezone.localtime(value).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return str(value)


def _parse_admin_expiry_input(raw_text: str):
    value = str(raw_text or '').strip()
    if not value:
        return None
    normalized = value.replace('年', '-').replace('月', '-').replace('日', ' ').replace('/', '-').strip()
    parsed = parse_datetime(normalized)
    if parsed is None:
        parsed_date = parse_date(normalized)
        if parsed_date:
            parsed = dt_datetime.combine(parsed_date, dt_datetime.min.time()).replace(hour=15)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed.astimezone(timezone.get_current_timezone())


def _cloud_order_ip_text(order) -> str:
    return getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '未分配'


def _cloud_order_plan_text(order, include_warnings: bool = True) -> str:
    expires_at = getattr(order, 'service_expires_at', None)
    suspend_at = getattr(order, 'suspend_at', None)
    delete_at = getattr(order, 'delete_at', None)
    auto_renew_enabled = bool(getattr(order, 'auto_renew_enabled', False))
    auto_renew_at = expires_at - timezone.timedelta(days=1) if expires_at else None
    lines = [f'到期时间: {_format_local_dt(expires_at)}']
    if auto_renew_enabled:
        lines.append(f'自动续费: 已开启，预计 {_format_local_dt(auto_renew_at)} 自动续费')
    else:
        lines.append('自动续费: 本IP未开启自动续费')
    lines.extend([
        f'关机计划: {_format_local_dt(suspend_at)}',
        f'删除计划: {_format_local_dt(delete_at)}',
    ])
    if include_warnings and suspend_at:
        lines.append(f'请务必在 {_format_local_dt(suspend_at)} 之前完成续费，避免关机。')
    return '\n'.join(lines)


def _display_username(user) -> str:
    primary = getattr(user, 'primary_username', None)
    username = primary or (str(getattr(user, 'username', '') or '').split(',')[0].strip())
    return f'@{username}' if username else '无'


def _reminder_list_text(summary: dict, page: int = 1, per_page: int = 5) -> str:
    if not summary:
        return '🔔 提醒列表\n\n暂无提醒设置。'
    cloud_orders = summary.get('cloud_orders') or []
    total_pages = max(1, math.ceil(len(cloud_orders) / per_page))
    page = min(max(1, page), total_pages)
    page_orders = cloud_orders[(page - 1) * per_page: page * per_page]
    lines = [f'🔔 提醒列表（{page}/{total_pages}）', '', '云服务器:']
    if page_orders:
        for order in page_orders:
            ip = order.public_ip or order.previous_public_ip or order.order_no
            reminder_count = sum(1 for field in ('cloud_reminder_enabled', 'suspend_reminder_enabled', 'delete_reminder_enabled', 'ip_recycle_reminder_enabled') if getattr(order, field, True))
            auto = '自动续费开' if order.auto_renew_enabled else '自动续费关'
            lines.append(f'- {escape(str(ip))} | 到期 {_format_local_dt(order.service_expires_at)} | 提醒 {reminder_count}/4 | {auto}')
    else:
        lines.append('- 暂无云服务器提醒')
    lines.extend(['', '这里只管理 IP 到期提醒和自动续费提醒。'])
    return '\n'.join(lines)


def _reminder_page_items(summary: dict, page: int = 1, per_page: int = 5):
    cloud_orders = (summary or {}).get('cloud_orders') or []
    total_pages = max(1, math.ceil(len(cloud_orders) / per_page))
    page = min(max(1, page), total_pages)
    return cloud_orders[(page - 1) * per_page: page * per_page], page, total_pages


def _find_reminder_order(summary: dict, order_id: int):
    for order in (summary or {}).get('cloud_orders') or []:
        if int(getattr(order, 'id', 0) or 0) == int(order_id):
            return order
    return None


def _reminder_ip_detail_text(order, page: int = 1) -> str:
    ip = order.public_ip or order.previous_public_ip or order.order_no
    expiry = '已开启' if getattr(order, 'cloud_reminder_enabled', True) else '已关闭'
    suspend = '已开启' if getattr(order, 'suspend_reminder_enabled', True) else '已关闭'
    delete = '已开启' if getattr(order, 'delete_reminder_enabled', True) else '已关闭'
    ip_recycle = '已开启' if getattr(order, 'ip_recycle_reminder_enabled', True) else '已关闭'
    auto = '已开启' if getattr(order, 'auto_renew_enabled', False) else '已关闭'
    return '\n'.join([
        '🌐 IP 提醒设置',
        '',
        f'IP: <code>{escape(str(ip))}</code>',
        f'订单号: {escape(str(order.order_no))}',
        f'到期时间: {_format_local_dt(order.service_expires_at)}',
        f'到期提醒: {expiry}',
        f'停机提醒: {suspend}',
        f'删机提醒: {delete}',
        f'IP保留期提醒: {ip_recycle}',
        f'自动续费提醒/续费: {auto}',
        '',
        '可以分别开启或关闭这台 IP 的各类生命周期提醒。',
    ])


def _cloud_asset_detail_text(item) -> str:
    proxy_links_text = _proxy_links_text(item)
    return (
        '☁️ 代理详情\n\n'
        f'名称: {escape(str(getattr(item, "order_no", "-") or "-"))}\n'
        f'{_public_region_line(getattr(item, "region_name", ""))}'
        f'状态: {escape(str(item.get_status_display() if hasattr(item, "get_status_display") else getattr(item, "status", "-")))}\n'
        f'IP: <code>{escape(str(getattr(item, "public_ip", "") or getattr(item, "previous_public_ip", "") or "未分配"))}</code>\n'
        f'端口: <code>{escape(str(getattr(item, "mtproxy_port", None) or "未设置"))}</code>\n'
        f'密钥: <code>{escape(str(getattr(item, "mtproxy_secret", None) or "尚未生成"))}</code>\n'
        f'{proxy_links_text}\n'
        f'到期时间: {_format_local_dt(getattr(item, "service_expires_at", None))}\n'
        f'创建时间: {_format_local_dt(getattr(item, "created_at", None))}'
    )


def _cloud_server_detail_text(order) -> str:
    status_hint = _cloud_order_status_hint(order)
    service_expires_at = _format_local_dt(order.service_expires_at) if order.service_expires_at else '今天到期'
    renew_price = getattr(order, 'renewal_price', None) or order.pay_amount or order.total_amount
    auto_renew_status = '已开启' if getattr(order, 'auto_renew_enabled', False) else '已关闭'
    proxy_links_text = _proxy_links_text(order)
    text = (
        '☁️ 云服务器详情\n\n'
        f'订单号: {escape(str(order.order_no or "-"))}\n'
        f'{_public_region_line(order.region_name)}'
        f'套餐: {escape(str(order.plan_name or "-"))}\n'
        f'数量: {order.quantity}\n'
        f'状态: {escape(str(order.get_status_display()))}\n'
        f'支付方式: {"余额" if order.pay_method == "balance" else "地址"}\n'
        f'金额: {fmt_pay_amount(order.pay_amount or order.total_amount)} {escape(str(order.currency or ""))}\n'
        f'IP: <code>{escape(order.public_ip or order.previous_public_ip or "未分配")}</code>\n'
        f'端口: <code>{escape(str(order.mtproxy_port or "未设置"))}</code>\n'
        f'密钥: <code>{escape(str(order.mtproxy_secret or "尚未生成"))}</code>\n'
        f'{proxy_links_text}\n'
        f'到期时间: {service_expires_at}\n'
        f'续费价格: {fmt_pay_amount(renew_price)} {escape(str(order.currency or ""))}\n'
        f'自动续费: {auto_renew_status}\n'
        f'IP保留到期: {order.ip_recycle_at or "未设置"}\n'
        f'创建时间: {order.created_at:%Y-%m-%d %H:%M:%S}'
    )
    if status_hint:
        text += f'\n{status_hint}'
    return text


@sync_to_async
def _hydrate_order_proxy_links(order):
    if not order:
        return order
    current_links = list(getattr(order, 'proxy_links', None) or [])
    if len(current_links) > 1:
        return order
    try:
        from cloud.models import CloudAsset, CloudServerOrder
        candidates = []
        asset = _order_primary_asset(order)
        if asset:
            candidates.append(asset)
        lookup_values = [value for value in [order.instance_id, order.provider_resource_id, order.server_name, order.public_ip, order.previous_public_ip] if value]
        try:
            link_server = parse_qs(urlparse(str(getattr(order, 'mtproxy_link', '') or '')).query).get('server', [''])[0]
            if link_server:
                lookup_values.append(link_server)
        except Exception:
            pass
        if lookup_values:
            asset_qs = CloudAsset.objects.filter(
                Q(instance_id__in=lookup_values) | Q(provider_resource_id__in=lookup_values) | Q(asset_name__in=lookup_values) | Q(public_ip__in=lookup_values) | Q(previous_public_ip__in=lookup_values)
            )
            scope_q = Q(order_id=order.id)
            if getattr(order, 'user_id', None):
                scope_q |= Q(user_id=order.user_id)
            asset_qs = asset_qs.filter(scope_q)
            if getattr(order, 'provider', None):
                asset_qs = asset_qs.filter(provider=order.provider)
            if getattr(order, 'cloud_account_id', None):
                from core.cloud_accounts import cloud_account_label_variants

                account_labels = set()
                if getattr(order, 'account_label', None):
                    account_labels.add(order.account_label)
                if getattr(order, 'cloud_account', None):
                    account_labels.update(cloud_account_label_variants(order.cloud_account))
                account_scope = Q(cloud_account_id=order.cloud_account_id)
                if account_labels:
                    account_scope |= Q(account_label__in=list(account_labels))
                asset_qs = asset_qs.filter(account_scope)
            elif getattr(order, 'account_label', None):
                asset_qs = asset_qs.filter(account_label=order.account_label)
            candidates.extend(asset_qs.order_by('-updated_at', '-id')[:5])
        for source in candidates:
            links = list(getattr(source, 'proxy_links', None) or [])
            if len(links) > len(current_links):
                order.proxy_links = links
                current_links = links
            if not getattr(order, 'mtproxy_link', None) and getattr(source, 'mtproxy_link', None):
                order.mtproxy_link = source.mtproxy_link
            if not getattr(order, 'mtproxy_secret', None) and getattr(source, 'mtproxy_secret', None):
                order.mtproxy_secret = source.mtproxy_secret
            if not getattr(order, 'mtproxy_port', None) and getattr(source, 'mtproxy_port', None):
                order.mtproxy_port = source.mtproxy_port
        if len(current_links) <= 1 and getattr(order, 'mtproxy_link', None):
            richer_order = CloudServerOrder.objects.filter(user_id=order.user_id, mtproxy_link=order.mtproxy_link).exclude(id=order.id).order_by('-updated_at', '-id').first()
            richer_links = list(getattr(richer_order, 'proxy_links', None) or []) if richer_order else []
            if len(richer_links) > len(current_links):
                order.proxy_links = richer_links
                current_links = richer_links
        if len(current_links) <= 1 and getattr(order, 'replacement_for_id', None):
            source_order = CloudServerOrder.objects.filter(id=order.replacement_for_id).first()
            source_links = list(getattr(source_order, 'proxy_links', None) or []) if source_order else []
            if len(source_links) > len(current_links):
                order.proxy_links = source_links
    except Exception as exc:
        logger.warning('CLOUD_ORDER_PROXY_LINK_HYDRATE_FAILED order_id=%s error=%s', getattr(order, 'id', None), exc)
    return order


def _chain_trace_text(item) -> str:
    payer = str(getattr(item, 'payer_address', '') or '').strip()
    receiver = str(getattr(item, 'receive_address', '') or '').strip()
    tx_hash = str(getattr(item, 'tx_hash', '') or '').strip()
    lines = []
    if payer:
        lines.append(f'付款地址: <a href="{_tronscan_address_url(payer)}">{escape(payer)}</a>')
    if receiver:
        lines.append(f'收款地址: <a href="{_tronscan_address_url(receiver)}">{escape(receiver)}</a>')
    if tx_hash:
        lines.append(f'链上交易: <a href="{_tronscan_tx_url(tx_hash)}">{escape(tx_hash)}</a>')
    return '\n'.join(lines)


def _cloud_order_readonly_text(order) -> str:
    status_hint = _cloud_order_status_hint(order)
    service_expires_at = _format_local_dt(order.service_expires_at) if order.service_expires_at else '未设置'
    paid_at = getattr(order, 'paid_at', None) or getattr(order, 'completed_at', None)
    paid_at_text = f'{paid_at:%Y-%m-%d %H:%M:%S}' if paid_at else '未支付'
    proxy_links_text = _proxy_links_text(order)
    chain_trace = _chain_trace_text(order)
    text = (
        '☁️ 云服务器订单详情\n\n'
        f'订单号: {escape(str(order.order_no or "-"))}\n'
        f'{_public_region_line(order.region_name)}'
        f'套餐: {escape(str(order.plan_name or "-"))}\n'
        f'数量: {order.quantity}\n'
        f'状态: {escape(str(order.get_status_display()))}\n'
        f'支付方式: {"余额" if order.pay_method == "balance" else "地址"}\n'
        f'金额: {fmt_pay_amount(order.pay_amount or order.total_amount)} {escape(str(order.currency or ""))}\n'
        f'IP: <code>{escape(order.public_ip or order.previous_public_ip or "未分配")}</code>\n'
        f'端口: <code>{escape(str(order.mtproxy_port or "未设置"))}</code>\n'
        f'密钥: <code>{escape(str(order.mtproxy_secret or "尚未生成"))}</code>\n'
        f'{proxy_links_text}\n'
        f'到期时间: {service_expires_at}\n'
        f'支付时间: {paid_at_text}'
    )
    if chain_trace:
        text += f'\n{chain_trace}'
    text += f'\n创建时间: {order.created_at:%Y-%m-%d %H:%M:%S}'
    if status_hint:
        text += f'\n{status_hint}'
    text += '\n\n此处仅用于查询订单，不提供自助操作。如需续费、初始化或其他处理，请联系人工客服。'
    return text


def _cloud_order_detail_text(order) -> str:
    return _cloud_order_readonly_text(order)


def _balance_detail_text(item) -> str:
    created_at = item['created_at'].strftime('%Y-%m-%d %H:%M:%S') if item.get('created_at') else '-'
    before_balance = item.get('before_balance') or '-'
    after_balance = item.get('after_balance') or '-'
    direction_label = '收入' if item['direction'] == 'in' else '支出'
    return (
        '💳 余额明细详情\n\n'
        f"类型: {item['title']}\n"
        f"方向: {direction_label}\n"
        f"金额: {item['amount']} {item['currency']}\n"
        f"变动前余额: {before_balance}\n"
        f"变动后余额: {after_balance}\n"
        f"说明: {item['description']}\n"
        f"时间: {created_at}"
    )


def _monitor_detail_text(monitor) -> str:
    icon = '🟢' if monitor.is_active else '🔴'
    return (
        f'{icon} 监控详情\n'
        f'监控地址: <code>{escape(str(monitor.address or "-"))}</code>\n'
        f'备注: {escape(str(monitor.remark or "无"))}\n'
        f'💸 监控转账: {"开启" if monitor.monitor_transfers else "关闭"}\n'
        f'⚡ 监控资源: {"开启" if monitor.monitor_resources else "关闭"}\n'
        f'USDT 阈值: {fmt_amount(monitor.usdt_threshold)}\n'
        f'TRX 阈值: {fmt_amount(monitor.trx_threshold)}\n'
        f'能量增加阈值: {int(monitor.energy_threshold or 0)}\n'
        f'带宽增加阈值: {int(monitor.bandwidth_threshold or 0)}\n\n'
        f'📘 使用说明:\n'
        f'1. 监控转账：地址收到 USDT/TRX 转账时通知。\n'
        f'2. 监控资源：地址可用能量/带宽增加时通知；正常转账消耗不通知。'
    )


def _recharges_page(recharges, page: int, total: int):
    total_pages = max(1, math.ceil(total / 5))
    if not recharges:
        return _bot_text('bot_recharges_empty', '暂无充值记录。'), None
    return _bot_text('bot_recharges_title', '📜 充值记录：'), kb_recharge_list(recharges, page, total_pages)


def _plan_display_name(plan) -> str:
    return getattr(plan, 'display_plan_name', None) or getattr(plan, 'plan_name', '-') or '-'


def _custom_plan_text(region_name: str, plans) -> str:
    if not plans:
        return f'🛠 {region_name}\n\n当前地区暂无可用套餐。'
    labels = ['套餐一', '套餐二', '套餐三', '套餐四', '套餐五', '套餐六', '套餐七', '套餐八', '套餐九']
    lines = [f'🛠 {region_name} 可用套餐', '']
    for idx, plan in enumerate(plans, start=1):
        display_name = _plan_display_name(plan)
        display_description = (getattr(plan, 'display_description', None) or getattr(plan, 'plan_description', None) or '').strip()
        label = labels[idx - 1] if idx - 1 < len(labels) else f'套餐{idx}'
        lines.append(f'{label}｜{display_name}')
        if display_description:
            lines.append(display_description)
        lines.append('')
    lines.append('请选择下面的套餐按钮：')
    return '\n'.join(lines)


def _cloud_order_payment_text(order) -> str:
    receive_address = _receive_address()
    display_name = _plan_display_name(order)
    amount = Decimal(str(getattr(order, 'pay_amount', None) or getattr(order, 'total_amount', None) or 0))
    currency = getattr(order, 'currency', None) or 'USDT'
    return (
        _bot_text('bot_custom_payment_title', '🧾 支付页面') + '\n\n'
        f'订单号: {getattr(order, "order_no", "-")}\n'
        f'{_public_region_line(getattr(order, "region_name", None))}'
        f'套餐: {display_name}\n'
        f'数量: {getattr(order, "quantity", 1) or 1}\n'
        f'支付金额: {fmt_pay_amount(amount)} {currency}\n'
        f'支付地址: <code>{escape(receive_address)}</code>\n\n'
        + _bot_text('bot_custom_order_notice', f'系统已开始自动监控 {currency} 到账，检测到支付成功后会自动进入后续流程。')
    )


def _retained_ip_renewal_plan_text(order, plans, user=None) -> str:
    labels = ['套餐一', '套餐二', '套餐三', '套餐四', '套餐五', '套餐六', '套餐七', '套餐八', '套餐九']
    ip = getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '-'
    region = _public_region_text(getattr(order, 'region_name', None) or getattr(order, 'region_code', None)) or '-'
    lines = [
        _bot_text_format(
            'bot_retained_ip_renewal_plan_intro',
            '🔄 未附加固定 IP 续费\n\n保留 IP: {ip}\n地区: {region}\n\n请选择要恢复的新服务器套餐。选好后，我会要求你发送旧的主代理链接，用来保持原链接/密钥不变。',
            ip=ip,
            region=region,
        ),
        '',
    ]
    for idx, plan in enumerate(plans[:9], start=1):
        label = labels[idx - 1] if idx - 1 < len(labels) else f'套餐{idx}'
        display_name = _plan_display_name(plan)
        display_description = (getattr(plan, 'display_description', None) or getattr(plan, 'plan_description', None) or '').strip()
        discount_rate = Decimal(str(getattr(user, 'cloud_discount_rate', 100) or 100))
        display_price = (Decimal(str(getattr(plan, 'price', 0) or 0)) * discount_rate / Decimal('100')).quantize(Decimal('0.001'))
        currency = getattr(plan, 'currency', None) or 'USDT'
        lines.append(f'{label}｜{display_name}｜{fmt_amount(display_price)} {currency}')
        if display_description:
            lines.append(display_description)
        lines.append('')
    lines.append(_bot_text('bot_retained_ip_renewal_plan_footer', '请选择下面的套餐按钮：'))
    return '\n'.join(lines)


def _retained_ip_renewal_plan_keyboard(order_id: int, plans):
    labels = ['套餐一', '套餐二', '套餐三', '套餐四', '套餐五', '套餐六', '套餐七', '套餐八', '套餐九']
    buttons = []
    for idx, plan in enumerate(plans[:9]):
        label = labels[idx] if idx < len(labels) else f'套餐{idx + 1}'
        buttons.append(InlineKeyboardButton(text=label, callback_data=f'cloud:renewplan:{order_id}:{plan.id}'))
    rows = [buttons[index:index + 3] for index in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text='🔙 返回详情', callback_data=f'cloud:detail:{order_id}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _asset_renewal_plan_text(asset, plans, user=None) -> str:
    labels = ['套餐一', '套餐二', '套餐三', '套餐四', '套餐五', '套餐六', '套餐七', '套餐八', '套餐九']
    ip = getattr(asset, 'public_ip', None) or getattr(asset, 'previous_public_ip', None) or '-'
    region = _public_region_text(getattr(asset, 'region_name', None) or getattr(asset, 'region_code', None)) or '-'
    lines = [
        f'🔄 未绑定代理资产续费\n\nIP: {ip}\n地区: {region}\n\n这条代理还未绑定订单，请先选择套餐；选择后发送旧主代理链接，系统会生成支付订单。',
        '',
    ]
    for idx, plan in enumerate(plans[:9], start=1):
        label = labels[idx - 1] if idx - 1 < len(labels) else f'套餐{idx}'
        display_name = _plan_display_name(plan)
        display_description = (getattr(plan, 'display_description', None) or getattr(plan, 'plan_description', None) or '').strip()
        discount_rate = Decimal(str(getattr(user, 'cloud_discount_rate', 100) or 100))
        display_price = (Decimal(str(getattr(plan, 'price', 0) or 0)) * discount_rate / Decimal('100')).quantize(Decimal('0.001'))
        currency = getattr(plan, 'currency', None) or 'USDT'
        lines.append(f'{label}｜{display_name}｜{fmt_amount(display_price)} {currency}')
        if display_description:
            lines.append(display_description)
        lines.append('')
    lines.append('请选择下面的套餐按钮：')
    return '\n'.join(lines)


def _asset_renewal_plan_keyboard(asset_id: int, plans):
    labels = ['套餐一', '套餐二', '套餐三', '套餐四', '套餐五', '套餐六', '套餐七', '套餐八', '套餐九']
    buttons = []
    for idx, plan in enumerate(plans[:9]):
        label = labels[idx] if idx < len(labels) else f'套餐{idx + 1}'
        buttons.append(InlineKeyboardButton(text=label, callback_data=f'cloud:assetrenewplan:{asset_id}:{plan.id}'))
    rows = [buttons[index:index + 3] for index in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text='🔙 返回代理详情', callback_data=f'cloud:assetdetail:asset:{asset_id}:cloud:list:page:1')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _receive_address() -> str:
    from core.cache import _cached_config
    return _cached_config.get('receive_address', '')


def _bot_text(key: str, default: str) -> str:
    return site_text(key, default)


def _bot_text_format(key: str, default: str, **kwargs) -> str:
    template = _bot_text(key, default)
    try:
        return template.format(**kwargs)
    except Exception:
        return default.format(**kwargs)
