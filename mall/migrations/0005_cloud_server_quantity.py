from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0004_cloud_server_lifecycle'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverorder',
            name='quantity',
            field=models.IntegerField(default=1, verbose_name='购买数量'),
        ),
    ]
