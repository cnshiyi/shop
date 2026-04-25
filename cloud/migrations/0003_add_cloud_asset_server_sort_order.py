from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0002_addressmonitor_resourcesnapshot_dailyaddressstat'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudasset',
            name='sort_order',
            field=models.IntegerField(db_index=True, default=0, verbose_name='排序'),
        ),
        migrations.AddField(
            model_name='server',
            name='sort_order',
            field=models.IntegerField(db_index=True, default=0, verbose_name='排序'),
        ),
    ]
