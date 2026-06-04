"""云资产后台 API。"""

import re
from urllib.parse import urlparse

from django.db.models import Q
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_GET

from bot.models import TelegramLoginAccount, TelegramUser
from cloud.api_asset_snapshots import (
    _dashboard_snapshot_group_page,
    _dashboard_snapshot_ordering,
    _dashboard_snapshot_queryset,
    _dashboard_snapshot_risk_counts,
    _ensure_cloud_asset_dashboard_snapshots,
    _filter_dashboard_snapshots_by_risk,
    _group_cloud_asset_payloads,
    _paginate_dashboard_snapshot_queryset,
    _snapshot_payloads,
)
from cloud.dashboard_api_helpers import _dashboard_sort_direction, _preserve_link_status_label, _preserve_link_status_with_countdown
from cloud.lifecycle_schedule import compute_unattached_ip_release_at
from cloud.models import CloudAsset, CloudServerOrder
from cloud.services import sync_cloud_asset_user_binding
from core.cloud_accounts import cloud_account_label, cloud_account_label_variants, list_cloud_account_labels
from core.dashboard_api import _countdown_label, _days_left, _decimal_to_str, _get_keyword, _iso, _ok, _provider_label, _provider_status_label, _region_label, _server_source_label, _split_usernames, _status_label, _user_payload, dashboard_login_required


def _mask_secret(value, keep=4):
    text = str(value or '')
    if not text:
        return ''
    if len(text) <= keep * 2:
        return '*' * len(text)
    return f'{text[:keep]}***{text[-keep:]}'


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _is_unattached_ip_asset(asset: CloudAsset) -> bool:
    return '未附加' in str(asset.provider_status or '')


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _unattached_ip_delete_due_at(*, now=None):
    return compute_unattached_ip_release_at(now or timezone.now())


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _ensure_unattached_ip_expiry(asset: CloudAsset, *, now=None) -> bool:
    """未附加固定 IP 必须有计划删除时间；缺失时按系统配置补齐。"""
    if not _is_unattached_ip_asset(asset) or asset.actual_expires_at:
        return False
    asset.actual_expires_at = _unattached_ip_delete_due_at(now=now)
    asset.save(update_fields=['actual_expires_at', 'updated_at'])
    return True


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _telegram_user_lookup_terms(value):
    raw = str(value or '').strip()
    if not raw:
        return []

    terms = []

    # 功能：处理 后台 API 接口 中的 add 业务流程。
    def add(term):
        normalized = str(term or '').strip().strip('`"\'<>，,。；;：:').lstrip('@')
        if normalized and normalized not in terms:
            terms.append(normalized)

    add(raw)
    parsed = urlparse(raw if '://' in raw else f'https://{raw}')
    if parsed.netloc.lower() in {'t.me', 'telegram.me', 'www.t.me', 'www.telegram.me'}:
        path_parts = [part for part in parsed.path.split('/') if part]
        if path_parts:
            add(path_parts[0])
    for match in re.findall(r'@([A-Za-z0-9_]{3,64})', raw):
        add(match)
    for match in re.findall(r'(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,64})', raw, flags=re.I):
        add(match)
    for match in re.findall(r'\b\d{5,20}\b', raw):
        add(match)
    return terms


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _username_matches(saved_value, lookup_value) -> bool:
    lookup_names = {item.lower() for item in TelegramUser.normalize_usernames(lookup_value)}
    if not lookup_names:
        return False
    saved_names = {item.lower() for item in TelegramUser.normalize_usernames(saved_value)}
    return bool(saved_names & lookup_names)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _resolve_telegram_user(value):
    terms = _telegram_user_lookup_terms(value)
    if not terms:
        return None
    queryset = TelegramUser.objects.all()
    for raw in terms:
        if raw.isdigit():
            found = queryset.filter(Q(id=int(raw)) | Q(tg_user_id=int(raw))).first()
            if found:
                return found
            continue
        candidates = list(queryset.filter(username__icontains=raw).order_by('-updated_at', '-id')[:20])
        found = next((item for item in candidates if _username_matches(item.username, raw)), None)
        if found:
            return found
    for raw in terms:
        account_query = Q(tg_user_id=int(raw)) if raw.isdigit() else Q(username__icontains=raw)
        accounts = TelegramLoginAccount.objects.filter(account_query).exclude(tg_user_id__isnull=True).order_by('-updated_at', '-id')[:20]
        account = next((item for item in accounts if raw.isdigit() or _username_matches(item.username, raw)), None)
        if not account or not account.tg_user_id:
            continue
        user, _ = TelegramUser.objects.get_or_create(
            tg_user_id=account.tg_user_id,
            defaults={
                'username': TelegramUser.serialize_usernames(account.username),
                'first_name': account.label or '',
            },
        )
        _sync_telegram_username(user, account.username)
        return user
    return None


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _parse_iso_datetime(value, field_label='时间'):
    raw = str(value or '').strip()
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        parsed_date = parse_date(raw)
        if parsed_date is not None:
            parsed = timezone.datetime.combine(parsed_date, timezone.datetime.min.time())
    if parsed is None:
        raise ValueError(f'{field_label}格式不正确，请使用 ISO 时间或 YYYY-MM-DD 日期')
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _sync_telegram_username(user, username=None):
    incoming = _split_usernames(username)
    if not incoming:
        return
    merged = []
    seen = set()
    for item in [*user.usernames, *incoming]:
        key = str(item).lower()
        if item and key not in seen:
            merged.append(item)
            seen.add(key)
    user.username = ','.join(merged)
    user.save(update_fields=['username', 'updated_at'])


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _infer_asset_order(asset):
    order = getattr(asset, 'order', None)
    if order:
        return order
    provider = str(getattr(asset, 'provider', '') or '').strip()
    region_code = str(getattr(asset, 'region_code', '') or '').strip()
    account = getattr(asset, 'cloud_account', None)
    account_labels = cloud_account_label_variants(account) if account else []
    asset_account_label = str(getattr(asset, 'account_label', '') or '').strip()
    if asset_account_label:
        account_labels.append(asset_account_label)
    account_labels = list(dict.fromkeys(label for label in account_labels if label))
    names = {
        str(getattr(asset, 'asset_name', '') or '').strip(),
        str(getattr(asset, 'instance_id', '') or '').strip(),
        str(getattr(asset, 'provider_resource_id', '') or '').strip(),
    }
    ips = {
        str(getattr(asset, 'public_ip', '') or '').strip(),
        str(getattr(asset, 'previous_public_ip', '') or '').strip(),
    }
    names.discard('')
    ips.discard('')
    if not names and not ips:
        return None
    queryset = CloudServerOrder.objects.select_related('user', 'plan', 'cloud_account')
    if provider:
        queryset = queryset.filter(provider=provider)
    if region_code:
        queryset = queryset.filter(Q(region_code=region_code) | Q(region_code='') | Q(region_code__isnull=True))
    if getattr(asset, 'user_id', None):
        queryset = queryset.filter(Q(user_id=asset.user_id) | Q(user__isnull=True))
    if getattr(asset, 'cloud_account_id', None):
        queryset = queryset.filter(Q(cloud_account_id=asset.cloud_account_id) | Q(account_label__in=account_labels))
    elif account_labels:
        queryset = queryset.filter(Q(account_label__in=account_labels) | Q(account_label='') | Q(account_label__isnull=True))
    if ips:
        ip_lookup = Q(public_ip__in=ips) | Q(previous_public_ip__in=ips)
        found = queryset.filter(ip_lookup).order_by('-updated_at', '-id').first()
        if found:
            return found
    if names:
        name_lookup = Q(server_name__in=names) | Q(instance_id__in=names) | Q(provider_resource_id__in=names)
        return queryset.filter(name_lookup).order_by('-updated_at', '-id').first()
    return None


# 类型说明：封装 后台 API 接口 中 CloudAssetPayloadContext 相关的数据和行为。
class CloudAssetPayloadContext:
    # 功能：初始化对象状态和依赖。
    def __init__(self, *, active_account_labels=None, inferred_orders=None, allow_mutation=False, now=None):
        self.active_account_labels = set(active_account_labels or [])
        self.inferred_orders = inferred_orders or {}
        self.allow_mutation = allow_mutation
        self.now = now or timezone.now()


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _asset_lookup_values(asset):
    names = {
        str(getattr(asset, 'asset_name', '') or '').strip(),
        str(getattr(asset, 'instance_id', '') or '').strip(),
        str(getattr(asset, 'provider_resource_id', '') or '').strip(),
    }
    ips = {
        str(getattr(asset, 'public_ip', '') or '').strip(),
        str(getattr(asset, 'previous_public_ip', '') or '').strip(),
    }
    names.discard('')
    ips.discard('')
    return names, ips


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _asset_account_label_variants(asset):
    account = getattr(asset, 'cloud_account', None)
    labels = cloud_account_label_variants(account) if account else []
    asset_account_label = str(getattr(asset, 'account_label', '') or '').strip()
    if asset_account_label:
        labels.append(asset_account_label)
    return list(dict.fromkeys(label for label in labels if label))


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _order_matches_asset_lookup(order, asset, account_labels) -> bool:
    provider = str(getattr(asset, 'provider', '') or '').strip()
    region_code = str(getattr(asset, 'region_code', '') or '').strip()
    if provider and order.provider != provider:
        return False
    if region_code and str(order.region_code or '') not in {region_code, ''}:
        return False
    if getattr(asset, 'user_id', None) and order.user_id != asset.user_id:
        return False
    if getattr(asset, 'cloud_account_id', None):
        return order.cloud_account_id == asset.cloud_account_id or str(order.account_label or '') in account_labels
    if account_labels:
        return str(order.account_label or '') in account_labels or not str(order.account_label or '').strip()
    return True


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _bulk_infer_asset_orders(assets):
    targets = []
    all_names = set()
    all_ips = set()
    providers = set()
    for asset in assets:
        if getattr(asset, 'order_id', None):
            continue
        names, ips = _asset_lookup_values(asset)
        if not names and not ips:
            continue
        account_labels = _asset_account_label_variants(asset)
        targets.append((asset, names, ips, account_labels))
        all_names.update(names)
        all_ips.update(ips)
        provider = str(getattr(asset, 'provider', '') or '').strip()
        if provider:
            providers.add(provider)
    if not targets:
        return {}

    lookup = Q()
    if all_ips:
        lookup |= Q(public_ip__in=all_ips) | Q(previous_public_ip__in=all_ips)
    if all_names:
        lookup |= Q(server_name__in=all_names) | Q(instance_id__in=all_names) | Q(provider_resource_id__in=all_names)
    queryset = CloudServerOrder.objects.select_related('user', 'plan', 'cloud_account').filter(lookup)
    if providers:
        queryset = queryset.filter(provider__in=providers)
    orders = list(queryset.order_by('-updated_at', '-id'))

    by_ip = {}
    by_name = {}
    for order in orders:
        for value in {str(order.public_ip or '').strip(), str(order.previous_public_ip or '').strip()}:
            if value:
                by_ip.setdefault(value, []).append(order)
        for value in {str(order.server_name or '').strip(), str(order.instance_id or '').strip(), str(order.provider_resource_id or '').strip()}:
            if value:
                by_name.setdefault(value, []).append(order)

    inferred = {}
    for asset, names, ips, account_labels in targets:
        for ip in ips:
            for order in by_ip.get(ip, []):
                if _order_matches_asset_lookup(order, asset, account_labels):
                    inferred[asset.id] = order
                    break
            if asset.id in inferred:
                break
        if asset.id in inferred:
            continue
        for name in names:
            for order in by_name.get(name, []):
                if _order_matches_asset_lookup(order, asset, account_labels):
                    inferred[asset.id] = order
                    break
            if asset.id in inferred:
                break
    return inferred


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _build_cloud_asset_payload_context(assets, *, allow_mutation=False):
    asset_list = list(assets)
    return CloudAssetPayloadContext(
        active_account_labels=list_cloud_account_labels(True),
        inferred_orders=_bulk_infer_asset_orders(asset_list),
        allow_mutation=allow_mutation,
        now=timezone.now(),
    )


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_payloads(assets, *, allow_mutation=False):
    asset_list = list(assets)
    context = _build_cloud_asset_payload_context(asset_list, allow_mutation=allow_mutation)
    return [_asset_payload(asset, context=context) for asset in asset_list]


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _display_cloud_asset_note(note: str | None) -> str:
    noisy_prefixes = (
        'Get:', 'Hit:', 'Ign:', 'Err:', 'Fetched ', 'Reading package lists',
        'Building dependency tree', 'Reading state information', 'Selecting previously',
        'Preparing to unpack', 'Unpacking ', 'Setting up ', 'Processing triggers',
        'Created symlink ', 'Synchronizing state', 'Need to get ', 'After this operation',
        'The following ', '0 upgraded,', 'debconf:', 'apt-listchanges:', 'WARNING:',
    )
    lines = []
    seen = set()
    for raw_line in str(note or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if 'tg://proxy?' in line or 'socks5://' in line:
            continue
        if line.startswith(('TG链接:', '分享链接:', '扩展链接:', 'SOCKS5链接:')):
            continue
        if line.startswith(noisy_prefixes):
            continue
        if line.startswith('状态: ') and ('最近同步:' in line or '覆盖同步时间:' in line):
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return '\n'.join(lines)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_shutdown_enabled(asset, order=None) -> bool:
    return getattr(asset, 'shutdown_enabled', True) is not False


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _static_ip_name_from_resource_id(value) -> str:
    text = str(value or '').strip()
    if not text or 'StaticIp' not in text:
        return ''
    return text.rsplit('/', 1)[-1] or text


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_static_ip_name(asset, order=None) -> str:
    asset_static_ip_name = ''
    provider_status = str(getattr(asset, 'provider_status', '') or '')
    provider_resource_id = str(getattr(asset, 'provider_resource_id', '') or '')
    if (
        '未附加' in provider_status
        or '固定IP保留' in provider_status
        or 'StaticIp' in provider_resource_id
    ):
        asset_static_ip_name = (
            _static_ip_name_from_resource_id(provider_resource_id)
            or (
                str(getattr(asset, 'asset_name', '') or '').strip()
                if not str(getattr(asset, 'instance_id', '') or '').strip()
                else ''
            )
        )
    return asset_static_ip_name or str(getattr(order, 'static_ip_name', '') if order else '').strip()


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_provider_status_label(asset, account_label: str | None = None, *, active_account_labels=None) -> str:
    if active_account_labels is None:
        active_account_labels = set(list_cloud_account_labels(True))
    else:
        active_account_labels = set(active_account_labels)
    account = getattr(asset, 'cloud_account', None)
    asset_account_label = str(account_label or getattr(asset, 'account_label', '') or '').strip()
    account_disabled = (
        getattr(account, 'is_active', True) is False
        or (asset_account_label and asset_account_label not in active_account_labels)
    )
    if account_disabled:
        base_label = _provider_status_label(asset.provider_status)
        return f'云账号已停用 / {base_label}' if base_label and base_label != '-' else '云账号已停用'
    if asset.status == CloudAsset.STATUS_DELETED:
        return '已删除'
    if asset.status == CloudAsset.STATUS_TERMINATED:
        return '已终止'
    label = _provider_status_label(asset.provider_status)
    parts = [part.strip() for part in str(label or '').split('/') if part.strip()]
    if len(parts) > 1 and '运行中' in parts and all(part in {'运行中', '正常'} for part in parts):
        return '运行中'
    return label


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_risk_state(asset, order, expires_at, provider_status_label, display_status, user) -> dict:
    now = timezone.now()
    reasons = []
    risk_statuses = []
    risk_status = 'other'
    risk_label = '其他'
    risk_rank = 99
    raw_provider_text = str(asset.provider_status or '')
    provider_text = str(provider_status_label or raw_provider_text or '')
    provider_match_text = '\n'.join(filter(None, [provider_text, raw_provider_text]))
    status_text = str(display_status or asset.status or '')
    note_text = str(asset.note or '')
    days_left = _days_left(expires_at)
    shutdown_enabled = _cloud_asset_shutdown_enabled(asset, order)
    is_unattached_ip = (
        '未附加' in provider_match_text
        or '固定IP保留中' in provider_match_text
        or '固定 IP 保留中' in provider_match_text
        or '未附加IP' in note_text
        or '未附加 IP' in note_text
        or '未附加固定IP' in note_text
        or '固定IP保留中' in note_text
        or '固定 IP 保留中' in note_text
        or status_text == 'unattached'
    )

    # 功能：设置运行状态或配置值；当前函数属于 后台 API 接口。
    def set_risk(status: str, label: str, rank: int, reason: str):
        nonlocal risk_status, risk_label, risk_rank
        if status and status not in risk_statuses:
            risk_statuses.append(status)
        if rank < risk_rank:
            risk_status = status
            risk_label = label
            risk_rank = rank
        if reason and reason not in reasons:
            reasons.append(reason)

    if status_text == CloudAsset.STATUS_RUNNING and isinstance(days_left, int) and days_left > 7:
        set_risk('normal', '运行中', 20, '')
    if not user:
        set_risk('unbound_user', '未绑定用户', 12, '未绑定用户')
    if not getattr(asset, 'telegram_group_id', None):
        set_risk('unbound_group', '未绑定群组', 14, '未绑定群组')
    if order and not getattr(order, 'auto_renew_enabled', False):
        set_risk('auto_renew_off', '续费关闭', 13, '自动续费关闭')
    if not shutdown_enabled:
        set_risk('shutdown_disabled', '资产开关关闭', 4, '本资产已关闭自动生命周期')
    if is_unattached_ip:
        set_risk('unattached_ip', '未附加固定IP', 3, '固定IP未附加实例')
    if not is_unattached_ip and expires_at and expires_at <= now:
        set_risk('expired', '已过期', 1, '服务已过期')
    elif not is_unattached_ip and isinstance(days_left, int) and days_left <= 7:
        set_risk('due_soon', '即将到期', 2, f'剩余 {days_left} 天')
    if (
        status_text in {'failed', 'unknown'}
        or '失败' in provider_text
        or '异常' in provider_text
        or '云账号已停用' in provider_text
        or '云上未找到' in provider_text
        or '云上不存在' in provider_text
        or '待确认' in provider_text
    ):
        set_risk('abnormal', '异常/待确认', 5, provider_text or '状态异常')
    if '云账号已停用' in provider_text:
        set_risk('account_disabled', '云账号已停用', 6, '云账号已停用')
    if status_text in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_TERMINATING}:
        set_risk('deleted', '已删除/终止', 30, '资产已删除或终止')

    return {
        'risk_status': risk_status,
        'risk_statuses': risk_statuses or ['other'],
        'risk_label': risk_label,
        'risk_rank': risk_rank,
        'risk_reasons': reasons,
        'shutdown_enabled': shutdown_enabled,
    }


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _asset_payload(asset, *, context: CloudAssetPayloadContext | None = None):
    context = context or CloudAssetPayloadContext(active_account_labels=list_cloud_account_labels(True))
    order = getattr(asset, 'order', None) or context.inferred_orders.get(getattr(asset, 'id', None))
    if not order and context.allow_mutation:
        order = _infer_asset_order(asset)
    if context.allow_mutation and order and not getattr(asset, 'order_id', None):
        asset.order = order
        asset.order_id = order.id
    user = asset.user or getattr(order, 'user', None)
    if not user and context.allow_mutation:
        user = sync_cloud_asset_user_binding(asset)
    user_payload = None
    if user:
        usernames = user.usernames
        user_payload = _user_payload({
            'id': user.id,
            'tg_user_id': user.tg_user_id,
            'username': user.username,
            'first_name': user.first_name,
            'usernames': usernames,
            'primary_username': usernames[0] if usernames else '',
        })
    expires_at = asset.actual_expires_at
    if _is_unattached_ip_asset(asset) and not expires_at:
        expires_at = _unattached_ip_delete_due_at(now=context.now)
        if context.allow_mutation:
            asset.actual_expires_at = expires_at
            asset.save(update_fields=['actual_expires_at', 'updated_at'])
    countdown_label = _countdown_label(expires_at)
    preserve_link_status = _preserve_link_status_with_countdown(
        _preserve_link_status_label(asset.note, getattr(order, 'provision_note', None)),
        countdown_label,
    )
    account_label = asset.account_label or cloud_account_label(getattr(asset, 'cloud_account', None)) or getattr(order, 'account_label', '')
    cloud_account_id = asset.cloud_account_id or getattr(order, 'cloud_account_id', None)
    display_status = asset.status
    display_status_label = '旧机保留中' if asset.status == CloudAsset.STATUS_DELETING and '旧机保留期' in str(asset.provider_status or '') else _status_label(asset.status, CloudAsset.STATUS_CHOICES)
    provider_status_label = _cloud_asset_provider_status_label(asset, account_label, active_account_labels=context.active_account_labels)
    provider_account_disabled = '云账号已停用' in str(provider_status_label or '')
    if asset.status == CloudAsset.STATUS_UNKNOWN and '未附加' in str(asset.provider_status or ''):
        display_status = 'unattached'
        display_status_label = '未附加固定IP'
        provider_status_label = '云账号已停用 / 未附加固定IP' if provider_account_disabled else '未附加固定IP'
    elif asset.status == CloudAsset.STATUS_UNKNOWN and '固定IP仍存在但未附加' in str(asset.provider_status or ''):
        display_status = 'unattached'
        display_status_label = '未附加固定IP'
        provider_status_label = '云账号已停用 / 固定IP仍存在但未附加' if provider_account_disabled else '固定IP仍存在但未附加'
    risk_state = _cloud_asset_risk_state(asset, order, expires_at, provider_status_label, display_status, user)
    return {
        'id': asset.id,
        'kind': asset.kind,
        'source': asset.source,
        'source_label': _server_source_label(asset.source),
        'provider': asset.provider,
        'provider_label': _provider_label(asset.provider),
        'cloud_account_id': cloud_account_id,
        'account_label': account_label,
        'region_code': asset.region_code,
        'region_label': _region_label(getattr(asset, 'region_code', None), asset.region_name),
        'region_name': asset.region_name,
        'asset_name': asset.asset_name,
        'instance_id': asset.instance_id,
        'provider_resource_id': asset.provider_resource_id,
        'static_ip_name': _cloud_asset_static_ip_name(asset, order),
        'public_ip': asset.public_ip or asset.previous_public_ip or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None),
        'previous_public_ip': asset.previous_public_ip or getattr(order, 'previous_public_ip', None),
        'mtproxy_link': asset.mtproxy_link or getattr(order, 'mtproxy_link', None),
        'proxy_links': asset.proxy_links or getattr(order, 'proxy_links', None) or [],
        'mtproxy_port': asset.mtproxy_port or getattr(order, 'mtproxy_port', None),
        'mtproxy_secret': _mask_secret(asset.mtproxy_secret or getattr(order, 'mtproxy_secret', None)),
        'has_mtproxy_secret': bool(asset.mtproxy_secret or getattr(order, 'mtproxy_secret', None)),
        'mtproxy_host': asset.mtproxy_host or getattr(order, 'mtproxy_host', None),
        'note': _display_cloud_asset_note(asset.note),
        'sort_order': asset.sort_order,
        'actual_expires_at': _iso(expires_at),
        'days_left': _days_left(expires_at),
        'status_countdown': countdown_label,
        'preserve_link_status': preserve_link_status,
        'ip_change_quota': max(int(getattr(order, 'ip_change_quota', 0) or 0), 0) if order else 0,
        'price': _decimal_to_str(asset.price if asset.price is not None else (order.total_amount if order and order.total_amount is not None else None), 2),
        'currency': asset.currency or (order.currency if order else ''),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'telegram_group_id': asset.telegram_group_id,
        'telegram_group_chat_id': asset.telegram_group.chat_id if asset.telegram_group_id and asset.telegram_group else None,
        'telegram_group_title': asset.telegram_group.title if asset.telegram_group_id and asset.telegram_group else '',
        'telegram_group_username': asset.telegram_group.username if asset.telegram_group_id and asset.telegram_group else '',
        'order_id': order.id if order else None,
        'order_no': order.order_no if order else '',
        'order_detail_path': f'/admin/cloud-orders/{order.id}' if order else '',
        'order_link_path': f'/admin/cloud-orders/{order.id}' if order else '',
        'can_auto_renew': bool(user and not (display_status == 'unattached' or '未附加' in str(provider_status_label or ''))),
        'auto_renew_enabled': bool(getattr(order, 'auto_renew_enabled', False)),
        'status': display_status,
        'status_label': display_status_label,
        'provider_status': provider_status_label,
        **risk_state,
        'is_active': asset.is_active,
        'updated_at': _iso(asset.updated_at),
    }

# 功能：处理 后台 API 接口 中的 cloud assets list 业务流程。
@dashboard_login_required
@require_GET
def cloud_assets_list(request):
    keyword = _get_keyword(request)
    grouped = (request.GET.get('grouped') or '').lower() in {'1', 'true', 'yes'}
    group_by = (request.GET.get('group_by') or 'telegram_group').strip().lower()
    if group_by not in {'telegram_group', 'user'}:
        group_by = 'telegram_group'
    paginated = (request.GET.get('paginated') or '').lower() in {'1', 'true', 'yes'}
    risk_status = (request.GET.get('risk_status') or 'all').strip()
    show_deleted = (request.GET.get('show_deleted') or '').lower() in {'1', 'true', 'yes'}
    sort_by = (request.GET.get('sort_by') or '').strip().lower()
    sort_direction = _dashboard_sort_direction(request)
    try:
        _ensure_cloud_asset_dashboard_snapshots('cloud_assets_list')
        base_queryset = _dashboard_snapshot_queryset(keyword)
        risk_counts = _dashboard_snapshot_risk_counts(base_queryset)
        queryset = _filter_dashboard_snapshots_by_risk(base_queryset, risk_status)
        if not show_deleted and risk_status in {'', 'all'}:
            queryset = queryset.filter(
                Q(risk_unattached_ip=True)
                | (
                    Q(is_active=True)
                    & ~Q(status__in=[
                        CloudAsset.STATUS_DELETED,
                        CloudAsset.STATUS_DELETING,
                        CloudAsset.STATUS_EXPIRED,
                        CloudAsset.STATUS_TERMINATED,
                        CloudAsset.STATUS_TERMINATING,
                        CloudAsset.STATUS_UNKNOWN,
                    ])
                )
            )
        if not grouped and paginated:
            page_items, total, total_pages, page, page_size = _paginate_dashboard_snapshot_queryset(
                queryset,
                request,
                sort_by=sort_by,
                sort_direction=sort_direction,
                default_size=20,
                min_size=10,
                max_size=200,
            )
            return _ok({'items': page_items, 'total': total, 'page': page, 'page_size': page_size, 'total_pages': total_pages, 'risk_counts': risk_counts})
        if grouped and paginated:
            page_groups, page_items, total, total_pages, page, page_size = _dashboard_snapshot_group_page(
                queryset,
                request,
                group_by=group_by,
                sort_by=sort_by,
                sort_direction=sort_direction,
            )
            return _ok({'groups': page_groups, 'items': page_items, 'total': total, 'page': page, 'page_size': page_size, 'total_pages': total_pages, 'risk_counts': risk_counts})
        items = _snapshot_payloads(list(queryset.order_by(*_dashboard_snapshot_ordering(sort_by, sort_direction))))
    except (OperationalError, ProgrammingError):
        if grouped and paginated:
            return _ok({'groups': [], 'items': [], 'total': 0, 'page': 1, 'page_size': 20, 'total_pages': 1, 'risk_counts': {'all': 0}})
        if grouped:
            return _ok({'groups': [], 'items': [], 'risk_counts': {'all': 0}})
        if paginated:
            return _ok({'items': [], 'total': 0, 'page': 1, 'page_size': 20, 'total_pages': 1, 'risk_counts': {'all': 0}})
        return _ok([])

    if not grouped:
        return _ok(items)

    ordered_groups = _group_cloud_asset_payloads(items, group_by)
    return _ok({'groups': ordered_groups, 'items': items, 'risk_counts': risk_counts})


# 功能：处理 后台 API 接口 中的 cloud assets risk summary 业务流程。
@dashboard_login_required
@require_GET
def cloud_assets_risk_summary(request):
    keyword = _get_keyword(request)
    try:
        _ensure_cloud_asset_dashboard_snapshots('cloud_assets_risk_summary')
        queryset = _dashboard_snapshot_queryset(keyword)
        return _ok({'risk_counts': _dashboard_snapshot_risk_counts(queryset), 'total': queryset.count()})
    except (OperationalError, ProgrammingError):
        return _ok({'risk_counts': {'all': 0}, 'total': 0})
