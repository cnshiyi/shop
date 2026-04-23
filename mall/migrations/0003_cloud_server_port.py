from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('mall', '0002_cloud_server_credentials'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverorder',
            name='mtproxy_port',
            field=models.IntegerField(default=9528, verbose_name='MTProxy端口'),
        ),
    ]
