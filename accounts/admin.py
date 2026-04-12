from django.contrib import admin, messages
from django.db.models import Sum

from .models import TelegramUser


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ('id', 'tg_user_id', 'username', 'first_name', 'balance', 'balance_trx', 'order_count', 'cloud_order_count', 'created_at')
    search_fields = ('tg_user_id', 'username', 'first_name')
    list_filter = ('created_at',)
    readonly_fields = ('created_at', 'updated_at', 'tg_user_id')
    ordering = ('-id',)
    list_per_page = 50
    actions = ('add_1_usdt', 'add_10_usdt', 'add_100_trx', 'deduct_1_usdt', 'deduct_10_trx')
    fieldsets = (
        ('基础信息', {'fields': ('tg_user_id', 'username', 'first_name')}),
        ('余额信息', {'fields': ('balance', 'balance_trx')}),
        ('时间信息', {'fields': ('created_at', 'updated_at')}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related('orders', 'cloud_server_orders')

    def order_count(self, obj):
        return obj.orders.count()
    order_count.short_description = '订阅单数'

    def cloud_order_count(self, obj):
        return obj.cloud_server_orders.count()
    cloud_order_count.short_description = '云服务器单数'

    @admin.action(description='充值 1 USDT')
    def add_1_usdt(self, request, queryset):
        count = 0
        for user in queryset:
            user.balance = (user.balance or 0) + 1
            user.save(update_fields=['balance', 'updated_at'])
            count += 1
        self.message_user(request, f'已为 {count} 个用户充值 1 USDT。', level=messages.SUCCESS)

    @admin.action(description='充值 10 USDT')
    def add_10_usdt(self, request, queryset):
        count = 0
        for user in queryset:
            user.balance = (user.balance or 0) + 10
            user.save(update_fields=['balance', 'updated_at'])
            count += 1
        self.message_user(request, f'已为 {count} 个用户充值 10 USDT。', level=messages.SUCCESS)

    @admin.action(description='扣减 1 USDT')
    def deduct_1_usdt(self, request, queryset):
        count = 0
        for user in queryset:
            current = user.balance or 0
            user.balance = max(0, current - 1)
            user.save(update_fields=['balance', 'updated_at'])
            count += 1
        self.message_user(request, f'已为 {count} 个用户扣减 1 USDT。', level=messages.SUCCESS)

    @admin.action(description='扣减 10 TRX')
    def deduct_10_trx(self, request, queryset):
        count = 0
        for user in queryset:
            current = user.balance_trx or 0
            user.balance_trx = max(0, current - 10)
            user.save(update_fields=['balance_trx', 'updated_at'])
            count += 1
        self.message_user(request, f'已为 {count} 个用户扣减 10 TRX。', level=messages.SUCCESS)
