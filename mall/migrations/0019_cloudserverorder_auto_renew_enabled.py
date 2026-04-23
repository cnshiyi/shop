from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0018_alter_cloudserverplan_price'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverorder',
            name='auto_renew_enabled',
            field=models.BooleanField(db_index=True, default=False, verbose_name='自动续费'),
        ),
    ]
