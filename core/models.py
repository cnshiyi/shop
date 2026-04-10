from django.db import models


class SiteConfig(models.Model):
    key = models.CharField('键', max_length=191, unique=True, db_index=True)
    value = models.TextField('值', blank=True, null=True)

    class Meta:
        db_table = 'configs'
        verbose_name = '系统配置'
        verbose_name_plural = '系统配置'

    @classmethod
    def get(cls, key: str, default: str = '') -> str:
        try:
            obj = cls.objects.filter(key=key).first()
            return obj.value if obj and obj.value else default
        except Exception:
            return default

    def __str__(self):
        return self.key
