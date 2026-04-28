# Generated manually to split cloud lifecycle reminder toggles.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0021_cloudserverorder_ip_change_quota_default'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverorder',
            name='suspend_reminder_enabled',
            field=models.BooleanField(db_index=True, default=True, verbose_name='停机提醒'),
        ),
        migrations.AddField(
            model_name='cloudserverorder',
            name='delete_reminder_enabled',
            field=models.BooleanField(db_index=True, default=True, verbose_name='删机提醒'),
        ),
        migrations.AddField(
            model_name='cloudserverorder',
            name='ip_recycle_reminder_enabled',
            field=models.BooleanField(db_index=True, default=True, verbose_name='IP保留期提醒'),
        ),
    ]
