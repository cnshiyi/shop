from django.db import migrations


def encrypt_login_sessions(apps, schema_editor):
    from core.crypto import encrypt_text

    TelegramLoginAccount = apps.get_model('bot', 'TelegramLoginAccount')
    for item in TelegramLoginAccount.objects.all():
        changed = []
        if item.phone_code_hash and not str(item.phone_code_hash).startswith('gAAAA'):
            item.phone_code_hash = encrypt_text(item.phone_code_hash)
            changed.append('phone_code_hash')
        if item.session_string and not str(item.session_string).startswith('gAAAA'):
            item.session_string = encrypt_text(item.session_string)
            changed.append('session_string')
        if changed:
            item.save(update_fields=changed)


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0007_bot_operation_log'),
    ]

    operations = [
        migrations.RunPython(encrypt_login_sessions, migrations.RunPython.noop),
    ]
