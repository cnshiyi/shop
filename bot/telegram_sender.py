"""Helpers for sending messages through logged-in Telegram accounts."""

from __future__ import annotations

from asgiref.sync import sync_to_async

from bot.models import TelegramLoginAccount
from core.models import SiteConfig
from core.runtime_config import get_runtime_config


@sync_to_async
def _telegram_api_credentials() -> tuple[int, str]:
    api_id = SiteConfig.get('telegram_api_id', '') or get_runtime_config('telegram_api_id', '')
    api_hash = SiteConfig.get('telegram_api_hash', '') or get_runtime_config('telegram_api_hash', '')
    if not str(api_id or '').strip() or not str(api_hash or '').strip():
        raise ValueError('未配置 Telegram API ID / API Hash')
    return int(str(api_id).strip()), str(api_hash).strip()


@sync_to_async
def _notification_accounts() -> list[tuple[int, str, str]]:
    accounts = TelegramLoginAccount.objects.filter(
        status='logged_in',
        notify_enabled=True,
    ).exclude(session_string__isnull=True).exclude(session_string='').order_by('-updated_at', '-id')
    return [(item.id, item.label, item.session_string_plain) for item in accounts if item.session_string_plain]


async def send_with_notification_account(chat_id: int, text: str) -> bool:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id, api_hash = await _telegram_api_credentials()
    for account_id, _label, session_string in await _notification_accounts():
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                continue
            await client.send_message(chat_id, text)
            return True
        except Exception:
            continue
        finally:
            await client.disconnect()
    return False
