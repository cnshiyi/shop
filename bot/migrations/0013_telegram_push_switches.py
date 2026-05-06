from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0012_telegramgroupfilter_collapsed'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramloginaccount',
            name='listener_push_enabled',
            field=models.BooleanField('个人号监听推送', default=True, db_index=True),
        ),
        migrations.AddField(
            model_name='telegramgroupfilter',
            name='push_enabled',
            field=models.BooleanField('允许推送', default=False, db_index=True),
        ),
    ]
