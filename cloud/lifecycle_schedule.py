"""Central lifecycle schedule calculations for cloud resources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone as dt_timezone

from django.utils import timezone

from core.runtime_config import get_runtime_config


@dataclass(frozen=True)
class OrderLifecycleSchedule:
    renew_grace_expires_at: object | None = None
    suspend_at: object | None = None
    delete_at: object | None = None
    ip_recycle_at: object | None = None

    def as_update_fields(self) -> dict:
        return {
            'renew_grace_expires_at': self.renew_grace_expires_at,
            'suspend_at': self.suspend_at,
            'delete_at': self.delete_at,
            'ip_recycle_at': self.ip_recycle_at,
        }


def runtime_int_config(key: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(str(get_runtime_config(key, str(default)) or default).strip())
        return max(value, minimum)
    except Exception:
        return default


def runtime_time_config(key: str, default: str = '15:00') -> tuple[int, int]:
    try:
        raw = str(get_runtime_config(key, default) or default).strip()
        if '-' in raw:
            raw = raw.split('-', 1)[0].strip()
        hour_text, minute_text = raw.split(':', 1)
        hour = min(max(int(hour_text), 0), 23)
        minute = min(max(int(minute_text), 0), 59)
        return hour, minute
    except Exception:
        hour_text, minute_text = default.split(':', 1)
        return int(hour_text), int(minute_text)


def with_runtime_time(value, key: str, default: str = '15:00'):
    if not value:
        return None
    hour, minute = runtime_time_config(key, default)
    local_value = timezone.localtime(value) if timezone.is_aware(value) else value
    local_value = local_value.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if timezone.is_aware(local_value):
        return local_value.astimezone(dt_timezone.utc)
    return local_value


def normalize_asset_expiry(value):
    if not value:
        return value
    local_value = timezone.localtime(value) if timezone.is_aware(value) else value
    if (
        local_value.hour == 0
        and local_value.minute == 0
        and local_value.second == 0
        and local_value.microsecond == 0
    ):
        local_value = local_value.replace(hour=15)
        if timezone.is_aware(local_value):
            return local_value.astimezone(dt_timezone.utc)
        return timezone.make_aware(local_value, timezone.get_current_timezone()).astimezone(dt_timezone.utc)
    return value


def compute_order_lifecycle_schedule(expires_at) -> OrderLifecycleSchedule:
    if not expires_at:
        return OrderLifecycleSchedule()
    suspend_days = runtime_int_config('cloud_suspend_after_days', 3)
    delete_days = runtime_int_config('cloud_delete_after_days', 0)
    ip_recycle_days = runtime_int_config('cloud_unattached_ip_delete_after_days', 15)
    suspend_at = with_runtime_time(
        expires_at + timezone.timedelta(days=suspend_days),
        'cloud_suspend_time',
    )
    delete_at = with_runtime_time(
        suspend_at + timezone.timedelta(days=delete_days),
        'cloud_delete_time',
    )
    if delete_at and suspend_at and delete_at < suspend_at:
        delete_at = suspend_at
    ip_recycle_at = delete_at + timezone.timedelta(days=ip_recycle_days) if delete_at else None
    return OrderLifecycleSchedule(
        renew_grace_expires_at=suspend_at,
        suspend_at=suspend_at,
        delete_at=delete_at,
        ip_recycle_at=ip_recycle_at,
    )


def compute_order_lifecycle_fields(expires_at) -> dict:
    return {
        key: value
        for key, value in compute_order_lifecycle_schedule(expires_at).as_update_fields().items()
        if value is not None
    }


def compute_orphan_asset_delete_at(expires_at):
    if not expires_at:
        return None
    return compute_order_lifecycle_schedule(expires_at).delete_at or expires_at


def compute_unattached_ip_release_at(base_at=None):
    base_at = base_at or timezone.now()
    days = runtime_int_config('cloud_unattached_ip_delete_after_days', 15, minimum=1)
    return with_runtime_time(
        base_at + timezone.timedelta(days=days),
        'cloud_unattached_ip_delete_time',
    )
