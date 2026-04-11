from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('mall', '0001_cloud_servers'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverorder',
            name='image_name',
            field=models.CharField(default='debian', max_length=128, verbose_name='镜像'),
        ),
        migrations.AddField(
            model_name='cloudserverorder',
            name='instance_id',
            field=models.CharField(blank=True, max_length=191, null=True, verbose_name='实例ID'),
        ),
        migrations.AddField(
            model_name='cloudserverorder',
            name='public_ip',
            field=models.CharField(blank=True, max_length=128, null=True, verbose_name='公网IP'),
        ),
        migrations.AddField(
            model_name='cloudserverorder',
            name='login_user',
            field=models.CharField(blank=True, max_length=64, null=True, verbose_name='登录账号'),
        ),
        migrations.AddField(
            model_name='cloudserverorder',
            name='login_password',
            field=models.CharField(blank=True, max_length=191, null=True, verbose_name='登录密码'),
        ),
    ]
