from django.db import migrations, models
import django.db.models.deletion


def _table_exists(schema_editor, table_name: str) -> bool:
    return table_name in schema_editor.connection.introspection.table_names()


def migrate_server_rows_to_assets(apps, schema_editor):
    if not _table_exists(schema_editor, 'cloud_server'):
        return
    CloudAsset = apps.get_model('cloud', 'CloudAsset')
    Server = apps.get_model('cloud', 'Server')
    CloudIpLog = apps.get_model('cloud', 'CloudIpLog')

    for server in Server.objects.using(schema_editor.connection.alias).all().iterator():
        identity = models.Q()
        if getattr(server, 'order_id', None):
            identity |= models.Q(order_id=server.order_id)
        if getattr(server, 'instance_id', None):
            identity |= models.Q(instance_id=server.instance_id)
        if getattr(server, 'provider_resource_id', None):
            identity |= models.Q(provider_resource_id=server.provider_resource_id)
        if getattr(server, 'public_ip', None):
            identity |= models.Q(public_ip=server.public_ip) | models.Q(previous_public_ip=server.public_ip)
        if getattr(server, 'previous_public_ip', None):
            identity |= models.Q(public_ip=server.previous_public_ip) | models.Q(previous_public_ip=server.previous_public_ip)
        asset = None
        if identity:
            asset = (
                CloudAsset.objects.using(schema_editor.connection.alias)
                .filter(identity, kind='server')
                .order_by('-updated_at', '-id')
                .first()
            )
        if not asset:
            asset = CloudAsset.objects.using(schema_editor.connection.alias).create(
                kind='server',
                source=getattr(server, 'source', None) or 'order',
                provider=getattr(server, 'provider', None),
                account_label=getattr(server, 'account_label', None),
                region_code=getattr(server, 'region_code', None),
                region_name=getattr(server, 'region_name', None),
                asset_name=getattr(server, 'server_name', None),
                instance_id=getattr(server, 'instance_id', None),
                provider_resource_id=getattr(server, 'provider_resource_id', None),
                public_ip=getattr(server, 'public_ip', None),
                previous_public_ip=getattr(server, 'previous_public_ip', None),
                login_user=getattr(server, 'login_user', None),
                login_password=getattr(server, 'login_password', None),
                actual_expires_at=getattr(server, 'expires_at', None),
                order_id=getattr(server, 'order_id', None),
                user_id=getattr(server, 'user_id', None),
                note=getattr(server, 'note', None),
                sort_order=getattr(server, 'sort_order', 99) or 99,
                status=getattr(server, 'status', None) or 'unknown',
                provider_status=getattr(server, 'provider_status', None),
                is_active=bool(getattr(server, 'is_active', True)),
            )
        CloudIpLog.objects.using(schema_editor.connection.alias).filter(server_id=server.id).update(server_id=asset.id)


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0036_daily_address_stat_account_key_constraint'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='server',
            options={
                'managed': False,
                'ordering': ['expires_at', '-updated_at', '-id'],
                'verbose_name': '服务器',
                'verbose_name_plural': '服务器',
            },
        ),
        migrations.AlterField(
            model_name='cloudiplog',
            name='server',
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='ip_logs',
                to='cloud.server',
                verbose_name='关联服务器',
            ),
        ),
        migrations.RunPython(migrate_server_rows_to_assets, migrations.RunPython.noop),
        migrations.RunSQL('DROP TABLE IF EXISTS cloud_server', reverse_sql=migrations.RunSQL.noop),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterModelTable(
                    name='server',
                    table='cloud_asset',
                ),
            ],
        ),
    ]
