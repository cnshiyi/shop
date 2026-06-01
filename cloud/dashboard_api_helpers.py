"""Cloud dashboard helper functions shared by split API modules."""

import uuid

from django.db.models import F


def _generate_cloud_plan_config_id():
    return f'cfg-{uuid.uuid4().hex[:12]}'


def _preserve_link_status_label(*notes):
    text = '\n'.join(str(item or '') for item in notes if item)
    if '继续保留旧机服务' in text:
        return '重装失败，仍使用旧机'
    if '已发起重装迁移' in text:
        return '重装迁移中'
    if '重装迁移完成' in text:
        return '已切换到新机'
    return ''


def _preserve_link_status_with_countdown(status_label, countdown_label):
    status_label = str(status_label or '').strip()
    countdown_label = str(countdown_label or '').strip()
    if status_label == '重装迁移中':
        return ''
    if not status_label:
        return ''
    if countdown_label and countdown_label != '-' and '剩余' not in status_label and '已过期' not in status_label:
        return f'{status_label}（{countdown_label}）'
    return status_label


def _dashboard_sort_direction(request):
    direction = (request.GET.get('sort_order') or request.GET.get('sort_direction') or '').strip().lower()
    return 'desc' if direction in {'desc', 'descending', '降序'} else 'asc'


def _dashboard_expiry_ordering(field_name: str, direction: str):
    field = F(field_name)
    if direction == 'desc':
        return [field.desc(nulls_last=True), '-updated_at', '-id']
    return [field.asc(nulls_last=True), '-updated_at', '-id']
