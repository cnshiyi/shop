from decimal import Decimal

from asgiref.sync import async_to_sync
from django.test import TestCase

from bot.models import TelegramUser
from orders.models import BalanceLedger, CartItem, Product
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
