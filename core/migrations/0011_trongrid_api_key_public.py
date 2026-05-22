import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from django.db import migrations

from core.crypto import decrypt_text


def _decrypt_or_blank(value):
    if not value:
        return ''
    try:
        plain_value = decrypt_text(value)
    except Exception:
        plain_value = value
    if not str(plain_value).startswith('gAAAA'):
        return plain_value
    for raw_secret in (
        os.getenv('CONFIG_ENCRYPTION_KEY') or '',
        os.getenv('SECRET_KEY') or 'dev-secret-key-change-me',
    ):
        if not raw_secret:
            continue
        try:
            key = base64.urlsafe_b64encode(hashlib.sha256(raw_secret.encode('utf-8')).digest())
            return Fernet(key).decrypt(str(value).encode('utf-8')).decode('utf-8')
        except InvalidToken:
            continue
    return ''


def mark_trongrid_api_key_public(apps, schema_editor):
    SiteConfig = apps.get_model('core', 'SiteConfig')
    for item in SiteConfig.objects.filter(key='trongrid_api_key'):
        item.value = _decrypt_or_blank(item.value or '')
        item.is_sensitive = False
        item.save(update_fields=['value', 'is_sensitive'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_bot_notice_copy_chat_ids'),
    ]

    operations = [
        migrations.RunPython(mark_trongrid_api_key_public, migrations.RunPython.noop),
    ]
