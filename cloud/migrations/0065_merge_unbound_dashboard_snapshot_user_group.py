from django.db import migrations


def merge_unbound_user_group(apps, schema_editor):
    CloudAssetDashboardSnapshot = apps.get_model('cloud', 'CloudAssetDashboardSnapshot')
    CloudAssetDashboardSnapshot.objects.filter(
        user_id__isnull=True,
        tg_user_id__isnull=True,
    ).exclude(group_user_key='user:unbound').update(
        group_user_key='user:unbound',
        group_user_label='未绑定用户',
    )
    CloudAssetDashboardSnapshot.objects.filter(
        user_id__isnull=True,
        tg_user_id__isnull=True,
        telegram_group_id__isnull=True,
    ).exclude(group_telegram_key='user:unbound').update(
        group_telegram_key='user:unbound',
        group_telegram_label='未绑定用户',
    )


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0064_cloudasset_shutdown_default_off'),
    ]

    operations = [
        migrations.RunPython(merge_unbound_user_group, noop_reverse),
    ]
