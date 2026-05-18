from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from accounts.models import TelegramUser
from mall.models import CloudAsset, CloudServerOrder, CloudServerPlan, Order, Product, Server
from tron.parser import _timestamp_ms
from tron.scanner import (
    _confirm_cloud_server_order,
    _confirm_order_paid,
    _confirm_recharge,
    _get_pending_address_orders,
    _get_pending_cloud_server_orders,
    _transfer_not_before_created_at,
    cleanup_expired_payment_windows,
)
from finance.models import Recharge


class TronPaymentMatchingTests(SimpleTestCase):
    def test_timestamp_ms_is_extracted_from_transaction_raw_data(self):
        self.assertEqual(
            _timestamp_ms({'raw_data': {'timestamp': '1770000000123'}}),
            1_770_000_000_123,
        )
        self.assertIsNone(_timestamp_ms({'raw_data': {'timestamp': 'bad'}}))

    def test_transfer_before_order_creation_is_rejected(self):
        created_at = timezone.now()
        transfer_time = created_at - timezone.timedelta(minutes=10)
        target = type('Target', (), {'created_at': created_at})()
        transfer = {'timestamp_ms': int(transfer_time.timestamp() * 1000)}

        self.assertFalse(_transfer_not_before_created_at(transfer, target))

    def test_transfer_after_order_creation_is_allowed(self):
        created_at = timezone.now()
        transfer_time = created_at + timezone.timedelta(seconds=5)
        target = type('Target', (), {'created_at': created_at})()
        transfer = {'timestamp_ms': int(transfer_time.timestamp() * 1000)}

        self.assertTrue(_transfer_not_before_created_at(transfer, target))


class TronCloudServerRenewalTests(TestCase):
    def _create_plan(self):
        return CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='TRON Renew Plan',
            price='10.00',
            currency='USDT',
            is_active=True,
        )

    def test_address_paid_renewal_syncs_asset_and_server_expiry(self):
        user = TelegramUser.objects.create(tg_user_id=990301, username='tron_renew_user')
        plan = self._create_plan()
        original_expires_at = timezone.now() + timezone.timedelta(days=2)
        order = CloudServerOrder.objects.create(
            order_no='TRON-RENEW-1',
            user=user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='10.00',
            pay_amount='10.123',
            pay_method='address',
            status='renew_pending',
            lifecycle_days=31,
            service_expires_at=original_expires_at,
            expired_at=timezone.now() + timezone.timedelta(minutes=30),
            renew_notice_sent_at=timezone.now(),
            delete_notice_sent_at=timezone.now(),
            recycle_notice_sent_at=timezone.now(),
            public_ip='203.0.113.88',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            provider=order.provider,
            region_code=order.region_code,
            asset_name='tron-renew-asset',
            public_ip=order.public_ip,
            actual_expires_at=original_expires_at,
            order=order,
            user=user,
        )
        server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            provider=order.provider,
            region_code=order.region_code,
            server_name='tron-renew-server',
            public_ip=order.public_ip,
            expires_at=original_expires_at,
            order=order,
            user=user,
        )

        confirmed = async_to_sync(_confirm_cloud_server_order)(order.id, 'tron-renew-tx')

        self.assertIsNotNone(confirmed)
        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(order.status, 'completed')
        self.assertIsNone(order.expired_at)
        self.assertIsNone(order.renew_notice_sent_at)
        self.assertGreater(order.service_expires_at, original_expires_at)
        self.assertEqual(asset.actual_expires_at, order.service_expires_at)
        self.assertEqual(server.expires_at, order.service_expires_at)

    def test_confirm_rejects_expired_cloud_server_order_inside_transaction(self):
        user = TelegramUser.objects.create(tg_user_id=990303, username='tron_expired_cloud_confirm')
        plan = self._create_plan()
        order = CloudServerOrder.objects.create(
            order_no='TRON-CONFIRM-EXPIRED-CLOUD',
            user=user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='10.00',
            pay_amount='10.123',
            pay_method='address',
            status='pending',
            lifecycle_days=31,
            expired_at=timezone.now() - timezone.timedelta(seconds=1),
        )

        confirmed = async_to_sync(_confirm_cloud_server_order)(order.id, 'tron-expired-cloud-tx')

        self.assertIsNone(confirmed)
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertFalse(order.tx_hash)

    def test_expired_address_renewal_reverts_to_active_status(self):
        user = TelegramUser.objects.create(tg_user_id=990302, username='tron_renew_expired_user')
        plan = self._create_plan()
        order = CloudServerOrder.objects.create(
            order_no='TRON-RENEW-EXPIRED-1',
            user=user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='10.00',
            pay_amount='10.123',
            pay_method='address',
            status='renew_pending',
            lifecycle_days=31,
            service_expires_at=timezone.now() + timezone.timedelta(days=2),
            expired_at=timezone.now() - timezone.timedelta(minutes=1),
            public_ip='203.0.113.89',
        )

        pending = async_to_sync(_get_pending_cloud_server_orders)('USDT')

        self.assertEqual(pending, [])
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')
        self.assertIsNone(order.expired_at)


class TronProductAddressOrderTests(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(tg_user_id=990401, username='tron_product_user')
        self.product = Product.objects.create(
            name='TRON 商品',
            price='5.00',
            stock=1,
            is_active=True,
        )

    def test_paid_address_order_does_not_deduct_reserved_stock_again(self):
        order = Order.objects.create(
            order_no='TRON-PRODUCT-1',
            user=self.user,
            product=self.product,
            product_name=self.product.name,
            quantity=1,
            currency='USDT',
            total_amount='5.00',
            pay_amount='5.123',
            pay_method='address',
            status='pending',
            stock_reserved=True,
            expired_at=timezone.now() + timezone.timedelta(minutes=10),
        )
        self.product.stock = 0
        self.product.save(update_fields=['stock', 'updated_at'])

        confirmed = async_to_sync(_confirm_order_paid)(order.id, 'tron-product-tx')

        self.assertIsNotNone(confirmed)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, 'delivered')
        self.assertFalse(order.stock_reserved)
        self.assertEqual(self.product.stock, 0)

    def test_expired_address_order_releases_reserved_stock(self):
        order = Order.objects.create(
            order_no='TRON-PRODUCT-EXPIRED-1',
            user=self.user,
            product=self.product,
            product_name=self.product.name,
            quantity=1,
            currency='USDT',
            total_amount='5.00',
            pay_amount='5.123',
            pay_method='address',
            status='pending',
            stock_reserved=True,
            expired_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        self.product.stock = 0
        self.product.save(update_fields=['stock', 'updated_at'])

        pending = async_to_sync(_get_pending_address_orders)('USDT')

        self.assertEqual(pending, [])
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, 'expired')
        self.assertFalse(order.stock_reserved)
        self.assertEqual(self.product.stock, 1)

    def test_cleanup_expired_payment_windows_releases_stock_without_transfer(self):
        order = Order.objects.create(
            order_no='TRON-PRODUCT-CLEANUP-1',
            user=self.user,
            product=self.product,
            product_name=self.product.name,
            quantity=1,
            currency='USDT',
            total_amount='5.00',
            pay_amount='5.456',
            pay_method='address',
            status='pending',
            stock_reserved=True,
            expired_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        self.product.stock = 0
        self.product.save(update_fields=['stock', 'updated_at'])

        result = async_to_sync(cleanup_expired_payment_windows)()

        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(result['orders'], 1)
        self.assertEqual(order.status, 'expired')
        self.assertFalse(order.stock_reserved)
        self.assertEqual(self.product.stock, 1)

    def test_legacy_unreserved_address_order_deducts_stock_on_payment(self):
        order = Order.objects.create(
            order_no='TRON-PRODUCT-LEGACY-1',
            user=self.user,
            product=self.product,
            product_name=self.product.name,
            quantity=1,
            currency='USDT',
            total_amount='5.00',
            pay_amount='5.789',
            pay_method='address',
            status='pending',
            stock_reserved=False,
            expired_at=timezone.now() + timezone.timedelta(minutes=10),
        )

        confirmed = async_to_sync(_confirm_order_paid)(order.id, 'tron-product-legacy-tx')

        self.assertIsNotNone(confirmed)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, 'delivered')
        self.assertFalse(order.stock_reserved)
        self.assertEqual(self.product.stock, 0)

    def test_confirm_rejects_expired_product_order_inside_transaction(self):
        order = Order.objects.create(
            order_no='TRON-PRODUCT-CONFIRM-EXPIRED',
            user=self.user,
            product=self.product,
            product_name=self.product.name,
            quantity=1,
            currency='USDT',
            total_amount='5.00',
            pay_amount='5.111',
            pay_method='address',
            status='pending',
            stock_reserved=True,
            expired_at=timezone.now() - timezone.timedelta(seconds=1),
        )

        confirmed = async_to_sync(_confirm_order_paid)(order.id, 'tron-product-expired-confirm-tx')

        self.assertIsNone(confirmed)
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertFalse(order.tx_hash)


class TronRechargeTests(TestCase):
    def test_confirm_rejects_expired_recharge_inside_transaction(self):
        user = TelegramUser.objects.create(tg_user_id=990501, username='tron_recharge_expired')
        recharge = Recharge.objects.create(
            user=user,
            currency='USDT',
            amount='8.00',
            pay_amount='8.123',
            status='pending',
            expired_at=timezone.now() - timezone.timedelta(seconds=1),
        )

        confirmed = async_to_sync(_confirm_recharge)(recharge.id, 'tron-expired-recharge-tx')

        self.assertIsNone(confirmed)
        recharge.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(recharge.status, 'pending')
        self.assertFalse(recharge.tx_hash)
        self.assertEqual(user.balance, 0)
