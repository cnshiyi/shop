from decimal import Decimal

from asgiref.sync import sync_to_async

from biz.models import AddressMonitor


@sync_to_async
def list_monitors(user_id: int):
    return list(AddressMonitor.objects.filter(user_id=user_id).order_by('-created_at'))


@sync_to_async
def get_monitor(monitor_id: int, user_id: int):
    return AddressMonitor.objects.filter(id=monitor_id, user_id=user_id).first()


@sync_to_async
def add_monitor(user_id: int, address: str, remark: str | None):
    return AddressMonitor.objects.create(user_id=user_id, address=address, remark=remark or '')


@sync_to_async
def delete_monitor(monitor_id: int, user_id: int) -> bool:
    deleted, _ = AddressMonitor.objects.filter(id=monitor_id, user_id=user_id).delete()
    return deleted > 0


@sync_to_async
def set_monitor_threshold(monitor_id: int, user_id: int, currency: str, amount: Decimal) -> bool:
    field = 'usdt_threshold' if currency == 'USDT' else 'trx_threshold'
    return AddressMonitor.objects.filter(id=monitor_id, user_id=user_id).update(**{field: amount}) > 0


@sync_to_async
def toggle_monitor_flag(monitor_id: int, user_id: int, field: str):
    monitor = AddressMonitor.objects.filter(id=monitor_id, user_id=user_id).first()
    if not monitor or field not in {'monitor_transfers', 'monitor_resources'}:
        return None
    current = getattr(monitor, field)
    setattr(monitor, field, not current)
    monitor.save(update_fields=[field])
    return monitor
