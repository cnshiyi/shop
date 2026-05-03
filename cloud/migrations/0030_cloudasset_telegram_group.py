# Generated for binding cloud assets to Telegram groups.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0011_telegramgroupfilter'),
        ('cloud', '0029_cloud_user_notice_log'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudasset',
            name='telegram_group',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='bot.telegramgroupfilter', verbose_name='绑定群组'),
        ),
    ]
