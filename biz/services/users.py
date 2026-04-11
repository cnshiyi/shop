from asgiref.sync import sync_to_async

from biz.models import TelegramUser


@sync_to_async
def get_or_create_user(tg_user_id: int, username: str | None, first_name: str | None) -> TelegramUser:
    user, _ = TelegramUser.objects.get_or_create(
        tg_user_id=tg_user_id,
        defaults={'username': username, 'first_name': first_name},
    )
    changed = False
    if user.username != username:
        user.username = username
        changed = True
    if user.first_name != first_name:
        user.first_name = first_name
        changed = True
    if changed:
        user.save(update_fields=['username', 'first_name', 'updated_at'])
    return user
