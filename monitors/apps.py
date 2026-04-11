from django.apps import AppConfig


class MonitorsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'monitors'
    verbose_name = '地址监控（兼容层）'
