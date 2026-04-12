from django.contrib import admin, messages
from django.utils.html import format_html
from .models import SiteConfig

admin.site.site_header = '云商城后台管理'
admin.site.site_title = '云商城后台'
admin.site.index_title = '运营管理面板'

CONFIG_HELP = {
    'bot_token': 'Telegram 机器人 Token',
    'receive_address': '收款地址（USDT/TRX 共用）',
    'trongrid_api_key': 'TRON API Key',
}


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    list_display = ('key', 'value_preview', 'description')
    search_fields = ('key', 'value')
    ordering = ('key',)
    actions = ('init_common_configs',)

    def value_preview(self, obj):
        value = obj.value or ''
        return value if len(value) <= 60 else f'{value[:60]}...'
    value_preview.short_description = '当前值'

    def description(self, obj):
        return CONFIG_HELP.get(obj.key, '-')
    description.short_description = '说明'

    fieldsets = (
        ('配置项', {'fields': ('key', 'value')}),
    )

    @admin.action(description='初始化常用配置项')
    def init_common_configs(self, request, queryset):
        created = 0
        for key in CONFIG_HELP:
            _, was_created = SiteConfig.objects.get_or_create(key=key, defaults={'value': ''})
            created += int(was_created)
        self.message_user(request, f'已初始化 {created} 个常用配置项。', level=messages.SUCCESS)
