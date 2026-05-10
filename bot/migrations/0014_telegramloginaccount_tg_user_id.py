from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('bot', '0013_telegram_push_switches'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramloginaccount',
            name='tg_user_id',
            field=models.BigIntegerField(blank=True, db_index=True, null=True, verbose_name='Telegram 用户ID'),
        ),
    ]
