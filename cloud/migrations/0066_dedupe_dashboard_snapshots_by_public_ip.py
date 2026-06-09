from django.db import migrations


def _snapshot_rank(row):
    status = row.get('status') or ''
    asset_updated_at = row.get('asset_updated_at')
    return (
        3 if row.get('risk_unattached_ip') else 0,
        2 if status == 'deleting' else 0,
        1 if status not in {'deleted', 'terminated'} else 0,
        1 if row.get('user_id') else 0,
        asset_updated_at.timestamp() if asset_updated_at else 0,
        row.get('asset_id') or 0,
    )


def dedupe_dashboard_snapshots_by_public_ip(apps, schema_editor):
    CloudAssetDashboardSnapshot = apps.get_model('cloud', 'CloudAssetDashboardSnapshot')
    fields = ['id', 'asset_id', 'public_ip', 'status', 'risk_unattached_ip', 'user_id', 'asset_updated_at']
    rows = (
        CloudAssetDashboardSnapshot.objects
        .exclude(public_ip__isnull=True)
        .exclude(public_ip='')
        .order_by('public_ip', 'id')
        .values(*fields)
        .iterator(chunk_size=5000)
    )
    delete_ids = []
    current_ip = None
    current_rows = []

    def flush_group(group):
        if len(group) <= 1:
            return
        keep = max(group, key=_snapshot_rank)
        keep_id = keep['id']
        delete_ids.extend(row['id'] for row in group if row['id'] != keep_id)

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

    for index in range(0, len(delete_ids), 2000):
        CloudAssetDashboardSnapshot.objects.filter(id__in=delete_ids[index:index + 2000]).delete()


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0065_merge_unbound_dashboard_snapshot_user_group'),
    ]

    operations = [
        migrations.RunPython(dedupe_dashboard_snapshots_by_public_ip, noop_reverse),
    ]
