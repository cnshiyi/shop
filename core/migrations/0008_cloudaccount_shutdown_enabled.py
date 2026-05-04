from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_siteconfig_sort_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudaccountconfig',
            name='shutdown_enabled',
            field=models.BooleanField(db_index=True, default=True, verbose_name='关机计划启用'),
        ),
    ]
