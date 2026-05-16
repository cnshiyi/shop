import json
import re
from datetime import datetime
from decimal import Decimal

from django.apps import apps
from django.db import transaction


def _external_sync_log_model():
    return apps.get_model('core', 'ExternalSyncLog')


def _address_monitor_model():
    return apps.get_model('cloud', 'AddressMonitor')


def _daily_address_stat_model():
    return apps.get_model('cloud', 'DailyAddressStat')


def _resource_snapshot_model():
    return apps.get_model('cloud', 'ResourceSnapshot')


def record_external_sync_log(*, source: str, action: str, target: str = '', request_payload=None, response_payload=None, is_success: bool = True, error_message: str = '', account=None):
    ExternalSyncLog = _external_sync_log_model()
    return ExternalSyncLog.objects.create(
        account=account,
        source=source,
        action=action,
        target=target or '',
        request_payload=_to_json(request_payload),
        response_payload=_to_json(response_payload),
        is_success=is_success,
        error_message=_sanitize_text(error_message or ''),
    )


def bump_daily_address_stat(*, user_id: int, address: str, currency: str, direction: str, amount: Decimal, account_scope: str | None = None, account_key: str = '', monitor_id: int | None = None, stats_date=None):
    DailyAddressStat = _daily_address_stat_model()
    stats_date = stats_date or datetime.now().date()
    amount = Decimal(str(amount or 0))
    if amount <= 0:
        return None
    defaults = {
        'monitor_id': monitor_id,
    }
    account_key_value = account_key or ''
    account_scope_value = account_scope or DailyAddressStat.ACCOUNT_SCOPE_PLATFORM
    with transaction.atomic():
        stat, _ = DailyAddressStat.objects.select_for_update().get_or_create(
            user_id=user_id,
            address=address,
            currency=currency,
            stats_date=stats_date,
            account_scope=account_scope_value,
            account_key=account_key_value,
            defaults=defaults,
        )
        changed_fields = []
        if monitor_id and stat.monitor_id != monitor_id:
            stat.monitor_id = monitor_id
            changed_fields.append('monitor')
        if direction == 'income':
            stat.income = (stat.income or Decimal('0')) + amount
            changed_fields.append('income')
        elif direction == 'expense':
            stat.expense = (stat.expense or Decimal('0')) + amount
            changed_fields.append('expense')
        if changed_fields:
            changed_fields.append('updated_at')
            stat.save(update_fields=changed_fields)
    return stat


def save_resource_snapshot(*, monitor_id: int, address: str, energy: int, bandwidth: int, delta_energy: int = 0, delta_bandwidth: int = 0, account_scope: str | None = None, account_key: str = ''):
    DailyAddressStat = _daily_address_stat_model()
    ResourceSnapshot = _resource_snapshot_model()
    return ResourceSnapshot.objects.create(
        monitor_id=monitor_id,
        address=address,
        energy=energy,
        bandwidth=bandwidth,
        delta_energy=delta_energy,
        delta_bandwidth=delta_bandwidth,
        account_scope=account_scope or DailyAddressStat.ACCOUNT_SCOPE_PLATFORM,
        account_key=account_key or '',
    )


def _to_json(value):
    if value in (None, ''):
        return ''
    if isinstance(value, str):
        return _sanitize_text(value)
    try:
        return json.dumps(_sanitize_payload(value), ensure_ascii=False, default=str)
    except Exception:
        return _sanitize_text(str(value))


_SENSITIVE_KEY_PARTS = (
    'access_key',
    'accesskey',
    'api_key',
    'apikey',
    'authorization',
    'auth_token',
    'login_password',
    'mtproxy_secret',
    'password',
    'private_key',
    'secret',
    'secret_key',
    'secretkey',
    'session_string',
    'token',
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r'(?i)(access[_-]?key|api[_-]?key|authorization|login[_-]?password|mtproxy[_-]?secret|password|private[_-]?key|secret[_-]?key|secret|session[_-]?string|token)\s*[:=]\s*([^,;&\r\n]+)'
)


def _is_sensitive_key(key) -> bool:
    normalized = str(key or '').lower().replace('-', '_')
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _sanitize_payload(value):
    if isinstance(value, dict):
        return {
            key: '***'
            if _is_sensitive_key(key)
            else _sanitize_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_text(value: str) -> str:
    if not value:
        return ''
    text = str(value)
    stripped = text.strip()
    if stripped and stripped[0] in '[{':
        try:
            parsed = json.loads(stripped)
            return json.dumps(_sanitize_payload(parsed), ensure_ascii=False, default=str)
        except Exception:
            pass
    return _SENSITIVE_ASSIGNMENT_RE.sub(r'\1=***', text)
