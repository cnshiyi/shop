"""Telegram personal-account listener powered by Telethon."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from asgiref.sync import sync_to_async
from django.utils import timezone

from bot.models import TelegramChatMessage, TelegramLoginAccount
from bot.services import record_telegram_message, telegram_group_delivery_flags
from core.models import SiteConfig
from core.runtime_config import get_runtime_config

logger = logging.getLogger(__name__)
_PUSH_CONFIG_TTL_SECONDS = 30.0
_PUSH_CONFIG_CACHE = {
    'loaded_at': 0.0,
    'value': {
        'enabled': False,
        'bark_url': '',
        'private_enabled': True,
        'encryption_key': '',
        'encryption_iv': '',
        'encryption_algorithm': 'AES256',
        'encryption_mode': 'CBC',
        'encryption_padding': 'pkcs7',
        'chat_ids': set(),
    },
}


@dataclass(frozen=True)
class LoginAccountSnapshot:
    id: int
    label: str
    session_string: str
    listener_push_enabled: bool


@sync_to_async
def _telegram_api_credentials() -> tuple[int, str]:
    api_id = SiteConfig.get('telegram_api_id', '') or get_runtime_config('telegram_api_id', '')
    api_hash = SiteConfig.get('telegram_api_hash', '') or get_runtime_config('telegram_api_hash', '')
    if not str(api_id or '').strip() or not str(api_hash or '').strip():
        raise ValueError('未配置 Telegram API ID / API Hash')
    return int(str(api_id).strip()), str(api_hash).strip()


@sync_to_async
def _logged_in_accounts() -> list[LoginAccountSnapshot]:
    return [
        LoginAccountSnapshot(item.id, item.label, item.session_string_plain, bool(getattr(item, 'listener_push_enabled', True)))
        for item in TelegramLoginAccount.objects.filter(status='logged_in').exclude(session_string__isnull=True).exclude(session_string='')
        if item.session_string_plain
    ]


@sync_to_async
def _mark_account(account_id: int, status: str, note: str = ''):
    fields = {'status': status, 'updated_at': timezone.now()}
    if note:
        fields['note'] = note[:1000]
    if status == 'logged_in':
        fields['last_synced_at'] = timezone.now()
    TelegramLoginAccount.objects.filter(id=account_id).update(**fields)


def _entity_username(entity) -> str | None:
    return getattr(entity, 'username', None) or None


def _entity_name(entity) -> str | None:
    first_name = getattr(entity, 'first_name', None)
    last_name = getattr(entity, 'last_name', None)
    title = getattr(entity, 'title', None)
    full_name = ' '.join(part for part in [first_name, last_name] if part).strip()
    return full_name or title or None


def _content_type(message) -> str:
    if getattr(message, 'text', None):
        return 'text'
    if getattr(message, 'photo', None):
        return 'photo'
    if getattr(message, 'video', None):
        return 'video'
    if getattr(message, 'voice', None):
        return 'voice'
    if getattr(message, 'document', None):
        return 'document'
    if getattr(message, 'sticker', None):
        return 'sticker'
    if getattr(message, 'media', None):
        return 'media'
    return 'unknown'


def _config_bool(value) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _build_push_payload(*, is_outgoing: bool, is_private_chat: bool, sender_name: str | None, chat_title: str | None, text: str, content_type: str, private_enabled: bool, group_push_enabled: bool = False) -> tuple[str, str] | None:
    if is_outgoing:
        return None
    if is_private_chat:
        if not private_enabled:
            return None
        return '📨 私聊消息', '收到一条新的私聊消息'
    if group_push_enabled:
        return '📢 群/频道消息', '收到一条新的群组或频道消息'
    return None


@sync_to_async
def _telegram_push_config() -> dict:
    return {
        'enabled': _config_bool(get_runtime_config('telegram_listener_push_enabled', '0')),
        'bark_url': str(get_runtime_config('telegram_listener_push_bark_url', '') or '').strip(),
        'private_enabled': _config_bool(get_runtime_config('telegram_listener_push_private_enabled', '1')),
        'encryption_key': str(get_runtime_config('telegram_listener_push_bark_encryption_key', '') or '').strip(),
        'encryption_iv': str(get_runtime_config('telegram_listener_push_bark_encryption_iv', '') or '').strip(),
        'encryption_algorithm': str(get_runtime_config('telegram_listener_push_bark_encryption_algorithm', 'AES256') or 'AES256').strip().upper(),
        'encryption_mode': str(get_runtime_config('telegram_listener_push_bark_encryption_mode', 'CBC') or 'CBC').strip().upper(),
        'encryption_padding': str(get_runtime_config('telegram_listener_push_bark_encryption_padding', 'pkcs7') or 'pkcs7').strip(),
    }


async def _cached_telegram_push_config() -> dict:
    now = time.monotonic()
    if now - float(_PUSH_CONFIG_CACHE['loaded_at']) <= _PUSH_CONFIG_TTL_SECONDS:
        return _PUSH_CONFIG_CACHE['value']
    value = await _telegram_push_config()
    _PUSH_CONFIG_CACHE['loaded_at'] = now
    _PUSH_CONFIG_CACHE['value'] = value
    return value


def _split_bark_url(bark_url: str) -> tuple[str, dict[str, str], str | None]:
    parsed = urlsplit(bark_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    path_parts = [part for part in parsed.path.split('/') if part]
    base_path = f'/{path_parts[0]}' if path_parts else parsed.path
    base_url = urlunsplit((parsed.scheme, parsed.netloc, base_path, '', ''))
    path_title = path_parts[1] if len(path_parts) > 1 else None
    return base_url, query, path_title


def _bark_binary_value(value: str, expected_length: int, field_name: str) -> bytes:
    raw = str(value or '').strip()
    if len(raw) == expected_length * 2 and re.fullmatch(r'[0-9a-fA-F]+', raw):
        data = bytes.fromhex(raw)
    else:
        data = raw.encode('utf-8')
    if len(data) != expected_length:
        raise ValueError(f'Bark 加密 {field_name} 长度必须是 {expected_length} 字节')
    return data


def _bark_key_bytes(value: str, expected_length: int) -> bytes:
    return _bark_binary_value(value, expected_length, 'key')


def _bark_iv_bytes(value: str, expected_length: int) -> bytes:
    return _bark_binary_value(value, expected_length, 'iv')


def _bark_encrypt_payload(payload: dict[str, str], config: dict) -> tuple[str, str]:
    algorithm = str(config.get('encryption_algorithm') or 'AES256').upper()
    mode_name = str(config.get('encryption_mode') or 'CBC').upper()
    padding_name = str(config.get('encryption_padding') or 'pkcs7')
    key_lengths = {'AES128': 16, 'AES192': 24, 'AES256': 32}
    if algorithm not in key_lengths:
        raise ValueError('Bark 加密算法只支持 AES128/AES192/AES256')
    key = _bark_key_bytes(str(config.get('encryption_key') or ''), key_lengths[algorithm])
    iv = str(config.get('encryption_iv') or '')
    data = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if mode_name == 'GCM':
        iv_bytes = _bark_iv_bytes(iv, 12)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(iv_bytes)).encryptor()
        ciphertext = encryptor.update(data) + encryptor.finalize() + encryptor.tag
    elif mode_name == 'CBC':
        iv_bytes = _bark_iv_bytes(iv, 16)
        if padding_name.lower() == 'pkcs7':
            padder = PKCS7(128).padder()
            data = padder.update(data) + padder.finalize()
        elif padding_name != 'noPadding':
            raise ValueError('Bark 加密 padding 只支持 pkcs7/noPadding')
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv_bytes)).encryptor()
        ciphertext = encryptor.update(data) + encryptor.finalize()
    elif mode_name == 'ECB':
        if padding_name.lower() == 'pkcs7':
            padder = PKCS7(128).padder()
            data = padder.update(data) + padder.finalize()
        elif padding_name != 'noPadding':
            raise ValueError('Bark 加密 padding 只支持 pkcs7/noPadding')
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        ciphertext = encryptor.update(data) + encryptor.finalize()
    else:
        raise ValueError('Bark 加密模式只支持 CBC/ECB/GCM')
    return base64.b64encode(ciphertext).decode('ascii'), iv


def _build_bark_request(bark_url: str, *, title: str, body: str, config: dict) -> tuple[str, dict[str, str]]:
    base_url, params, path_title = _split_bark_url(bark_url)
    payload = {**params, 'title': title or path_title or '', 'body': body or ''}
    if str(config.get('encryption_key') or '').strip():
        ciphertext, iv = _bark_encrypt_payload(payload, config)
        encrypted_params = {'ciphertext': ciphertext}
        if iv:
            encrypted_params['iv'] = iv
        return base_url, encrypted_params
    return bark_url, {'title': title, 'body': body}


async def _send_listener_push(*, title: str, body: str) -> bool:
    config = await _cached_telegram_push_config()
    bark_url = str(config.get('bark_url') or '').strip()
    if not config.get('enabled') or not bark_url:
        return False
    try:
        url, params = _build_bark_request(bark_url, title=title, body=body, config=config)
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, params=params)
        if not response.is_success:
            logger.warning('Telegram个人号 Bark 推送失败 status=%s body=%s', response.status_code, response.text[:300])
        return response.is_success
    except Exception as exc:
        logger.warning('Telegram个人号 Bark 推送失败 err=%s', exc)
        return False


async def _record_event(account: LoginAccountSnapshot, event):
    from telethon.tl.types import User

    message = event.message
    if not message:
        return
    sender = await event.get_sender()
    chat = await event.get_chat()
    is_outgoing = bool(getattr(message, 'out', False))
    is_group_chat = not isinstance(chat, User)
    counterpart = None
    if is_outgoing and isinstance(chat, User):
        counterpart = chat
    elif isinstance(sender, User):
        counterpart = sender
    elif isinstance(chat, User):
        counterpart = chat
    if not counterpart or not getattr(counterpart, 'id', None):
        return
    text = getattr(message, 'message', None) or getattr(message, 'raw_text', None) or ''
    content_type = _content_type(message)
    chat_id = int(event.chat_id or counterpart.id)
    chat_title = _entity_name(chat)
    group_push_enabled = False
    if is_group_chat:
        flags = await telegram_group_delivery_flags(
            chat_id=chat_id,
            title=chat_title,
            username=_entity_username(chat),
        )
        group_push_enabled = bool(flags.get('push_enabled'))
    await record_telegram_message(
        tg_user_id=int(counterpart.id),
        chat_id=chat_id,
        message_id=int(message.id) if getattr(message, 'id', None) else None,
        direction=TelegramChatMessage.DIRECTION_OUT if is_outgoing else TelegramChatMessage.DIRECTION_IN,
        content_type=content_type,
        text=text,
        username=_entity_username(counterpart),
        first_name=_entity_name(counterpart),
        login_account_id=account.id,
        chat_title=chat_title,
        source='account',
    )
    push_config = await _cached_telegram_push_config()
    account_push_enabled = bool(account.listener_push_enabled)
    payload = _build_push_payload(
        is_outgoing=is_outgoing,
        is_private_chat=not is_group_chat,
        sender_name=_entity_name(sender),
        chat_title=chat_title,
        text=text,
        content_type=content_type,
        private_enabled=bool(push_config.get('private_enabled')),
        group_push_enabled=group_push_enabled,
    )
    if payload and account_push_enabled:
        await _send_listener_push(title=payload[0], body=payload[1])
    await _mark_account(account.id, 'logged_in')


async def _run_account_listener(account: LoginAccountSnapshot, stop_event: asyncio.Event):
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession

    api_id, api_hash = await _telegram_api_credentials()
    client = TelegramClient(StringSession(account.session_string), api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await _mark_account(account.id, 'session_expired', 'Telegram 会话已失效，请重新登录')
            return

        @client.on(events.NewMessage())
        async def _handler(event):
            try:
                await _record_event(account, event)
            except Exception as exc:
                logger.warning('个人号消息入库失败 account=%s err=%s', account.id, exc)

        logger.info('Telegram个人号监听已启动 account=%s label=%s', account.id, account.label)
        while not stop_event.is_set():
            if not client.is_connected():
                await client.connect()
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning('Telegram个人号监听停止 account=%s err=%s', account.id, exc)
        await _mark_account(account.id, 'listener_error', f'监听失败：{exc}')
    finally:
        await client.disconnect()


async def run_telegram_account_listeners(stop_event: asyncio.Event):
    tasks: dict[int, asyncio.Task] = {}
    while not stop_event.is_set():
        try:
            accounts = await _logged_in_accounts()
            active_ids = {account.id for account in accounts}
            for account_id in list(tasks):
                if account_id not in active_ids:
                    tasks.pop(account_id).cancel()
            for account in accounts:
                task = tasks.get(account.id)
                if not task or task.done():
                    tasks[account.id] = asyncio.create_task(_run_account_listener(account, stop_event))
        except Exception as exc:
            logger.warning('Telegram个人号监听调度失败：%s', exc)
        await asyncio.sleep(30)
    for task in tasks.values():
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks.values(), return_exceptions=True)
