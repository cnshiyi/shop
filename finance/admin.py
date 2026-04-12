from django.contrib import admin, messages
from django.utils import timezone

from .models import Recharge


@admin.register(Recharge)
class RechargeAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'currency', 'amount', 'pay_amount', 'status', 'tx_hash', 'created_at')
    list_filter = ('status', 'currency', 'created_at')
    search_fields = ('id', 'tx_hash', 'user__tg_user_id', 'user__username')
    readonly_fields = ('created_at', 'completed_at')
    ordering = ('-id',)
    list_per_page = 50
    actions = ('mark_completed',)
    fieldsets = (
        ('充值信息', {'fields': ('user', 'currency', 'amount', 'pay_amount', 'status')}),
        ('链上信息', {'fields': ('tx_hash',)}),
        ('时间信息', {'fields': ('created_at', 'completed_at', 'expired_at')}),
    )

    @admin.action(description='标记为已完成并入账')
    def mark_completed(self, request, queryset):
        count = 0
        for recharge in queryset.filter(status='pending'):
            field = 'balance_trx' if recharge.currency == 'TRX' else 'balance'
            user = recharge.user
            setattr(user, field, getattr(user, field) + recharge.amount)
            user.save(update_fields=[field, 'updated_at'])
            recharge.status = 'completed'
            recharge.completed_at = timezone.now()
            recharge.save(update_fields=['status', 'completed_at', 'updated_at'])
            count += 1
        self.message_user(request, f'已完成并入账 {count} 条充值记录。', level=messages.SUCCESS)
