from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0002_move_balanceledger_state_from_accounts'),
        ('accounts', '0011_move_telegramuser_state_to_bot'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(
                    name='BalanceLedger',
                ),
            ],
        ),
    ]
