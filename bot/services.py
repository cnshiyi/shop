"""bot 域服务。"""

import logging

from asgiref.sync import sync_to_async
from django.apps import apps

logger = logging.getLogger(__name__)


def _telegram_user_model():
    return apps.get_model('bot', 'TelegramUser')


def _normalize_usernames(username: str | list[str] | tuple[str, ...] | None) -> list[str]:
    TelegramUser = _telegram_user_model()
    return TelegramUser.normalize_usernames(username)


def _serialize_usernames(usernames: list[str]) -> str:
    TelegramUser = _telegram_user_model()
    return TelegramUser.serialize_usernames(usernames)


def _merge_usernames(current: str | None, incoming: list[str]) -> list[str]:
    merged = []
    seen = set()
    for value in [*incoming, *_normalize_usernames(current)]:
        key = value.lower()
        if value and key not in seen:
            merged.append(value)
            seen.add(key)
    return merged


@sync_to_async
def get_or_create_user(
    tg_user_id: int,
    username: str | None,
    first_name: str | None,
    active_usernames: list[str] | tuple[str, ...] | None = None,
):
    TelegramUser = _telegram_user_model()
    incoming_usernames = _normalize_usernames(active_usernames) or _normalize_usernames(username)
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            'sync_user start tg_user_id=%s username=%s first_name=%s active_usernames=%s',
            tg_user_id,
            username,
            first_name,
            incoming_usernames,
        )
    user, _ = TelegramUser.objects.get_or_create(
        tg_user_id=tg_user_id,
        defaults={
            'username': _serialize_usernames(incoming_usernames),
            'first_name': first_name or '',
        },
    )
    changed = []
    merged_usernames = _merge_usernames(user.username, incoming_usernames)
    serialized_usernames = _serialize_usernames(merged_usernames)
    if user.username != serialized_usernames:
        user.username = serialized_usernames
        changed.append('username')
    if first_name and user.first_name != first_name:
        user.first_name = first_name
        changed.append('first_name')
    if changed:
        changed.append('updated_at')
        user.save(update_fields=changed)
    return user
