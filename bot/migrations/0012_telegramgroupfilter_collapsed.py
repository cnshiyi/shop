from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0011_telegramgroupfilter'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramgroupfilter',
            name='collapsed',
            field=models.BooleanField(db_index=True, default=False, verbose_name='折叠'),
        ),
    ]
