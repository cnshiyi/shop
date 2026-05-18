from contextlib import nullcontext

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.crypto import (
    SecretDecryptionError,
    allow_legacy_secret_key_decryption,
    decrypt_text,
    encrypt_text,
)
from core.models import CloudAccountConfig, SiteConfig
from mall.models import CloudAsset, CloudServerOrder, Server


class Command(BaseCommand):
    help = 'Re-encrypt sensitive database fields with the active CONFIG_ENCRYPTION_KEY.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--write',
            action='store_true',
            help='Persist the re-encrypted values. Without this flag the command only reports what would change.',
        )
        parser.add_argument(
            '--allow-legacy-secret-key',
            action='store_true',
            help='Temporarily allow decrypting old Fernet values that were derived from SECRET_KEY.',
        )

    def handle(self, *args, **options):
        write = bool(options['write'])
        allow_legacy = bool(options['allow_legacy_secret_key'])
        context = allow_legacy_secret_key_decryption() if allow_legacy else nullcontext()

        with context:
            entries, errors = self._collect_entries()

        if errors:
            for label, error in errors:
                self.stderr.write(f'{label}: {error}')
            raise CommandError(
                '存在无法解密的敏感字段。请确认 CONFIG_ENCRYPTION_KEY，'
                '如需迁移旧 SECRET_KEY 密文请加 --allow-legacy-secret-key。'
            )

        if not write:
            self.stdout.write(
                self.style.WARNING(
                    f'DRY RUN: {len(entries)} 个敏感字段可重加密。加 --write 才会写入数据库。'
                )
            )
            return

        encrypted_probe = encrypt_text('__probe__')
        if not encrypted_probe.startswith('gAAAA'):
            raise CommandError('CONFIG_ENCRYPTION_KEY 未能生成有效密文。')

        with transaction.atomic():
            for obj, field_name, plain_value in entries:
                setattr(obj, field_name, encrypt_text(plain_value))
                obj.save(update_fields=[field_name])

        self.stdout.write(self.style.SUCCESS(f'已重加密 {len(entries)} 个敏感字段。'))

    def _collect_entries(self):
        entries = []
        errors = []
        for obj, field_name, label in self._iter_secret_fields():
            raw_value = getattr(obj, field_name, '') or ''
            if not raw_value:
                continue
            try:
                plain_value = decrypt_text(raw_value)
            except SecretDecryptionError as exc:
                errors.append((label, exc))
                continue
            if plain_value:
                entries.append((obj, field_name, plain_value))
        return entries, errors

    def _iter_secret_fields(self):
        for item in SiteConfig.objects.filter(is_sensitive=True).order_by('id'):
            yield item, 'value', f'SiteConfig#{item.id}:{item.key}'

        for item in CloudAccountConfig.objects.order_by('id'):
            yield item, 'access_key', f'CloudAccountConfig#{item.id}:access_key'
            yield item, 'secret_key', f'CloudAccountConfig#{item.id}:secret_key'

        for item in CloudServerOrder.objects.exclude(login_password__isnull=True).exclude(login_password='').order_by('id'):
            yield item, 'login_password', f'CloudServerOrder#{item.id}:login_password'

        for item in CloudAsset.objects.exclude(login_password__isnull=True).exclude(login_password='').order_by('id'):
            yield item, 'login_password', f'CloudAsset#{item.id}:login_password'

        for item in Server.objects.exclude(login_password__isnull=True).exclude(login_password='').order_by('id'):
            yield item, 'login_password', f'Server#{item.id}:login_password'
