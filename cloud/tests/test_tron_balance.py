from .common import *


class DashboardTronBalanceQueryTestCase(TestCase):
    def test_resource_monitor_uses_runtime_trongrid_base_url(self):
        from cloud.resource_monitor import _fetch_account_resource

        captured = {}

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    'freeNetLimit': 100,
                    'freeNetUsed': 10,
                    'NetLimit': 50,
                    'NetUsed': 5,
                    'EnergyLimit': 200,
                    'EnergyUsed': 30,
                }

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, json=None, headers=None):
                captured['url'] = url
                return FakeResponse()

        async def fake_build_headers():
            return {'TRON-PRO-API-KEY': 'resource-key'}

        with (
            patch('cloud.resource_monitor.get_runtime_config', return_value='https://custom.trongrid.example/'),
            patch('cloud.resource_monitor.build_trongrid_headers', new=fake_build_headers),
            patch('cloud.resource_monitor.httpx.AsyncClient', new=FakeAsyncClient),
        ):
            energy, bandwidth = async_to_sync(_fetch_account_resource)('TResourceMonitorAddress')

        self.assertEqual(energy, 170)
        self.assertEqual(bandwidth, 135)
        self.assertEqual(captured['url'], 'https://custom.trongrid.example/wallet/getaccountresource')

    def test_resource_detail_cache_is_scoped_per_user_for_same_address_time(self):
        from cloud.resource_monitor import _cache_resource_detail, get_resource_detail

        first_key = _cache_resource_detail('TResourceMonitorAddress:2026-05-16 08:00:00', {'user_id': 1, 'remark': 'first'})
        second_key = _cache_resource_detail('TResourceMonitorAddress:2026-05-16 08:00:00', {'user_id': 2, 'remark': 'second'})

        self.assertNotEqual(first_key, second_key)
        self.assertEqual(get_resource_detail(first_key)['remark'], 'first')
        self.assertEqual(get_resource_detail(second_key)['remark'], 'second')

    def test_fetch_address_chain_balances_uses_resolved_headers(self):
        captured = {}

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    'data': [{
                        'balance': 2000000,
                        'trc20': [{'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t': '3000000'}],
                    }],
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def get(self, url, headers=None):
                captured['headers'] = headers
                return FakeResponse()

        async def fake_get_redis():
            return None

        async def fake_build_headers():
            return {'TRON-PRO-API-KEY': 'dashboard-key'}

        with (
            patch('cloud.api.get_redis', new=fake_get_redis),
            patch('cloud.api.build_trongrid_headers', new=fake_build_headers),
            patch('cloud.api.httpx.Client', new=FakeClient),
        ):
            usdt_balance, trx_balance, error = _fetch_address_chain_balances('TDashboardBalanceAddress')

        self.assertIsNone(error)
        self.assertEqual(captured['headers'], {'TRON-PRO-API-KEY': 'dashboard-key'})
        self.assertEqual(usdt_balance, Decimal('3'))
        self.assertEqual(trx_balance, Decimal('2'))
