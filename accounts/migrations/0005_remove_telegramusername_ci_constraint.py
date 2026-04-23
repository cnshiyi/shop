from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_telegramusername_ci_constraints'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='telegramusername',
            name='uniq_telegram_username_per_user_ci',
        ),
    ]
