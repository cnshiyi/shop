from asgiref.sync import sync_to_async
import logging

from bot.models import TelegramUser, TelegramUsername

logger = logging.getLogger(__name__)


def _normalize_usernames(username: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if not username:
        return []
    if isinstance(username, (list, tuple)):
        values = []
        for item in username:
            values.extend(_normalize_usernames(item))
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


def _serialize_usernames(usernames: list[str]) -> str:
    return ','.join(usernames)


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
) -> TelegramUser:
    incoming_usernames = _normalize_usernames(active_usernames) or _normalize_usernames(username)
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            '用户同步开始: tg_user_id=%s incoming_username=%s incoming_first_name=%s active_usernames=%s incoming_usernames=%s',
            tg_user_id,
            username,
            first_name,
            list(active_usernames or []),
            incoming_usernames,
        )
    user, created = TelegramUser.objects.get_or_create(
        tg_user_id=tg_user_id,
        defaults={'username': _serialize_usernames(incoming_usernames), 'first_name': first_name},
    )
    usernames = _merge_usernames(user.username, incoming_usernames)
    serialized_usernames = _serialize_usernames(usernames)
    changed = False
    previous_username = user.username
    previous_first_name = user.first_name
    if user.username != serialized_usernames:
        user.username = serialized_usernames
        changed = True
    if user.first_name != first_name:
        user.first_name = first_name
        changed = True
    if changed:
        user.save(update_fields=['username', 'first_name', 'updated_at'])

    existing = {item.username.lower(): item for item in user.telegramusernames.all()}
    incoming_keys = {value.lower() for value in incoming_usernames}
    primary_key = incoming_usernames[0].lower() if incoming_usernames else None
    for index, value in enumerate(usernames):
        key = value.lower()
        item = existing.pop(key, None)
        should_be_primary = key == primary_key or (not primary_key and index == 0)
        if item:
            if item.is_primary != should_be_primary:
                item.is_primary = should_be_primary
                item.save(update_fields=['is_primary', 'updated_at'])
        else:
            TelegramUsername.objects.create(user=user, username=value, is_primary=should_be_primary)
    for leftover in existing.values():
        if leftover.username.lower() in incoming_keys and leftover.is_primary != (leftover.username.lower() == primary_key):
            leftover.is_primary = leftover.username.lower() == primary_key
            leftover.save(update_fields=['is_primary', 'updated_at'])
        elif leftover.is_primary and primary_key:
            leftover.is_primary = False
            leftover.save(update_fields=['is_primary', 'updated_at'])

    username_rows = list(
        user.telegramusernames.order_by('-is_primary', 'username').values('username', 'is_primary', 'created_at', 'updated_at')
    )
    if created or changed:
        logger.info(
            '用户同步完成: tg_user_id=%s user_id=%s created=%s changed=%s previous_username=%s current_username=%s previous_first_name=%s current_first_name=%s username_rows=%s',
            tg_user_id,
            user.id,
            created,
            changed,
            previous_username,
            user.username,
            previous_first_name,
            user.first_name,
            username_rows,
        )
    else:
        logger.debug('用户同步无变化: tg_user_id=%s user_id=%s', tg_user_id, user.id)
    return user
