from django.contrib import admin
from .models import Recharge


@admin.register(Recharge)
class RechargeAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'currency', 'amount', 'pay_amount', 'status', 'created_at')
    list_filter = ('status', 'currency', 'created_at')
    search_fields = ('tx_hash', 'user__tg_user_id', 'user__username')
    readonly_fields = ('created_at',)
