from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0009_product_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudasset',
            name='provider_status',
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True, verbose_name='云厂商原始状态'),
        ),
        migrations.AddField(
            model_name='cloudasset',
            name='status',
            field=models.CharField(choices=[('running', '运行中'), ('pending', '等待中'), ('starting', '启动中'), ('stopping', '停止中'), ('stopped', '已关机'), ('suspended', '已停机'), ('terminating', '终止中'), ('terminated', '已终止'), ('deleting', '删除中'), ('deleted', '已删除'), ('expired', '已过期'), ('missing', '云平台不存在'), ('unknown', '未知状态')], db_index=True, default='running', max_length=32, verbose_name='状态'),
        ),
        migrations.AddField(
            model_name='server',
            name='provider_status',
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True, verbose_name='云厂商原始状态'),
        ),
        migrations.AddField(
            model_name='server',
            name='status',
            field=models.CharField(choices=[('running', '运行中'), ('pending', '等待中'), ('starting', '启动中'), ('stopping', '停止中'), ('stopped', '已关机'), ('suspended', '已停机'), ('terminating', '终止中'), ('terminated', '已终止'), ('deleting', '删除中'), ('deleted', '已删除'), ('expired', '已过期'), ('missing', '云平台不存在'), ('unknown', '未知状态')], db_index=True, default='running', max_length=32, verbose_name='状态'),
        ),
    ]
