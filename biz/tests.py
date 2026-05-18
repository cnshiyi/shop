from unittest.mock import patch

from asgiref.sync import async_to_sync
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone

from accounts.models import TelegramUser
from biz.services.commerce import create_address_order
from biz.services.cloud_servers import (
    create_cloud_server_renewal,
    mark_cloud_server_ip_change_requested,
    pay_cloud_server_renewal_with_balance,
    set_cloud_server_auto_renew,
)
from biz.services.custom import pay_cloud_server_order_with_balance, set_cloud_server_port
from mall.models import CloudServerOrder, CloudServerPlan, Product


class CloudServerServicesTestCase(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(tg_user_id=990001, username='svc_test')
        self.plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Micro 1G 40G 2TB',
            cpu='2核',
            memory='1GB',
            storage='40GB SSD',
            bandwidth='2TB',
            price='19.00',
            currency='USDT',
            is_active=True,
            sort_order=100,
        )

    def test_create_cloud_server_renewal_rejects_deleted_or_ipless_order(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RENEW-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleted',
            public_ip='',
        )

        result = async_to_sync(create_cloud_server_renewal)(order.id, self.user.id, 31)

        self.assertFalse(result)

    def test_create_cloud_server_renewal_requires_order_owner(self):
        other_user = TelegramUser.objects.create(tg_user_id=990002, username='other_user')
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RENEW-OWNER',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='completed',
            public_ip='203.0.113.30',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )

        result = async_to_sync(create_cloud_server_renewal)(order.id, other_user.id, 31)

        self.assertIsNone(result)
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')

    def test_create_cloud_server_renewal_switches_to_address_payment(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RENEW-ADDRESS',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='203.0.113.31',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )

        result = async_to_sync(create_cloud_server_renewal)(order.id, self.user.id, 31)

        self.assertEqual(result.status, 'renew_pending')
        self.assertEqual(result.pay_method, 'address')
        self.assertIsNotNone(result.expired_at)

    def test_wallet_renewal_rejects_new_pending_cloud_order(self):
        self.user.balance = '50.00'
        self.user.save(update_fields=['balance', 'updated_at'])
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RENEW-WALLET-PENDING',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='pending',
            public_ip='203.0.113.32',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )

        paid_order, error = async_to_sync(pay_cloud_server_renewal_with_balance)(order.id, self.user.id)

        self.assertIsNone(paid_order)
        self.assertIn('状态不可钱包支付', error)
        order.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertEqual(self.user.balance, Decimal('50.000000'))

    def test_wallet_order_payment_expires_stale_pending_order_without_charge(self):
        self.user.balance = '50.00'
        self.user.save(update_fields=['balance', 'updated_at'])
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-WALLET-EXPIRED',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='address',
            status='pending',
            expired_at=timezone.now() - timezone.timedelta(minutes=1),
        )

        paid_order, error = async_to_sync(pay_cloud_server_order_with_balance)(order.id, self.user.id)

        self.assertIsNone(paid_order)
        self.assertIn('已过期', error)
        order.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(order.status, 'expired')
        self.assertEqual(self.user.balance, Decimal('50.000000'))

    def test_set_cloud_server_port_only_accepts_paid_unprovisioned_order(self):
        pending_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-PORT-PENDING',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='pending',
            mtproxy_port=9528,
        )
        paid_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-PORT-PAID',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='paid',
            mtproxy_port=9528,
        )

        rejected = async_to_sync(set_cloud_server_port)(pending_order.id, self.user.id, 10086)
        accepted = async_to_sync(set_cloud_server_port)(paid_order.id, self.user.id, 10087)

        self.assertFalse(rejected)
        pending_order.refresh_from_db()
        paid_order.refresh_from_db()
        self.assertEqual(pending_order.mtproxy_port, 9528)
        self.assertEqual(accepted.id, paid_order.id)
        self.assertEqual(paid_order.mtproxy_port, 10087)

    def test_auto_renew_toggle_rejects_nonrenewable_order(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-AUTORENEW-DELETED',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleted',
            public_ip='203.0.113.33',
        )

        result = async_to_sync(set_cloud_server_auto_renew)(order.id, self.user.id, True)

        self.assertFalse(result)
        order.refresh_from_db()
        self.assertFalse(order.auto_renew_enabled)

    def test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing(self):
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REPLACE-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='1.2.3.4',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )
        original_expires_at = source_order.service_expires_at

        class FakeQuerySet:
            def first(inner_self):
                source_order.plan_id = None
                return source_order

        with patch('biz.services.cloud_servers.CloudServerOrder.objects.filter', return_value=FakeQuerySet()):
            new_order = async_to_sync(mark_cloud_server_ip_change_requested)(source_order.id, self.user.id)

        self.assertTrue(new_order)
        self.assertEqual(new_order.plan_id, self.plan.id)
        self.assertEqual(new_order.replacement_for_id, source_order.id)
        self.assertEqual(new_order.service_expires_at, original_expires_at)

        source_order.refresh_from_db()
        self.assertLessEqual(
            source_order.service_expires_at,
            timezone.now() + timezone.timedelta(days=5, minutes=1),
        )

    def test_mark_cloud_server_ip_change_requested_reuses_active_replacement(self):
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REPLACE-REUSE',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='1.2.3.5',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )
        existing_replacement = CloudServerOrder.objects.create(
            order_no='HB-TEST-REPLACE-REUSE-IP',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='paid',
            public_ip='',
            replacement_for=source_order,
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )

        result = async_to_sync(mark_cloud_server_ip_change_requested)(source_order.id, self.user.id)

        self.assertEqual(result.id, existing_replacement.id)
        self.assertEqual(CloudServerOrder.objects.filter(replacement_for=source_order).count(), 1)


class CommerceServicesTestCase(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(tg_user_id=991001, username='commerce_test')

    def test_address_order_reserves_stock_when_created(self):
        product = Product.objects.create(
            name='库存商品',
            price='5.00',
            stock=2,
            is_active=True,
        )

        order = async_to_sync(create_address_order)(self.user.id, product.id, 1, Decimal('5.00'), 'USDT')

        product.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertEqual(product.stock, 1)

    def test_address_order_rejects_when_stock_unavailable(self):
        product = Product.objects.create(
            name='缺货商品',
            price='5.00',
            stock=0,
            is_active=True,
        )

        with self.assertRaises(ValueError):
            async_to_sync(create_address_order)(self.user.id, product.id, 1, Decimal('5.00'), 'USDT')

        product.refresh_from_db()
        self.assertEqual(product.stock, 0)
