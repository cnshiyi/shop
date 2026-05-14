from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.runtime_config import get_runtime_config
from cloud.note_utils import append_note

MISSING_SYNC_COUNT_MARKER = '[missing_sync_count:'
MISSING_SYNC_CHECKED_AT_MARKER = '[missing_sync_checked_at:'
MISSING_CONFIRMATION_THRESHOLD_DEFAULT = 5
MISSING_CONFIRMATION_INTERVAL_MINUTES_DEFAULT = 60


def get_missing_confirmation_threshold() -> int:
    raw = str(get_runtime_config('cloud_sync_missing_delete_confirmations', str(MISSING_CONFIRMATION_THRESHOLD_DEFAULT)) or '').strip()
    try:
        return max(MISSING_CONFIRMATION_THRESHOLD_DEFAULT, int(raw))
    except Exception:
        return MISSING_CONFIRMATION_THRESHOLD_DEFAULT


def get_missing_confirmation_interval_minutes() -> int:
    raw = str(get_runtime_config('cloud_sync_missing_delete_confirm_interval_minutes', str(MISSING_CONFIRMATION_INTERVAL_MINUTES_DEFAULT)) or '').strip()
    try:
        return max(1, int(raw))
    except Exception:
        return MISSING_CONFIRMATION_INTERVAL_MINUTES_DEFAULT


def _marker_value(note: str | None, marker: str) -> str:
    text = str(note or '')
    start = text.rfind(marker)
    if start < 0:
        return ''
    start += len(marker)
    end = text.find(']', start)
    if end < 0:
        return ''
    return text[start:end].strip()


def _without_marker(text: str, marker: str) -> str:
    start = text.rfind(marker)
    if start >= 0:
        end = text.find(']', start)
        if end >= 0:
            return (text[:start] + text[end + 1:]).rstrip()
    return text


def missing_confirmation_count(note: str | None) -> int:
    try:
        return max(0, int(_marker_value(note, MISSING_SYNC_COUNT_MARKER)))
    except Exception:
        return 0


def missing_confirmation_checked_at(note: str | None):
    value = _marker_value(note, MISSING_SYNC_CHECKED_AT_MARKER)
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def missing_confirmation_interval_due(note: str | None, *, now=None) -> bool:
    last_checked_at = missing_confirmation_checked_at(note)
    if not last_checked_at:
        return True
    now = now or timezone.now()
    return now - last_checked_at >= timezone.timedelta(minutes=get_missing_confirmation_interval_minutes())


def missing_confirmation_state(note: str | None, *, now=None) -> dict:
    now = now or timezone.now()
    count = missing_confirmation_count(note)
    threshold = get_missing_confirmation_threshold()
    interval_minutes = get_missing_confirmation_interval_minutes()
    checked_at = missing_confirmation_checked_at(note)
    next_check_at = None
    if checked_at:
        next_check_at = checked_at + timezone.timedelta(minutes=interval_minutes)
    return {
        'count': count,
        'threshold': threshold,
        'checked_at': checked_at,
        'next_check_at': next_check_at,
        'interval_minutes': interval_minutes,
        'due': not checked_at or next_check_at <= now,
        'remaining': max(threshold - count, 0),
    }


def with_missing_confirmation_note(base_note: str, count: int, checked_at=None) -> str:
    text = str(base_note or '')
    text = _without_marker(text, MISSING_SYNC_COUNT_MARKER)
    text = _without_marker(text, MISSING_SYNC_CHECKED_AT_MARKER)
    checked_at = checked_at or timezone.now()
    return append_note(
        text,
        f'{MISSING_SYNC_COUNT_MARKER}{max(0, int(count or 0))}] {MISSING_SYNC_CHECKED_AT_MARKER}{checked_at.isoformat()}]',
    )


def mark_missing_confirmation_pending(record, *, old_public_ip: str, now_iso: str, provider_status: str, pending_status: str):
    threshold = get_missing_confirmation_threshold()
    current_count = missing_confirmation_count(getattr(record, 'note', ''))
    now = timezone.now()
    if current_count > 0 and not missing_confirmation_interval_due(getattr(record, 'note', ''), now=now):
        record.provider_status = pending_status
        record.updated_at = now
        return current_count, threshold
    next_count = current_count + 1
    record.provider_status = pending_status
    record.note = with_missing_confirmation_note(
        append_note(
            getattr(record, 'note', ''),
            f'状态: {provider_status}；公网IP: {old_public_ip or "缺失"}；最近同步: {now_iso}；待确认次数: {next_count}/{threshold}',
        ),
        next_count,
        checked_at=now,
    )
    record.updated_at = now
    return next_count, threshold
