from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_alter_balanceledger_table_alter_telegramuser_table'),
        ('orders', '0001_initial'),
        ('cloud', '0002_addressmonitor_resourcesnapshot_dailyaddressstat'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name='balanceledger',
                    name='user',
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='balance_ledgers', to='bot.telegramuser', verbose_name='用户'),
                ),
            ],
        ),
    ]
