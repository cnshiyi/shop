from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0014_telegramloginaccount_tg_user_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramgroupfilter',
            name='archived',
            field=models.BooleanField(db_index=True, default=False, verbose_name='归档'),
        ),
    ]
