from datetime import datetime, timezone as dt_timezone

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.runtime_config import get_runtime_config

MISSING_CONFIRMATION_STATE_KEY = 'missing_confirmation'
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


def _parse_state_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if timezone.is_aware(value) else timezone.make_aware(value, timezone.get_current_timezone())
    if isinstance(value, (int, float)) or str(value).isdigit():
        return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)
    parsed = parse_datetime(str(value))
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _state_payload(record_or_state) -> dict:
    if isinstance(record_or_state, dict):
        source = record_or_state.get('sync_state') if isinstance(record_or_state.get('sync_state'), dict) else record_or_state
    else:
        source = getattr(record_or_state, 'sync_state', None) or {}
    if not isinstance(source, dict):
        return {}
    payload = source.get(MISSING_CONFIRMATION_STATE_KEY)
    return payload if isinstance(payload, dict) else {}


def _record_sync_state(record) -> dict:
    state = getattr(record, 'sync_state', None)
    if not isinstance(state, dict):
        state = {}
    else:
        state = dict(state)
    record.sync_state = state
    return state


def _state_count(payload: dict) -> int:
    try:
        return max(0, int(payload.get('count') or 0))
    except Exception:
        return 0


def missing_confirmation_count(record_or_state) -> int:
    return _state_count(_state_payload(record_or_state))


def missing_confirmation_checked_at(record_or_state):
    return _parse_state_datetime(_state_payload(record_or_state).get('checked_at'))


def missing_confirmation_interval_due(record_or_state, *, now=None) -> bool:
    last_checked_at = missing_confirmation_checked_at(record_or_state)
    if not last_checked_at:
        return True
    now = now or timezone.now()
    return now - last_checked_at >= timezone.timedelta(minutes=get_missing_confirmation_interval_minutes())


def missing_confirmation_state(record_or_state, *, now=None) -> dict:
    now = now or timezone.now()
    payload = _state_payload(record_or_state)
    count = _state_count(payload)
    threshold = get_missing_confirmation_threshold()
    interval_minutes = get_missing_confirmation_interval_minutes()
    checked_at = _parse_state_datetime(payload.get('checked_at'))
    first_seen_at = _parse_state_datetime(payload.get('first_seen_at'))
    next_check_at = checked_at + timezone.timedelta(minutes=interval_minutes) if checked_at else None
    due = not checked_at or next_check_at <= now
    return {
        'count': count,
        'threshold': threshold,
        'checked_at': checked_at,
        'first_seen_at': first_seen_at,
        'next_check_at': next_check_at,
        'interval_minutes': interval_minutes,
        'due': due,
        'remaining': max(threshold - count, 0),
        'status': str(payload.get('status') or ''),
        'provider_status': str(payload.get('provider_status') or ''),
        'pending_status': str(payload.get('pending_status') or ''),
        'old_public_ip': str(payload.get('old_public_ip') or ''),
    }


def mark_missing_confirmation_pending(record, *, old_public_ip: str, now_iso: str, provider_status: str, pending_status: str):
    threshold = get_missing_confirmation_threshold()
    now = _parse_state_datetime(now_iso) or timezone.now()
    current_state = missing_confirmation_state(record, now=now)
    current_count = current_state['count']
    checked_at = current_state['checked_at']
    if current_count > 0 and checked_at and not missing_confirmation_interval_due(record, now=now):
        next_count = current_count
        effective_checked_at = checked_at
    else:
        next_count = current_count + 1
        effective_checked_at = now

    state = _record_sync_state(record)
    existing = state.get(MISSING_CONFIRMATION_STATE_KEY)
    existing = existing if isinstance(existing, dict) else {}
    first_seen_at = existing.get('first_seen_at') or now.isoformat()
    state[MISSING_CONFIRMATION_STATE_KEY] = {
        'status': 'confirmed' if next_count >= threshold else 'pending',
        'count': next_count,
        'threshold': threshold,
        'checked_at': effective_checked_at.isoformat(),
        'first_seen_at': first_seen_at,
        'last_seen_at': now.isoformat(),
        'interval_minutes': get_missing_confirmation_interval_minutes(),
        'provider_status': str(provider_status or ''),
        'pending_status': str(pending_status or ''),
        'old_public_ip': str(old_public_ip or ''),
    }
    record.provider_status = str(pending_status or provider_status or '')
    record.updated_at = now
    return next_count, threshold


def clear_missing_confirmation_state(record):
    state = _record_sync_state(record)
    if MISSING_CONFIRMATION_STATE_KEY in state:
        state.pop(MISSING_CONFIRMATION_STATE_KEY, None)
        return True
    return False
