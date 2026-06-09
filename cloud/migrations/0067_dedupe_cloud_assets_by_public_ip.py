from django.db import migrations, models


def _asset_rank(row):
    status = row.get('status') or ''
    provider_status = row.get('provider_status') or ''
    updated_at = row.get('updated_at')
    is_unattached = '未附加' in provider_status or '固定IP仍存在但未附加' in provider_status
    return (
        1 if row.get('kind') == 'server' else 0,
        3 if is_unattached else 0,
        2 if status == 'deleting' else 0,
        1 if status not in {'deleted', 'terminated'} else 0,
        1 if row.get('order_id') else 0,
        1 if row.get('user_id') else 0,
        updated_at.timestamp() if updated_at else 0,
        row.get('id') or 0,
    )


def _first_value(keeper, duplicates, field):
    value = keeper.get(field)
    if value not in (None, '', []):
        return value
    for duplicate in duplicates:
        value = duplicate.get(field)
        if value not in (None, '', []):
            return value
    return keeper.get(field)


def _merge_group(apps, group):
    CloudAsset = apps.get_model('cloud', 'CloudAsset')
    CloudAssetDashboardSnapshot = apps.get_model('cloud', 'CloudAssetDashboardSnapshot')
    CloudIpLog = apps.get_model('cloud', 'CloudIpLog')
    CloudLifecyclePlanNote = apps.get_model('cloud', 'CloudLifecyclePlanNote')
    CloudLifecycleTask = apps.get_model('cloud', 'CloudLifecycleTask')
    CloudNoticeTask = apps.get_model('cloud', 'CloudNoticeTask')

    ordered = sorted(group, key=_asset_rank, reverse=True)
    keeper = ordered[0]
    duplicates = ordered[1:]
    duplicate_ids = [row['id'] for row in duplicates]
    update_fields = [
        'provider',
        'cloud_account_id',
        'account_label',
        'region_code',
        'region_name',
        'asset_name',
        'instance_id',
        'provider_resource_id',
        'previous_public_ip',
        'login_user',
        'login_password',
        'mtproxy_port',
        'mtproxy_link',
        'proxy_links',
        'mtproxy_secret',
        'mtproxy_host',
        'actual_expires_at',
        'price',
        'currency',
        'order_id',
        'user_id',
        'telegram_group_id',
        'note',
        'provider_status',
    ]
    updates = {}
    for field in update_fields:
        value = _first_value(keeper, duplicates, field)
        if value not in (None, '', []) and value != keeper.get(field):
            updates[field] = value
    if any(row.get('shutdown_enabled') for row in duplicates) and not keeper.get('shutdown_enabled'):
        updates['shutdown_enabled'] = True
    if any(row.get('server_delete_enabled') for row in duplicates) and not keeper.get('server_delete_enabled'):
        updates['server_delete_enabled'] = True
    if any(row.get('ip_delete_enabled') for row in duplicates) and not keeper.get('ip_delete_enabled'):
        updates['ip_delete_enabled'] = True
    if updates:
        CloudAsset.objects.filter(id=keeper['id']).update(**updates)

    CloudIpLog.objects.filter(asset_id__in=duplicate_ids).update(asset_id=keeper['id'])
    CloudLifecyclePlanNote.objects.filter(asset_id__in=duplicate_ids).update(asset_id=keeper['id'])
    CloudLifecycleTask.objects.filter(asset_id__in=duplicate_ids).update(asset_id=keeper['id'])
    CloudNoticeTask.objects.filter(asset_id__in=duplicate_ids).update(asset_id=keeper['id'])
    CloudAssetDashboardSnapshot.objects.filter(asset_id__in=duplicate_ids).delete()
    CloudAsset.objects.filter(id__in=duplicate_ids).delete()


def dedupe_cloud_assets_by_public_ip(apps, schema_editor):
    CloudAsset = apps.get_model('cloud', 'CloudAsset')
    fields = [
        'id',
        'kind',
        'public_ip',
        'provider',
        'cloud_account_id',
        'account_label',
        'region_code',
        'region_name',
        'asset_name',
        'instance_id',
        'provider_resource_id',
        'previous_public_ip',
        'login_user',
        'login_password',
        'mtproxy_port',
        'mtproxy_link',
        'proxy_links',
        'mtproxy_secret',
        'mtproxy_host',
        'actual_expires_at',
        'price',
        'currency',
        'order_id',
        'user_id',
        'telegram_group_id',
        'note',
        'provider_status',
        'status',
        'shutdown_enabled',
        'server_delete_enabled',
        'ip_delete_enabled',
        'updated_at',
    ]
    rows = (
        CloudAsset.objects
        .exclude(public_ip__isnull=True)
        .exclude(public_ip='')
        .order_by('public_ip', 'id')
        .values(*fields)
        .iterator(chunk_size=5000)
    )
    current_ip = None
    current_rows = []

    def flush_group(group):
        if len(group) > 1:
            _merge_group(apps, group)

    for row in rows:
        public_ip = row['public_ip']
        if current_ip is None:
            current_ip = public_ip
        if public_ip != current_ip:
            flush_group(current_rows)
            current_ip = public_ip
            current_rows = []
        current_rows.append(row)
    flush_group(current_rows)


def normalize_blank_cloud_asset_public_ip(apps, schema_editor):
    CloudAsset = apps.get_model('cloud', 'CloudAsset')
    CloudAsset.objects.filter(public_ip='').update(public_ip=None)
    queryset = (
        CloudAsset.objects
        .exclude(public_ip__isnull=True)
        .exclude(public_ip='')
        .values('id', 'public_ip')
        .iterator(chunk_size=5000)
    )
    for row in queryset:
        normalized_public_ip = str(row['public_ip'] or '').strip() or None
        if normalized_public_ip != row['public_ip']:
            CloudAsset.objects.filter(id=row['id']).update(public_ip=normalized_public_ip)


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0066_dedupe_dashboard_snapshots_by_public_ip'),
    ]

    operations = [
        migrations.RunPython(normalize_blank_cloud_asset_public_ip, noop_reverse),
        migrations.RunPython(dedupe_cloud_assets_by_public_ip, noop_reverse),
        migrations.AddConstraint(
            model_name='cloudasset',
            constraint=models.UniqueConstraint(fields=('public_ip',), name='uniq_cloud_asset_public_ip'),
        ),
    ]
