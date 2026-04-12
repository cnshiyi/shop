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
    fieldsets = (
        ('基础信息', {'fields': ('user', 'address', 'remark', 'is_active')}),
        ('监控开关', {'fields': ('monitor_transfers', 'monitor_resources')}),
        ('阈值设置', {'fields': ('usdt_threshold', 'trx_threshold')}),
        ('资源快照', {'fields': ('last_energy', 'last_bandwidth', 'resource_checked_at')}),
        ('统计信息', {'fields': ('daily_income', 'daily_expense', 'stats_date')}),
        ('时间信息', {'fields': ('created_at',)}),
    )

    @admin.action(description='启用监控')
    def activate_monitors(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f'已启用 {count} 个监控地址。', level=messages.SUCCESS)

    @admin.action(description='停用监控')
    def deactivate_monitors(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f'已停用 {count} 个监控地址。', level=messages.SUCCESS)
