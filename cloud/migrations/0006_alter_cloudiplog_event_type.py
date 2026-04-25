from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0004_alter_cloud_asset_server_sort_order_default'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cloudiplog',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('created', '创建分配'),
                    ('changed', 'IP变更'),
                    ('renewed', '续费'),
                    ('expired', '到期'),
                    ('suspended', '延停'),
                    ('deleted', '删除'),
                    ('recycled', '回收'),
                ],
                db_index=True,
                max_length=32,
                verbose_name='事件类型',
            ),
        ),
    ]
