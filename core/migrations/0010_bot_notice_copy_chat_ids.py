from django.db import migrations


def add_bot_notice_copy_chat_ids(apps, schema_editor):
    SiteConfig = apps.get_model('core', 'SiteConfig')
    admin_item = SiteConfig.objects.filter(key='bot_admin_chat_id').first()
    existing = SiteConfig.objects.filter(key='bot_notice_copy_chat_ids').first()
    if existing:
        return
    sort_order = 5
    if admin_item and getattr(admin_item, 'sort_order', None):
        sort_order = int(admin_item.sort_order) + 1
    SiteConfig.objects.create(
        key='bot_notice_copy_chat_ids',
        value=(getattr(admin_item, 'value', '') or '') if admin_item else '',
        is_sensitive=False,
        sort_order=sort_order,
    )


def remove_bot_notice_copy_chat_ids(apps, schema_editor):
    SiteConfig = apps.get_model('core', 'SiteConfig')
    SiteConfig.objects.filter(key='bot_notice_copy_chat_ids').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0009_cloud_asset_sync_interval_ten_minutes'),
    ]

    operations = [
        migrations.RunPython(add_bot_notice_copy_chat_ids, remove_bot_notice_copy_chat_ids),
    ]
