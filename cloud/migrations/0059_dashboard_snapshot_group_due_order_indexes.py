from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0058_dashboard_snapshot_list_page_index'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='cloudassetdashboardsnapshot',
            index=models.Index(
                fields=['is_display_visible', 'asset_due_sort_null_rank', 'asset_due_sort_at', 'group_user_label', 'group_user_key'],
                name='cad_vis_user_due_ord_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='cloudassetdashboardsnapshot',
            index=models.Index(
                fields=['is_display_visible', 'asset_due_sort_null_rank', 'asset_due_sort_at', 'group_telegram_label', 'group_telegram_key'],
                name='cad_vis_tg_due_ord_idx',
            ),
        ),
    ]
