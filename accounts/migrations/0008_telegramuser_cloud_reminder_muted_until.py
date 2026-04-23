from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_telegramuser_cloud_discount_rate'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramuser',
            name='cloud_reminder_muted_until',
            field=models.DateTimeField(blank=True, null=True, verbose_name='云服务器提醒静默到'),
        ),
    ]
