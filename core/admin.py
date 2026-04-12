from django.contrib import admin
from .models import SiteConfig

admin.site.site_header = '云商城后台管理'
admin.site.site_title = '云商城后台'
admin.site.index_title = '运营管理面板'


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    list_display = ('key', 'value')
    search_fields = ('key', 'value')
    ordering = ('key',)
