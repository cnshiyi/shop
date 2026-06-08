"""Dashboard API views for Telegram login accounts, chats, and group filters."""

import re
import unicodedata

from asgiref.sync import async_to_sync
from django.db.models import Count, Max, Q
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.dashboard_api import (
    _error,
    _get_keyword,
    _iso,
    _ok,
    _payload_bool,
    _read_payload,
    _split_usernames,
    _user_payload,
    dashboard_login_required,
    dashboard_superuser_required,
)
from bot.models import TelegramChatArchive, TelegramChatMessage, TelegramGroupFilter, TelegramLoginAccount, TelegramUser
from bot.services import _get_or_create_user_sync
from core.models import SiteConfig
from core.runtime_config import get_runtime_config


def _telegram_user_labels(user):
    if not user:
        return '未绑定用户', '-'
    usernames = _split_usernames(getattr(user, 'username', '') or getattr(user, 'primary_username', ''))
    first_name = (getattr(user, 'first_name', '') or '').strip()
    primary_username = getattr(user, 'primary_username', '') or (usernames[0] if usernames else '')
    display_name = first_name or (f'@{primary_username}' if primary_username else str(getattr(user, 'tg_user_id', '') or getattr(user, 'id', '')))
    username_label = '｜'.join(f'@{name}' for name in usernames) if usernames else '-'
    return display_name, username_label


def _telegram_login_account_payload(item):
    usernames = TelegramUser.normalize_usernames(item.username)
    return {
        'id': item.id,
        'label': item.label,
        'phone': item.phone or '',
        'tg_user_id': item.tg_user_id,
        'username': '｜'.join(usernames) if usernames else '',
        'status': item.status,
        'note': item.note or '',
        'notify_enabled': bool(getattr(item, 'notify_enabled', True)),
        'listener_push_enabled': bool(getattr(item, 'listener_push_enabled', True)),
        'has_session': bool(getattr(item, 'session_string', None)),
        'last_synced_at': _iso(item.last_synced_at),
        'created_at': _iso(item.created_at),
        'updated_at': _iso(item.updated_at),
    }


def _telegram_chat_user_payload(user, latest=None, message_count=0):
    _, username_label = _telegram_user_labels(user)
    return {
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'display_name': user.first_name or user.primary_username or str(user.tg_user_id),
        'first_name': user.first_name or '',
        'primary_username': user.primary_username,
        'username_label': username_label,
        'usernames': user.usernames,
        'message_count': message_count,
        'latest_chat_id': latest.chat_id if latest else None,
        'latest_login_account_id': latest.login_account_id if latest else None,
        'latest_message': latest.text if latest else '',
        'latest_at': _iso(latest.created_at) if latest else None,
    }


def _telegram_chat_payload(chat_id, latest, message_count, archived_ids=None):
    username = latest.username_snapshot or ''
    title = latest.chat_title or latest.first_name_snapshot or username or str(chat_id)
    subtitle = ''
    if latest.chat_title and (latest.first_name_snapshot or username):
        subtitle = latest.first_name_snapshot or f'@{username}'
    elif latest.login_account_id and latest.login_account:
        subtitle = latest.login_account.label
    return {
        'chat_id': chat_id,
        'is_group': int(chat_id) < 0,
        'login_account_id': latest.login_account_id,
        'login_account_label': latest.login_account.label if latest.login_account_id and latest.login_account else '',
        'title': title,
        'subtitle': subtitle,
        'latest_message': latest.text or f'[{latest.content_type}]',
        'latest_at': _iso(latest.created_at),
        'message_count': message_count,
        'archived': chat_id in (archived_ids or set()),
        'source': latest.source or 'bot',
        'source_label': '个人号' if str(latest.source or '').startswith('account') else '机器人',
    }


def _telegram_message_payload(item):
    return {
        'id': item.id,
        'tg_user_id': item.tg_user_id,
        'chat_id': item.chat_id,
        'message_id': item.message_id,
        'login_account_id': item.login_account_id,
        'login_account_label': item.login_account.label if item.login_account_id and item.login_account else '',
        'direction': item.direction,
        'direction_label': item.get_direction_display(),
        'content_type': item.content_type,
        'text': item.text or '',
        'username_snapshot': item.username_snapshot or '',
        'first_name_snapshot': item.first_name_snapshot or '',
        'chat_title': item.chat_title or '',
        'source': item.source or 'bot',
        'source_label': '个人号' if str(item.source or '').startswith('account') else '机器人',
        'created_at': _iso(item.created_at),
    }


def _telegram_group_filter_payload(item):
    return {
        'id': item.id,
        'chat_id': item.chat_id,
        'title': item.title or '',
        'username': item.username or '',
        'enabled': bool(item.enabled),
        'push_enabled': bool(getattr(item, 'push_enabled', False)),
        'collapsed': bool(item.collapsed),
        'archived': bool(getattr(item, 'archived', False)),
        'updated_at': _iso(item.updated_at),
        'created_at': _iso(item.created_at),
    }


def _telegram_group_member_payload(item, latest):
    username = latest.username_snapshot or (latest.user.primary_username if latest.user_id and latest.user else '')
    first_name = latest.first_name_snapshot or (latest.user.first_name if latest.user_id and latest.user else '')
    display_name = first_name or (f'@{username}' if username else str(item['tg_user_id']))
    return {
        'tg_user_id': item['tg_user_id'],
        'username': username or '',
        'first_name': first_name or '',
        'display_name': display_name,
        'display_label': f'{display_name} (ID: {item["tg_user_id"]})',
        'message_count': item['message_count'],
        'last_seen_at': _iso(item['last_seen_at']),
    }


def _limited_username_string(value):
    result = []
    for username in TelegramUser.normalize_usernames(value):
        candidate = ','.join([*result, username])
        if len(candidate) > 191:
            continue
        result.append(username)
    return ','.join(result) or None


def _merge_login_account_usernames(current, incoming):
    return _limited_username_string([incoming, current])


def _normalize_telegram_group_username(value):
    return str(value or '').strip().lstrip('@')


def _telegram_group_identity_label(chat_id, title='', username=''):
    normalized_username = _normalize_telegram_group_username(username)
    if normalized_username:
        return f'@{normalized_username}'
    if str(title or '').strip():
        return str(title).strip()
    return str(chat_id)


def _validate_telegram_group_filter_payload(payload, *, current_id=None):
    raw_chat_id = payload.get('chat_id')
    title = str(payload.get('title') or '').strip()
    username = _normalize_telegram_group_username(payload.get('username'))
    try:
        chat_id = int(raw_chat_id)
    except (TypeError, ValueError):
        raise ValueError('群组 Chat ID 无效')
    if chat_id >= 0:
        raise ValueError('只允许保存群组/频道 Chat ID')
    if not title:
        raise ValueError('群组名称不能为空')
    if username and not username.replace('_', '').isalnum():
        raise ValueError(f'用户名格式不正确：@{username}')
    duplicate = TelegramGroupFilter.objects.filter(chat_id=chat_id)
    if current_id:
        duplicate = duplicate.exclude(id=current_id)
    duplicate = duplicate.first()
    if duplicate:
        raise ValueError(f'群组已保存：{_telegram_group_identity_label(duplicate.chat_id, duplicate.title, duplicate.username)}')
    if username:
        duplicate_username = TelegramGroupFilter.objects.filter(username__iexact=username)
        if current_id:
            duplicate_username = duplicate_username.exclude(id=current_id)
        duplicate_username = duplicate_username.first()
        if duplicate_username:
            raise ValueError(f'用户名已保存：@{duplicate_username.username}')
    return chat_id, title[:191], username[:191] or None


@dashboard_login_required
@require_GET
def telegram_accounts_overview(request):
    keyword = (request.GET.get('keyword') or '').strip().lstrip('@')
    scope = (request.GET.get('scope') or '').strip().lower()
    include_archived = (request.GET.get('archived') or '').strip() in {'1', 'true', 'yes'}
    accounts = TelegramLoginAccount.objects.order_by('-updated_at', '-id')
    if keyword:
        accounts = accounts.filter(Q(label__icontains=keyword) | Q(phone__icontains=keyword) | Q(username__icontains=keyword))
    if scope == 'accounts':
        return _ok({
            'accounts': [_telegram_login_account_payload(item) for item in accounts[:50]],
            'chats': [],
            'users': [],
            'messages': [],
        })

    users = TelegramUser.objects.order_by('-updated_at', '-id')
    if keyword:
        user_filter = Q(username__icontains=keyword) | Q(first_name__icontains=keyword)
        if keyword.isdigit():
            user_filter |= Q(tg_user_id=int(keyword))
        users = users.filter(user_filter)

    user_items = list(users[:100])
    user_ids = [item.tg_user_id for item in user_items]
    user_messages = TelegramChatMessage.objects.filter(tg_user_id__in=user_ids)
    counts = dict(
        user_messages.values('tg_user_id')
        .annotate(total=Count('id'))
        .values_list('tg_user_id', 'total')
    )
    latest_by_user = {}
    for msg in (
        user_messages.select_related('login_account')
        .order_by('-created_at', '-id')
        .iterator(chunk_size=200)
    ):
        latest_by_user.setdefault(msg.tg_user_id, msg)
        if len(latest_by_user) >= len(user_ids):
            break
    if scope in {'', 'users'}:
        return _ok({
            'accounts': [_telegram_login_account_payload(item) for item in accounts[:50]],
            'chats': [],
            'users': [_telegram_chat_user_payload(user, latest_by_user.get(user.tg_user_id), counts.get(user.tg_user_id, 0)) for user in user_items],
            'messages': [],
        })

    archived_ids = set(TelegramChatArchive.objects.values_list('chat_id', flat=True))
    messages = TelegramChatMessage.objects.select_related('user', 'login_account').order_by('-created_at', '-id')
    if keyword:
        message_filter = Q(text__icontains=keyword) | Q(username_snapshot__icontains=keyword) | Q(first_name_snapshot__icontains=keyword)
        if keyword.isdigit():
            message_filter |= Q(tg_user_id=int(keyword))
        messages = messages.filter(message_filter)
    if not include_archived and archived_ids:
        messages = messages.exclude(chat_id__in=archived_ids)
    chat_counts = dict(messages.values('chat_id').annotate(total=Count('id')).values_list('chat_id', 'total'))
    latest_by_chat = {}
    for msg in messages.select_related('login_account').iterator(chunk_size=500):
        latest_by_chat.setdefault(msg.chat_id, msg)
        if len(latest_by_chat) >= 100:
            break
    return _ok({
        'accounts': [_telegram_login_account_payload(item) for item in accounts[:50]],
        'chats': [_telegram_chat_payload(chat_id, latest, chat_counts.get(chat_id, 0), archived_ids) for chat_id, latest in list(latest_by_chat.items())[:100]],
        'users': [_telegram_chat_user_payload(user, latest_by_user.get(user.tg_user_id), counts.get(user.tg_user_id, 0)) for user in user_items],
        'messages': [],
    })


def _telegram_api_credentials():
    api_id = SiteConfig.get('telegram_api_id', '') or get_runtime_config('telegram_api_id', '')
    api_hash = SiteConfig.get('telegram_api_hash', '') or get_runtime_config('telegram_api_hash', '')
    if not str(api_id or '').strip() or not str(api_hash or '').strip():
        raise ValueError('请先在系统设置中配置 Telegram API ID 和 API Hash')
    try:
        return int(str(api_id).strip()), str(api_hash).strip()
    except ValueError as exc:
        raise ValueError('Telegram API ID 必须是数字') from exc


def _normalize_telegram_phone(value) -> str:
    raw = unicodedata.normalize('NFKC', str(value or '').strip())
    if not raw:
        return ''
    phone = re.sub(r'[\s().-]+', '', raw)
    if phone.startswith('00'):
        phone = f'+{phone[2:]}'
    if not phone.startswith('+'):
        raise ValueError('手机号必须使用国际格式，请带国家码，例如 +8613800000000')
    if not re.fullmatch(r'\+[1-9]\d{7,14}', phone):
        raise ValueError('手机号格式无效，请使用国际格式，例如 +8613800000000')
    return phone


async def _telegram_send_code(phone: str, api_id: int, api_hash: str):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        session = client.session.save()
        return sent.phone_code_hash, session
    finally:
        await client.disconnect()


async def _telegram_sign_in_code(session_string: str, phone: str, code: str, phone_code_hash: str, api_id: int, api_hash: str):
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_string or ''), api_id, api_hash)
    await client.connect()
    try:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            return {'requires_password': True, 'session_string': client.session.save(), 'user': None}
        me = await client.get_me()
        return {'requires_password': False, 'session_string': client.session.save(), 'user': me}
    finally:
        await client.disconnect()


async def _telegram_check_session(session_string: str, api_id: int, api_hash: str):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_string or ''), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return {'ok': False, 'user': None, 'note': 'Telegram 会话已失效，请重新登录'}
        me = await client.get_me()
        return {'ok': True, 'user': me, 'note': '状态正常'}
    finally:
        await client.disconnect()


async def _telegram_send_message(session_string: str, chat_id: int, text: str, api_id: int, api_hash: str):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_string or ''), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise ValueError('Telegram 会话已失效，请重新登录')
        return await client.send_message(chat_id, text)
    finally:
        await client.disconnect()


async def _telegram_sign_in_password(session_string: str, password: str, api_id: int, api_hash: str):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_string or ''), api_id, api_hash)
    await client.connect()
    try:
        await client.sign_in(password=password)
        me = await client.get_me()
        return {'session_string': client.session.save(), 'user': me}
    finally:
        await client.disconnect()


def _update_login_account_from_me(item, me, status='logged_in'):
    item.status = status
    item.tg_user_id = getattr(me, 'id', None) or item.tg_user_id
    item.username = _merge_login_account_usernames(item.username, getattr(me, 'username', None)) or item.username
    item.label = getattr(me, 'first_name', None) or item.username or item.phone or item.label
    if item.tg_user_id:
        _get_or_create_user_sync(item.tg_user_id, getattr(me, 'username', None), getattr(me, 'first_name', None))
    item.note = '登录成功'
    item.last_synced_at = timezone.now()
    item.save(update_fields=['status', 'tg_user_id', 'username', 'label', 'note', 'last_synced_at', 'updated_at'])
    return item


@csrf_exempt
@dashboard_superuser_required
@require_POST
def check_telegram_login_account_status(request, account_id: int):
    item = TelegramLoginAccount.objects.filter(id=account_id).first()
    if not item:
        return _error('账号不存在', status=404)
    if not item.session_string_plain:
        item.status = 'session_expired'
        item.note = '缺少 Telegram 会话，请重新登录'
        item.save(update_fields=['status', 'note', 'updated_at'])
        return _ok(_telegram_login_account_payload(item))
    try:
        api_id, api_hash = _telegram_api_credentials()
        result = async_to_sync(_telegram_check_session)(item.session_string_plain, api_id, api_hash)
    except Exception as exc:
        item.status = 'listener_error'
        item.note = f'状态检查失败：{exc}'[:1000]
        item.save(update_fields=['status', 'note', 'updated_at'])
        return _ok(_telegram_login_account_payload(item))
    if result.get('ok'):
        _update_login_account_from_me(item, result.get('user'), status='logged_in')
    else:
        item.status = 'session_expired'
        item.note = str(result.get('note') or 'Telegram 会话已失效，请重新登录')[:1000]
        item.save(update_fields=['status', 'note', 'updated_at'])
    return _ok(_telegram_login_account_payload(item))


@csrf_exempt
@dashboard_superuser_required
@require_POST
def update_telegram_account_notify(request, account_id: int):
    payload = _read_payload(request)
    item = TelegramLoginAccount.objects.filter(id=account_id).first()
    if not item:
        return _error('账号不存在', status=404)
    if 'notify_enabled' in payload:
        item.notify_enabled = _payload_bool(payload, 'notify_enabled')
    if 'listener_push_enabled' in payload:
        item.listener_push_enabled = _payload_bool(payload, 'listener_push_enabled')
    item.save(update_fields=['notify_enabled', 'listener_push_enabled', 'updated_at'])
    return _ok(_telegram_login_account_payload(item))


@csrf_exempt
@dashboard_superuser_required
@require_POST
def telegram_login_start(request):
    payload = _read_payload(request)
    raw_phone = payload.get('phone')
    try:
        phone = _normalize_telegram_phone(raw_phone)
    except ValueError as exc:
        return _error(str(exc), status=400)
    if not phone:
        return _error('手机号不能为空', status=400)
    try:
        api_id, api_hash = _telegram_api_credentials()
        phone_code_hash, session_string = async_to_sync(_telegram_send_code)(phone, api_id, api_hash)
    except Exception as exc:
        return _error(f'发送验证码失败：{exc}', status=400)
    item, _ = TelegramLoginAccount.objects.update_or_create(
        phone=phone,
        defaults={
            'label': phone,
            'phone_code_hash': phone_code_hash,
            'session_string': session_string,
            'status': 'code_sent',
            'note': '验证码已发送',
        },
    )
    return _ok({'account': _telegram_login_account_payload(item), 'account_id': item.id, 'next_step': 'code'})


@csrf_exempt
@dashboard_superuser_required
@require_POST
def telegram_login_code(request):
    payload = _read_payload(request)
    account_id = payload.get('account_id')
    code = str(payload.get('code') or '').strip().replace(' ', '')
    item = TelegramLoginAccount.objects.filter(id=account_id).first()
    if not item:
        return _error('登录账号不存在', status=404)
    if not code:
        return _error('验证码不能为空', status=400)
    try:
        api_id, api_hash = _telegram_api_credentials()
        result = async_to_sync(_telegram_sign_in_code)(item.session_string_plain, item.phone or '', code, item.phone_code_hash_plain, api_id, api_hash)
    except Exception as exc:
        item.status = 'error'
        item.note = f'验证码登录失败：{exc}'
        item.save(update_fields=['status', 'note', 'updated_at'])
        return _error(f'验证码登录失败：{exc}', status=400)
    item.session_string = result['session_string']
    if result['requires_password']:
        item.status = 'password_required'
        item.note = '需要二级密码'
        item.save(update_fields=['session_string', 'status', 'note', 'updated_at'])
        return _ok({'account': _telegram_login_account_payload(item), 'account_id': item.id, 'requires_password': True, 'next_step': 'password'})
    item = _update_login_account_from_me(item, result['user'])
    item.save(update_fields=['session_string', 'updated_at'])
    return _ok({'account': _telegram_login_account_payload(item), 'account_id': item.id, 'requires_password': False, 'next_step': 'done'})


@csrf_exempt
@dashboard_superuser_required
@require_POST
def telegram_login_password(request):
    payload = _read_payload(request)
    account_id = payload.get('account_id')
    password = str(payload.get('password') or '')
    item = TelegramLoginAccount.objects.filter(id=account_id).first()
    if not item:
        return _error('登录账号不存在', status=404)
    if item.status != 'password_required' and not password:
        return _ok({'account': _telegram_login_account_payload(item), 'account_id': item.id, 'next_step': 'done'})
    if not password:
        return _error('该账号需要二级密码；如果没有二级密码，请返回检查验证码登录结果', status=400)
    try:
        api_id, api_hash = _telegram_api_credentials()
        result = async_to_sync(_telegram_sign_in_password)(item.session_string_plain, password, api_id, api_hash)
    except Exception as exc:
        item.status = 'error'
        item.note = f'二级密码登录失败：{exc}'
        item.save(update_fields=['status', 'note', 'updated_at'])
        return _error(f'二级密码登录失败：{exc}', status=400)
    item.session_string = result['session_string']
    item = _update_login_account_from_me(item, result['user'])
    item.save(update_fields=['session_string', 'updated_at'])
    return _ok({'account': _telegram_login_account_payload(item), 'account_id': item.id, 'next_step': 'done'})


@dashboard_login_required
@require_GET
def telegram_group_filters_list(request):
    keyword = _get_keyword(request).lstrip('@')
    binding_only = str(request.GET.get('binding_only') or '').lower() in {'1', 'true', 'yes', 'on'}
    queryset = TelegramGroupFilter.objects.order_by('-updated_at', '-id')
    if binding_only:
        queryset = queryset.filter(collapsed=False, archived=False)
    if keyword:
        query = Q(title__icontains=keyword) | Q(username__icontains=keyword)
        try:
            query |= Q(chat_id=int(keyword))
        except ValueError:
            pass
        queryset = queryset.filter(query)
    return _ok([_telegram_group_filter_payload(item) for item in queryset[:300]])


@csrf_exempt
@dashboard_superuser_required
@require_POST
def create_telegram_group_filter(request):
    payload = _read_payload(request)
    try:
        chat_id, title, username = _validate_telegram_group_filter_payload(payload)
    except ValueError as exc:
        return _error(str(exc), status=400)
    item = TelegramGroupFilter.objects.create(
        chat_id=chat_id,
        title=title,
        username=username,
        enabled=_payload_bool(payload, 'enabled'),
        push_enabled=_payload_bool(payload, 'push_enabled'),
        collapsed=_payload_bool(payload, 'collapsed'),
        archived=_payload_bool(payload, 'archived'),
    )
    return _ok(_telegram_group_filter_payload(item))


@dashboard_login_required
@require_GET
def telegram_group_filter_detail(request, group_id: int):
    item = TelegramGroupFilter.objects.filter(id=group_id).first()
    if not item:
        return _error('群组不存在', status=404)
    messages = list(
        TelegramChatMessage.objects.filter(chat_id=item.chat_id)
        .select_related('user', 'login_account')
        .order_by('-created_at', '-id')[:100]
    )
    latest_by_user = {}
    for message_item in messages:
        latest_by_user.setdefault(message_item.tg_user_id, message_item)
    member_rows = list(
        TelegramChatMessage.objects.filter(chat_id=item.chat_id)
        .values('tg_user_id')
        .annotate(message_count=Count('id'), last_seen_at=Max('created_at'))
        .order_by('-last_seen_at')[:100]
    )
    missing_user_ids = [row['tg_user_id'] for row in member_rows if row['tg_user_id'] not in latest_by_user]
    if missing_user_ids:
        for message_item in (
            TelegramChatMessage.objects.filter(chat_id=item.chat_id, tg_user_id__in=missing_user_ids)
            .select_related('user')
            .order_by('-created_at', '-id')
        ):
            latest_by_user.setdefault(message_item.tg_user_id, message_item)
            if len(latest_by_user) >= len(member_rows):
                break
    return _ok({
        'group': _telegram_group_filter_payload(item),
        'members': [_telegram_group_member_payload(row, latest_by_user[row['tg_user_id']]) for row in member_rows if row['tg_user_id'] in latest_by_user],
        'messages': [_telegram_message_payload(message_item) for message_item in messages],
    })


@csrf_exempt
@dashboard_superuser_required
@require_POST
def update_telegram_group_filter(request, group_id: int):
    item = TelegramGroupFilter.objects.filter(id=group_id).first()
    if not item:
        return _error('群组不存在', status=404)
    payload = _read_payload(request)
    changed = []
    if any(key in payload for key in ('chat_id', 'title', 'username')):
        try:
            chat_id, title, username = _validate_telegram_group_filter_payload(payload, current_id=group_id)
        except ValueError as exc:
            return _error(str(exc), status=400)
        for field, value in {'chat_id': chat_id, 'title': title, 'username': username}.items():
            if getattr(item, field) != value:
                setattr(item, field, value)
                changed.append(field)
    if 'enabled' in payload:
        enabled = _payload_bool(payload, 'enabled')
        if item.enabled != enabled:
            item.enabled = enabled
            changed.append('enabled')
    if 'push_enabled' in payload:
        push_enabled = _payload_bool(payload, 'push_enabled')
        if getattr(item, 'push_enabled', False) != push_enabled:
            item.push_enabled = push_enabled
            changed.append('push_enabled')
    if 'collapsed' in payload:
        collapsed = _payload_bool(payload, 'collapsed')
        if item.collapsed != collapsed:
            item.collapsed = collapsed
            changed.append('collapsed')
    if 'archived' in payload:
        archived = _payload_bool(payload, 'archived')
        if getattr(item, 'archived', False) != archived:
            item.archived = archived
            changed.append('archived')
    if changed:
        changed.append('updated_at')
        item.save(update_fields=changed)
    return _ok(_telegram_group_filter_payload(item))


@csrf_exempt
@dashboard_superuser_required
@require_POST
def send_telegram_chat_message(request):
    payload = _read_payload(request)
    text = str(payload.get('text') or '').strip()
    raw_chat_id = payload.get('chat_id')
    raw_account_id = payload.get('login_account_id')
    try:
        chat_id = int(raw_chat_id)
    except (TypeError, ValueError):
        return _error('会话ID无效', status=400)
    if not text:
        return _error('消息内容不能为空', status=400)
    account = None
    if raw_account_id:
        account = TelegramLoginAccount.objects.filter(id=raw_account_id, status='logged_in').first()
    if not account:
        latest = TelegramChatMessage.objects.filter(chat_id=chat_id, login_account__status='logged_in').select_related('login_account').order_by('-created_at', '-id').first()
        account = latest.login_account if latest else None
    if not account:
        account = TelegramLoginAccount.objects.filter(status='logged_in').exclude(session_string__isnull=True).exclude(session_string='').order_by('-updated_at', '-id').first()
    if not account or not account.session_string_plain:
        return _error('没有可用的已登录 Telegram 账号', status=400)
    try:
        api_id, api_hash = _telegram_api_credentials()
        sent = async_to_sync(_telegram_send_message)(account.session_string_plain, chat_id, text, api_id, api_hash)
    except Exception as exc:
        return _error(f'发送失败：{exc}', status=400)
    item = TelegramChatMessage.objects.create(
        login_account=account,
        tg_user_id=chat_id,
        chat_id=chat_id,
        message_id=getattr(sent, 'id', None),
        direction=TelegramChatMessage.DIRECTION_OUT,
        content_type='text',
        text=text[:4000],
        chat_title=str(chat_id),
        source='account',
    )
    account.last_synced_at = timezone.now()
    account.save(update_fields=['last_synced_at', 'updated_at'])
    return _ok(_telegram_message_payload(item))


@csrf_exempt
@dashboard_superuser_required
@require_POST
def archive_telegram_chat(request):
    payload = _read_payload(request)
    raw_chat_id = payload.get('chat_id')
    archived = _payload_bool(payload, 'archived', True)
    try:
        chat_id = int(raw_chat_id)
    except (TypeError, ValueError):
        return _error('会话ID无效', status=400)
    latest = TelegramChatMessage.objects.filter(chat_id=chat_id).order_by('-created_at', '-id').first()
    title = payload.get('title') or (latest.chat_title if latest else '') or str(chat_id)
    if archived:
        TelegramChatArchive.objects.update_or_create(chat_id=chat_id, defaults={'title': title})
    else:
        TelegramChatArchive.objects.filter(chat_id=chat_id).delete()
    return _ok({'chat_id': chat_id, 'archived': archived})


@csrf_exempt
@dashboard_superuser_required
@require_POST
def create_telegram_login_account(request):
    payload = _read_payload(request)
    label = str(payload.get('label') or '').strip()
    phone = str(payload.get('phone') or '').strip()
    username = _limited_username_string(payload.get('username'))
    note = str(payload.get('note') or '').strip()
    tg_user_id = None
    raw_tg_user_id = str(payload.get('tg_user_id') or '').strip()
    if raw_tg_user_id:
        try:
            tg_user_id = int(raw_tg_user_id)
        except (TypeError, ValueError):
            return _error('Telegram 用户ID必须是数字', status=400)
    if not label:
        return _error('账号备注不能为空', status=400)
    item = TelegramLoginAccount.objects.create(
        label=label,
        phone=phone or None,
        tg_user_id=tg_user_id,
        username=username,
        note=note or '已登记。自动采集仅限 bot 会话内收到的用户资料和聊天记录；不会后台登录个人 Telegram 账号抓取私聊。',
        status='registered',
    )
    if tg_user_id:
        _get_or_create_user_sync(tg_user_id, username, label)
    return _ok(_telegram_login_account_payload(item))


@dashboard_login_required
@require_GET
def telegram_chat_messages(request):
    keyword = (request.GET.get('keyword') or '').strip().lstrip('@')
    user_id = request.GET.get('user_id')
    tg_user_id = request.GET.get('tg_user_id')
    chat_id = request.GET.get('chat_id')
    if not any([user_id, tg_user_id, chat_id]):
        return _ok([])
    qs = TelegramChatMessage.objects.select_related('user', 'login_account').order_by('-created_at', '-id')
    if user_id:
        qs = qs.filter(user_id=user_id)
    if tg_user_id:
        qs = qs.filter(tg_user_id=tg_user_id)
    if chat_id:
        qs = qs.filter(chat_id=chat_id)
    if keyword:
        message_filter = Q(text__icontains=keyword) | Q(username_snapshot__icontains=keyword) | Q(first_name_snapshot__icontains=keyword)
        if keyword.isdigit():
            message_filter |= Q(tg_user_id=int(keyword))
        qs = qs.filter(message_filter)
    return _ok([_telegram_message_payload(item) for item in qs[:300]])
