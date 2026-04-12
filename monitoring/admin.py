from django.contrib import admin, messages

from .models import AddressMonitor


@admin.register(AddressMonitor)
class AddressMonitorAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'user', 'address', 'remark', 'monitor_transfers', 'monitor_resources',
        'last_energy', 'last_bandwidth', 'usdt_threshold', 'trx_threshold', 'is_active', 'created_at'
    )
    list_filter = ('is_active', 'monitor_transfers', 'monitor_resources', 'created_at')
    search_fields = ('address', 'remark', 'user__tg_user_id', 'user__username')
    readonly_fields = ('created_at', 'resource_checked_at')
    ordering = ('-id',)
    list_per_page = 50
    actions = ('activate_monitors', 'deactivate_monitors')

    @admin.action(description='启用监控')
    def activate_monitors(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f'已启用 {count} 个监控地址。', level=messages.SUCCESS)

    @admin.action(description='停用监控')
    def deactivate_monitors(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f'已停用 {count} 个监控地址。', level=messages.SUCCESS)
