import json
import os
from decimal import Decimal
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from bot.api import _shutdown_log_items, _unattached_ip_delete_items
from bot.models import TelegramGroupFilter, TelegramUser
from cloud.bootstrap import _build_mtproxy_script, _extract_tg_links
from cloud.models import CloudAsset, CloudAutoRenewPatrolLog, CloudIpLog, CloudServerOrder, CloudServerPlan, CloudUserNoticeLog, Server
from cloud.lifecycle import _apply_notice_schedule_to_order, _get_due_orders, _get_migration_due_orders, _get_orphan_asset_delete_due, _is_cloud_delete_safe_time, _is_cloud_suspend_time, _mark_suspended, _next_cloud_action_run_at, _notice_plan_text, _send_logged_cloud_notice, lifecycle_tick, sync_server_status_tick
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
from cloud.services import apply_cloud_server_renewal, create_cloud_server_rebuild_order, create_cloud_server_renewal, create_cloud_server_upgrade_order, delay_cloud_server_expiry, ensure_cloud_asset_operation_order, list_cloud_server_upgrade_plans, list_retained_ip_renewal_plans, mark_cloud_server_ip_change_requested, replace_cloud_asset_order_by_admin
from cloud.sync_safety import get_missing_confirmation_threshold
from cloud.api import _cloud_order_source_tags, auto_renew_task_detail, cloud_order_detail, cloud_orders_list, delete_cloud_asset, delete_server, run_auto_renew_order, run_auto_renew_tasks, sync_cloud_asset_status, tasks_overview, update_cloud_asset
from core.cloud_accounts import cloud_account_label
from core.models import CloudAccountConfig
from orders.payment_scanner import _confirm_cloud_server_order


class CloudServerServicesTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
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

    def test_update_cloud_asset_rejects_collapsed_telegram_group_binding(self):
        admin = get_user_model().objects.create_user(username='admin_bind_group', password='x', is_staff=True)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='bind-group-asset',
            public_ip='11.11.11.11',
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
        )
        visible_group = TelegramGroupFilter.objects.create(
            chat_id=-1001001,
            title='Visible Group',
            username='visible_group',
            enabled=False,
            collapsed=False,
        )
        hidden_group = TelegramGroupFilter.objects.create(
            chat_id=-1001002,
            title='Hidden Group',
            username='hidden_group',
            enabled=False,
            collapsed=True,
        )

        request = self.factory.post(
            '/api/dashboard/cloud-assets/%s/' % asset.id,
            data=json.dumps({'telegram_group_query': hidden_group.chat_id}),
            content_type='application/json',
        )
        request.user = admin
        response = update_cloud_asset(request, asset.id)
        self.assertEqual(response.status_code, 404)
        self.assertIn('绑定页隐藏', json.loads(response.content.decode('utf-8'))['message'])

        request2 = self.factory.post(
            '/api/dashboard/cloud-assets/%s/' % asset.id,
            data=json.dumps({'telegram_group_query': visible_group.chat_id}),
            content_type='application/json',
        )
        request2.user = admin
        response2 = update_cloud_asset(request2, asset.id)
        self.assertEqual(response2.status_code, 200)
        asset.refresh_from_db()
        self.assertEqual(asset.telegram_group_id, visible_group.id)

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

    def test_apply_cloud_server_renewal_keeps_original_service_started_at(self):
        original_started_at = timezone.now() - timezone.timedelta(days=20)
        original_expiry = timezone.now() + timezone.timedelta(days=10)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RENEW-KEEP-STARTED',
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
            public_ip='8.8.4.8',
            service_started_at=original_started_at,
            service_expires_at=original_expiry,
        )
        with patch('cloud.services._renew_aliyun_instance', return_value=(True, 'ok')), patch('cloud.services._ensure_aws_instance_running', return_value=(False, 'skip start')):
            renewed = async_to_sync(apply_cloud_server_renewal)(order.id, 31, False)

        renewed.refresh_from_db()
        self.assertEqual(renewed.service_started_at, original_started_at)
        self.assertGreater(renewed.service_expires_at, original_expiry)

    def test_address_renewal_failure_rolls_back_paid_fields(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-ADDR-RENEW-FAIL',
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
            status='renew_pending',
            public_ip='8.8.8.8',
            instance_id='',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=3),
            lifecycle_days=31,
        )

        confirmed = async_to_sync(_confirm_cloud_server_order)(order.id, 'tx-renew-fail', 'payer', 'receiver')

        self.assertIsNone(confirmed)
        order.refresh_from_db()
        self.assertEqual(order.status, 'renew_pending')
        self.assertIsNone(order.paid_at)
        self.assertIsNone(order.tx_hash)
        self.assertEqual(order.payer_address or '', '')
        self.assertEqual(order.receive_address or '', '')

    def test_cloud_upgrade_wallet_payment_is_idempotent(self):
        self.user.balance = Decimal('100.000000')
        self.user.save(update_fields=['balance', 'updated_at'])
        target_plan = CloudServerPlan.objects.create(
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name='Large 2G 60G 3TB',
            cpu='2核',
            memory='2GB',
            storage='60GB SSD',
            bandwidth='3TB',
            price='29.00',
            currency='USDT',
            is_active=True,
            sort_order=101,
        )
        source = CloudServerOrder.objects.create(
            order_no='HB-TEST-UPGRADE-SOURCE',
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
            previous_public_ip='8.8.4.4',
            instance_id='upgrade-source-instance',
            static_ip_name='StaticIp-upgrade-source',
            mtproxy_port=9528,
            mtproxy_secret='0123456789abcdef0123456789abcdef',
            mtproxy_link='tg://proxy?server=8.8.4.4&port=9528&secret=0123456789abcdef0123456789abcdef',
            proxy_links=[{'label': '主链路', 'url': 'tg://proxy?server=8.8.4.4&port=9528&secret=0123456789abcdef0123456789abcdef'}],
            service_started_at=timezone.now() - timezone.timedelta(days=1),
            service_expires_at=timezone.now() + timezone.timedelta(days=1),
        )

        first_order, first_err = async_to_sync(create_cloud_server_upgrade_order)(source.id, self.user.id, target_plan.id)
        balance_after_first = TelegramUser.objects.get(id=self.user.id).balance
        second_order, second_err = async_to_sync(create_cloud_server_upgrade_order)(source.id, self.user.id, target_plan.id)

        self.assertIsNotNone(first_order)
        self.assertIsNone(first_err)
        self.assertIsNone(second_order)
        self.assertIn('已有配置调整任务', second_err)
        self.assertEqual(CloudServerOrder.objects.filter(replacement_for=source).count(), 1)
        self.assertEqual(TelegramUser.objects.get(id=self.user.id).balance, balance_after_first)

    def test_cloud_config_change_lists_and_creates_downgrade_order(self):
        small_plan = CloudServerPlan.objects.create(
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name='Nano 512M 20G 1TB',
            cpu='1核',
            memory='512MB',
            storage='20GB SSD',
            bandwidth='1TB',
            price='10.00',
            currency='USDT',
            is_active=True,
            sort_order=99,
        )
        source = CloudServerOrder.objects.create(
            order_no='HB-TEST-DOWNGRADE-SOURCE',
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
            public_ip='8.8.4.5',
            previous_public_ip='8.8.4.5',
            instance_id='downgrade-source-instance',
            static_ip_name='StaticIp-downgrade-source',
            mtproxy_port=9528,
            mtproxy_secret='0123456789abcdef0123456789abcdef',
            mtproxy_link='tg://proxy?server=8.8.4.5&port=9528&secret=0123456789abcdef0123456789abcdef',
            proxy_links=[{'label': '主链路', 'url': 'tg://proxy?server=8.8.4.5&port=9528&secret=0123456789abcdef0123456789abcdef'}],
            service_started_at=timezone.now() - timezone.timedelta(days=1),
            service_expires_at=timezone.now() + timezone.timedelta(days=1),
        )

        plans, err = async_to_sync(list_cloud_server_upgrade_plans)(source.id, self.user.id)
        new_order, create_err = async_to_sync(create_cloud_server_upgrade_order)(source.id, self.user.id, small_plan.id)

        self.assertIsNone(err)
        self.assertTrue(any(plan['id'] == small_plan.id and plan['action'] == 'downgrade' for plan in plans))
        self.assertIsNone(create_err)
        self.assertIsNotNone(new_order)
        self.assertEqual(new_order.plan_id, small_plan.id)
        self.assertEqual(new_order.pay_amount, Decimal('0.000000000'))
        self.assertIn('DOWNGRADE', new_order.order_no)

    def test_cloud_config_change_ceil_custom_price_to_plan_tier(self):
        self.user.balance = Decimal('100.000000')
        self.user.save(update_fields=['balance', 'updated_at'])
        small_plan = CloudServerPlan.objects.create(
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name='Nano 512M 20G 1TB',
            cpu='1核',
            memory='512MB',
            storage='20GB SSD',
            bandwidth='1TB',
            price='10.00',
            currency='USDT',
            is_active=True,
            sort_order=99,
        )
        large_plan = CloudServerPlan.objects.create(
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name='Large 2G 60G 3TB',
            cpu='2核',
            memory='2GB',
            storage='60GB SSD',
            bandwidth='3TB',
            price='29.00',
            currency='USDT',
            is_active=True,
            sort_order=101,
        )
        source = CloudServerOrder.objects.create(
            order_no='HB-TEST-CEIL-PRICE-SOURCE',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='15.00',
            pay_amount='15.00',
            pay_method='balance',
            status='completed',
            public_ip='8.8.4.6',
            previous_public_ip='8.8.4.6',
            instance_id='ceil-source-instance',
            static_ip_name='StaticIp-ceil-source',
            mtproxy_port=9528,
            mtproxy_secret='0123456789abcdef0123456789abcdef',
            mtproxy_link='tg://proxy?server=8.8.4.6&port=9528&secret=0123456789abcdef0123456789abcdef',
            proxy_links=[{'label': '主链路', 'url': 'tg://proxy?server=8.8.4.6&port=9528&secret=0123456789abcdef0123456789abcdef'}],
            service_started_at=timezone.now() - timezone.timedelta(days=1),
            service_expires_at=timezone.now() + timezone.timedelta(days=1),
        )

        plans, err = async_to_sync(list_cloud_server_upgrade_plans)(source.id, self.user.id)
        large = next(plan for plan in plans if plan['id'] == large_plan.id)
        same_order, same_err = async_to_sync(create_cloud_server_upgrade_order)(source.id, self.user.id, self.plan.id)
        large_order, large_err = async_to_sync(create_cloud_server_upgrade_order)(source.id, self.user.id, large_plan.id)

        self.assertIsNone(err)
        self.assertTrue(any(plan['id'] == small_plan.id and plan['action'] == 'downgrade' for plan in plans))
        self.assertEqual(large['diff'], '10.000')
        self.assertIsNone(same_order)
        self.assertEqual(same_err, '目标套餐与当前配置相同')
        self.assertIsNone(large_err)
        self.assertEqual(large_order.pay_amount, Decimal('10.000000000'))

    def test_due_orders_use_order_expiry_for_lightsail_instead_of_stale_asset_expiry(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-DUE-1',
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
            public_ip='10.0.0.1',
            service_started_at=timezone.now() - timezone.timedelta(days=1),
            service_expires_at=timezone.now() + timezone.timedelta(days=10),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='stale-expired-asset',
            public_ip='10.0.0.9',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )

        due = async_to_sync(_get_due_orders)()

        self.assertFalse(any(item.id == order.id for item in due['expire']))
        self.assertFalse(any(item.id == order.id for item in due['suspend']))
        self.assertFalse(any(item.id == order.id for item in due['delete']))

    def test_due_orders_skip_suspend_when_account_shutdown_disabled(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='shutdown-off',
            external_account_id='acct-shutdown-off',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            shutdown_enabled=False,
        )
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-SUSPEND-OFF-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='10.0.0.21',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(days=5),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            cloud_account=account,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='shutdown-off-asset',
            public_ip='10.0.0.21',
            actual_expires_at=timezone.now() - timezone.timedelta(days=5),
            is_active=True,
        )

        due = async_to_sync(_get_due_orders)()

        self.assertFalse(any(item.id == order.id for item in due['suspend']))
        self.assertTrue(any(item.id == order.id for item in due['expire']))

    def test_due_orders_include_order_expiry_when_asset_expiry_missing(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-ORDER-EXPIRY-FALLBACK',
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
            public_ip='10.0.0.23',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(hours=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='order-expiry-fallback-asset',
            public_ip='10.0.0.23',
            actual_expires_at=None,
            is_active=True,
        )

        due = async_to_sync(_get_due_orders)()

        self.assertTrue(any(item.id == order.id for item in due['expire']))

    def test_due_orders_respect_deferred_suspend_at(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-DEFERRED-SUSPEND',
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
            status='expiring',
            public_ip='10.0.0.24',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(days=5),
        )
        deferred_suspend_at = timezone.now() + timezone.timedelta(hours=6)
        CloudServerOrder.objects.filter(id=order.id).update(suspend_at=deferred_suspend_at)
        order.refresh_from_db()
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='deferred-suspend-asset',
            public_ip='10.0.0.24',
            actual_expires_at=order.service_expires_at,
            is_active=True,
        )

        due = async_to_sync(_get_due_orders)()

        self.assertFalse(any(item.id == order.id for item in due['suspend']))

    def test_delay_cloud_server_expiry_persists_lifecycle_fields(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-DELAY-PERSIST',
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
            public_ip='10.0.0.25',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(days=1),
            delay_quota=1,
        )
        old_suspend_at = order.suspend_at

        result, err = async_to_sync(delay_cloud_server_expiry)(order.id, self.user.id, days=5)

        self.assertIsNone(err)
        self.assertIsNotNone(result)
        order.refresh_from_db()
        self.assertEqual(order.renew_extension_days, 5)
        self.assertEqual(order.delay_quota, 0)
        self.assertGreater(order.suspend_at, old_suspend_at + timezone.timedelta(days=4))
        self.assertEqual(order.renew_grace_expires_at, order.suspend_at)
        self.assertGreaterEqual(order.delete_at, order.suspend_at)
        self.assertGreater(order.ip_recycle_at, order.delete_at)

    def test_orphan_rebound_asset_waiting_manual_time_is_not_delete_due(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='orphan-rebound-wait-time',
            public_ip='10.0.0.26',
            instance_id='i-orphan-rebound-wait-time',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            provider_status='已重新绑定实例-待人工添加时间',
            note='未附加IP已重新绑定到实例，等待人工添加真实到期时间。',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        due = async_to_sync(_get_orphan_asset_delete_due)()

        self.assertFalse(any(item.id == asset.id for item in due))

    def test_delay_cloud_server_expiry_accumulates_days(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-DELAY-ACCUMULATE',
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
            public_ip='10.0.0.27',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(days=1),
            delay_quota=2,
            renew_extension_days=2,
        )

        result, err = async_to_sync(delay_cloud_server_expiry)(order.id, self.user.id, days=3)

        self.assertIsNone(err)
        self.assertIsNotNone(result)
        order.refresh_from_db()
        self.assertEqual(order.renew_extension_days, 5)
        self.assertEqual(order.delay_quota, 1)

    def test_due_orders_restore_suspend_after_account_shutdown_reenabled(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='shutdown-on',
            external_account_id='acct-shutdown-on',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            shutdown_enabled=False,
        )
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-SUSPEND-ON-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='10.0.0.22',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(days=5),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            cloud_account=account,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='shutdown-on-asset',
            public_ip='10.0.0.22',
            actual_expires_at=timezone.now() - timezone.timedelta(days=5),
            is_active=True,
        )

        self.assertFalse(any(item.id == order.id for item in async_to_sync(_get_due_orders)()['suspend']))

        account.shutdown_enabled = True
        account.save(update_fields=['shutdown_enabled', 'updated_at'])

        due = async_to_sync(_get_due_orders)()

        self.assertTrue(any(item.id == order.id for item in due['suspend']))

    def test_mark_suspended_only_updates_latest_asset_and_server(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-SUSPEND-1',
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
            public_ip='10.0.0.2',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(days=5),
        )
        stale_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='stale-asset',
            public_ip='10.0.0.3',
            actual_expires_at=timezone.now() - timezone.timedelta(days=6),
            is_active=True,
        )
        active_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='active-asset',
            public_ip='10.0.0.2',
            actual_expires_at=timezone.now() - timezone.timedelta(days=5),
            is_active=True,
        )
        stale_server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='stale-server',
            public_ip='10.0.0.3',
            is_active=True,
        )
        active_server = Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='active-server',
            public_ip='10.0.0.2',
            is_active=True,
        )

        async_to_sync(_mark_suspended)(order.id, 'unit-test suspend')

        stale_asset.refresh_from_db()
        active_asset.refresh_from_db()
        stale_server.refresh_from_db()
        active_server.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(order.status, 'suspended')
        self.assertTrue(stale_asset.is_active)
        self.assertFalse(active_asset.is_active)
        self.assertTrue(stale_server.is_active)
        self.assertFalse(active_server.is_active)
        self.assertIn('unit-test suspend', active_asset.note)
        self.assertIn('unit-test suspend', active_server.note)

    def test_cloud_action_time_only_runs_in_configured_window(self):
        base = timezone.localtime(timezone.now()).replace(hour=15, minute=5, second=0, microsecond=0)
        with patch('cloud.lifecycle._config_time', return_value=(15, 0)):
            self.assertTrue(_is_cloud_suspend_time(now=base))
            self.assertTrue(_is_cloud_delete_safe_time(now=base))
            self.assertFalse(_is_cloud_suspend_time(now=base.replace(minute=11)))
            self.assertFalse(_is_cloud_delete_safe_time(now=base.replace(minute=11)))

    def test_next_cloud_action_run_at_sticks_to_configured_time(self):
        base = timezone.localtime(timezone.now()).replace(hour=16, minute=20, second=0, microsecond=0)
        with patch('cloud.lifecycle._config_time', return_value=(15, 0)):
            run_at = _next_cloud_action_run_at('cloud_suspend_time', '15:00', now=base, min_delay_seconds=3600)
        self.assertEqual((run_at.hour, run_at.minute), (15, 0))
        self.assertGreater(run_at, base + timezone.timedelta(seconds=3600))

    def test_notice_plan_text_shows_configured_execution_time(self):
        order = CloudServerOrder.objects.create(
            order_no='PLAN-TEXT-1',
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
            service_expires_at=timezone.now() + timezone.timedelta(days=1),
            suspend_at=timezone.now() + timezone.timedelta(days=4),
            delete_at=timezone.now() + timezone.timedelta(days=4, hours=1),
        )
        with patch('cloud.lifecycle._config_time', side_effect=[(15, 30), (16, 45)]):
            text = _notice_plan_text(order)
        self.assertIn('关机计划:', text)
        self.assertIn('后台执行时间 15:30', text)
        self.assertIn('后台执行时间 16:45', text)

    def test_get_migration_due_orders_is_distinct(self):
        old_order = CloudServerOrder.objects.create(
            order_no='HB-MIGRATION-OLD-1',
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
            public_ip='10.0.1.1',
            migration_due_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        CloudServerOrder.objects.create(
            order_no='HB-MIGRATION-NEW-1',
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
            public_ip='10.0.1.2',
            replacement_for=old_order,
        )
        CloudServerOrder.objects.create(
            order_no='HB-MIGRATION-NEW-2',
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
            public_ip='10.0.1.3',
            replacement_for=old_order,
        )

        due_orders = async_to_sync(_get_migration_due_orders)()

        self.assertEqual([item.id for item in due_orders], [old_order.id])

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

    def test_dashboard_order_expiry_update_recomputes_lifecycle_plan(self):
        old_expiry = timezone.now() + timezone.timedelta(days=1)
        new_expiry = timezone.now() + timezone.timedelta(days=20)
        order = CloudServerOrder.objects.create(
            order_no='DASH-ORDER-EXPIRY-UPDATE-1',
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
            public_ip='4.4.4.5',
            service_started_at=timezone.now(),
            service_expires_at=old_expiry,
        )
        old_suspend_at = order.suspend_at
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='dash-order-expiry-update-asset',
            public_ip='4.4.4.5',
            actual_expires_at=old_expiry,
        )
        Server.objects.create(
            source=Server.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            server_name='dash-order-expiry-update-server',
            public_ip='4.4.4.5',
            expires_at=old_expiry,
        )
        staff_user = get_user_model().objects.create_user(username='staff_order_expiry_update', password='x', is_staff=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-orders/{order.id}/',
            data=json.dumps({'service_expires_at': new_expiry.isoformat()}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = cloud_order_detail(request, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset = CloudAsset.objects.get(order=order)
        server = Server.objects.get(order=order)
        self.assertEqual(order.service_expires_at, CloudServerOrder.normalize_expiry_time(new_expiry))
        self.assertGreater(order.suspend_at, old_suspend_at)
        self.assertEqual(order.renew_grace_expires_at, order.suspend_at)
        self.assertGreaterEqual(order.delete_at, order.suspend_at)
        self.assertGreater(order.ip_recycle_at, order.delete_at)
        self.assertEqual(asset.actual_expires_at, order.service_expires_at)
        self.assertEqual(server.expires_at, order.service_expires_at)

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

    def test_unattached_asset_operation_order_enters_retained_renewal_flow(self):
        due_at = timezone.now() + timezone.timedelta(days=9)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-retained-flow',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-retained-flow',
            public_ip='31.31.31.31',
            previous_public_ip='31.31.31.31',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            mtproxy_port=9528,
            mtproxy_link='tg://proxy?server=31.31.31.31&port=9528&secret=dddddddddddddddd',
            mtproxy_secret='dddddddddddddddd',
            mtproxy_host='31.31.31.31',
            is_active=False,
        )

        order, error = async_to_sync(ensure_cloud_asset_operation_order)(asset.id, self.user.id)

        self.assertIsNone(error)
        self.assertIsNotNone(order)
        self.assertEqual(order.ip_recycle_at, due_at)
        retained_order, plans, retained_err = async_to_sync(list_retained_ip_renewal_plans)(order.id, self.user.id)
        self.assertIsNone(retained_err)
        self.assertIsNotNone(retained_order)
        self.assertTrue(plans)
        self.assertEqual(retained_order.id, order.id)

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

    def test_shutdown_log_items_prefer_order_lifecycle_schedule(self):
        expires_at = timezone.now() + timezone.timedelta(days=1)
        order = CloudServerOrder.objects.create(
            order_no='SHUTDOWN-SCHEDULE-ORDER-1',
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
            public_ip='6.6.6.8',
            service_started_at=timezone.now(),
            service_expires_at=expires_at,
        )
        custom_suspend_at = timezone.now() + timezone.timedelta(days=9)
        custom_delete_at = custom_suspend_at + timezone.timedelta(hours=2)
        CloudServerOrder.objects.filter(id=order.id).update(suspend_at=custom_suspend_at, delete_at=custom_delete_at)
        order.refresh_from_db()
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='shutdown-schedule-asset',
            public_ip='6.6.6.8',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        items = _shutdown_log_items(limit=20)
        row = next(item for item in items if item.get('order_id') == order.id)

        self.assertEqual(parse_datetime(row['suspend_at']), order.suspend_at)
        self.assertEqual(parse_datetime(row['delete_at']), order.delete_at)

    def test_cloud_orders_list_keeps_renew_pending_visible(self):
        order = CloudServerOrder.objects.create(
            order_no='CLOUD-ORDER-LIST-RENEW-PENDING-1',
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
            status='renew_pending',
            public_ip='6.6.6.9',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(hours=8),
            expired_at=timezone.now() + timezone.timedelta(minutes=30),
        )
        staff_user = get_user_model().objects.create_user(username='staff_cloud_order_list', password='x', is_staff=True)
        request = RequestFactory().get('/api/dashboard/cloud-orders/')
        request.user = staff_user

        response = cloud_orders_list(request)
        payload = json.loads(response.content)
        data = payload.get('data') or []
        row = next(item for item in data if item.get('id') == order.id)

        self.assertEqual(row['renew_status'], 'renew_pending')
        self.assertEqual(row['renew_status_label'], '续费待支付')
        self.assertTrue(row['can_renew'])

    def test_unattached_ip_delete_items_use_actual_expiry_as_delete_plan(self):
        delete_due_at = timezone.now() + timezone.timedelta(days=3)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='visible-unattached-direct-delete-plan',
            public_ip='5.5.5.7',
            actual_expires_at=delete_due_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
        )

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('id') == asset.id)

        self.assertEqual(parse_datetime(row['delete_at']), delete_due_at)

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

    def test_sync_cloud_asset_status_uses_asset_scope(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='single-asset-sync',
            external_account_id='acct-single-asset-sync',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            account_label=cloud_account_label(account),
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='single-asset-sync',
            public_ip='3.3.3.3',
            instance_id='i-single-asset-sync',
            provider_resource_id='res-single-asset-sync',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_sync_one', password='x', is_staff=True)
        with patch('cloud.api._call_command_capture', return_value=(object(), None)) as mocked:
            request = RequestFactory().post(f'/api/dashboard/cloud-assets/{asset.id}/sync/', data='{}', content_type='application/json')
            request.user = staff_user
            response = sync_cloud_asset_status(request, asset.id)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload['data']['ok'])
        self.assertEqual(payload['data']['asset']['id'], asset.id)
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.args[0], 'sync_aws_assets')
        self.assertEqual(mocked.call_args.kwargs['account_id'], str(account.id))
        self.assertEqual(mocked.call_args.kwargs['region'], 'ap-southeast-1')

    def test_lifecycle_aws_sync_scans_all_regions_without_env_region(self):
        aws_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-lifecycle-all-region-sync',
            external_account_id='acct-aws-lifecycle-all',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        aliyun_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_ALIYUN,
            name='aliyun-lifecycle-region-sync',
            external_account_id='acct-aliyun-lifecycle',
            access_key='ak',
            secret_key='sk',
            region_hint='cn-hongkong',
            is_active=True,
        )
        calls = []

        def fake_call_command(command_name, **kwargs):
            calls.append((command_name, kwargs))

        with patch.dict(os.environ, {'AWS_REGION': '', 'ALIYUN_REGION': ''}, clear=False), patch('cloud.lifecycle.call_command', side_effect=fake_call_command):
            async_to_sync(sync_server_status_tick)()

        aws_call = next(item for item in calls if item[0] == 'sync_aws_assets')
        aliyun_call = next(item for item in calls if item[0] == 'sync_aliyun_assets')
        self.assertEqual(aws_call[1]['account_id'], str(aws_account.id))
        self.assertNotIn('region', aws_call[1])
        self.assertEqual(aliyun_call[1]['account_id'], str(aliyun_account.id))
        self.assertEqual(aliyun_call[1]['region'], 'cn-hongkong')

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

    def test_auto_renew_task_detail_includes_due_retry_and_fallback_items(self):
        due_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-DUE-1',
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
            public_ip='10.0.0.1',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(hours=12),
            auto_renew_enabled=True,
        )
        retry_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-RETRY-1',
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
            public_ip='10.0.0.2',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(days=2),
            auto_renew_enabled=True,
        )
        fallback_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-FALLBACK-1',
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
            public_ip='10.0.0.3',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(hours=1),
            auto_renew_enabled=True,
        )
        CloudAutoRenewPatrolLog.objects.create(
            order=retry_order,
            user=self.user,
            batch_id='failed-batch-1',
            order_no=retry_order.order_no,
            ip=retry_order.public_ip,
            provider=retry_order.provider,
            user_display_name='svc_test',
            username_label='@svc_test',
            tg_user_id=self.user.tg_user_id,
            is_success=False,
            failure_reason='余额不足',
        )
        staff_user = get_user_model().objects.create_user(username='staff_auto_renew_detail', password='x', is_staff=True)
        request = RequestFactory().get('/api/dashboard/tasks/auto-renew/')
        request.user = staff_user

        async def fake_get_due_orders():
            return {'auto_renew': [due_order]}

        with patch('cloud.api._get_due_orders', side_effect=fake_get_due_orders):
            response = auto_renew_task_detail(request)

        payload = json.loads(response.content)
        data = payload.get('data') or payload
        due_items = data['due_items']
        queue_status_map = {item['order_no']: item['queue_status'] for item in due_items}

        self.assertEqual(response.status_code, 200)
        self.assertEqual(queue_status_map[due_order.order_no], 'due_now')
        self.assertEqual(queue_status_map[retry_order.order_no], 'retry_failed')
        self.assertEqual(queue_status_map[fallback_order.order_no], 'fallback_retry')
        retry_item = next(item for item in due_items if item['order_no'] == retry_order.order_no)
        self.assertEqual(retry_item['last_failure_reason'], '余额不足')

    def test_run_auto_renew_tasks_executes_due_retry_and_fallback_queue(self):
        due_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-RUN-DUE-1',
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
            public_ip='10.0.1.1',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(hours=8),
            auto_renew_enabled=True,
        )
        retry_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-RUN-RETRY-1',
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
            public_ip='10.0.1.2',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(days=1),
            auto_renew_enabled=True,
        )
        fallback_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-RUN-FALLBACK-1',
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
            public_ip='10.0.1.3',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(hours=2),
            auto_renew_enabled=True,
        )
        CloudAutoRenewPatrolLog.objects.create(
            order=retry_order,
            user=self.user,
            batch_id='failed-batch-2',
            order_no=retry_order.order_no,
            ip=retry_order.public_ip,
            provider=retry_order.provider,
            user_display_name='svc_test',
            username_label='@svc_test',
            tg_user_id=self.user.tg_user_id,
            is_success=False,
            failure_reason='上次失败',
        )
        staff_user = get_user_model().objects.create_user(username='staff_auto_renew_run', password='x', is_staff=True)
        request = RequestFactory().post('/api/dashboard/tasks/auto-renew/run/', data='{}', content_type='application/json')
        request.user = staff_user

        async def fake_get_due_orders():
            return {'auto_renew': [due_order]}

        def fake_run_auto_renew(order_id):
            order = CloudServerOrder.objects.get(id=order_id)
            if order_id == retry_order.id:
                return None, '余额不足', {'currency': 'USDT', 'amount': None}
            return order, None, {'currency': 'USDT', 'amount': Decimal('19.00'), 'before': Decimal('100.00'), 'after': Decimal('81.00'), 'payer_user_id': self.user.id}

        with patch('cloud.api._get_due_orders', side_effect=fake_get_due_orders), patch('cloud.api._run_auto_renew', new=fake_run_auto_renew):
            response = run_auto_renew_tasks(request)

        payload = json.loads(response.content)
        data = payload.get('data') or payload
        items = data['items']
        item_map = {item['order_no']: item for item in items}

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['total'], 3)
        self.assertEqual(data['success_count'], 2)
        self.assertEqual(data['failure_count'], 1)
        self.assertTrue(item_map[due_order.order_no]['ok'])
        self.assertFalse(item_map[retry_order.order_no]['ok'])
        self.assertEqual(item_map[retry_order.order_no]['error'], '余额不足')
        self.assertEqual(item_map[fallback_order.order_no]['queue_status'], 'fallback_retry')
        self.assertEqual(CloudAutoRenewPatrolLog.objects.filter(batch_id=data['batch_id']).count(), 3)

    def test_run_auto_renew_order_executes_single_order(self):
        order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-SINGLE-1',
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
            public_ip='10.0.2.1',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            service_expires_at=timezone.now() + timezone.timedelta(hours=4),
            auto_renew_enabled=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_auto_renew_single', password='x', is_staff=True)
        request = RequestFactory().post(f'/api/dashboard/tasks/auto-renew/orders/{order.id}/run/', data='{}', content_type='application/json')
        request.user = staff_user

        def fake_run_auto_renew(order_id):
            renewed = CloudServerOrder.objects.get(id=order_id)
            return renewed, None, {'currency': 'USDT', 'amount': Decimal('19.00'), 'before': Decimal('50.00'), 'after': Decimal('31.00'), 'payer_user_id': self.user.id}

        with patch('cloud.api._run_auto_renew', new=fake_run_auto_renew):
            response = run_auto_renew_order(request, order.id)

        payload = json.loads(response.content)
        data = payload.get('data') or payload

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['queue_status'], 'manual_single')
        self.assertTrue(data['items'][0]['ok'])
        self.assertTrue(CloudAutoRenewPatrolLog.objects.filter(batch_id=data['batch_id'], order=order).exists())

    def test_update_cloud_asset_refreshes_unattached_ip_delete_plan(self):
        old_due_at = timezone.now() + timezone.timedelta(days=2)
        old_ip_recycle_at = timezone.now() + timezone.timedelta(days=2)
        order = CloudServerOrder.objects.create(
            order_no='UNATTACHED-REFRESH-PLAN-1',
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
            public_ip='10.9.0.9',
            previous_public_ip='10.9.0.9',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            service_expires_at=timezone.now() - timezone.timedelta(days=10),
            delete_at=timezone.now() - timezone.timedelta(days=7),
            ip_recycle_at=old_ip_recycle_at,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            order=order,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='refresh-unattached-ip-asset',
            provider_resource_id='aws-static-ip-refresh-1',
            public_ip='10.9.0.9',
            actual_expires_at=old_due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            order=order,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='refresh-unattached-ip-server',
            provider_resource_id='aws-static-ip-refresh-1',
            public_ip='10.9.0.9',
            expires_at=old_due_at,
            status=Server.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        staff_user = get_user_model().objects.create_user(username='staff_refresh_unattached_plan', password='x', is_staff=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({'note': '未附加固定IP\n人工刷新删除计划'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        asset.refresh_from_db()
        server.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertGreater(asset.actual_expires_at, old_due_at)
        self.assertEqual(server.expires_at, asset.actual_expires_at)
        self.assertEqual(order.ip_recycle_at, asset.actual_expires_at)

    def test_update_cloud_asset_rebinds_unattached_ip_to_instance(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='rebound-ip-asset',
            provider_resource_id='aws-static-ip-manual-1',
            public_ip='10.9.0.1',
            actual_expires_at=timezone.now() + timezone.timedelta(days=10),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='rebound-ip-server',
            provider_resource_id='aws-static-ip-manual-1',
            public_ip='10.9.0.1',
            expires_at=asset.actual_expires_at,
            status=Server.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        staff_user = get_user_model().objects.create_user(username='staff_rebound_manual', password='x', is_staff=True)
        request = RequestFactory().patch(
            f'/api/dashboard/cloud-assets/{asset.id}/',
            data=json.dumps({'instance_id': 'i-rebound-manual-1'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request.user = staff_user

        response = update_cloud_asset(request, asset.id)

        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(asset.instance_id, 'i-rebound-manual-1')
        self.assertEqual(asset.provider_status, '已重新绑定实例-待人工添加时间')
        self.assertIsNone(asset.actual_expires_at)
        self.assertTrue(asset.is_active)
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertIn('未附加IP已重新绑定到实例', asset.note or '')
        self.assertIn('等待人工添加真实到期时间', asset.note or '')
        self.assertEqual(server.instance_id, 'i-rebound-manual-1')
        self.assertIsNone(server.expires_at)
        self.assertEqual(server.provider_status, '已重新绑定实例-待人工添加时间')
        self.assertTrue(server.is_active)
        self.assertEqual(server.status, Server.STATUS_RUNNING)

    def test_sync_missing_delete_threshold_is_configurable(self):
        with patch('cloud.sync_safety.get_runtime_config', return_value='3'):
            self.assertEqual(get_missing_confirmation_threshold(), 3)
        with patch('cloud.sync_safety.get_runtime_config', return_value='0'):
            self.assertEqual(get_missing_confirmation_threshold(), 1)

    def test_sync_aws_missing_instance_requires_two_passes_before_delete(self):
        from cloud.management.commands.sync_aws_assets import _mark_deleted_when_missing_in_aws

        class DummyStyle:
            def WARNING(self, text):
                return text

        class DummyStdout:
            def __init__(self):
                self.stdout = self
                self.style = DummyStyle()
            def write(self, text):
                return text

        order = CloudServerOrder.objects.create(
            order_no='AWS-MISS-CONFIRM-1',
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
            previous_public_ip='9.9.9.9',
            instance_id='i-aws-missing-confirm-1',
            provider_resource_id='res-aws-missing-confirm-1',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='aws-missing-confirm-asset',
            public_ip='9.9.9.9',
            previous_public_ip='9.9.9.9',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            actual_expires_at=order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='aws-missing-confirm-server',
            public_ip='9.9.9.9',
            previous_public_ip='9.9.9.9',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        deleted = _mark_deleted_when_missing_in_aws(self.plan.region_code, set(), set(), DummyStdout())
        asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(deleted, [])
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.provider_status, '云上未找到实例/IP-待确认')
        self.assertEqual(server.status, Server.STATUS_RUNNING)
        self.assertEqual(order.status, 'completed')

        deleted = _mark_deleted_when_missing_in_aws(self.plan.region_code, set(), set(), DummyStdout())
        asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
        self.assertTrue(deleted)
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(server.status, Server.STATUS_DELETED)
        self.assertEqual(order.status, 'deleted')

    def test_sync_aliyun_missing_instance_requires_two_passes_before_delete(self):
        from cloud.management.commands.sync_aliyun_assets import _mark_deleted_when_missing_in_aliyun

        class DummyStyle:
            def WARNING(self, text):
                return text

        class DummyStdout:
            def __init__(self):
                self.stdout = self
                self.style = DummyStyle()
            def write(self, text):
                return text

        order = CloudServerOrder.objects.create(
            order_no='ALIYUN-MISS-CONFIRM-1',
            user=self.user,
            plan=self.plan,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='6.6.6.6',
            previous_public_ip='6.6.6.6',
            instance_id='i-aliyun-missing-confirm-1',
            provider_resource_id='i-aliyun-missing-confirm-1',
            service_started_at=timezone.now(),
            service_expires_at=timezone.now() + timezone.timedelta(days=31),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            order=order,
            user=self.user,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            asset_name='aliyun-missing-confirm-asset',
            public_ip='6.6.6.6',
            previous_public_ip='6.6.6.6',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            actual_expires_at=order.service_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        server = Server.objects.create(
            source=Server.SOURCE_ALIYUN,
            order=order,
            user=self.user,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            server_name='aliyun-missing-confirm-server',
            public_ip='6.6.6.6',
            previous_public_ip='6.6.6.6',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=Server.STATUS_RUNNING,
            is_active=True,
        )

        deleted = _mark_deleted_when_missing_in_aliyun('cn-hongkong', set(), DummyStdout())
        asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(deleted, [])
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.provider_status, '云上未找到实例-待确认')
        self.assertEqual(server.status, Server.STATUS_RUNNING)
        self.assertEqual(order.status, 'completed')

        deleted = _mark_deleted_when_missing_in_aliyun('cn-hongkong', set(), DummyStdout())
        asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
        self.assertTrue(deleted)
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(server.status, Server.STATUS_DELETED)
        self.assertEqual(order.status, 'deleted')

    def test_sync_aws_assets_rebinds_unattached_ip_when_instance_reappears(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-rebind',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='rebind-static-ip',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/rebind-static-ip',
            public_ip='10.9.0.2',
            actual_expires_at=timezone.now() + timezone.timedelta(days=3),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='rebind-static-ip-server',
            public_ip='10.9.0.2',
            expires_at=asset.actual_expires_at,
            status=Server.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )

        class FakeLightsailClient:
            def get_static_ips(self, **kwargs):
                return {'staticIps': [], 'nextPageToken': None}

            def get_instances(self, **kwargs):
                return {
                    'instances': [{
                        'name': 'i-rebound-sync-1',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-rebound-sync-1',
                        'state': {'name': 'running'},
                        'location': {'regionName': '新加坡'},
                        'publicIpAddress': '10.9.0.2',
                        'bundleId': 'micro_1_0',
                        'blueprintId': 'debian_12',
                    }],
                    'nextPageToken': None,
                }

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1')

        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(asset.instance_id, 'i-rebound-sync-1')
        self.assertEqual(asset.provider_status, '已重新绑定实例-待人工添加时间')
        self.assertIsNone(asset.actual_expires_at)
        self.assertTrue(asset.is_active)
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertIn('未附加IP已重新绑定到实例', asset.note or '')
        self.assertIn('等待人工添加真实到期时间', asset.note or '')
        self.assertEqual(server.instance_id, 'i-rebound-sync-1')
        self.assertIsNone(server.expires_at)
        self.assertEqual(server.provider_status, '已重新绑定实例-待人工添加时间')
        self.assertTrue(server.is_active)
        self.assertEqual(server.status, Server.STATUS_RUNNING)

    def test_sync_aws_assets_keeps_runtime_running_when_order_is_suspended(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-suspended-runtime',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        order = CloudServerOrder.objects.create(
            order_no='AWS-SYNC-SUSPENDED-RUNTIME-1',
            user=self.user,
            plan=self.plan,
            cloud_account=account,
            account_label=account_label,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='suspended',
            public_ip='10.9.0.3',
            previous_public_ip='10.9.0.3',
            instance_id='i-suspended-runtime-1',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-suspended-runtime-1',
            server_name='i-suspended-runtime-1',
            service_started_at=timezone.now() - timezone.timedelta(days=20),
            service_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='i-suspended-runtime-1',
            public_ip='10.9.0.3',
            previous_public_ip='10.9.0.3',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            actual_expires_at=order.service_expires_at,
            status=CloudAsset.STATUS_SUSPENDED,
            provider_status='已到期关机，等待删除（云端已关机）',
            is_active=False,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='i-suspended-runtime-1',
            public_ip='10.9.0.3',
            previous_public_ip='10.9.0.3',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            expires_at=order.service_expires_at,
            status=Server.STATUS_SUSPENDED,
            provider_status='已到期关机，等待删除（云端已关机）',
            is_active=False,
        )

        class FakeLightsailClient:
            def get_static_ips(self, **kwargs):
                return {'staticIps': [], 'nextPageToken': None}

            def get_instances(self, **kwargs):
                return {
                    'instances': [{
                        'name': 'i-suspended-runtime-1',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-suspended-runtime-1',
                        'state': {'name': 'running'},
                        'location': {'regionName': '新加坡'},
                        'publicIpAddress': '10.9.0.3',
                        'bundleId': 'micro_1_0',
                        'blueprintId': 'debian_12',
                    }],
                    'nextPageToken': None,
                }

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1')

        asset.refresh_from_db()
        server.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertTrue(asset.is_active)
        self.assertEqual(server.status, Server.STATUS_RUNNING)
        self.assertTrue(server.is_active)
        self.assertEqual(order.status, 'suspended')
        self.assertIn('云端运行中', asset.provider_status or '')
        self.assertIn('已到期关机，等待删除', asset.provider_status or '')


    def test_lifecycle_tick_releases_retained_static_ip_after_recycle_due(self):
        recycle_due_at = timezone.now() - timezone.timedelta(days=2)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RECYCLE-DUE',
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
            public_ip='20.20.20.20',
            previous_public_ip='20.20.20.20',
            static_ip_name='StaticIp-retained-due',
            service_expires_at=timezone.now() - timezone.timedelta(days=20),
            delete_at=timezone.now() - timezone.timedelta(days=17),
            ip_recycle_at=recycle_due_at,
            instance_id='',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-retained-due',
            public_ip='20.20.20.20',
            previous_public_ip='20.20.20.20',
            actual_expires_at=recycle_due_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='固定IP保留中-实例已删除',
            note='固定IP保留中',
            is_active=False,
        )

        released = []

        class FakeLightsailClient:
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {'operations': [{'id': 'op-retained-release'}]}

        with patch('cloud.lifecycle._aws_client', return_value=FakeLightsailClient()):
            async_to_sync(lifecycle_tick)()

        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(released, ['StaticIp-retained-due'])
        self.assertIsNone(order.ip_recycle_at)
        self.assertEqual(order.public_ip, '')
        self.assertEqual(order.previous_public_ip, '20.20.20.20')
        self.assertEqual(order.static_ip_name, '')
        self.assertIsNone(asset.public_ip)
        self.assertEqual(asset.previous_public_ip, '20.20.20.20')
        self.assertIn('AWS 固定 IP 已真实释放', order.provision_note or '')

    def test_lifecycle_tick_releases_overdue_unattached_static_ip(self):
        due_at = timezone.now() - timezone.timedelta(days=4)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-unattached-due',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-unattached-due',
            public_ip='21.21.21.21',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        server = Server.objects.create(
            source=Server.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            server_name='unattached-static-ip-shadow',
            public_ip='21.21.21.21',
            expires_at=due_at,
            status=Server.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )

        released = []

        class FakeLightsailClient:
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {'operations': [{'id': 'op-unattached-release'}]}

        with patch('cloud.lifecycle._aws_client', return_value=FakeLightsailClient()), patch('cloud.lifecycle._is_cloud_delete_safe_time', return_value=True):
            async_to_sync(lifecycle_tick)()

        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(released, ['StaticIp-unattached-due'])
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(asset.provider_status, '未附加固定IP-已到期删除')
        self.assertIsNone(asset.public_ip)
        self.assertEqual(asset.previous_public_ip, '21.21.21.21')
        self.assertEqual(server.status, Server.STATUS_DELETED)
        self.assertEqual(server.provider_status, '未附加固定IP-已到期删除')
        self.assertIsNone(server.public_ip)
        self.assertEqual(server.previous_public_ip, '21.21.21.21')
