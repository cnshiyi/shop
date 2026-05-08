from django.utils import timezone

from core.runtime_config import get_runtime_config
from cloud.note_utils import append_note

MISSING_SYNC_COUNT_MARKER = '[missing_sync_count:'
MISSING_CONFIRMATION_THRESHOLD_DEFAULT = 2


def get_missing_confirmation_threshold() -> int:
    raw = str(get_runtime_config('cloud_sync_missing_delete_confirmations', str(MISSING_CONFIRMATION_THRESHOLD_DEFAULT)) or '').strip()
    try:
        return max(1, int(raw))
    except Exception:
        return MISSING_CONFIRMATION_THRESHOLD_DEFAULT


def missing_confirmation_count(note: str | None) -> int:
    text = str(note or '')
    start = text.rfind(MISSING_SYNC_COUNT_MARKER)
    if start < 0:
        return 0
    start += len(MISSING_SYNC_COUNT_MARKER)
    end = text.find(']', start)
    if end < 0:
        return 0
    try:
        return max(0, int(text[start:end].strip()))
    except Exception:
        return 0


def with_missing_confirmation_note(base_note: str, count: int) -> str:
    text = str(base_note or '')
    start = text.rfind(MISSING_SYNC_COUNT_MARKER)
    if start >= 0:
        end = text.find(']', start)
        if end >= 0:
            text = (text[:start] + text[end + 1:]).rstrip()
    return append_note(text, f'{MISSING_SYNC_COUNT_MARKER}{max(0, int(count or 0))}]')


def mark_missing_confirmation_pending(record, *, old_public_ip: str, now_iso: str, provider_status: str, pending_status: str):
    threshold = get_missing_confirmation_threshold()
    current_count = missing_confirmation_count(getattr(record, 'note', ''))
    next_count = current_count + 1
    record.provider_status = pending_status
    record.note = with_missing_confirmation_note(
        append_note(
            getattr(record, 'note', ''),
            f'状态: {provider_status}；公网IP: {old_public_ip or "缺失"}；最近同步: {now_iso}；待确认次数: {next_count}/{threshold}',
        ),
        next_count,
    )
    record.updated_at = timezone.now()
    return next_count, threshold
