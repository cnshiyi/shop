from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramUsername',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('username', models.CharField(db_index=True, max_length=191, verbose_name='用户名')),
                ('is_primary', models.BooleanField(db_index=True, default=False, verbose_name='主用户名')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='telegramusernames', to='accounts.telegramuser', verbose_name='Telegram 用户')),
            ],
            options={
                'verbose_name': 'Telegram用户名',
                'verbose_name_plural': 'Telegram用户名',
                'db_table': 'telegram_usernames',
                'ordering': ['-is_primary', 'username'],
                'unique_together': {('user', 'username')},
            },
        ),
    ]
