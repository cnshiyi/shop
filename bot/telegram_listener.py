import asyncio
import contextlib
import html
import json
import logging
from datetime import timezone as datetime_timezone
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from accounts.models import TelegramUser, TelegramUsername
from core.models import SiteConfig

logger = logging.getLogger(__name__)

OVERVIEW_CONFIG_KEY = 'dashboard_telegram_accounts_overview'
SESSION_CONFIG_PREFIX = 'telegram_login_session_'
MAX_STORED_MESSAGES = 2000
SYNC_INTERVAL_SECONDS = 30


def _default_overview():
    return {
        'accounts': [],
        'chats': [],
        'messages': [],
        'users': [],
    }


def _iso(value):
    if not value:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, datetime_timezone.utc)
    return timezone.localtime(value).isoformat()


def _split_usernames(username):
    if not username:
        return []
    if isinstance(username, (list, tuple)):
        values = []
        for item in username:
            values.extend(_split_usernames(item))
        return values
    normalized = str(username).replace('，', ',').replace(' / ', ',').replace('/', ',')
    result = []
    seen = set()
    for item in normalized.split(','):
        value = item.strip().lstrip('@')
        key = value.lower()
        if value and key not in seen:
            result.append(value)
            seen.add(key)
    return result


def _merge_usernames(existing, incoming):
    result = []
    seen = set()
    for item in [*incoming, *_split_usernames(existing)]:
        key = str(item).lower()
        if item and key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _read_json_config(key, default):
    raw = SiteConfig.get(key, '')
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default
    return value if isinstance(value, type(default)) else default


def _set_json_config(key, value):
    SiteConfig.set(key, json.dumps(value, ensure_ascii=False), sensitive=False)


def _telegram_session_key(account_id):
    return f'{SESSION_CONFIG_PREFIX}{account_id}'


def _get_telegram_session(account_id):
    return SiteConfig.get(_telegram_session_key(account_id), '')


def _set_telegram_session(account_id, session_string):
    SiteConfig.set(_telegram_session_key(account_id), session_string or '', sensitive=True)


def _truthy_config(key):
    return str(SiteConfig.get(key, '') or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _telegram_api_config():
    api_id_raw = SiteConfig.get('telegram_api_id', '').strip()
    api_hash = SiteConfig.get('telegram_api_hash', '').strip()
    if not api_id_raw or not api_hash:
        raise ValueError('Telegram 登录应用 ID / 密钥未配置')
    return int(api_id_raw), api_hash


def _normalize_overview(overview):
    if not isinstance(overview, dict):
        overview = _default_overview()
    for key, default in _default_overview().items():
        if not isinstance(overview.get(key), list):
            overview[key] = list(default)
    return overview


def _save_telegram_identity(sender):
    tg_user_id = int(sender.get('tg_user_id') or 0)
    if not tg_user_id:
        return None

    incoming = _split_usernames(sender.get('usernames') or sender.get('username') or '')
    first_name = sender.get('first_name') or ''
    user, _ = TelegramUser.objects.get_or_create(
        tg_user_id=tg_user_id,
        defaults={'username': ','.join(incoming), 'first_name': first_name},
    )

    merged = _merge_usernames(user.username, incoming)
    changed_fields = []
    username_text = ','.join(merged)
    if incoming and user.username != username_text:
        user.username = username_text
        changed_fields.append('username')
    if first_name and user.first_name != first_name:
        user.first_name = first_name
        changed_fields.append('first_name')
    if changed_fields:
        changed_fields.append('updated_at')
        user.save(update_fields=changed_fields)

    if not incoming:
        return user

    existing = {item.username.lower(): item for item in user.telegramusernames.all() if item.username}
    primary_key = incoming[0].lower()
    for raw in merged:
        key = raw.lower()
        item = existing.pop(key, None)
        should_be_primary = key == primary_key
        if item:
            update_fields = []
            if item.username != raw:
                item.username = raw
                update_fields.append('username')
            if item.is_primary != should_be_primary:
                item.is_primary = should_be_primary
                update_fields.append('is_primary')
            if update_fields:
                update_fields.append('updated_at')
                item.save(update_fields=update_fields)
        else:
            TelegramUsername.objects.create(user=user, username=raw, is_primary=should_be_primary)

    for item in existing.values():
        if item.is_primary:
            item.is_primary = False
            item.save(update_fields=['is_primary', 'updated_at'])
    return user


def _find_group(groups, chat_id):
    for item in groups:
        if int(item.get('chat_id') or 0) == int(chat_id or 0):
            return item
    return None


def _notification_targets(groups, source_chat_id):
    targets = []
    seen = set()
    for item in groups:
        if not item.get('enabled'):
            continue
        chat_id = int(item.get('chat_id') or 0)
        if not chat_id or chat_id == int(source_chat_id or 0) or chat_id in seen:
            continue
        targets.append({
            'chat_id': chat_id,
            'title': item.get('title') or str(chat_id),
        })
        seen.add(chat_id)
    return targets


def _format_push_message(record):
    sender = record.get('first_name_snapshot') or ''
    username = record.get('username_snapshot') or ''
    if username:
        sender = f'{sender} (@{username})' if sender else f'@{username}'
    sender = sender or f"ID {record.get('tg_user_id') or '-'}"
    text = record.get('text') or f"[{record.get('content_type') or '非文本消息'}]"
    if len(text) > 1200:
        text = f'{text[:1200]}...'
    return (
        '<b>Telegram 新消息</b>\n'
        f"账号：{html.escape(str(record.get('login_account_label') or '-'))}\n"
        f"会话：{html.escape(str(record.get('chat_title') or record.get('chat_id') or '-'))}\n"
        f"发信人：{html.escape(str(sender))}\n"
        f"内容：{html.escape(str(text))}"
    )


def load_listener_accounts():
    overview = _normalize_overview(_read_json_config(OVERVIEW_CONFIG_KEY, _default_overview()))
    accounts = []
    for item in overview.get('accounts', []):
        account_id = int(item.get('id') or 0)
        if not account_id or not item.get('listener_push_enabled'):
            continue
        session_string = _get_telegram_session(account_id)
        if not session_string:
            continue
        accounts.append({
            **item,
            'id': account_id,
            'notify_enabled': bool(item.get('notify_enabled', True)),
            'session_string': session_string,
        })
    return accounts


def mark_listener_error(account_id, note):
    with transaction.atomic():
        overview = _normalize_overview(_read_json_config(OVERVIEW_CONFIG_KEY, _default_overview()))
        for item in overview.get('accounts', []):
            if int(item.get('id') or 0) == int(account_id or 0):
                item['status'] = 'listener_error'
                item['note'] = str(note or '')[:500]
                item['updated_at'] = _iso(timezone.now())
                break
        _set_json_config(OVERVIEW_CONFIG_KEY, overview)


def record_incoming_message(account, payload):
    groups = _read_json_config('dashboard_telegram_groups', [])
    if not isinstance(groups, list):
        groups = []

    sender = payload.get('sender') or {}
    _save_telegram_identity(sender)

    with transaction.atomic():
        overview = _normalize_overview(_read_json_config(OVERVIEW_CONFIG_KEY, _default_overview()))
        messages = overview.setdefault('messages', [])
        message_id = payload.get('message_id')
        chat_id = int(payload.get('chat_id') or 0)
        account_id = int(account.get('id') or 0)
        for existing in messages:
            if (
                int(existing.get('login_account_id') or 0) == account_id
                and int(existing.get('chat_id') or 0) == chat_id
                and existing.get('message_id') == message_id
                and existing.get('direction') == 'in'
            ):
                return {'record': existing, 'targets': []}

        next_id = max([int(item.get('id') or 0) for item in messages] or [0]) + 1
        usernames = _split_usernames(sender.get('usernames') or sender.get('username') or '')
        first_name = sender.get('first_name') or ''
        record = {
            'chat_id': chat_id,
            'chat_title': payload.get('chat_title') or str(chat_id),
            'content_type': payload.get('content_type') or 'text',
            'created_at': payload.get('created_at') or _iso(timezone.now()),
            'direction': 'in',
            'direction_label': '收到',
            'first_name_snapshot': first_name,
            'id': next_id,
            'login_account_id': account_id,
            'login_account_label': account.get('label') or account.get('phone') or f'Telegram 账号 {account_id}',
            'message_id': message_id,
            'source': 'telegram_login',
            'source_label': 'Telegram 登录账号',
            'text': payload.get('text') or '',
            'tg_user_id': int(sender.get('tg_user_id') or 0),
            'username_snapshot': usernames[0] if usernames else '',
        }
        messages.insert(0, record)
        del messages[MAX_STORED_MESSAGES:]

        chats = overview.setdefault('chats', [])
        chat = None
        for existing in chats:
            if (
                int(existing.get('chat_id') or 0) == chat_id
                and int(existing.get('login_account_id') or 0) == account_id
            ):
                chat = existing
                break
        if not chat:
            chat = {
                'archived': False,
                'chat_id': chat_id,
                'is_group': bool(payload.get('is_group')),
                'login_account_id': account_id,
                'login_account_label': record['login_account_label'],
                'message_count': 0,
                'source': 'telegram_login',
                'source_label': 'Telegram 登录账号',
                'subtitle': account.get('label') or '',
                'title': payload.get('chat_title') or str(chat_id),
            }
            chats.insert(0, chat)
        chat.update({
            'is_group': bool(payload.get('is_group')),
            'latest_at': record['created_at'],
            'latest_message': record['text'] or f"[{record['content_type']}]",
            'message_count': int(chat.get('message_count') or 0) + 1,
            'title': payload.get('chat_title') or chat.get('title') or str(chat_id),
        })

        now = _iso(timezone.now())
        for item in overview.get('accounts', []):
            if int(item.get('id') or 0) == account_id:
                item['has_session'] = True
                item['last_synced_at'] = now
                item['note'] = ''
                item['status'] = 'logged_in'
                item['updated_at'] = now
                break

        _set_json_config(OVERVIEW_CONFIG_KEY, overview)

    source_group = _find_group(groups, chat_id)
    group_push_enabled = _truthy_config('telegram_listener_push_enabled')
    private_push_enabled = _truthy_config('telegram_listener_push_private_enabled')
    should_push = bool(account.get('notify_enabled', True))
    if payload.get('is_group'):
        should_push = should_push and group_push_enabled
        if source_group is not None:
            should_push = should_push and bool(source_group.get('push_enabled'))
    else:
        should_push = should_push and private_push_enabled
    targets = _notification_targets(groups, chat_id) if should_push else []
    return {'record': record, 'targets': targets}


def _entity_usernames(entity):
    usernames = []
    username = getattr(entity, 'username', None)
    if username:
        usernames.append(str(username).lstrip('@'))
    for item in getattr(entity, 'usernames', None) or []:
        value = getattr(item, 'username', None) or str(item)
        if value:
            usernames.append(str(value).lstrip('@'))
    return _split_usernames(usernames)


def _entity_name(entity):
    first_name = getattr(entity, 'first_name', None) or ''
    last_name = getattr(entity, 'last_name', None) or ''
    full_name = ' '.join(part for part in [first_name, last_name] if part).strip()
    return full_name or getattr(entity, 'title', None) or ''


def _message_content_type(message):
    if getattr(message, 'raw_text', None):
        return 'text'
    if getattr(message, 'photo', None):
        return 'photo'
    if getattr(message, 'video', None):
        return 'video'
    if getattr(message, 'document', None):
        return 'document'
    if getattr(message, 'voice', None):
        return 'voice'
    if getattr(message, 'sticker', None):
        return 'sticker'
    return 'message'


@dataclass
class _RunningClient:
    account: dict[str, Any]
    client: Any
    task: asyncio.Task
    signature: tuple[Any, ...]


class TelegramAccountListenerManager:
    def __init__(self, bot=None, *, interval_seconds=SYNC_INTERVAL_SECONDS):
        self.bot = bot
        self.interval_seconds = interval_seconds
        self.bot_user_id = None
        self._clients: dict[int, _RunningClient] = {}
        self._sync_task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self):
        if self._sync_task and not self._sync_task.done():
            return
        if self.bot:
            with contextlib.suppress(Exception):
                me = await self.bot.get_me()
                self.bot_user_id = int(me.id)
        self._sync_task = asyncio.create_task(self._sync_loop(), name='telegram-account-listener-sync')

    async def stop(self):
        self._stopping.set()
        if self._sync_task:
            self._sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sync_task
        await asyncio.gather(
            *(self._stop_client(account_id) for account_id in list(self._clients)),
            return_exceptions=True,
        )

    async def _sync_loop(self):
        while not self._stopping.is_set():
            try:
                await self._sync_accounts()
            except Exception as exc:
                logger.exception('Telegram 登录账号监听同步失败: %s', exc)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _sync_accounts(self):
        accounts = await asyncio.to_thread(load_listener_accounts)
        wanted = {int(item.get('id') or 0): item for item in accounts}
        for account_id in list(self._clients):
            if account_id not in wanted:
                await self._stop_client(account_id)
        for account_id, account in wanted.items():
            signature = (
                account.get('session_string'),
                account.get('label'),
                account.get('notify_enabled'),
                account.get('listener_push_enabled'),
            )
            running = self._clients.get(account_id)
            if running and running.signature == signature and not running.task.done():
                running.account = account
                continue
            if running:
                await self._stop_client(account_id)
            await self._start_client(account, signature)

    async def _start_client(self, account, signature):
        from telethon import TelegramClient, events
        from telethon.sessions import StringSession

        account_id = int(account.get('id') or 0)
        api_id, api_hash = await asyncio.to_thread(_telegram_api_config)
        client = TelegramClient(StringSession(account.get('session_string') or ''), api_id, api_hash)

        async def _handle_message(event):
            await self._handle_message(account_id, event)

        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise ValueError('Telegram 会话已失效，请重新登录账号')
            client.add_event_handler(_handle_message, events.NewMessage(incoming=True))
            task = asyncio.create_task(client.run_until_disconnected(), name=f'telegram-listener-{account_id}')
            self._clients[account_id] = _RunningClient(
                account=account,
                client=client,
                task=task,
                signature=signature,
            )
            await asyncio.to_thread(_set_telegram_session, account_id, client.session.save())
            logger.info('Telegram 登录账号监听已启动 account=%s label=%s', account_id, account.get('label') or '')
        except Exception as exc:
            with contextlib.suppress(Exception):
                await client.disconnect()
            await asyncio.to_thread(mark_listener_error, account_id, exc)
            logger.warning('Telegram 登录账号监听启动失败 account=%s err=%s', account_id, exc)

    async def _stop_client(self, account_id):
        running = self._clients.pop(int(account_id), None)
        if not running:
            return
        running.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running.task
        with contextlib.suppress(Exception):
            await running.client.disconnect()
        logger.info('Telegram 登录账号监听已停止 account=%s', account_id)

    async def _handle_message(self, account_id, event):
        running = self._clients.get(int(account_id))
        if not running:
            return
        message = event.message
        sender = await event.get_sender()
        sender_id = int(getattr(sender, 'id', 0) or 0)
        if self.bot_user_id and sender_id == self.bot_user_id:
            return
        chat = await event.get_chat()
        chat_id = int(event.chat_id or 0)
        payload = {
            'chat_id': chat_id,
            'chat_title': _entity_name(chat) or str(chat_id),
            'content_type': _message_content_type(message),
            'created_at': _iso(getattr(message, 'date', None) or timezone.now()),
            'is_group': bool(event.is_group or event.is_channel),
            'message_id': getattr(message, 'id', None),
            'sender': {
                'first_name': _entity_name(sender),
                'tg_user_id': sender_id,
                'username': (_entity_usernames(sender) or [''])[0],
                'usernames': _entity_usernames(sender),
            },
            'text': getattr(message, 'raw_text', '') or '',
        }
        result = await asyncio.to_thread(record_incoming_message, running.account, payload)
        targets = result.get('targets') or []
        if not self.bot or not targets:
            return
        text = _format_push_message(result.get('record') or {})
        for target in targets:
            try:
                await self.bot.send_message(chat_id=target['chat_id'], text=text, parse_mode='HTML')
            except Exception as exc:
                logger.warning(
                    'Telegram 监听推送发送失败 account=%s target=%s err=%s',
                    account_id,
                    target.get('chat_id'),
                    exc,
                )
