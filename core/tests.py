import os
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.test import override_settings

from core.cloud_accounts import get_active_cloud_account
from core.crypto import SecretDecryptionError, decrypt_text, encrypt_text
from core.models import CloudAccountConfig, SiteConfig


class CloudAccountSelectionTests(TestCase):
    def test_active_account_selection_excludes_error_accounts(self):
        error_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='bad',
            access_key='BADKEY',
            secret_key='BADSECRET',
            status=CloudAccountConfig.STATUS_ERROR,
            is_active=True,
        )
        usable_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='unknown',
            access_key='OKKEY',
            secret_key='OKSECRET',
            status=CloudAccountConfig.STATUS_UNKNOWN,
            is_active=True,
        )

        self.assertEqual(get_active_cloud_account('aws'), usable_account)
        self.assertNotEqual(get_active_cloud_account('aws'), error_account)

    def test_active_account_selection_prefers_ok_and_region_hint(self):
        CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='unknown-sg',
            access_key='UNKNOWNKEY',
            secret_key='UNKNOWNSECRET',
            status=CloudAccountConfig.STATUS_UNKNOWN,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        ok_hk = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='ok-hk',
            access_key='OKHKKEY',
            secret_key='OKHKSECRET',
            status=CloudAccountConfig.STATUS_OK,
            region_hint='ap-east-1',
            is_active=True,
        )
        ok_sg = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='ok-sg',
            access_key='OKSGKEY',
            secret_key='OKSGSECRET',
            status=CloudAccountConfig.STATUS_OK,
            region_hint='ap-southeast-1',
            is_active=True,
        )

        self.assertEqual(get_active_cloud_account('aws'), ok_hk)
        self.assertEqual(get_active_cloud_account('aws', 'ap-southeast-1'), ok_sg)


class CryptoConfigTests(TestCase):
    def test_encrypt_requires_config_encryption_key_outside_sqlite_tests(self):
        old_key = os.environ.pop('CONFIG_ENCRYPTION_KEY', None)
        old_test_flag = os.environ.get('DJANGO_TEST_SQLITE')
        old_debug = os.environ.get('DEBUG')
        os.environ['DJANGO_TEST_SQLITE'] = '0'
        os.environ['DEBUG'] = '0'
        try:
            with self.assertRaises(ImproperlyConfigured):
                encrypt_text('secret')
        finally:
            if old_key is not None:
                os.environ['CONFIG_ENCRYPTION_KEY'] = old_key
            if old_test_flag is None:
                os.environ.pop('DJANGO_TEST_SQLITE', None)
            else:
                os.environ['DJANGO_TEST_SQLITE'] = old_test_flag
            if old_debug is None:
                os.environ.pop('DEBUG', None)
            else:
                os.environ['DEBUG'] = old_debug

    def test_decrypt_keeps_legacy_plaintext_but_rejects_bad_fernet_payload(self):
        self.assertEqual(decrypt_text('legacy-plain-secret'), 'legacy-plain-secret')

        with self.assertRaises(SecretDecryptionError):
            decrypt_text('gAAAA-invalid-token')

    def test_reencrypt_secrets_rewrites_legacy_secret_key_values(self):
        old_config_key = os.environ.get('CONFIG_ENCRYPTION_KEY')
        old_secret_key = os.environ.get('SECRET_KEY')
        old_debug = os.environ.get('DEBUG')
        os.environ['SECRET_KEY'] = 'legacy-secret-key'
        os.environ['CONFIG_ENCRYPTION_KEY'] = 'active-config-encryption-key'
        os.environ['DEBUG'] = '0'
        try:
            os.environ['CONFIG_ENCRYPTION_KEY'] = 'legacy-secret-key'
            legacy_value = encrypt_text('legacy-token')
            os.environ['CONFIG_ENCRYPTION_KEY'] = 'active-config-encryption-key'
            config = SiteConfig.objects.create(
                key='bot_token',
                value=legacy_value,
                is_sensitive=True,
            )

            with self.assertRaises(SecretDecryptionError):
                decrypt_text(config.value)

            call_command(
                'reencrypt_secrets',
                '--allow-legacy-secret-key',
                '--write',
                stdout=StringIO(),
            )
            config.refresh_from_db()

            self.assertEqual(decrypt_text(config.value), 'legacy-token')
            self.assertNotEqual(config.value, legacy_value)
        finally:
            if old_config_key is None:
                os.environ.pop('CONFIG_ENCRYPTION_KEY', None)
            else:
                os.environ['CONFIG_ENCRYPTION_KEY'] = old_config_key
            if old_secret_key is None:
                os.environ.pop('SECRET_KEY', None)
            else:
                os.environ['SECRET_KEY'] = old_secret_key
            if old_debug is None:
                os.environ.pop('DEBUG', None)
            else:
                os.environ['DEBUG'] = old_debug


class EnsureDashboardAdminCommandTests(TestCase):
    def test_existing_admin_password_is_not_reset_without_env_password(self):
        User = get_user_model()
        user = User.objects.create_superuser(username='admin', password='OriginalPass123!')
        old_password_hash = user.password
        old_password = os.environ.pop('DASHBOARD_ADMIN_PASSWORD', None)
        old_username = os.environ.get('DASHBOARD_ADMIN_USERNAME')
        os.environ['DASHBOARD_ADMIN_USERNAME'] = 'admin'
        try:
            out = StringIO()
            call_command('ensure_dashboard_admin', stdout=out)
            user.refresh_from_db()
            self.assertEqual(user.password, old_password_hash)
            self.assertTrue(user.check_password('OriginalPass123!'))
            self.assertIn('password was not changed', out.getvalue())
        finally:
            if old_password is not None:
                os.environ['DASHBOARD_ADMIN_PASSWORD'] = old_password
            if old_username is None:
                os.environ.pop('DASHBOARD_ADMIN_USERNAME', None)
            else:
                os.environ['DASHBOARD_ADMIN_USERNAME'] = old_username

    @override_settings(DEBUG=False)
    def test_requires_env_password_when_creating_admin_in_production(self):
        old_password = os.environ.pop('DASHBOARD_ADMIN_PASSWORD', None)
        old_username = os.environ.get('DASHBOARD_ADMIN_USERNAME')
        os.environ['DASHBOARD_ADMIN_USERNAME'] = 'fresh-admin'
        try:
            with self.assertRaisesMessage(CommandError, 'DASHBOARD_ADMIN_PASSWORD is required'):
                call_command('ensure_dashboard_admin', stdout=StringIO())
        finally:
            if old_password is not None:
                os.environ['DASHBOARD_ADMIN_PASSWORD'] = old_password
            if old_username is None:
                os.environ.pop('DASHBOARD_ADMIN_USERNAME', None)
            else:
                os.environ['DASHBOARD_ADMIN_USERNAME'] = old_username
