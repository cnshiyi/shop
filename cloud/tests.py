from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TestCase
from django.utils import timezone

from bot.models import TelegramUser
from cloud.models import CloudAsset, CloudIpLog, CloudServerOrder, CloudServerPlan, Server
from cloud.provisioning import _extract_mtproxy_fields, _mark_provisioning_start, _mark_success
from cloud.services import create_cloud_server_rebuild_order, create_cloud_server_renewal, mark_cloud_server_ip_change_requested


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

    def test_create_cloud_server_rebuild_order_preserves_static_ip_and_secret(self):
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-1',
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
            static_ip_name='hb-static-ip',
            mtproxy_port=8443,
            mtproxy_secret='ee1234567890abcdef1234567890abcd',
            mtproxy_link='tg://proxy?server=1.2.3.4&port=8443&secret=ee1234567890abcdef1234567890abcd',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )

        new_order, error = create_cloud_server_rebuild_order(source_order.id)

        self.assertIsNone(error)
        self.assertIsNotNone(new_order)
        self.assertEqual(new_order.replacement_for_id, source_order.id)
        self.assertEqual(new_order.static_ip_name, source_order.static_ip_name)
        self.assertEqual(new_order.mtproxy_secret, source_order.mtproxy_secret)
        self.assertEqual(new_order.mtproxy_port, source_order.mtproxy_port)
        self.assertEqual(new_order.status, 'paid')

    def test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing(self):
        original_expires_at = timezone.now() + timezone.timedelta(days=31)
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
            service_expires_at=original_expires_at,
        )

        new_order = async_to_sync(mark_cloud_server_ip_change_requested)(source_order.id, self.user.id)

        source_order.refresh_from_db()
        self.assertTrue(new_order)
        self.assertEqual(new_order.plan_id, self.plan.id)
        self.assertEqual(new_order.replacement_for_id, source_order.id)
        self.assertEqual(new_order.service_expires_at, original_expires_at)
        self.assertIsNotNone(source_order.migration_due_at)
        self.assertEqual(source_order.service_expires_at, source_order.migration_due_at)
        self.assertEqual(source_order.suspend_at, source_order.migration_due_at)
        self.assertEqual(source_order.delete_at, source_order.migration_due_at)
        self.assertEqual(source_order.renew_grace_expires_at, source_order.migration_due_at)
        self.assertEqual(
            source_order.ip_recycle_at,
            source_order.delete_at + timezone.timedelta(days=15),
        )

    def test_mark_provisioning_start_creates_pending_asset_server_and_log(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-PROVISION-1',
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
        )

        async_to_sync(_mark_provisioning_start)(order.id, 'sg-test-node-01')

        order.refresh_from_db()
        asset = CloudAsset.objects.get(order=order, kind=CloudAsset.KIND_SERVER)
        server = Server.objects.get(order=order)
        log = CloudIpLog.objects.filter(order=order).latest('id')

        self.assertEqual(order.status, 'provisioning')
        self.assertEqual(order.server_name, 'sg-test-node-01')
        self.assertEqual(asset.status, CloudAsset.STATUS_PENDING)
        self.assertTrue(asset.is_active)
        self.assertEqual(server.status, Server.STATUS_PENDING)
        self.assertTrue(server.is_active)
        self.assertEqual(log.event_type, CloudIpLog.EVENT_CREATED)
        self.assertIn('服务器开始创建', log.note)

    def test_extract_mtproxy_fields_keeps_fake_tls_secret_and_link(self):
        link, secret, host = _extract_mtproxy_fields(
            'MTProxy 安装完成\n'
            '状态: 运行正常\n'
            '端口: 8443\n'
            'TG链接: tg://proxy?server=1.2.3.4&port=8443&secret=ee1234567890abcdef1234567890abcd617a7572652e6d6963726f736f66742e636f6d\n'
            '分享链接: https://t.me/proxy?server=1.2.3.4&port=8443&secret=ee1234567890abcdef1234567890abcd617a7572652e6d6963726f736f66742e636f6d'
        )
        self.assertEqual(host, '1.2.3.4')
        self.assertEqual(link, 'tg://proxy?server=1.2.3.4&port=8443&secret=ee1234567890abcdef1234567890abcd617a7572652e6d6963726f736f66742e636f6d')
        self.assertEqual(secret, 'ee1234567890abcdef1234567890abcd617a7572652e6d6963726f736f66742e636f6d')

    def test_mark_success_updates_existing_server_asset_instead_of_creating_duplicate(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-PROVISION-2',
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
            mtproxy_port=8443,
        )

        async_to_sync(_mark_provisioning_start)(order.id, 'sg-test-node-02')
        async_to_sync(_mark_success)(
            order.id,
            'sg-test-node-02',
            'ins-001',
            '1.2.3.4',
            'root',
            'pass',
            'TG链接: tg://proxy?server=1.2.3.4&port=8443&secret=ee1234567890abcdef1234567890abcd',
            '',
        )

        self.assertEqual(CloudAsset.objects.filter(order=order, kind=CloudAsset.KIND_SERVER).count(), 1)
        asset = CloudAsset.objects.get(order=order, kind=CloudAsset.KIND_SERVER)
        self.assertEqual(asset.instance_id, 'ins-001')
        self.assertEqual(asset.public_ip, '1.2.3.4')
        self.assertIn('tg://proxy?', asset.mtproxy_link or '')
        self.assertEqual(asset.mtproxy_port, 8443)
