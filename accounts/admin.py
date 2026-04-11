from django.contrib import admin

from .models import TelegramUser


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ('tg_user_id', 'username', 'first_name', 'balance', 'balance_trx', 'created_at')
    search_fields = ('tg_user_id', 'username', 'first_name')
    list_filter = ('created_at',)
    readonly_fields = ('created_at', 'updated_at')
