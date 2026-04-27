from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0018_cloudserverorder_ip_change_quota'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverorder',
            name='cloud_reminder_enabled',
            field=models.BooleanField(db_index=True, default=True, verbose_name='到期提醒'),
        ),
    ]
