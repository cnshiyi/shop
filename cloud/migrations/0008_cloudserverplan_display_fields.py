from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('cloud', '0007_cloudserverplan_provider_plan_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverplan',
            name='display_plan_name',
            field=models.CharField(blank=True, default='', max_length=191, verbose_name='展示套餐名'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='cloudserverplan',
            name='display_cpu',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='展示CPU'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='cloudserverplan',
            name='display_memory',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='展示内存'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='cloudserverplan',
            name='display_storage',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='展示存储'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='cloudserverplan',
            name='display_bandwidth',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='展示带宽'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='cloudserverplan',
            name='display_description',
            field=models.TextField(blank=True, default='', verbose_name='展示说明'),
            preserve_default=False,
        ),
    ]
