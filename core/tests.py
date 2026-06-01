import json
import os
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from cloud.server_records import Server
from core.cloud_accounts import cloud_account_label_variants, list_cloud_accounts_by_server_load
from core.crypto import decrypt_text, encrypt_text
from core.models import CloudAccountConfig
from core.models import SiteConfig
from core.persistence import record_external_sync_log


class CryptoDecryptTestCase(SimpleTestCase):
    def test_plain_legacy_value_still_returns_as_plaintext(self):
        self.assertEqual(decrypt_text('legacy-plain-value'), 'legacy-plain-value')

    def test_invalid_fernet_like_token_does_not_fallback_to_ciphertext(self):
        with patch.dict(os.environ, {'CONFIG_ENCRYPTION_KEY': 'first-key'}, clear=False):
            encrypted = encrypt_text('secret-value')
        with patch.dict(os.environ, {'CONFIG_ENCRYPTION_KEY': 'second-key'}, clear=False):
            with self.assertLogs('core.crypto', level='WARNING') as logs:
                decrypted = decrypt_text(encrypted)

        self.assertEqual(decrypted, '')
        self.assertIn('CONFIG_DECRYPT_INVALID_TOKEN', '\n'.join(logs.output))


class SiteConfigCacheTestCase(TestCase):
    def tearDown(self):
        SiteConfig.clear_cache()

    def test_get_refreshes_after_cache_ttl_expires(self):
        SiteConfig.objects.create(key='cache_ttl_test', value='old')
        self.assertEqual(SiteConfig.get('cache_ttl_test'), 'old')

        SiteConfig.objects.filter(key='cache_ttl_test').update(value='new')
        original_ttl = SiteConfig._CACHE_TTL_SECONDS
        SiteConfig._CACHE_TTL_SECONDS = -1
        try:
            self.assertEqual(SiteConfig.get('cache_ttl_test'), 'new')
        finally:
            SiteConfig._CACHE_TTL_SECONDS = original_ttl


class ExternalSyncLogSanitizeTestCase(TestCase):
    def test_record_external_sync_log_masks_sensitive_payload_fields(self):
        log = record_external_sync_log(
            source='dashboard',
            action='sync',
            request_payload={
                'access_key': 'AKIA_REAL_VALUE',
                'nested': {
                    'secret_key': 'SECRET_REAL_VALUE',
                    'items': [{'login_password': 'root-password'}],
                },
                'public_ip': '1.2.3.4',
            },
            response_payload='{"mtproxy_secret": "abcdef", "ok": true}',
            is_success=False,
            error_message='Authorization: Bearer abc123; password=root-password',
        )

        request_payload = json.loads(log.request_payload)
        response_payload = json.loads(log.response_payload)
        self.assertEqual(request_payload['access_key'], '***')
        self.assertEqual(request_payload['nested']['secret_key'], '***')
        self.assertEqual(request_payload['nested']['items'][0]['login_password'], '***')
        self.assertEqual(request_payload['public_ip'], '1.2.3.4')
        self.assertEqual(response_payload['mtproxy_secret'], '***')
        self.assertNotIn('root-password', log.error_message)
        self.assertNotIn('abc123', log.error_message)
        self.assertNotIn('Bearer', log.error_message)


class EnsureDashboardAdminCommandTestCase(TestCase):
    def test_existing_admin_password_is_not_reset_without_env_password(self):
        User = get_user_model()
        user = User.objects.create_user(
            username='admin',
            password='StrongExistingPass123!',
            is_staff=True,
            is_superuser=True,
        )

        with override_settings(), patch.dict(os.environ, {'DASHBOARD_ADMIN_USERNAME': 'admin'}, clear=False):
            os.environ.pop('DASHBOARD_ADMIN_PASSWORD', None)
            call_command('ensure_dashboard_admin', stdout=None)

        user.refresh_from_db()
        self.assertTrue(user.check_password('StrongExistingPass123!'))
        self.assertFalse(user.check_password('Admin@123456'))

    def test_new_admin_requires_env_password_when_debug_false(self):
        with override_settings(DEBUG=False), patch.dict(os.environ, {'DASHBOARD_ADMIN_USERNAME': 'admin'}, clear=False):
            os.environ.pop('DASHBOARD_ADMIN_PASSWORD', None)
            with self.assertRaises(CommandError):
                call_command('ensure_dashboard_admin', stdout=None)


class CloudAccountSelectionTestCase(TestCase):
    def test_aws_label_variants_include_lightsail_alias_for_historical_rows(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='main',
            external_account_id='123456789012',
            access_key='ak',
            secret_key='sk',
        )

        labels = cloud_account_label_variants(account)

        self.assertIn('aws+123456789012+main', labels)
        self.assertIn('aws_lightsail+123456789012+main', labels)

    def test_server_load_counts_historical_aws_lightsail_account_labels(self):
        first = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='first',
            external_account_id='111',
            access_key='ak1',
            secret_key='sk1',
        )
        second = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='second',
            external_account_id='222',
            access_key='ak2',
            secret_key='sk2',
        )
        Server.objects.create(provider='aws_lightsail', account_label='aws_lightsail+111+first', public_ip='10.0.0.1')
        Server.objects.create(provider='aws_lightsail', account_label='aws_lightsail+111+first', public_ip='10.0.0.2')

        accounts = list_cloud_accounts_by_server_load('aws_lightsail')

        self.assertEqual([account.id for account in accounts], [second.id, first.id])
