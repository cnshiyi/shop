from django.db import migrations, models


def copy_shutdown_switch(apps, schema_editor):
    CloudAsset = apps.get_model('cloud', 'CloudAsset')
    CloudAsset.objects.filter(shutdown_enabled=False).update(
        server_delete_enabled=False,
        ip_delete_enabled=False,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0053_cloud_asset_dashboard_snapshot_display_visible'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudasset',
            name='server_delete_enabled',
            field=models.BooleanField(db_comment='服务器删除计划启用', db_index=True, default=True, verbose_name='服务器删除计划启用'),
        ),
        migrations.AddField(
            model_name='cloudasset',
            name='ip_delete_enabled',
            field=models.BooleanField(db_comment='IP删除计划启用', db_index=True, default=True, verbose_name='IP删除计划启用'),
        ),
        migrations.RunPython(copy_shutdown_switch, migrations.RunPython.noop),
    ]
