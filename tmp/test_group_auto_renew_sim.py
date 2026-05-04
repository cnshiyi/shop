from decimal import Decimal
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TestCase
from django.utils import timezone

from bot.models import TelegramGroupFilter, TelegramUser
from bot.services import should_forward_telegram_group
from cloud.lifecycle import _run_auto_renew
from cloud.models import CloudAsset, CloudServerOrder, CloudServerPlan
from cloud.services import create_cloud_server_renewal_for_user, get_cloud_order_group_balance_lines, list_user_auto_renew_cloud_servers


class GroupAutoRenewSimulationTestCase(TestCase):
    def setUp(self):
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
        self.owner = TelegramUser.objects.create(
            tg_user_id=880001,
            username='owner_a',
            balance=Decimal('1.000000'),
            balance_trx=Decimal('10.000000'),
        )
        self.helper = TelegramUser.objects.create(
            tg_user_id=880002,
            username='helper_b',
            balance=Decimal('100.000000'),
            balance_trx=Decimal('20.000000'),
        )
        self.group = TelegramGroupFilter.objects.create(
            chat_id=-1001234567890,
            title='测试群组',
            username='testgroup',
            enabled=False,
        )
        self.order = CloudServerOrder.objects.create(
            order_no='SIM-GROUP-AUTO-1',
            user=self.owner,
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
            public_ip='8.8.8.8',
            instance_id='i-sim-001',
            server_name='sim-server-001',
            service_started_at=timezone.now() - timezone.timedelta(days=10),
            service_expires_at=timezone.now() + timezone.timedelta(hours=20),
            auto_renew_enabled=True,
        )
        self.asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=self.order,
            user=self.owner,
            telegram_group=self.group,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='sim-group-asset',
            public_ip='8.8.8.8',
            actual_expires_at=self.order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
        )
        self.helper_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            user=self.helper,
            telegram_group=self.group,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='sim-helper-group-asset',
            public_ip='8.8.8.8',
            actual_expires_at=self.order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
        )

    def test_full_group_auto_renew_flow(self):
        disabled_forward = async_to_sync(should_forward_telegram_group)(self.group.chat_id, self.group.title, self.group.username)
        self.assertFalse(disabled_forward)

        self.group.enabled = True
        self.group.save(update_fields=['enabled', 'updated_at'])
        enabled_forward = async_to_sync(should_forward_telegram_group)(self.group.chat_id, self.group.title, self.group.username)
        self.assertTrue(enabled_forward)

        visible_orders = async_to_sync(list_user_auto_renew_cloud_servers)(self.helper.id)
        self.assertIn(self.asset.id, [item.id for item in visible_orders])

        balance_lines = async_to_sync(get_cloud_order_group_balance_lines)(self.order.id)
        self.assertIn('owner_a', '\n'.join(balance_lines))
        self.assertIn('helper_b', '\n'.join(balance_lines))

        renewal = async_to_sync(create_cloud_server_renewal_for_user)(self.order.id, self.helper.id, 31)
        self.assertIsNotNone(renewal)
        self.assertEqual(renewal.status, 'renew_pending')

        with patch('cloud.services._renew_aliyun_instance', return_value=(True, 'ok')):
            renewed_order, error, balance_change = async_to_sync(_run_auto_renew)(self.order.id)

        self.assertIsNotNone(renewed_order)
        self.assertIsNone(error)
        self.assertEqual(balance_change['payer_user_id'], self.helper.id)
        self.assertIn('helper_b', balance_change['payer_label'])
        self.order.refresh_from_db()
        self.helper.refresh_from_db()
        self.assertEqual(self.order.status, 'completed')
        self.assertGreater(self.order.service_expires_at, timezone.now())
        self.assertLess(self.helper.balance, Decimal('100.000000'))
