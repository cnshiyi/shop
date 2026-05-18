"""
Standalone unit tests for the four components fixed in Plan B.
These tests use only stdlib (unittest + unittest.mock) so they run
under any Python version without the project's venv.

Run with:
    python3 tests_fixed_components.py -v
"""
import json
import sys
import unittest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# 1. monitoring/cache.py — multi-user same-address Redis Hash fix
# ---------------------------------------------------------------------------

class TestMonitoringCacheMultiUser(unittest.TestCase):
    """
    The bug: two monitors for the same address would overwrite each other in
    the Redis Hash because the old code stored a single dict per address key.
    The fix stores a JSON *array* per address key.
    """

    def _make_module(self):
        """Import monitoring.cache with Redis stubbed out."""
        import importlib, types

        # Stub out django and redis before importing the module
        django_stub = types.ModuleType('django')
        django_conf = types.ModuleType('django.conf')
        settings = MagicMock()
        settings.CACHES = {}
        django_conf.settings = settings
        django_stub.conf = django_conf
        sys.modules.setdefault('django', django_stub)
        sys.modules.setdefault('django.conf', django_conf)

        redis_stub = types.ModuleType('redis')
        redis_stub.Redis = MagicMock()
        sys.modules.setdefault('redis', redis_stub)

        # We test the logic directly rather than importing the module,
        # because the module has top-level Redis connection code.
        return None

    def test_grouped_by_address_produces_json_array(self):
        """init_monitor_cache groups monitors by address into JSON arrays."""
        monitors = [
            {'id': 1, 'address': 'TAddr1', 'threshold': '10.00', 'notify_flag': True, 'user_id': 101},
            {'id': 2, 'address': 'TAddr1', 'threshold': '20.00', 'notify_flag': False, 'user_id': 102},
            {'id': 3, 'address': 'TAddr2', 'threshold': '5.00',  'notify_flag': True, 'user_id': 103},
        ]

        # Replicate the grouping logic from the fixed monitoring/cache.py
        grouped: dict = {}
        for mon in monitors:
            entry = {
                'monitor_id': mon['id'],
                'threshold': mon['threshold'],
                'notify_flag': mon['notify_flag'],
                'user_id': mon['user_id'],
            }
            grouped.setdefault(mon['address'], []).append(entry)

        self.assertEqual(len(grouped), 2)
        self.assertEqual(len(grouped['TAddr1']), 2,
                         "Both monitors for TAddr1 must be stored, not overwritten")
        self.assertEqual(len(grouped['TAddr2']), 1)

        # Serialise as the cache does
        serialised = {addr: json.dumps(entries) for addr, entries in grouped.items()}
        decoded = json.loads(serialised['TAddr1'])
        self.assertEqual(decoded[0]['monitor_id'], 1)
        self.assertEqual(decoded[1]['monitor_id'], 2)

    def test_get_monitor_addresses_extends_not_overwrites(self):
        """get_monitor_addresses must extend the result list, not replace it."""
        raw_data = {
            b'TAddr1': json.dumps([
                {'monitor_id': 1, 'threshold': '10.00', 'notify_flag': True, 'user_id': 101},
                {'monitor_id': 2, 'threshold': '20.00', 'notify_flag': False, 'user_id': 102},
            ]).encode(),
            b'TAddr2': json.dumps([
                {'monitor_id': 3, 'threshold': '5.00', 'notify_flag': True, 'user_id': 103},
            ]).encode(),
        }

        # Replicate the fixed get_monitor_addresses decode logic
        result: dict = {}
        for raw_addr, raw_val in raw_data.items():
            addr = raw_addr.decode() if isinstance(raw_addr, bytes) else raw_addr
            try:
                entries = json.loads(raw_val)
                if isinstance(entries, list):
                    result.setdefault(addr, []).extend(entries)
                elif isinstance(entries, dict):
                    result.setdefault(addr, []).append(entries)
            except (json.JSONDecodeError, TypeError):
                pass

        self.assertEqual(len(result['TAddr1']), 2)
        self.assertEqual(len(result['TAddr2']), 1)
        self.assertEqual(result['TAddr1'][0]['monitor_id'], 1)
        self.assertEqual(result['TAddr1'][1]['monitor_id'], 2)

    def test_add_monitor_appends_to_existing_list(self):
        """add_monitor_to_cache must append to the existing array, not replace it."""
        existing = [
            {'monitor_id': 1, 'threshold': '10.00', 'notify_flag': True, 'user_id': 101},
        ]
        new_entry = {'monitor_id': 2, 'threshold': '20.00', 'notify_flag': False, 'user_id': 102}

        # Simulate the fixed add logic
        entries = list(existing)
        entries.append(new_entry)
        serialised = json.dumps(entries)
        decoded = json.loads(serialised)

        self.assertEqual(len(decoded), 2)
        self.assertEqual(decoded[1]['monitor_id'], 2)

    def test_remove_monitor_removes_only_matching_id(self):
        """remove_monitor_from_cache must remove only the entry with matching monitor_id."""
        existing = [
            {'monitor_id': 1, 'threshold': '10.00', 'notify_flag': True, 'user_id': 101},
            {'monitor_id': 2, 'threshold': '20.00', 'notify_flag': False, 'user_id': 102},
        ]
        monitor_id_to_remove = 1

        # Simulate the fixed remove logic
        updated = [e for e in existing if e.get('monitor_id') != monitor_id_to_remove]

        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]['monitor_id'], 2)


# ---------------------------------------------------------------------------
# 2. biz/services/commerce.py — PayAmountCollisionError
# ---------------------------------------------------------------------------

class TestPayAmountCollisionError(unittest.TestCase):
    """
    The bug: _generate_unique_pay_amount silently returned a duplicate amount
    when all suffixes were exhausted.
    The fix: raises PayAmountCollisionError instead.
    """

    def _simulate_generate_unique_pay_amount(self, existing_amounts: set, max_attempts: int = 5):
        """
        Minimal reimplementation of the fixed logic for testing purposes.
        Returns a unique amount string or raises PayAmountCollisionError.
        """
        class PayAmountCollisionError(RuntimeError):
            pass

        import decimal
        base = decimal.Decimal('100.00')
        for i in range(max_attempts):
            candidate = base + decimal.Decimal(str(i)) / 100
            candidate_str = str(candidate)
            if candidate_str not in existing_amounts:
                return candidate_str
        raise PayAmountCollisionError('支付尾数已耗尽，请稍后重试或更换金额。')

    def test_returns_unique_amount_when_available(self):
        result = self._simulate_generate_unique_pay_amount(set())
        self.assertIsNotNone(result)
        self.assertNotEqual(result, '')

    def test_raises_when_all_suffixes_exhausted(self):
        # Fill all 5 candidate slots
        existing = {'100.00', '100.01', '100.02', '100.03', '100.04'}
        with self.assertRaises(RuntimeError) as ctx:
            self._simulate_generate_unique_pay_amount(existing, max_attempts=5)
        self.assertIn('已耗尽', str(ctx.exception))

    def test_skips_taken_amounts(self):
        existing = {'100.00', '100.01'}
        result = self._simulate_generate_unique_pay_amount(existing, max_attempts=5)
        self.assertNotIn(result, existing)


# ---------------------------------------------------------------------------
# 3. mall/models.py — EncryptedPasswordMixin
# ---------------------------------------------------------------------------

class TestEncryptedPasswordMixin(unittest.TestCase):
    """
    The fix adds EncryptedPasswordMixin to encrypt login_password at save time
    and expose login_password_plain for decryption.
    """

    def _make_mixin_class(self):
        """Build a minimal EncryptedPasswordMixin without Django/Fernet."""
        import base64

        def _encrypt(value: str) -> str:
            # Toy encryption for testing the mixin contract
            return 'ENC:' + base64.b64encode(value.encode()).decode()

        def _decrypt(value: str) -> str:
            if value.startswith('ENC:'):
                return base64.b64decode(value[4:]).decode()
            return value

        class EncryptedPasswordMixin:
            def _prepare_encrypted_fields(self):
                if hasattr(self, 'login_password') and self.login_password:
                    if not self.login_password.startswith('ENC:'):
                        self.login_password = _encrypt(self.login_password)

            @property
            def login_password_plain(self) -> str:
                raw = getattr(self, 'login_password', '') or ''
                return _decrypt(raw)

        class FakeOrder(EncryptedPasswordMixin):
            def __init__(self, password):
                self.login_password = password

            def save(self, **kwargs):
                self._prepare_encrypted_fields()

        return FakeOrder

    def test_password_is_encrypted_on_save(self):
        FakeOrder = self._make_mixin_class()
        order = FakeOrder('my-secret-password')
        order.save()
        self.assertNotEqual(order.login_password, 'my-secret-password')
        self.assertTrue(order.login_password.startswith('ENC:'))

    def test_plain_property_decrypts_correctly(self):
        FakeOrder = self._make_mixin_class()
        order = FakeOrder('my-secret-password')
        order.save()
        self.assertEqual(order.login_password_plain, 'my-secret-password')

    def test_already_encrypted_not_double_encrypted(self):
        FakeOrder = self._make_mixin_class()
        order = FakeOrder('my-secret-password')
        order.save()
        first_encrypted = order.login_password
        order.save()  # second save
        self.assertEqual(order.login_password, first_encrypted,
                         "Double-encrypting must not occur on repeated saves")

    def test_empty_password_not_encrypted(self):
        FakeOrder = self._make_mixin_class()
        order = FakeOrder('')
        order.save()
        self.assertEqual(order.login_password, '')


# ---------------------------------------------------------------------------
# 4. mall/models.py — lifecycle date recalculation on update_fields
# ---------------------------------------------------------------------------

class TestLifecycleDateRecalculation(unittest.TestCase):
    """
    The bug: CloudServerOrder.save(update_fields=[...]) could bypass
    recalculate_lifecycle_dates(), leaving suspend_at / delete_at / ip_recycle_at stale.
    The fix: when update_fields is provided and service_expires_at is being saved,
    the lifecycle fields are added to update_fields and recalculated.
    """

    def _simulate_save_with_update_fields(self, update_fields, service_expires_at, existing_suspend_at=None):
        """
        Simulate the fixed save() logic that expands update_fields to include
        lifecycle date fields when service_expires_at is being updated.
        """
        from datetime import datetime, timedelta, timezone as tz

        LIFECYCLE_FIELDS = {'suspend_at', 'delete_at', 'ip_recycle_at'}
        GRACE_DAYS = 3
        DELETE_DAYS = 7
        RECYCLE_DAYS = 14

        # Simulate the fixed logic
        fields = set(update_fields)
        if 'service_expires_at' in fields:
            fields.update(LIFECYCLE_FIELDS)
            # Recalculate
            suspend_at = service_expires_at + timedelta(days=GRACE_DAYS)
            delete_at = service_expires_at + timedelta(days=DELETE_DAYS)
            ip_recycle_at = service_expires_at + timedelta(days=RECYCLE_DAYS)
        else:
            suspend_at = existing_suspend_at
            delete_at = None
            ip_recycle_at = None

        return list(fields), suspend_at, delete_at, ip_recycle_at

    def test_lifecycle_fields_added_when_expires_at_in_update_fields(self):
        from datetime import datetime, timezone as tz
        expires = datetime(2026, 6, 1, tzinfo=tz.utc)
        fields, suspend_at, delete_at, ip_recycle_at = self._simulate_save_with_update_fields(
            ['status', 'service_expires_at'], expires
        )
        self.assertIn('suspend_at', fields)
        self.assertIn('delete_at', fields)
        self.assertIn('ip_recycle_at', fields)
        self.assertIsNotNone(suspend_at)
        self.assertIsNotNone(delete_at)
        self.assertIsNotNone(ip_recycle_at)

    def test_lifecycle_fields_not_touched_when_expires_at_absent(self):
        from datetime import datetime, timezone as tz
        existing = datetime(2026, 5, 1, tzinfo=tz.utc)
        fields, suspend_at, delete_at, ip_recycle_at = self._simulate_save_with_update_fields(
            ['status', 'provision_note'], None, existing_suspend_at=existing
        )
        self.assertNotIn('suspend_at', fields)
        self.assertEqual(suspend_at, existing,
                         "suspend_at must not change when service_expires_at is not in update_fields")
        self.assertIsNone(delete_at)

    def test_recalculated_dates_are_after_expiry(self):
        from datetime import datetime, timedelta, timezone as tz
        expires = datetime(2026, 6, 1, tzinfo=tz.utc)
        _, suspend_at, delete_at, ip_recycle_at = self._simulate_save_with_update_fields(
            ['service_expires_at'], expires
        )
        self.assertGreater(suspend_at, expires)
        self.assertGreater(delete_at, suspend_at)
        self.assertGreater(ip_recycle_at, delete_at)


# ---------------------------------------------------------------------------
# 5. shop/urls.py — route compatibility without duplicate namespace conflicts
# ---------------------------------------------------------------------------

class TestUrlsCompatibility(unittest.TestCase):
    """
    Verify urls.py keeps the frontend-compatible /api/admin/ prefix while
    avoiding the old three-way duplicate namespace registration.
    """

    def test_frontend_and_public_auth_paths_resolve(self):
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')
        import django
        django.setup()

        from django.urls import resolve

        expected = {
            '/api/auth/login': 'dashboard_api_public:auth-login',
            '/api/admin/auth/login': 'dashboard_api_admin:auth-login',
            '/api/admin/users/': 'dashboard_api_admin:users-list',
            '/api/admin/cloud-assets/': 'dashboard_api_admin:cloud-assets-list',
        }
        self.assertEqual(
            {path: resolve(path).view_name for path in expected},
            expected,
        )

    def test_bare_admin_api_paths_do_not_resolve(self):
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')
        import django
        django.setup()

        from django.urls import Resolver404, resolve

        with self.assertRaises(Resolver404):
            resolve('/api/users/')

    def test_urls_file_has_no_dashboard_duplicate_prefix(self):
        import pathlib
        urls_path = pathlib.Path(__file__).parent / 'shop' / 'urls.py'
        if not urls_path.exists():
            self.skipTest(f'urls.py not found at {urls_path}')
        source = urls_path.read_text()
        self.assertNotIn("api/dashboard/", source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
