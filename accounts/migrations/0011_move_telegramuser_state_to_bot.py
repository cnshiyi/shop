from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_alter_balanceledger_table_alter_telegramuser_table'),
        ('finance', '0003_move_recharge_state_to_orders'),
        ('mall', '0028_switch_user_fk_to_bot'),
        ('monitoring', '0003_switch_user_fk_to_bot'),
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
                migrations.DeleteModel(
                    name='TelegramUser',
                ),
            ],
        ),
    ]
