import json
from decimal import Decimal

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from bot.models import TelegramGroupFilter, TelegramUser
from bot.services import should_forward_telegram_group
from cloud.lifecycle import _run_auto_renew
from cloud.models import CloudAsset, CloudServerOrder, CloudServerPlan
from cloud.services import create_cloud_server_renewal_for_user, get_cloud_order_group_balance_lines, list_user_auto_renew_cloud_servers
from cloud.api import update_cloud_asset


class ProjectSelfCheckSimulationTestCase(TestCase):
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
            tg_user_id=990101,
            username='owner_main',
            balance=Decimal('2.000000'),
            balance_trx=Decimal('8.000000'),
        )
        self.helper = TelegramUser.objects.create(
            tg_user_id=990102,
            username='helper_side',
            balance=Decimal('55.000000'),
            balance_trx=Decimal('5.000000'),
        )
        self.group = TelegramGroupFilter.objects.create(
            chat_id=-1009876543210,
            title='自检群组',
            username='selfcheckgroup',
            enabled=False,
        )
        self.order = CloudServerOrder.objects.create(
            order_no='SELF-CHECK-ORDER-1',
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
            public_ip='9.9.9.9',
            instance_id='i-selfcheck-001',
            server_name='selfcheck-server-001',
            service_started_at=timezone.now() - timezone.timedelta(days=8),
            service_expires_at=timezone.now() + timezone.timedelta(hours=18),
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
            asset_name='selfcheck-asset',
            public_ip='9.9.9.9',
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
            asset_name='selfcheck-helper-asset',
            public_ip='9.9.9.9',
            actual_expires_at=self.order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
        )

    def test_group_toggle_visibility_and_balance_lines(self):
        disabled = async_to_sync(should_forward_telegram_group)(self.group.chat_id, self.group.title, self.group.username)
        self.assertFalse(disabled)

        self.group.enabled = True
        self.group.save(update_fields=['enabled', 'updated_at'])
        enabled = async_to_sync(should_forward_telegram_group)(self.group.chat_id, self.group.title, self.group.username)
        self.assertTrue(enabled)

        visible = async_to_sync(list_user_auto_renew_cloud_servers)(self.helper.id)
        self.assertIn(self.asset.id, [item.id for item in visible])

        balance_lines = async_to_sync(get_cloud_order_group_balance_lines)(self.order.id)
        self.assertTrue(any('owner_main' in line for line in balance_lines))
        self.assertTrue(any('helper_side' in line for line in balance_lines))

    def test_edit_asset_binds_group_and_updates_expiry(self):
        staff_user = get_user_model().objects.create_user(username='staff_selfcheck_edit', password='x', is_staff=True)
        new_expiry = timezone.now() + timezone.timedelta(days=45)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{self.asset.id}/',
            data=json.dumps({
                'actual_expires_at': new_expiry.isoformat(),
                'telegram_group_query': str(self.group.chat_id),
            }),
            content_type='application/json',
        )
        request.user = staff_user

        response = update_cloud_asset(request, self.asset.id)
        payload = json.loads(response.content)
        data = payload['data']

        self.assertEqual(response.status_code, 200)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.telegram_group_id, self.group.id)
        self.assertEqual(self.asset.actual_expires_at.replace(microsecond=0), new_expiry.replace(microsecond=0))
        self.assertEqual(data['telegram_group_id'], self.group.id)
        self.assertEqual(data['order_detail_path'], f'/admin/cloud-orders/{data["order_id"]}')
        self.assertEqual(data['order_link_path'], f'/admin/cloud-orders/{data["order_id"]}')
        new_order = CloudServerOrder.objects.get(id=data['order_id'])
        self.assertEqual(new_order.service_expires_at.replace(microsecond=0), new_expiry.replace(microsecond=0))
        self.assertEqual(new_order.order_no[:7], 'SRVADMI')

    def test_edit_owner_rebinds_order_and_keeps_expiry_sync(self):
        target_user = TelegramUser.objects.create(
            tg_user_id=990103,
            username='rebound_user',
            balance=Decimal('15.000000'),
            balance_trx=Decimal('1.000000'),
        )
        staff_user = get_user_model().objects.create_user(username='staff_selfcheck_owner', password='x', is_staff=True)
        new_expiry = timezone.now() + timezone.timedelta(days=30)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{self.asset.id}/',
            data=json.dumps({
                'user_id': target_user.id,
                'actual_expires_at': new_expiry.isoformat(),
            }),
            content_type='application/json',
        )
        request.user = staff_user

        response = update_cloud_asset(request, self.asset.id)
        self.assertEqual(response.status_code, 200)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.user_id, target_user.id)
        self.assertEqual(self.asset.actual_expires_at.replace(microsecond=0), new_expiry.replace(microsecond=0))
        new_order = CloudServerOrder.objects.get(id=json.loads(response.content)['data']['order_id'])
        self.assertEqual(new_order.user_id, target_user.id)
        self.assertEqual(new_order.service_expires_at.replace(microsecond=0), new_expiry.replace(microsecond=0))
        self.assertNotEqual(new_order.id, self.order.id)

    def test_auto_renew_picks_helper_or_fails_cleanly(self):
        renewal = async_to_sync(create_cloud_server_renewal_for_user)(self.order.id, self.helper.id, 31)
        self.assertIsNotNone(renewal)
        self.assertEqual(renewal.status, 'renew_pending')

        renewed_order, error, balance_change = async_to_sync(_run_auto_renew)(self.order.id)
        self.assertIsNotNone(renewed_order)
        self.assertIsNone(error)
        self.assertEqual(balance_change['payer_user_id'], self.helper.id)
        self.helper.refresh_from_db()
        self.assertLess(self.helper.balance, Decimal('55.000000'))

        poor_owner = TelegramUser.objects.create(
            tg_user_id=990104,
            username='poor_owner',
            balance=Decimal('0.000000'),
            balance_trx=Decimal('0.000000'),
        )
        poor_helper = TelegramUser.objects.create(
            tg_user_id=990105,
            username='poor_helper',
            balance=Decimal('0.000000'),
            balance_trx=Decimal('0.000000'),
        )
        poor_group = TelegramGroupFilter.objects.create(
            chat_id=-1009876543211,
            title='余额不足群组',
            username='poorcheckgroup',
            enabled=True,
        )
        poor_order = CloudServerOrder.objects.create(
            order_no='SELF-CHECK-ORDER-2',
            user=poor_owner,
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
            public_ip='9.9.9.10',
            instance_id='i-selfcheck-002',
            server_name='selfcheck-server-002',
            service_started_at=timezone.now() - timezone.timedelta(days=8),
            service_expires_at=timezone.now() + timezone.timedelta(hours=18),
            auto_renew_enabled=True,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=poor_order,
            user=poor_owner,
            telegram_group=poor_group,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='poor-selfcheck-asset',
            public_ip='9.9.9.10',
            actual_expires_at=poor_order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            user=poor_helper,
            telegram_group=poor_group,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='poor-selfcheck-helper-asset',
            public_ip='9.9.9.10',
            actual_expires_at=poor_order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
        )

        failed_order, failed_error, failed_meta = async_to_sync(_run_auto_renew)(poor_order.id)
        self.assertIsNone(failed_order)
        self.assertIn('余额不足', failed_error)
        self.assertEqual(failed_meta['candidate_count'], 2)
