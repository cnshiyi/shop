from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0035_cloud_auto_renew_plan'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='dailyaddressstat',
            name='uniq_daily_address_stat_scope',
        ),
        migrations.AddConstraint(
            model_name='dailyaddressstat',
            constraint=models.UniqueConstraint(
                fields=('user', 'address', 'currency', 'stats_date', 'account_scope', 'account_key'),
                name='uniq_daily_address_stat_scope',
            ),
        ),
    ]
