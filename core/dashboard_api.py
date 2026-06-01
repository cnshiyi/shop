"""Shared dashboard API helpers.

This module is intentionally domain-neutral. Backend apps should import
generic response, payload, and formatting helpers from here instead of
reaching into ``bot.api``.
"""

import json
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from django.db.models import Q
from django.contrib.sessions.models import Session
from django.http import JsonResponse
from django.utils import timezone

DASHBOARD_SESSION_IDLE_SECONDS = 60 * 60
DASHBOARD_SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS', 'TRACE'}


def _decimal_to_str(value, places=3):
    if value is None:
        value = Decimal('0')
    elif not isinstance(value, Decimal):
        value = Decimal(str(value))
    if places is None:
        places = 3
    places = min(int(places), 3)
    quantizer = Decimal('1').scaleb(-places)
    value = value.quantize(quantizer, rounding=ROUND_DOWN)
    text = format(value, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def _parse_decimal(value, field_label):
    raw = str(value or '').strip()
    if raw == '':
        raise ValueError(f'{field_label}不能为空')
    try:
        return Decimal(raw).quantize(Decimal('0.001'), rounding=ROUND_DOWN)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'{field_label}格式不正确')


def _ok(data):
    return JsonResponse({'code': 0, 'data': data, 'message': 'ok'})


def _error(message, code=1, status=400):
    return JsonResponse({'code': code, 'message': message, 'data': None}, status=status)


def _iso(value):
    if not value:
        return None
    return timezone.localtime(value).isoformat() if timezone.is_aware(value) else value.isoformat()


def _status_label(status, choices=()):
    hardcoded = {
        'running': '运行中',
        'pending': '等待中',
        'starting': '启动中',
        'stopping': '停止中',
        'stopped': '已关机',
        'suspended': '已停机',
        'terminating': '终止中',
        'terminated': '已终止',
        'deleting': '删除中',
        'deleted': '已删除',
        'expired': '已过期',
        'expired_grace': '到期延停',
        'unknown': '未知状态',
    }
    mapping = dict(choices or [])
    return hardcoded.get(status) or mapping.get(status, status or '-')


def _days_left(value):
    if not value:
        return None
    delta = value - timezone.now()
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        overdue_days = ((-total_seconds) + 86399) // 86400
        return -max(overdue_days, 1)
    return delta.days + (1 if delta.seconds > 0 or delta.microseconds > 0 else 0)


def _countdown_label(value):
    if not value:
        return '-'
    delta = value - timezone.now()
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        overdue_days = ((-total_seconds) + 86399) // 86400
        return f'已过期 {max(overdue_days, 1)} 天'
    total_hours = (total_seconds + 3599) // 3600
    if total_hours < 24:
        return f'剩余 {total_hours} 小时'
    total_days = (total_seconds + 86399) // 86400
    return f'剩余 {total_days} 天'


def _provider_label(provider):
    mapping = {
        'aliyun_simple': '阿里云',
        'aws_lightsail': 'AWS Lightsail',
    }
    return mapping.get(provider, provider or '-')


def _provider_status_label(value):
    if not value:
        return '-'
    mapping = {
        'running': '运行中',
        'normal': '正常',
        'starting': '启动中',
        'pending': '等待中',
        'stopping': '停止中',
        'stopped': '已关机',
        'disabled': '已禁用',
        'expired': '已过期',
        'deleting': '删除中',
        'deleted': '已删除',
        'terminated': '已终止',
        'terminating': '终止中',
        'shutting-down': '关机中',
        'missing': '未发现',
    }
    parts = [part.strip() for part in str(value).split('/')]
    translated = []
    for part in parts:
        key = part.strip().lower()
        translated.append(mapping.get(key, part.strip() or '-'))
    return ' / '.join([part for part in translated if part]) or '-'


def _server_source_label(source):
    mapping = {
        'aliyun': '阿里云自动同步',
        'aws_manual': 'AWS 手工录入',
        'aws_sync': 'AWS 自动同步',
        'order': '订单创建',
    }
    return mapping.get(source, source or '-')


def _region_label(region_code, region_name=None):
    mapping = {
        'cn-qingdao': '华北1（青岛）',
        'cn-beijing': '华北2（北京）',
        'cn-zhangjiakou': '华北3（张家口）',
        'cn-huhehaote': '华北5（呼和浩特）',
        'cn-wulanchabu': '华北6（乌兰察布）',
        'cn-hangzhou': '华东1（杭州）',
        'cn-shanghai': '华东2（上海）',
        'cn-nanjing': '华东5（南京）',
        'cn-fuzhou': '华东6（福州）',
        'cn-shenzhen': '华南1（深圳）',
        'cn-heyuan': '华南2（河源）',
        'cn-guangzhou': '华南3（广州）',
        'cn-chengdu': '西南1（成都）',
        'cn-hongkong': '中国香港',
        'ap-southeast-1': '新加坡',
        'ap-southeast-2': '澳大利亚（悉尼）',
        'ap-southeast-3': '马来西亚（吉隆坡）',
        'ap-southeast-5': '印度尼西亚（雅加达）',
        'ap-southeast-6': '菲律宾（马尼拉）',
        'ap-southeast-7': '泰国（曼谷）',
        'ap-northeast-1': '日本（东京）',
        'ap-northeast-2': '韩国（首尔）',
        'us-west-1': '美国西部（硅谷）',
        'us-east-1': '美国东部（弗吉尼亚）',
        'eu-central-1': '德国（法兰克福）',
        'eu-west-1': '英国（伦敦）',
        'me-east-1': '阿联酋（迪拜）',
        'us-east-2': '美国东部（俄亥俄）',
        'us-west-2': '美国西部（俄勒冈）',
        'af-south-1': '非洲（开普敦）',
        'ap-east-1': '亚太（香港）',
        'ap-south-1': '亚太（孟买）',
        'ap-south-2': '亚太（海得拉巴）',
        'ap-southeast-4': '亚太（墨尔本）',
        'ap-southeast-8': '亚太（台北）',
        'ap-southeast-9': '亚太（新西兰）',
        'ap-northeast-3': '亚太（大阪）',
        'ca-central-1': '加拿大（中部）',
        'ca-west-1': '加拿大西部（卡尔加里）',
        'eu-north-1': '欧洲（斯德哥尔摩）',
        'eu-south-1': '欧洲（米兰）',
        'eu-south-2': '欧洲（西班牙）',
        'eu-west-2': '欧洲（伦敦）',
        'eu-west-3': '欧洲（巴黎）',
        'il-central-1': '以色列（特拉维夫）',
        'me-central-1': '中东（阿联酋）',
        'me-south-1': '中东（巴林）',
        'sa-east-1': '南美洲（圣保罗）',
    }
    code = (region_code or '').strip()
    name = (region_name or '').strip()
    if code and code in mapping:
        return mapping[code]
    if name in mapping:
        return mapping[name]
    return name or code or '-'


def _split_usernames(username):
    if not username:
        return []
    normalized = (
        str(username)
        .replace('，', ',')
        .replace('｜', ',')
        .replace('|', ',')
        .replace(' / ', ',')
        .replace('/', ',')
    )
    parts = [item.strip().lstrip('@') for item in normalized.split(',')]
    result = []
    seen = set()
    for item in parts:
        key = item.lower()
        if item and key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _user_payload(item):
    usernames = item.get('usernames') or _split_usernames(item.get('username'))
    first_name = (item.get('first_name') or '').strip()
    primary_username = item.get('primary_username') or (usernames[0] if usernames else '')
    display_name = first_name or (f'@{primary_username}' if primary_username else str(item.get('tg_user_id') or ''))
    return {
        **item,
        'display_name': display_name,
        'primary_username': primary_username,
        'usernames': usernames,
        'username_label': '｜'.join(f'@{name}' for name in usernames) if usernames else '-',
    }


def _read_payload(request):
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}') if request.body else {}
    except Exception:
        payload = {}
    if payload:
        return payload
    return request.POST.dict() if hasattr(request.POST, 'dict') else request.POST


def _json_payload(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        return {}


def _payload_bool(payload, key, default=False):
    if key not in payload:
        return default
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _parse_runtime_time_point(raw: str, fallback: str = '15:00') -> tuple[int, int]:
    try:
        hour_text, minute_text = str(raw or fallback).strip().split(':', 1)
        return min(max(int(hour_text), 0), 23), min(max(int(minute_text), 0), 59)
    except Exception:
        hour_text, minute_text = fallback.split(':', 1)
        return int(hour_text), int(minute_text)


def _get_keyword(request):
    return (request.GET.get('keyword') or request.GET.get('q') or request.GET.get('search') or '').strip()


def _apply_keyword_filter(queryset, keyword, fields):
    if not keyword:
        return queryset
    condition = Q()
    for field in fields:
        condition |= Q(**{f'{field}__icontains': keyword})
    return queryset.filter(condition)


def _staff_required(user):
    return user.is_active and (user.is_staff or user.is_superuser)


def _refresh_dashboard_session(request, session_key: str | None = None):
    session = getattr(request, 'session', None)
    if session is not None:
        session.set_expiry(DASHBOARD_SESSION_IDLE_SECONDS)
    if session_key:
        Session.objects.filter(session_key=session_key, expire_date__gt=timezone.now()).update(
            expire_date=timezone.now() + timezone.timedelta(seconds=DASHBOARD_SESSION_IDLE_SECONDS)
        )


def _session_token_for_request(request) -> str:
    if not request.session.session_key:
        request.session.save()
    return f'session-{request.session.session_key}'


def _dashboard_session_payload(request):
    return {
        'accessToken': _session_token_for_request(request),
        'expiresIn': max(0, request.session.get_expiry_age()),
    }


def _user_from_bearer_session(token: str):
    user, _session_key = _user_and_session_key_from_bearer_session(token)
    return user


def _user_and_session_key_from_bearer_session(token: str):
    prefix = 'session-'
    if not token.startswith(prefix):
        return None, None
    session_key = token[len(prefix):].strip()
    if not session_key or session_key.isdigit():
        return None, None
    session = Session.objects.filter(session_key=session_key, expire_date__gt=timezone.now()).first()
    if not session:
        return None, None
    data = session.get_decoded()
    raw_user_id = data.get('_auth_user_id')
    if not raw_user_id:
        return None, None
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=raw_user_id, is_active=True).first()
    return user, session_key if user else None


def _bearer_token_from_request(request) -> str:
    auth_header = request.headers.get('Authorization') or ''
    prefix = 'Bearer '
    if not auth_header.startswith(prefix):
        return ''
    return auth_header[len(prefix):].strip()


def _dashboard_requires_bearer(request) -> bool:
    return str(getattr(request, 'method', '') or '').upper() not in DASHBOARD_SAFE_METHODS


def _authenticate_dashboard_request(request, *, require_bearer: bool = False):
    token = _bearer_token_from_request(request)
    if token:
        user, session_key = _user_and_session_key_from_bearer_session(token)
        if user and _staff_required(user):
            request.user = user
            _refresh_dashboard_session(request, session_key=session_key)
            return user
        return None
    if require_bearer:
        return None
    user = getattr(request, 'user', None)
    if user and _staff_required(user):
        _refresh_dashboard_session(request)
        return user
    return None


def dashboard_login_required(view_func):
    def wrapped(request, *args, **kwargs):
        if not _authenticate_dashboard_request(request, require_bearer=_dashboard_requires_bearer(request)):
            return _error('请先登录', status=401)
        return view_func(request, *args, **kwargs)
    return wrapped


def dashboard_superuser_required(view_func):
    @dashboard_login_required
    def wrapped(request, *args, **kwargs):
        if not getattr(request.user, 'is_superuser', False):
            return _error('需要超级管理员权限', status=403)
        return view_func(request, *args, **kwargs)
    return wrapped
