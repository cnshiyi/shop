import json
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import F

from core.models import ExternalSyncLog
from cloud.models import AddressMonitor, DailyAddressStat, ResourceSnapshot


def record_external_sync_log(*, source: str, action: str, target: str = '', request_payload=None, response_payload=None, is_success: bool = True, error_message: str = '', account=None):
    return ExternalSyncLog.objects.create(
        account=account,
        source=source,
        action=action,
        target=target or '',
        request_payload=_to_json(request_payload),
        response_payload=_to_json(response_payload),
        is_success=is_success,
        error_message=error_message or '',
    )


def bump_daily_address_stat(*, user_id: int, address: str, currency: str, direction: str, amount: Decimal, account_scope: str = DailyAddressStat.ACCOUNT_SCOPE_PLATFORM, account_key: str = '', monitor_id: int | None = None, stats_date=None):
    stats_date = stats_date or datetime.now().date()
    amount = Decimal(str(amount or 0))
    if amount <= 0:
        return None
    defaults = {
        'monitor_id': monitor_id,
    }
    account_key_value = account_key or ''
    with transaction.atomic():
        stat, _ = DailyAddressStat.objects.select_for_update().get_or_create(
            user_id=user_id,
            address=address,
            currency=currency,
            stats_date=stats_date,
            account_scope=account_scope,
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


def save_resource_snapshot(*, monitor_id: int, address: str, energy: int, bandwidth: int, delta_energy: int = 0, delta_bandwidth: int = 0, account_scope: str = DailyAddressStat.ACCOUNT_SCOPE_PLATFORM, account_key: str = ''):
    return ResourceSnapshot.objects.create(
        monitor_id=monitor_id,
        address=address,
        energy=energy,
        bandwidth=bandwidth,
        delta_energy=delta_energy,
        delta_bandwidth=delta_bandwidth,
        account_scope=account_scope,
        account_key=account_key or '',
    )


def _to_json(value):
    if value in (None, ''):
        return ''
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)
