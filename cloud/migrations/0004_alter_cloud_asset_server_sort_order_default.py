from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0003_add_cloud_asset_server_sort_order'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cloudasset',
            name='sort_order',
            field=models.IntegerField(db_index=True, default=99, verbose_name='排序'),
        ),
        migrations.AlterField(
            model_name='server',
            name='sort_order',
            field=models.IntegerField(db_index=True, default=99, verbose_name='排序'),
        ),
    ]
