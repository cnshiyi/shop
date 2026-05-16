from decimal import Decimal
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase
from django.utils import timezone

from bot.models import TelegramUser
from cloud.models import AddressMonitor, CloudAsset, CloudServerOrder, CloudServerPlan, DailyAddressStat
from orders.models import BalanceLedger, CartItem, Order, Product, Recharge
from orders.payment_scanner import _cache_tx_detail, _confirm_order_paid, _copy_notice_to_admins, _expire_timed_out_payment_orders, _get_address_chain_balances, _get_pending_address_orders, _get_pending_cloud_server_orders, _process_payment, _record_daily_stats_for_monitors, get_tx_detail, set_bot
from orders.services import create_cart_address_orders, create_cart_balance_orders
from orders.tron_parser import is_valid_tron_address


class OrderBalancePaymentTestCase(TestCase):
    def _create_plan(self):
        return CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Micro 1G 40G 2TB',
            price=Decimal('19.000000'),
            currency='USDT',
            is_active=True,
        )

    def test_cart_balance_ledgers_track_running_balance(self):
        user = TelegramUser.objects.create(
            tg_user_id=990201,
            username='cart_balance_test',
            balance=Decimal('20.000000'),
        )
        first_product = Product.objects.create(
            name='商品A',
            price=Decimal('3.000000'),
            content_type=Product.CONTENT_TEXT,
            content_text='A',
            stock=10,
            is_active=True,
        )
        second_product = Product.objects.create(
            name='商品B',
            price=Decimal('4.000000'),
            content_type=Product.CONTENT_TEXT,
            content_text='B',
            stock=10,
            is_active=True,
        )
        CartItem.objects.create(user=user, product=first_product, quantity=2)
        CartItem.objects.create(user=user, product=second_product, quantity=1)

        orders, err = async_to_sync(create_cart_balance_orders)(user.id, 'USDT')

        self.assertIsNone(err)
        self.assertEqual(len(orders), 2)
        user.refresh_from_db()
        self.assertEqual(user.balance, Decimal('10.000000'))
        ledgers = list(BalanceLedger.objects.filter(user=user, type='order_balance_pay').order_by('created_at', 'id'))
        self.assertEqual([ledger.before_balance for ledger in ledgers], [Decimal('20.000000000'), Decimal('16.000000000')])
        self.assertEqual([ledger.after_balance for ledger in ledgers], [Decimal('16.000000000'), Decimal('10.000000000')])

    def test_cart_address_checkout_keeps_cloud_plan_items(self):
        user = TelegramUser.objects.create(tg_user_id=990202, username='cart_address_keep_cloud')
        product = Product.objects.create(
            name='普通商品',
            price=Decimal('3.000000'),
            content_type=Product.CONTENT_TEXT,
            content_text='content',
            stock=10,
            is_active=True,
        )
        plan = self._create_plan()
        CartItem.objects.create(user=user, item_type='product', product=product, quantity=1)
        cloud_item = CartItem.objects.create(user=user, item_type='cloud_plan', cloud_plan=plan, quantity=1)

        orders = async_to_sync(create_cart_address_orders)(user.id, 'USDT')

        self.assertEqual(len(orders), 1)
        self.assertFalse(CartItem.objects.filter(user=user, item_type='product').exists())
        self.assertTrue(CartItem.objects.filter(id=cloud_item.id, user=user, item_type='cloud_plan').exists())

    def test_cart_balance_checkout_keeps_cloud_plan_items(self):
        user = TelegramUser.objects.create(
            tg_user_id=990203,
            username='cart_balance_keep_cloud',
            balance=Decimal('20.000000'),
        )
        product = Product.objects.create(
            name='普通商品',
            price=Decimal('3.000000'),
            content_type=Product.CONTENT_TEXT,
            content_text='content',
            stock=10,
            is_active=True,
        )
        plan = self._create_plan()
        CartItem.objects.create(user=user, item_type='product', product=product, quantity=1)
        cloud_item = CartItem.objects.create(user=user, item_type='cloud_plan', cloud_plan=plan, quantity=1)

        orders, err = async_to_sync(create_cart_balance_orders)(user.id, 'USDT')

        self.assertIsNone(err)
        self.assertEqual(len(orders), 1)
        self.assertFalse(CartItem.objects.filter(user=user, item_type='product').exists())
        self.assertTrue(CartItem.objects.filter(id=cloud_item.id, user=user, item_type='cloud_plan').exists())

    def test_cart_balance_checkout_with_only_cloud_plan_does_not_clear_cart(self):
        user = TelegramUser.objects.create(
            tg_user_id=990204,
            username='cart_balance_only_cloud',
            balance=Decimal('20.000000'),
        )
        plan = self._create_plan()
        cloud_item = CartItem.objects.create(user=user, item_type='cloud_plan', cloud_plan=plan, quantity=1)

        orders, err = async_to_sync(create_cart_balance_orders)(user.id, 'USDT')

        self.assertIsNone(err)
        self.assertEqual(orders, [])
        self.assertTrue(CartItem.objects.filter(id=cloud_item.id, user=user, item_type='cloud_plan').exists())


class TronParserTestCase(TestCase):
    def test_tron_address_validation_uses_base58check(self):
        self.assertTrue(is_valid_tron_address('TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'))
        self.assertFalse(is_valid_tron_address('TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6x'))
        self.assertFalse(is_valid_tron_address('T' + '1' * 33))


class TronMonitorStatsTestCase(TestCase):
    def test_tx_detail_cache_is_scoped_per_user_for_same_hash(self):
        first_key = _cache_tx_detail('tx-shared-monitor-detail', {'user_id': 1, 'remark': 'first'})
        second_key = _cache_tx_detail('tx-shared-monitor-detail', {'user_id': 2, 'remark': 'second'})

        self.assertNotEqual(first_key, second_key)
        self.assertEqual(get_tx_detail(first_key)['remark'], 'first')
        self.assertEqual(get_tx_detail(second_key)['remark'], 'second')

    def test_daily_stats_are_recorded_for_each_monitor_on_same_address(self):
        first = TelegramUser.objects.create(tg_user_id=990251, username='monitor_stats_first')
        second = TelegramUser.objects.create(tg_user_id=990252, username='monitor_stats_second')
        first_monitor = AddressMonitor.objects.create(user=first, address='TMonitorAddress', remark='first')
        second_monitor = AddressMonitor.objects.create(user=second, address='TMonitorAddress', remark='second')

        with patch('orders.payment_scanner.bump_daily_stats', new=AsyncMock()), \
            patch('orders.payment_scanner.get_daily_stats', new=AsyncMock(side_effect=[123000000, 0])):
            stats = async_to_sync(_record_daily_stats_for_monitors)(
                'TMonitorAddress',
                'USDT',
                'income',
                Decimal('123'),
                [{'user_id': first.id, 'id': first_monitor.id}, {'user_id': second.id, 'id': second_monitor.id}],
            )

        self.assertEqual(stats, {'income': '123', 'expense': '0'})
        rows = DailyAddressStat.objects.filter(address='TMonitorAddress', currency='USDT').order_by('user_id')
        self.assertEqual([row.user_id for row in rows], [first.id, second.id])
        self.assertEqual([row.monitor_id for row in rows], [first_monitor.id, second_monitor.id])
        self.assertEqual([row.income for row in rows], [Decimal('123.000000000'), Decimal('123.000000000')])


class ChainPaymentScannerTestCase(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(tg_user_id=990301, username='chain_payment_test')
        self.product = Product.objects.create(
            name='链上商品',
            price=Decimal('5.000000'),
            content_type=Product.CONTENT_TEXT,
            content_text='content',
            stock=10,
            is_active=True,
        )
        self.plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Micro 1G 40G 2TB',
            price=Decimal('19.000000'),
            currency='USDT',
            is_active=True,
        )

    def test_chain_payment_conflict_is_not_auto_confirmed(self):
        amount = Decimal('5.123')
        order = Order.objects.create(
            order_no='CHAIN-CONFLICT-ORDER',
            user=self.user,
            product=self.product,
            product_name=self.product.name,
            quantity=1,
            currency='USDT',
            total_amount=Decimal('5.000000'),
            pay_amount=amount,
            pay_method='address',
            status='pending',
            expired_at=timezone.now() + timezone.timedelta(minutes=10),
        )
        recharge = Recharge.objects.create(
            user=self.user,
            currency='USDT',
            amount=Decimal('5.000000'),
            pay_amount=amount,
            status='pending',
            expired_at=timezone.now() + timezone.timedelta(minutes=10),
        )

        matched = async_to_sync(_process_payment)({'amount': amount, 'tx_hash': 'tx-chain-conflict', 'currency': 'USDT', 'from': 'payer', 'to': 'receiver'})

        self.assertFalse(matched)
        order.refresh_from_db()
        recharge.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertEqual(recharge.status, 'pending')
        self.assertIsNone(order.tx_hash)
        self.assertIsNone(recharge.tx_hash)

    def test_product_chain_payment_does_not_overdraw_stock(self):
        product = Product.objects.create(
            name='库存不足商品',
            price=Decimal('5.000000'),
            content_type=Product.CONTENT_TEXT,
            content_text='content',
            stock=1,
            is_active=True,
        )
        order = Order.objects.create(
            order_no='CHAIN-STOCK-INSUFFICIENT',
            user=self.user,
            product=product,
            product_name=product.name,
            quantity=2,
            currency='USDT',
            total_amount=Decimal('10.000000'),
            pay_amount=Decimal('10.123'),
            pay_method='address',
            status='pending',
            expired_at=timezone.now() + timezone.timedelta(minutes=10),
        )

        confirmed = async_to_sync(_confirm_order_paid)(order.id, 'tx-stock-insufficient', 'payer', 'receiver')

        self.assertIsNone(confirmed)
        order.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertIsNone(order.tx_hash)
        self.assertEqual(product.stock, 1)

    def test_product_chain_payment_confirms_and_delivers_without_updated_at_field(self):
        order = Order.objects.create(
            order_no='CHAIN-PRODUCT-SUCCESS',
            user=self.user,
            product=self.product,
            product_name=self.product.name,
            quantity=2,
            currency='USDT',
            total_amount=Decimal('10.000000'),
            pay_amount=Decimal('10.123'),
            pay_method='address',
            status='pending',
            expired_at=timezone.now() + timezone.timedelta(minutes=10),
        )

        confirmed = async_to_sync(_confirm_order_paid)(order.id, 'tx-product-success', 'payer', 'receiver')

        self.assertIsNotNone(confirmed)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, 'delivered')
        self.assertEqual(order.tx_hash, 'tx-product-success')
        self.assertEqual(order.payer_address, 'payer')
        self.assertEqual(order.receive_address, 'receiver')
        self.assertEqual(self.product.stock, 8)

    def test_expired_address_payments_are_not_candidates_and_renewal_status_restores(self):
        expired_at = timezone.now() - timezone.timedelta(minutes=1)
        Order.objects.create(
            order_no='CHAIN-EXPIRED-ORDER',
            user=self.user,
            product=self.product,
            product_name=self.product.name,
            quantity=1,
            currency='USDT',
            total_amount=Decimal('5.000000'),
            pay_amount=Decimal('5.234'),
            pay_method='address',
            status='pending',
            expired_at=expired_at,
        )
        cloud_order = CloudServerOrder.objects.create(
            order_no='CHAIN-EXPIRED-CLOUD',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount=Decimal('19.000000'),
            pay_amount=Decimal('19.234'),
            pay_method='address',
            status='renew_pending',
            public_ip='1.1.1.1',
            expired_at=expired_at,
        )

        products = async_to_sync(_get_pending_address_orders)('USDT')
        clouds = async_to_sync(_get_pending_cloud_server_orders)('USDT')
        async_to_sync(_expire_timed_out_payment_orders)()

        self.assertFalse(products)
        self.assertFalse(clouds)
        cloud_order.refresh_from_db()
        self.assertEqual(cloud_order.status, 'completed')
        self.assertIsNone(cloud_order.expired_at)

    def test_expired_asset_renewal_payment_unbinds_asset_for_retry(self):
        expired_at = timezone.now() - timezone.timedelta(minutes=1)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='expired-asset-renewal',
            public_ip='31.31.31.99',
            previous_public_ip='31.31.31.99',
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        renewal = CloudServerOrder.objects.create(
            order_no='CHAIN-EXPIRED-ASSET-RENEWAL',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount=Decimal('19.000000'),
            pay_amount=Decimal('19.234'),
            pay_method='address',
            status='pending',
            public_ip=asset.public_ip,
            previous_public_ip=asset.previous_public_ip,
            expired_at=expired_at,
            provision_note='未绑定代理资产续费：来源资产 #1；等待支付。',
        )
        asset.order = renewal
        asset.save(update_fields=['order', 'updated_at'])

        async_to_sync(_expire_timed_out_payment_orders)()

        renewal.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(renewal.status, 'expired')
        self.assertIsNone(asset.order_id)
        self.assertIn('可重新发起续费', asset.note)

    def test_duplicate_tx_hash_is_not_reused_across_payment_types(self):
        Recharge.objects.create(
            user=self.user,
            currency='USDT',
            amount=Decimal('8.000000'),
            pay_amount=Decimal('8.123'),
            status='completed',
            tx_hash='tx-duplicate-chain',
            completed_at=timezone.now(),
        )
        order = Order.objects.create(
            order_no='CHAIN-DUPLICATE-TX-ORDER',
            user=self.user,
            product=self.product,
            product_name=self.product.name,
            quantity=1,
            currency='USDT',
            total_amount=Decimal('5.000000'),
            pay_amount=Decimal('5.456'),
            pay_method='address',
            status='pending',
            expired_at=timezone.now() + timezone.timedelta(minutes=10),
        )

        matched = async_to_sync(_process_payment)({'amount': Decimal('5.456'), 'tx_hash': 'tx-duplicate-chain', 'currency': 'USDT', 'from': 'payer', 'to': 'receiver'})

        self.assertFalse(matched)
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertIsNone(order.tx_hash)

    def test_renew_pending_cloud_with_previous_ip_is_candidate(self):
        order = CloudServerOrder.objects.create(
            order_no='CHAIN-PREVIOUS-IP-RENEW',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount=Decimal('19.000000'),
            pay_amount=Decimal('19.345'),
            pay_method='address',
            status='renew_pending',
            public_ip='',
            previous_public_ip='2.2.2.2',
            expired_at=timezone.now() + timezone.timedelta(minutes=10),
        )

        clouds = async_to_sync(_get_pending_cloud_server_orders)('USDT')

        self.assertIn(order.id, [item.id for item in clouds])

    def test_usdt_cloud_order_is_not_trx_payment_candidate(self):
        order = CloudServerOrder.objects.create(
            order_no='CHAIN-USDT-CLOUD-NOT-TRX',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount=Decimal('19.000000'),
            pay_amount=Decimal('19.345'),
            pay_method='address',
            status='pending',
            expired_at=timezone.now() + timezone.timedelta(minutes=10),
        )
        trx_order = CloudServerOrder.objects.create(
            order_no='CHAIN-TRX-CLOUD-CANDIDATE',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='TRX',
            total_amount=Decimal('19.000000'),
            pay_amount=Decimal('100.345'),
            pay_method='address',
            status='pending',
            expired_at=timezone.now() + timezone.timedelta(minutes=10),
        )

        clouds = async_to_sync(_get_pending_cloud_server_orders)('TRX')

        self.assertNotIn(order.id, [item.id for item in clouds])
        self.assertIn(trx_order.id, [item.id for item in clouds])

    def test_address_balance_query_awaits_trongrid_headers(self):
        captured = {}

        class FakeResponse:
            def __init__(self, status_code=200):
                self.status_code = status_code

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    'data': [{
                        'balance': 123000000,
                        'trc20': [{'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t': '456000000'}],
                    }],
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.calls = 0
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, headers=None):
                self.calls += 1
                captured['headers'] = headers
                captured.setdefault('all_headers', []).append(headers)
                return FakeResponse(401 if self.calls == 1 else 200)

        with (
            patch('orders.payment_scanner.get_redis', new=AsyncMock(return_value=None)),
            patch('orders.payment_scanner.build_trongrid_headers', new=AsyncMock(return_value={'TRON-PRO-API-KEY': 'key'})),
            patch('orders.payment_scanner.httpx.AsyncClient', new=FakeClient),
        ):
            usdt_balance, trx_balance = async_to_sync(_get_address_chain_balances)('TBalanceAddress')

        self.assertEqual(captured['all_headers'][0], {'TRON-PRO-API-KEY': 'key'})
        self.assertNotIn('TRON-PRO-API-KEY', captured['all_headers'][1])
        self.assertEqual(usdt_balance, Decimal('456'))
        self.assertEqual(trx_balance, Decimal('123'))

    def test_notice_copy_reads_async_site_config(self):
        class FakeBot:
            def __init__(self):
                self.messages = []

            async def send_message(self, **kwargs):
                self.messages.append(kwargs)

        bot = FakeBot()
        set_bot(bot)
        try:
            with patch('orders.payment_scanner.get_config', new=AsyncMock(return_value='12345')):
                async_to_sync(_copy_notice_to_admins)(self.user, 'hello')
        finally:
            set_bot(None)

        self.assertEqual(len(bot.messages), 1)
        self.assertEqual(bot.messages[0]['chat_id'], 12345)
        self.assertIn('hello', bot.messages[0]['text'])


class RechargeDashboardStatusTestCase(TestCase):
    def test_manual_complete_and_revert_write_balance_ledgers(self):
        from orders.api import _apply_recharge_status

        user = TelegramUser.objects.create(tg_user_id=990401, username='dashboard_recharge_test', balance=Decimal('1.000000'))
        recharge = Recharge.objects.create(
            user=user,
            currency='USDT',
            amount=Decimal('7.500000'),
            pay_amount=Decimal('7.500000000'),
            status='pending',
            expired_at=timezone.now() + timezone.timedelta(minutes=10),
        )

        _apply_recharge_status(recharge, 'completed', operator='admin')

        user.refresh_from_db()
        recharge.refresh_from_db()
        self.assertEqual(recharge.status, 'completed')
        self.assertEqual(user.balance, Decimal('8.500000'))
        complete_ledger = BalanceLedger.objects.get(related_type='recharge', related_id=recharge.id, type='recharge')
        self.assertEqual(complete_ledger.direction, BalanceLedger.DIRECTION_IN)
        self.assertEqual(complete_ledger.before_balance, Decimal('1.000000000'))
        self.assertEqual(complete_ledger.after_balance, Decimal('8.500000000'))
        self.assertEqual(complete_ledger.operator, 'admin')

        _apply_recharge_status(recharge, 'expired', operator='admin')

        user.refresh_from_db()
        recharge.refresh_from_db()
        self.assertEqual(recharge.status, 'expired')
        self.assertIsNone(recharge.completed_at)
        self.assertEqual(user.balance, Decimal('1.000000'))
        revert_ledger = BalanceLedger.objects.get(related_type='recharge', related_id=recharge.id, type='manual_adjust')
        self.assertEqual(revert_ledger.direction, BalanceLedger.DIRECTION_OUT)
        self.assertEqual(revert_ledger.before_balance, Decimal('8.500000000'))
        self.assertEqual(revert_ledger.after_balance, Decimal('1.000000000'))


class CloudRefundSafetyTestCase(TestCase):
    def test_refund_can_not_credit_same_order_twice(self):
        from cloud.services import refund_cloud_server_to_balance

        user = TelegramUser.objects.create(tg_user_id=990402, username='cloud_refund_test', balance=Decimal('0.000000'))
        plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Refund Test',
            price=Decimal('12.000000'),
            currency='USDT',
            is_active=True,
        )
        order = CloudServerOrder.objects.create(
            order_no='REFUND-SAFETY-ORDER',
            user=user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount=Decimal('12.000000'),
            pay_amount=Decimal('12.000000000'),
            pay_method='balance',
            status='failed',
        )

        result, error = async_to_sync(refund_cloud_server_to_balance)(order.id, user.id)
        self.assertIsNone(error)
        self.assertEqual(result['amount'], Decimal('12.000'))

        result, error = async_to_sync(refund_cloud_server_to_balance)(order.id, user.id)
        self.assertIsNone(result)
        self.assertEqual(error, '该订单已退款，不能重复退款')

        user.refresh_from_db()
        self.assertEqual(user.balance, Decimal('12.000000'))
        ledgers = BalanceLedger.objects.filter(
            user=user,
            type='manual_adjust',
            direction=BalanceLedger.DIRECTION_IN,
            related_type='cloud_order',
            related_id=order.id,
            description__startswith='云服务器剩余价值退款 #',
        )
        self.assertEqual(ledgers.count(), 1)
