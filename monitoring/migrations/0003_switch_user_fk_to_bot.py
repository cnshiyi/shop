from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0001_initial'),
        ('monitoring', '0002_alter_addressmonitor_table_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name='addressmonitor',
                    name='user',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='bot.telegramuser', verbose_name='用户'),
                ),
                migrations.AlterField(
                    model_name='dailyaddressstat',
                    name='user',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='daily_address_stats', to='bot.telegramuser', verbose_name='用户'),
                ),
            ],
        ),
    ]
