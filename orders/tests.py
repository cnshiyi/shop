from decimal import Decimal

from asgiref.sync import async_to_sync
from django.test import TestCase
from django.utils import timezone

from bot.models import TelegramUser
from cloud.models import CloudServerOrder, CloudServerPlan
from orders.models import BalanceLedger, CartItem, Order, Product, Recharge
from orders.payment_scanner import _expire_timed_out_payment_orders, _get_pending_address_orders, _get_pending_cloud_server_orders, _process_payment
from orders.services import create_cart_balance_orders


class OrderBalancePaymentTestCase(TestCase):
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
