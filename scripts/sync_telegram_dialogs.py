#!/usr/bin/env python3
"""Sync dialogs from logged-in Telegram accounts into the local database.

Usage:
    uv run python scripts/sync_telegram_dialogs.py
    uv run python scripts/sync_telegram_dialogs.py --account-id 1 --limit 500
    uv run python scripts/sync_telegram_dialogs.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')

import django  # noqa: E402


django.setup()

from asgiref.sync import sync_to_async  # noqa: E402
from django.utils import timezone  # noqa: E402

from bot.models import TelegramChatMessage, TelegramLoginAccount, TelegramUser  # noqa: E402
from core.models import SiteConfig  # noqa: E402
from core.runtime_config import get_runtime_config  # noqa: E402


def _full_name(entity) -> str:
    first_name = getattr(entity, 'first_name', None)
    last_name = getattr(entity, 'last_name', None)
    title = getattr(entity, 'title', None)
    return ' '.join(part for part in [first_name, last_name] if part).strip() or title or ''


def _username(entity) -> str:
    return str(getattr(entity, 'username', None) or '').strip().lstrip('@')


def _content_type(message) -> str:
    if not message:
        return 'dialog'
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


@sync_to_async
def _telegram_api_credentials() -> tuple[int, str]:
    api_id = SiteConfig.get('telegram_api_id', '') or get_runtime_config('telegram_api_id', '')
    api_hash = SiteConfig.get('telegram_api_hash', '') or get_runtime_config('telegram_api_hash', '')
    if not str(api_id or '').strip() or not str(api_hash or '').strip():
        raise ValueError('未配置 Telegram API ID / API Hash')
    return int(str(api_id).strip()), str(api_hash).strip()


@sync_to_async
def _logged_in_accounts(account_id: int | None = None) -> list[dict]:
    qs = TelegramLoginAccount.objects.filter(status='logged_in').exclude(session_string__isnull=True).exclude(session_string='')
    if account_id:
        qs = qs.filter(id=account_id)
    accounts = []
    for item in qs.order_by('id'):
        session_string = item.session_string_plain
        if session_string:
            accounts.append({'id': item.id, 'label': item.label, 'session_string': session_string})
    return accounts


@sync_to_async
def _upsert_private_user(tg_user_id: int, username: str, first_name: str) -> int:
    user, created = TelegramUser.objects.get_or_create(
        tg_user_id=tg_user_id,
        defaults={'username': username or None, 'first_name': first_name[:191] or None},
    )
    changed = []
    if username:
        existing = TelegramUser.normalize_usernames(user.username)
        merged = TelegramUser.normalize_usernames([*existing, username])
        serialized = TelegramUser.serialize_usernames(merged)
        if serialized != (user.username or ''):
            user.username = serialized
            changed.append('username')
    if first_name and user.first_name != first_name[:191]:
        user.first_name = first_name[:191]
        changed.append('first_name')
    if changed and not created:
        changed.append('updated_at')
        user.save(update_fields=changed)
    return user.id


@sync_to_async
def _upsert_dialog_message(
    *,
    login_account_id: int,
    user_id: int | None,
    tg_user_id: int,
    chat_id: int,
    message_id: int | None,
    direction: str,
    content_type: str,
    text: str,
    username: str,
    first_name: str,
    chat_title: str,
) -> bool:
    existing = None
    if message_id:
        existing = TelegramChatMessage.objects.filter(
            chat_id=chat_id,
            message_id=message_id,
            direction=direction,
            login_account_id=login_account_id,
        ).first()
    if existing:
        changed = []
        updates = {
            'user_id': user_id,
            'tg_user_id': tg_user_id,
            'content_type': (content_type or 'dialog')[:32],
            'text': (text or '')[:4000],
            'username_snapshot': username[:191] or None,
            'first_name_snapshot': first_name[:191] or None,
            'chat_title': chat_title[:191] or None,
            'source': 'account_dialog',
        }
        for field, value in updates.items():
            if getattr(existing, field) != value:
                setattr(existing, field, value)
                changed.append(field)
        if changed:
            existing.save(update_fields=changed)
        return False
    TelegramChatMessage.objects.create(
        user_id=user_id,
        login_account_id=login_account_id,
        tg_user_id=tg_user_id,
        chat_id=chat_id,
        message_id=message_id,
        direction=direction,
        content_type=(content_type or 'dialog')[:32],
        text=(text or '')[:4000],
        username_snapshot=username[:191] or None,
        first_name_snapshot=first_name[:191] or None,
        chat_title=chat_title[:191] or None,
        source='account_dialog',
    )
    return True


@sync_to_async
def _mark_account_synced(account_id: int, note: str) -> None:
    TelegramLoginAccount.objects.filter(id=account_id).update(
        last_synced_at=timezone.now(),
        note=note[:1000],
        status='logged_in',
        updated_at=timezone.now(),
    )


async def _sender_snapshot(client, message, fallback_entity):
    if not message:
        return '', ''
    sender = None
    try:
        sender = await message.get_sender()
    except Exception:
        sender = None
    entity = sender or fallback_entity
    return _username(entity), _full_name(entity)


async def sync_account(account: dict, *, limit: int, include_groups: bool, dry_run: bool) -> dict:
    from telethon import TelegramClient, utils
    from telethon.sessions import StringSession
    from telethon.tl.types import User

    api_id, api_hash = await _telegram_api_credentials()
    client = TelegramClient(StringSession(account['session_string']), api_id, api_hash)
    stats = {'dialogs': 0, 'users': 0, 'messages_created': 0, 'messages_seen': 0, 'skipped_groups': 0}
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError(f"账号 {account['id']} 会话已失效，请重新登录")
        async for dialog in client.iter_dialogs(limit=limit):
            entity = dialog.entity
            is_private = isinstance(entity, User)
            if not is_private and not include_groups:
                stats['skipped_groups'] += 1
                continue
            chat_id = int(utils.get_peer_id(entity))
            message = dialog.message
            message_id = int(message.id) if message and getattr(message, 'id', None) else None
            direction = TelegramChatMessage.DIRECTION_OUT if message and getattr(message, 'out', False) else TelegramChatMessage.DIRECTION_IN
            text = getattr(message, 'message', None) or getattr(message, 'raw_text', None) or '' if message else ''
            chat_title = _full_name(entity) or str(dialog.name or chat_id)
            username = _username(entity) if is_private else ''
            first_name = _full_name(entity)
            user_id = None
            tg_user_id = int(getattr(entity, 'id', None) or chat_id)
            if is_private:
                stats['users'] += 1
                if not dry_run:
                    user_id = await _upsert_private_user(tg_user_id, username, first_name)
            else:
                sender_username, sender_name = await _sender_snapshot(client, message, entity)
                username = sender_username
                first_name = sender_name or chat_title
                tg_user_id = chat_id
            stats['dialogs'] += 1
            if dry_run:
                continue
            created = await _upsert_dialog_message(
                login_account_id=account['id'],
                user_id=user_id,
                tg_user_id=tg_user_id,
                chat_id=chat_id,
                message_id=message_id,
                direction=direction,
                content_type=_content_type(message),
                text=text,
                username=username,
                first_name=first_name,
                chat_title=chat_title,
            )
            stats['messages_seen'] += 1
            if created:
                stats['messages_created'] += 1
        if not dry_run:
            await _mark_account_synced(account['id'], f"聊天列表同步完成：dialogs={stats['dialogs']} users={stats['users']} created={stats['messages_created']}")
        return stats
    finally:
        await client.disconnect()


async def amain(args: argparse.Namespace) -> int:
    accounts = await _logged_in_accounts(args.account_id)
    if not accounts:
        print('没有可用的已登录 Telegram 账号。')
        return 1
    total = {'dialogs': 0, 'users': 0, 'messages_created': 0, 'messages_seen': 0, 'skipped_groups': 0}
    for account in accounts:
        print(f"同步账号 #{account['id']} {account['label']} ...")
        stats = await sync_account(account, limit=args.limit, include_groups=args.include_groups, dry_run=args.dry_run)
        for key, value in stats.items():
            total[key] += value
        print(
            f"完成账号 #{account['id']}: dialogs={stats['dialogs']} users={stats['users']} "
            f"created={stats['messages_created']} skipped_groups={stats['skipped_groups']}"
        )
    print(
        f"总计: dialogs={total['dialogs']} users={total['users']} created={total['messages_created']} "
        f"seen={total['messages_seen']} skipped_groups={total['skipped_groups']} dry_run={args.dry_run}"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='同步已登录 Telegram 账号的聊天列表用户信息到数据库。')
    parser.add_argument('--account-id', type=int, default=None, help='只同步指定 TelegramLoginAccount ID；默认同步全部 logged_in 账号。')
    parser.add_argument('--limit', type=int, default=500, help='每个账号最多扫描多少个聊天，默认 500。')
    parser.add_argument('--include-groups', action='store_true', help='同时把群组/频道最近会话写入聊天记录；默认只导入私聊用户。')
    parser.add_argument('--dry-run', action='store_true', help='只扫描统计，不写入数据库。')
    return parser.parse_args()


def main() -> int:
    return asyncio.run(amain(parse_args()))


if __name__ == '__main__':
    raise SystemExit(main())
