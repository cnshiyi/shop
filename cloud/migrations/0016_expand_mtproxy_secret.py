from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0015_cloud_account_binding'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cloudasset',
            name='mtproxy_secret',
            field=models.CharField(blank=True, max_length=191, null=True, verbose_name='MTProxy密钥'),
        ),
        migrations.AlterField(
            model_name='cloudserverorder',
            name='mtproxy_secret',
            field=models.CharField(blank=True, max_length=191, null=True, verbose_name='MTProxy密钥'),
        ),
    ]
