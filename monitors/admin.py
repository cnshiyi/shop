from django.contrib import admin
from .models import AddressMonitor


@admin.register(AddressMonitor)
class AddressMonitorAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'user', 'address', 'remark', 'monitor_transfers', 'monitor_resources',
        'last_energy', 'last_bandwidth', 'usdt_threshold', 'trx_threshold', 'is_active', 'created_at'
    )
    list_filter = ('is_active', 'created_at')
    search_fields = ('address', 'remark', 'user__tg_user_id', 'user__username')
