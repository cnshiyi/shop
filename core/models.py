from django.db import models


class SiteConfig(models.Model):
    key = models.CharField('键', max_length=191, unique=True, db_index=True)
    value = models.TextField('值', blank=True, null=True)

    class Meta:
        db_table = 'configs'
        verbose_name = '系统配置'
        verbose_name_plural = '系统配置'

    def __str__(self):
        return self.key
