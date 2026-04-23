from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0010_cloud_asset_server_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cloudasset',
            name='status',
            field=models.CharField(choices=[('running', '运行中'), ('pending', '等待中'), ('starting', '启动中'), ('stopping', '停止中'), ('stopped', '已关机'), ('suspended', '已停机'), ('terminating', '终止中'), ('terminated', '已终止'), ('deleting', '删除中'), ('deleted', '已删除'), ('expired', '已过期'), ('unknown', '未知状态')], db_index=True, default='running', max_length=32, verbose_name='状态'),
        ),
        migrations.AlterField(
            model_name='server',
            name='status',
            field=models.CharField(choices=[('running', '运行中'), ('pending', '等待中'), ('starting', '启动中'), ('stopping', '停止中'), ('stopped', '已关机'), ('suspended', '已停机'), ('terminating', '终止中'), ('terminated', '已终止'), ('deleting', '删除中'), ('deleted', '已删除'), ('expired', '已过期'), ('unknown', '未知状态')], db_index=True, default='running', max_length=32, verbose_name='状态'),
        ),
    ]
