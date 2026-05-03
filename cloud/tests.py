import json
from decimal import Decimal
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from bot.api import _shutdown_log_items, _unattached_ip_delete_items
from bot.models import TelegramUser
from cloud.bootstrap import _build_mtproxy_script, _extract_tg_links
from cloud.models import CloudAsset, CloudIpLog, CloudServerOrder, CloudServerPlan, CloudUserNoticeLog, Server
from cloud.lifecycle import _apply_notice_schedule_to_order, _send_logged_cloud_notice
from cloud.ports import get_mtproxy_port_label, get_mtproxy_public_ports, is_valid_mtproxy_main_port
from cloud.provisioning import (
    _candidate_cloud_account_ids,
    _extract_mtproxy_fields,
    _extract_proxy_links,
    _get_aws_create_payload,
    _mark_provisioning_start,
    _mark_rebuild_source_pending_deletion,
    _mark_success,
)
from cloud.services import create_cloud_server_rebuild_order, create_cloud_server_renewal, ensure_cloud_asset_operation_order, mark_cloud_server_ip_change_requested, replace_cloud_asset_order_by_admin
from cloud.api import _cloud_order_source_tags, delete_cloud_asset, delete_server, tasks_overview, update_cloud_asset
from core.models import CloudAccountConfig


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

    def test_create_cloud_server_rebuild_order_reuses_original_static_ip_without_temp(self):
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
        source_order.refresh_from_db()
        self.assertIsNotNone(source_order.migration_due_at)

    def test_rebuild_order_create_payload_skips_static_ip_binding(self):
        source_account = CloudAccountConfig.objects.create(
            provider='aws',
            name='source-account',
            external_account_id='111111111111',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
        )
        other_account = CloudAccountConfig.objects.create(
            provider='aws',
            name='other-account',
            external_account_id='222222222222',
            access_key='ak2',
            secret_key='sk2',
            region_hint='ap-southeast-1',
        )
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-PAYLOAD-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=source_account,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='3.1.169.183',
            static_ip_name='StaticIp-2',
            mtproxy_port=8443,
            mtproxy_secret='ee1234567890abcdef1234567890abcd',
        )
        rebuild_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-PAYLOAD-2',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=source_account,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='paid',
            static_ip_name='StaticIp-2',
            replacement_for=source_order,
        )
        Server.objects.create(
            provider='aws_lightsail',
            account_label=f'aws+{other_account.external_account_id}+{other_account.name}',
            region_code=self.plan.region_code,
            public_ip='3.0.114.174',
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        payload = async_to_sync(_get_aws_create_payload)(rebuild_order.id)
        account_ids = async_to_sync(_candidate_cloud_account_ids)(rebuild_order.id)

        self.assertTrue(payload['skip_static_ip'])
        self.assertEqual(payload['static_ip_name'], '')
        self.assertEqual(payload['cloud_account_id'], source_account.id)
        self.assertEqual(account_ids, [source_account.id])

    def test_rebuild_source_expiry_moves_to_three_day_migration_due(self):
        source = CloudServerOrder.objects.create(
            order_no='REBUILD-SOURCE-EXPIRY',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            account_label='aws+111111111111+primary',
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
            service_expires_at=timezone.now() + timezone.timedelta(days=30),
            migration_due_at=timezone.now() + timezone.timedelta(days=3),
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=source, user=self.user, public_ip='1.2.3.4')
        Server.objects.create(source=Server.SOURCE_ORDER, order=source, user=self.user, public_ip='1.2.3.4')
        replacement = CloudServerOrder.objects.create(
            order_no='REBUILD-NEW-EXPIRY',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            account_label='aws+111111111111+primary',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='5.6.7.8',
            replacement_for=source,
        )

        async_to_sync(_mark_rebuild_source_pending_deletion)(source.id, replacement.id, '旧机保留 3 天后删除。')

        source.refresh_from_db()
        asset = CloudAsset.objects.get(order=source)
        server = Server.objects.get(order=source)
        self.assertEqual(source.service_expires_at, source.migration_due_at)
        self.assertEqual(source.renew_grace_expires_at, source.migration_due_at + timezone.timedelta(days=3))
        self.assertEqual(source.delete_at, source.migration_due_at + timezone.timedelta(days=3))
        self.assertEqual(asset.actual_expires_at, source.migration_due_at)
        self.assertEqual(server.expires_at, source.migration_due_at)

    def test_manual_admin_replace_order_takes_effect_immediately_for_aws_asset(self):
        old_expiry = timezone.now() + timezone.timedelta(days=10)
        new_expiry = timezone.now() + timezone.timedelta(days=40)
        old_order = CloudServerOrder.objects.create(
            order_no='MANUAL-REPLACE-OLD-1',
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
            public_ip='8.8.8.8',
            service_started_at=timezone.now(),
            service_expires_at=old_expiry,
            renew_grace_expires_at=old_expiry + timezone.timedelta(days=3),
            suspend_at=old_expiry + timezone.timedelta(days=3),
            delete_at=old_expiry + timezone.timedelta(days=3),
            ip_recycle_at=old_expiry + timezone.timedelta(days=18),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=old_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='manual-proxy',
            public_ip='8.8.8.8',
            actual_expires_at=old_expiry,
            price='23.00',
        )
        server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=old_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            public_ip='8.8.8.8',
            expires_at=old_expiry,
            is_active=True,
        )
        new_user = TelegramUser.objects.create(tg_user_id=990002, username='svc_target')

        new_order, err = replace_cloud_asset_order_by_admin(
            asset,
            new_user=new_user,
            new_expires_at=new_expiry,
            previous_user=self.user,
            previous_expires_at=old_expiry,
        )

        self.assertIsNone(err)
        self.assertIsNotNone(new_order)
        old_order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(old_order.status, 'cancelled')
        self.assertIsNone(old_order.renew_grace_expires_at)
        self.assertIsNone(old_order.suspend_at)
        self.assertIsNone(old_order.delete_at)
        self.assertIsNone(old_order.ip_recycle_at)
        self.assertIsNotNone(old_order.expired_at)
        self.assertEqual(asset.order_id, new_order.id)
        self.assertEqual(server.order_id, new_order.id)
        self.assertEqual(asset.user_id, new_user.id)
        self.assertEqual(server.user_id, new_user.id)
        self.assertEqual(new_order.user_id, new_user.id)
        self.assertEqual(asset.actual_expires_at, new_expiry)
        self.assertEqual(server.expires_at, new_expiry)
        self.assertEqual(new_order.service_expires_at, new_expiry)
        self.assertEqual(new_order.replacement_for_id, old_order.id)

    def test_manual_admin_replace_order_aggregates_price_change_into_same_order(self):
        old_expiry = timezone.now() + timezone.timedelta(days=10)
        new_expiry = timezone.now() + timezone.timedelta(days=40)
        old_order = CloudServerOrder.objects.create(
            order_no='MANUAL-REPLACE-PRICE-OLD-1',
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
            public_ip='8.8.4.4',
            service_started_at=timezone.now(),
            service_expires_at=old_expiry,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=old_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='manual-price-proxy',
            public_ip='8.8.4.4',
            actual_expires_at=new_expiry,
            price='29.00',
        )

        new_order, err = replace_cloud_asset_order_by_admin(
            asset,
            new_expires_at=new_expiry,
            new_price=asset.price,
            previous_user=self.user,
            previous_expires_at=old_expiry,
            previous_price='19.00',
        )

        self.assertIsNone(err)
        self.assertIsNotNone(new_order)
        old_order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(old_order.status, 'cancelled')
        self.assertEqual(asset.order_id, new_order.id)
        self.assertEqual(Decimal(str(new_order.total_amount)), Decimal('29.00'))
        self.assertEqual(Decimal(str(new_order.pay_amount)), Decimal('29.00'))
        self.assertIn('到期时间', new_order.provision_note or '')
        self.assertIn('价格 19.00 -> 29.00', new_order.provision_note or '')
        tags = _cloud_order_source_tags(new_order)
        self.assertEqual(
            [item[0] for item in tags],
            ['manual_expiry_change', 'manual_price_change'],
        )

    def test_update_cloud_asset_for_aws_creates_single_replace_order_for_expiry_and_price(self):
        old_expiry = timezone.now() + timezone.timedelta(days=10)
        new_expiry = timezone.now() + timezone.timedelta(days=20)
        order = CloudServerOrder.objects.create(
            order_no='MANUAL-UPDATE-PRICE-OLD-1',
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
            public_ip='4.4.4.4',
            service_started_at=timezone.now(),
            service_expires_at=old_expiry,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='manual-update-price-proxy',
            public_ip='4.4.4.4',
            actual_expires_at=old_expiry,
            price='19.00',
        )
        staff_user = get_user_model().objects.create_user(username='staff_api_price_replace', password='x', is_staff=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({
                'price': '29.00',
                'actual_expires_at': new_expiry.isoformat(),
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')
        self.assertEqual(asset.price, Decimal('29.00'))
        self.assertEqual(asset.actual_expires_at, new_expiry)
        replace_orders = CloudServerOrder.objects.filter(replacement_for=order).order_by('id')
        self.assertEqual(replace_orders.count(), 1)
        new_order = replace_orders.get()
        self.assertTrue(new_order.order_no.startswith('SRVADMIN'))
        self.assertEqual(new_order.total_amount, Decimal('29.00'))
        self.assertEqual(new_order.pay_amount, Decimal('29.00'))
        self.assertIn('价格 19.00 -> 29.00', new_order.provision_note or '')
        self.assertEqual(
            CloudServerOrder.objects.filter(order_no__startswith='SRVMANUAL', replacement_for=order).count(),
            0,
        )

    def test_aws_notice_schedule_does_not_override_manual_order_expiry(self):
        order = CloudServerOrder.objects.create(
            order_no='MANUAL-NOTICE-OLD-1',
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
            public_ip='8.8.4.4',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=15),
        )
        manual_expiry = order.service_expires_at
        notice_expiry = timezone.now() + timezone.timedelta(days=5)

        _apply_notice_schedule_to_order(order, {
            'expires_at': notice_expiry,
            'suspend_at': notice_expiry,
            'delete_at': notice_expiry + timezone.timedelta(days=3),
            'ip_recycle_at': notice_expiry + timezone.timedelta(days=7),
        })

        order.refresh_from_db()
        self.assertEqual(order.service_expires_at, manual_expiry)
        self.assertEqual(order.suspend_at, notice_expiry)

    def test_rebuild_payload_prefers_source_account_when_rebuild_order_is_polluted(self):
        source_account = CloudAccountConfig.objects.create(
            provider='aws',
            name='22',
            external_account_id='039612864876',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
        )
        wrong_account = CloudAccountConfig.objects.create(
            provider='aws',
            name='11',
            external_account_id='172678727708',
            access_key='ak2',
            secret_key='sk2',
            region_hint='ap-southeast-1',
        )
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-SOURCE-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=source_account,
            account_label='aws+039612864876+22',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='3.1.169.183',
            static_ip_name='StaticIp-2',
            mtproxy_secret='ee1234567890abcdef1234567890abcd',
        )
        rebuild_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-SOURCE-2',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=wrong_account,
            account_label='aws+172678727708+11',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='paid',
            static_ip_name='StaticIp-2',
            replacement_for=source_order,
        )

        payload = async_to_sync(_get_aws_create_payload)(rebuild_order.id)
        account_ids = async_to_sync(_candidate_cloud_account_ids)(rebuild_order.id)

        self.assertEqual(payload['cloud_account_id'], source_account.id)
        self.assertEqual(payload['account_label'], source_order.account_label)
        self.assertEqual(account_ids, [source_account.id])

    def test_asset_operation_order_resolves_account_from_label(self):
        account = CloudAccountConfig.objects.create(
            provider='aws',
            name='22',
            external_account_id='039612864876',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label='aws+039612864876+22',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='Debian-1',
            instance_id='Debian-1',
            public_ip='3.1.169.183',
            user=self.user,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        order, error = async_to_sync(ensure_cloud_asset_operation_order)(asset.id, self.user.id)

        self.assertIsNone(error)
        self.assertIsNotNone(order)
        self.assertEqual(order.cloud_account_id, account.id)
        self.assertEqual(order.account_label, asset.account_label)

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
        self.assertEqual(source_order.suspend_at, source_order.migration_due_at + timezone.timedelta(days=3))
        self.assertEqual(source_order.delete_at, source_order.migration_due_at + timezone.timedelta(days=3))
        self.assertEqual(source_order.renew_grace_expires_at, source_order.migration_due_at + timezone.timedelta(days=3))
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

    def test_sync_aws_assets_requires_database_cloud_account(self):
        with self.assertRaisesMessage(CommandError, '未添加启用的 AWS 云账号'):
            call_command('sync_aws_assets', region='ap-southeast-1')

    def test_backup_ports_are_fixed(self):
        self.assertTrue(is_valid_mtproxy_main_port(443))
        self.assertFalse(is_valid_mtproxy_main_port(444))
        self.assertFalse(is_valid_mtproxy_main_port(9529))
        self.assertFalse(is_valid_mtproxy_main_port(65531))
        self.assertEqual(get_mtproxy_public_ports(443), [443, 9529, 9530, 9531, 9532, 9533])
        self.assertEqual(get_mtproxy_public_ports(8443), [8443, 9529, 9530, 9531, 9532, 9533])
        self.assertEqual(get_mtproxy_port_label(443, 9529), '备用 mtprotoproxy')

    def test_mtproxy_script_runs_mtg_with_fake_tls_secret(self):
        script = _build_mtproxy_script(443, 'eec3bda48fee649e9ea6e32d33cd5f3dd9617a7572652e6d6963726f736f66742e636f6d')
        self.assertIn('RUN_SECRET="ee${RUN_SECRET}617a7572652e6d6963726f736f66742e636f6d"', script)
        self.assertIn('$WORKDIR/bin/mtg run $RUN_SECRET', script)

    def test_mtproxy_extra_links_exclude_main_port(self):
        links = _extract_tg_links(
            'TG链接: tg://proxy?server=1.2.3.4&port=443&secret=ee11111111111111111111111111111111\n'
            '扩展链接: tg://proxy?server=1.2.3.4&port=443&secret=ee22222222222222222222222222222222\n'
            '扩展链接: tg://proxy?server=1.2.3.4&port=9529&secret=ee33333333333333333333333333333333',
            exclude_port=443,
        )
        self.assertEqual(links, ['tg://proxy?server=1.2.3.4&port=9529&secret=ee33333333333333333333333333333333'])

    def test_extract_proxy_links_labels_custom_low_port_plan(self):
        links = _extract_proxy_links(
            'MTProxy 安装完成\n'
            '端口: 443\n'
            'TG链接: tg://proxy?server=1.2.3.4&port=443&secret=ee1234567890abcdef1234567890abcd\n'
            '扩展链接: tg://proxy?server=1.2.3.4&port=9529&secret=eeabcdefabcdefabcdefabcdefabcdefab\n'
            '扩展链接: tg://proxy?server=1.2.3.4&port=9530&secret=eeabcdefabcdefabcdefabcdefabcdefab'
        )
        self.assertEqual([item['name'] for item in links], ['主代理 mtg', '备用 mtprotoproxy', 'Telemt A 三模式'])

    def test_mark_success_preserves_existing_main_link_when_install_output_lacks_link(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-LINK',
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
            public_ip='1.2.3.4',
            mtproxy_port=443,
            mtproxy_secret='ee1234567890abcdef1234567890abcd',
            mtproxy_link='tg://proxy?server=1.2.3.4&port=443&secret=ee1234567890abcdef1234567890abcd',
            proxy_links=[{'name': '主代理 mtg', 'server': '1.2.3.4', 'port': '443', 'secret': 'ee1234567890abcdef1234567890abcd', 'url': 'tg://proxy?server=1.2.3.4&port=443&secret=ee1234567890abcdef1234567890abcd'}],
        )

        async_to_sync(_mark_success)(
            order.id,
            'sg-test-node-03',
            'ins-003',
            '1.2.3.4',
            'root',
            'pass',
            'MTProxy 安装完成\n状态: 运行正常\n端口: 443',
            '',
        )

        order.refresh_from_db()
        self.assertEqual(order.mtproxy_link, 'tg://proxy?server=1.2.3.4&port=443&secret=ee1234567890abcdef1234567890abcd')
        self.assertEqual(order.mtproxy_secret, 'ee1234567890abcdef1234567890abcd')
        self.assertEqual(order.proxy_links[0]['port'], '443')

    def test_non_aws_manual_asset_edit_updates_existing_order_in_place(self):
        old_expiry = timezone.now() + timezone.timedelta(days=10)
        new_expiry = timezone.now() + timezone.timedelta(days=35)
        aliyun_plan = CloudServerPlan.objects.create(
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            plan_name='Aliyun Lite',
            cpu='2核',
            memory='1GB',
            storage='40GB SSD',
            bandwidth='1TB',
            price='29.00',
            currency='USDT',
            is_active=True,
            sort_order=90,
        )
        order = CloudServerOrder.objects.create(
            order_no='MANUAL-ALIYUN-OLD-1',
            user=self.user,
            plan=aliyun_plan,
            provider=aliyun_plan.provider,
            region_code=aliyun_plan.region_code,
            region_name=aliyun_plan.region_name,
            plan_name=aliyun_plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='29.00',
            pay_amount='29.00',
            pay_method='balance',
            status='completed',
            public_ip='47.1.1.1',
            service_started_at=timezone.now(),
            service_expires_at=old_expiry,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aliyun_simple',
            region_code=aliyun_plan.region_code,
            region_name=aliyun_plan.region_name,
            asset_name='aliyun-proxy',
            public_ip='47.1.1.1',
            actual_expires_at=old_expiry,
            price='29.00',
        )
        new_user = TelegramUser.objects.create(tg_user_id=990003, username='aliyun_target')
        staff_user = get_user_model().objects.create_user(username='staff_api_1', password='x', is_staff=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({
                'user_id': new_user.id,
                'actual_expires_at': new_expiry.isoformat(),
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.user_id, new_user.id)
        self.assertEqual(asset.order_id, order.id)
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order.service_expires_at, new_expiry)
        self.assertEqual(asset.actual_expires_at, new_expiry)
        self.assertFalse(CloudServerOrder.objects.filter(order_no__startswith='SRVADMIN', replacement_for=order).exists())
        owner_audit_order = CloudServerOrder.objects.filter(order_no__startswith='SRVMANUAL', replacement_for=order, user=new_user).exclude(id=order.id).latest('id')
        self.assertEqual(owner_audit_order.service_expires_at, old_expiry)
        self.assertIn('人工编辑所属人', owner_audit_order.provision_note or '')
        self.assertNotIn('人工编辑到期时间', owner_audit_order.provision_note or '')

    def test_manual_order_source_tags_support_multiple_labels_on_same_order(self):
        order = CloudServerOrder.objects.create(
            order_no='SRVMANUAL-MULTI-1',
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
            provision_note='后台人工编辑：人工编辑用户 old -> new；人工编辑价格 19.00 -> 29.00。',
        )

        tags = _cloud_order_source_tags(order)

        self.assertEqual(
            [item[0] for item in tags],
            ['manual_owner_change', 'manual_price_change'],
        )
        self.assertEqual(
            [item[1] for item in tags],
            ['人工改用户', '人工改价格'],
        )

    def test_shutdown_log_items_skip_assets_hidden_from_cloud_asset_list(self):
        inactive_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='inactive-shutdown',
            external_account_id='acct-shutdown-inactive',
            access_key='ak',
            secret_key='sk',
            is_active=False,
        )
        active_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='active-shutdown',
            external_account_id='acct-shutdown-active',
            access_key='ak2',
            secret_key='sk2',
            is_active=True,
        )
        old_expiry = timezone.now() + timezone.timedelta(days=3)
        hidden_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=inactive_account,
            account_label='acct-shutdown-inactive',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='hidden-shutdown-asset',
            public_ip='6.6.6.6',
            actual_expires_at=old_expiry,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        visible_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=active_account,
            account_label='acct-shutdown-active',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='visible-shutdown-asset',
            public_ip='6.6.6.7',
            actual_expires_at=old_expiry,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        items = _shutdown_log_items(limit=20)
        asset_ids = {item.get('asset_id') for item in items}

        self.assertIn(visible_asset.id, asset_ids)
        self.assertNotIn(hidden_asset.id, asset_ids)

    def test_unattached_ip_delete_items_skip_assets_hidden_from_cloud_asset_list(self):
        inactive_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='inactive-unattached',
            external_account_id='acct-unattached-inactive',
            access_key='ak3',
            secret_key='sk3',
            is_active=False,
        )
        active_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='active-unattached',
            external_account_id='acct-unattached-active',
            access_key='ak4',
            secret_key='sk4',
            is_active=True,
        )
        hidden_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=inactive_account,
            account_label='acct-unattached-inactive',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='hidden-unattached-asset',
            public_ip='5.5.5.5',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
        )
        visible_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=active_account,
            account_label='acct-unattached-active',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='visible-unattached-asset',
            public_ip='5.5.5.6',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
        )

        items = _unattached_ip_delete_items(limit=20)
        asset_ids = {item.get('id') for item in items}

        self.assertIn(visible_asset.id, asset_ids)
        self.assertNotIn(hidden_asset.id, asset_ids)

    def test_delete_cloud_asset_only_removes_asset_record(self):
        order = CloudServerOrder.objects.create(
            order_no='DELETE-ASSET-ONLY-1',
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
            public_ip='8.8.8.8',
            instance_id='i-delete-asset-only',
            provider_resource_id='res-delete-asset-only',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='delete-asset-only',
            public_ip=order.public_ip,
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            actual_expires_at=order.service_expires_at,
            price='19.00',
            status=CloudAsset.STATUS_RUNNING,
        )
        server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='delete-asset-only-server',
            public_ip=order.public_ip,
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=Server.STATUS_RUNNING,
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_delete_only', password='x', is_staff=True)
        request = RequestFactory().post(f'/api/dashboard/cloud-assets/{asset.id}/delete/')
        request.user = staff_user

        response = delete_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertFalse(CloudAsset.objects.filter(id=asset.id).exists())
        self.assertTrue(Server.objects.filter(id=server.id).exists())
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order.public_ip, '8.8.8.8')
        self.assertEqual(order.instance_id, 'i-delete-asset-only')

    def test_delete_cloud_asset_also_removes_residual_server_record(self):
        order = CloudServerOrder.objects.create(
            order_no='DELETE-ASSET-RESIDUAL-1',
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
            status='deleted',
            public_ip=None,
            previous_public_ip='8.8.4.4',
            instance_id='i-delete-asset-residual',
            provider_resource_id='res-delete-asset-residual',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='delete-asset-residual',
            public_ip=None,
            previous_public_ip='8.8.4.4',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            actual_expires_at=order.service_expires_at,
            price='19.00',
            status=CloudAsset.STATUS_DELETED,
            provider_status='云上未找到实例/IP',
            is_active=False,
            note='状态: 云上未找到实例/IP',
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='delete-asset-residual-server',
            public_ip=None,
            previous_public_ip='8.8.4.4',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=Server.STATUS_DELETED,
            provider_status='云上未找到实例/IP',
            is_active=False,
            note='状态: 云上未找到实例/IP',
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_delete_residual', password='x', is_staff=True)
        request = RequestFactory().post(f'/api/dashboard/cloud-assets/{asset.id}/delete/')
        request.user = staff_user

        response = delete_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertFalse(CloudAsset.objects.filter(id=asset.id).exists())
        self.assertFalse(Server.objects.filter(id=server.id).exists())
        self.assertEqual(payload['data']['removed_servers'], 1)
        self.assertEqual(payload['data']['removed_server_ids'], [server.id])

    def test_reconcile_cloud_assets_skips_deleted_server_residual(self):
        order = CloudServerOrder.objects.create(
            order_no='RECONCILE-DELETED-SERVER-1',
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
            status='deleted',
            public_ip=None,
            previous_public_ip='7.7.7.7',
            instance_id='i-reconcile-deleted-server',
            provider_resource_id='res-reconcile-deleted-server',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )
        Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='reconcile-deleted-server',
            public_ip=None,
            previous_public_ip='7.7.7.7',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=Server.STATUS_DELETED,
            provider_status='云上未找到实例/IP',
            is_active=False,
            note='状态: 云上未找到实例/IP',
        )

        call_command('reconcile_cloud_assets_from_servers')

        self.assertFalse(
            CloudAsset.objects.filter(
                instance_id='i-reconcile-deleted-server',
                provider_resource_id='res-reconcile-deleted-server',
            ).exists()
        )

    def test_delete_server_only_removes_server_record(self):
        order = CloudServerOrder.objects.create(
            order_no='DELETE-SERVER-ONLY-1',
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
            public_ip='9.9.9.9',
            instance_id='i-delete-server-only',
            provider_resource_id='res-delete-server-only',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='delete-server-only-asset',
            public_ip=order.public_ip,
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            actual_expires_at=order.service_expires_at,
            price='19.00',
            status=CloudAsset.STATUS_RUNNING,
        )
        server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='delete-server-only',
            public_ip=order.public_ip,
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=Server.STATUS_RUNNING,
        )
        staff_user = get_user_model().objects.create_user(username='staff_server_delete_only', password='x', is_staff=True)
        request = RequestFactory().post(f'/api/dashboard/servers/{server.id}/delete/')
        request.user = staff_user

        response = delete_server(request, server.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertFalse(Server.objects.filter(id=server.id).exists())
        self.assertTrue(CloudAsset.objects.filter(id=asset.id).exists())
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order.public_ip, '9.9.9.9')
        self.assertEqual(order.instance_id, 'i-delete-server-only')

    def test_delete_server_does_not_fallback_to_asset_id(self):
        order = CloudServerOrder.objects.create(
            order_no='DELETE-SERVER-NO-FALLBACK-1',
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
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='delete-server-no-fallback',
            price='19.00',
            status=CloudAsset.STATUS_RUNNING,
        )
        staff_user = get_user_model().objects.create_user(username='staff_server_delete_no_fallback', password='x', is_staff=True)
        request = RequestFactory().post(f'/api/dashboard/servers/{asset.id}/delete/')
        request.user = staff_user

        response = delete_server(request, asset.id)

        self.assertEqual(response.status_code, 404)
        self.assertTrue(CloudAsset.objects.filter(id=asset.id).exists())

    def test_send_logged_cloud_notice_deduplicates_same_event_and_order(self):
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-DEDUPE-1',
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
            public_ip='8.8.8.9',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=12),
        )
        sent = []

        async def fake_notify(user_id, text, reply_markup=None):
            sent.append((user_id, text))
            return True

        result1 = async_to_sync(_send_logged_cloud_notice)('renew_notice', fake_notify, self.user.id, 'hello', None, order=order, notice={'ip': '8.8.8.9'})
        result2 = async_to_sync(_send_logged_cloud_notice)('renew_notice', fake_notify, self.user.id, 'hello', None, order=order, notice={'ip': '8.8.8.9'})

        self.assertTrue(result1)
        self.assertFalse(result2)
        self.assertEqual(len(sent), 1)
        self.assertEqual(CloudUserNoticeLog.objects.filter(event_type='renew_notice', user=self.user, order=order, delivered=True).count(), 1)

    def test_tasks_overview_exposes_click_paths_for_entry_and_order_number(self):
        order = CloudServerOrder.objects.create(
            order_no='TASK-LINK-1',
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
            status='provisioning',
            public_ip='1.1.1.1',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=5),
            auto_renew_enabled=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_api_2', password='x', is_staff=True)
        request = RequestFactory().get('/api/dashboard/tasks/')
        request.user = staff_user

        response = tasks_overview(request)
        payload = json.loads(response.content)
        items = payload.get('data') or payload
        pinned = next(item for item in items if item['id'] == -10001)
        regular = next(item for item in items if item['id'] == order.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(pinned['detail_path'], '/admin/tasks/auto-renew')
        self.assertEqual(pinned['order_link_path'], '/admin/tasks/auto-renew')
        self.assertEqual(regular['detail_path'], f'/admin/cloud-orders/{order.id}')
        self.assertEqual(regular['order_detail_path'], f'/admin/cloud-orders/{order.id}')

    def test_cloud_asset_detail_exposes_related_order_click_path(self):
        order = CloudServerOrder.objects.create(
            order_no='ASSET-DETAIL-ORDER-1',
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
            public_ip='2.2.2.2',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=8),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='asset-detail-proxy',
            public_ip='2.2.2.2',
            actual_expires_at=order.service_expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_api_3', password='x', is_staff=True)
        request = RequestFactory().get(f'/api/dashboard/cloud-assets/{asset.id}/')
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)
        payload = json.loads(response.content)
        data = payload.get('data') or payload

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['order_detail_path'], f'/admin/cloud-orders/{order.id}')
        self.assertEqual(data['order_link_path'], f'/admin/cloud-orders/{order.id}')
        self.assertEqual(data['related_order']['order_link_path'], f'/admin/cloud-orders/{order.id}')

    def test_cloud_asset_detail_exposes_history_orders_with_click_paths(self):
        root_order = CloudServerOrder.objects.create(
            order_no='ASSET-HISTORY-ROOT-1',
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
            status='cancelled',
            public_ip='3.3.3.3',
            service_started_at=timezone.now() - timezone.timedelta(days=20),
            service_expires_at=timezone.now() - timezone.timedelta(days=5),
        )
        newer_order = CloudServerOrder.objects.create(
            order_no='ASSET-HISTORY-NEW-1',
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
            public_ip='3.3.3.3',
            service_started_at=timezone.now() - timezone.timedelta(days=4),
            service_expires_at=timezone.now() + timezone.timedelta(days=20),
            replacement_for=root_order,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=newer_order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='asset-history-proxy',
            public_ip='3.3.3.3',
            actual_expires_at=newer_order.service_expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_api_4', password='x', is_staff=True)
        request = RequestFactory().get(f'/api/dashboard/cloud-assets/{asset.id}/')
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)
        payload = json.loads(response.content)
        data = payload.get('data') or payload
        history_orders = data['history_orders']

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(history_orders), 2)
        self.assertEqual(history_orders[0]['order_link_path'], f"/admin/cloud-orders/{history_orders[0]['id']}")
        self.assertTrue(any(item['id'] == root_order.id for item in history_orders))
        root_item = next(item for item in history_orders if item['id'] == root_order.id)
        self.assertEqual(root_item['order_detail_path'], f'/admin/cloud-orders/{root_order.id}')
