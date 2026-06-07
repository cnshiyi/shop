from django.db import migrations, models


def backfill_asset_due_sort_null_rank(apps, schema_editor):
    CloudAssetDashboardSnapshot = apps.get_model('cloud', 'CloudAssetDashboardSnapshot')
    CloudAssetDashboardSnapshot.objects.filter(asset_due_sort_at__isnull=False).update(asset_due_sort_null_rank=0)
    CloudAssetDashboardSnapshot.objects.filter(asset_due_sort_at__isnull=True).update(asset_due_sort_null_rank=1)


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0057_lifecycle_plan_pagination_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudassetdashboardsnapshot',
            name='asset_due_sort_null_rank',
            field=models.PositiveSmallIntegerField(
                db_comment='仅用于后台列表排序，0=有到期时间，1=无到期时间，不作为资产到期事实',
                default=1,
                verbose_name='资产到期空值排序',
            ),
        ),
        migrations.RunPython(backfill_asset_due_sort_null_rank, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name='cloudassetdashboardsnapshot',
            index=models.Index(
                fields=['is_display_visible', 'risk_rank', 'asset_due_sort_null_rank', 'asset_due_sort_at', '-sort_order', '-asset_id'],
                name='cad_vis_list_page_idx',
            ),
        ),
    ]
