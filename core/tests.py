import json
import importlib
import os
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.apps import apps as django_apps
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import migrations
from django.test import override_settings

from cloud.server_records import Server
from core.cloud_accounts import cloud_account_label_variants, list_cloud_accounts_by_server_load
from core.crypto import decrypt_text, encrypt_text
from core.models import CloudAccountConfig
from core.models import SiteConfig
from core.persistence import record_external_sync_log


class MySqlSqlModeSettingsTestCase(SimpleTestCase):
    def test_mysql_sql_mode_defaults_to_strict_trans_tables(self):
        with patch.dict(os.environ, {}, clear=True):
            from shop.settings import _mysql_sql_mode

            self.assertEqual(_mysql_sql_mode(), 'STRICT_TRANS_TABLES')

    def test_mysql_sql_mode_normalizes_and_deduplicates_values(self):
        with patch.dict(os.environ, {'MYSQL_SQL_MODE': ' strict_trans_tables,ansi,STRICT_TRANS_TABLES '}, clear=False):
            from shop.settings import _mysql_sql_mode

            self.assertEqual(_mysql_sql_mode(), 'STRICT_TRANS_TABLES,ANSI')

    def test_mysql_sql_mode_can_be_disabled(self):
        with patch.dict(os.environ, {'MYSQL_SQL_MODE': ' '}, clear=False):
            from shop.settings import _mysql_sql_mode

            self.assertEqual(_mysql_sql_mode(), '')

    def test_mysql_sql_mode_rejects_unsafe_characters(self):
        with patch.dict(os.environ, {'MYSQL_SQL_MODE': "STRICT_TRANS_TABLES';DROP"}, clear=False):
            from django.core.exceptions import ImproperlyConfigured
            from shop.settings import _mysql_sql_mode

            with self.assertRaises(ImproperlyConfigured):
                _mysql_sql_mode()


class MySqlTimeoutSettingsTestCase(SimpleTestCase):
    def test_mysql_timeout_options_default_to_ten_seconds(self):
        with patch.dict(os.environ, {}, clear=True):
            from shop.settings import _mysql_timeout_options

            self.assertEqual(
                _mysql_timeout_options(),
                {
                    'connect_timeout': 10,
                    'read_timeout': 10,
                    'write_timeout': 10,
                },
            )

    def test_mysql_timeout_options_read_custom_env_values(self):
        with patch.dict(
            os.environ,
            {
                'MYSQL_CONNECT_TIMEOUT': '3',
                'MYSQL_READ_TIMEOUT': '7',
                'MYSQL_WRITE_TIMEOUT': '11',
            },
            clear=False,
        ):
            from shop.settings import _mysql_timeout_options

            self.assertEqual(
                _mysql_timeout_options(),
                {
                    'connect_timeout': 3,
                    'read_timeout': 7,
                    'write_timeout': 11,
                },
            )

    def test_mysql_timeout_options_can_be_disabled(self):
        with patch.dict(
            os.environ,
            {
                'MYSQL_CONNECT_TIMEOUT': '0',
                'MYSQL_READ_TIMEOUT': '-1',
                'MYSQL_WRITE_TIMEOUT': '0',
            },
            clear=False,
        ):
            from shop.settings import _mysql_timeout_options

            self.assertEqual(_mysql_timeout_options(), {})

    def test_mysql_timeout_options_invalid_values_fall_back_to_defaults(self):
        with patch.dict(
            os.environ,
            {
                'MYSQL_CONNECT_TIMEOUT': 'abc',
                'MYSQL_READ_TIMEOUT': '',
                'MYSQL_WRITE_TIMEOUT': ' ',
            },
            clear=False,
        ):
            from shop.settings import _mysql_timeout_options

            self.assertEqual(
                _mysql_timeout_options(),
                {
                    'connect_timeout': 10,
                    'read_timeout': 10,
                    'write_timeout': 10,
                },
            )


class RedisCacheBackoffTestCase(SimpleTestCase):
    def tearDown(self):
        from core import cache

        async_to_sync(cache.close)()

    def test_get_redis_skips_reconnect_during_failure_backoff(self):
        from core import cache

        class FailingRedis:
            async def ping(self):
                raise OSError('redis down')

        async_to_sync(cache.close)()
        with (
            patch.dict(os.environ, {'REDIS_RETRY_INTERVAL_SECONDS': '30'}, clear=False),
            patch('core.cache.redis.from_url', return_value=FailingRedis()) as from_url,
            patch('core.cache._redis_retry_now', side_effect=[100.0, 101.0]),
        ):
            first = async_to_sync(cache.get_redis)()
            second = async_to_sync(cache.get_redis)()

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(from_url.call_count, 1)


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


class SiteConfigCacheTestCase(TransactionTestCase):
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

    def test_set_invalidates_async_config_cache(self):
        from core.cache import cache_config_value, get_cached_config_value, get_config

        cache_config_value('cache_invalidate_test', 'old')

        SiteConfig.set('cache_invalidate_test', 'new')

        self.assertEqual(get_cached_config_value('cache_invalidate_test', ''), '')
        self.assertEqual(async_to_sync(get_config)('cache_invalidate_test', ''), 'new')


class PortOverrideTextMigrationTestCase(TestCase):
    def test_port_override_text_migration_does_not_restore_removed_copy(self):
        migration = importlib.import_module('core.migrations.0012_remove_user_port_override_texts')
        reinstall_values = migration.TEXT_UPDATES['bot_reinstall_need_main_link']
        retained_values = migration.TEXT_UPDATES['bot_retained_ip_renewal_link_prompt']
        custom_value = '自定义保留文案：不要自动覆盖'

        SiteConfig.objects.create(key='bot_reinstall_need_main_link', value=reinstall_values['old'])
        SiteConfig.objects.create(key='bot_retained_ip_renewal_link_prompt', value=custom_value)

        migration.update_port_override_texts(django_apps, None)

        self.assertEqual(SiteConfig.objects.get(key='bot_reinstall_need_main_link').value, reinstall_values['new'])
        self.assertEqual(SiteConfig.objects.get(key='bot_retained_ip_renewal_link_prompt').value, custom_value)
        self.assertNotIn('以你发送的主链接端口为准', reinstall_values['new'])
        self.assertNotIn('系统记录的主端口不对', retained_values['new'])
        self.assertIs(migration.Migration.operations[0].reverse_code, migrations.RunPython.noop)


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
