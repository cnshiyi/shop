from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_move_balanceledger_state_to_orders'),
        ('orders', '0004_alter_cartitem_cloud_plan'),
        ('cloud', '0002_addressmonitor_resourcesnapshot_dailyaddressstat'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(
                    name='TelegramUser',
                ),
            ],
        ),
    ]
