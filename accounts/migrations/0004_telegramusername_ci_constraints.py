from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_telegramuser_username_text'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='telegramusername',
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name='telegramusername',
            constraint=models.UniqueConstraint(fields=('user', 'username'), name='uniq_telegram_username_per_user'),
        ),
        migrations.AddConstraint(
            model_name='telegramusername',
            constraint=models.UniqueConstraint(Lower('username'), 'user', name='uniq_telegram_username_per_user_ci'),
        ),
    ]
