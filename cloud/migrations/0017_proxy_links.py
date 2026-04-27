# Generated manually for structured proxy links.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0016_expand_mtproxy_secret'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverorder',
            name='proxy_links',
            field=models.JSONField(blank=True, default=list, verbose_name='代理链路'),
        ),
        migrations.AddField(
            model_name='cloudasset',
            name='proxy_links',
            field=models.JSONField(blank=True, default=list, verbose_name='代理链路'),
        ),
    ]
