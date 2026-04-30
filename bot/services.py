"""bot 域服务。"""

import logging

from asgiref.sync import sync_to_async
from django.utils import timezone
from django.apps import apps

logger = logging.getLogger(__name__)


def _telegram_user_model():
    return apps.get_model('bot', 'TelegramUser')


def _admin_reply_link_model():
    return apps.get_model('bot', 'AdminReplyLink')


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


def _get_or_create_user_sync(
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


@sync_to_async
def get_or_create_user(
    tg_user_id: int,
    username: str | None,
    first_name: str | None,
    active_usernames: list[str] | tuple[str, ...] | None = None,
):
    return _get_or_create_user_sync(tg_user_id, username, first_name, active_usernames)


@sync_to_async
def record_bot_operation_log(
    tg_user_id: int,
    action_type: str,
    payload: str | None,
    username: str | None,
    first_name: str | None,
    chat_id: int | None = None,
    message_id: int | None = None,
    action_label: str | None = None,
):
    BotOperationLog = apps.get_model('bot', 'BotOperationLog')
    user = _get_or_create_user_sync(tg_user_id, username, first_name)
    username_snapshot = (_normalize_usernames(username) or [''])[0] or None
    return BotOperationLog.objects.create(
        user=user,
        tg_user_id=tg_user_id,
        chat_id=chat_id,
        message_id=message_id,
        action_type=(action_type or 'message')[:32],
        action_label=(action_label or '')[:191] or None,
        payload=(payload or '')[:4000] or None,
        username_snapshot=username_snapshot,
        first_name_snapshot=(first_name or '')[:191] or None,
    )


@sync_to_async
def create_admin_reply_link(
    *,
    admin_chat_id: int,
    admin_message_id: int,
    user_tg_id: int,
    user_chat_id: int,
    user_message_id: int | None = None,
    source_content_type: str = 'text',
):
    AdminReplyLink = _admin_reply_link_model()
    user = _get_or_create_user_sync(user_tg_id, None, None)
    link, _ = AdminReplyLink.objects.update_or_create(
        admin_chat_id=admin_chat_id,
        admin_message_id=admin_message_id,
        defaults={
            'user': user,
            'user_chat_id': user_chat_id,
            'user_message_id': user_message_id,
            'source_content_type': (source_content_type or 'text')[:32],
            'is_active': True,
        },
    )
    return link


@sync_to_async
def get_admin_reply_link_by_id(link_id: int):
    AdminReplyLink = _admin_reply_link_model()
    return AdminReplyLink.objects.select_related('user').filter(id=link_id, is_active=True).first()


@sync_to_async
def get_admin_reply_link(admin_chat_id: int, admin_message_id: int):
    AdminReplyLink = _admin_reply_link_model()
    return (
        AdminReplyLink.objects.select_related('user')
        .filter(admin_chat_id=admin_chat_id, admin_message_id=admin_message_id, is_active=True)
        .first()
    )


@sync_to_async
def is_admin_forward_muted(tg_user_id: int) -> bool:
    TelegramUser = _telegram_user_model()
    user = TelegramUser.objects.filter(tg_user_id=tg_user_id).only('admin_forward_muted_until').first()
    return bool(user and user.admin_forward_muted_until and user.admin_forward_muted_until > timezone.now())


@sync_to_async
def mute_admin_forward_for_days(tg_user_id: int, days: int = 3):
    TelegramUser = _telegram_user_model()
    user = _get_or_create_user_sync(tg_user_id, None, None)
    muted_until = timezone.now() + timezone.timedelta(days=days)
    user.admin_forward_muted_until = muted_until
    user.save(update_fields=['admin_forward_muted_until', 'updated_at'])
    return muted_until


@sync_to_async
def get_admin_forward_mute_status(tg_user_id: int):
    TelegramUser = _telegram_user_model()
    user = TelegramUser.objects.filter(tg_user_id=tg_user_id).only('admin_forward_muted_until').first()
    return user.admin_forward_muted_until if user else None


@sync_to_async
def record_telegram_message(
    tg_user_id: int,
    chat_id: int,
    message_id: int | None,
    direction: str,
    content_type: str,
    text: str | None,
    username: str | None,
    first_name: str | None,
    login_account_id: int | None = None,
    chat_title: str | None = None,
    source: str = 'bot',
):
    TelegramChatMessage = apps.get_model('bot', 'TelegramChatMessage')
    user = _get_or_create_user_sync(tg_user_id, username, first_name)
    username_snapshot = (_normalize_usernames(username) or [''])[0] or None
    existing = None
    if message_id:
        existing = TelegramChatMessage.objects.filter(chat_id=chat_id, message_id=message_id, direction=direction).first()
    if existing:
        changed = []
        if not existing.user_id and user:
            existing.user = user
            changed.append('user')
        if username_snapshot and existing.username_snapshot != username_snapshot:
            existing.username_snapshot = username_snapshot
            changed.append('username_snapshot')
        if first_name and existing.first_name_snapshot != first_name[:191]:
            existing.first_name_snapshot = first_name[:191]
            changed.append('first_name_snapshot')
        if changed:
            existing.save(update_fields=changed)
        return existing
    return TelegramChatMessage.objects.create(
        user=user,
        login_account_id=login_account_id,
        tg_user_id=tg_user_id,
        chat_id=chat_id,
        message_id=message_id,
        direction=direction,
        content_type=(content_type or 'text')[:32],
        text=(text or '')[:4000],
        username_snapshot=username_snapshot,
        first_name_snapshot=(first_name or '')[:191] or None,
        chat_title=(chat_title or '')[:191] or None,
        source=(source or 'bot')[:32],
    )
