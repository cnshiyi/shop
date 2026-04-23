from django.apps import AppConfig


class BizConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'biz'
    verbose_name = '业务聚合层'
