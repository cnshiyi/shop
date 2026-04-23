from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_telegramuser_cloud_reminder_muted_until'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(
                    name='TelegramUsername',
                ),
            ],
        ),
    ]
