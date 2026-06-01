"""Dashboard API views for bot operation logs."""

from django.db.models import Q
from django.views.decorators.http import require_GET

from bot.api import (
    _get_keyword,
    _iso,
    _ok,
    _user_payload,
    dashboard_login_required,
)
from bot.models import BotOperationLog


def _bot_operation_log_payload(item):
    user_payload = _user_payload({
        'id': item.user.id,
        'tg_user_id': item.user.tg_user_id,
        'username': item.user.username,
        'first_name': item.user.first_name,
        'usernames': item.user.usernames,
        'primary_username': item.user.primary_username,
    }) if item.user_id else None
    return {
        'id': item.id,
        'created_at': _iso(item.created_at),
        'action_type': item.action_type,
        'action_label': item.get_action_type_display() if hasattr(item, 'get_action_type_display') else item.action_label,
        'payload': item.payload,
        'chat_id': item.chat_id,
        'message_id': item.message_id,
        'tg_user_id': item.tg_user_id,
        'user_id': item.user_id,
        'user_display_name': user_payload['display_name'] if user_payload else (item.first_name_snapshot or str(item.tg_user_id)),
        'username_label': user_payload['username_label'] if user_payload else (f"@{item.username_snapshot}" if item.username_snapshot else '-'),
    }


@dashboard_login_required
@require_GET
def bot_operation_logs(request):
    keyword = _get_keyword(request)
    queryset = BotOperationLog.objects.select_related('user').order_by('-created_at', '-id')
    if keyword:
        keyword_filter = (
            Q(payload__icontains=keyword)
            | Q(action_label__icontains=keyword)
            | Q(username_snapshot__icontains=keyword)
            | Q(first_name_snapshot__icontains=keyword)
            | Q(user__username__icontains=keyword)
            | Q(user__first_name__icontains=keyword)
        )
        if keyword.isdigit():
            keyword_filter |= Q(tg_user_id=int(keyword)) | Q(chat_id=int(keyword)) | Q(message_id=int(keyword))
        queryset = queryset.filter(keyword_filter)
    return _ok([_bot_operation_log_payload(item) for item in queryset[:200]])
