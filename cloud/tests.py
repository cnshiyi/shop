import asyncio
import json
import os
import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from bot.api import _asset_delete_plan_item_payload, _shutdown_log_items, _unattached_ip_delete_items, lifecycle_plans, refresh_lifecycle_plan_view, update_lifecycle_plan_note
from bot.models import TelegramGroupFilter, TelegramUser
from cloud.bootstrap import _build_mtproxy_script, _extract_tg_links
from cloud.models import CloudAsset, CloudAssetDashboardSnapshot, CloudAssetSyncJob, CloudAssetSyncJobEvent, CloudAutoRenewPatrolLog, CloudAutoRenewRetryTask, CloudIpLog, CloudLifecyclePlanNote, CloudLifecycleTask, CloudNoticeTask, CloudServerOrder, CloudServerPlan, CloudUserNoticeLog, DailyAddressStat
from cloud.lifecycle import _apply_notice_schedule_to_order, _auto_renew_candidate_users, _enqueue_auto_renew_retry, _get_due_orders, _get_migration_due_orders, _get_orphan_asset_delete_due, _get_unattached_static_ip_delete_due, _group_balance_lines_for_orders, _is_cloud_delete_safe_time, _is_cloud_suspend_time, _mark_deleted, _mark_suspended, _next_cloud_action_run_at, _notice_payload_for_order, _notice_plan_text, _process_auto_renew_retry_tasks, _run_auto_renew, _send_logged_cloud_notice, _send_order_notice_batch, auto_renew_patrol_tick, daily_expiry_summary_tick, lifecycle_tick, sync_server_status_tick
from cloud.lifecycle_schedule import compute_order_lifecycle_fields
from cloud.asset_expiry import order_asset_expiry
from cloud.note_utils import append_status_note
from cloud.ports import get_mtproxy_port_label, get_mtproxy_public_ports, is_valid_mtproxy_main_port
from cloud.aws_lightsail import _public_ip_exists_sync, _resolve_static_ip_name_for_move
from cloud.ip_guard import validate_server_connection_ip, validate_server_connection_ip_with_retry
from cloud.provisioning import (
    _append_cloud_asset_note,
    _candidate_cloud_account_ids,
    _cloud_created_server_name,
    _compact_proxy_install_note,
    _extract_mtproxy_fields,
    _extract_proxy_links,
    _get_aws_create_payload,
    _get_rebuild_static_ip_context,
    _log_provision_result,
    _mark_failed,
    _mark_instance_created,
    _mark_provisioning_start,
    _mark_rebuild_source_pending_deletion,
    _mark_success,
    _mask_proxy_log_preview,
    provision_cloud_server,
)
from cloud.services import _cloud_asset_deleted_or_missing, apply_cloud_server_renewal, create_cloud_server_order, create_cloud_server_rebuild_order, create_cloud_server_renewal, create_cloud_server_renewal_by_public_query, create_cloud_server_renewal_for_user, create_cloud_server_upgrade_order, ensure_cloud_asset_operation_order, get_cloud_server_by_ip, get_cloud_server_by_ip_for_user, get_group_proxy_asset_detail, get_proxy_asset_by_ip_for_admin, get_proxy_asset_by_ip_for_user, get_user_proxy_asset_detail, is_retained_ip_order_visible_in_group, list_all_auto_renew_cloud_servers, list_cloud_asset_renewal_plans, list_cloud_server_upgrade_plans, list_group_cloud_servers, list_retained_ip_renewal_plans, list_retained_ip_renewal_plans_by_asset, list_user_cloud_servers, mark_cloud_server_ip_change_requested, mark_cloud_server_reinit_requested, pay_cloud_server_order_with_balance, pay_cloud_server_renewal_with_balance, prepare_cloud_asset_renewal_with_link, prepare_retained_ip_renewal_with_link, rebind_cloud_server_user, record_cloud_ip_log, replace_cloud_asset_order_by_admin, run_cloud_server_renewal_postcheck, set_cloud_server_auto_renew_admin, set_group_cloud_server_auto_renew, sync_cloud_asset_user_binding
from cloud.sync_safety import get_missing_confirmation_threshold
from cloud.api_asset_edit import delete_cloud_asset, update_cloud_asset
from cloud.api_asset_snapshots import _dashboard_snapshot_can_use_forward_row_paging, _dashboard_snapshot_group_keys_from_ordered_rows, _dashboard_snapshot_ordering, _dashboard_snapshot_risk_counts, backfill_cloud_asset_dashboard_snapshots, refresh_cloud_asset_dashboard_snapshots
from cloud.api_assets import _asset_payload, _display_cloud_asset_note, _infer_asset_order, cloud_assets_list
from cloud.api_monitors import _fetch_address_chain_balances
from cloud.api_orders import _cloud_order_source_tags, cloud_order_detail, cloud_orders_list, delete_cloud_order, update_cloud_order_status
from cloud.api_servers import delete_server, servers_list
from cloud.api_sync import _apply_server_missing_state, sync_cloud_asset_status
from cloud.api_tasks import auto_renew_task_detail, delete_notice_history, notice_task_detail, refresh_notice_plan_view, run_auto_renew_order, run_auto_renew_tasks, tasks_overview, update_notice_plan_text, update_notice_switches
from cloud.task_center import task_center_payload
from cloud.sync_jobs import _cloud_assets_sync_status_counts, _execute_cloud_asset_sync_job, _latest_synced_cloud_asset_updated_at, cancel_cloud_asset_sync_job, cloud_asset_sync_job_detail, cloud_asset_sync_jobs_list, cloud_asset_sync_jobs_metrics, retry_cloud_asset_sync_job, sync_cloud_assets
from core.cloud_accounts import cloud_account_label, cloud_account_label_variants, list_cloud_accounts_by_server_load
from core.models import CloudAccountConfig, SiteConfig
from core.persistence import bump_daily_address_stat
from orders.payment_scanner import _confirm_cloud_server_order


# 测试类：组织 CloudServerServicesTestCase 相关的回归测试。
class CloudServerServicesTestCase(TestCase):
    # 功能：延迟刷新线程启动失败时必须释放锁，避免后续仪表盘/计划刷新被卡住。
    def test_dashboard_snapshot_deferred_releases_lock_when_thread_start_fails(self):
        from cloud.dashboard_snapshots import _refresh_dashboard_plan_snapshots_deferred

        class FakeCache:
            def __init__(self):
                self.deleted = []

            def add(self, key, value, timeout=None):
                return True

            def delete(self, key):
                self.deleted.append(key)

        fake_cache = FakeCache()

        with patch('cloud.dashboard_snapshots.cache', fake_cache), \
                patch('cloud.dashboard_snapshots.threading.Thread') as thread_cls, \
                self.assertLogs('cloud.dashboard_snapshots', level='INFO') as logs:
            thread_cls.return_value.start.side_effect = RuntimeError("can't start new thread")

            _refresh_dashboard_plan_snapshots_deferred(
                'thread-start-failed-test',
                cloud_asset_ids=[123],
                full_cloud_assets=False,
            )

        self.assertEqual(fake_cache.deleted, ['dashboard:snapshot-refresh:deferred:assets:123'])
        self.assertIn('DASHBOARD_SNAPSHOT_DEFERRED_SKIPPED', '\n'.join(logs.output))

    # 功能：验证开通结果日志在异步开通流程返回后不再隐式查询资产表。
    def test_provision_result_log_uses_cached_asset_expiry(self):
        order = CloudServerOrder.objects.create(
            order_no='PROVISION-LOG-CACHED-EXPIRY',
            status='completed',
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            server_name='provision-log-cached-expiry',
            instance_id='provision-log-cached-expiry',
            public_ip='10.0.0.91',
            mtproxy_port=9528,
            total_amount=Decimal('0'),
            user=self.user,
            plan=self.plan,
        )
        expires_at = timezone.now() + timezone.timedelta(days=31)
        order._asset_expires_at = expires_at

        with patch('cloud.provisioning.order_asset_expiry', side_effect=AssertionError('不应在日志中查询资产到期时间')):
            _log_provision_result(order)

    # 功能：验证开通结果日志不会在代理链接预览或错误字段中暴露 secret。
    def test_provision_result_log_masks_proxy_secrets(self):
        secret = 'ee0123456789abcdef0123456789abcdef'
        order = CloudServerOrder.objects.create(
            order_no='PROVISION-LOG-MASK-SECRET',
            status='failed',
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            server_name='provision-log-mask-secret',
            instance_id='provision-log-mask-secret',
            public_ip='10.0.0.92',
            mtproxy_port=9528,
            mtproxy_link=f'tg://proxy?server=10.0.0.92&port=9528&secret={secret}',
            total_amount=Decimal('0'),
            user=self.user,
            plan=self.plan,
        )
        order._asset_expires_at = timezone.now() + timezone.timedelta(days=31)

        with patch('cloud.provisioning.logger.log') as mock_log:
            _log_provision_result(order, error=f'安装失败 secret={secret}')

        _, args, kwargs = mock_log.mock_calls[0]
        logged_values = ' '.join(str(value) for value in args[2:])
        payload = kwargs['extra']['provision_result']
        self.assertNotIn(secret, logged_values)
        self.assertNotIn(secret, str(payload))
        self.assertIn('secret=***', payload['error'])

    # 功能：验证代理链接日志预览不会保留 secret 尾部。
    def test_proxy_log_preview_masks_secret_tail(self):
        secret = 'ee0123456789abcdef0123456789abcdef'
        link = f'tg://proxy?server=10.0.0.93&port=9528&secret={secret}'

        preview = _mask_proxy_log_preview(link, visible=12)

        self.assertNotIn(secret, preview)
        self.assertNotIn(secret[-12:], preview)
        self.assertIn('secret=***', preview)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_account_label_variants_return_current_label_only(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='current-label-account',
            external_account_id='123456789012',
            access_key='AKIACURRENTLABEL123',
            secret_key='current-secret-key-value-long-enough-1234567890',
            is_active=True,
        )

        variants = cloud_account_label_variants(account)

        self.assertEqual(variants, [cloud_account_label(account)])
        self.assertNotIn(f'aws:{account.id}:current-label-account', variants)
        self.assertNotIn('aws:123456789012:current-label-account', variants)
        self.assertNotIn('aws', variants)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_account_load_does_not_count_provider_only_label_for_every_account(self):
        first = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='load-account-a',
            external_account_id='111111111111',
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        second = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='load-account-b',
            external_account_id='222222222222',
            access_key='C' * 20,
            secret_key='D' * 40,
            is_active=True,
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label=cloud_account_label(first),
            region_code='ap-southeast-1',
            asset_name='load-a-current',
            instance_id='load-a-current',
            public_ip='8.8.8.81',
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label='aws',
            region_code='ap-southeast-1',
            asset_name='provider-only-label',
            instance_id='provider-only-label',
            public_ip='8.8.8.82',
        )

        ordered = list_cloud_accounts_by_server_load('aws', 'ap-southeast-1')

        self.assertEqual([item.id for item in ordered[:2]], [second.id, first.id])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aliyun_desired_plan_id_is_preferred_without_locking_candidates(self):
        from cloud.aliyun_simple import _prefer_plan_id

        plans = [
            {'PlanId': 'fallback-plan', 'OriginPrice': '$5'},
            {'PlanId': 'desired-plan', 'OriginPrice': '$4'},
            {'PlanId': 'larger-plan', 'OriginPrice': '$8'},
        ]

        ordered = _prefer_plan_id(plans, 'desired-plan')

        self.assertEqual([item['PlanId'] for item in ordered], ['desired-plan', 'fallback-plan', 'larger-plan'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aws_sync_server_resolution_accepts_current_account_label(self):
        from cloud.management.commands.sync_aws_assets import _resolve_server

        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='current-sync-account',
            external_account_id='123456789012',
            access_key='AKIACURRENTSYNC123',
            secret_key='current-secret-key-value-long-enough-1234567890',
            is_active=True,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label=cloud_account_label(account),
            region_code='ap-southeast-1',
            asset_name='current-sync-instance',
            instance_id='current-sync-instance',
            public_ip='8.8.8.88',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        resolved = _resolve_server('current-sync-instance', '', '', None, account)

        self.assertEqual(resolved, server)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aws_sync_resolution_does_not_match_cross_region_same_instance_without_ip(self):
        from cloud.management.commands.sync_aws_assets import _resolve_asset, _resolve_server

        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='region-scope-account',
            external_account_id='123456789012',
            access_key='AKIAREGIONSCOPE123',
            secret_key='region-scope-secret-key-value-long-enough',
            is_active=True,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='us-east-1',
            asset_name='same-name-no-ip',
            instance_id='same-name-no-ip',
            public_ip='',
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label=cloud_account_label(account),
            region_code='us-east-1',
            asset_name='same-name-no-ip',
            instance_id='same-name-no-ip',
            public_ip='',
        )

        self.assertIsNone(_resolve_asset('same-name-no-ip', '', '', None, account, 'ap-southeast-1'))
        self.assertIsNone(_resolve_server('same-name-no-ip', '', '', None, account, 'ap-southeast-1'))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aliyun_sync_resolution_does_not_match_cross_region_same_instance_without_ip(self):
        from cloud.management.commands.sync_aliyun_assets import _resolve_asset, _resolve_server

        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_ALIYUN,
            name='aliyun-region-scope-account',
            external_account_id='aliyun-region-scope-id',
            access_key='aliyun-region-ak',
            secret_key='aliyun-region-sk',
            is_active=True,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            provider='aliyun_simple',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='cn-shanghai',
            asset_name='aliyun-same-name-no-ip',
            instance_id='aliyun-same-name-no-ip',
            public_ip='',
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            provider='aliyun_simple',
            account_label=cloud_account_label(account),
            region_code='cn-shanghai',
            asset_name='aliyun-same-name-no-ip',
            instance_id='aliyun-same-name-no-ip',
            public_ip='',
        )

        self.assertIsNone(_resolve_asset('aliyun-same-name-no-ip', '', account, 'cn-hongkong'))
        self.assertIsNone(_resolve_server('aliyun-same-name-no-ip', '', account, 'cn-hongkong'))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aliyun_audit_inventory_uses_asset_account(self):
        from cloud.management.commands.audit_cloud_asset_ip_presence import Command

        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_ALIYUN,
            name='aliyun-audit-account',
            external_account_id='aliyun-audit-account-id',
            access_key='aliyun-ak',
            secret_key='aliyun-sk',
            is_active=True,
        )
        captured = {}

        # 测试类：组织 FakeClient 相关的回归测试。
        class FakeClient:
            # 功能：查询并返回列表数据；当前函数属于 云资产、云订单和生命周期。
            def list_instances_with_options(self, request, runtime_options):
                captured['request'] = request
                return SimpleNamespace(body=SimpleNamespace(to_map=lambda: {'Instances': []}))

        fake_aliyun_module = SimpleNamespace(models=SimpleNamespace(ListInstancesRequest=lambda **kwargs: kwargs))
        with patch.dict(sys.modules, {'alibabacloud_swas_open20200601': fake_aliyun_module}), \
            patch('cloud.management.commands.audit_cloud_asset_ip_presence._build_client', return_value=FakeClient()) as build_client:
            inventory = Command()._load_aliyun_inventory('cn-hongkong', account)

        self.assertEqual(inventory, {'instances': {}})
        build_client.assert_called_once()
        self.assertIs(build_client.call_args.kwargs['account'], account)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_daily_address_stats_are_separated_by_account_key(self):
        user = TelegramUser.objects.create(tg_user_id=9901001, username='daily_stat_scope')
        stats_date = timezone.localdate()

        first = bump_daily_address_stat(
            user_id=user.id,
            address='TAddressScope',
            currency='USDT',
            direction='income',
            amount=Decimal('1.5'),
            account_scope=DailyAddressStat.ACCOUNT_SCOPE_CLOUD,
            account_key='cloud-account-a',
            stats_date=stats_date,
        )
        second = bump_daily_address_stat(
            user_id=user.id,
            address='TAddressScope',
            currency='USDT',
            direction='income',
            amount=Decimal('2.5'),
            account_scope=DailyAddressStat.ACCOUNT_SCOPE_CLOUD,
            account_key='cloud-account-b',
            stats_date=stats_date,
        )

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(
            DailyAddressStat.objects.filter(
                user=user,
                address='TAddressScope',
                currency='USDT',
                stats_date=stats_date,
                account_scope=DailyAddressStat.ACCOUNT_SCOPE_CLOUD,
            ).count(),
            2,
        )

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_server_connection_ip_guard_rejects_mismatch_before_ssh(self):
        ok, note = validate_server_connection_ip('54.151.227.23', ['13.228.232.184'], context='test_mismatch')

        self.assertFalse(ok)
        self.assertIn('目标 IP 54.151.227.23 与预期 IP 13.228.232.184 不一致', note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_created_server_name_uses_actual_aws_instance_name_only(self):
        aws_result = SimpleNamespace(instance_id='requested-node-1')
        aliyun_result = SimpleNamespace(instance_id='i-aliyun-resource-id')

        self.assertEqual(_cloud_created_server_name('aws_lightsail', 'requested-node', aws_result), 'requested-node-1')
        self.assertEqual(_cloud_created_server_name('aliyun_simple', 'requested-node', aliyun_result), 'requested-node')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_orders_list_exposes_auto_renew_enabled(self):
        expires_at = timezone.now() + timezone.timedelta(days=20)
        order = CloudServerOrder.objects.create(
            order_no='ORDER-LIST-AUTO-RENEW-1',
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
            public_ip='13.250.20.21',
            auto_renew_enabled=True,
        )
        self._attach_order_expiry_asset(order, expires_at)
        staff_user = get_user_model().objects.create_user(username='staff_order_list_auto_renew', password='x', is_staff=True)
        request = self.factory.get('/api/admin/cloud-orders/')
        self._attach_bearer_session(request, staff_user)

        response = cloud_orders_list(request)

        self.assertEqual(response.status_code, 200)
        rows = json.loads(response.content)['data']
        row = next(item for item in rows if item['id'] == order.id)
        self.assertTrue(row['auto_renew_enabled'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_server_connection_ip_guard_requires_public_ipv4(self):
        ok, note = validate_server_connection_ip('127.0.0.1', ['127.0.0.1'], context='test_loopback')

        self.assertFalse(ok)
        self.assertIn('目标 IP 无效', note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_server_connection_ip_guard_retries_mismatch_until_refreshed(self):
        refreshed = iter(['54.151.227.23', '13.228.232.184'])

        ok, note, final_ip = async_to_sync(validate_server_connection_ip_with_retry)(
            '54.151.227.23',
            ['13.228.232.184'],
            context='test_retry_mismatch',
            attempts=3,
            delay_seconds=0,
            refresh_target=lambda: next(refreshed),
        )

        self.assertTrue(ok)
        self.assertEqual(final_ip, '13.228.232.184')
        self.assertIn('第 3 次校验通过', note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_server_connection_ip_guard_does_not_retry_invalid_target(self):
        refresh = AsyncMock(return_value='13.228.232.184')

        ok, note, final_ip = async_to_sync(validate_server_connection_ip_with_retry)(
            '127.0.0.1',
            ['13.228.232.184'],
            context='test_retry_invalid',
            attempts=3,
            delay_seconds=0,
            refresh_target=refresh,
        )

        self.assertFalse(ok)

        self.assertEqual(final_ip, '')
        self.assertIn('目标 IP 无效', note)
        refresh.assert_not_called()

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aws_expected_ip_existence_check_passes_when_static_ip_exists(self):
        # 测试类：组织 Client 相关的回归测试。
        class Client:
            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_static_ips(self):
                return {'staticIps': [{'ipAddress': '13.228.232.184'}]}

            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_instances(self):
                return {'instances': []}

        with patch('cloud.aws_lightsail._aws_client_from_order_data', return_value=(Client(), '')):
            ok, note = _public_ip_exists_sync({'order_no': 'TEST'}, ['13.228.232.184'])

        self.assertTrue(ok)
        self.assertIn('存在于固定 IP', note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aws_expected_ip_existence_check_fails_when_ip_missing(self):
        # 测试类：组织 Client 相关的回归测试。
        class Client:
            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_static_ips(self):
                return {'staticIps': [{'ipAddress': '54.151.227.23'}]}

            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_instances(self):
                return {'instances': [{'publicIpAddress': '54.151.227.24'}]}

        with patch('cloud.aws_lightsail._aws_client_from_order_data', return_value=(Client(), '')):
            ok, note = _public_ip_exists_sync({'order_no': 'TEST'}, ['13.228.232.184'])

        self.assertFalse(ok)
        self.assertIn('在当前云账号中不存在', note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_manual_order_delete_bypasses_schedule_limits(self):
        from bot.api import _run_shutdown_order_sync

        account = self._aws_test_account()
        account.shutdown_enabled = False
        account.save(update_fields=['shutdown_enabled', 'updated_at'])
        order = CloudServerOrder.objects.create(
            order_no='MANUAL-DELETE-BYPASS-ORDER-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleting',
            public_ip='52.77.18.241',
            delete_at=timezone.now() + timezone.timedelta(days=1),
        )
        with patch('cloud.lifecycle.cloud_server_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_delete_safe_time', return_value=False) as safe_time, \
            patch('cloud.lifecycle._delete_instance', new=AsyncMock(return_value=(True, 'manual delete ok'))), \
            patch('cloud.lifecycle._mark_deleted', new=AsyncMock()):
            result = _run_shutdown_order_sync(order.id, enforce_schedule=False)

        self.assertTrue(result['ok'])
        safe_time.assert_not_called()

    # 功能：验证全局关机开关默认保持开启，避免新增配置改变既有生命周期行为。
    def test_cloud_server_shutdown_enabled_defaults_on(self):
        from cloud.lifecycle import cloud_server_shutdown_enabled

        SiteConfig.objects.filter(key='cloud_server_shutdown_enabled').delete()

        self.assertTrue(cloud_server_shutdown_enabled())

    # 功能：验证全局关机开关关闭时，计划关机不会触发真实云关机。
    def test_global_shutdown_switch_blocks_scheduled_suspend(self):
        from cloud.lifecycle_execution import run_shutdown_order_suspend

        SiteConfig.set('cloud_server_shutdown_enabled', '0')
        account = self._aws_test_account()
        now = timezone.now()
        order = CloudServerOrder.objects.create(
            order_no='GLOBAL-SHUTDOWN-OFF-ORDER-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='completed',
            public_ip='52.77.18.242',
            suspend_at=now - timezone.timedelta(minutes=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=order.region_code,
            region_name=order.region_name,
            public_ip=order.public_ip,
            actual_expires_at=now - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            shutdown_enabled=True,
            is_active=True,
        )

        with patch('cloud.lifecycle._stop_instance', new=AsyncMock(return_value=(True, 'should not stop'))) as stop_instance:
            result = run_shutdown_order_suspend(order.id, enforce_schedule=True)

        self.assertFalse(result['ok'])
        self.assertIn('服务器关机总开关已关闭', result['error'])
        stop_instance.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_manual_orphan_asset_delete_bypasses_schedule_limits(self):
        from bot.api import _run_orphan_asset_delete_sync

        account = self._aws_test_account()
        account.shutdown_enabled = False
        account.save(update_fields=['shutdown_enabled', 'updated_at'])
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='manual-owner-asset',
            instance_id='manual-owner-asset-instance',
            public_ip='52.77.18.241',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        original_asset_id = asset.id
        with patch('cloud.lifecycle.cloud_server_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_delete_safe_time', return_value=False) as safe_time, \
            patch('cloud.lifecycle._delete_orphan_asset_instance', new=AsyncMock(return_value=(True, 'manual asset delete ok'))), \
            patch('cloud.lifecycle._mark_orphan_asset_deleted', new=AsyncMock()):
            result = _run_orphan_asset_delete_sync(asset.id, enforce_schedule=False)

        self.assertTrue(result['ok'])
        safe_time.assert_not_called()

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_manual_unattached_ip_delete_writes_log_and_history_item(self):
        from bot.api import _run_unattached_ip_delete_sync

        SiteConfig.set('cloud_ip_delete_enabled', '1')
        account = self._aws_test_account()
        account.shutdown_enabled = False
        account.save(update_fields=['shutdown_enabled', 'updated_at'])
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='manual-unattached-ip-delete',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/manual-unattached-ip-delete',
            public_ip='52.77.18.244',
            actual_expires_at=timezone.now() + timezone.timedelta(days=3),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        with patch('cloud.lifecycle._release_unattached_static_ip', new=AsyncMock(return_value=(True, 'manual release ok'))):
            result = _run_unattached_ip_delete_sync(asset.id, enforce_schedule=False)

        asset.refresh_from_db()
        self.assertTrue(result['ok'])
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertTrue(CloudIpLog.objects.filter(asset=asset, event_type=CloudIpLog.EVENT_RECYCLED).exists())
        items = _unattached_ip_delete_items(limit=20)
        history = [item for item in items if item.get('is_history') and item.get('public_ip') == '52.77.18.244']
        self.assertTrue(history)
        self.assertIn('manual release ok', history[0]['note'])
        self.assertEqual(history[0]['deletion_source_label'], '人工手动删除')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_manual_unattached_ip_delete_clears_retained_order_after_successful_release(self):
        from bot.api import _run_unattached_ip_delete_sync

        SiteConfig.set('cloud_ip_delete_enabled', '1')
        account = self._aws_test_account()
        recycle_at = timezone.now() + timezone.timedelta(days=3)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-MANUAL-IP-RELEASE-CLEARS-ORDER',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleted',
            public_ip='52.77.18.251',
            previous_public_ip='52.77.18.251',
            static_ip_name='manual-clear-retained-order-ip',
            mtproxy_host='52.77.18.251',
            ip_recycle_at=recycle_at,
            ip_recycle_reminder_enabled=True,
            instance_id='',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='manual-clear-retained-order-ip',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/manual-clear-retained-order-ip',
            public_ip='52.77.18.251',
            previous_public_ip='52.77.18.251',
            actual_expires_at=recycle_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        with patch('cloud.lifecycle._release_unattached_static_ip', new=AsyncMock(return_value=(True, 'manual retained release ok'))):
            result = _run_unattached_ip_delete_sync(asset.id, enforce_schedule=False)

        asset.refresh_from_db()
        order.refresh_from_db()
        self.assertTrue(result['ok'])
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(order.public_ip, '')
        self.assertEqual(order.previous_public_ip, '52.77.18.251')
        self.assertEqual(order.static_ip_name, '')
        self.assertEqual(order.mtproxy_host, '')
        self.assertIsNone(order.ip_recycle_at)
        self.assertIsNotNone(order.recycle_notice_sent_at)
        self.assertFalse(order.ip_recycle_reminder_enabled)
        self.assertTrue(CloudIpLog.objects.filter(order=order, asset=asset, event_type=CloudIpLog.EVENT_RECYCLED).exists())

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unattached_ip_delete_log_without_known_note_shows_history(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unattached-ip-delete-history',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/unattached-ip-delete-history',
            previous_public_ip='52.77.18.245',
            status=CloudAsset.STATUS_DELETED,
            provider_status='未附加固定IP-已到期删除',
            is_active=False,
        )
        record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_RECYCLED,
            asset=asset,
            previous_public_ip='52.77.18.245',
            public_ip=None,
            note='旧版本释放成功',
        )

        items = _unattached_ip_delete_items(limit=20)
        history = [item for item in items if item.get('is_history') and item.get('public_ip') == '52.77.18.245']
        self.assertTrue(history)
        self.assertIn('旧版本释放成功', history[0]['note'])
        self.assertEqual(history[0]['deletion_source_label'], '到期自动删除')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_manual_order_delete_writes_server_history_item(self):
        from bot.api import _run_shutdown_order_sync

        SiteConfig.set('cloud_server_delete_enabled', '1')
        expires_at = timezone.now() - timezone.timedelta(days=1)
        order = CloudServerOrder.objects.create(
            order_no='MANUAL-DELETE-HISTORY-ORDER-1',
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
            status='deleting',
            public_ip='52.77.18.246',
            previous_public_ip='52.77.18.246',
            delete_at=timezone.now() - timezone.timedelta(hours=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='manual-delete-history-order-asset',
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_DELETING,
            is_active=True,
        )
        with patch('cloud.lifecycle._delete_instance', new=AsyncMock(return_value=(True, 'manual server delete ok'))):
            result = _run_shutdown_order_sync(order.id, enforce_schedule=False)

        self.assertTrue(result['ok'])
        self.assertTrue(CloudIpLog.objects.filter(order=order, event_type=CloudIpLog.EVENT_DELETED).exists())
        items = _shutdown_log_items(limit=20)
        history = [item for item in items if item.get('public_ip') == '52.77.18.246']
        self.assertTrue(history)
        self.assertIn('manual server delete ok', history[0]['note'])
        self.assertEqual(history[0]['deletion_source_label'], '人工手动删除')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_missing_aws_instance_delete_marks_order_history(self):
        from bot.api import _run_shutdown_order_sync

        # 测试类：组织 Client 相关的回归测试。
        class Client:
            # 功能：删除或标记删除相关业务对象；当前函数属于 云资产、云订单和生命周期。
            def delete_instance(self, instanceName):
                raise Exception('NotFoundException: instance does not exist')

        order = CloudServerOrder.objects.create(
            order_no='MANUAL-MISSING-DELETE-ORDER-1',
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
            status='deleting',
            server_name='missing-instance',
            public_ip='52.77.18.241',
        )
        with patch('cloud.lifecycle._aws_client', return_value=Client()):
            result = _run_shutdown_order_sync(order.id, enforce_schedule=False)

        order.refresh_from_db()
        self.assertTrue(result['ok'])
        self.assertEqual(order.status, 'deleted')
        self.assertTrue(CloudIpLog.objects.filter(order=order, event_type='deleted').exists())

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_missing_aws_orphan_asset_delete_marks_asset_history(self):
        from bot.api import _run_orphan_asset_delete_sync

        # 测试类：组织 Client 相关的回归测试。
        class Client:
            # 功能：删除或标记删除相关业务对象；当前函数属于 云资产、云订单和生命周期。
            def delete_instance(self, instanceName):
                raise Exception('NotFoundException: instance does not exist')

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='missing-orphan-asset',
            instance_id='missing-orphan-asset',
            public_ip='52.77.18.242',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        with patch('cloud.lifecycle._aws_client', return_value=Client()):
            result = _run_orphan_asset_delete_sync(asset.id, enforce_schedule=False)

        asset.refresh_from_db()
        self.assertTrue(result['ok'])
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertTrue(CloudIpLog.objects.filter(asset=asset, event_type='deleted').exists())

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dashboard_shutdown_plan_run_respects_delete_at(self):
        from bot.api import run_shutdown_plan_order

        SiteConfig.set('cloud_server_delete_enabled', '1')
        order = CloudServerOrder.objects.create(
            order_no='PLAN-RUN-FUTURE-DELETE-ORDER-1',
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
            status='deleting',
            server_name='future-delete-order-instance',
            public_ip='52.77.18.247',
            delete_at=timezone.now() + timezone.timedelta(days=1),
        )
        staff_user = get_user_model().objects.create_user(
            username='staff_plan_run_future_delete_order',
            password='x',
            is_staff=True,
            is_superuser=True,
        )
        request = self.factory.post(f'/api/admin/tasks/plans/orders/{order.id}/run/')
        self._attach_bearer_session(request, staff_user)

        with patch('cloud.lifecycle._delete_instance', new=AsyncMock()) as delete_mock:
            response = run_shutdown_plan_order(request, order.id)

        data = json.loads(response.content)['data']
        delete_mock.assert_not_awaited()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['success_count'], 0)
        self.assertIn('服务器删除时间未到', data['message'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dashboard_orphan_asset_plan_run_respects_computed_delete_time(self):
        from bot.api import run_orphan_asset_delete_plan

        SiteConfig.set('cloud_server_delete_enabled', '1')
        SiteConfig.set('cloud_suspend_after_days', '3')
        SiteConfig.set('cloud_suspend_time', '17:00')
        SiteConfig.set('cloud_delete_after_days', '3')
        SiteConfig.set('cloud_delete_time', '18:00')
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='future-orphan-plan-run',
            instance_id='future-orphan-plan-run',
            public_ip='52.77.18.248',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(
            username='staff_plan_run_future_orphan_asset',
            password='x',
            is_staff=True,
            is_superuser=True,
        )
        request = self.factory.post(f'/api/admin/tasks/plans/orphan-assets/{asset.id}/run/')
        self._attach_bearer_session(request, staff_user)

        with patch('cloud.lifecycle._delete_orphan_asset_instance', new=AsyncMock()) as delete_mock:
            response = run_orphan_asset_delete_plan(request, asset.id)

        data = json.loads(response.content)['data']
        delete_mock.assert_not_awaited()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['success_count'], 0)
        self.assertIn('未到服务器删除时间', data['message'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_orphan_asset_plan_run_rejects_active_linked_order_asset(self):
        from bot.api import _run_orphan_asset_delete_sync

        order = CloudServerOrder.objects.create(
            order_no='ORPHAN-RUN-LINKED-ORDER-GUARD-1',
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
            status='deleting',
            public_ip='52.77.18.252',
            delete_at=timezone.now() - timezone.timedelta(hours=1),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='linked-order-guard-asset',
            instance_id='linked-order-guard-asset',
            public_ip=order.public_ip,
            actual_expires_at=timezone.now() - timezone.timedelta(days=5),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        with patch('cloud.lifecycle._delete_orphan_asset_instance', new=AsyncMock()) as delete_mock:
            result = _run_orphan_asset_delete_sync(asset.id, enforce_schedule=True)

        delete_mock.assert_not_awaited()
        self.assertFalse(result['ok'])
        self.assertIn('关联订单', result['error'])
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.status, 'deleting')
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dashboard_unattached_ip_plan_run_respects_delete_time(self):
        from bot.api import run_unattached_ip_delete_plan

        SiteConfig.set('cloud_ip_delete_enabled', '1')
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='future-unattached-ip-plan-run',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/future-unattached-ip-plan-run',
            public_ip='52.77.18.249',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        staff_user = get_user_model().objects.create_user(
            username='staff_plan_run_future_unattached_ip',
            password='x',
            is_staff=True,
            is_superuser=True,
        )
        request = self.factory.post(f'/api/admin/tasks/plans/unattached-ips/{asset.id}/run/')
        self._attach_bearer_session(request, staff_user)

        with patch('cloud.lifecycle._release_unattached_static_ip', new=AsyncMock()) as release_mock:
            response = run_unattached_ip_delete_plan(request, asset.id)

        data = json.loads(response.content)['data']
        release_mock.assert_not_awaited()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['success_count'], 0)
        self.assertIn('未到 IP 删除时间', data['message'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dashboard_unattached_ip_plan_run_uses_ip_delete_time_window(self):
        from bot.api import run_unattached_ip_delete_plan

        SiteConfig.set('cloud_ip_delete_enabled', '1')
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='window-unattached-ip-plan-run',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/window-unattached-ip-plan-run',
            public_ip='52.77.18.251',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        staff_user = get_user_model().objects.create_user(
            username='staff_plan_run_window_unattached_ip',
            password='x',
            is_staff=True,
            is_superuser=True,
        )
        request = self.factory.post(f'/api/admin/tasks/plans/unattached-ips/{asset.id}/run/')
        self._attach_bearer_session(request, staff_user)

        with patch('bot.api._is_cloud_unattached_ip_delete_time', return_value=False) as safe_time, \
            patch('cloud.lifecycle._release_unattached_static_ip', new=AsyncMock()) as release_mock:
            response = run_unattached_ip_delete_plan(request, asset.id)

        data = json.loads(response.content)['data']
        safe_time.assert_called_once()
        release_mock.assert_not_awaited()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['success_count'], 0)
        self.assertIn('IP 删除执行时间窗口', data['message'])

    # 功能：验证未附加固定 IP 删除只受 IP 删除开关影响，不受资产关机开关影响。
    def test_unattached_ip_delete_ignores_shutdown_disabled_asset(self):
        from bot.api import _run_unattached_ip_delete_sync, _unattached_ip_delete_items

        SiteConfig.set('cloud_ip_delete_enabled', '1')
        account = self._aws_test_account()
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='disabled-static-ip',
            public_ip='52.77.18.250',
            provider_status='未附加固定IP',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            shutdown_enabled=False,
            ip_delete_enabled=True,
            is_active=True,
        )

        due_ids = {item.id for item in async_to_sync(_get_unattached_static_ip_delete_due)()}
        with patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=True), \
            patch('cloud.lifecycle._release_unattached_static_ip', new=AsyncMock(return_value=(True, 'ip release ok'))):
            result = _run_unattached_ip_delete_sync(asset.id, enforce_schedule=True)
        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('asset_id') == asset.id)

        self.assertIn(asset.id, due_ids)
        self.assertTrue(result['ok'])
        self.assertNotEqual(row.get('queue_status'), 'shutdown_disabled')
        self.assertNotEqual(row.get('queue_status'), 'ip_delete_disabled')

    # 功能：处理 云资产、云订单和生命周期 中的 setUp 业务流程。
    def setUp(self):
        import bot.api as bot_api

        SiteConfig.clear_cache()
        bot_api._LIFECYCLE_PLAN_CACHE.update({
            'bundle': None,
            'counts': None,
            'generated_at': None,
            'limit': 0,
        })
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

    # 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
    def _attach_bearer_session(self, request, user):
        SessionMiddleware(lambda req: None).process_request(request)
        request.session['_auth_user_id'] = str(user.pk)
        request.session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
        request.session['_auth_user_hash'] = user.get_session_auth_hash()
        request.session.save()
        request.user = AnonymousUser()
        request.META['HTTP_AUTHORIZATION'] = f'Bearer session-{request.session.session_key}'
        return request

    # 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
    def _attach_order_expiry_asset(self, order, expires_at, *, asset_name=None, status=None, source=None, is_active=True):
        asset = CloudAsset.objects.filter(order=order, kind=CloudAsset.KIND_SERVER).order_by('-sort_order', '-id').first()
        if asset:
            asset.actual_expires_at = expires_at
            asset.save(update_fields=['actual_expires_at', 'updated_at'])
            return asset
        return CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=source or CloudAsset.SOURCE_ORDER,
            order=order,
            user=order.user,
            provider=order.provider,
            cloud_account=order.cloud_account,
            account_label=order.account_label,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=asset_name or order.server_name or order.instance_id or order.order_no,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            previous_public_ip=order.previous_public_ip,
            actual_expires_at=expires_at,
            status=status or CloudAsset.STATUS_RUNNING,
            is_active=is_active,
        )

    # 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
    def _aws_test_account(self):
        account = getattr(self, '_cached_aws_test_account', None)
        if account:
            return account
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name=f'aws-test-{self.user.id}',
            region_hint=self.plan.region_code,
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        self._cached_aws_test_account = account
        return account

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_aws_client_requires_bound_account(self):
        from cloud.lifecycle import _aws_client

        with self.assertRaisesMessage(ValueError, '缺少绑定的 AWS 云账号'):
            _aws_client(self.plan.region_code, None)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aws_create_client_requires_bound_account(self):
        from cloud.aws_lightsail import _aws_client_from_order_data

        client, error = _aws_client_from_order_data({
            'provider': 'aws_lightsail',
            'region_code': self.plan.region_code,
            'order_no': 'AWS-CREATE-NO-ACCOUNT-1',
        })

        self.assertIsNone(client)
        self.assertIn('缺少绑定的 AWS 云账号', error)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aliyun_create_and_renew_require_bound_account(self):
        from cloud.aliyun_simple import _create_instance_sync

        expires_at = timezone.now() + timezone.timedelta(days=1)
        order = CloudServerOrder.objects.create(
            order_no='ALIYUN-NO-ACCOUNT-1',
            user=self.user,
            plan=self.plan,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='香港',
            plan_name='基础型',
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='47.1.1.1',
            instance_id='aliyun-instance-without-account',
        )
        self._attach_order_expiry_asset(order, expires_at, source=CloudAsset.SOURCE_ALIYUN)

        create_result = _create_instance_sync(order, 'aliyun-no-account')
        with self.assertRaisesMessage(ValueError, '缺少订单绑定的启用阿里云账号'):
            apply_cloud_server_renewal.__wrapped__(order.id, 31, False)
        self.assertFalse(create_result.ok)
        self.assertIn('缺少订单绑定的启用云账号', create_result.note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_renewal_aws_runtime_check_requires_bound_account(self):
        from cloud.services import _aws_lightsail_client_for_order

        order = CloudServerOrder.objects.create(
            order_no='AWS-RUNTIME-NO-ACCOUNT',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='44.44.44.44',
        )

        with self.assertRaisesMessage(ValueError, '缺少绑定的 AWS 云账号'):
            _aws_lightsail_client_for_order(order)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_servers_missing_state_does_not_bypass_provider_confirmation(self):
        order = CloudServerOrder.objects.create(
            order_no='SYNC-SERVERS-NO-INSTANT-DELETE',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            server_name='sync-servers-still-confirming',
            instance_id='sync-servers-still-confirming',
            public_ip='44.44.44.45',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        updated = _apply_server_missing_state('aws_lightsail', self.plan.region_code, [], None)

        self.assertEqual(updated, 0)
        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(order.status, 'completed')
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(server.status, CloudAsset.STATUS_RUNNING)

    # 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
    def _create_auto_renew_asset(self, order, *, status=None, asset_name=None, expires_at=None):
        return CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=order.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=asset_name or f'{order.order_no}-asset',
            public_ip=order.public_ip,
            actual_expires_at=expires_at if expires_at is not None else order_asset_expiry(order),
            status=status or CloudAsset.STATUS_RUNNING,
        )

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dedupe_cloud_assets_does_not_merge_cross_account_same_ip(self):
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label='aws+111+primary',
            region_code='ap-southeast-1',
            asset_name='asset-account-a',
            public_ip='13.250.30.10',
            status=CloudAsset.STATUS_RUNNING,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label='aws+222+secondary',
            region_code='ap-southeast-1',
            asset_name='asset-account-b',
            public_ip='13.250.30.10',
            status=CloudAsset.STATUS_RUNNING,
        )

        call_command('dedupe_cloud_assets')

        self.assertEqual(CloudAsset.objects.filter(public_ip='13.250.30.10').count(), 2)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dedupe_cloud_assets_does_not_merge_old_account_label_variants(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='dedupe-label-variant',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        old_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label='aws_lightsail+123456789012+dedupe-label-variant',
            region_code='ap-southeast-1',
            asset_name='dedupe-label-variant-old',
            public_ip='13.250.30.13',
            status=CloudAsset.STATUS_RUNNING,
        )
        keep_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='ap-southeast-1',
            asset_name='dedupe-label-variant-new',
            public_ip='13.250.30.13',
            status=CloudAsset.STATUS_RUNNING,
        )
        log = CloudIpLog.objects.create(
            event_type=CloudIpLog.EVENT_CHANGED,
            asset=old_asset,
            public_ip='13.250.30.13',
            note='old duplicate log',
        )

        call_command('dedupe_cloud_assets')

        self.assertEqual(CloudAsset.objects.filter(public_ip='13.250.30.13').count(), 2)
        self.assertTrue(CloudAsset.objects.filter(id=old_asset.id).exists())
        self.assertTrue(CloudAsset.objects.filter(id=keep_asset.id).exists())
        log.refresh_from_db()
        self.assertEqual(log.asset_id, old_asset.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_assets_list_keeps_old_account_label_variants_separate(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='ui-dedupe-label-variant',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label='aws_lightsail+123456789012+ui-dedupe-label-variant',
            region_code='ap-southeast-1',
            asset_name='ui-dedupe-old',
            public_ip='13.250.30.14',
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        keep_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='ap-southeast-1',
            asset_name='ui-dedupe-new',
            public_ip='13.250.30.14',
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        admin = get_user_model().objects.create_user(username='ui_dedupe_admin', password='x', is_staff=True)
        request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1'})
        self._attach_bearer_session(request, admin)

        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['total'], 2)
        item_ids = {item['id'] for item in payload['items']}
        self.assertIn(keep_asset.id, item_ids)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_assets_list_uses_bulk_order_inference_without_per_asset_fallback(self):
        expires_at = timezone.now() + timezone.timedelta(days=30)
        order = CloudServerOrder.objects.create(
            order_no='BULK-LIST-INFER-001',
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
            public_ip='10.77.88.1',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='bulk-list-infer-asset',
            public_ip='10.77.88.1',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
        )
        admin = get_user_model().objects.create_user(username='bulk_list_infer_admin', password='x', is_staff=True)
        request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1'})
        self._attach_bearer_session(request, admin)

        with patch('cloud.api_assets._infer_asset_order', side_effect=AssertionError('per-asset order inference should not run')):
            response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        row = next(item for item in payload['items'] if item['id'] == asset.id)
        self.assertEqual(row['order_id'], order.id)
        self.assertEqual(row['user_id'], self.user.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_assets_list_does_not_persist_unattached_ip_expiry(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='list-unattached-no-write',
            public_ip='10.77.88.2',
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            actual_expires_at=None,
        )
        admin = get_user_model().objects.create_user(username='list_unattached_no_write_admin', password='x', is_staff=True)
        request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'risk_status': 'unattached_ip'})
        self._attach_bearer_session(request, admin)

        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        row = next(item for item in payload['items'] if item['id'] == asset.id)
        self.assertTrue(row['actual_expires_at'])
        asset.refresh_from_db()
        self.assertIsNone(asset.actual_expires_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_asset_dashboard_snapshot_refresh_materializes_paginated_list(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='snapshot-list-asset',
            public_ip='10.77.88.3',
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        summary = refresh_cloud_asset_dashboard_snapshots(asset_ids=[asset.id], reason='test', full=False)
        self.assertEqual(summary['assets'], 1)
        self.assertTrue(CloudAssetDashboardSnapshot.objects.filter(asset=asset, search_text__icontains='snapshot-list-asset').exists())

        admin = get_user_model().objects.create_user(username='snapshot_list_admin', password='x', is_staff=True)
        request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'keyword': 'snapshot-list-asset'})
        self._attach_bearer_session(request, admin)
        with patch('cloud.api_assets._cloud_asset_payloads', side_effect=AssertionError('list should read dashboard snapshots')):
            response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['total'], 1)
        self.assertEqual(payload['items'][0]['id'], asset.id)

    # 功能：云账号异常资产仍必须出现在默认全部列表，避免成为无法管理的孤儿资产。
    def test_cloud_assets_list_all_includes_disabled_or_missing_cloud_account_assets(self):
        disabled_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='snapshot-disabled-account',
            external_account_id='snapshot-disabled-account-id',
            access_key='snapshot-disabled-ak',
            secret_key='snapshot-disabled-sk',
            region_hint=self.plan.region_code,
            is_active=False,
        )
        disabled_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=disabled_account,
            account_label=cloud_account_label(disabled_account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='snapshot-disabled-account-asset',
            public_ip='10.77.88.31',
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        missing_account_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='snapshot-missing-account-asset',
            public_ip='10.77.88.32',
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        refresh_cloud_asset_dashboard_snapshots(
            asset_ids=[disabled_asset.id, missing_account_asset.id],
            reason='test',
            full=False,
        )

        admin = get_user_model().objects.create_user(username='snapshot_all_orphan_admin', password='x', is_staff=True)
        request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1'})
        self._attach_bearer_session(request, admin)
        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']
        item_ids = {item['id'] for item in payload['items']}

        self.assertEqual(response.status_code, 200)
        self.assertIn(disabled_asset.id, item_ids)
        self.assertIn(missing_account_asset.id, item_ids)
        self.assertEqual(payload['risk_counts']['all'], 2)
        self.assertEqual(payload['risk_counts']['account_disabled'], 2)

    # 功能：风险计数必须保持云账号异常与其他标签的口径一致，避免优化后首屏统计失真。
    def test_cloud_assets_list_risk_counts_keep_disabled_account_isolated(self):
        active_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='snapshot-risk-counts-active',
            region_hint=self.plan.region_code,
            is_active=True,
        )
        disabled_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='snapshot-risk-counts-disabled',
            region_hint=self.plan.region_code,
            is_active=False,
        )
        active_label = cloud_account_label(active_account)
        disabled_label = cloud_account_label(disabled_account)
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=active_account,
            account_label=active_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='snapshot-risk-counts-normal',
            public_ip='10.77.89.10',
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=active_account,
            account_label=active_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='snapshot-risk-counts-expired',
            public_ip='10.77.89.11',
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=disabled_account,
            account_label=disabled_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='snapshot-risk-counts-disabled',
            public_ip='10.77.89.12',
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        refresh_cloud_asset_dashboard_snapshots(reason='test', full=True)

        admin = get_user_model().objects.create_user(username='snapshot_risk_counts_admin', password='x', is_staff=True)
        request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1'})
        self._attach_bearer_session(request, admin)
        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['risk_counts']['all'], 3)
        self.assertEqual(payload['risk_counts']['normal'], 1)
        self.assertEqual(payload['risk_counts']['expired'], 1)
        self.assertEqual(payload['risk_counts']['account_disabled'], 1)

    # 功能：百万级 MySQL 快照表风险计数必须按索引拆分 count，避免单条 aggregate 冷缓存拖慢首屏。
    def test_cloud_asset_dashboard_risk_counts_do_not_use_single_aggregate(self):
        class FakeSnapshotQuerySet:
            count_by_filters = {
                (): 100,
                (('risk_account_disabled', True),): 40,
                (('risk_normal', True), ('risk_account_disabled', False)): 10,
                (('risk_due_soon', True), ('risk_account_disabled', False)): 11,
                (('risk_expired', True), ('risk_account_disabled', False)): 12,
                (('risk_unattached_ip', True), ('risk_account_disabled', False)): 13,
                (('risk_abnormal', True), ('risk_account_disabled', False)): 14,
                (('risk_shutdown_disabled', True), ('risk_account_disabled', False)): 15,
                (('risk_unbound_user', True), ('risk_account_disabled', False)): 16,
                (('risk_unbound_group', True), ('risk_account_disabled', False)): 17,
                (('risk_auto_renew_off', True), ('risk_account_disabled', False)): 18,
                (('risk_deleted', True), ('risk_account_disabled', False)): 19,
            }

            def __init__(self, filters=()):
                self.filters = tuple(filters)

            def order_by(self):
                raise RuntimeError('skip cache key for fake queryset')

            def filter(self, **kwargs):
                return FakeSnapshotQuerySet((*self.filters, *kwargs.items()))

            def count(self):
                return self.count_by_filters[self.filters]

            def aggregate(self, **_kwargs):
                raise AssertionError('risk counts must not use single aggregate on large MySQL tables')

        counts = _dashboard_snapshot_risk_counts(FakeSnapshotQuerySet())

        self.assertEqual(counts['all'], 100)
        self.assertEqual(counts['account_disabled'], 40)
        self.assertEqual(counts['normal'], 10)
        self.assertEqual(counts['unattached_ip'], 13)

    # 功能：缺失的代理列表快照必须能被分批补齐，避免百万压测资产成为不可见孤儿资产。
    def test_cloud_asset_dashboard_snapshot_backfill_materializes_missing_assets(self):
        assets = []
        for index in range(3):
            assets.append(CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'snapshot-missing-backfill-{index}',
                public_ip=f'10.77.88.{70 + index}',
                status=CloudAsset.STATUS_RUNNING,
                actual_expires_at=timezone.now() + timezone.timedelta(days=30),
            ))
        refresh_cloud_asset_dashboard_snapshots(asset_ids=[assets[0].id], reason='test', full=False)

        summary = backfill_cloud_asset_dashboard_snapshots(reason='test', batch_size=1, max_batches=5)

        self.assertEqual(summary['batches'], 2)
        self.assertEqual(summary['created'], 2)
        self.assertEqual(
            set(CloudAssetDashboardSnapshot.objects.values_list('asset_id', flat=True)),
            {asset.id for asset in assets},
        )

    # 功能：默认补齐只处理缺失快照，不进入百万级旧快照扫描。
    def test_cloud_asset_dashboard_snapshot_backfill_skips_stale_by_default(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='snapshot-stale-default-skip',
            public_ip='10.77.88.79',
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        refresh_cloud_asset_dashboard_snapshots(asset_ids=[asset.id], reason='test', full=False)
        asset.asset_name = 'snapshot-stale-default-skip-updated'
        asset.save(update_fields=['asset_name', 'updated_at'])

        with patch('cloud.api_asset_snapshots._next_stale_snapshot_asset_ids', side_effect=AssertionError('stale scan should be explicit')):
            summary = backfill_cloud_asset_dashboard_snapshots(reason='test', batch_size=1, max_batches=1)

        self.assertEqual(summary['batches'], 0)

    # 功能：大量缺失快照不应阻塞列表请求，但必须触发后台分批补齐。
    def test_cloud_assets_list_defers_large_missing_snapshot_backfill(self):
        assets = []
        for index in range(3):
            assets.append(CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'snapshot-missing-large-{index}',
                public_ip=f'10.77.88.{80 + index}',
                status=CloudAsset.STATUS_RUNNING,
                actual_expires_at=timezone.now() + timezone.timedelta(days=30),
            ))
        refresh_cloud_asset_dashboard_snapshots(asset_ids=[assets[0].id], reason='test', full=False)

        admin = get_user_model().objects.create_user(username='snapshot_missing_large_admin', password='x', is_staff=True)
        request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1'})
        self._attach_bearer_session(request, admin)
        with patch('cloud.api_asset_snapshots._SNAPSHOT_SYNC_REFRESH_LIMIT', 1), \
            patch('cloud.api_asset_snapshots._defer_cloud_asset_dashboard_snapshot_backfill', return_value=True) as deferred_backfill:
            response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['total'], 1)
        deferred_backfill.assert_called_once()

    # 功能：验证 IP 轻量视图只返回列表必要字段，避免大列表加载完整代理 payload。
    def test_cloud_assets_list_compact_returns_ip_view_payload(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='snapshot-compact-ip-asset',
            public_ip='10.77.88.41',
            status=CloudAsset.STATUS_RUNNING,
            mtproxy_link='tg://proxy?server=10.77.88.41&port=443&secret=hidden',
            proxy_links=[{'url': 'tg://proxy?server=10.77.88.41&port=443&secret=hidden'}],
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
            price=Decimal('5.123456'),
            currency='USDT',
        )
        refresh_cloud_asset_dashboard_snapshots(asset_ids=[asset.id], reason='test', full=False)

        admin = get_user_model().objects.create_user(username='snapshot_compact_admin', password='x', is_staff=True)
        request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'compact': '1'})
        self._attach_bearer_session(request, admin)
        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']
        row = next(item for item in payload['items'] if item['id'] == asset.id)
        snapshot = CloudAssetDashboardSnapshot.objects.get(asset=asset)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(row['public_ip'], '10.77.88.41')
        self.assertEqual(snapshot.asset_due_sort_at, asset.actual_expires_at)
        self.assertTrue(snapshot.is_display_visible)
        self.assertEqual(row['price'], '5.12')
        self.assertIn('status_countdown', row)
        self.assertNotIn('mtproxy_link', row)
        self.assertNotIn('proxy_links', row)
        self.assertNotIn('provider_resource_id', row)

    # 功能：验证未绑定用户资产在分组分页中保持独立分组键，避免最后一页被合并丢组。
    def test_cloud_assets_list_compact_keeps_unbound_group_key(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='snapshot-compact-unbound-asset',
            public_ip='10.77.88.61',
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        refresh_cloud_asset_dashboard_snapshots(asset_ids=[asset.id], reason='test', full=False)

        admin = get_user_model().objects.create_user(username='compact_unbound_group_admin', password='x', is_staff=True)
        request = self.factory.get('/api/admin/cloud-assets/', {
            'compact': '1',
            'group_by': 'user',
            'grouped': '1',
            'paginated': '1',
        })
        self._attach_bearer_session(request, admin)
        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']
        group = next(item for item in payload['groups'] if item['items'][0]['id'] == asset.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(group['user_key'], f'unbound:{asset.id}')
        self.assertNotEqual(group['user_key'], 'user:unbound')

    # 功能：风险标签使用快照表已有组合索引顺序，避免大表默认排序拖慢 IP 视图。
    def test_cloud_assets_risk_ordering_uses_existing_page_indexes(self):
        due_group_ordering = ['asset_due_sort_null_rank', 'asset_due_sort_at', 'group_user_label', 'group_user_key', '-asset_id']
        for risk_status in [
            'abnormal',
            'due_soon',
            'expired',
            'normal',
            'unattached_ip',
            'unbound_group',
            'unbound_user',
        ]:
            self.assertEqual(_dashboard_snapshot_ordering('', '', risk_status), due_group_ordering)
        for risk_status in ['auto_renew_off', 'shutdown_disabled']:
            self.assertEqual(
                _dashboard_snapshot_ordering('', '', risk_status),
                ['group_telegram_key', 'group_telegram_label', '-asset_id'],
            )
        self.assertEqual(
            _dashboard_snapshot_ordering('actual_expires_at', 'asc', 'unbound_user'),
            ['asset_due_sort_null_rank', 'asset_due_sort_at', 'risk_rank', '-sort_order', '-asset_id'],
        )

    # 功能：同步状态数量复用仪表盘快照，避免后台代理列表刷新时反复扫描 CloudAsset 大表。
    def test_cloud_assets_sync_status_counts_use_dashboard_snapshots(self):
        active_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='sync-status-active-account',
            external_account_id='sync-status-active',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        inactive_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='sync-status-inactive-account',
            external_account_id='sync-status-inactive',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            is_active=False,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=active_account,
            account_label=cloud_account_label(active_account),
            region_code='ap-southeast-1',
            asset_name='sync-status-aws',
            instance_id='sync-status-aws',
            public_ip='10.88.91.1',
            status=CloudAsset.STATUS_RUNNING,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=active_account,
            account_label=cloud_account_label(active_account),
            region_code='ap-southeast-1',
            asset_name='sync-status-unattached',
            public_ip='10.88.91.2',
            provider_status='未附加',
            status=CloudAsset.STATUS_RUNNING,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            user=self.user,
            provider='aliyun_simple',
            cloud_account=active_account,
            account_label=cloud_account_label(active_account),
            region_code='cn-hongkong',
            asset_name='sync-status-aliyun',
            instance_id='sync-status-aliyun',
            public_ip='10.88.91.3',
            status=CloudAsset.STATUS_RUNNING,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=inactive_account,
            account_label=cloud_account_label(inactive_account),
            region_code='ap-southeast-1',
            asset_name='sync-status-inactive',
            instance_id='sync-status-inactive',
            public_ip='10.88.91.4',
            status=CloudAsset.STATUS_RUNNING,
        )
        refresh_cloud_asset_dashboard_snapshots(reason='sync-status-test', full=True)

        counts = _cloud_assets_sync_status_counts()

        self.assertEqual(counts['aws_existing_count'], 2)
        self.assertEqual(counts['aliyun_existing_count'], 1)
        self.assertEqual(counts['unattached_ip_count'], 1)
        self.assertIsNotNone(_latest_synced_cloud_asset_updated_at())

    # 功能：大量快照过期时列表接口不应在请求内同步全量刷新，避免大数据页面超时。
    def test_cloud_assets_list_defers_large_stale_snapshot_refresh(self):
        assets = []
        for index in range(2):
            assets.append(CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'snapshot-stale-large-{index}',
                public_ip=f'10.77.89.{index + 1}',
                status=CloudAsset.STATUS_RUNNING,
                actual_expires_at=timezone.now() + timezone.timedelta(days=30),
            ))
        refresh_cloud_asset_dashboard_snapshots(asset_ids=[asset.id for asset in assets], reason='test', full=False)
        for asset in assets:
            asset.asset_name = f'{asset.asset_name}-updated'
            asset.save(update_fields=['asset_name', 'updated_at'])

        admin = get_user_model().objects.create_user(username='snapshot_stale_large_admin', password='x', is_staff=True)
        request = self.factory.get('/api/admin/cloud-assets/', {'compact': '1', 'grouped': '1', 'paginated': '1', 'group_by': 'user'})
        self._attach_bearer_session(request, admin)
        with patch('cloud.api_asset_snapshots._SNAPSHOT_SYNC_REFRESH_LIMIT', 1), \
            patch('cloud.api_asset_snapshots._defer_cloud_asset_dashboard_snapshot_backfill', side_effect=AssertionError('stale backfill should be explicit')), \
            patch('cloud.api_asset_snapshots.refresh_cloud_asset_dashboard_snapshots', side_effect=AssertionError('large stale snapshots should not block list requests')):
            response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['total'], 1)
        self.assertEqual(len(payload['groups']), 1)
        self.assertEqual({item['id'] for item in payload['items']}, {asset.id for asset in assets})

    # 功能：验证删除计划轻量字段开关会移除备注和执行详情，降低大列表 payload。
    def test_lifecycle_plans_fields_basic_omits_notes_and_execution_payload(self):
        for index in range(12):
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'snapshot-lifecycle-fields-asset-{index}',
                public_ip=f'10.77.88.{50 + index}',
                status=CloudAsset.STATUS_RUNNING,
                actual_expires_at=timezone.now() - timezone.timedelta(days=3),
                note='这是一段很长的删除计划备注',
            )
        admin = get_user_model().objects.create_user(username='lifecycle_fields_admin', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'compact': '1', 'fields': 'basic', 'limit': '5', 'refresh': '1'})
        self._attach_bearer_session(request, admin)
        response = lifecycle_plans(request)
        payload = json.loads(response.content.decode('utf-8'))['data']
        row = payload['shutdown_plan_items'][0]

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(payload['shutdown_plan_items']), 5)
        self.assertGreater(payload['shutdown_plan_count'], len(payload['shutdown_plan_items']))
        for old_field in ['due_items', 'future_plan_items', 'history_items', 'shutdown_items', 'ip_delete_items']:
            self.assertNotIn(old_field, payload)
        self.assertNotIn('note', row)
        self.assertNotIn('display_note', row)
        self.assertNotIn('execution_status', row)
        self.assertNotIn('execution_plan', row)

    # 功能：验证计划页局部翻页只返回当前表 items，避免翻一个表时重算所有深页。
    def test_lifecycle_plans_tables_param_returns_only_requested_items(self):
        CloudIpLog.objects.create(
            event_type=CloudIpLog.EVENT_RECYCLED,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='partial-ip-history-only',
            previous_public_ip='5.5.20.8',
            note='固定 IP 保留期结束，AWS 固定 IP 已真实释放',
        )
        admin = get_user_model().objects.create_user(username='lifecycle_partial_admin', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {
            'compact': '1',
            'fields': 'basic,execution',
            'ip_delete_history_page': '1',
            'ip_delete_history_page_size': '5',
            'limit': '5',
            'tables': 'ip_delete_history',
        })
        self._attach_bearer_session(request, admin)
        response = lifecycle_plans(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertIn('ip_delete_history_items', payload)
        self.assertNotIn('shutdown_plan_items', payload)
        self.assertNotIn('server_delete_items', payload)
        self.assertNotIn('server_history_items', payload)
        self.assertNotIn('ip_delete_plan_items', payload)
        self.assertEqual(payload['pagination']['ip_delete_history']['page'], 1)
        self.assertTrue(any(item.get('asset_name') == 'partial-ip-history-only' for item in payload['ip_delete_history_items']))

    # 功能：验证云资产列表快照搜索文本不会持久化代理密钥。
    def test_cloud_asset_dashboard_snapshot_search_text_masks_proxy_secret(self):
        secret = 'ee0123456789abcdef0123456789abcdef'
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='snapshot-secret-asset',
            public_ip='10.77.88.4',
            mtproxy_host='10.77.88.4',
            mtproxy_link=f'tg://proxy?server=10.77.88.4&port=9528&secret={secret}',
            proxy_links=[{
                'name': '主代理 mtg',
                'server': '10.77.88.4',
                'port': '9528',
                'secret': secret,
                'url': f'tg://proxy?server=10.77.88.4&port=9528&secret={secret}',
            }],
            status=CloudAsset.STATUS_RUNNING,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )

        refresh_cloud_asset_dashboard_snapshots(asset_ids=[asset.id], reason='test', full=False)
        snapshot = CloudAssetDashboardSnapshot.objects.get(asset=asset)

        self.assertIn('snapshot-secret-asset', snapshot.search_text)
        self.assertIn('10.77.88.4', snapshot.search_text)
        self.assertIn('9528', snapshot.search_text)
        self.assertNotIn(secret, snapshot.search_text)
        self.assertNotIn('secret=', snapshot.search_text)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dedupe_cloud_assets_does_not_merge_cross_region_same_instance(self):
        for region, public_ip in [('ap-southeast-1', '13.250.30.15'), ('ap-northeast-1', '13.250.30.16')]:
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                provider='aws_lightsail',
                account_label='aws+111+primary',
                region_code=region,
                asset_name='asset-region-scope',
                instance_id='same-instance-name',
                public_ip=public_ip,
                status=CloudAsset.STATUS_RUNNING,
            )

        call_command('dedupe_cloud_assets')

        self.assertEqual(CloudAsset.objects.filter(instance_id='same-instance-name').count(), 2)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dedupe_cloud_assets_keeps_same_instance_with_different_ips(self):
        for public_ip in ['13.250.31.15', '13.250.31.16']:
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                provider='aws_lightsail',
                account_label='aws+111+primary',
                region_code='ap-southeast-1',
                asset_name='asset-same-instance-different-ip',
                instance_id='same-instance-different-ip',
                public_ip=public_ip,
                status=CloudAsset.STATUS_RUNNING,
            )

        call_command('dedupe_cloud_assets')

        self.assertEqual(CloudAsset.objects.filter(instance_id='same-instance-different-ip').count(), 2)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dedupe_servers_does_not_delete_cross_account_instance_id(self):
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label='aws+111+primary',
            region_code='ap-southeast-1',
            asset_name='server-account-a',
            instance_id='same-instance-name',
            public_ip='13.250.30.11',
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            account_label='aws+222+secondary',
            region_code='ap-southeast-1',
            asset_name='server-account-b',
            instance_id='same-instance-name',
            public_ip='13.250.30.12',
        )

        call_command('dedupe_servers')

        self.assertEqual(CloudAsset.objects.filter(instance_id='same-instance-name').count(), 2)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dedupe_servers_does_not_delete_cross_region_instance_id(self):
        for region, public_ip in [('ap-southeast-1', '13.250.30.17'), ('ap-northeast-1', '13.250.30.18')]:
            CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                provider='aws_lightsail',
                account_label='aws+111+primary',
                region_code=region,
                asset_name='server-region-scope',
                instance_id='same-region-instance-name',
                public_ip=public_ip,
            )

        call_command('dedupe_servers')

        self.assertEqual(CloudAsset.objects.filter(instance_id='same-region-instance-name').count(), 2)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dedupe_servers_keeps_same_instance_with_different_ips(self):
        for public_ip in ['13.250.31.17', '13.250.31.18']:
            CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                provider='aws_lightsail',
                account_label='aws+111+primary',
                region_code='ap-southeast-1',
                asset_name='server-same-instance-different-ip',
                instance_id='server-same-instance-different-ip',
                public_ip=public_ip,
            )

        call_command('dedupe_servers')

        self.assertEqual(CloudAsset.objects.filter(instance_id='server-same-instance-different-ip').count(), 2)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_upsert_cloud_asset_keeps_assets_separated_by_account(self):
        for account_label, public_ip in [('aws+111+primary', '13.250.30.13'), ('aws+222+secondary', '13.250.30.14')]:
            call_command(
                'upsert_cloud_asset',
                kind=CloudAsset.KIND_SERVER,
                provider='aws_lightsail',
                account_label=account_label,
                region_code='ap-southeast-1',
                instance_id='manual-same-instance',
                public_ip=public_ip,
            )

        self.assertEqual(CloudAsset.objects.filter(instance_id='manual-same-instance').count(), 2)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_upsert_cloud_asset_keeps_same_instance_with_different_ips(self):
        for public_ip in ['13.250.31.19', '13.250.31.20']:
            call_command(
                'upsert_cloud_asset',
                kind=CloudAsset.KIND_SERVER,
                provider='aws_lightsail',
                account_label='aws+111+primary',
                region_code='ap-southeast-1',
                instance_id='manual-same-instance-different-ip',
                public_ip=public_ip,
            )

        self.assertEqual(CloudAsset.objects.filter(instance_id='manual-same-instance-different-ip').count(), 2)
        self.assertTrue(CloudAsset.objects.filter(instance_id='manual-same-instance-different-ip', public_ip='13.250.31.19').exists())
        self.assertTrue(CloudAsset.objects.filter(instance_id='manual-same-instance-different-ip', public_ip='13.250.31.20').exists())

    # 功能：验证同订单新身份的资产创建不会覆盖已有人工备注记录。
    def test_asset_create_with_new_identity_does_not_overwrite_same_order_note(self):
        order = CloudServerOrder.objects.create(
            order_no='SERVER-CREATE-NEW-IDENTITY-NOTE',
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
        )
        existing = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            asset_name='existing-note-server',
            instance_id='existing-note-instance',
            public_ip='13.250.31.21',
            note='已有人工备注',
        )

        created = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            asset_name='created-note-server',
            instance_id='created-note-instance',
            public_ip='13.250.31.22',
            note='新资产备注',
        )

        existing.refresh_from_db()
        self.assertNotEqual(existing.id, created.id)
        self.assertEqual(CloudAsset.objects.filter(order=order).count(), 2)
        self.assertEqual(existing.instance_id, 'existing-note-instance')
        self.assertEqual(existing.public_ip, '13.250.31.21')
        self.assertEqual(existing.note, '已有人工备注')
        self.assertEqual(created.note, '新资产备注')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_rebind_cloud_server_user_syncs_order_asset_and_server(self):
        new_user = TelegramUser.objects.create(tg_user_id=990002, username='svc_rebind_new')
        expires_at = timezone.now() + timezone.timedelta(days=20)
        order = CloudServerOrder.objects.create(
            order_no='REBIND-SYNC-1',
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
            server_name='rebind-sync-server',
            instance_id='rebind-sync-server',
            public_ip='13.250.10.21',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            source=CloudAsset.SOURCE_ORDER,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            status=CloudAsset.STATUS_RUNNING,
        )

        rebound = async_to_sync(rebind_cloud_server_user)(order.id, new_user.id)

        self.assertEqual(rebound.user_id, new_user.id)
        self.assertEqual(rebound.last_user_id, new_user.tg_user_id)
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(asset.user_id, new_user.id)
        self.assertEqual(server.user_id, new_user.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_order_wallet_pay_uses_total_amount_not_address_unique_amount(self):
        self.user.balance = Decimal('19.00')
        self.user.save(update_fields=['balance', 'updated_at'])
        order = CloudServerOrder.objects.create(
            order_no='WALLET-PAY-BASE-AMOUNT-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.777',
            pay_method='address',
            status='pending',
        )

        paid_order, err = async_to_sync(pay_cloud_server_order_with_balance)(order.id, self.user.id, 'USDT')

        self.assertIsNone(err)
        self.assertEqual(paid_order.status, 'paid')
        self.user.refresh_from_db()
        paid_order.refresh_from_db()
        self.assertEqual(self.user.balance, Decimal('0.000000'))
        self.assertEqual(paid_order.pay_amount, Decimal('19.000000000'))
        self.assertEqual(paid_order.currency, 'USDT')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_order_wallet_pay_trx_converts_total_amount_once(self):
        self.user.balance_trx = Decimal('100.00')
        self.user.save(update_fields=['balance_trx', 'updated_at'])
        order = CloudServerOrder.objects.create(
            order_no='WALLET-PAY-TRX-BASE-AMOUNT-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='TRX',
            total_amount='19.00',
            pay_amount='101.000',
            pay_method='address',
            status='pending',
        )

        # 功能：处理 云资产、云订单和生命周期 中的 fake usdt to trx 业务流程。
        async def fake_usdt_to_trx(amount):
            self.assertEqual(amount, Decimal('19.000000'))
            return Decimal('100.00')

        with patch('cloud.services.usdt_to_trx', fake_usdt_to_trx):
            paid_order, err = async_to_sync(pay_cloud_server_order_with_balance)(order.id, self.user.id, 'TRX')

        self.assertIsNone(err)
        self.assertEqual(paid_order.status, 'paid')
        self.user.refresh_from_db()
        paid_order.refresh_from_db()
        self.assertEqual(self.user.balance_trx, Decimal('0.000000'))
        self.assertEqual(paid_order.pay_amount, Decimal('100.000000000'))
        self.assertEqual(paid_order.currency, 'TRX')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_renewal_address_order_uses_usdt_even_after_trx_wallet_order(self):
        expires_at = timezone.now() + timezone.timedelta(days=10)
        order = CloudServerOrder.objects.create(
            order_no='RENEW-TRX-SOURCE-USDT-ADDRESS-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='TRX',
            total_amount='19.00',
            pay_amount='100.00',
            pay_method='balance',
            status='completed',
            public_ip='8.8.4.80',
            service_started_at=timezone.now() - timezone.timedelta(days=20),
        )
        self._attach_order_expiry_asset(order, expires_at)

        renewal = async_to_sync(create_cloud_server_renewal_for_user)(order.id, self.user.id, 31)

        self.assertEqual(renewal.status, 'renew_pending')
        self.assertEqual(renewal.currency, 'USDT')
        self.assertEqual(renewal.total_amount, Decimal('19.00'))
        self.assertGreaterEqual(renewal.pay_amount, Decimal('19.001000000'))
        self.assertLess(renewal.pay_amount, Decimal('20.000000000'))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_address_order_forces_usdt_when_requested_trx(self):
        order = async_to_sync(create_cloud_server_order)(self.user.id, self.plan.id, 'TRX', 1)

        self.assertEqual(order.currency, 'USDT')
        self.assertEqual(order.total_amount, Decimal('19.00'))
        self.assertGreaterEqual(order.pay_amount, Decimal('19.001000000'))
        self.assertLess(order.pay_amount, Decimal('20.000000000'))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unbound_asset_renewal_address_order_forces_usdt_from_trx_source(self):
        self.plan.currency = 'TRX'
        self.plan.save(update_fields=['currency'])
        due_at = timezone.now() + timezone.timedelta(days=9)
        account = self._aws_test_account()
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unbound-renewal-trx-source',
            public_ip='31.31.31.37',
            previous_public_ip='31.31.31.37',
            actual_expires_at=due_at,
            price='19.00',
            currency='TRX',
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            mtproxy_port=9528,
            is_active=False,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.37&port=9528&secret=eeeeeeeeeeeeeeee',
            'server': '31.31.31.37',
            'port': '9528',
            'secret': 'eeeeeeeeeeeeeeee',
        }

        order, error = async_to_sync(prepare_cloud_asset_renewal_with_link)(asset.id, self.user.id, self.plan.id, link)

        self.assertIsNone(error)
        self.assertEqual(order.currency, 'USDT')
        self.assertEqual(order.total_amount, Decimal('19.00'))
        self.assertGreaterEqual(order.pay_amount, Decimal('19.001000000'))
        self.assertLess(order.pay_amount, Decimal('20.000000000'))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unbound_asset_renewal_rejects_link_port_override(self):
        due_at = timezone.now() + timezone.timedelta(days=9)
        account = self._aws_test_account()
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unbound-renewal-port-override',
            public_ip='31.31.31.40',
            previous_public_ip='31.31.31.40',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.40&port=9528&secret=eeeeeeeeeeeeeeee',
            'server': '31.31.31.40',
            'port': '9528',
            'secret': 'eeeeeeeeeeeeeeee',
        }

        order, error = async_to_sync(prepare_cloud_asset_renewal_with_link)(asset.id, self.user.id, self.plan.id, link)

        self.assertIsNone(order)
        self.assertIn('当前主代理端口是 443', error)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_retained_ip_renewal_address_order_forces_usdt_from_trx_order(self):
        self.plan.currency = 'TRX'
        self.plan.save(update_fields=['currency'])
        recycle_at = timezone.now() + timezone.timedelta(days=9)
        order = CloudServerOrder.objects.create(
            order_no='RETAINED-IP-TRX-SOURCE-USDT-ADDRESS-1',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='TRX',
            total_amount='19.00',
            pay_amount='100.00',
            pay_method='balance',
            status='deleted',
            public_ip='31.31.31.39',
            previous_public_ip='31.31.31.39',
            instance_id='',
            static_ip_name='StaticIp-retained-trx-source',
            ip_recycle_at=recycle_at,
            mtproxy_secret='eeeeeeeeeeeeeeee',
            mtproxy_port=9528,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.39&port=9528&secret=eeeeeeeeeeeeeeee',
            'server': '31.31.31.39',
            'port': '9528',
            'secret': 'eeeeeeeeeeeeeeee',
        }

        renewal, error = async_to_sync(prepare_retained_ip_renewal_with_link)(order.id, self.user.id, self.plan.id, link)

        self.assertIsNone(error)
        self.assertEqual(renewal.currency, 'USDT')
        self.assertEqual(renewal.total_amount, Decimal('19.00'))
        self.assertGreaterEqual(renewal.pay_amount, Decimal('19.001000000'))
        self.assertLess(renewal.pay_amount, Decimal('20.000000000'))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_retained_ip_renewal_rejects_link_port_override(self):
        recycle_at = timezone.now() + timezone.timedelta(days=9)
        order = CloudServerOrder.objects.create(
            order_no='RETAINED-IP-PORT-OVERRIDE-1',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='deleted',
            public_ip='31.31.31.41',
            previous_public_ip='31.31.31.41',
            instance_id='',
            static_ip_name='StaticIp-retained-port-override',
            ip_recycle_at=recycle_at,
            mtproxy_secret='eeeeeeeeeeeeeeee',
            mtproxy_port=443,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.41&port=9528&secret=eeeeeeeeeeeeeeee',
            'server': '31.31.31.41',
            'port': '9528',
            'secret': 'eeeeeeeeeeeeeeee',
        }

        renewal, error = async_to_sync(prepare_retained_ip_renewal_with_link)(order.id, self.user.id, self.plan.id, link)

        self.assertIsNone(renewal)
        self.assertIn('当前主代理端口是 443', error)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_delete_notice_batches_multiple_ips_for_same_user(self):
        now = timezone.now()
        orders = []
        for index in range(2):
            expires_at = now - timezone.timedelta(days=2)
            order = CloudServerOrder.objects.create(
                order_no=f'BATCH-DELETE-NOTICE-{index + 1}',
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
                status='suspended',
                public_ip=f'10.66.0.{index + 1}',
                service_started_at=now - timezone.timedelta(days=35),
                suspend_at=now - timezone.timedelta(days=1),
                delete_at=now + timezone.timedelta(hours=12),
                delete_reminder_enabled=True,
            )
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_ORDER,
                order=order,
                user=self.user,
                provider=order.provider,
                region_code=order.region_code,
                region_name=order.region_name,
                asset_name=f'batch-delete-notice-{index + 1}',
                public_ip=order.public_ip,
                actual_expires_at=expires_at,
                status=CloudAsset.STATUS_RUNNING,
                is_active=True,
            )
            orders.append(order)
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': orders,
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [],
        }
        notify = AsyncMock(return_value=True)

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]):
            async_to_sync(lifecycle_tick)(notify=notify)

        notify.assert_awaited_once()
        _, text, _ = notify.await_args.args
        self.assertIn('10.66.0.1', text)
        self.assertIn('10.66.0.2', text)
        self.assertNotIn('订单号', text)
        self.assertEqual(CloudUserNoticeLog.objects.filter(event_type='delete_notice', is_batch=True).count(), 1)
        for order in orders:
            order.refresh_from_db()
            self.assertIsNotNone(order.delete_notice_sent_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_update_cloud_asset_write_requires_superuser(self):
        staff = get_user_model().objects.create_user(username='staff_asset_update_forbidden', password='x', is_staff=True)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='staff-forbidden-update-asset',
            public_ip='11.11.10.10',
            status=CloudAsset.STATUS_RUNNING,
            price='19.00',
        )

        request = self.factory.patch(
            '/api/admin/cloud-assets/%s/' % asset.id,
            data=json.dumps({
                'public_ip': '11.11.10.11',
                'actual_expires_at': (timezone.now() + timezone.timedelta(days=10)).isoformat(),
                'price': '29.00',
            }),
            content_type='application/json',
        )
        self._attach_bearer_session(request, staff)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content.decode('utf-8'))['message'], '需要超级管理员权限')
        asset.refresh_from_db()
        self.assertEqual(asset.public_ip, '11.11.10.10')
        self.assertEqual(asset.price, Decimal('19.00'))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_update_cloud_asset_blank_mtproxy_secret_preserves_existing_secret(self):
        admin = get_user_model().objects.create_user(username='admin_preserve_asset_secret', password='x', is_staff=True, is_superuser=True)
        order = CloudServerOrder.objects.create(
            order_no='PRESERVE-ASSET-SECRET-1',
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
            mtproxy_secret='order-secret',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='preserve-secret-asset',
            public_ip='11.11.10.12',
            status=CloudAsset.STATUS_RUNNING,
            mtproxy_secret='asset-secret',
        )
        request = self.factory.patch(
            '/api/admin/cloud-assets/%s/' % asset.id,
            data=json.dumps({'mtproxy_secret': ''}),
            content_type='application/json',
        )
        request = self._attach_bearer_session(request, admin)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.mtproxy_secret, 'order-secret')
        self.assertEqual(asset.mtproxy_secret, 'asset-secret')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_update_cloud_asset_mtproxy_link_refreshes_secret_and_proxy_links(self):
        admin = get_user_model().objects.create_user(username='admin_refresh_asset_link', password='x', is_staff=True, is_superuser=True)
        old_link = 'tg://proxy?server=11.11.10.13&port=9528&secret=old-secret'
        new_link = 'tg://proxy?server=11.11.10.13&port=443&secret=new-secret'
        order = CloudServerOrder.objects.create(
            order_no='REFRESH-ASSET-LINK-1',
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
            mtproxy_host='11.11.10.13',
            mtproxy_port=9528,
            mtproxy_secret='old-secret',
            mtproxy_link=old_link,
            proxy_links=[{'name': '主代理 mtg', 'server': '11.11.10.13', 'port': '9528', 'secret': 'old-secret', 'url': old_link}],
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='refresh-asset-link',
            public_ip='11.11.10.13',
            status=CloudAsset.STATUS_RUNNING,
            mtproxy_host='11.11.10.13',
            mtproxy_port=9528,
            mtproxy_secret='old-secret',
            mtproxy_link=old_link,
            proxy_links=[{'name': '主代理 mtg', 'server': '11.11.10.13', 'port': '9528', 'secret': 'old-secret', 'url': old_link}],
        )
        request = self.factory.patch(
            '/api/admin/cloud-assets/%s/' % asset.id,
            data=json.dumps({'mtproxy_link': new_link}),
            content_type='application/json',
        )
        request = self._attach_bearer_session(request, admin)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.mtproxy_link, new_link)
        self.assertEqual(order.mtproxy_secret, 'new-secret')
        self.assertEqual(order.mtproxy_port, 443)
        self.assertEqual(order.proxy_links[0]['url'], new_link)
        self.assertNotIn(old_link, [item.get('url') for item in order.proxy_links])
        self.assertEqual(asset.mtproxy_link, new_link)
        self.assertEqual(asset.mtproxy_secret, 'new-secret')
        self.assertEqual(asset.mtproxy_port, 443)
        self.assertEqual(asset.proxy_links[0]['url'], new_link)
        self.assertNotIn(old_link, [item.get('url') for item in asset.proxy_links])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_update_cloud_asset_rejects_collapsed_telegram_group_binding(self):
        admin = get_user_model().objects.create_user(username='admin_bind_group', password='x', is_staff=True, is_superuser=True)
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
            '/api/admin/cloud-assets/%s/' % asset.id,
            data=json.dumps({'telegram_group_query': hidden_group.chat_id}),
            content_type='application/json',
        )
        self._attach_bearer_session(request, admin)
        response = update_cloud_asset(request, asset.id)
        self.assertEqual(response.status_code, 404)
        self.assertIn('绑定页隐藏', json.loads(response.content.decode('utf-8'))['message'])

        request2 = self.factory.post(
            '/api/admin/cloud-assets/%s/' % asset.id,
            data=json.dumps({'telegram_group_query': visible_group.chat_id}),
            content_type='application/json',
        )
        self._attach_bearer_session(request2, admin)
        response2 = update_cloud_asset(request2, asset.id)
        self.assertEqual(response2.status_code, 200)
        asset.refresh_from_db()
        self.assertEqual(asset.telegram_group_id, visible_group.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_update_cloud_asset_allows_clearing_telegram_group_binding(self):
        admin = get_user_model().objects.create_user(username='admin_unbind_group', password='x', is_staff=True, is_superuser=True)
        group = TelegramGroupFilter.objects.create(
            chat_id=-1002001,
            title='Bound Group',
            username='bound_group',
            enabled=False,
            collapsed=False,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unbind-group-asset',
            public_ip='11.11.22.22',
            status=CloudAsset.STATUS_RUNNING,
            telegram_group=group,
        )

        request = self.factory.post(
            '/api/admin/cloud-assets/%s/' % asset.id,
            data=json.dumps({'telegram_group_id': None}),
            content_type='application/json',
        )
        self._attach_bearer_session(request, admin)
        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        asset.refresh_from_db()
        self.assertIsNone(asset.telegram_group_id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_update_cloud_asset_defers_snapshot_refresh(self):
        admin = get_user_model().objects.create_user(username='admin_defer_asset_refresh', password='x', is_staff=True, is_superuser=True)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='defer-refresh-asset',
            public_ip='10.88.9.9',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        request = self.factory.patch(
            '/api/admin/cloud-assets/%s/' % asset.id,
            data=json.dumps({'is_active': False}),
            content_type='application/json',
        )
        request = self._attach_bearer_session(request, admin)

        with patch('cloud.api_asset_edit._refresh_dashboard_plan_snapshots') as direct_refresh, \
            patch('cloud.api_asset_edit._refresh_dashboard_plan_snapshots_deferred') as deferred_refresh:
            response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        direct_refresh.assert_not_called()
        deferred_refresh.assert_called_once_with(f'cloud_asset:{asset.id}', cloud_asset_ids=[asset.id])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_assets_paginated_uses_true_database_pages(self):
        admin = get_user_model().objects.create_user(username='admin_asset_pages', password='x', is_staff=True)
        first_user = TelegramUser.objects.create(tg_user_id=991001, username='page_first')
        boundary_user = TelegramUser.objects.create(tg_user_id=991002, username='page_boundary')
        tail_user = TelegramUser.objects.create(tg_user_id=991003, username='page_tail')

        # 功能：创建相关业务对象；当前函数属于 云资产、云订单和生命周期。
        def create_asset(user, index, sort_order):
            return CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'page-asset-{index}',
                public_ip=f'10.77.0.{index}',
                status=CloudAsset.STATUS_RUNNING,
                sort_order=sort_order,
            )

        for index in range(1, 50):
            create_asset(first_user, index, 200 - index)
        boundary_assets = [create_asset(boundary_user, 50, 120), create_asset(boundary_user, 51, 119)]
        create_asset(tail_user, 52, 10)

        page1 = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'page': '1', 'page_size': '50'})
        self._attach_bearer_session(page1, admin)
        response1 = cloud_assets_list(page1)
        payload1 = json.loads(response1.content.decode('utf-8'))['data']
        self.assertEqual(response1.status_code, 200)
        self.assertEqual(payload1['page_size'], 50)
        self.assertGreaterEqual(payload1['total_pages'], 2)
        self.assertEqual(len(payload1['items']), 50)
        self.assertEqual([item['user_id'] for item in payload1['items'] if item['user_id'] == boundary_user.id], [boundary_user.id])

        page2 = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'page': '2', 'page_size': '50'})
        self._attach_bearer_session(page2, admin)
        response2 = cloud_assets_list(page2)
        payload2 = json.loads(response2.content.decode('utf-8'))['data']
        boundary_ids = {asset.id for asset in boundary_assets}
        page2_boundary_ids = {item['id'] for item in payload2['items'] if item['user_id'] == boundary_user.id}
        self.assertEqual(len(page2_boundary_ids), 1)
        self.assertNotEqual(page2_boundary_ids, boundary_ids)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_assets_paginated_uses_true_database_pages_for_telegram_group_sort(self):
        admin = get_user_model().objects.create_user(username='admin_asset_group_pages', password='x', is_staff=True)
        first_group = TelegramGroupFilter.objects.create(chat_id=-1001991001, title='Page First Group', enabled=True)
        boundary_group = TelegramGroupFilter.objects.create(chat_id=-1001991002, title='Page Boundary Group', enabled=True)
        tail_group = TelegramGroupFilter.objects.create(chat_id=-1001991003, title='Page Tail Group', enabled=True)

        # 功能：创建相关业务对象；当前函数属于 云资产、云订单和生命周期。
        def create_asset(group, index, sort_order):
            return CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                telegram_group=group,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'page-group-asset-{index}',
                public_ip=f'10.78.0.{index}',
                status=CloudAsset.STATUS_RUNNING,
                sort_order=sort_order,
            )

        for index in range(1, 20):
            create_asset(first_group, index, 200 - index)
        boundary_assets = [create_asset(boundary_group, 20, 120), create_asset(boundary_group, 21, 119)]
        create_asset(tail_group, 22, 10)

        page1 = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'group_by': 'telegram_group', 'page': '1', 'page_size': '20'})
        self._attach_bearer_session(page1, admin)
        response1 = cloud_assets_list(page1)
        payload1 = json.loads(response1.content.decode('utf-8'))['data']
        self.assertEqual(response1.status_code, 200)
        self.assertEqual(payload1['page_size'], 20)
        self.assertGreaterEqual(payload1['total_pages'], 2)
        self.assertEqual(len(payload1['items']), 20)
        self.assertEqual([item['telegram_group_id'] for item in payload1['items'] if item['telegram_group_id'] == boundary_group.id], [boundary_group.id])

        page2 = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'group_by': 'telegram_group', 'page': '2', 'page_size': '20'})
        self._attach_bearer_session(page2, admin)
        response2 = cloud_assets_list(page2)
        payload2 = json.loads(response2.content.decode('utf-8'))['data']
        boundary_ids = {asset.id for asset in boundary_assets}
        page2_boundary_ids = {item['id'] for item in payload2['items'] if item['telegram_group_id'] == boundary_group.id}
        self.assertEqual(len(page2_boundary_ids), 1)
        self.assertNotEqual(page2_boundary_ids, boundary_ids)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_assets_grouped_paginated_uses_twenty_user_groups_per_page(self):
        admin = get_user_model().objects.create_user(username='admin_asset_grouped_user_pages', password='x', is_staff=True)
        for index in range(1, 23):
            user = TelegramUser.objects.create(tg_user_id=992000 + index, username=f'group_page_user_{index}')
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'group-page-user-{index}',
                public_ip=f'10.79.0.{index}',
                status=CloudAsset.STATUS_RUNNING,
                sort_order=300 - index,
            )

        page1 = self.factory.get('/api/admin/cloud-assets/', {'grouped': '1', 'paginated': '1', 'group_by': 'user', 'page': '1', 'page_size': '20'})
        self._attach_bearer_session(page1, admin)
        response1 = cloud_assets_list(page1)
        payload1 = json.loads(response1.content.decode('utf-8'))['data']

        self.assertEqual(response1.status_code, 200)
        self.assertEqual(payload1['page_size'], 20)
        self.assertEqual(payload1['total'], 22)
        self.assertEqual(payload1['total_pages'], 2)
        self.assertEqual(len(payload1['groups']), 20)
        self.assertEqual(len(payload1['items']), 20)

        page2 = self.factory.get('/api/admin/cloud-assets/', {'grouped': '1', 'paginated': '1', 'group_by': 'user', 'page': '2', 'page_size': '20'})
        self._attach_bearer_session(page2, admin)
        with patch(
            'cloud.api_asset_snapshots._dashboard_snapshot_group_keys_from_ordered_rows',
            wraps=_dashboard_snapshot_group_keys_from_ordered_rows,
        ) as row_paging:
            response2 = cloud_assets_list(page2)
        payload2 = json.loads(response2.content.decode('utf-8'))['data']
        self.assertGreaterEqual(row_paging.call_count, 1)
        self.assertEqual(len(payload2['groups']), 2)
        self.assertEqual(len(payload2['items']), 2)

    # 功能：旧快照 payload 缺少用户展示字段时，风险标签分组分页仍不能 500。
    def test_cloud_assets_grouped_risk_page_tolerates_old_snapshot_payload_missing_user_fields(self):
        cache.clear()
        admin = get_user_model().objects.create_user(username='admin_asset_grouped_old_payload', password='x', is_staff=True)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='old-payload-unattached-ip',
            public_ip='10.79.8.8',
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() + timezone.timedelta(days=15),
        )
        refresh_cloud_asset_dashboard_snapshots(asset_ids=[asset.id], reason='test', full=False)
        snapshot = CloudAssetDashboardSnapshot.objects.get(asset=asset)
        payload = dict(snapshot.payload or {})
        for key in ('actual_expires_at', 'tg_user_id', 'user_display_name', 'username_label'):
            payload.pop(key, None)
        snapshot.payload = payload
        snapshot.save(update_fields=['payload'])

        request = self.factory.get('/api/admin/cloud-assets/', {
            'grouped': '1',
            'paginated': '1',
            'group_by': 'user',
            'page': '1',
            'page_size': '20',
            'risk_status': 'unattached_ip',
        })
        self._attach_bearer_session(request, admin)
        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['total'], 1)
        self.assertEqual(len(payload['groups']), 1)
        self.assertEqual(payload['groups'][0]['username_label'], '-')
        self.assertEqual([item['id'] for item in payload['items']], [asset.id])

    # 功能：快照 payload 为空时，分组分页仍要从快照列和资产表补齐真实分组字段，避免前端并成 1 个空组。
    def test_cloud_assets_grouped_page_rebuilds_empty_snapshot_payload_group_keys(self):
        cache.clear()
        admin = get_user_model().objects.create_user(username='admin_asset_grouped_empty_payload', password='x', is_staff=True)
        users = [
            TelegramUser.objects.create(tg_user_id=992610 + index, username=f'empty_payload_user_{index}')
            for index in range(3)
        ]
        assets = [
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'empty-payload-group-{index}',
                public_ip=f'10.79.9.{index}',
                status=CloudAsset.STATUS_RUNNING,
                actual_expires_at=timezone.now() + timezone.timedelta(days=index + 1),
            )
            for index, user in enumerate(users)
        ]
        refresh_cloud_asset_dashboard_snapshots(asset_ids=[asset.id for asset in assets], reason='test', full=False)
        CloudAssetDashboardSnapshot.objects.filter(asset_id__in=[asset.id for asset in assets]).update(payload={})

        request = self.factory.get('/api/admin/cloud-assets/', {
            'grouped': '1',
            'paginated': '1',
            'group_by': 'user',
            'page': '1',
            'page_size': '2',
        })
        self._attach_bearer_session(request, admin)
        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['total'], 3)
        self.assertEqual(len(payload['groups']), 2)
        self.assertEqual(len(payload['items']), 2)
        self.assertEqual(
            {group['user_key'] for group in payload['groups']},
            {f'user:{users[0].id}', f'user:{users[1].id}'},
        )
        self.assertEqual({item['id'] for item in payload['items']}, {assets[0].id, assets[1].id})

    # 功能：验证分组分页总数只按分组键统计，末页反向分页不丢组。
    def test_cloud_assets_grouped_total_counts_distinct_groups_only(self):
        cache.clear()
        admin = get_user_model().objects.create_user(username='admin_asset_grouped_distinct_pages', password='x', is_staff=True)
        shared_user = TelegramUser.objects.create(tg_user_id=992500, username='group_page_shared_user')
        tail_user = TelegramUser.objects.create(tg_user_id=992501, username='group_page_tail_user')
        for index in range(3):
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=shared_user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'group-page-shared-{index}',
                public_ip=f'10.79.10.{index}',
                actual_expires_at=timezone.now() + timezone.timedelta(days=index + 1),
                status=CloudAsset.STATUS_RUNNING,
                sort_order=300 - index,
            )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=tail_user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='group-page-tail',
            public_ip='10.79.10.9',
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
            status=CloudAsset.STATUS_RUNNING,
            sort_order=100,
        )

        request = self.factory.get('/api/admin/cloud-assets/', {
            'compact': '1',
            'grouped': '1',
            'paginated': '1',
            'group_by': 'user',
            'page': '2',
            'page_size': '1',
        })
        self._attach_bearer_session(request, admin)
        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['total'], 2)
        self.assertEqual(payload['total_pages'], 2)
        self.assertEqual(len(payload['groups']), 1)
        self.assertEqual(payload['groups'][0]['user_key'], f'user:{tail_user.id}')

    # 功能：重复分组在 compact 分页下不能跨页重复，避免大数据快路径把旧分组排到后续页。
    def test_cloud_assets_grouped_duplicate_groups_do_not_repeat_across_pages(self):
        cache.clear()
        admin = get_user_model().objects.create_user(username='admin_asset_grouped_duplicate_precise', password='x', is_staff=True)
        shared_user = TelegramUser.objects.create(tg_user_id=992520, username='group_page_duplicate_shared')
        middle_user = TelegramUser.objects.create(tg_user_id=992522, username='group_page_duplicate_middle')
        tail_user = TelegramUser.objects.create(tg_user_id=992521, username='group_page_duplicate_tail')
        for index in range(2):
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=shared_user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'group-page-duplicate-shared-{index}',
                public_ip=f'10.79.12.{index}',
                actual_expires_at=timezone.now() + timezone.timedelta(days=index + 1),
                status=CloudAsset.STATUS_RUNNING,
            )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=middle_user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='group-page-duplicate-middle',
            public_ip='10.79.12.8',
            actual_expires_at=timezone.now() + timezone.timedelta(days=10),
            status=CloudAsset.STATUS_RUNNING,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=tail_user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='group-page-duplicate-tail',
            public_ip='10.79.12.9',
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
            status=CloudAsset.STATUS_RUNNING,
        )

        request = self.factory.get('/api/admin/cloud-assets/', {
            'compact': '1',
            'grouped': '1',
            'paginated': '1',
            'group_by': 'user',
            'page': '1',
            'page_size': '2',
        })
        self._attach_bearer_session(request, admin)
        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        page1_keys = [group['user_key'] for group in payload['groups']]

        page2 = self.factory.get('/api/admin/cloud-assets/', {
            'compact': '1',
            'grouped': '1',
            'paginated': '1',
            'group_by': 'user',
            'page': '2',
            'page_size': '2',
        })
        self._attach_bearer_session(page2, admin)
        response2 = cloud_assets_list(page2)
        payload2 = json.loads(response2.content.decode('utf-8'))['data']
        page2_keys = [group['user_key'] for group in payload2['groups']]

        self.assertEqual(response2.status_code, 200)
        self.assertEqual(payload['total'], 3)
        self.assertEqual(payload['total_pages'], 2)
        self.assertEqual(page1_keys, [f'user:{shared_user.id}', f'user:{middle_user.id}'])
        self.assertEqual(page2_keys, [f'user:{tail_user.id}'])
        self.assertFalse(set(page1_keys) & set(page2_keys))

    # 功能：重复分组的最后一页也必须走有界反向分页，避免百万数据末页回落到超重 group-by 后空页。
    def test_cloud_assets_grouped_duplicate_groups_reverse_tail_keeps_last_page(self):
        cache.clear()
        admin = get_user_model().objects.create_user(username='admin_asset_grouped_duplicate_tail', password='x', is_staff=True)
        shared_user = TelegramUser.objects.create(tg_user_id=992530, username='group_page_tail_shared')
        asset_ids = []
        for index in range(2):
            asset_ids.append(CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=shared_user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'group-page-tail-shared-{index}',
                public_ip=f'10.79.13.{index}',
                actual_expires_at=timezone.now() + timezone.timedelta(days=index + 1),
                status=CloudAsset.STATUS_RUNNING,
            ).id)
        tail_user = None
        for index in range(104):
            user = TelegramUser.objects.create(tg_user_id=992600 + index, username=f'group_page_tail_{index}')
            tail_user = user
            asset_ids.append(CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'group-page-tail-{index}',
                public_ip=f'10.79.14.{index}',
                actual_expires_at=timezone.now() + timezone.timedelta(days=10 + index),
                status=CloudAsset.STATUS_RUNNING,
            ).id)
        refresh_cloud_asset_dashboard_snapshots(asset_ids=asset_ids, reason='test', full=False)

        request = self.factory.get('/api/admin/cloud-assets/', {
            'compact': '1',
            'grouped': '1',
            'paginated': '1',
            'group_by': 'user',
            'page': '105',
            'page_size': '1',
        })
        self._attach_bearer_session(request, admin)
        from cloud import api_asset_snapshots
        with patch('cloud.api_asset_snapshots._dashboard_snapshot_group_keys_from_ordered_rows', return_value=[]), patch(
            'cloud.api_asset_snapshots._dashboard_snapshot_group_keys_from_reverse_tail',
            wraps=api_asset_snapshots._dashboard_snapshot_group_keys_from_reverse_tail,
        ) as reverse_tail:
            response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertTrue(reverse_tail.called)
        self.assertEqual(payload['total'], 105)
        self.assertEqual(payload['total_pages'], 105)
        self.assertEqual(len(payload['groups']), 1)
        self.assertEqual(payload['groups'][0]['user_key'], f'user:{tail_user.id}')

    # 功能：10 万量级无重复分组末页允许正向有界扫描，避免无专用索引标签走慢反向排序。
    def test_cloud_assets_forward_row_paging_allows_medium_unique_tail_pages(self):
        self.assertTrue(_dashboard_snapshot_can_use_forward_row_paging(start=100000, duplicate_excess=10))
        self.assertTrue(_dashboard_snapshot_can_use_forward_row_paging(start=120000, duplicate_excess=0))
        self.assertFalse(_dashboard_snapshot_can_use_forward_row_paging(start=120000, duplicate_excess=1))
        self.assertFalse(_dashboard_snapshot_can_use_forward_row_paging(start=150001, duplicate_excess=0))

    # 功能：验证分组分页按到期时间排序时，无到期时间的资产组排在最后。
    def test_cloud_assets_grouped_paginated_orders_null_due_groups_last(self):
        cache.clear()
        admin = get_user_model().objects.create_user(username='admin_asset_grouped_null_due', password='x', is_staff=True)
        null_user = TelegramUser.objects.create(tg_user_id=992610, username='group_null_due')
        early_user = TelegramUser.objects.create(tg_user_id=992611, username='group_early_due')
        later_user = TelegramUser.objects.create(tg_user_id=992612, username='group_later_due')

        # 功能：创建相关业务对象；当前函数属于 云资产、云订单和生命周期。
        def create_asset(user, name, expires_at):
            return CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=name,
                public_ip=f'10.79.20.{user.id % 250}',
                actual_expires_at=expires_at,
                status=CloudAsset.STATUS_RUNNING,
            )

        create_asset(null_user, 'group-null-due-asset', None)
        create_asset(later_user, 'group-later-due-asset', timezone.now() + timezone.timedelta(days=20))
        create_asset(early_user, 'group-early-due-asset', timezone.now() + timezone.timedelta(days=2))

        request = self.factory.get('/api/admin/cloud-assets/', {
            'compact': '1',
            'grouped': '1',
            'paginated': '1',
            'group_by': 'user',
            'page': '1',
            'page_size': '20',
        })
        self._attach_bearer_session(request, admin)
        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [group['user_key'] for group in payload['groups']],
            [f'user:{early_user.id}', f'user:{later_user.id}', f'user:{null_user.id}'],
        )

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_assets_list_filters_by_risk_and_searches_asset_identifiers(self):
        admin = get_user_model().objects.create_user(username='admin_asset_risk_filter', password='x', is_staff=True)
        group = TelegramGroupFilter.objects.create(chat_id=-1001993001, title='Risk Filter Group', enabled=True)
        normal_user = TelegramUser.objects.create(tg_user_id=991301, username='risk_normal_user')
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='risk-filter-active-account',
            region_hint=self.plan.region_code,
            is_active=True,
        )
        account_label = cloud_account_label(account)
        due_expires_at = timezone.now() + timezone.timedelta(days=2)
        due_order = CloudServerOrder.objects.create(
            order_no='RISK-FILTER-ORDER-001',
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
            public_ip='10.88.0.1',
            static_ip_name='risk-static-ip-001',
        )
        due_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=due_order,
            user=self.user,
            telegram_group=group,
            cloud_account=account,
            account_label=account_label,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='risk-due-asset',
            instance_id='risk-instance-001',
            public_ip='10.88.0.1',
            actual_expires_at=due_expires_at,
            status=CloudAsset.STATUS_RUNNING,
        )
        normal_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=normal_user,
            telegram_group=group,
            cloud_account=account,
            account_label=account_label,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='risk-normal-asset',
            instance_id='risk-normal-instance-002',
            public_ip='10.88.0.2',
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
            status=CloudAsset.STATUS_RUNNING,
        )

        request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'risk_status': 'due_soon'})
        self._attach_bearer_session(request, admin)
        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in payload['items']], [due_asset.id])
        self.assertEqual(payload['items'][0]['risk_status'], 'due_soon')
        self.assertIn('due_soon', payload['items'][0]['risk_statuses'])
        self.assertIn('auto_renew_off', payload['items'][0]['risk_statuses'])
        self.assertEqual(payload['risk_counts']['all'], 2)
        self.assertEqual(payload['risk_counts']['due_soon'], 1)
        self.assertEqual(payload['risk_counts']['auto_renew_off'], 1)
        self.assertEqual(payload['risk_counts']['normal'], 1)

        secondary_request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'risk_status': 'auto_renew_off'})
        self._attach_bearer_session(secondary_request, admin)
        secondary_response = cloud_assets_list(secondary_request)
        secondary_payload = json.loads(secondary_response.content.decode('utf-8'))['data']
        self.assertEqual([item['id'] for item in secondary_payload['items']], [due_asset.id])

        normal_request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'risk_status': 'normal'})
        self._attach_bearer_session(normal_request, admin)
        normal_response = cloud_assets_list(normal_request)
        normal_payload = json.loads(normal_response.content.decode('utf-8'))['data']
        self.assertEqual([item['id'] for item in normal_payload['items']], [normal_asset.id])
        self.assertEqual(normal_payload['items'][0]['risk_label'], '运行中')

        search_request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'keyword': 'risk-instance-001'})
        self._attach_bearer_session(search_request, admin)
        search_response = cloud_assets_list(search_request)
        search_payload = json.loads(search_response.content.decode('utf-8'))['data']
        self.assertEqual([item['id'] for item in search_payload['items']], [due_asset.id])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_assets_search_filters_full_dataset_before_pagination(self):
        admin = get_user_model().objects.create_user(username='admin_asset_full_search', password='x', is_staff=True)
        target_user = TelegramUser.objects.create(tg_user_id=991900, username='target_full_search', first_name='代理昵称阿尔法')
        target_group = TelegramGroupFilter.objects.create(chat_id=-1001991900, title='Full Search Group', enabled=True)
        target_expires_at = timezone.now() + timezone.timedelta(days=90)
        target_order = CloudServerOrder.objects.create(
            order_no='FULL-SEARCH-ORDER-001',
            user=target_user,
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
            server_name='full-search-order-name-alpha',
            public_ip='10.90.0.250',
            auto_renew_enabled=True,
        )
        target_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=target_order,
            user=target_user,
            telegram_group=target_group,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='full-search-asset-alpha',
            public_ip='10.90.0.250',
            actual_expires_at=target_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            sort_order=1,
        )
        for index in range(12):
            user = TelegramUser.objects.create(tg_user_id=991910 + index, username=f'decoy_full_search_{index}')
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'decoy-full-search-{index}',
                public_ip=f'10.90.0.{index + 1}',
                actual_expires_at=timezone.now() + timezone.timedelta(days=30),
                status=CloudAsset.STATUS_RUNNING,
                sort_order=200 - index,
            )

        first_page_request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'page': '1', 'page_size': '10'})
        self._attach_bearer_session(first_page_request, admin)
        first_page_response = cloud_assets_list(first_page_request)
        first_page_payload = json.loads(first_page_response.content.decode('utf-8'))['data']
        self.assertGreater(first_page_payload['total'], 10)
        self.assertNotIn(target_asset.id, {item['id'] for item in first_page_payload['items']})

        grouped_search_request = self.factory.get('/api/admin/cloud-assets/', {
            'grouped': '1',
            'paginated': '1',
            'group_by': 'user',
            'page': '1',
            'page_size': '10',
            'keyword': 'asset-alpha',
        })
        self._attach_bearer_session(grouped_search_request, admin)
        grouped_search_response = cloud_assets_list(grouped_search_request)
        grouped_search_payload = json.loads(grouped_search_response.content.decode('utf-8'))['data']
        self.assertEqual(grouped_search_response.status_code, 200)
        self.assertEqual(grouped_search_payload['total'], 1)
        self.assertEqual([item['id'] for item in grouped_search_payload['items']], [target_asset.id])

        nickname_search_request = self.factory.get('/api/admin/cloud-assets/', {
            'paginated': '1',
            'page': '1',
            'page_size': '10',
            'keyword': '昵称阿尔法',
        })
        self._attach_bearer_session(nickname_search_request, admin)
        nickname_search_response = cloud_assets_list(nickname_search_request)
        nickname_search_payload = json.loads(nickname_search_response.content.decode('utf-8'))['data']
        self.assertEqual([item['id'] for item in nickname_search_payload['items']], [target_asset.id])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_assets_search_expands_to_all_assets_for_matched_user(self):
        admin = get_user_model().objects.create_user(username='admin_asset_user_search_expand', password='x', is_staff=True)
        target_user = TelegramUser.objects.create(
            tg_user_id=991930,
            username='alpha_search_user,backup_alpha_name',
            first_name='搜索昵称甲',
        )
        other_user = TelegramUser.objects.create(tg_user_id=991931, username='other_search_user', first_name='搜索昵称乙')
        target_assets = [
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=target_user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name='search-expand-primary',
                public_ip='10.91.0.10',
                actual_expires_at=timezone.now() + timezone.timedelta(days=30),
                status=CloudAsset.STATUS_RUNNING,
                sort_order=20,
            ),
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=target_user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name='search-expand-secondary',
                public_ip='10.91.0.11',
                actual_expires_at=timezone.now() + timezone.timedelta(days=31),
                status=CloudAsset.STATUS_RUNNING,
                sort_order=10,
            ),
        ]
        decoy_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=other_user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='search-expand-decoy',
            public_ip='10.91.0.12',
            actual_expires_at=timezone.now() + timezone.timedelta(days=32),
            status=CloudAsset.STATUS_RUNNING,
            sort_order=30,
        )

        for keyword in ['10.91.0.10', '@backup_alpha', '昵称甲']:
            request = self.factory.get('/api/admin/cloud-assets/', {
                'paginated': '1',
                'page': '1',
                'page_size': '10',
                'keyword': keyword,
            })
            self._attach_bearer_session(request, admin)
            response = cloud_assets_list(request)
            payload = json.loads(response.content.decode('utf-8'))['data']
            result_ids = {item['id'] for item in payload['items']}
            self.assertEqual(response.status_code, 200)
            self.assertEqual(result_ids, {item.id for item in target_assets})
            self.assertNotIn(decoy_asset.id, result_ids)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_asset_expired_filter_excludes_unattached_ip_assets(self):
        admin = get_user_model().objects.create_user(username='admin_asset_expired_unattached_filter', password='x', is_staff=True)
        group = TelegramGroupFilter.objects.create(chat_id=-1001993002, title='Risk Filter Group 2', enabled=True)
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='risk-expired-active-account',
            region_hint=self.plan.region_code,
            is_active=True,
        )
        account_label = cloud_account_label(account)
        expired_at = timezone.now() - timezone.timedelta(days=1)
        expired_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            telegram_group=group,
            cloud_account=account,
            account_label=account_label,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='expired-running-asset',
            public_ip='10.88.0.10',
            actual_expires_at=expired_at,
            status=CloudAsset.STATUS_RUNNING,
        )
        unattached_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            telegram_group=group,
            cloud_account=account,
            account_label=account_label,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='expired-unattached-ip-asset',
            public_ip='10.88.0.11',
            actual_expires_at=expired_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )

        expired_request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'risk_status': 'expired'})
        self._attach_bearer_session(expired_request, admin)
        expired_response = cloud_assets_list(expired_request)
        expired_payload = json.loads(expired_response.content.decode('utf-8'))['data']

        self.assertEqual(expired_response.status_code, 200)
        self.assertEqual([item['id'] for item in expired_payload['items']], [expired_asset.id])
        self.assertEqual(expired_payload['risk_counts']['expired'], 1)
        self.assertEqual(expired_payload['risk_counts']['unattached_ip'], 1)

        unattached_request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'risk_status': 'unattached_ip'})
        self._attach_bearer_session(unattached_request, admin)
        unattached_response = cloud_assets_list(unattached_request)
        unattached_payload = json.loads(unattached_response.content.decode('utf-8'))['data']

        self.assertEqual(unattached_response.status_code, 200)
        self.assertEqual([item['id'] for item in unattached_payload['items']], [unattached_asset.id])
        self.assertEqual(unattached_payload['items'][0]['risk_status'], 'unattached_ip')
        self.assertNotIn('expired', unattached_payload['items'][0]['risk_statuses'])

    # 功能：验证后台资产风险识别使用原始云状态，避免展示态标签折叠后漏掉未附加固定IP。
    def test_cloud_asset_unattached_filter_uses_raw_provider_status(self):
        admin = get_user_model().objects.create_user(username='admin_asset_raw_unattached_filter', password='x', is_staff=True)
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='risk-unattached-active-account',
            region_hint=self.plan.region_code,
            is_active=True,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            cloud_account=account,
            account_label=cloud_account_label(account),
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='raw-provider-unattached-ip-asset',
            public_ip='10.88.0.12',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_DELETED,
            provider_status='固定IP保留中-实例已删除',
            is_active=False,
        )

        request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'risk_status': 'unattached_ip'})
        self._attach_bearer_session(request, admin)
        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in payload['items']], [asset.id])
        self.assertEqual(payload['items'][0]['risk_status'], 'unattached_ip')
        self.assertIn('unattached_ip', payload['items'][0]['risk_statuses'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
            renew_notice_sent_at=timezone.now(),
            auto_renew_notice_sent_at=timezone.now(),
            auto_renew_failure_notice_sent_at=timezone.now(),
            delete_notice_sent_at=timezone.now(),
            recycle_notice_sent_at=timezone.now(),
        )
        self._attach_order_expiry_asset(order, original_expiry)
        with patch('cloud.services._renew_aliyun_instance', return_value=(True, 'ok')), patch('cloud.services._ensure_aws_instance_running', return_value=(False, 'skip start')):
            renewed = async_to_sync(apply_cloud_server_renewal)(order.id, 31, False)

        renewed.refresh_from_db()
        self.assertEqual(renewed.service_started_at, original_started_at)
        self.assertGreater(order_asset_expiry(renewed), original_expiry)
        self.assertIsNone(renewed.renew_notice_sent_at)
        self.assertIsNone(renewed.auto_renew_notice_sent_at)
        self.assertIsNone(renewed.auto_renew_failure_notice_sent_at)
        self.assertIsNone(renewed.delete_notice_sent_at)
        self.assertIsNone(renewed.recycle_notice_sent_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_renewal_postcheck_skips_running_records(self):
        old_expiry = timezone.now() + timezone.timedelta(days=7)
        new_expiry = timezone.now() + timezone.timedelta(days=38)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RENEW-POSTCHECK-RUNNING',
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
            server_name='renew-postcheck-running',
            instance_id='renew-postcheck-running',
            public_ip='8.8.4.9',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            actual_expires_at=old_expiry,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='running',
            is_active=True,
        )
        self._attach_order_expiry_asset(order, new_expiry)
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            actual_expires_at=old_expiry,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='running',
            is_active=True,
        )

        with patch('cloud.services._ensure_aws_instance_running') as ensure_running, \
            patch('cloud.services._ensure_mtproxy_after_renewal') as ensure_mtproxy:
            checked, error = async_to_sync(run_cloud_server_renewal_postcheck)(order.id)

        self.assertIsNone(error)
        self.assertEqual(checked.id, order.id)
        ensure_running.assert_not_called()
        ensure_mtproxy.assert_not_called()
        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertIn('已跳过开机和 MTProxy 巡检', order.provision_note)
        self.assertEqual(asset.actual_expires_at, new_expiry)
        self.assertEqual(server.actual_expires_at, new_expiry)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        )
        self._attach_order_expiry_asset(source, timezone.now() + timezone.timedelta(days=1))

        first_order, first_err = async_to_sync(create_cloud_server_upgrade_order)(source.id, self.user.id, target_plan.id)
        balance_after_first = TelegramUser.objects.get(id=self.user.id).balance
        second_order, second_err = async_to_sync(create_cloud_server_upgrade_order)(source.id, self.user.id, target_plan.id)

        self.assertIsNotNone(first_order)
        self.assertIsNone(first_err)
        self.assertIsNone(second_order)
        self.assertIn('已有配置调整任务', second_err)
        self.assertEqual(CloudServerOrder.objects.filter(replacement_for=source).count(), 1)
        self.assertEqual(TelegramUser.objects.get(id=self.user.id).balance, balance_after_first)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_config_change_success_does_not_steal_old_server_record(self):
        source = CloudServerOrder.objects.create(
            order_no='HB-TEST-UPGRADE-SOURCE-SERVER',
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
            public_ip='8.8.4.44',
            previous_public_ip='8.8.4.44',
            server_name='old-config-instance',
            instance_id='old-config-instance',
            provider_resource_id='old-config-instance',
            static_ip_name='StaticIp-config-source',
            mtproxy_port=9528,
            mtproxy_secret='0123456789abcdef0123456789abcdef',
            service_started_at=timezone.now() - timezone.timedelta(days=1),
        )
        source_expiry = timezone.now() + timezone.timedelta(days=20)
        self._attach_order_expiry_asset(source, source_expiry)
        old_server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            order=source,
            user=self.user,
            provider=source.provider,
            account_label=source.provider,
            region_code=source.region_code,
            region_name=source.region_name,
            asset_name=source.server_name,
            instance_id=source.instance_id,
            provider_resource_id=source.provider_resource_id,
            public_ip=source.public_ip,
            actual_expires_at=source_expiry,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        replacement = CloudServerOrder.objects.create(
            order_no='HB-TEST-UPGRADE-NEW-SERVER',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='29.00',
            pay_amount='10.00',
            pay_method='balance',
            status='provisioning',
            public_ip='10.0.0.10',
            replacement_for=source,
            static_ip_name=source.static_ip_name,
            mtproxy_port=source.mtproxy_port,
            mtproxy_secret=source.mtproxy_secret,
            service_started_at=source.service_started_at,
        )
        self._attach_order_expiry_asset(replacement, source_expiry)

        async_to_sync(_mark_success)(
            replacement.id,
            'new-config-instance',
            'new-config-instance',
            source.public_ip,
            'ubuntu',
            'secret',
            '配置调整完成',
            source.static_ip_name,
        )

        old_server.refresh_from_db()
        replacement.refresh_from_db()
        new_server = CloudAsset.objects.filter(order=replacement).first()
        self.assertEqual(old_server.order_id, source.id)
        self.assertEqual(old_server.instance_id, source.instance_id)
        self.assertIsNotNone(new_server)
        self.assertNotEqual(new_server.id, old_server.id)
        self.assertEqual(new_server.public_ip, source.public_ip)
        self.assertEqual(new_server.actual_expires_at, source_expiry)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_asset_renewal_mark_success_starts_new_service_period(self):
        old_release_at = timezone.now() + timezone.timedelta(days=7)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-ASSET-RENEWAL-MARK-SUCCESS',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='provisioning',
            public_ip='10.0.0.90',
            previous_public_ip='10.0.0.90',
            static_ip_name='StaticIp-asset-renewal-success',
            mtproxy_port=443,
            mtproxy_secret='secret',
            lifecycle_days=31,
            ip_recycle_at=old_release_at,
            provision_note='未绑定代理资产续费：来源资产 #999；旧IP=10.0.0.90。',
        )
        self._attach_order_expiry_asset(order, old_release_at)

        async_to_sync(_mark_success)(
            order.id,
            'asset-renewal-instance',
            'asset-renewal-instance',
            order.public_ip,
            'admin',
            'secret',
            '恢复完成',
            order.static_ip_name,
        )

        order.refresh_from_db()
        asset = CloudAsset.objects.get(order=order, kind=CloudAsset.KIND_SERVER)
        self.assertEqual(order.status, 'completed')
        self.assertGreater(asset.actual_expires_at, old_release_at)
        self.assertEqual(asset.actual_expires_at.date(), (order.completed_at + timezone.timedelta(days=31)).date())

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aws_sync_resolver_does_not_match_replacement_by_old_ip(self):
        from cloud.management.commands.sync_aws_assets import _resolve_server

        source = CloudServerOrder.objects.create(
            order_no='HB-TEST-AWS-SYNC-OLD-IP',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
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
            server_name='old-sync-instance',
            instance_id='old-sync-instance',
            provider_resource_id='old-sync-instance',
        )
        source_expiry = timezone.now() + timezone.timedelta(days=20)
        self._attach_order_expiry_asset(source, source_expiry)
        replacement = CloudServerOrder.objects.create(
            order_no='HB-TEST-AWS-SYNC-NEW-IP',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='provisioning',
            public_ip='9.9.9.9',
            replacement_for=source,
            server_name='new-sync-instance',
            instance_id='new-sync-instance',
            provider_resource_id='new-sync-instance',
        )
        old_server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            order=source,
            user=self.user,
            provider='aws_lightsail',
            account_label='aws_lightsail',
            region_code=source.region_code,
            region_name=source.region_name,
            asset_name=source.server_name,
            instance_id=source.instance_id,
            provider_resource_id=source.provider_resource_id,
            public_ip=source.public_ip,
            actual_expires_at=source_expiry,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        resolved = _resolve_server(replacement.instance_id, replacement.provider_resource_id, replacement.public_ip, replacement)

        self.assertIsNone(resolved)
        old_server.refresh_from_db()
        self.assertEqual(old_server.order_id, source.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aws_sync_resolver_prefers_ip_over_changed_instance_name(self):
        from cloud.management.commands.sync_aws_assets import _resolve_asset, _resolve_order_for_instance_sync, _resolve_server

        stable_ip_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-AWS-SYNC-IP-FIRST',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
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
            previous_public_ip='8.8.8.8',
            server_name='old-instance-name',
            instance_id='old-instance-name',
            provider_resource_id='old-instance-arn',
        )
        stable_ip_expiry = timezone.now() + timezone.timedelta(days=20)
        self._attach_order_expiry_asset(stable_ip_order, stable_ip_expiry)
        dirty_name_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-AWS-SYNC-DIRTY-NAME',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='7.7.7.7',
            server_name='new-instance-name',
            instance_id='new-instance-name',
            provider_resource_id='new-instance-arn',
        )
        self._attach_order_expiry_asset(dirty_name_order, stable_ip_expiry)
        ip_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=stable_ip_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='old-instance-name',
            instance_id='old-instance-name',
            provider_resource_id='old-instance-arn',
            public_ip='8.8.8.8',
            status=CloudAsset.STATUS_RUNNING,
        )
        dirty_name_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=dirty_name_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='new-instance-name',
            instance_id='new-instance-name',
            provider_resource_id='new-instance-arn',
            public_ip='7.7.7.7',
            status=CloudAsset.STATUS_RUNNING,
        )
        ip_server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            order=stable_ip_order,
            user=self.user,
            provider='aws_lightsail',
            account_label='aws_lightsail',
            region_code=stable_ip_order.region_code,
            region_name=stable_ip_order.region_name,
            asset_name='old-instance-name',
            instance_id='old-instance-name',
            provider_resource_id='old-instance-arn',
            public_ip='8.8.8.8',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            order=dirty_name_order,
            user=self.user,
            provider='aws_lightsail',
            account_label='aws_lightsail',
            region_code=dirty_name_order.region_code,
            region_name=dirty_name_order.region_name,
            asset_name='new-instance-name',
            instance_id='new-instance-name',
            provider_resource_id='new-instance-arn',
            public_ip='7.7.7.7',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        resolved_order = _resolve_order_for_instance_sync('new-instance-name', 'new-instance-arn', '8.8.8.8')
        resolved_asset = _resolve_asset('new-instance-name', 'new-instance-arn', '8.8.8.8', resolved_order)
        resolved_server = _resolve_server('new-instance-name', 'new-instance-arn', '8.8.8.8', resolved_order)

        self.assertEqual(resolved_order.id, stable_ip_order.id)
        self.assertEqual(resolved_asset.id, ip_asset.id)
        self.assertEqual(resolved_server.id, ip_server.id)
        self.assertNotEqual(resolved_asset.id, dirty_name_asset.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        )
        self._attach_order_expiry_asset(source, timezone.now() + timezone.timedelta(days=1))

        plans, err = async_to_sync(list_cloud_server_upgrade_plans)(source.id, self.user.id)
        new_order, create_err = async_to_sync(create_cloud_server_upgrade_order)(source.id, self.user.id, small_plan.id)

        self.assertIsNone(err)
        self.assertTrue(any(plan['id'] == small_plan.id and plan['action'] == 'downgrade' for plan in plans))
        self.assertIsNone(create_err)
        self.assertIsNotNone(new_order)
        self.assertEqual(new_order.plan_id, small_plan.id)
        self.assertEqual(new_order.pay_amount, Decimal('0.000000000'))
        self.assertIn('DOWNGRADE', new_order.order_no)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        )
        self._attach_order_expiry_asset(source, timezone.now() + timezone.timedelta(days=1))

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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_due_orders_use_asset_expiry_for_lightsail_lifecycle(self):
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
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='future-expiry-asset',
            public_ip='10.0.0.9',
            actual_expires_at=timezone.now() + timezone.timedelta(days=10),
        )

        due = async_to_sync(_get_due_orders)()

        self.assertFalse(any(item.id == order.id for item in due['expire']))
        self.assertFalse(any(item.id == order.id for item in due['suspend']))
        self.assertFalse(any(item.id == order.id for item in due['delete']))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_due_orders_ignore_account_shutdown_disabled(self):
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

        self.assertTrue(any(item.id == order.id for item in due['suspend']))
        self.assertTrue(any(item.id == order.id for item in due['expire']))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_due_orders_skip_suspend_when_asset_shutdown_disabled(self):
        expires_at = timezone.now() - timezone.timedelta(days=5)
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-ASSET-SHUTDOWN-OFF-1',
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
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='asset-shutdown-off-asset',
            public_ip='10.0.0.23',
            actual_expires_at=expires_at,
            shutdown_enabled=False,
            is_active=True,
        )

        due = async_to_sync(_get_due_orders)()

        self.assertFalse(any(item.id == order.id for item in due['suspend']))
        self.assertTrue(any(item.id == order.id for item in due['expire']))

    # 功能：验证订单固定 IP 回收只受 IP 删除开关影响，不受资产关机开关影响。
    def test_due_orders_recycle_ignores_asset_shutdown_disabled(self):
        recycle_at = timezone.now() - timezone.timedelta(minutes=1)
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-ASSET-RECYCLE-OFF-1',
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
            previous_public_ip='10.0.0.24',
            static_ip_name='StaticIp-asset-recycle-off',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            ip_recycle_at=recycle_at,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='StaticIp-asset-recycle-off',
            public_ip='10.0.0.24',
            previous_public_ip='10.0.0.24',
            actual_expires_at=recycle_at,
            status=CloudAsset.STATUS_DELETED,
            provider_status='固定IP保留中-实例已删除',
            shutdown_enabled=False,
            ip_delete_enabled=True,
            is_active=False,
        )

        due = async_to_sync(_get_due_orders)()

        self.assertTrue(any(item.id == order.id for item in due['recycle']))

    # 功能：验证全局关机总开关只影响关机，不会误挡删机和固定 IP 回收队列。
    def test_due_orders_global_shutdown_switch_does_not_block_delete_or_recycle(self):
        SiteConfig.set('cloud_server_shutdown_enabled', '0')
        now = timezone.now()
        delete_order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-GLOBAL-SHUTDOWN-DELETE-1',
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
            status='suspended',
            public_ip='10.0.0.25',
            delete_at=now - timezone.timedelta(minutes=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=delete_order,
            user=self.user,
            provider=delete_order.provider,
            region_code=delete_order.region_code,
            region_name=delete_order.region_name,
            asset_name='global-shutdown-delete-asset',
            public_ip='10.0.0.25',
            actual_expires_at=now - timezone.timedelta(days=5),
            shutdown_enabled=True,
            is_active=True,
        )
        recycle_order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-GLOBAL-SHUTDOWN-RECYCLE-1',
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
            previous_public_ip='10.0.0.26',
            static_ip_name='StaticIp-global-shutdown-recycle',
            ip_recycle_at=now - timezone.timedelta(minutes=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=recycle_order,
            user=self.user,
            provider=recycle_order.provider,
            region_code=recycle_order.region_code,
            region_name=recycle_order.region_name,
            asset_name='StaticIp-global-shutdown-recycle',
            public_ip='10.0.0.26',
            previous_public_ip='10.0.0.26',
            actual_expires_at=now - timezone.timedelta(minutes=1),
            status=CloudAsset.STATUS_DELETED,
            provider_status='固定IP保留中-实例已删除',
            shutdown_enabled=True,
            is_active=False,
        )

        due = async_to_sync(_get_due_orders)()

        self.assertTrue(any(item.id == delete_order.id for item in due['delete']))
        self.assertTrue(any(item.id == recycle_order.id for item in due['recycle']))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_suspend_execution_guard_respects_asset_shutdown_disabled(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='shutdown-off-exec',
            external_account_id='acct-shutdown-off-exec',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            shutdown_enabled=False,
        )
        expires_at = timezone.now() - timezone.timedelta(days=5)
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-SUSPEND-GUARD-1',
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
            suspend_at=timezone.now() - timezone.timedelta(minutes=5),
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
            asset_name='shutdown-off-exec-asset',
            public_ip='10.0.0.22',
            actual_expires_at=expires_at,
            shutdown_enabled=False,
            is_active=True,
        )
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [order],
            'delete': [],
            'recycle': [],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._is_cloud_suspend_time', return_value=True), \
            patch('cloud.lifecycle_execution.run_shutdown_order_suspend', return_value={'ok': False, 'error': '资产自动生命周期开关已关闭，跳过真实关机。'}) as suspend_mock:
            async_to_sync(lifecycle_tick)()

        suspend_mock.assert_called_once_with(order.id, queue_status='scheduled_suspend', enforce_schedule=True)
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_due_orders_skip_asset_when_expiry_missing(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-LIFECYCLE-ASSET-EXPIRY-MISSING',
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

        self.assertFalse(any(item.id == order.id for item in due['expire']))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_due_orders_respect_deferred_suspend_at(self):
        expires_at = timezone.now() - timezone.timedelta(days=5)
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
            actual_expires_at=expires_at,
            is_active=True,
        )

        due = async_to_sync(_get_due_orders)()

        self.assertFalse(any(item.id == order.id for item in due['suspend']))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_due_orders_restore_suspend_after_asset_shutdown_reenabled(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='shutdown-on',
            external_account_id='acct-shutdown-on',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            shutdown_enabled=False,
        )
        expires_at = timezone.now() - timezone.timedelta(days=5)
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
            actual_expires_at=expires_at,
            is_active=True,
            shutdown_enabled=False,
        )

        self.assertFalse(any(item.id == order.id for item in async_to_sync(_get_due_orders)()['suspend']))

        CloudAsset.objects.filter(order=order, kind=CloudAsset.KIND_SERVER).update(shutdown_enabled=True)

        due = async_to_sync(_get_due_orders)()

        self.assertTrue(any(item.id == order.id for item in due['suspend']))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        stale_server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='stale-server',
            public_ip='10.0.0.3',
            is_active=True,
        )
        active_server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='active-server',
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
        self.assertIn('unit-test suspend', order.provision_note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_action_time_only_runs_in_configured_window(self):
        base = timezone.localtime(timezone.now()).replace(hour=15, minute=5, second=0, microsecond=0)
        with patch('cloud.lifecycle._config_time', return_value=(15, 0)):
            self.assertTrue(_is_cloud_suspend_time(now=base))
            self.assertTrue(_is_cloud_delete_safe_time(now=base))
            self.assertFalse(_is_cloud_suspend_time(now=base.replace(minute=11)))
            self.assertFalse(_is_cloud_delete_safe_time(now=base.replace(minute=11)))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_tick_reads_suspend_time_config_outside_async_loop(self):
        now = timezone.now()
        local_now = timezone.localtime(now)
        configured_time = f'{local_now.hour:02d}:{local_now.minute:02d}'
        expires_at = now - timezone.timedelta(days=1)
        order = CloudServerOrder.objects.create(
            order_no='ASYNC-CONFIG-SUSPEND-1',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='Singapore',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            server_name='async-config-suspend-server',
            public_ip='13.250.10.20',
            service_started_at=now - timezone.timedelta(days=40),
            suspend_at=now - timezone.timedelta(minutes=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        CloudServerOrder.objects.filter(id=order.id).update(suspend_at=now - timezone.timedelta(minutes=1))
        order.suspend_at = now - timezone.timedelta(minutes=1)
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [order],
            'delete': [],
            'recycle': [],
        }

        # 功能：处理 云资产、云订单和生命周期 中的 runtime config side effect 业务流程。
        def runtime_config_side_effect(key, default=None):
            if key == 'cloud_suspend_time':
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    return configured_time
                return '00:00'
            return default

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle.get_runtime_config', side_effect=runtime_config_side_effect), \
            patch('cloud.lifecycle_execution.run_shutdown_order_suspend', return_value={'ok': True, 'error': None}) as suspend_mock:
            async_to_sync(lifecycle_tick)()

        suspend_mock.assert_called_once_with(order.id, queue_status='scheduled_suspend', enforce_schedule=True)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_next_cloud_action_run_at_sticks_to_configured_time(self):
        base = timezone.localtime(timezone.now()).replace(hour=16, minute=20, second=0, microsecond=0)
        with patch('cloud.lifecycle._config_time', return_value=(15, 0)):
            run_at = _next_cloud_action_run_at('cloud_suspend_time', '15:00', now=base, min_delay_seconds=3600)
        self.assertEqual((run_at.hour, run_at.minute), (15, 0))
        self.assertGreater(run_at, base + timezone.timedelta(seconds=3600))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
            suspend_at=timezone.now() + timezone.timedelta(days=4),
            delete_at=timezone.now() + timezone.timedelta(days=4, hours=1),
        )
        with patch('cloud.lifecycle._config_time', side_effect=[(15, 30), (16, 45)]):
            text = _notice_plan_text(order)
        self.assertIn('关机计划:', text)
        self.assertIn('价格: <code>19.00</code> USDT', text)
        self.assertNotIn('后台执行时间', text)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_notice_delete_plan_and_proxy_list_use_asset_expiry(self):
        now = timezone.now()
        asset_expiry = now + timezone.timedelta(days=3)
        order = CloudServerOrder.objects.create(
            order_no='PLAN-SAME-ASSET-EXPIRY-1',
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
            public_ip='3.3.3.31',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='plan-same-asset-expiry',
            public_ip='3.3.3.31',
            actual_expires_at=asset_expiry,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        notice = _notice_payload_for_order(order)
        delete_item = _asset_delete_plan_item_payload(asset)
        proxy_item = _asset_payload(asset)

        self.assertEqual(notice['expires_at'], asset_expiry)
        self.assertEqual(parse_datetime(delete_item['actual_expires_at']), asset_expiry)
        self.assertEqual(parse_datetime(proxy_item['actual_expires_at']), asset_expiry)

    # 功能：验证已进入关机/删除流程的订单计划优先保留已存执行时间，避免资产到期变化导致计划漂移。
    def test_notice_schedule_preserves_stored_delete_and_recycle_after_status_progress(self):
        now = timezone.now()
        future_asset_expiry = now + timezone.timedelta(days=30)
        stored_delete_at = now + timezone.timedelta(hours=6)
        suspended_order = CloudServerOrder.objects.create(
            order_no='PLAN-PRESERVE-SUSPENDED-DELETE',
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
            status='suspended',
            public_ip='3.3.3.41',
            delete_at=stored_delete_at,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=suspended_order,
            user=self.user,
            provider=suspended_order.provider,
            region_code=suspended_order.region_code,
            region_name=suspended_order.region_name,
            asset_name='plan-preserve-suspended-delete',
            public_ip='3.3.3.41',
            actual_expires_at=future_asset_expiry,
            status=CloudAsset.STATUS_STOPPED,
            is_active=False,
        )
        stored_recycle_at = now + timezone.timedelta(hours=8)
        deleted_order = CloudServerOrder.objects.create(
            order_no='PLAN-PRESERVE-DELETED-RECYCLE',
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
            public_ip='3.3.3.42',
            ip_recycle_at=stored_recycle_at,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=deleted_order,
            user=self.user,
            provider=deleted_order.provider,
            region_code=deleted_order.region_code,
            region_name=deleted_order.region_name,
            asset_name='plan-preserve-deleted-recycle',
            public_ip='3.3.3.42',
            actual_expires_at=future_asset_expiry,
            status=CloudAsset.STATUS_DELETED,
            provider_status='固定IP保留中-实例已删除',
            is_active=False,
        )

        suspended_notice = _notice_payload_for_order(suspended_order)
        deleted_notice = _notice_payload_for_order(deleted_order)

        self.assertEqual(suspended_notice['delete_at'], stored_delete_at)
        self.assertEqual(deleted_notice['ip_recycle_at'], stored_recycle_at)

    # 功能：验证订单旧到期字段已彻底移除，测试和业务不能再把它当模型字段写入。
    def test_order_rejects_removed_service_expiry_field(self):
        with self.assertRaises(TypeError):
            CloudServerOrder.objects.create(
                order_no='ORDER-REMOVED-ASSET-EXPIRY-1',
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
                public_ip='3.3.3.32',
                actual_expires_at=timezone.now() + timezone.timedelta(days=5),
            )

    # 功能：验证有关联有效订单的资产删除计划回到订单计划展示，避免作为孤立资产执行。
    def test_linked_active_order_asset_delete_plan_uses_order_payload(self):
        expires_at = timezone.now() - timezone.timedelta(days=10)
        delete_at = timezone.now() - timezone.timedelta(days=1)
        order = CloudServerOrder.objects.create(
            order_no='ORDER-LINKED-ASSET-PLAN-1',
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
            public_ip='3.3.3.35',
            delete_at=delete_at,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='linked-active-order-plan-asset',
            instance_id='linked-active-order-plan-asset',
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        item = _asset_delete_plan_item_payload(asset, queue_status='due_now', queue_status_label='待执行')

        self.assertEqual(item['item_type'], 'order')
        self.assertEqual(item['order_id'], order.id)
        self.assertEqual(item['asset_id'], asset.id)
        self.assertEqual(item['detail_path'], f'/admin/cloud-orders/{order.id}')
        self.assertEqual(item['asset_detail_path'], f'/admin/cloud-assets/{asset.id}')
        self.assertEqual(parse_datetime(item['actual_expires_at']), expires_at)

    # 功能：验证孤立资产删机入口不能绕过仍有效的关联订单，避免订单和资产状态分叉。
    def test_orphan_asset_delete_refuses_linked_active_order_when_enforced(self):
        from cloud.lifecycle_execution import run_orphan_asset_delete

        order = CloudServerOrder.objects.create(
            order_no='ORDER-LINKED-ASSET-RUN-1',
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
            public_ip='3.3.3.36',
            delete_at=timezone.now() - timezone.timedelta(days=1),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='linked-active-order-run-asset',
            instance_id='linked-active-order-run-asset',
            public_ip=order.public_ip,
            actual_expires_at=timezone.now() - timezone.timedelta(days=10),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        with patch('cloud.lifecycle._delete_orphan_asset_instance', new=AsyncMock()) as delete_mock:
            result = run_orphan_asset_delete(asset.id, enforce_schedule=True)

        delete_mock.assert_not_called()
        self.assertFalse(result['ok'])
        self.assertIn('请走订单删机计划', result['error'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
            status='deleting',
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_get_migration_due_orders_skips_non_deleting_orders(self):
        old_order = CloudServerOrder.objects.create(
            order_no='HB-MIGRATION-OLD-SKIP-1',
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
            migration_due_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        CloudServerOrder.objects.create(
            order_no='HB-MIGRATION-NEW-SKIP-1',
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
            public_ip='10.0.2.2',
            replacement_for=old_order,
        )

        due_orders = async_to_sync(_get_migration_due_orders)()

        self.assertEqual(due_orders, [])

    # 功能：验证迁移旧机自动删机同样受资产关机计划开关保护。
    def test_replaced_order_delete_respects_asset_shutdown_switch(self):
        from cloud.lifecycle_execution import run_replaced_order_delete

        old_order = CloudServerOrder.objects.create(
            order_no='HB-MIGRATION-SHUTDOWN-OFF-OLD',
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
            status='deleting',
            public_ip='10.0.3.1',
            instance_id='migration-shutdown-off-old',
            migration_due_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        CloudServerOrder.objects.create(
            order_no='HB-MIGRATION-SHUTDOWN-OFF-NEW',
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
            public_ip='10.0.3.2',
            replacement_for=old_order,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=old_order,
            user=self.user,
            provider=old_order.provider,
            region_code=old_order.region_code,
            region_name=old_order.region_name,
            asset_name='migration-shutdown-off-old',
            instance_id='migration-shutdown-off-old',
            public_ip='10.0.3.1',
            status=CloudAsset.STATUS_RUNNING,
            shutdown_enabled=False,
        )

        with patch('cloud.lifecycle.cloud_server_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._delete_replaced_server') as delete_mock:
            result = run_replaced_order_delete(old_order.id, enforce_schedule=True)

        old_order.refresh_from_db()
        self.assertFalse(result['ok'])
        self.assertIn('生命周期开关已关闭', result['error'])
        self.assertEqual(old_order.status, 'deleting')
        delete_mock.assert_not_called()

    # 功能：验证生命周期巡检不会因迁移旧机资产已进入删除中而跳过旧机删除。
    def test_lifecycle_tick_deletes_migration_due_order_with_deleting_asset(self):
        migration_due_at = timezone.now() - timezone.timedelta(minutes=1)
        old_order = CloudServerOrder.objects.create(
            order_no='HB-MIGRATION-LIFECYCLE-OLD',
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
            status='deleting',
            public_ip='10.0.4.1',
            previous_public_ip='10.0.4.1',
            server_name='migration-lifecycle-old',
            instance_id='migration-lifecycle-old',
            migration_due_at=migration_due_at,
            delete_at=timezone.now() + timezone.timedelta(days=3),
        )
        CloudServerOrder.objects.create(
            order_no='HB-MIGRATION-LIFECYCLE-NEW',
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
            public_ip='10.0.4.2',
            replacement_for=old_order,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=old_order,
            user=self.user,
            provider=old_order.provider,
            region_code=old_order.region_code,
            region_name=old_order.region_name,
            asset_name='migration-lifecycle-old',
            instance_id='migration-lifecycle-old',
            public_ip='10.0.4.1',
            actual_expires_at=migration_due_at,
            status=CloudAsset.STATUS_DELETING,
            is_active=False,
        )
        deleted_orders = []

        # 功能：模拟迁移旧机执行入口，避免 SQLite 内存库跨线程查询。
        def fake_run_replaced_order_delete(order_id, **kwargs):
            deleted_orders.append((order_id, kwargs.get('queue_status'), kwargs.get('enforce_schedule')))
            return {'ok': True, 'error': None}

        with patch('cloud.lifecycle_execution.run_replaced_order_delete', side_effect=fake_run_replaced_order_delete):
            async_to_sync(lifecycle_tick)()

        old_order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(deleted_orders, [(old_order.id, 'scheduled_migration_delete', True)])
        self.assertEqual(old_order.status, 'deleting')
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETING)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_mark_failed_schedules_incomplete_instance_cleanup(self):
        order = CloudServerOrder.objects.create(
            order_no='FAILED-CLEANUP-SCHEDULE',
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
            server_name='failed-instance-1',
            instance_id='failed-instance-1',
            public_ip='13.229.249.56',
        )
        cleanup_at = timezone.now() + timezone.timedelta(days=1)

        async_to_sync(_mark_failed)(order.id, '固定 IP 迁移失败', cleanup_at=cleanup_at)

        order.refresh_from_db()
        self.assertEqual(order.status, 'failed')
        self.assertEqual(order.delete_at, cleanup_at)
        self.assertIn('固定 IP 迁移失败', order.provision_note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_failed_instance_cleanup_due_orders_are_deleted(self):
        SiteConfig.set('cloud_server_delete_enabled', '1')
        order = CloudServerOrder.objects.create(
            order_no='FAILED-CLEANUP-DUE',
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
            status='failed',
            server_name='failed-instance-2',
            instance_id='failed-instance-2',
            public_ip='13.229.249.57',
            delete_at=timezone.now() - timezone.timedelta(minutes=1),
            provision_note='创建流程未完成，等待清理。',
        )

        due = async_to_sync(_get_due_orders)()
        self.assertIn(order.id, [item.id for item in due['delete']])

        # 功能：处理 云资产、云订单和生命周期 中的 fake delete instance 业务流程。
        async def fake_delete_instance(delete_order):
            return True, '失败新实例已删除'

        with patch('cloud.lifecycle.cloud_server_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_delete_safe_time', return_value=True), \
            patch('cloud.lifecycle._delete_instance', side_effect=fake_delete_instance):
            async_to_sync(lifecycle_tick)()

        order.refresh_from_db()
        self.assertEqual(order.status, 'deleted')
        self.assertEqual(order.instance_id, '')
        self.assertIn('失败新实例已删除', order.provision_note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_tick_rechecks_order_delete_at_before_cloud_delete(self):
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-RECHECK-FUTURE-DELETE',
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
            status='deleting',
            server_name='lifecycle-recheck-future-delete',
            public_ip='13.229.249.58',
            delete_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        due_order = CloudServerOrder.objects.get(id=order.id)
        CloudServerOrder.objects.filter(id=order.id).update(delete_at=timezone.now() + timezone.timedelta(days=1))
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [due_order],
            'recycle': [],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle.cloud_server_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_delete_safe_time', return_value=True), \
            patch('cloud.lifecycle._delete_instance', new_callable=AsyncMock) as delete_mock:
            async_to_sync(lifecycle_tick)()

        delete_mock.assert_not_awaited()
        order.refresh_from_db()
        self.assertEqual(order.status, 'deleting')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_tick_rechecks_order_ip_recycle_at_before_release(self):
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-RECHECK-FUTURE-RECYCLE',
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
            public_ip='13.229.249.59',
            previous_public_ip='13.229.249.59',
            static_ip_name='StaticIp-recheck-future-recycle',
            instance_id='',
            ip_recycle_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        due_order = CloudServerOrder.objects.get(id=order.id)
        CloudServerOrder.objects.filter(id=order.id).update(ip_recycle_at=timezone.now() + timezone.timedelta(days=1))
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [due_order],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._release_order_static_ip', new_callable=AsyncMock) as release_mock:
            async_to_sync(lifecycle_tick)()

        release_mock.assert_not_awaited()
        order.refresh_from_db()
        self.assertEqual(order.status, 'deleted')
        self.assertIsNotNone(order.ip_recycle_at)

    # 功能：验证启动延迟保护会顺延固定 IP 回收，但不触发真实释放或改写资产真实到期事实。
    def test_lifecycle_tick_startup_defer_reschedules_static_ip_cleanup_without_release(self):
        original_expiry = timezone.now() - timezone.timedelta(days=2)
        old_recycle_at = timezone.now() - timezone.timedelta(minutes=5)
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-DEFER-RECYCLE-1',
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
            public_ip='13.229.249.60',
            previous_public_ip='13.229.249.60',
            static_ip_name='StaticIp-defer-recycle',
            ip_recycle_at=old_recycle_at,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-defer-unattached',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-defer-unattached',
            public_ip='21.21.21.25',
            actual_expires_at=original_expiry,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [order],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[asset]), \
            patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle_execution.run_order_static_ip_release') as order_release, \
            patch('cloud.lifecycle_execution.run_unattached_ip_release') as unattached_release:
            async_to_sync(lifecycle_tick)(defer_destructive_seconds=300)

        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertGreater(order.ip_recycle_at, old_recycle_at)
        self.assertEqual(asset.actual_expires_at, original_expiry)
        self.assertEqual(asset.public_ip, '21.21.21.25')
        order_release.assert_not_called()
        unattached_release.assert_not_called()

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_create_cloud_server_rebuild_order_reuses_original_static_ip_without_temp(self):
        source_expires_at = timezone.now() + timezone.timedelta(days=31)
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
        )
        self._attach_order_expiry_asset(source_order, source_expires_at)

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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_reinit_request_creates_rebuild_order_for_active_server(self):
        source_expires_at = timezone.now() + timezone.timedelta(days=31)
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REINIT-NO-REBUILD-1',
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
            public_ip='1.2.3.44',
            login_password='root-password',
            static_ip_name='hb-static-ip-reinit',
            mtproxy_port=8443,
            mtproxy_secret='ee1234567890abcdef1234567890abcd',
            mtproxy_link='tg://proxy?server=1.2.3.44&port=8443&secret=ee1234567890abcdef1234567890abcd',
            service_started_at=timezone.now(),
        )
        self._attach_order_expiry_asset(source_order, source_expires_at)

        result = async_to_sync(mark_cloud_server_reinit_requested)(source_order.id, self.user.id)

        self.assertNotEqual(result.id, source_order.id)
        self.assertEqual(result.replacement_for_id, source_order.id)
        self.assertTrue(result.order_no.startswith('SRVREBUILD'))
        self.assertEqual(result.static_ip_name, source_order.static_ip_name)
        self.assertEqual(result.mtproxy_secret, source_order.mtproxy_secret)
        source_order.refresh_from_db()
        self.assertIn('重装迁移', source_order.provision_note)
        self.assertIsNotNone(source_order.migration_due_at)

    # 功能：验证未完成订单仍走继续初始化，不创建重建迁移订单。
    def test_reinit_request_keeps_unfinished_order_as_resume_init(self):
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REINIT-RESUME-1',
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
            status='failed',
            public_ip='1.2.3.45',
            login_password='root-password',
            mtproxy_port=8443,
            service_started_at=timezone.now(),
        )

        result = async_to_sync(mark_cloud_server_reinit_requested)(source_order.id, self.user.id)

        self.assertEqual(result.id, source_order.id)
        self.assertFalse(CloudServerOrder.objects.filter(replacement_for=source_order).exists())
        source_order.refresh_from_db()
        self.assertIn('继续初始化请求', source_order.provision_note)
        self.assertIsNone(source_order.migration_due_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_rebuild_static_ip_context_corrects_stale_static_ip_name(self):
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-STATIC-RESOLVE-1',
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
            public_ip='3.1.169.183',
            static_ip_name='260410007170',
            mtproxy_port=8443,
            mtproxy_secret='ee1234567890abcdef1234567890abcd',
        )
        rebuild_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REBUILD-STATIC-RESOLVE-2',
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
            static_ip_name='260410007170',
            replacement_for=source_order,
        )
        with patch('cloud.provisioning._resolve_aws_static_ip_name_for_order', return_value='StaticIp-real-name'):
            context = async_to_sync(_get_rebuild_static_ip_context)(rebuild_order.id)

        self.assertTrue(context['is_rebuild'])
        self.assertEqual(context['original_static_ip_name'], 'StaticIp-real-name')
        self.assertEqual(context['payload']['original_public_ip'], '3.1.169.183')
        source_order.refresh_from_db()
        rebuild_order.refresh_from_db()
        self.assertEqual(source_order.static_ip_name, 'StaticIp-real-name')
        self.assertEqual(rebuild_order.static_ip_name, 'StaticIp-real-name')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_resolve_static_ip_name_for_move_falls_back_to_public_ip(self):
        # 测试类：组织 FakeClient 相关的回归测试。
        class FakeClient:
            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_static_ip(self, staticIpName):
                raise Exception(f'The StaticIp does not exist: {staticIpName}')

            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_static_ips(self, **kwargs):
                return {
                    'staticIps': [
                        {'name': 'StaticIp-real-name', 'ipAddress': '13.229.249.56', 'attachedTo': 'old-instance'},
                    ]
                }

        resolved = _resolve_static_ip_name_for_move(
            FakeClient(),
            '260410007170',
            {'order_no': 'SRVDOWNGRADE-TEST', 'original_public_ip': '13.229.249.56'},
        )

        self.assertEqual(resolved, 'StaticIp-real-name')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            provider='aws_lightsail',
            account_label=f'aws+{other_account.external_account_id}+{other_account.name}',
            region_code=self.plan.region_code,
            public_ip='3.0.114.174',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        payload = async_to_sync(_get_aws_create_payload)(rebuild_order.id)
        account_ids = async_to_sync(_candidate_cloud_account_ids)(rebuild_order.id)

        self.assertTrue(payload['skip_static_ip'])
        self.assertEqual(payload['static_ip_name'], '')
        self.assertEqual(payload['cloud_account_id'], source_account.id)
        self.assertEqual(account_ids, [source_account.id])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_rebuild_source_migration_schedule_preserves_asset_expiry(self):
        source_expires_at = timezone.now() + timezone.timedelta(days=30)
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
            migration_due_at=timezone.now() + timezone.timedelta(days=3),
        )
        self._attach_order_expiry_asset(source, source_expires_at)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=source, user=self.user, public_ip='1.2.3.4')
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
        server = CloudAsset.objects.get(order=source)
        self.assertEqual(order_asset_expiry(source), source_expires_at)
        self.assertEqual(source.renew_grace_expires_at, source.migration_due_at + timezone.timedelta(days=3))
        self.assertEqual(source.delete_at, source.migration_due_at + timezone.timedelta(days=3))
        self.assertEqual(asset.actual_expires_at, source_expires_at)
        self.assertEqual(server.actual_expires_at, source_expires_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_rebuild_job_keeps_old_instance_until_migration_due(self):
        from cloud.services import run_cloud_server_rebuild_job

        source_expires_at = timezone.now() + timezone.timedelta(days=3)
        replacement_expires_at = timezone.now() + timezone.timedelta(days=30)
        source = CloudServerOrder.objects.create(
            order_no='REBUILD-JOB-SOURCE-KEEP-3D',
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
            status='deleting',
            public_ip='1.2.3.40',
            server_name='old-rebuild-job-instance',
            instance_id='old-rebuild-job-instance',
            migration_due_at=timezone.now() + timezone.timedelta(days=3),
            delete_at=timezone.now() + timezone.timedelta(days=6),
        )
        self._attach_order_expiry_asset(source, source_expires_at)
        replacement = CloudServerOrder.objects.create(
            order_no='REBUILD-JOB-NEW-KEEP-3D',
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
            public_ip='1.2.3.40',
            server_name='new-rebuild-job-instance',
            instance_id='new-rebuild-job-instance',
            replacement_for=source,
        )
        self._attach_order_expiry_asset(replacement, replacement_expires_at)

        # 功能：处理 云资产、云订单和生命周期 中的 fake provision cloud server 业务流程。
        async def fake_provision_cloud_server(order_id):
            self.assertEqual(order_id, replacement.id)
            return replacement

        with patch('cloud.provisioning.provision_cloud_server', fake_provision_cloud_server):
            run_cloud_server_rebuild_job(replacement.id)

        source.refresh_from_db()
        self.assertEqual(source.status, 'deleting')
        self.assertIsNotNone(source.delete_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=old_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            public_ip='8.8.8.8',
            actual_expires_at=old_expiry,
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
        self.assertEqual(server.actual_expires_at, new_expiry)
        self.assertEqual(order_asset_expiry(new_order), new_expiry)
        self.assertEqual(new_order.replacement_for_id, old_order.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        staff_user = get_user_model().objects.create_user(username='staff_api_price_replace', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({
                'price': '29.00',
                'actual_expires_at': new_expiry.isoformat(),
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        self._attach_bearer_session(request, staff_user)

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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dashboard_order_expiry_update_syncs_asset_expiry_and_lifecycle_plan(self):
        old_expiry = timezone.now() + timezone.timedelta(days=1)
        new_expiry = timezone.now() + timezone.timedelta(days=20)
        old_lifecycle = compute_order_lifecycle_fields(old_expiry)
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
            **old_lifecycle,
        )
        old_suspend_at = order.suspend_at
        asset = CloudAsset.objects.create(
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
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='dash-order-expiry-update-server',
            public_ip='4.4.4.5',
            actual_expires_at=old_expiry,
        )
        asset.refresh_from_db()
        server.refresh_from_db()
        staff_user = get_user_model().objects.create_user(username='staff_order_expiry_update', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/admin/cloud-orders/{order.id}/',
            data=json.dumps({'actual_expires_at': new_expiry.isoformat()}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request = self._attach_bearer_session(request, staff_user)

        response = cloud_order_detail(request, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(order_asset_expiry(order), new_expiry)
        self.assertGreater(order.suspend_at, old_suspend_at)
        self.assertEqual(order.renew_grace_expires_at, order.suspend_at)
        self.assertGreaterEqual(order.delete_at, order.suspend_at)
        self.assertGreater(order.ip_recycle_at, order.delete_at)
        self.assertEqual(asset.actual_expires_at, new_expiry)
        self.assertEqual(server.actual_expires_at, new_expiry)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dashboard_order_ip_and_name_update_syncs_asset_server(self):
        expires_at = timezone.now() + timezone.timedelta(days=20)
        order = CloudServerOrder.objects.create(
            order_no='DASH-ORDER-IP-NAME-UPDATE-1',
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
            public_ip='4.4.4.40',
            server_name='old-dashboard-name',
            service_started_at=timezone.now(),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_order_ip_name_update', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/admin/cloud-orders/{order.id}/',
            data=json.dumps({'public_ip': '4.4.4.41', 'server_name': 'new-dashboard-name'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        self._attach_bearer_session(request, staff_user)

        response = cloud_order_detail(request, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset = CloudAsset.objects.get(order=order)
        server = CloudAsset.objects.get(order=order)
        self.assertEqual(order.public_ip, '4.4.4.41')
        self.assertEqual(order.previous_public_ip, '4.4.4.40')
        self.assertEqual(order.server_name, 'new-dashboard-name')
        self.assertEqual(asset.public_ip, '4.4.4.41')
        self.assertEqual(asset.previous_public_ip, '4.4.4.40')
        self.assertEqual(asset.asset_name, 'new-dashboard-name')
        self.assertEqual(server.public_ip, '4.4.4.41')
        self.assertEqual(server.previous_public_ip, '4.4.4.40')
        self.assertEqual(server.asset_name, 'new-dashboard-name')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dashboard_asset_ip_update_syncs_order_previous_ip(self):
        expires_at = timezone.now() + timezone.timedelta(days=20)
        order = CloudServerOrder.objects.create(
            order_no='DASH-ASSET-IP-UPDATE-1',
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
            public_ip='4.4.4.42',
            server_name='asset-ip-update-server',
            service_started_at=timezone.now(),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_ip_update', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({'public_ip': '4.4.4.43'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        server = CloudAsset.objects.get(order=order)
        self.assertEqual(order.public_ip, '4.4.4.43')
        self.assertEqual(order.previous_public_ip, '4.4.4.42')
        self.assertEqual(asset.public_ip, '4.4.4.43')
        self.assertEqual(asset.previous_public_ip, '4.4.4.42')
        self.assertEqual(server.public_ip, '4.4.4.43')
        self.assertEqual(server.previous_public_ip, '4.4.4.42')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dashboard_asset_ip_update_uses_asset_old_ip_when_server_was_pre_synced(self):
        expires_at = timezone.now() + timezone.timedelta(days=20)
        order = CloudServerOrder.objects.create(
            order_no='DASH-ASSET-IP-PRESYNC-1',
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
            public_ip='4.4.4.44',
            server_name='asset-ip-presync-server',
            service_started_at=timezone.now(),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            public_ip='4.4.4.45',
            actual_expires_at=expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_ip_presync', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({'public_ip': '4.4.4.45'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        server = CloudAsset.objects.get(order=order)
        self.assertEqual(order.public_ip, '4.4.4.45')
        self.assertEqual(order.previous_public_ip, '4.4.4.44')
        self.assertEqual(asset.public_ip, '4.4.4.45')
        self.assertEqual(asset.previous_public_ip, '4.4.4.44')
        self.assertEqual(server.public_ip, '4.4.4.45')
        self.assertEqual(server.previous_public_ip, '4.4.4.44')
        log = CloudIpLog.objects.filter(asset=asset, event_type=CloudIpLog.EVENT_CHANGED).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.previous_public_ip, '4.4.4.44')
        self.assertEqual(log.public_ip, '4.4.4.45')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dashboard_asset_update_does_not_touch_cross_account_same_instance_server(self):
        expires_at = timezone.now() + timezone.timedelta(days=20)
        order = CloudServerOrder.objects.create(
            order_no='DASH-ASSET-SCOPED-SERVER-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            account_label='aws+111+primary',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            instance_id='same-instance-scoped',
            public_ip='4.4.4.50',
            server_name='scoped-server-primary',
            service_started_at=timezone.now(),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            account_label=order.account_label,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
        )
        wrong_server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider=order.provider,
            account_label='aws+222+secondary',
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='scoped-server-secondary',
            instance_id=order.instance_id,
            public_ip='4.4.4.99',
        )
        right_server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            account_label=order.account_label,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_scoped_server', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({'public_ip': '4.4.4.51'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        right_server.refresh_from_db()
        wrong_server.refresh_from_db()
        self.assertEqual(right_server.public_ip, '4.4.4.51')
        self.assertEqual(wrong_server.public_ip, '4.4.4.99')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dashboard_asset_update_matches_current_account_label(self):
        account = self._aws_test_account()
        current_label = cloud_account_label(account)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider=self.plan.provider,
            cloud_account=account,
            account_label=current_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='current-label-server',
            instance_id='current-label-server',
            public_ip='4.4.4.70',
            actual_expires_at=timezone.now() + timezone.timedelta(days=20),
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider=self.plan.provider,
            account_label=current_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='current-label-server',
            instance_id='current-label-server',
            public_ip='4.4.4.70',
            actual_expires_at=asset.actual_expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_current_label_update', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({'public_ip': '4.4.4.71'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        server.refresh_from_db()
        self.assertEqual(server.public_ip, '4.4.4.71')
        self.assertEqual(server.account_label, current_label)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dashboard_asset_update_created_server_preserves_account_label(self):
        account = self._aws_test_account()
        label = cloud_account_label(account)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider=self.plan.provider,
            cloud_account=account,
            account_label=label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='create-server-account-scope',
            instance_id='i-create-server-account-scope',
            public_ip='4.4.4.61',
            actual_expires_at=timezone.now() + timezone.timedelta(days=20),
        )
        staff_user = get_user_model().objects.create_user(username='staff_create_server_account_label', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({'note': '触发补建服务器记录'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        server = CloudAsset.objects.get(instance_id='i-create-server-account-scope')
        self.assertEqual(server.account_label, label)
        self.assertEqual(server.provider, self.plan.provider)
        self.assertEqual(server.region_code, self.plan.region_code)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aws_notice_schedule_does_not_override_manual_order_expiry(self):
        manual_expiry = timezone.now() + timezone.timedelta(days=15)
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
        )
        CloudAsset.objects.create(
            order=order,
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            public_ip=order.public_ip,
            actual_expires_at=manual_expiry,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        notice_expiry = timezone.now() + timezone.timedelta(days=5)

        _apply_notice_schedule_to_order(order, {
            'expires_at': notice_expiry,
            'suspend_at': notice_expiry,
            'delete_at': notice_expiry + timezone.timedelta(days=3),
            'ip_recycle_at': notice_expiry + timezone.timedelta(days=7),
        })

        order.refresh_from_db()
        self.assertEqual(order_asset_expiry(order), manual_expiry)
        self.assertEqual(order.suspend_at, notice_expiry)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_deleted_retained_order_without_active_static_ip_is_not_query_result(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-DELETED-RETAINED-MISSING',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='deleted',
            public_ip='54.255.96.64',
            previous_public_ip='54.255.96.64',
            static_ip_name='released-static-ip',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=10),
        )
        CloudAsset.objects.create(
            order=order,
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='released-static-ip',
            public_ip='54.255.96.64',
            status=CloudAsset.STATUS_DELETED,
            provider_status='云上未找到',
            note='云上不存在，已标记删除',
            is_active=False,
        )

        result = async_to_sync(get_cloud_server_by_ip_for_user)('54.255.96.64', self.user.id)
        retained_order, plans, err = async_to_sync(list_retained_ip_renewal_plans)(order.id, self.user.id)

        self.assertIsNone(result)
        self.assertEqual(retained_order.id, order.id)
        self.assertEqual(plans, [])
        self.assertIsNone(err)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_completed_order_without_instance_and_released_static_ip_is_not_query_result(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-COMPLETED-RELEASED-IP',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='54.255.96.65',
            previous_public_ip='54.255.96.65',
            static_ip_name='released-static-ip-completed',
            instance_id='',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=10),
        )
        CloudAsset.objects.create(
            order=order,
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='released-static-ip-completed',
            public_ip='54.255.96.65',
            status=CloudAsset.STATUS_DELETED,
            provider_status='云上未找到',
            note='云上不存在，已标记删除',
            is_active=False,
        )

        result = async_to_sync(get_cloud_server_by_ip_for_user)('54.255.96.65', self.user.id)
        retained_order, plans, err = async_to_sync(list_retained_ip_renewal_plans)(order.id, self.user.id)

        self.assertIsNone(result)
        self.assertEqual(retained_order.id, order.id)
        self.assertEqual(plans, [])
        self.assertIsNone(err)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unbound_asset_renewal_lists_plans_without_creating_order(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unbound-renewal-plan-list',
            public_ip='31.31.31.30',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        returned_asset, plans, error = async_to_sync(list_cloud_asset_renewal_plans)(asset.id, self.user.id)
        asset.refresh_from_db()

        self.assertIsNone(error)
        self.assertEqual(returned_asset.id, asset.id)
        self.assertTrue(plans)
        self.assertIsNone(asset.order_id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_prepare_unbound_asset_renewal_creates_pending_payment_order(self):
        due_at = timezone.now() + timezone.timedelta(days=9)
        account = self._aws_test_account()
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unbound-renewal-payment',
            public_ip='31.31.31.32',
            previous_public_ip='31.31.31.32',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            mtproxy_port=9528,
            is_active=False,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.32&port=9528&secret=eeeeeeeeeeeeeeee',
            'server': '31.31.31.32',
            'port': '9528',
            'secret': 'eeeeeeeeeeeeeeee',
        }

        order, error = async_to_sync(prepare_cloud_asset_renewal_with_link)(asset.id, self.user.id, self.plan.id, link)
        asset.refresh_from_db()

        self.assertIsNone(error)
        self.assertIsNotNone(order)
        self.assertEqual(order.status, 'pending')
        self.assertEqual(order.plan_id, self.plan.id)
        self.assertEqual(order.pay_method, 'address')
        self.assertEqual(order_asset_expiry(order), due_at)
        self.assertEqual(order.ip_recycle_at, due_at)
        self.assertEqual(order.mtproxy_link, link['url'])
        self.assertEqual(asset.order_id, order.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery(self):
        self.user.balance = Decimal('100.000000')
        self.user.save(update_fields=['balance', 'updated_at'])
        due_at = timezone.now() + timezone.timedelta(days=9)
        account = self._aws_test_account()
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unbound-renewal-wallet-payment',
            public_ip='31.31.31.33',
            previous_public_ip='31.31.31.33',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.33&port=443&secret=eed5c148e2922f6c49611e7d53fe432a94617a7572652e6d6963726f736f66742e636f6d',
            'server': '31.31.31.33',
            'port': '443',
            'secret': 'eed5c148e2922f6c49611e7d53fe432a94617a7572652e6d6963726f736f66742e636f6d',
        }
        order, error = async_to_sync(prepare_cloud_asset_renewal_with_link)(asset.id, self.user.id, self.plan.id, link)

        paid_order, pay_error = async_to_sync(pay_cloud_server_renewal_with_balance)(order.id, self.user.id, 'USDT', 31)

        self.assertIsNone(error)
        self.assertIsNone(pay_error)
        self.assertEqual(paid_order.id, order.id)
        self.assertEqual(paid_order.status, 'paid')
        self.assertEqual(paid_order.pay_method, 'balance')
        self.assertIsNotNone(paid_order.paid_at)
        self.assertEqual(order_asset_expiry(paid_order), due_at)
        self.assertEqual(paid_order.ip_recycle_at, due_at)
        self.assertIn('正在恢复未绑定代理资产固定 IP', paid_order.provision_note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unbound_asset_renewal_wallet_payment_repairs_completed_unpaid_state(self):
        self.user.balance = Decimal('100.000000')
        self.user.save(update_fields=['balance', 'updated_at'])
        due_at = timezone.now() + timezone.timedelta(days=9)
        account = self._aws_test_account()
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unbound-renewal-wallet-repair',
            public_ip='31.31.31.34',
            previous_public_ip='31.31.31.34',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.34&port=443&secret=eed5c148e2922f6c49611e7d53fe432a94617a7572652e6d6963726f736f66742e636f6d',
            'server': '31.31.31.34',
            'port': '443',
            'secret': 'eed5c148e2922f6c49611e7d53fe432a94617a7572652e6d6963726f736f66742e636f6d',
        }
        order, _ = async_to_sync(prepare_cloud_asset_renewal_with_link)(asset.id, self.user.id, self.plan.id, link)
        CloudServerOrder.objects.filter(id=order.id).update(status='completed', paid_at=None, instance_id='')

        paid_order, pay_error = async_to_sync(pay_cloud_server_renewal_with_balance)(order.id, self.user.id, 'USDT', 31)

        self.assertIsNone(pay_error)
        self.assertEqual(paid_order.status, 'paid')
        self.assertIsNotNone(paid_order.paid_at)
        self.assertEqual(order_asset_expiry(paid_order), due_at)
        self.assertEqual(paid_order.ip_recycle_at, due_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_completed_asset_recovery_order_renews_without_reprovisioning(self):
        self.user.balance = Decimal('100.000000')
        self.user.save(update_fields=['balance', 'updated_at'])
        completed_at = timezone.now() - timezone.timedelta(days=1)
        old_expiry = timezone.now() + timezone.timedelta(days=10)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-ASSET-RECOVERY-NORMAL-RENEW',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='renew_pending',
            public_ip='31.31.31.36',
            instance_id='recovered-instance-36',
            static_ip_name='StaticIp-recovered-36',
            mtproxy_port=443,
            mtproxy_secret='secret',
            service_started_at=completed_at,
            provision_note='未绑定代理资产续费：来源资产 #999；恢复完成。',
        )
        CloudAsset.objects.create(
            order=order,
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.server_name,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            previous_public_ip=order.previous_public_ip,
            actual_expires_at=old_expiry,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        renewed, pay_error = async_to_sync(pay_cloud_server_renewal_with_balance)(order.id, self.user.id, 'USDT', 31)

        self.assertIsNone(pay_error)
        self.assertEqual(renewed.status, 'completed')
        self.assertEqual(renewed.instance_id, 'recovered-instance-36')
        self.assertGreater(order_asset_expiry(renewed), old_expiry)
        self.assertIsNotNone(renewed.paid_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unbound_asset_renewal_chain_payment_marks_paid_for_recovery(self):
        due_at = timezone.now() + timezone.timedelta(days=9)
        account = self._aws_test_account()
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unbound-renewal-chain-payment',
            public_ip='31.31.31.35',
            previous_public_ip='31.31.31.35',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.35&port=443&secret=eed5c148e2922f6c49611e7d53fe432a94617a7572652e6d6963726f736f66742e636f6d',
            'server': '31.31.31.35',
            'port': '443',
            'secret': 'eed5c148e2922f6c49611e7d53fe432a94617a7572652e6d6963726f736f66742e636f6d',
        }
        order, error = async_to_sync(prepare_cloud_asset_renewal_with_link)(asset.id, self.user.id, self.plan.id, link)

        confirmed = async_to_sync(_confirm_cloud_server_order)(order.id, '0xassetrenewalchainpayment', 'payer', 'receiver')

        self.assertIsNone(error)
        self.assertIsNotNone(confirmed)
        self.assertEqual(confirmed.status, 'paid')
        self.assertIsNotNone(confirmed.paid_at)
        self.assertEqual(order_asset_expiry(confirmed), due_at)
        self.assertEqual(confirmed.ip_recycle_at, due_at)
        self.assertIn('正在恢复未绑定代理资产固定 IP', confirmed.provision_note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unsynced_deleted_aws_asset_prepares_static_ip_recovery(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='gray-zone-account',
            region_hint=self.plan.region_code,
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        due_at = timezone.now() + timezone.timedelta(days=9)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='gray-zone-stale-instance',
            instance_id='gray-zone-stale-instance',
            public_ip='31.31.31.38',
            previous_public_ip='31.31.31.38',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            note='AWS 已删机但同步未更新，DB 仍是运行中资产',
            mtproxy_port=443,
            mtproxy_link='tg://proxy?server=31.31.31.38&port=443&secret=eeeeeeeeeeeeeeee',
            mtproxy_secret='eeeeeeeeeeeeeeee',
            mtproxy_host='31.31.31.38',
            is_active=True,
        )
        link = {
            'url': 'tg://proxy?server=31.31.31.38&port=443&secret=eeeeeeeeeeeeeeee',
            'server': '31.31.31.38',
            'port': '443',
            'secret': 'eeeeeeeeeeeeeeee',
        }

        with patch('cloud.services._resolve_unattached_aws_static_ip_name_for_asset', return_value='StaticIp-gray-zone'):
            order, error = async_to_sync(prepare_cloud_asset_renewal_with_link)(asset.id, self.user.id, self.plan.id, link)

        self.assertIsNone(error)
        self.assertIsNotNone(order)
        self.assertEqual(order.static_ip_name, 'StaticIp-gray-zone')
        self.assertEqual(order.cloud_account_id, account.id)
        self.assertIn('灰区续费：AWS 实时确认固定 IP 未附加', order.provision_note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        )
        CloudAsset.objects.create(
            order=source_order,
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            user=self.user,
            provider=source_order.provider,
            region_code=source_order.region_code,
            region_name=source_order.region_name,
            public_ip=source_order.public_ip,
            actual_expires_at=original_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        new_order = async_to_sync(mark_cloud_server_ip_change_requested)(source_order.id, self.user.id)

        source_order.refresh_from_db()
        self.assertTrue(new_order)
        self.assertEqual(new_order.plan_id, self.plan.id)
        self.assertEqual(new_order.replacement_for_id, source_order.id)
        self.assertEqual(order_asset_expiry(new_order), original_expires_at)
        self.assertIsNotNone(source_order.migration_due_at)
        self.assertEqual(order_asset_expiry(source_order), original_expires_at)
        self.assertEqual(source_order.suspend_at, source_order.migration_due_at + timezone.timedelta(days=3))
        self.assertEqual(source_order.delete_at, source_order.migration_due_at + timezone.timedelta(days=3))
        self.assertEqual(source_order.renew_grace_expires_at, source_order.migration_due_at + timezone.timedelta(days=3))
        self.assertEqual(
            source_order.ip_recycle_at,
            source_order.delete_at + timezone.timedelta(days=15),
        )

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_mark_cloud_server_ip_change_requested_returns_existing_replacement(self):
        source_expires_at = timezone.now() + timezone.timedelta(days=31)
        source_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-REPLACE-EXISTING',
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
            public_ip='11.22.33.44',
            service_started_at=timezone.now(),
            ip_change_quota=1,
        )
        self._attach_order_expiry_asset(source_order, source_expires_at)

        first_order = async_to_sync(mark_cloud_server_ip_change_requested)(source_order.id, self.user.id)
        second_order = async_to_sync(mark_cloud_server_ip_change_requested)(source_order.id, self.user.id)

        source_order.refresh_from_db()
        self.assertIsNotNone(first_order)
        self.assertIsNotNone(second_order)
        self.assertEqual(first_order.id, second_order.id)
        self.assertEqual(CloudServerOrder.objects.filter(replacement_for=source_order).count(), 1)
        self.assertEqual(source_order.ip_change_quota, 0)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        server = CloudAsset.objects.get(order=order)
        log = CloudIpLog.objects.filter(order=order).latest('id')

        self.assertEqual(order.status, 'provisioning')
        self.assertEqual(order.server_name, 'sg-test-node-01')
        self.assertEqual(asset.status, CloudAsset.STATUS_PENDING)
        self.assertTrue(asset.is_active)
        self.assertEqual(server.status, CloudAsset.STATUS_PENDING)
        self.assertTrue(server.is_active)
        self.assertEqual(log.event_type, CloudIpLog.EVENT_CREATED)
        self.assertIn('服务器开始创建', log.note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_mark_success_preserves_existing_manual_asset_fields_on_update(self):
        existing_owner = TelegramUser.objects.create(tg_user_id=21989111, username='manual_asset_owner')
        old_expiry = timezone.now() + timezone.timedelta(days=13)
        old_link = 'tg://proxy?server=1.2.3.5&port=8443&secret=old-secret'
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-PROVISION-MANUAL-ASSET',
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
        asset = CloudAsset.objects.get(order=order, kind=CloudAsset.KIND_SERVER)
        asset.user = existing_owner
        asset.actual_expires_at = old_expiry
        asset.mtproxy_host = '1.2.3.5'
        asset.mtproxy_port = 8443
        asset.mtproxy_secret = 'old-secret'
        asset.mtproxy_link = old_link
        asset.proxy_links = [{'name': '主代理 mtg', 'server': '1.2.3.5', 'port': '8443', 'secret': 'old-secret', 'url': old_link}]
        asset.save(update_fields=['user', 'actual_expires_at', 'mtproxy_host', 'mtproxy_port', 'mtproxy_secret', 'mtproxy_link', 'proxy_links', 'updated_at'])

        async_to_sync(_mark_success)(
            order.id,
            'sg-test-node-02',
            'ins-002',
            '1.2.3.5',
            'root',
            'pass',
            'MTProxy 安装完成\n状态: 运行正常\n端口: 8443',
            '',
        )

        self.assertEqual(CloudAsset.objects.filter(order=order, kind=CloudAsset.KIND_SERVER).count(), 1)
        asset.refresh_from_db()
        self.assertEqual(asset.user_id, existing_owner.id)
        self.assertEqual(asset.actual_expires_at, old_expiry)
        self.assertEqual(asset.mtproxy_link, old_link)
        self.assertEqual(asset.mtproxy_secret, 'old-secret')
        self.assertEqual(asset.mtproxy_port, 8443)
        self.assertEqual(asset.proxy_links[0]['url'], old_link)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_early_provisioning_steps_preserve_existing_manual_asset_fields(self):
        existing_owner = TelegramUser.objects.create(tg_user_id=21989112, username='early_manual_asset_owner')
        manual_expiry = timezone.now() + timezone.timedelta(days=9)
        manual_link = 'tg://proxy?server=1.2.3.6&port=9443&secret=manual-secret'
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-PROVISION-EARLY-MANUAL-ASSET',
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

        async_to_sync(_mark_provisioning_start)(order.id, 'sg-test-node-03')
        asset = CloudAsset.objects.get(order=order, kind=CloudAsset.KIND_SERVER)
        asset.user = existing_owner
        asset.actual_expires_at = manual_expiry
        asset.mtproxy_host = '1.2.3.6'
        asset.mtproxy_port = 9443
        asset.mtproxy_secret = 'manual-secret'
        asset.mtproxy_link = manual_link
        asset.proxy_links = [{'name': '手工代理', 'server': '1.2.3.6', 'port': '9443', 'secret': 'manual-secret', 'url': manual_link}]
        asset.save(update_fields=['user', 'actual_expires_at', 'mtproxy_host', 'mtproxy_port', 'mtproxy_secret', 'mtproxy_link', 'proxy_links', 'updated_at'])

        async_to_sync(_mark_provisioning_start)(order.id, 'sg-test-node-03')
        async_to_sync(_mark_instance_created)(
            order.id,
            'sg-test-node-03',
            'ins-003',
            '1.2.3.6',
            'root',
            'pass',
            '实例已创建，等待安装代理',
        )

        asset.refresh_from_db()
        self.assertEqual(asset.user_id, existing_owner.id)
        self.assertEqual(asset.actual_expires_at, manual_expiry)
        self.assertEqual(asset.mtproxy_link, manual_link)
        self.assertEqual(asset.mtproxy_secret, 'manual-secret')
        self.assertEqual(asset.mtproxy_port, 9443)
        self.assertEqual(asset.proxy_links[0]['url'], manual_link)
        self.assertEqual(asset.instance_id, 'ins-003')
        self.assertEqual(asset.public_ip, '1.2.3.6')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aws_assets_requires_database_cloud_account(self):
        with self.assertRaisesMessage(CommandError, '未添加启用的 AWS 云账号'):
            call_command('sync_aws_assets', region='ap-southeast-1')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_backup_ports_are_fixed(self):
        self.assertTrue(is_valid_mtproxy_main_port(443))
        self.assertFalse(is_valid_mtproxy_main_port(444))
        self.assertFalse(is_valid_mtproxy_main_port(9529))
        self.assertFalse(is_valid_mtproxy_main_port(9534))
        self.assertFalse(is_valid_mtproxy_main_port(65530))
        self.assertEqual(get_mtproxy_public_ports(443), [443, 9529, 9530, 9531, 9532, 9533, 9534])
        self.assertEqual(get_mtproxy_public_ports(8443), [8443, 9529, 9530, 9531, 9532, 9533, 9534])
        self.assertEqual(get_mtproxy_port_label(443, 9529), '备用 mtprotoproxy')
        self.assertEqual(get_mtproxy_port_label(443, 9534), 'SOCKS5')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_mtproxy_script_runs_mtg_with_fake_tls_secret(self):
        script = _build_mtproxy_script(443, 'eec3bda48fee649e9ea6e32d33cd5f3dd9617a7572652e6d6963726f736f66742e636f6d')
        self.assertIn('RUN_SECRET="ee${RUN_SECRET}617a7572652e6d6963726f736f66742e636f6d"', script)
        self.assertIn('$WORKDIR/bin/mtg run $RUN_SECRET', script)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_mtproxy_extra_links_exclude_main_port(self):
        links = _extract_tg_links(
            'TG链接: tg://proxy?server=1.2.3.4&port=443&secret=ee11111111111111111111111111111111\n'
            '扩展链接: tg://proxy?server=1.2.3.4&port=443&secret=ee22222222222222222222222222222222\n'
            '扩展链接: tg://proxy?server=1.2.3.4&port=9529&secret=ee33333333333333333333333333333333',
            exclude_port=443,
        )
        self.assertEqual(links, ['tg://proxy?server=1.2.3.4&port=9529&secret=ee33333333333333333333333333333333'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_extract_proxy_links_labels_custom_low_port_plan(self):
        links = _extract_proxy_links(
            'MTProxy 安装完成\n'
            '端口: 443\n'
            'TG链接: tg://proxy?server=1.2.3.4&port=443&secret=ee1234567890abcdef1234567890abcd\n'
            '扩展链接: tg://proxy?server=1.2.3.4&port=9529&secret=eeabcdefabcdefabcdefabcdefabcdefab\n'
            '扩展链接: tg://proxy?server=1.2.3.4&port=9530&secret=eeabcdefabcdefabcdefabcdefabcdefab\n'
            'SOCKS5链接: socks5://abcdefabcdefabcdefabcdefabcdefab:abcdefabcdefabcdefabcdefabcdefab@1.2.3.4:9534'
        )
        self.assertEqual([item['name'] for item in links], ['主代理 mtg', '备用 mtprotoproxy', 'Telemt A 三模式', 'SOCKS5'])
        self.assertEqual(links[-1]['username'], 'abcdefabcdefabcdefabcdefabcdefab')
        self.assertEqual(links[-1]['password'], 'abcdefabcdefabcdefabcdefabcdefab')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_compact_proxy_install_note_removes_raw_links(self):
        note = (
            'AWS 实例已创建\n'
            'MTProxy 安装完成\n'
            'TG链接: tg://proxy?server=1.2.3.4&port=443&secret=ee1234\n'
            'SOCKS5链接: socks5://abcdefabcdefabcdefabcdefabcdefab:abcdefabcdefabcdefabcdefabcdefab@1.2.3.4:9534\n'
            '扩展链接: tg://proxy?server=1.2.3.4&port=9530&secret=eeabcd'
        )
        links = _extract_proxy_links(note)
        compact = _compact_proxy_install_note(note, links, 443)

        self.assertIn('AWS 实例已创建', compact)
        self.assertIn('MTProxy/SOCKS5 安装完成', compact)
        self.assertIn('SOCKS5端口: 9534', compact)
        self.assertIn('代理链接已保存到代理链路列表。', compact)
        self.assertNotIn('tg://proxy?', compact)
        self.assertNotIn('socks5://', compact)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_append_status_note_replaces_old_sync_status(self):
        note = append_status_note(
            '人工备注\n状态: 运行中；公网IP: 1.1.1.1；最近同步: old',
            '状态: 运行中；公网IP: 1.1.1.1；最近同步: new',
        )

        self.assertEqual(note, '人工备注\n状态: 运行中；公网IP: 1.1.1.1；最近同步: new')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_asset_note_display_hides_install_and_sync_noise(self):
        note = _display_cloud_asset_note(
            '人工备注保留\n'
            'TG链接: tg://proxy?server=1.2.3.4&port=443&secret=ee1234\n'
            'Get:1 https://cdn-aws.deb.debian.org/debian bookworm InRelease [151 kB]\n'
            'Reading package lists...\n'
            'SOCKS5链接: socks5://secret:secret@1.2.3.4:9534\n'
            'BBR 执行完成\n'
            '状态: 运行中；公网IP: 1.1.1.1；最近同步: old\n'
            '状态: 运行中；公网IP: 1.1.1.1；最近同步: new\n'
            '人工备注保留'
        )

        self.assertEqual(note, '人工备注保留\nBBR 执行完成')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_asset_note_appends_clean_install_summary(self):
        note = _append_cloud_asset_note(
            '人工备注保留\nTG链接: tg://proxy?server=old&port=443&secret=old',
            'MTProxy 安装完成\nTG链接: tg://proxy?server=1.2.3.4&port=443&secret=ee1234\nSOCKS5链接: socks5://secret:secret@1.2.3.4:9534',
            [
                {'name': '主代理 mtg', 'port': '443', 'url': 'tg://proxy?server=1.2.3.4&port=443&secret=ee1234'},
                {'name': 'SOCKS5', 'port': '9534', 'url': 'socks5://secret:secret@1.2.3.4:9534'},
            ],
            443,
        )

        self.assertIn('人工备注保留', note)
        self.assertIn('TG链接: tg://proxy?server=old&port=443&secret=old', note)
        self.assertIn('MTProxy/SOCKS5 安装完成', note)
        self.assertIn('SOCKS5端口: 9534', note)
        self.assertNotIn('socks5://secret:secret@1.2.3.4:9534', note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        staff_user = get_user_model().objects.create_user(username='staff_api_1', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({
                'user_id': new_user.id,
                'actual_expires_at': new_expiry.isoformat(),
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.user_id, new_user.id)
        self.assertEqual(asset.order_id, order.id)
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order_asset_expiry(order), new_expiry)
        self.assertEqual(asset.actual_expires_at, new_expiry)
        self.assertFalse(CloudServerOrder.objects.filter(order_no__startswith='SRVADMIN', replacement_for=order).exists())
        owner_audit_order = CloudServerOrder.objects.filter(order_no__startswith='SRVMANUAL', replacement_for=order, user=new_user).exclude(id=order.id).latest('id')
        self.assertEqual(order_asset_expiry(owner_audit_order), old_expiry)
        self.assertIn('人工编辑所属人', owner_audit_order.provision_note or '')
        self.assertNotIn('人工编辑到期时间', owner_audit_order.provision_note or '')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_shutdown_log_items_prefer_order_lifecycle_schedule(self):
        expires_at = timezone.now() + timezone.timedelta(days=1)
        asset_expires_at = timezone.now() + timezone.timedelta(days=3)
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
            actual_expires_at=asset_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        items = _shutdown_log_items(limit=20)
        row = next(item for item in items if item.get('order_id') == order.id)

        self.assertEqual(parse_datetime(row['actual_expires_at']), asset_expires_at)
        self.assertEqual(parse_datetime(row['suspend_at']), order.suspend_at)
        self.assertEqual(parse_datetime(row['delete_at']), order.delete_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_ip_log_note_aggregates_into_single_ip_trace(self):
        expires_at = timezone.now() + timezone.timedelta(days=2)
        order = CloudServerOrder.objects.create(
            order_no='LOG-CONTEXT-ORDER-1',
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
            suspend_at=expires_at + timezone.timedelta(days=3),
            delete_at=expires_at + timezone.timedelta(days=4),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='log-context-asset',
            public_ip='8.8.8.8',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        first = record_cloud_ip_log(event_type=CloudIpLog.EVENT_CREATED, order=order, asset=asset, public_ip=None, note='开始创建，暂未分配IP')
        second = record_cloud_ip_log(event_type=CloudIpLog.EVENT_CREATED, order=order, asset=asset, public_ip='8.8.8.8', note='第一次创建')
        third = record_cloud_ip_log(event_type=CloudIpLog.EVENT_CREATED, order=order, asset=asset, public_ip='8.8.8.8', note='同秒重复创建')
        fourth = record_cloud_ip_log(event_type=CloudIpLog.EVENT_DELETED, order=order, asset=asset, previous_public_ip='8.8.8.8', public_ip=None, note='实例已删除')

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.id, third.id)
        self.assertEqual(first.id, fourth.id)
        self.assertEqual(CloudIpLog.objects.filter(public_ip='8.8.8.8').count(), 1)
        first.refresh_from_db()
        self.assertEqual(first.event_type, CloudIpLog.EVENT_DELETED)
        self.assertIn('IP：8.8.8.8', first.note)
        self.assertIn('用户：', first.note)
        self.assertIn('执行时间：', first.note)
        self.assertIn('到期时间：', first.note)
        self.assertIn('执行计划：', first.note)
        self.assertIn('执行内容：开始创建，暂未分配IP', first.note)
        self.assertIn('执行内容：第一次创建', first.note)
        self.assertIn('执行内容：同秒重复创建', first.note)
        self.assertIn('执行内容：实例已删除', first.note)

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.id, third.id)
        self.assertEqual(CloudIpLog.objects.filter(public_ip='8.8.8.8').count(), 1)
        first.refresh_from_db()
        self.assertEqual(first.event_type, CloudIpLog.EVENT_DELETED)
        self.assertIn('IP：8.8.8.8', first.note)
        self.assertIn('用户：', first.note)
        self.assertIn('执行时间：', first.note)
        self.assertIn('到期时间：', first.note)
        self.assertIn('执行计划：', first.note)
        self.assertIn('执行内容：第一次创建', first.note)
        self.assertIn('执行内容：同秒重复创建', first.note)
        self.assertIn('执行内容：实例已删除', first.note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_ip_log_rebinds_trace_to_latest_replacement_order(self):
        expires_at = timezone.now() + timezone.timedelta(days=2)
        source_order = CloudServerOrder.objects.create(
            order_no='LOG-TRACE-REPLACE-SOURCE',
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
        )
        source_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=source_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='trace-source',
            public_ip='9.9.9.9',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        replacement_order = CloudServerOrder.objects.create(
            order_no='LOG-TRACE-REPLACE-NEW',
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
            public_ip='5.5.5.5',
            previous_public_ip='9.9.9.9',
            replacement_for=source_order,
        )
        replacement_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=replacement_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='trace-replacement',
            public_ip='5.5.5.5',
            previous_public_ip='9.9.9.9',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_PENDING,
            is_active=True,
        )

        source_log = record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_CREATED,
            order=source_order,
            asset=source_asset,
            public_ip='9.9.9.9',
            note='源订单创建成功',
        )
        replacement_log = record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_CREATED,
            order=replacement_order,
            asset=replacement_asset,
            public_ip='5.5.5.5',
            previous_public_ip='9.9.9.9',
            note='替换订单创建成功',
        )

        self.assertEqual(source_log.id, replacement_log.id)
        source_log.refresh_from_db()
        self.assertEqual(source_log.order_id, replacement_order.id)
        self.assertEqual(source_log.asset_id, replacement_asset.id)
        self.assertEqual(source_log.public_ip, '5.5.5.5')
        self.assertEqual(source_log.previous_public_ip, '9.9.9.9')
        self.assertIn('执行内容：源订单创建成功', source_log.note)
        self.assertIn('执行内容：替换订单创建成功', source_log.note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_shutdown_log_items_include_execution_detail_and_links(self):
        expires_at = timezone.now() - timezone.timedelta(hours=2)
        order = CloudServerOrder.objects.create(
            order_no='SHUTDOWN-DETAIL-ORDER-1',
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
            public_ip='7.7.7.7',
            provision_note='关机执行失败：余额不足',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='shutdown-detail-asset',
            public_ip='7.7.7.7',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=True,
            note='关机执行失败：余额不足',
        )

        items = _shutdown_log_items(limit=20)
        row = next(item for item in items if item.get('asset_id') == asset.id)

        self.assertEqual(row['order_detail_path'], f'/admin/cloud-orders/{order.id}')
        self.assertEqual(row['asset_detail_path'], f'/admin/cloud-assets/{asset.id}')
        self.assertIn('执行状态：', row['note'])
        self.assertIn('是否成功：失败', row['note'])
        self.assertIn('执行时间：', row['note'])
        self.assertIn('执行内容：', row['note'])
        self.assertIn('失败原因：关机执行失败：余额不足', row['note'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unattached_ip_delete_items_use_asset_note(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unattached-shared-note',
            public_ip='5.5.5.31',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=True,
            provider_status='未附加固定IP',
            note='代理列表备注：删除计划也使用我',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('id') == asset.id and not item.get('is_history'))
        self.assertEqual(row['note'], '代理列表备注：删除计划也使用我')
        self.assertIn('代理列表备注', row['display_note'])

        CloudLifecyclePlanNote.objects.create(
            plan_kind=CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE,
            asset=asset,
            note='旧版删除计划备注：现在不再使用',
        )
        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('id') == asset.id and not item.get('is_history'))
        self.assertEqual(row['note'], '代理列表备注：删除计划也使用我')
        self.assertNotIn('旧版删除计划备注', row['display_note'])

        staff_user = get_user_model().objects.create_user(username='staff_plan_table_ip', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/tasks/plans/', {'limit': 20, 'refresh': 1})
        self._attach_bearer_session(request, staff_user)
        response = lifecycle_plans(request)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)['data']
        self.assertTrue(any(
            item.get('asset_id') == asset.id and item.get('plan_kind') == CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE
            for item in data['ip_delete_plan_items']
        ))

    # 功能：验证生命周期计划总数统计全量远期计划，不受当前加载条数截断。
    def test_lifecycle_plans_counts_all_future_server_assets_beyond_loaded_limit(self):
        now = timezone.now()
        for index in range(3):
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'lifecycle-full-count-{index}',
                instance_id=f'i-lifecycle-full-count-{index}',
                public_ip=f'5.5.6.{10 + index}',
                status=CloudAsset.STATUS_RUNNING,
                is_active=True,
                actual_expires_at=now + timezone.timedelta(days=90, minutes=index),
            )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_full_count', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/tasks/plans/', {
            'compact': '1',
            'fields': 'basic,execution',
            'limit': '1',
            'refresh': '1',
        })
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)['data']
        self.assertGreaterEqual(data['shutdown_plan_count'], 3)
        self.assertEqual(len(data['shutdown_plan_items']), 1)
        self.assertFalse(any(
            item.get('asset_name', '').startswith('lifecycle-full-count-')
            for item in data['server_delete_items']
        ))

    # 功能：验证 IP 删除计划总数统计全量未附加固定 IP，不受当前加载条数截断。
    def test_lifecycle_plans_counts_all_ip_delete_plans_beyond_loaded_limit(self):
        now = timezone.now()
        for index in range(3):
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'lifecycle-ip-full-count-{index}',
                public_ip=f'5.5.7.{10 + index}',
                instance_id='',
                provider_resource_id=f'StaticIp-lifecycle-ip-full-count-{index}',
                status=CloudAsset.STATUS_RUNNING,
                provider_status='未附加固定IP',
                note='未附加固定IP',
                is_active=True,
                actual_expires_at=now + timezone.timedelta(days=15, minutes=index),
            )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_ip_full_count', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/tasks/plans/', {
            'compact': '1',
            'fields': 'basic,execution',
            'limit': '1',
            'refresh': '1',
        })
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)['data']
        self.assertGreaterEqual(data['ip_delete_count'], 3)
        self.assertEqual(len(data['ip_delete_plan_items']), 1)
        self.assertTrue(all(not item.get('is_history') for item in data['ip_delete_plan_items']))

    # 功能：验证 IP 删除历史总数统计全量历史来源，不受当前加载条数截断。
    def test_lifecycle_plans_counts_all_ip_delete_history_beyond_loaded_limit(self):
        for index in range(3):
            history_asset = CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'lifecycle-ip-history-full-count-{index}',
                previous_public_ip=f'5.5.8.{10 + index}',
                status=CloudAsset.STATUS_DELETED,
                provider_status='已删除',
                is_active=False,
            )
            record_cloud_ip_log(
                event_type=CloudIpLog.EVENT_RECYCLED,
                asset=history_asset,
                previous_public_ip=f'5.5.8.{10 + index}',
                public_ip=None,
                note='固定 IP 保留期结束，AWS 固定 IP 已真实释放',
            )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_ip_history_full_count', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/tasks/plans/', {
            'compact': '1',
            'fields': 'basic,execution',
            'limit': '1',
            'refresh': '1',
        })
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)['data']
        self.assertGreaterEqual(data['ip_delete_history_count'], 3)
        self.assertEqual(len(data['ip_delete_history_items']), 1)
        self.assertTrue(all(item.get('is_history') for item in data['ip_delete_history_items']))

    # 功能：验证服务器删除历史作为独立分页表返回，不混入删除计划或 IP 删除历史。
    def test_lifecycle_plans_returns_server_delete_history_table(self):
        for index in range(3):
            CloudServerOrder.objects.create(
                order_no=f'LIFECYCLE-SERVER-HISTORY-{index}',
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
                public_ip=f'5.5.9.{10 + index}',
                previous_public_ip=f'5.5.9.{10 + index}',
                delete_at=timezone.now() - timezone.timedelta(hours=index + 1),
                provision_note='执行内容：实例已删除；删除来源：到期自动删除',
            )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_server_history', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/tasks/plans/', {
            'compact': '1',
            'fields': 'basic,execution,notes',
            'limit': '1',
            'refresh': '1',
            'server_history_page_size': '1',
        })
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)['data']
        self.assertGreaterEqual(data['server_history_count'], 3)
        self.assertEqual(len(data['server_history_items']), 1)
        row = data['server_history_items'][0]
        self.assertEqual(row['plan_kind'], 'server_history')
        self.assertEqual(row['source_kind'], 'order')
        self.assertEqual(row['result_label'], '成功')
        self.assertEqual(data['pagination']['server_history']['page_size'], 1)
        self.assertGreaterEqual(data['pagination']['server_history']['total'], 3)
        self.assertNotEqual(row['plan_kind'], CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE)

    # 功能：验证无订单已删除服务器也进入服务器删除历史，避免成为计划页不可见的孤儿资产。
    def test_lifecycle_plans_server_history_includes_orphan_deleted_server_asset(self):
        CloudServerOrder.objects.create(
            order_no='LIFECYCLE-SERVER-HISTORY-ORDER',
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
            public_ip='5.5.9.20',
            previous_public_ip='5.5.9.20',
            delete_at=timezone.now() - timezone.timedelta(hours=1),
            provision_note='执行内容：实例已删除；删除来源：到期自动删除',
        )
        orphan_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='orphan-deleted-server-history',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/orphan-deleted-server-history',
            previous_public_ip='5.5.9.21',
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            note='无订单服务器已删除，应该进入服务器删除历史',
            is_active=False,
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_orphan_server_history', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/tasks/plans/', {
            'compact': '1',
            'fields': 'basic,execution,notes',
            'limit': '20',
            'refresh': '1',
            'server_history_page_size': '20',
        })
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)['data']
        asset_rows = [
            item for item in data['server_history_items']
            if item.get('source_kind') == 'asset' and item.get('asset_id') == orphan_asset.id
        ]
        self.assertEqual(len(asset_rows), 1)
        self.assertGreaterEqual(data['server_history_count'], 2)
        self.assertEqual(asset_rows[0]['plan_kind'], 'server_history')
        self.assertEqual(asset_rows[0]['detail_path'], f'/admin/cloud-assets/{orphan_asset.id}')
        self.assertFalse(any(item.get('asset_id') == orphan_asset.id for item in data['ip_delete_history_items']))

    # 功能：验证服务器删除历史跨来源分页按统一更新时间排序，不会先吐尽订单再补资产。
    def test_lifecycle_plans_server_history_mixes_orders_and_assets_by_updated_at(self):
        base = timezone.now()
        order_newest = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-SERVER-HISTORY-MIX-ORDER-NEWEST',
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
            public_ip='5.5.9.30',
            previous_public_ip='5.5.9.30',
            delete_at=base - timezone.timedelta(hours=1),
            provision_note='最新删机订单',
        )
        order_oldest = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-SERVER-HISTORY-MIX-ORDER-OLDEST',
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
            public_ip='5.5.9.31',
            previous_public_ip='5.5.9.31',
            delete_at=base - timezone.timedelta(hours=4),
            provision_note='最旧删机订单',
        )
        asset_middle = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='server-history-mix-asset-middle',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/server-history-mix-asset-middle',
            previous_public_ip='5.5.9.32',
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            note='中间时间的孤儿删机资产',
            is_active=False,
            actual_expires_at=base - timezone.timedelta(days=1),
        )
        asset_newer = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='server-history-mix-asset-newer',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/server-history-mix-asset-newer',
            previous_public_ip='5.5.9.33',
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            note='第二新的孤儿删机资产',
            is_active=False,
            actual_expires_at=base - timezone.timedelta(days=1),
        )
        CloudServerOrder.objects.filter(id=order_newest.id).update(updated_at=base + timezone.timedelta(minutes=5))
        CloudAsset.objects.filter(id=asset_newer.id).update(updated_at=base + timezone.timedelta(minutes=4))
        CloudAsset.objects.filter(id=asset_middle.id).update(updated_at=base + timezone.timedelta(minutes=3))
        CloudServerOrder.objects.filter(id=order_oldest.id).update(updated_at=base + timezone.timedelta(minutes=2))

        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_server_history_mix', password='x', is_staff=True)
        seen = []
        for page in [1, 2]:
            request = RequestFactory().get('/api/admin/tasks/plans/', {
                'compact': '1',
                'fields': 'basic,execution,notes',
                'limit': '2',
                'server_history_page': str(page),
                'server_history_page_size': '2',
                **({'refresh': '1'} if page == 1 else {}),
            })
            self._attach_bearer_session(request, staff_user)
            response = lifecycle_plans(request)
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.content)['data']
            self.assertEqual(data['pagination']['server_history']['page'], page)
            self.assertEqual(data['pagination']['server_history']['page_size'], 2)
            seen.extend((item.get('source_kind'), item.get('source_id')) for item in data['server_history_items'])

        self.assertEqual(seen, [
            ('order', order_newest.id),
            ('asset', asset_newer.id),
            ('asset', asset_middle.id),
            ('order', order_oldest.id),
        ])

    # 功能：验证计划页全量统计随刷新进入缓存，普通加载不重复扫全库。
    def test_lifecycle_plans_reuses_cached_count_snapshot_after_refresh(self):
        import bot.api as bot_api

        SiteConfig.objects.filter(key=bot_api._LIFECYCLE_PLAN_COUNT_SNAPSHOT_KEY).delete()
        bot_api._LIFECYCLE_PLAN_CACHE.update({
            'bundle': None,
            'counts': None,
            'generated_at': None,
            'limit': 0,
        })
        now = timezone.now()
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='lifecycle-cached-count-server',
            instance_id='i-lifecycle-cached-count-server',
            public_ip='5.5.7.40',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            actual_expires_at=now + timezone.timedelta(days=30),
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_cached_count', password='x', is_staff=True)
        refresh_request = RequestFactory().get('/api/admin/tasks/plans/', {
            'compact': '1',
            'fields': 'basic,execution',
            'limit': '1',
            'refresh': '1',
        })
        self._attach_bearer_session(refresh_request, staff_user)

        refresh_response = lifecycle_plans(refresh_request)

        self.assertEqual(refresh_response.status_code, 200)
        self.assertIsNotNone(bot_api._LIFECYCLE_PLAN_CACHE.get('counts'))

        cached_request = RequestFactory().get('/api/admin/tasks/plans/', {
            'compact': '1',
            'fields': 'basic,execution',
            'limit': '1',
        })
        self._attach_bearer_session(cached_request, staff_user)
        with patch('bot.api._build_lifecycle_plan_count_snapshot', side_effect=AssertionError('count snapshot should be cached')):
            cached_response = lifecycle_plans(cached_request)

        self.assertEqual(cached_response.status_code, 200)
        cached_data = json.loads(cached_response.content)['data']
        self.assertGreaterEqual(cached_data['shutdown_plan_count'], 1)

    # 功能：验证计划页进程缓存清空后可复用持久计数快照，避免冷启动普通加载重扫全库。
    def test_lifecycle_plans_reads_persisted_count_snapshot_after_process_cache_clear(self):
        import bot.api as bot_api

        SiteConfig.objects.filter(key=bot_api._LIFECYCLE_PLAN_COUNT_SNAPSHOT_KEY).delete()
        bot_api._LIFECYCLE_PLAN_CACHE.update({
            'bundle': None,
            'counts': None,
            'generated_at': None,
            'limit': 0,
        })
        now = timezone.now()
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='lifecycle-persisted-count-server',
            instance_id='i-lifecycle-persisted-count-server',
            public_ip='5.5.7.41',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            actual_expires_at=now + timezone.timedelta(days=30),
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_persisted_count', password='x', is_staff=True)
        refresh_request = RequestFactory().get('/api/admin/tasks/plans/', {
            'compact': '1',
            'fields': 'basic,execution',
            'limit': '1',
            'refresh': '1',
        })
        self._attach_bearer_session(refresh_request, staff_user)
        refresh_response = lifecycle_plans(refresh_request)
        self.assertEqual(refresh_response.status_code, 200)

        bot_api._LIFECYCLE_PLAN_CACHE.update({
            'bundle': None,
            'counts': None,
            'generated_at': None,
            'limit': 0,
        })
        cached_request = RequestFactory().get('/api/admin/tasks/plans/', {
            'compact': '1',
            'fields': 'basic,execution',
            'limit': '1',
        })
        self._attach_bearer_session(cached_request, staff_user)
        with patch('bot.api._build_lifecycle_plan_count_snapshot', side_effect=AssertionError('persisted count snapshot should be reused')):
            cached_response = lifecycle_plans(cached_request)

        self.assertEqual(cached_response.status_code, 200)
        cached_data = json.loads(cached_response.content)['data']
        self.assertEqual(cached_data['cache_mode'], 'cached')
        self.assertGreaterEqual(cached_data['shutdown_plan_count'], 1)

    # 功能：验证计划页计数缓存遇到资产数据变化会自动失效，避免分页 total 继续使用旧值。
    def test_lifecycle_plans_rebuilds_cached_count_snapshot_when_asset_changes(self):
        import bot.api as bot_api

        SiteConfig.objects.filter(key=bot_api._LIFECYCLE_PLAN_COUNT_SNAPSHOT_KEY).delete()
        bot_api._LIFECYCLE_PLAN_CACHE.update({
            'bundle': None,
            'counts': None,
            'counts_fingerprint': None,
            'generated_at': None,
            'limit': 0,
        })
        now = timezone.now()
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='lifecycle-fingerprint-server-1',
            instance_id='i-lifecycle-fingerprint-server-1',
            public_ip='5.5.7.42',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            actual_expires_at=now + timezone.timedelta(days=30),
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_fingerprint_count', password='x', is_staff=True)
        refresh_request = RequestFactory().get('/api/admin/tasks/plans/', {
            'compact': '1',
            'fields': 'basic,execution',
            'limit': '1',
            'refresh': '1',
            'shutdown_page_size': '1',
        })
        self._attach_bearer_session(refresh_request, staff_user)
        refresh_response = lifecycle_plans(refresh_request)
        self.assertEqual(refresh_response.status_code, 200)
        refresh_data = json.loads(refresh_response.content)['data']
        self.assertEqual(refresh_data['shutdown_plan_count'], 1)
        self.assertEqual(refresh_data['pagination']['shutdown_plan']['total'], 1)

        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='lifecycle-fingerprint-server-2',
            instance_id='i-lifecycle-fingerprint-server-2',
            public_ip='5.5.7.43',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            actual_expires_at=now + timezone.timedelta(days=31),
        )
        cached_request = RequestFactory().get('/api/admin/tasks/plans/', {
            'compact': '1',
            'fields': 'basic,execution',
            'limit': '1',
            'shutdown_page_size': '1',
        })
        self._attach_bearer_session(cached_request, staff_user)
        cached_response = lifecycle_plans(cached_request)

        self.assertEqual(cached_response.status_code, 200)
        cached_data = json.loads(cached_response.content)['data']
        self.assertEqual(cached_data['shutdown_plan_count'], 2)
        self.assertEqual(cached_data['pagination']['shutdown_plan']['total'], 2)

    # 功能：验证计划页计数快照过期后即使资产指纹未变也会重算，避免默认加载继续显示旧计划总数。
    def test_lifecycle_plans_rebuilds_stale_count_snapshot_without_fingerprint_change(self):
        import bot.api as bot_api

        SiteConfig.objects.filter(key=bot_api._LIFECYCLE_PLAN_COUNT_SNAPSHOT_KEY).delete()
        bot_api._LIFECYCLE_PLAN_CACHE.update({
            'bundle': None,
            'counts': None,
            'counts_fingerprint': None,
            'generated_at': None,
            'limit': 0,
        })
        now = timezone.now()
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='lifecycle-stale-count-server',
            instance_id='i-lifecycle-stale-count-server',
            public_ip='5.5.7.44',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            actual_expires_at=now + timezone.timedelta(days=30),
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_stale_count', password='x', is_staff=True)
        refresh_request = RequestFactory().get('/api/admin/tasks/plans/', {
            'compact': '1',
            'fields': 'basic,execution',
            'limit': '1',
            'refresh': '1',
            'shutdown_page_size': '1',
            'delete_page_size': '1',
        })
        self._attach_bearer_session(refresh_request, staff_user)
        refresh_response = lifecycle_plans(refresh_request)
        self.assertEqual(refresh_response.status_code, 200)
        refresh_data = json.loads(refresh_response.content)['data']
        self.assertEqual(refresh_data['shutdown_plan_count'], 1)
        self.assertEqual(refresh_data['server_delete_count'], 0)

        fingerprint = bot_api._lifecycle_plan_count_fingerprint()
        CloudAsset.objects.filter(id=asset.id).update(status=CloudAsset.STATUS_STOPPED)
        self.assertEqual(bot_api._lifecycle_plan_count_fingerprint(), fingerprint)

        stale_generated_at = timezone.now() - timezone.timedelta(
            seconds=bot_api._LIFECYCLE_PLAN_COUNT_SNAPSHOT_MAX_AGE_SECONDS + 5,
        )
        raw = SiteConfig.get(bot_api._LIFECYCLE_PLAN_COUNT_SNAPSHOT_KEY, '')
        payload = json.loads(raw)
        payload['generated_at'] = stale_generated_at.isoformat()
        SiteConfig.set(bot_api._LIFECYCLE_PLAN_COUNT_SNAPSHOT_KEY, json.dumps(payload, ensure_ascii=False))
        bot_api._LIFECYCLE_PLAN_CACHE['generated_at'] = stale_generated_at

        cached_request = RequestFactory().get('/api/admin/tasks/plans/', {
            'compact': '1',
            'fields': 'basic,execution',
            'limit': '1',
            'shutdown_page_size': '1',
            'delete_page_size': '1',
        })
        self._attach_bearer_session(cached_request, staff_user)
        cached_response = lifecycle_plans(cached_request)

        self.assertEqual(cached_response.status_code, 200)
        cached_data = json.loads(cached_response.content)['data']
        self.assertEqual(cached_data['shutdown_plan_count'], 0)
        self.assertEqual(cached_data['server_delete_count'], 1)
        self.assertEqual(cached_data['pagination']['shutdown_plan']['total'], 0)
        self.assertEqual(cached_data['pagination']['server_delete']['total'], 1)

    # 功能：验证服务器删除计划服务端分页契约，跨页不重复且排序结果能和数据库事实对上。
    def test_lifecycle_plans_server_delete_pagination_contract(self):
        now = timezone.now()
        account = self._aws_test_account()
        expected_names = []
        for index in range(7):
            expected_names.append(f'lifecycle-server-delete-page-{index}')
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                provider='aws_lightsail',
                cloud_account=account,
                account_label=cloud_account_label(account),
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'lifecycle-server-delete-page-{index}',
                instance_id=f'i-lifecycle-server-delete-page-{index}',
                public_ip=f'5.5.9.{10 + index}',
                status=CloudAsset.STATUS_STOPPED,
                is_active=True,
                actual_expires_at=now + timezone.timedelta(days=30, minutes=index),
            )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_server_page', password='x', is_staff=True)

        seen = []
        for page in [1, 2, 3, 4]:
            params = {
                'compact': '1',
                'fields': 'basic,execution',
                'limit': '2',
                'server_delete_page': str(page),
                'server_delete_page_size': '2',
            }
            if page == 1:
                params['refresh'] = '1'
            request = RequestFactory().get('/api/admin/tasks/plans/', params)
            self._attach_bearer_session(request, staff_user)
            response = lifecycle_plans(request)
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.content)['data']
            self.assertEqual(data['pagination']['server_delete']['page'], page)
            self.assertEqual(data['pagination']['server_delete']['page_size'], 2)
            self.assertGreaterEqual(data['pagination']['server_delete']['total'], 7)
            for item in data['server_delete_items']:
                self.assertIsInstance(item['id'], int)
                self.assertIn(item.get('source_kind'), {'asset', 'order'})
                self.assertIsInstance(item.get('source_id'), int)
                self.assertTrue(str(item.get('plan_item_key') or '').startswith('plan:'))
            seen.extend(item['asset_name'] for item in data['server_delete_items'])

        self.assertEqual([name for name in seen if name in expected_names], expected_names)
        self.assertEqual(len(seen), len(set(seen)))

    # 功能：服务器生命周期计划基准查询不得回退到未附加 IP id__in 子查询，避免真实 MySQL 百万数据 count 超时。
    def test_lifecycle_plan_server_queryset_avoids_unattached_ip_subquery(self):
        from cloud.lifecycle_plan_queries import server_lifecycle_plan_queryset

        sql = str(server_lifecycle_plan_queryset().query).lower()

        self.assertNotIn(' in (select ', sql)
        self.assertNotIn(' in ( select ', sql)

    # 功能：验证服务器计划页不会按同 IP 折叠旧服务器资产，避免深分页少行和资产不可管理。
    def test_lifecycle_plans_keep_same_ip_orphan_servers_visible_across_pages(self):
        now = timezone.now()
        account = self._aws_test_account()
        expected_names = []
        base_expires_at = now - timezone.timedelta(days=3650)
        for index in range(60):
            asset_name = f'lifecycle-same-ip-visible-{index:02d}'
            expected_names.append(asset_name)
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                provider='aws_lightsail',
                cloud_account=account,
                account_label=cloud_account_label(account),
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=asset_name,
                instance_id=f'i-lifecycle-same-ip-visible-{index:02d}',
                public_ip='5.5.19.19',
                status=CloudAsset.STATUS_RUNNING,
                is_active=True,
                actual_expires_at=base_expires_at + timezone.timedelta(minutes=index),
            )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_same_ip_visible', password='x', is_staff=True)

        seen = []
        for page in [1, 2]:
            request = RequestFactory().get('/api/admin/tasks/plans/', {
                'compact': '1',
                'fields': 'basic,execution',
                'limit': '20',
                'tables': 'shutdown_plan',
                'shutdown_page': str(page),
                'shutdown_page_size': '20',
            })
            self._attach_bearer_session(request, staff_user)
            response = lifecycle_plans(request)
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.content)['data']
            self.assertEqual(data['pagination']['shutdown_plan']['page'], page)
            self.assertEqual(data['pagination']['shutdown_plan']['page_size'], 20)
            self.assertEqual(data['pagination']['shutdown_plan']['loaded'], 20)
            self.assertGreaterEqual(data['pagination']['shutdown_plan']['total'], 60)
            self.assertEqual(len(data['shutdown_plan_items']), 20)
            seen.extend(item['asset_name'] for item in data['shutdown_plan_items'])

        self.assertEqual([name for name in seen if name in expected_names], expected_names[:40])

    # 功能：验证 IP 删除历史服务端分页契约，跨页不重复且排序结果能和数据库事实对上。
    def test_lifecycle_plans_ip_delete_history_pagination_contract(self):
        expected_names = []
        for index in range(7):
            expected_names.append(f'lifecycle-ip-history-page-{index}')
            CloudIpLog.objects.create(
                event_type=CloudIpLog.EVENT_RECYCLED,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'lifecycle-ip-history-page-{index}',
                previous_public_ip=f'5.5.10.{10 + index}',
                public_ip=None,
                note='固定 IP 保留期结束，AWS 固定 IP 已真实释放',
            )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_ip_history_page', password='x', is_staff=True)

        seen = []
        for page in [1, 2, 3, 4]:
            params = {
                'compact': '1',
                'fields': 'basic,execution',
                'limit': '2',
                'ip_delete_history_page': str(page),
                'ip_delete_history_page_size': '2',
            }
            if page == 1:
                params['refresh'] = '1'
            request = RequestFactory().get('/api/admin/tasks/plans/', params)
            self._attach_bearer_session(request, staff_user)
            response = lifecycle_plans(request)
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.content)['data']
            self.assertEqual(data['pagination']['ip_delete_history']['page'], page)
            self.assertEqual(data['pagination']['ip_delete_history']['page_size'], 2)
            self.assertGreaterEqual(data['pagination']['ip_delete_history']['total'], 7)
            for item in data['ip_delete_history_items']:
                self.assertIsInstance(item['id'], int)
                self.assertIn(item.get('source_kind'), {'asset', 'ip_log'})
                self.assertIsInstance(item.get('source_id'), int)
                self.assertTrue(str(item.get('plan_item_key') or '').startswith('plan:'))
            seen.extend(item['asset_name'] for item in data['ip_delete_history_items'])

        self.assertEqual([name for name in seen if name in expected_names], list(reversed(expected_names)))
        self.assertEqual(len(seen), len(set(seen)))

    # 功能：验证 IP 删除历史后半段分页从尾部反向合并后，返回顺序仍与正向时间轴一致。
    def test_ip_delete_history_page_sources_reverse_tail_keeps_order(self):
        from cloud.lifecycle_plan_queries import ip_delete_history_page_sources

        base = timezone.now() - timezone.timedelta(days=1)
        logs = []
        for index in range(211):
            logs.append(CloudIpLog.objects.create(
                event_type=CloudIpLog.EVENT_RECYCLED,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'ip-history-tail-{index}',
                previous_public_ip=f'5.5.11.{index}',
                public_ip=None,
                note='固定 IP 保留期结束，AWS 固定 IP 已真实释放',
            ))
        for index, log in enumerate(logs):
            CloudIpLog.objects.filter(id=log.id).update(created_at=base + timezone.timedelta(seconds=index))

        sources = ip_delete_history_page_sources(
            page=106,
            page_size=2,
            log_total=211,
            asset_total=0,
            completed_total=0,
        )

        self.assertEqual([source.asset_name for _kind, source in sources], ['ip-history-tail-0'])

    # 功能：验证 IP 删除计划尾页候选扫描会跳过非未附加资产，并保持与精确查询相同的顺序。
    def test_unattached_ip_delete_plan_tail_page_keeps_exact_order(self):
        from cloud.lifecycle_plan_queries import _unattached_ip_delete_tail_page, unattached_ip_delete_active_queryset

        base = timezone.now()
        for index in range(9):
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'tail-ip-delete-match-{index}',
                instance_id='',
                provider_resource_id=f'StaticIp-tail-match-{index}',
                public_ip=f'5.5.21.{10 + index}',
                status=CloudAsset.STATUS_UNKNOWN,
                provider_status='未附加固定IP',
                actual_expires_at=base + timezone.timedelta(minutes=index),
            )
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'tail-ip-delete-noise-{index}',
                instance_id='',
                provider_resource_id=f'instance-tail-noise-{index}',
                public_ip=f'5.5.22.{10 + index}',
                status=CloudAsset.STATUS_RUNNING,
                provider_status='运行中',
                actual_expires_at=base + timezone.timedelta(minutes=index, seconds=30),
            )

        queryset = unattached_ip_delete_active_queryset()
        expected = list(queryset.order_by('actual_expires_at', 'id').values_list('asset_name', flat=True))[-3:]
        rows = _unattached_ip_delete_tail_page(queryset, reverse_start=0, count=3)
        self.assertEqual([row.asset_name for row in rows], expected)

    # 功能：验证 IP 删除历史跨来源分页按统一时间轴排序，不会先吐尽日志再补资产。
    def test_lifecycle_plans_ip_delete_history_mixes_logs_and_assets_by_time(self):
        base = timezone.now()
        log_newest = CloudIpLog.objects.create(
            event_type=CloudIpLog.EVENT_RECYCLED,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='ip-history-log-newest',
            previous_public_ip='5.5.10.30',
            public_ip=None,
            note='固定 IP 保留期结束，AWS 固定 IP 已真实释放',
        )
        history_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='ip-history-asset-middle',
            previous_public_ip='5.5.10.31',
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            note='固定 IP 云端已不存在',
            is_active=False,
            actual_expires_at=base - timezone.timedelta(days=1),
        )
        completed_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='ip-history-completed-active',
            previous_public_ip='5.5.10.32',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=True,
            actual_expires_at=base - timezone.timedelta(days=1),
        )
        CloudIpLog.objects.create(
            event_type=CloudIpLog.EVENT_CHANGED,
            asset=completed_asset,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name=completed_asset.asset_name,
            previous_public_ip='5.5.10.32',
            public_ip=None,
            note='实例已删除，固定 IP 保留',
        )
        log_oldest = CloudIpLog.objects.create(
            event_type=CloudIpLog.EVENT_DELETED,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='ip-history-log-oldest',
            previous_public_ip='5.5.10.33',
            public_ip=None,
            note='未附加固定IP；IP校验发现云上不存在，已标记删除',
        )

        CloudIpLog.objects.filter(id=log_newest.id).update(created_at=base + timezone.timedelta(minutes=5))
        CloudAsset.objects.filter(id=history_asset.id).update(updated_at=base + timezone.timedelta(minutes=4))
        CloudAsset.objects.filter(id=completed_asset.id).update(updated_at=base + timezone.timedelta(minutes=3))
        CloudIpLog.objects.filter(id=log_oldest.id).update(created_at=base + timezone.timedelta(minutes=2))

        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_ip_history_mix', password='x', is_staff=True)
        seen = []
        for page in [1, 2]:
            request = RequestFactory().get('/api/admin/tasks/plans/', {
                'compact': '1',
                'fields': 'basic,execution,notes',
                'limit': '2',
                'ip_delete_history_page': str(page),
                'ip_delete_history_page_size': '2',
                **({'refresh': '1'} if page == 1 else {}),
            })
            self._attach_bearer_session(request, staff_user)
            response = lifecycle_plans(request)
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.content)['data']
            self.assertEqual(data['pagination']['ip_delete_history']['page'], page)
            self.assertEqual(data['pagination']['ip_delete_history']['page_size'], 2)
            seen.extend((item.get('source_kind'), item.get('source_id')) for item in data['ip_delete_history_items'])

        self.assertEqual(seen, [
            ('ip_log', log_newest.id),
            ('asset', history_asset.id),
            ('asset', completed_asset.id),
            ('ip_log', log_oldest.id),
        ])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_update_lifecycle_plan_note_updates_asset_note(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unattached-shared-note-save',
            public_ip='5.5.5.32',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=True,
            provider_status='未附加固定IP',
            note='代理列表原备注',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        staff_user = get_user_model().objects.create_user(username='staff_plan_note_asset', password='x', is_staff=True)
        sync_request = self.factory.get('/api/admin/tasks/plans/', {'limit': 20, 'refresh': 1})
        self._attach_bearer_session(sync_request, staff_user)
        sync_response = lifecycle_plans(sync_request)
        self.assertEqual(sync_response.status_code, 200)

        request = RequestFactory().post(
            '/api/admin/tasks/plans/notes/',
            data=json.dumps({'asset_id': asset.id, 'item_type': 'asset', 'note': '删除计划新备注'}),
            content_type='application/json',
        )
        self._attach_bearer_session(request, staff_user)

        response = update_lifecycle_plan_note(request)

        self.assertEqual(response.status_code, 200)
        asset.refresh_from_db()
        self.assertEqual(asset.note, '删除计划新备注')
        self.assertFalse(CloudLifecyclePlanNote.objects.filter(
            plan_kind=CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE,
            asset=asset,
        ).exists())
        sync_response = lifecycle_plans(sync_request)
        data = json.loads(sync_response.content)['data']
        plan_row = next(item for item in data['ip_delete_plan_items'] if item.get('asset_id') == asset.id)
        self.assertEqual(plan_row['note'], '删除计划新备注')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_use_separate_order_plan_note(self):
        delete_at = timezone.now() + timezone.timedelta(hours=1)
        order = CloudServerOrder.objects.create(
            order_no='SHUTDOWN-INDEPENDENT-NOTE-1',
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
            status='suspended',
            public_ip='7.7.7.31',
            suspend_at=delete_at - timezone.timedelta(days=1),
            delete_at=delete_at,
            provision_note='订单原备注：不要复用我',
        )
        self._attach_order_expiry_asset(order, delete_at - timezone.timedelta(days=3))
        plan_note = CloudLifecyclePlanNote.objects.create(
            plan_kind=CloudLifecyclePlanNote.PLAN_KIND_SHUTDOWN_ORDER,
            order=order,
            note='删机计划备注：单独保存',
        )
        staff_user = get_user_model().objects.create_user(username='staff_plan_note_order', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/tasks/plans/', {'limit': 20, 'refresh': 1})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['code'], 0)
        plan_note.refresh_from_db()
        self.assertEqual(plan_note.note, '删机计划备注：单独保存')
        self.assertEqual(order.provision_note, '订单原备注：不要复用我')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unattached_ip_delete_items_include_name_expiry_and_detail_path(self):
        delete_due_at = timezone.now() + timezone.timedelta(days=3)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='visible-unattached-name-expiry',
            public_ip='5.5.5.9',
            actual_expires_at=delete_due_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
        )

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('id') == asset.id)

        self.assertEqual(row['asset_name'], 'visible-unattached-name-expiry')
        self.assertEqual(row['detail_path'], f'/admin/cloud-assets/{asset.id}')
        self.assertEqual(parse_datetime(row['actual_expires_at']), delete_due_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_orders_list_keeps_renew_pending_visible(self):
        expires_at = timezone.now() + timezone.timedelta(hours=8)
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
            expired_at=timezone.now() + timezone.timedelta(minutes=30),
        )
        self._attach_order_expiry_asset(order, expires_at)
        staff_user = get_user_model().objects.create_user(username='staff_cloud_order_list', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/cloud-orders/')
        self._attach_bearer_session(request, staff_user)

        response = cloud_orders_list(request)
        payload = json.loads(response.content)
        data = payload.get('data') or []
        row = next(item for item in data if item.get('id') == order.id)

        self.assertEqual(row['renew_status'], 'renew_pending')
        self.assertEqual(row['renew_status_label'], '续费待支付')
        self.assertTrue(row['can_renew'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unattached_ip_delete_items_compact_display_note(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='visible-unattached-compact-note',
            public_ip='5.5.5.8',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP\nGet: apt noise\ntg://proxy?server=1.1.1.1&port=9528&secret=x\nsocks5://u:p@1.1.1.1:9534\n人工备注保留',
        )

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('id') == asset.id)

        self.assertIn('未附加固定IP', row['display_note'])
        self.assertIn('人工备注保留', row['display_note'])
        self.assertNotIn('tg://proxy?', row['display_note'])
        self.assertNotIn('socks5://', row['display_note'])
        self.assertNotIn('Get:', row['display_note'])
        self.assertEqual(row['note'], asset.note)
        self.assertIn('tg://proxy?', row['source_note'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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

    # 功能：验证未附加固定 IP 缺失到期时间时，计划页会自动补齐 15 天后删除。
    def test_unattached_ip_delete_items_fill_missing_expiry_with_default_delete_plan(self):
        before = timezone.now()
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='visible-unattached-missing-expiry',
            public_ip='5.5.5.17',
            actual_expires_at=None,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
        )

        items = _unattached_ip_delete_items(limit=20)
        asset.refresh_from_db()
        row = next(item for item in items if item.get('id') == asset.id)

        self.assertIsNotNone(asset.actual_expires_at)
        self.assertGreater(asset.actual_expires_at, before + timezone.timedelta(days=14))
        self.assertLess(asset.actual_expires_at, before + timezone.timedelta(days=16))
        self.assertEqual(parse_datetime(row['delete_at']), asset.actual_expires_at)
        self.assertEqual(parse_datetime(row['actual_expires_at']), asset.actual_expires_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unattached_ip_delete_items_skip_inactive_cloud_account_assets(self):
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
            is_active=False,
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unattached_ip_delete_items_include_sync_deleted_history(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='sync-deleted-unattached-ip',
            public_ip=None,
            previous_public_ip='5.5.5.10',
            status=CloudAsset.STATUS_DELETED,
            is_active=False,
            provider_status='云上未找到实例/IP',
            note='未附加固定IP；状态: 云上未找到实例/IP',
        )
        record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_DELETED,
            asset=asset,
            previous_public_ip='5.5.5.10',
            public_ip=None,
            note='IP校验发现云上不存在，已标记删除',
        )

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('asset_name') == 'sync-deleted-unattached-ip')

        self.assertEqual(row['public_ip'], '5.5.5.10')
        self.assertIn('IP校验发现云上不存在，已标记删除', row['note'])
        self.assertTrue(row['is_overdue'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unattached_ip_delete_items_exclude_cloud_missing_active_plan(self):
        missing_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='missing-unattached-active-plan',
            public_ip='5.5.5.11',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='云上未找到实例/IP-待确认',
            note='未附加固定IP；IP校验发现云上不存在，已标记删除',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        note_deleted_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='note-deleted-unattached-active-plan',
            public_ip='5.5.5.13',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            note='未附加固定IP；IP校验发现云上不存在，已标记删除',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        visible_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='visible-unattached-active-plan',
            public_ip='5.5.5.12',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )

        items = _unattached_ip_delete_items(limit=20)
        active_asset_ids = {item.get('id') for item in items if not item.get('is_history')}

        self.assertNotIn(missing_asset.id, active_asset_ids)
        self.assertNotIn(note_deleted_asset.id, active_asset_ids)
        self.assertIn(visible_asset.id, active_asset_ids)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unattached_ip_delete_items_prefer_asset_note_over_trace_note(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='manual-note-unattached-active-plan',
            public_ip='5.5.5.18',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=True,
            provider_status='未附加固定IP',
            note='人工备注：先生已确认保留',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        CloudLifecyclePlanNote.objects.create(
            plan_kind=CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE,
            asset=asset,
            note='旧版删除计划备注：不要显示我',
        )
        CloudIpLog.objects.create(
            asset=asset,
            user=self.user,
            public_ip=asset.public_ip,
            event_type=CloudIpLog.EVENT_DELETED,
            note='未附加固定IP；IP校验发现云上不存在，已标记删除',
        )

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('id') == asset.id and not item.get('is_history'))

        self.assertEqual(row['note'], '人工备注：先生已确认保留')
        self.assertIn('人工备注', row['display_note'])
        self.assertNotIn('旧版删除计划备注', row['display_note'])
        self.assertEqual(row['deletion_source_label'], '同步校验删除')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_ip_log_delete_keeps_previous_ip_from_change_chain(self):
        order = CloudServerOrder.objects.create(
            order_no='IP-LOG-CHAIN-DELETE-1',
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
            public_ip='6.6.6.2',
            previous_public_ip='6.6.6.1',
        )
        first = record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_CHANGED,
            order=order,
            public_ip='6.6.6.2',
            previous_public_ip='6.6.6.1',
            note='更换IP，6.6.6.1 -> 6.6.6.2',
        )
        second = record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_DELETED,
            order=order,
            public_ip=None,
            previous_public_ip='6.6.6.2',
            note='IP校验发现云上不存在，已标记删除',
        )
        first.refresh_from_db()

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.public_ip, '6.6.6.2')
        self.assertEqual(first.previous_public_ip, '6.6.6.1')
        self.assertEqual(first.event_type, CloudIpLog.EVENT_DELETED)
        self.assertIn('IP校验发现云上不存在，已标记删除', first.note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unattached_ip_delete_items_dedupe_same_ip_and_mark_covered(self):
        old_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='old-duplicate-static-ip',
            public_ip='5.5.5.20',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        latest_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='latest-duplicate-static-ip',
            public_ip='5.5.5.20',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() + timezone.timedelta(days=2),
        )
        CloudAsset.objects.filter(id=old_asset.id).update(updated_at=timezone.now() - timezone.timedelta(days=1))
        CloudAsset.objects.filter(id=latest_asset.id).update(updated_at=timezone.now())

        items = _unattached_ip_delete_items(limit=20)
        rows = [item for item in items if item.get('public_ip') == '5.5.5.20']

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['asset_id'], latest_asset.id)
        self.assertIn('covered_duplicates', rows[0].get('quality_flags') or [])
        self.assertIn('已覆盖 1 条同 IP 旧记录', rows[0].get('quality_label') or '')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unattached_ip_delete_items_mark_cloud_missing_history(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='cloud-missing-history-static-ip',
            public_ip=None,
            previous_public_ip='5.5.5.21',
            status=CloudAsset.STATUS_DELETED,
            is_active=False,
            provider_status='云上未找到实例/IP',
            note='未附加固定IP；IP校验发现云上不存在，已标记删除',
        )
        record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_DELETED,
            asset=asset,
            previous_public_ip='5.5.5.21',
            public_ip=None,
            note='IP校验发现云上不存在，已标记删除',
        )

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('public_ip') == '5.5.5.21')

        self.assertIn('cloud_missing', row.get('quality_flags') or [])
        self.assertIn('云上已不存在', row.get('quality_label') or '')
        self.assertIn('云上已不存在', row.get('execution_status') or '')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unattached_ip_delete_items_skip_assets_attached_to_instance(self):
        attached_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='attached-static-ip-asset',
            public_ip='5.5.5.8',
            instance_id='attached-instance-1',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='旧同步残留：未附加固定IP',
        )

        items = _unattached_ip_delete_items(limit=20)
        asset_ids = {item.get('id') for item in items}

        self.assertNotIn(attached_asset.id, asset_ids)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_cloud_assets_runs_enabled_accounts_and_merges_results(self):
        aliyun_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_ALIYUN,
            name='aliyun-sync-assets-all',
            external_account_id='acct-aliyun-sync-assets-all',
            access_key='ak',
            secret_key='sk',
            region_hint='cn-hongkong',
            is_active=True,
        )
        aws_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-sync-assets-all',
            external_account_id='acct-aws-sync-assets-all',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_sync_assets_all', password='x', is_staff=True, is_superuser=True)
        calls = []

        # 测试类：组织 AwsCommand 相关的回归测试。
        class AwsCommand:
            synced_regions = ['ap-southeast-1']
            sync_errors = []

        # 测试类：组织 AliyunCommand 相关的回归测试。
        class AliyunCommand:
            pass

        # 功能：处理 云资产、云订单和生命周期 中的 fake call command 业务流程。
        def fake_call_command(command_name, **kwargs):
            calls.append((command_name, kwargs))
            if command_name == 'sync_aws_assets':
                return AwsCommand(), f'aws account {kwargs.get("account_id")} ok\n'
            return AliyunCommand(), f'aliyun account {kwargs.get("account_id")} ok\n'

        request = RequestFactory().post('/api/admin/cloud-assets/sync/', data='{}', content_type='application/json')
        request = self._attach_bearer_session(request, staff_user)
        response = sync_cloud_assets(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)['data']
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['queued'])

        job = CloudAssetSyncJob.objects.get(pk=payload['job_id'])
        self.assertEqual(job.status, CloudAssetSyncJob.STATUS_QUEUED)
        self.assertEqual(job.current_task, '已加入同步队列')
        self.assertTrue(CloudAssetSyncJobEvent.objects.filter(job_id=job.id, event_type=CloudAssetSyncJobEvent.TYPE_QUEUED).exists())
        with patch('cloud.sync_jobs._call_command_capture_threaded', side_effect=fake_call_command), \
            patch('cloud.sync_jobs._refresh_dashboard_plan_snapshots_deferred'):
            job = _execute_cloud_asset_sync_job(job)

        result = job.result_payload
        self.assertEqual(job.status, CloudAssetSyncJob.STATUS_SUCCEEDED)
        self.assertEqual(job.progress_current, job.progress_total)
        self.assertEqual(job.progress_total, 2)
        self.assertEqual(job.current_task, '同步完成')
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertTrue(CloudAssetSyncJobEvent.objects.filter(job_id=job.id, event_type=CloudAssetSyncJobEvent.TYPE_PROGRESS).exists())
        self.assertTrue(CloudAssetSyncJobEvent.objects.filter(job_id=job.id, event_type=CloudAssetSyncJobEvent.TYPE_LOG).exists())
        self.assertTrue(result['ok'])
        self.assertTrue(result['synced']['aliyun'])
        self.assertTrue(result['synced']['aws'])
        self.assertTrue(result['synced']['reconcile'])
        self.assertIn('ap-southeast-1', result['aws_regions'])
        self.assertIn(('sync_aliyun_assets', {'region': 'cn-hongkong', 'account_id': str(aliyun_account.id)}), calls)
        self.assertIn(('sync_aws_assets', {'region': '', 'account_id': str(aws_account.id)}), calls)

        detail_request = RequestFactory().get(f'/api/admin/cloud-assets/sync-jobs/{job.id}/')
        self._attach_bearer_session(detail_request, staff_user)
        detail_response = cloud_asset_sync_job_detail(detail_request, job.id)
        detail_payload = json.loads(detail_response.content)['data']
        self.assertEqual(detail_payload['status'], CloudAssetSyncJob.STATUS_SUCCEEDED)
        self.assertEqual(detail_payload['progress_percent'], 100)
        self.assertEqual(detail_payload['current_task'], '同步完成')
        self.assertTrue(detail_payload['events'])
        self.assertEqual({task['provider'] for task in detail_payload['tasks']}, {'aliyun', 'aws'})

        list_request = RequestFactory().get('/api/admin/cloud-assets/sync-jobs/')
        self._attach_bearer_session(list_request, staff_user)
        list_response = cloud_asset_sync_jobs_list(list_request)
        list_payload = json.loads(list_response.content)['data']
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_payload['items'][0]['id'], job.id)

        retry_request = RequestFactory().post(f'/api/admin/cloud-assets/sync-jobs/{job.id}/retry/', data='{}', content_type='application/json')
        retry_request = self._attach_bearer_session(retry_request, staff_user)
        retry_response = retry_cloud_asset_sync_job(retry_request, job.id)
        retry_payload = json.loads(retry_response.content)['data']
        self.assertEqual(retry_response.status_code, 200)
        self.assertTrue(retry_payload['queued'])
        self.assertEqual(CloudAssetSyncJob.objects.get(pk=retry_payload['job_id']).scope['retry_of_job_id'], job.id)
        self.assertTrue(CloudAssetSyncJobEvent.objects.filter(job_id=job.id, event_type=CloudAssetSyncJobEvent.TYPE_RETRY).exists())

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_asset_sync_jobs_metrics_returns_operational_summary(self):
        now = timezone.now()
        staff_user = get_user_model().objects.create_user(username='staff_sync_metrics', password='x', is_staff=True)
        running_job = CloudAssetSyncJob.objects.create(
            run_id='metrics-running-job',
            status=CloudAssetSyncJob.STATUS_RUNNING,
            started_at=now - timezone.timedelta(minutes=30),
            worker_heartbeat_at=now - timezone.timedelta(minutes=20),
            current_task='metrics running',
        )
        failed_job = CloudAssetSyncJob.objects.create(
            run_id='metrics-failed-job',
            status=CloudAssetSyncJob.STATUS_FAILED,
            started_at=now - timezone.timedelta(minutes=10),
            finished_at=now - timezone.timedelta(minutes=5),
            errors=['boom'],
            current_task='metrics failed',
        )
        CloudAssetSyncJobEvent.objects.create(job_id=running_job.id, event_type=CloudAssetSyncJobEvent.TYPE_HEARTBEAT, message='stale heartbeat')
        CloudAssetSyncJobEvent.objects.create(job_id=failed_job.id, event_type=CloudAssetSyncJobEvent.TYPE_ERROR, message='failed')

        request = RequestFactory().get('/api/admin/cloud-assets/sync-jobs/metrics/?window_hours=24')
        self._attach_bearer_session(request, staff_user)
        response = cloud_asset_sync_jobs_metrics(request)
        payload = json.loads(response.content)['data']

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(payload['active_count'], 1)
        self.assertGreaterEqual(payload['failed_count'], 1)
        self.assertGreaterEqual(payload['stale_running_count'], 1)
        self.assertEqual(payload['latest_failed_job']['id'], failed_job.id)
        self.assertGreaterEqual(payload['event_counts'][CloudAssetSyncJobEvent.TYPE_ERROR], 1)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cancel_queued_cloud_asset_sync_job_marks_terminal_and_events(self):
        staff_user = get_user_model().objects.create_user(username='staff_cancel_sync_job', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().post('/api/admin/cloud-assets/sync/', data='{}', content_type='application/json')
        request = self._attach_bearer_session(request, staff_user)
        response = sync_cloud_assets(request)
        payload = json.loads(response.content)['data']
        job = CloudAssetSyncJob.objects.get(pk=payload['job_id'])

        cancel_request = RequestFactory().post(f'/api/admin/cloud-assets/sync-jobs/{job.id}/cancel/', data='{}', content_type='application/json')
        cancel_request = self._attach_bearer_session(cancel_request, staff_user)
        cancel_response = cancel_cloud_asset_sync_job(cancel_request, job.id)
        cancel_payload = json.loads(cancel_response.content)['data']

        self.assertEqual(cancel_response.status_code, 200)
        self.assertTrue(cancel_payload['cancelled'])
        job.refresh_from_db()
        self.assertEqual(job.status, CloudAssetSyncJob.STATUS_CANCELLED)
        self.assertIsNotNone(job.cancel_requested_at)
        self.assertEqual(job.cancel_requested_by, staff_user)
        self.assertTrue(CloudAssetSyncJobEvent.objects.filter(job_id=job.id, event_type=CloudAssetSyncJobEvent.TYPE_CANCEL).exists())

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_cloud_assets_with_selected_assets_uses_asset_scoped_tasks(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-selected-asset-sync',
            external_account_id='acct-selected-asset-sync',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        first_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='ap-southeast-1',
            asset_name='selected-sync-one',
            instance_id='selected-sync-one',
            public_ip='10.88.30.1',
            status=CloudAsset.STATUS_RUNNING,
        )
        second_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='ap-southeast-1',
            asset_name='selected-sync-two',
            instance_id='selected-sync-two',
            public_ip='10.88.30.2',
            status=CloudAsset.STATUS_RUNNING,
        )
        staff_user = get_user_model().objects.create_user(username='staff_sync_selected_assets', password='x', is_staff=True, is_superuser=True)
        calls = []

        # 测试类：组织 AwsCommand 相关的回归测试。
        class AwsCommand:
            synced_regions = ['ap-southeast-1']
            sync_errors = []
            summary = {'updated': 1}

        # 功能：处理 云资产、云订单和生命周期 中的 fake call command 业务流程。
        def fake_call_command(command_name, **kwargs):
            calls.append((command_name, kwargs))
            return AwsCommand(), f'aws asset {kwargs.get("asset_id")} ok\n'

        request = RequestFactory().post(
            '/api/admin/cloud-assets/sync/',
            data=json.dumps({'asset_ids': [first_asset.id, second_asset.id]}),
            content_type='application/json',
        )
        request = self._attach_bearer_session(request, staff_user)
        response = sync_cloud_assets(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)['data']
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['queued'])

        job = CloudAssetSyncJob.objects.get(pk=payload['job_id'])
        with patch('cloud.sync_jobs._call_command_capture_threaded', side_effect=fake_call_command), \
            patch('cloud.sync_jobs._refresh_dashboard_plan_snapshots_deferred'):
            job = _execute_cloud_asset_sync_job(job)

        result = job.result_payload
        self.assertEqual(job.status, CloudAssetSyncJob.STATUS_SUCCEEDED)
        self.assertTrue(result['synced']['aws'])
        self.assertEqual(len(calls), 2)
        self.assertEqual({call[1]['asset_id'] for call in calls}, {str(first_asset.id), str(second_asset.id)})
        self.assertTrue(all(call[0] == 'sync_aws_assets' for call in calls))
        self.assertTrue(all(call[1]['region'] == 'ap-southeast-1' for call in calls))
        self.assertFalse(any('asset_id' not in call[1] for call in calls))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_process_cloud_asset_sync_jobs_worker_processes_queued_job(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='worker-asset-sync',
            external_account_id='acct-worker-asset-sync',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='ap-southeast-1',
            asset_name='worker-sync-asset',
            instance_id='worker-sync-asset',
            public_ip='10.88.31.1',
            status=CloudAsset.STATUS_RUNNING,
        )
        staff_user = get_user_model().objects.create_user(username='staff_worker_sync', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().post(
            '/api/admin/cloud-assets/sync/',
            data=json.dumps({'asset_ids': [asset.id]}),
            content_type='application/json',
        )
        request = self._attach_bearer_session(request, staff_user)
        response = sync_cloud_assets(request)
        payload = json.loads(response.content)['data']

        self.assertTrue(payload['queued'])
        job = CloudAssetSyncJob.objects.get(pk=payload['job_id'])
        self.assertEqual(job.status, CloudAssetSyncJob.STATUS_QUEUED)

        calls = []

        # 测试类：组织 AwsCommand 相关的回归测试。
        class AwsCommand:
            synced_regions = ['ap-southeast-1']
            sync_errors = []

        # 功能：处理 云资产、云订单和生命周期 中的 fake call command 业务流程。
        def fake_call_command(command_name, **kwargs):
            calls.append((command_name, kwargs))
            return AwsCommand(), 'worker job ok\n'

        with patch('cloud.sync_jobs._call_command_capture_threaded', side_effect=fake_call_command), \
            patch('cloud.sync_jobs._refresh_dashboard_plan_snapshots_deferred'), \
            patch('cloud.management.commands.process_cloud_asset_sync_jobs.close_old_connections'):
            call_command('process_cloud_asset_sync_jobs', '--once', '--worker-id', 'test-worker', '--poll-interval', '0.1', '--stale-running-minutes', '0')

        job.refresh_from_db()
        self.assertEqual(job.status, CloudAssetSyncJob.STATUS_SUCCEEDED)
        self.assertEqual(job.progress_current, job.progress_total)
        self.assertEqual(job.current_task, '同步完成')
        self.assertEqual(job.worker_id, 'test-worker')
        self.assertIsNotNone(job.worker_heartbeat_at)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertTrue(CloudAssetSyncJobEvent.objects.filter(job_id=job.id, event_type=CloudAssetSyncJobEvent.TYPE_CLAIMED, worker_id='test-worker').exists())
        self.assertTrue(CloudAssetSyncJobEvent.objects.filter(job_id=job.id, event_type=CloudAssetSyncJobEvent.TYPE_HEARTBEAT, worker_id='test-worker').exists())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'sync_aws_assets')
        self.assertEqual(calls[0][1]['asset_id'], str(asset.id))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        staff_user = get_user_model().objects.create_user(username='staff_asset_sync_one', password='x', is_staff=True, is_superuser=True)
        with patch('cloud.api_sync._call_command_capture', return_value=(object(), None)) as mocked:
            request = RequestFactory().post(f'/api/admin/cloud-assets/{asset.id}/sync/', data='{}', content_type='application/json')
            request = self._attach_bearer_session(request, staff_user)
            response = sync_cloud_asset_status(request, asset.id)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload['data']['ok'])
        self.assertEqual(payload['data']['asset']['id'], asset.id)
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.args[0], 'sync_aws_assets')
        self.assertEqual(mocked.call_args.kwargs['account_id'], str(account.id))
        self.assertEqual(mocked.call_args.kwargs['region'], 'ap-southeast-1')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_retained_ip_asset_uses_order_account_and_static_ip_scope(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='single-retained-ip-sync',
            external_account_id='acct-single-retained-ip-sync',
            access_key='ak',
            secret_key='sk',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        order = CloudServerOrder.objects.create(
            order_no='SINGLE-RETAINED-IP-SYNC-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleted',
            public_ip='3.3.3.44',
            previous_public_ip='3.3.3.44',
            static_ip_name='StaticIp-single-retained-sync',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=10),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='stale-deleted-instance-name',
            public_ip='3.3.3.44',
            previous_public_ip='3.3.3.44',
            actual_expires_at=order.ip_recycle_at,
            status=CloudAsset.STATUS_DELETED,
            provider_status='固定IP保留中-实例已删除',
            is_active=False,
        )
        staff_user = get_user_model().objects.create_user(username='staff_retained_asset_sync_one', password='x', is_staff=True, is_superuser=True)
        with patch('cloud.api_sync._call_command_capture', return_value=(object(), None)) as mocked:
            request = RequestFactory().post(f'/api/admin/cloud-assets/{asset.id}/sync/', data='{}', content_type='application/json')
            request = self._attach_bearer_session(request, staff_user)
            response = sync_cloud_asset_status(request, asset.id)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)['data']
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['scope']['instance_id'], 'StaticIp-single-retained-sync')
        self.assertEqual(payload['scope']['public_ip'], '3.3.3.44')
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.args[0], 'sync_aws_assets')
        self.assertEqual(mocked.call_args.kwargs['account_id'], str(account.id))
        self.assertEqual(mocked.call_args.kwargs['instance_id'], 'StaticIp-single-retained-sync')
        self.assertEqual(mocked.call_args.kwargs['public_ip'], '3.3.3.44')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_proxy_asset_ip_query_exposes_manual_expiry_for_admin_and_user(self):
        expires_at = timezone.now() + timezone.timedelta(days=12)
        visible_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='manual-query-visible',
            public_ip='3.3.3.33',
            actual_expires_at=expires_at,
            price='19.00',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        other_user = TelegramUser.objects.create(tg_user_id=990002, username='other_query_user')

        admin_asset = async_to_sync(get_proxy_asset_by_ip_for_admin)('3.3.3.33')
        user_asset = async_to_sync(get_proxy_asset_by_ip_for_user)('3.3.3.33', self.user.id)
        hidden_asset = async_to_sync(get_proxy_asset_by_ip_for_user)('3.3.3.33', other_user.id)

        self.assertEqual(admin_asset.id, visible_asset.id)
        self.assertEqual(user_asset.id, visible_asset.id)
        self.assertEqual(admin_asset.actual_expires_at, expires_at)
        self.assertEqual(user_asset.actual_expires_at, expires_at)
        self.assertIsNone(hidden_asset)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_proxy_asset_ip_query_skips_cloud_missing_asset(self):
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='manual-query-missing',
            public_ip='3.3.3.34',
            actual_expires_at=timezone.now() + timezone.timedelta(days=12),
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='云上未找到实例/IP-待确认',
            note='未附加固定IP；IP校验发现云上不存在，已标记删除',
        )
        visible_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='manual-query-visible-fallback',
            public_ip='3.3.3.34',
            actual_expires_at=timezone.now() + timezone.timedelta(days=15),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='运行中',
        )

        admin_asset = async_to_sync(get_proxy_asset_by_ip_for_admin)('3.3.3.34')

        self.assertEqual(admin_asset.id, visible_asset.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_server_ip_query_requires_owner_identity(self):
        other_user = TelegramUser.objects.create(tg_user_id=990003, username='other_order_query_user')
        expires_at = timezone.now() + timezone.timedelta(days=10)
        order = CloudServerOrder.objects.create(
            order_no='IP-OWNER-QUERY-1',
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
            public_ip='4.4.4.44',
        )
        self._attach_order_expiry_asset(order, expires_at)

        owned_order = async_to_sync(get_cloud_server_by_ip_for_user)('4.4.4.44', self.user.id)
        hidden_order = async_to_sync(get_cloud_server_by_ip_for_user)('4.4.4.44', other_user.id)

        self.assertEqual(owned_order.id, order.id)
        self.assertIsNone(hidden_order)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_server_public_renewal_allows_stranger_payment_entry(self):
        other_user = TelegramUser.objects.create(tg_user_id=990004, username='other_order_renew_user')
        expires_at = timezone.now() + timezone.timedelta(days=10)
        order = CloudServerOrder.objects.create(
            order_no='IP-OWNER-RENEW-1',
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
            public_ip='4.4.4.45',
            service_started_at=timezone.now() - timezone.timedelta(days=20),
        )
        self._attach_order_expiry_asset(order, expires_at)

        user_scoped = async_to_sync(create_cloud_server_renewal_for_user)(order.id, other_user.id, 31)
        public_renewal = async_to_sync(create_cloud_server_renewal_by_public_query)(order.id, 31)

        self.assertIsNone(user_scoped)
        self.assertIsNotNone(public_renewal)
        self.assertEqual(public_renewal.user_id, self.user.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_public_unattached_asset_renewal_plans_are_available(self):
        other_user = TelegramUser.objects.create(tg_user_id=990006, username='other_unattached_asset_renew_user')
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='strict-unattached-account',
            external_account_id='acct-strict-unattached',
            access_key='ak',
            secret_key='sk',
            region_hint=self.plan.region_code,
            is_active=True,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label='aws+acct-strict-unattached+strict-unattached-account',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='public-unattached-asset-renewal',
            public_ip='4.4.4.47',
            actual_expires_at=timezone.now() + timezone.timedelta(days=15),
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:test:StaticIp/public-unattached-asset-renewal',
        )

        denied_asset, denied_plans, denied_err = async_to_sync(list_cloud_asset_renewal_plans)(asset.id, other_user.id)
        public_asset, public_plans, public_err = async_to_sync(list_cloud_asset_renewal_plans)(asset.id, other_user.id, public=True)

        self.assertIsNone(denied_asset)
        self.assertEqual(denied_plans, [])
        self.assertEqual(denied_err, '代理记录不存在')
        self.assertEqual(public_asset.id, asset.id)
        self.assertGreaterEqual(len(public_plans), 1)
        self.assertIsNone(public_err)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_retained_deleted_asset_renewal_plans_are_available_by_asset_button(self):
        now = timezone.now()
        order = CloudServerOrder.objects.create(
            order_no='RETAINED-ASSET-BUTTON-1',
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
            public_ip='4.4.4.49',
            previous_public_ip='4.4.4.49',
            instance_id='',
            static_ip_name='retained-asset-button-ip',
            ip_recycle_at=now + timezone.timedelta(days=10),
            service_started_at=now - timezone.timedelta(days=40),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='retained-asset-button-ip',
            public_ip='4.4.4.49',
            previous_public_ip='4.4.4.49',
            actual_expires_at=order.ip_recycle_at,
            status=CloudAsset.STATUS_DELETED,
            is_active=False,
            provider_status='固定IP保留中-实例已删除',
            note='实例删除后固定IP保留中',
        )

        detail = async_to_sync(get_user_proxy_asset_detail)(asset.id, self.user.id, 'asset')
        retained_order, plans, err = async_to_sync(list_retained_ip_renewal_plans_by_asset)(asset.id, self.user.id)

        self.assertIsNone(detail)
        self.assertEqual(retained_order.id, order.id)
        self.assertGreaterEqual(len(plans), 1)
        self.assertIsNone(err)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_retained_deleted_asset_renewal_plans_allow_same_group_visibility(self):
        now = timezone.now()
        member = TelegramUser.objects.create(tg_user_id=990021, username='retained_group_member')
        stranger = TelegramUser.objects.create(tg_user_id=990022, username='retained_group_stranger')
        group = TelegramGroupFilter.objects.create(chat_id=-1001887421, title='Retained Shared Group', enabled=True)
        other_group = TelegramGroupFilter.objects.create(chat_id=-1001887422, title='Retained Other Group', enabled=True)
        retained_order = CloudServerOrder.objects.create(
            order_no='RETAINED-GROUP-ASSET-1',
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
            public_ip='4.4.4.59',
            previous_public_ip='4.4.4.59',
            instance_id='',
            static_ip_name='retained-group-asset-ip',
            ip_recycle_at=now + timezone.timedelta(days=10),
            service_started_at=now - timezone.timedelta(days=40),
        )
        retained_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=retained_order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='retained-group-asset-ip',
            public_ip='4.4.4.59',
            previous_public_ip='4.4.4.59',
            actual_expires_at=retained_order.ip_recycle_at,
            status=CloudAsset.STATUS_DELETED,
            is_active=False,
            provider_status='固定IP保留中-实例已删除',
            note='实例删除后固定IP保留中',
            telegram_group=group,
        )
        member_expires_at = now + timezone.timedelta(days=5)
        member_order = CloudServerOrder.objects.create(
            order_no='RETAINED-GROUP-MEMBER-1',
            user=member,
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
            public_ip='4.4.4.60',
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=member_order,
            user=member,
            provider=member_order.provider,
            region_code=member_order.region_code,
            region_name=member_order.region_name,
            asset_name='retained-group-member',
            public_ip='4.4.4.60',
            actual_expires_at=member_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            telegram_group=group,
        )

        private_order, private_plans, private_err = async_to_sync(list_retained_ip_renewal_plans_by_asset)(retained_asset.id, member.id)
        group_order, group_plans, group_err = async_to_sync(list_retained_ip_renewal_plans_by_asset)(retained_asset.id, stranger.id, group_chat_id=group.chat_id)
        stranger_order, stranger_plans, stranger_err = async_to_sync(list_retained_ip_renewal_plans_by_asset)(retained_asset.id, stranger.id)
        wrong_group_order, wrong_group_plans, wrong_group_err = async_to_sync(list_retained_ip_renewal_plans_by_asset)(retained_asset.id, stranger.id, group_chat_id=other_group.chat_id)
        group_visible = async_to_sync(is_retained_ip_order_visible_in_group)(retained_order.id, group.chat_id)
        wrong_group_visible = async_to_sync(is_retained_ip_order_visible_in_group)(retained_order.id, other_group.chat_id)
        group_items = async_to_sync(list_group_cloud_servers)(group.chat_id)

        self.assertEqual(private_order.id, retained_order.id)
        self.assertGreaterEqual(len(private_plans), 1)
        self.assertIsNone(private_err)
        self.assertEqual(group_order.id, retained_order.id)
        self.assertGreaterEqual(len(group_plans), 1)
        self.assertIsNone(group_err)
        self.assertIsNone(stranger_order)
        self.assertEqual(stranger_plans, [])
        self.assertIsNone(stranger_err)
        self.assertIsNone(wrong_group_order)
        self.assertEqual(wrong_group_plans, [])
        self.assertIsNone(wrong_group_err)
        self.assertTrue(group_visible)
        self.assertFalse(wrong_group_visible)
        self.assertFalse(any(getattr(item, 'asset_id', None) == retained_asset.id for item in group_items))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_public_unattached_asset_renewal_requires_original_account(self):
        other_user = TelegramUser.objects.create(tg_user_id=990007, username='other_unattached_asset_no_account')
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='public-unattached-asset-no-account',
            public_ip='4.4.4.48',
            actual_expires_at=timezone.now() + timezone.timedelta(days=15),
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:test:StaticIp/public-unattached-asset-no-account',
        )

        public_asset, public_plans, public_err = async_to_sync(list_cloud_asset_renewal_plans)(asset.id, other_user.id, public=True)

        self.assertEqual(public_asset.id, asset.id)
        self.assertEqual(public_plans, [])
        self.assertEqual(public_err, '原固定 IP 所属云账号不可用，暂时无法自助续费，请联系人工客服。')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_asset_recovery_candidates_only_original_account(self):
        other_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='other-strict-unattached-account',
            external_account_id='acct-other-strict-unattached',
            access_key='ak',
            secret_key='sk',
            region_hint=self.plan.region_code,
            is_active=True,
        )
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='source-strict-unattached-account',
            external_account_id='acct-source-strict-unattached',
            access_key='ak',
            secret_key='sk',
            region_hint=self.plan.region_code,
            is_active=True,
        )
        order = CloudServerOrder.objects.create(
            order_no='ASSET-RECOVERY-STRICT-ACCOUNT',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            account_label='aws+acct-source-strict-unattached+source-strict-unattached-account',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='paid',
            public_ip='4.4.4.49',
            static_ip_name='strict-static-ip',
            provision_note='未绑定代理资产续费：来源资产 #999。',
        )

        account_ids = async_to_sync(_candidate_cloud_account_ids)(order.id)

        self.assertEqual(account_ids, [account.id])
        self.assertNotIn(other_account.id, account_ids)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_proxy_link_query_extracts_server_ip_only(self):
        from bot.handlers import _extract_proxy_links_by_ip, _extract_query_ips

        raw = 'https://t.me/proxy?server=3.0.162.212&port=443&secret=ee78fbdf52d2713cced14f283718ab6917617a7572652e6d6963726f736f66742e636f6d'

        self.assertEqual(_extract_query_ips(raw), ['3.0.162.212'])
        self.assertEqual(_extract_proxy_links_by_ip(raw)['3.0.162.212']['port'], '443')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_tg_proxy_link_query_extracts_server_ip_only(self):
        from bot.handlers import _extract_query_ips

        raw = 'tg://proxy?server=3.0.162.213&port=443&secret=abc'

        self.assertEqual(_extract_query_ips(raw), ['3.0.162.213'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_ip_query_displays_matched_asset_ip_not_order_ip(self):
        order_expires_at = timezone.now() + timezone.timedelta(days=10)
        order = CloudServerOrder.objects.create(
            order_no='IP-MATCH-ASSET-ORDER-1',
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
            public_ip='54.151.227.23',
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='ip-match-asset-order-1',
            public_ip='3.0.162.212',
            actual_expires_at=timezone.now() + timezone.timedelta(days=15),
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
        )
        self._attach_order_expiry_asset(order, order_expires_at)

        result = async_to_sync(get_cloud_server_by_ip)('3.0.162.212')

        self.assertEqual(result.matched_query_ip, '3.0.162.212')
        self.assertEqual(result.public_ip, '3.0.162.212')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_ip_query_displays_matched_previous_ip_not_order_ip(self):
        order_expires_at = timezone.now() + timezone.timedelta(days=10)
        order = CloudServerOrder.objects.create(
            order_no='IP-MATCH-PREVIOUS-ORDER-1',
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
            public_ip='54.151.227.24',
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='ip-match-previous-order-1',
            previous_public_ip='3.0.162.213',
            actual_expires_at=timezone.now() + timezone.timedelta(days=15),
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
        )
        self._attach_order_expiry_asset(order, order_expires_at)

        result = async_to_sync(get_cloud_server_by_ip)('3.0.162.213')

        self.assertEqual(result.matched_query_ip, '3.0.162.213')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_server_ip_change_requires_owner_identity(self):
        other_user = TelegramUser.objects.create(tg_user_id=990005, username='other_order_ip_change_user')
        expires_at = timezone.now() + timezone.timedelta(days=10)
        order = CloudServerOrder.objects.create(
            order_no='IP-OWNER-CHANGE-1',
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
            public_ip='4.4.4.46',
            mtproxy_port=9528,
            mtproxy_secret='abcdef',
            ip_change_quota=1,
        )
        self._attach_order_expiry_asset(order, expires_at)

        denied = async_to_sync(mark_cloud_server_ip_change_requested)(order.id, other_user.id, self.plan.region_code, 9528)
        allowed = async_to_sync(mark_cloud_server_ip_change_requested)(order.id, self.user.id, self.plan.region_code, 9528)

        self.assertIsNone(denied)
        self.assertIsNotNone(allowed)
        self.assertEqual(allowed.user_id, self.user.id)
        self.assertEqual(allowed.replacement_for_id, order.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_excludes_cloud_missing_orphan_server(self):
        missing_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='missing-orphan-server-plan',
            public_ip='3.3.3.35',
            instance_id='i-missing-orphan-server-plan',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='云上未找到实例/IP-待确认',
            note='服务器校验发现云上不存在，已标记删除',
        )
        visible_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='visible-orphan-server-plan',
            public_ip='3.3.3.36',
            instance_id='i-visible-orphan-server-plan',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='运行中',
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_plan_missing', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': 1})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        due_ids = {item.get('asset_id') for item in data['shutdown_plan_items']}

        self.assertNotIn(missing_asset.id, due_ids)
        self.assertIn(visible_asset.id, due_ids)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_keeps_asset_remarks_out_of_execution_status(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='orphan-plan-note-columns',
            instance_id='i-orphan-plan-note-columns',
            public_ip='3.3.3.37',
            actual_expires_at=timezone.now() - timezone.timedelta(days=7),
            status=CloudAsset.STATUS_STOPPED,
            is_active=True,
            provider_status='运行中',
            note='人工备注：这是一段很长的业务备注，不应该侵占执行状态列。\nGet: apt noise\ntg://proxy?server=1.1.1.1&port=9528&secret=x',
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_plan_columns', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': 1})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        row = next(item for item in data['shutdown_plan_items'] if item.get('asset_id') == asset.id)

        self.assertEqual(row['execution_status'], '无订单同步资产已到期，待执行删除服务器')
        self.assertEqual(row['execution_plan'][:5], '删除服务器')
        self.assertEqual(row['resource_state_label'], '实例仍存在')
        self.assertEqual(row['plan_state_label'], '待执行')
        self.assertTrue(row['should_execute'])
        self.assertIn('人工备注', row['display_note'])
        self.assertNotIn('tg://proxy?', row['display_note'])
        self.assertNotIn('Get:', row['display_note'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_ignore_account_shutdown_disabled_plan_state(self):
        SiteConfig.set('cloud_server_delete_enabled', '1')
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='shutdown-disabled-plan-state',
            external_account_id='acct-shutdown-disabled-plan-state',
            access_key='ak',
            secret_key='sk',
            region_hint=self.plan.region_code,
            shutdown_enabled=False,
        )
        expires_at = timezone.now() - timezone.timedelta(days=3)
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-SHUTDOWN-DISABLED-STATE-1',
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
            status='suspended',
            public_ip='3.3.3.38',
            suspend_at=timezone.now() - timezone.timedelta(days=2),
            delete_at=timezone.now() - timezone.timedelta(hours=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='shutdown-disabled-plan-state-asset',
            instance_id='shutdown-disabled-plan-state-asset',
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_shutdown_disabled_state', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        row = next(item for item in data['server_delete_items'] if item.get('order_id') == order.id)

        self.assertTrue(row['shutdown_enabled'])
        self.assertNotEqual(row['queue_status'], 'shutdown_disabled')
        self.assertNotEqual(row['plan_state'], 'shutdown_disabled')
        self.assertTrue(row['should_execute'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_show_asset_shutdown_disabled_plan_state(self):
        expires_at = timezone.now() - timezone.timedelta(days=3)
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-ASSET-SHUTDOWN-DISABLED-1',
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
            public_ip='3.3.3.39',
            suspend_at=timezone.now() - timezone.timedelta(days=2),
            delete_at=timezone.now() - timezone.timedelta(hours=1),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='asset-shutdown-disabled-plan-state',
            instance_id='asset-shutdown-disabled-plan-state',
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            shutdown_enabled=False,
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_shutdown_disabled_state', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        row = next(item for item in data['shutdown_plan_items'] if item.get('asset_id') == asset.id)

        self.assertFalse(row['shutdown_enabled'])
        self.assertEqual(row['queue_status'], 'shutdown_disabled')
        self.assertEqual(row['plan_state'], 'shutdown_disabled')
        self.assertEqual(row['plan_state_label'], '关机开关关闭')
        self.assertFalse(row['should_execute'])
        self.assertIn('关机计划开关关闭', row['blocked_reason'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_route_linked_asset_delete_to_order_item(self):
        expires_at = timezone.now() - timezone.timedelta(days=5)
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-LINKED-ASSET-ORDER-ROUTE-1',
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
            status='deleting',
            public_ip='3.3.3.41',
            suspend_at=timezone.now() - timezone.timedelta(days=2),
            delete_at=timezone.now() - timezone.timedelta(hours=1),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='linked-asset-order-route',
            instance_id='linked-asset-order-route',
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_linked_asset_order_route', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        row = next(item for item in data['shutdown_plan_items'] if item.get('asset_id') == asset.id)

        self.assertEqual(row['item_type'], 'order')
        self.assertEqual(row['order_id'], order.id)
        self.assertEqual(row['asset_id'], asset.id)
        self.assertTrue(row['shutdown_enabled'])
        self.assertEqual(row['order_detail_path'], f'/admin/cloud-orders/{order.id}')
        self.assertEqual(row['asset_detail_path'], f'/admin/cloud-assets/{asset.id}')

    # 功能：验证关机计划完成后才进入服务器删除计划；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_split_shutdown_before_server_delete(self):
        expires_at = timezone.now() - timezone.timedelta(days=5)
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-SPLIT-SHUTDOWN-DELETE-1',
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
            public_ip='3.3.3.42',
            suspend_at=timezone.now() - timezone.timedelta(days=4),
            delete_at=timezone.now() - timezone.timedelta(days=1),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='split-shutdown-before-delete',
            instance_id='split-shutdown-before-delete',
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_split_plan', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']

        self.assertTrue(any(item.get('asset_id') == asset.id for item in data['shutdown_plan_items']))
        self.assertFalse(any(item.get('asset_id') == asset.id for item in data['server_delete_items']))

        order.status = 'suspended'
        order.save(update_fields=['status', 'updated_at'])
        asset.status = CloudAsset.STATUS_STOPPED
        asset.save(update_fields=['status', 'updated_at'])
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']

        self.assertFalse(any(item.get('asset_id') == asset.id for item in data['shutdown_plan_items']))
        self.assertTrue(any(item.get('asset_id') == asset.id for item in data['server_delete_items']))

    # 功能：验证三个资产单项开关分别作用于关机、服务器删除和 IP 删除计划；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_use_stage_specific_asset_switches(self):
        SiteConfig.set('cloud_server_shutdown_enabled', '1')
        SiteConfig.set('cloud_server_delete_enabled', '1')
        SiteConfig.set('cloud_ip_delete_enabled', '1')
        expires_at = timezone.now() - timezone.timedelta(days=5)
        shutdown_order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-SWITCH-SHUTDOWN-1',
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
            public_ip='3.3.3.43',
            suspend_at=timezone.now() - timezone.timedelta(days=4),
            delete_at=timezone.now() - timezone.timedelta(days=1),
        )
        shutdown_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=shutdown_order,
            user=self.user,
            provider=shutdown_order.provider,
            region_code=shutdown_order.region_code,
            region_name=shutdown_order.region_name,
            asset_name='stage-switch-shutdown',
            instance_id='stage-switch-shutdown',
            public_ip=shutdown_order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            shutdown_enabled=False,
            server_delete_enabled=True,
            ip_delete_enabled=True,
            is_active=True,
        )
        delete_order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-SWITCH-DELETE-1',
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
            status='suspended',
            public_ip='3.3.3.44',
            suspend_at=timezone.now() - timezone.timedelta(days=4),
            delete_at=timezone.now() - timezone.timedelta(days=1),
        )
        delete_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=delete_order,
            user=self.user,
            provider=delete_order.provider,
            region_code=delete_order.region_code,
            region_name=delete_order.region_name,
            asset_name='stage-switch-delete',
            instance_id='stage-switch-delete',
            public_ip=delete_order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_STOPPED,
            shutdown_enabled=False,
            server_delete_enabled=False,
            ip_delete_enabled=True,
            is_active=True,
        )
        ip_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='stage-switch-ip',
            public_ip='3.3.3.45',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            shutdown_enabled=True,
            server_delete_enabled=True,
            ip_delete_enabled=False,
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_stage_switches', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        shutdown_row = next(item for item in data['shutdown_plan_items'] if item.get('asset_id') == shutdown_asset.id)
        delete_row = next(item for item in data['server_delete_items'] if item.get('asset_id') == delete_asset.id)
        ip_row = next(item for item in data['ip_delete_plan_items'] if item.get('asset_id') == ip_asset.id)

        self.assertEqual(shutdown_row['queue_status'], 'shutdown_disabled')
        self.assertEqual(delete_row['queue_status'], 'server_delete_disabled')
        self.assertEqual(ip_row['queue_status'], 'ip_delete_disabled')
        self.assertFalse(shutdown_row['shutdown_enabled'])
        self.assertFalse(delete_row['server_delete_enabled'])
        self.assertFalse(ip_row['ip_delete_enabled'])

    # 功能：验证未附加 IP 已有到期时间时，切换 IP 删除开关不会刷新到期时间且计划页仍显示关闭行；当前函数属于 云资产后台 API 和生命周期。
    def test_unattached_ip_switch_preserves_existing_expiry_and_plan_row(self):
        SiteConfig.set('cloud_ip_delete_enabled', '1')
        expires_at = (timezone.now() + timezone.timedelta(days=3)).replace(microsecond=0)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='stage-switch-ip-existing-expiry',
            instance_id='',
            public_ip='3.3.3.46',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=expires_at,
            ip_delete_enabled=True,
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(
            username='staff_ip_switch_existing_expiry',
            password='x',
            is_staff=True,
            is_superuser=True,
        )
        request = RequestFactory().patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({'ip_delete_enabled': False}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        asset.refresh_from_db()
        self.assertFalse(asset.ip_delete_enabled)
        self.assertEqual(asset.actual_expires_at, expires_at)

        plan_request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        self._attach_bearer_session(plan_request, staff_user)
        plan_response = lifecycle_plans(plan_request)
        data = json.loads(plan_response.content)['data']
        ip_row = next(item for item in data['ip_delete_plan_items'] if item.get('asset_id') == asset.id)

        self.assertEqual(ip_row['queue_status'], 'ip_delete_disabled')
        self.assertFalse(ip_row['ip_delete_enabled'])
        self.assertEqual(parse_datetime(ip_row['actual_expires_at']), expires_at)

    def test_lifecycle_plans_show_global_stage_switches(self):
        SiteConfig.set('cloud_server_shutdown_enabled', '0')
        SiteConfig.set('cloud_server_delete_enabled', '0')
        SiteConfig.set('cloud_ip_delete_enabled', '0')
        expires_at = timezone.now() - timezone.timedelta(days=5)
        shutdown_order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-GLOBAL-SHUTDOWN-1',
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
            public_ip='3.3.3.53',
            suspend_at=timezone.now() - timezone.timedelta(days=4),
            delete_at=timezone.now() - timezone.timedelta(days=1),
        )
        shutdown_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=shutdown_order,
            user=self.user,
            provider=shutdown_order.provider,
            region_code=shutdown_order.region_code,
            region_name=shutdown_order.region_name,
            asset_name='global-stage-switch-shutdown',
            instance_id='global-stage-switch-shutdown',
            public_ip=shutdown_order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            shutdown_enabled=True,
            server_delete_enabled=True,
            ip_delete_enabled=True,
            is_active=True,
        )
        delete_order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-GLOBAL-DELETE-1',
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
            status='suspended',
            public_ip='3.3.3.54',
            suspend_at=timezone.now() - timezone.timedelta(days=4),
            delete_at=timezone.now() - timezone.timedelta(days=1),
        )
        delete_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=delete_order,
            user=self.user,
            provider=delete_order.provider,
            region_code=delete_order.region_code,
            region_name=delete_order.region_name,
            asset_name='global-stage-switch-delete',
            instance_id='global-stage-switch-delete',
            public_ip=delete_order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_STOPPED,
            shutdown_enabled=True,
            server_delete_enabled=True,
            ip_delete_enabled=True,
            is_active=True,
        )
        ip_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='global-stage-switch-ip',
            public_ip='3.3.3.55',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            shutdown_enabled=True,
            server_delete_enabled=True,
            ip_delete_enabled=True,
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_global_stage_switches', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        shutdown_row = next(item for item in data['shutdown_plan_items'] if item.get('asset_id') == shutdown_asset.id)
        delete_row = next(item for item in data['server_delete_items'] if item.get('asset_id') == delete_asset.id)
        ip_row = next(item for item in data['ip_delete_plan_items'] if item.get('asset_id') == ip_asset.id)

        self.assertEqual(shutdown_row['queue_status'], 'global_shutdown_disabled')
        self.assertEqual(shutdown_row['plan_state'], 'global_shutdown_disabled')
        self.assertEqual(shutdown_row['plan_state_label'], '总开关关闭')
        self.assertFalse(shutdown_row['should_execute'])
        self.assertIn('服务器关机总开关关闭', shutdown_row['blocked_reason'])

        self.assertEqual(delete_row['queue_status'], 'global_server_delete_disabled')
        self.assertEqual(delete_row['plan_state'], 'global_server_delete_disabled')
        self.assertEqual(delete_row['plan_state_label'], '总开关关闭')
        self.assertFalse(delete_row['should_execute'])
        self.assertIn('服务器删除总开关关闭', delete_row['blocked_reason'])

        self.assertEqual(ip_row['queue_status'], 'global_ip_delete_disabled')
        self.assertEqual(ip_row['plan_state'], 'global_ip_delete_disabled')
        self.assertEqual(ip_row['plan_state_label'], '总开关关闭')
        self.assertFalse(ip_row['should_execute'])
        self.assertIn('IP 删除总开关关闭', ip_row['blocked_reason'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_move_deleted_orphan_server_out_of_future(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='deleted-orphan-should-not-stay-future',
            public_ip='3.3.3.88',
            instance_id='i-deleted-orphan-should-not-stay-future',
            actual_expires_at=timezone.now() + timezone.timedelta(days=3),
            status=CloudAsset.STATUS_RUNNING,
            is_active=False,
            provider_status='运行中',
            note='无订单 AWS 资产到期，已执行真实删机。 状态: 固定IP仍存在但未附加；公网IP: 3.3.3.88；计划释放时间: 2026-05-24 18:00:00',
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_plan_deleted_orphan', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']

        server_delete_asset_ids = {item.get('asset_id') for item in data['server_delete_items']}
        self.assertNotIn(asset.id, server_delete_asset_ids)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_manual_order_delete_enters_lifecycle_success_history(self):
        from bot.api import _run_shutdown_order_sync

        expires_at = timezone.now() - timezone.timedelta(days=1)
        order = CloudServerOrder.objects.create(
            order_no='MANUAL-DELETE-LIFECYCLE-HISTORY-1',
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
            status='deleting',
            public_ip='52.77.18.247',
            previous_public_ip='52.77.18.247',
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='manual-delete-lifecycle-history-asset',
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_DELETING,
            is_active=True,
        )
        with patch('cloud.lifecycle.cloud_server_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._delete_instance', new=AsyncMock(return_value=(True, 'manual lifecycle delete ok'))):
            result = _run_shutdown_order_sync(order.id, enforce_schedule=False)

        self.assertTrue(result['ok'])
        staff_user = get_user_model().objects.create_user(username='staff_manual_delete_lifecycle_history', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        self.assertNotIn('history_items', data)
        ip_delete_rows = [
            item for item in data['ip_delete_plan_items']
            if item.get('order_id') == order.id or item.get('public_ip') == '52.77.18.247'
        ]
        self.assertFalse(ip_delete_rows)

    # 功能：验证删机成功且保留固定 IP 后，计划页会从删机阶段切换到 IP 删除阶段；当前函数属于 云资产、云订单和生命周期。
    def test_manual_order_delete_with_retained_ip_moves_into_ip_delete_plan(self):
        from bot.api import _run_shutdown_order_sync

        SiteConfig.set('cloud_ip_delete_enabled', '1')
        now = timezone.now()
        order = CloudServerOrder.objects.create(
            order_no='MANUAL-DELETE-RETAINED-IP-PLAN-1',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleting',
            public_ip='52.77.18.248',
            previous_public_ip='52.77.18.248',
            static_ip_name='StaticIp-manual-delete-retained-plan',
            instance_id='manual-delete-retained-plan-instance',
            provider_resource_id='manual-delete-retained-plan-instance',
            delete_at=now - timezone.timedelta(minutes=1),
            ip_recycle_at=now + timezone.timedelta(days=3),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='manual-delete-retained-plan-instance',
            instance_id='manual-delete-retained-plan-instance',
            provider_resource_id='manual-delete-retained-plan-instance',
            public_ip='52.77.18.248',
            previous_public_ip='52.77.18.248',
            actual_expires_at=order.ip_recycle_at,
            status=CloudAsset.STATUS_DELETING,
            is_active=True,
        )
        with patch('cloud.lifecycle.cloud_server_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._delete_instance', new=AsyncMock(return_value=(True, 'manual lifecycle retained delete ok'))):
            result = _run_shutdown_order_sync(order.id, enforce_schedule=False)

        self.assertTrue(result['ok'])
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.status, 'deleted')
        self.assertTrue(order.ip_recycle_at)
        self.assertEqual(asset.provider_status, '固定IP保留中-实例已删除')
        self.assertEqual(asset.actual_expires_at, order.ip_recycle_at)

        staff_user = get_user_model().objects.create_user(
            username='staff_manual_delete_retained_ip_plan',
            password='x',
            is_staff=True,
        )
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']

        server_delete_rows = [
            item for item in data['server_delete_items']
            if item.get('order_id') == order.id or item.get('asset_id') == asset.id
        ]
        ip_delete_rows = [
            item for item in data['ip_delete_plan_items']
            if item.get('order_id') == order.id or item.get('asset_id') == asset.id
        ]

        self.assertFalse(server_delete_rows)
        self.assertEqual(len(ip_delete_rows), 1)
        self.assertEqual(ip_delete_rows[0]['plan_state'], 'scheduled')
        self.assertEqual(ip_delete_rows[0]['resource_state'], 'fixed_ip_unattached')
        self.assertEqual(parse_datetime(ip_delete_rows[0]['actual_expires_at']), order.ip_recycle_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_compact_request_keeps_ip_delete_history_item(self):
        now = timezone.now()
        for index in range(60):
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=self.user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'compact-active-ip-{index}',
                public_ip=f'10.0.0.{index}',
                provider_status='未附加固定IP',
                instance_id='',
                actual_expires_at=now + timezone.timedelta(days=10),
                is_active=True,
            )

        history_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='compact-ip-history-visible',
            previous_public_ip='52.77.18.250',
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
        )
        record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_DELETED,
            asset=history_asset,
            previous_public_ip='52.77.18.250',
            public_ip=None,
            note='人工手动删除；执行内容：固定 IP 已释放；IP校验发现云上不存在，已标记删除',
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_ip_history_compact', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'compact': 1, 'limit': 50})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']

        self.assertGreaterEqual(data['ip_delete_history_count'], 1)
        self.assertTrue(any(item.get('is_history') and item.get('public_ip') == '52.77.18.250' for item in data['ip_delete_history_items']))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_include_ip_delete_history_item(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='lifecycle-ip-history-visible',
            previous_public_ip='52.77.18.248',
            status=CloudAsset.STATUS_DELETED,
            provider_status='未附加固定IP-已释放',
            is_active=False,
        )
        record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_RECYCLED,
            asset=asset,
            previous_public_ip='52.77.18.248',
            public_ip=None,
            note='人工手动删除；执行内容：释放固定IP成功',
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_ip_history_visible', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        rows = [item for item in data['ip_delete_history_items'] if item.get('is_history') and item.get('public_ip') == '52.77.18.248']

        self.assertTrue(rows)
        self.assertGreaterEqual(data['ip_delete_history_count'], 1)
        self.assertEqual(rows[0]['deletion_source_label'], '人工手动删除')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_include_real_released_retained_ip_history_without_active_row(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='lifecycle-retained-ip-real-release-history',
            previous_public_ip='52.77.18.249',
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
        )
        record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_RECYCLED,
            asset=asset,
            previous_public_ip='52.77.18.249',
            public_ip=None,
            note='固定 IP 保留期结束，AWS 固定 IP 已真实释放：StaticIp-real-release-history',
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_ip_real_release_history', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'refresh': 1, 'limit': 1000})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        rows = [item for item in data['ip_delete_history_items'] if item.get('is_history') and item.get('public_ip') == '52.77.18.249']

        self.assertTrue(rows)
        self.assertGreaterEqual(data['ip_delete_history_count'], 1)

    # 功能：验证 IP 删除计划和 IP 删除历史记录在接口字段中严格分离。
    def test_lifecycle_plans_separate_ip_delete_plan_and_history_items(self):
        active_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='separate-active-unattached-ip',
            public_ip='52.77.18.251',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            is_active=True,
        )
        history_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='separate-history-unattached-ip',
            previous_public_ip='52.77.18.252',
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
        )
        record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_RECYCLED,
            asset=history_asset,
            previous_public_ip='52.77.18.252',
            public_ip=None,
            note='固定 IP 保留期结束，AWS 固定 IP 已真实释放',
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_ip_split_contract', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'refresh': 1, 'limit': 1000})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        plan_rows = data['ip_delete_plan_items']
        history_rows = data['ip_delete_history_items']

        self.assertTrue(any(item.get('asset_id') == active_asset.id for item in plan_rows))
        self.assertFalse(any(item.get('asset_id') == active_asset.id for item in history_rows))
        self.assertTrue(any(item.get('asset_id') == history_asset.id and item.get('is_history') for item in history_rows))
        self.assertFalse(any(item.get('asset_id') == history_asset.id for item in plan_rows))
        self.assertNotIn('ip_delete_items', data)
        self.assertGreaterEqual(data['ip_delete_count'], len(plan_rows))
        self.assertGreaterEqual(data['ip_delete_history_count'], len(history_rows))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_sort_shutdown_items_by_delete_time(self):
        later_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='sort-delete-plan-later',
            instance_id='sort-delete-plan-later',
            public_ip='5.5.5.61',
            status=CloudAsset.STATUS_STOPPED,
            is_active=True,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        earlier_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='sort-delete-plan-earlier',
            instance_id='sort-delete-plan-earlier',
            public_ip='5.5.5.62',
            status=CloudAsset.STATUS_STOPPED,
            is_active=True,
            actual_expires_at=timezone.now() + timezone.timedelta(days=10),
        )
        middle_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='sort-delete-plan-middle',
            instance_id='sort-delete-plan-middle',
            public_ip='5.5.5.63',
            status=CloudAsset.STATUS_STOPPED,
            is_active=True,
            actual_expires_at=timezone.now() + timezone.timedelta(days=20),
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_sort_delete_time', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': 1})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        rows = [
            item for item in data['server_delete_items']
            if item.get('asset_id') in {later_asset.id, earlier_asset.id, middle_asset.id}
        ]

        self.assertEqual([item['asset_id'] for item in rows], [earlier_asset.id, middle_asset.id, later_asset.id])
        delete_times = [parse_datetime(item['delete_at']) for item in rows]
        self.assertEqual(delete_times, sorted(delete_times))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_group_same_delete_time_by_user(self):
        second_user = TelegramUser.objects.create(tg_user_id=990002, username='svc_test_two')
        same_delete_at = timezone.now() + timezone.timedelta(days=10)
        assets = []
        for public_ip, user, label in [
            ('5.5.5.71', second_user, 'second-a'),
            ('5.5.5.72', self.user, 'first-a'),
            ('5.5.5.73', second_user, 'second-b'),
            ('5.5.5.74', self.user, 'first-b'),
        ]:
            assets.append(CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'sort-user-group-{label}',
                instance_id=f'sort-user-group-{label}',
                public_ip=public_ip,
                status=CloudAsset.STATUS_STOPPED,
                is_active=True,
                actual_expires_at=same_delete_at,
            ))
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_sort_user_group', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': 1})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        rows = [
            item for item in data['server_delete_items']
            if item.get('asset_id') in {asset.id for asset in assets}
        ]

        grouped_user_ids = [item['user_id'] for item in rows]
        self.assertEqual(len(grouped_user_ids), 4)
        self.assertIn(grouped_user_ids, [
            [self.user.id, self.user.id, second_user.id, second_user.id],
            [second_user.id, second_user.id, self.user.id, self.user.id],
        ])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_move_deleted_unattached_ip_active_row_to_history(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='completed-unattached-ip-active-row',
            public_ip='5.5.5.64',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        call_command('refresh_lifecycle_plans', limit=20)
        active_items = [item for item in _unattached_ip_delete_items(limit=20) if not item.get('is_history')]
        self.assertTrue(any(item.get('asset_id') == asset.id for item in active_items))
        asset.status = CloudAsset.STATUS_DELETED
        asset.is_active = False
        asset.provider_status = '已删除'
        asset.note = '固定 IP 已释放'
        asset.save(update_fields=['status', 'is_active', 'provider_status', 'note', 'updated_at'])
        call_command('refresh_lifecycle_plans', limit=20)

        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_ip_active_to_history', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        history_rows = [
            item for item in data['ip_delete_history_items']
            if item.get('asset_id') == asset.id and item.get('is_history')
        ]
        active_rows = [
            item for item in data['ip_delete_plan_items']
            if item.get('asset_id') == asset.id and not item.get('is_history')
        ]

        self.assertFalse(active_rows)
        self.assertTrue(history_rows)
        self.assertGreaterEqual(data['ip_delete_history_count'], 1)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_include_future_server_plan_item(self):
        delete_at = timezone.now() + timezone.timedelta(days=25)
        expires_at = timezone.now() + timezone.timedelta(days=20)
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-FUTURE-SERVER-PLAN-1',
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
            status='suspended',
            public_ip='52.77.18.249',
            suspend_at=timezone.now() + timezone.timedelta(days=8),
            delete_at=delete_at,
        )
        self._attach_order_expiry_asset(order, expires_at, status=CloudAsset.STATUS_STOPPED)
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_future_server_visible', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        rows = [item for item in data['server_delete_items'] if item.get('order_id') == order.id]

        self.assertTrue(rows)
        self.assertEqual(rows[0]['queue_status'], 'scheduled_future')
        self.assertEqual(rows[0]['plan_state_label'], '已排期')
        self.assertNotIn('due_items', data)
        self.assertNotIn('future_plan_items', data)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_compute_orphan_server_delete_after_suspend_window(self):
        SiteConfig.set('cloud_suspend_after_days', '3')
        SiteConfig.set('cloud_suspend_time', '17:00')
        SiteConfig.set('cloud_delete_after_days', '3')
        SiteConfig.set('cloud_delete_time', '18:00')
        expires_at = timezone.localtime(timezone.now()).replace(hour=16, minute=50, second=33, microsecond=0)
        if expires_at <= timezone.now():
            expires_at += timezone.timedelta(days=1)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='orphan-server-lifecycle-offset',
            public_ip='52.77.18.251',
            instance_id='i-orphan-server-lifecycle-offset',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_orphan_lifecycle_offset', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        row = next(item for item in data['shutdown_plan_items'] if item.get('asset_id') == asset.id)
        suspend_at = parse_datetime(row['suspend_at'])
        delete_at = parse_datetime(row['delete_at'])

        self.assertEqual(suspend_at, expires_at + timezone.timedelta(days=3, minutes=10, seconds=-33))
        self.assertEqual(delete_at, suspend_at + timezone.timedelta(days=3, hours=1))
        self.assertGreater(delete_at, suspend_at)
        self.assertNotEqual(delete_at, expires_at)
        self.assertIn(timezone.localtime(suspend_at).strftime('%Y-%m-%d %H:%M:%S'), row['execution_plan'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_orphan_server_not_due_until_computed_delete_time(self):
        SiteConfig.set('cloud_suspend_after_days', '3')
        SiteConfig.set('cloud_suspend_time', '17:00')
        SiteConfig.set('cloud_delete_after_days', '3')
        SiteConfig.set('cloud_delete_time', '18:00')
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            asset_name='orphan-server-not-delete-at-expiry',
            public_ip='52.77.18.252',
            instance_id='i-orphan-server-not-delete-at-expiry',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_STOPPED,
            is_active=True,
        )

        due_ids = {item.id for item in async_to_sync(_get_orphan_asset_delete_due)()}

        self.assertNotIn(asset.id, due_ids)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_refresh_lifecycle_plans_command_builds_lifecycle_plan_view(self):
        expires_at = timezone.now() - timezone.timedelta(days=2)
        order = CloudServerOrder.objects.create(
            order_no='CMD-LIFECYCLE-PLAN-1',
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
            status='suspended',
            public_ip='7.7.7.61',
            suspend_at=timezone.now() - timezone.timedelta(days=1),
            delete_at=timezone.now() + timezone.timedelta(hours=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='cmd-lifecycle-plan-asset',
            instance_id='cmd-lifecycle-plan-asset',
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        call_command('refresh_lifecycle_plans', limit=20)

        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 20})
        self._attach_bearer_session(request, get_user_model().objects.create_user(username='staff_cmd_lifecycle_plan', password='x', is_staff=True))
        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        self.assertTrue(any(
            item.get('asset_id') and item.get('order_id') == order.id
            for item in data['server_delete_items']
        ))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_refresh_lifecycle_plan_view_api_builds_lifecycle_plan_view(self):
        expires_at = timezone.now() - timezone.timedelta(days=2)
        order = CloudServerOrder.objects.create(
            order_no='API-LIFECYCLE-REFRESH-1',
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
            status='suspended',
            public_ip='7.7.7.63',
            suspend_at=timezone.now() - timezone.timedelta(days=1),
            delete_at=timezone.now() + timezone.timedelta(hours=1),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='api-lifecycle-refresh-asset',
            instance_id='api-lifecycle-refresh-asset',
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_api_lifecycle_refresh', password='x', is_staff=True)
        request = self.factory.post('/api/admin/tasks/plans/refresh/', data=json.dumps({'limit': 20}), content_type='application/json')
        self._attach_bearer_session(request, staff_user)

        response = refresh_lifecycle_plan_view(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)['data']
        self.assertGreaterEqual(data['server_delete_count'], 1)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_update_cloud_asset_expiry_refreshes_delete_plan_view(self):
        old_expiry = timezone.now() - timezone.timedelta(days=10)
        new_expiry = timezone.now() - timezone.timedelta(days=1)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='expiry-refresh-delete-plan',
            instance_id='expiry-refresh-delete-plan',
            public_ip='7.7.7.64',
            actual_expires_at=old_expiry,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        old_row = _asset_delete_plan_item_payload(asset)
        old_delete_at = parse_datetime(old_row['delete_at'])
        staff_user = get_user_model().objects.create_user(username='staff_asset_expiry_refresh_plan', password='x', is_staff=True, is_superuser=True)
        request = self.factory.patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({'actual_expires_at': new_expiry.isoformat()}),
            content_type='application/json',
        )
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        asset.refresh_from_db()
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 20})
        self._attach_bearer_session(request, staff_user)
        data = json.loads(lifecycle_plans(request).content)['data']
        row = next(item for item in data['shutdown_plan_items'] if item.get('asset_id') == asset.id)
        self.assertEqual(asset.actual_expires_at, new_expiry)
        self.assertNotEqual(parse_datetime(row['delete_at']), old_delete_at)
        self.assertEqual(parse_datetime(row['actual_expires_at']), new_expiry)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_update_unattached_ip_release_time_refreshes_delete_plan_view(self):
        old_release_at = timezone.now() + timezone.timedelta(days=1)
        new_release_at = timezone.now() + timezone.timedelta(days=3)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-expiry-refresh-plan',
            public_ip='7.7.7.65',
            actual_expires_at=old_release_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=True,
        )
        row = next(item for item in _unattached_ip_delete_items(limit=20) if item.get('asset_id') == asset.id)
        self.assertEqual(parse_datetime(row['delete_at']), old_release_at)
        staff_user = get_user_model().objects.create_user(username='staff_ip_expiry_refresh_plan', password='x', is_staff=True, is_superuser=True)
        request = self.factory.patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({'actual_expires_at': new_release_at.isoformat()}),
            content_type='application/json',
        )
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        asset.refresh_from_db()
        row = next(item for item in _unattached_ip_delete_items(limit=20) if item.get('asset_id') == asset.id)
        self.assertEqual(asset.actual_expires_at, new_release_at)
        self.assertEqual(parse_datetime(row['delete_at']), new_release_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_refresh_notice_plans_command_builds_notice_plan_view(self):
        now = timezone.now()
        expires_at = now + timezone.timedelta(days=1)
        order = CloudServerOrder.objects.create(
            order_no='CMD-NOTICE-PLAN-1',
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
            public_ip='7.7.7.62',
            cloud_reminder_enabled=True,
        )
        self._create_auto_renew_asset(order, expires_at=expires_at)

        call_command('refresh_notice_plans', limit=20, history_limit=20)

        staff_user = get_user_model().objects.create_user(username='staff_cmd_notice_plan', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/notices/', {'limit': 20, 'history_limit': 20})
        self._attach_bearer_session(request, staff_user)
        data = json.loads(notice_task_detail(request).content)['data']
        self.assertTrue(any(
            item.get('notice_type') == 'renew_notice' and order.id in (item.get('order_ids') or [])
            for item in data['active_user_summary_items']
        ))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_refresh_notice_plan_view_api_builds_notice_plan_view(self):
        now = timezone.now()
        expires_at = now + timezone.timedelta(days=1)
        order = CloudServerOrder.objects.create(
            order_no='API-NOTICE-REFRESH-1',
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
            public_ip='7.7.7.64',
            cloud_reminder_enabled=True,
        )
        self._create_auto_renew_asset(order, expires_at=expires_at)
        staff_user = get_user_model().objects.create_user(username='staff_api_notice_refresh', password='x', is_staff=True)
        request = self.factory.post('/api/admin/tasks/notices/refresh/', data=json.dumps({'limit': 20, 'history_limit': 20}), content_type='application/json')
        self._attach_bearer_session(request, staff_user)

        response = refresh_notice_plan_view(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)['data']
        self.assertGreaterEqual(data['due_count'], 1)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_notice_task_detail_uses_notice_plan_view(self):
        now = timezone.now()
        expires_at = now + timezone.timedelta(days=1)
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-PLAN-TABLE-RENEW-1',
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
            public_ip='7.7.7.71',
            cloud_reminder_enabled=True,
        )
        self._create_auto_renew_asset(order, expires_at=expires_at)
        staff_user = get_user_model().objects.create_user(username='staff_notice_plan_table', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/notices/', {'limit': 20, 'history_limit': 20})
        self._attach_bearer_session(request, staff_user)

        with patch('cloud.lifecycle._get_due_orders') as due_orders_mock:
            due_orders_mock.side_effect = AssertionError('通知计划详情不应回退到全量订单扫描')
            response = notice_task_detail(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)['data']
        row = next(item for item in data['active_user_summary_items'] if item.get('notice_type') == 'renew_notice' and order.id in (item.get('order_ids') or []))
        self.assertIn('7.7.7.71', row.get('ips') or [])

    # 功能：验证通知表关闭文案列后不再构造批量通知文案，避免大数据分页加载被隐藏列拖慢。
    def test_notice_task_detail_basic_fields_skip_batch_text_payload(self):
        now = timezone.now()
        expires_at = now + timezone.timedelta(days=1)
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-BASIC-FIELDS-1',
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
            public_ip='7.7.7.72',
            cloud_reminder_enabled=True,
        )
        self._create_auto_renew_asset(order, expires_at=expires_at)
        staff_user = get_user_model().objects.create_user(username='staff_notice_basic_fields', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/notices/', {
            'compact': '1',
            'fields': 'basic',
            'limit': '10',
            'history_limit': '10',
        })
        self._attach_bearer_session(request, staff_user)

        with patch('cloud.api_tasks._notice_actual_batch_payload', side_effect=AssertionError('隐藏文案列时不应构造批量文案')):
            response = notice_task_detail(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)['data']
        row = next(item for item in data['active_user_summary_items'] if item.get('user_id') == self.user.id)
        self.assertNotIn('notice_text_preview', row)

    # 功能：验证通知计划深分页 offset 不会在 10 万后被静默截断，避免前端跳最后页显示错页。
    def test_notice_task_detail_allows_deep_offsets_beyond_100k(self):
        staff_user = get_user_model().objects.create_user(username='staff_notice_deep_offset_limit', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/notices/', {
            'compact': '1',
            'fields': 'basic',
            'limit': '10',
            'offset': '120000',
            'history_limit': '10',
            'history_offset': '130000',
        })
        self._attach_bearer_session(request, staff_user)

        with patch('cloud.api_tasks._build_notice_plan_summary', return_value={
            'active_user_summary_items': [],
            'active_user_total': 0,
            'history_items': [],
            'history_count': 0,
            'total_counts': {
                'due_count': 0,
                'future_count': 0,
                'due_user_count': 0,
                'future_user_count': 0,
                'active_user_count': 0,
            },
        }) as summary_mock:
            response = notice_task_detail(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(summary_mock.call_args.kwargs['offset'], 120000)
        self.assertEqual(summary_mock.call_args.kwargs['history_offset'], 130000)

    # 功能：验证通知计划摘要复用同一轮分组结果，避免 10 万级页面重复扫描统计和分页。
    def test_notice_plan_summary_reuses_group_rows_for_counts(self):
        from cloud import api_tasks

        due_rows = [{
            'id': 'due-user:renew_notice:due',
            'plan_scope': 'due',
            'plan_scope_label': '近期计划',
            'user_id': self.user.id,
            'tg_user_id': self.user.tg_user_id,
            'user_display_name': 'due-user',
            'username_label': '-',
            'notice_type': 'renew_notice',
            'notice_type_label': '到期提醒',
            'notice_event': 'renew_notice_batch',
            'notice_count': 2,
            'ip_count': 2,
            'pending_count': 0,
            'failed_retry_count': 0,
            'next_notice_at': None,
            '_next_notice_at_value': timezone.now(),
        }]
        future_rows = [{
            'id': 'future-user:renew_notice:future',
            'plan_scope': 'future',
            'plan_scope_label': '未来计划',
            'user_id': self.user.id,
            'tg_user_id': self.user.tg_user_id,
            'user_display_name': 'future-user',
            'username_label': '-',
            'notice_type': 'renew_notice',
            'notice_type_label': '到期提醒',
            'notice_event': 'renew_notice_batch',
            'notice_count': 3,
            'ip_count': 3,
            'pending_count': 0,
            'failed_retry_count': 0,
            'next_notice_at': None,
            '_next_notice_at_value': timezone.now(),
        }]

        with patch('cloud.api_tasks._notice_group_rows_for_scope', side_effect=[due_rows, future_rows]) as rows_mock, \
            patch('cloud.api_tasks._notice_group_summary_from_row', side_effect=lambda row, **kwargs: {key: value for key, value in row.items() if not key.startswith('_')}), \
            patch('cloud.api_tasks._notice_latest_log_map', return_value={}), \
            patch('cloud.api_tasks._planned_notice_account_attempts', return_value=[]), \
            patch.object(api_tasks.CloudUserNoticeLog.objects, 'select_related', return_value=api_tasks.CloudUserNoticeLog.objects.none()):
            summary = api_tasks._build_notice_plan_summary(limit=10, fields={'basic'})

        self.assertEqual(rows_mock.call_count, 2)
        self.assertEqual(summary['active_user_total'], 2)
        self.assertEqual(summary['total_counts']['due_count'], 2)
        self.assertEqual(summary['total_counts']['future_count'], 3)
        self.assertEqual(summary['total_counts']['active_user_count'], 2)

    # 功能：验证通知计划总数统计全量未来计划，且分页只加载当前页分组。
    def test_notice_task_detail_counts_all_future_groups_beyond_loaded_limit(self):
        now = timezone.now()
        users = []
        for index in range(4):
            user = TelegramUser.objects.create(tg_user_id=910500 + index, username=f'notice_future_group_{index}')
            users.append(user)
            expires_at = now + timezone.timedelta(days=40, minutes=index)
            order = CloudServerOrder.objects.create(
                order_no=f'NOTICE-FULL-FUTURE-COUNT-{index}',
                user=user,
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
                public_ip=f'7.7.7.{90 + index}',
                cloud_reminder_enabled=True,
            )
            self._create_auto_renew_asset(order, expires_at=expires_at)
        staff_user = get_user_model().objects.create_user(username='staff_notice_full_future_count', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/notices/', {
            'compact': '1',
            'fields': 'basic',
            'limit': '1',
            'history_limit': '1',
        })
        self._attach_bearer_session(request, staff_user)

        response = notice_task_detail(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)['data']
        self.assertGreaterEqual(data['future_count'], 4)
        self.assertGreaterEqual(data['future_user_count'], 4)
        self.assertEqual(len(data['active_user_summary_items']), 1)
        self.assertEqual(data['active_user_summary_items'][0]['plan_scope'], 'future')

    # 功能：验证通知计划服务端分页深页不会空页、不会重复。
    def test_notice_task_detail_deep_group_page_has_no_duplicates(self):
        now = timezone.now()
        created_user_ids = []
        for index in range(6):
            user = TelegramUser.objects.create(
                tg_user_id=911000 + index,
                username=f'notice_deep_group_{index}',
                first_name=f'NoticeDeep{index}',
            )
            created_user_ids.append(user.id)
            expires_at = now + timezone.timedelta(days=40, minutes=index)
            order = CloudServerOrder.objects.create(
                order_no=f'NOTICE-DEEP-GROUP-PAGE-{index}',
                user=user,
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
                public_ip=f'7.7.8.{80 + index}',
                cloud_reminder_enabled=True,
            )
            self._create_auto_renew_asset(order, expires_at=expires_at)
        staff_user = get_user_model().objects.create_user(username='staff_notice_deep_group_page', password='x', is_staff=True)
        first_request = self.factory.get('/api/admin/tasks/notices/', {
            'compact': '1',
            'fields': 'basic',
            'limit': '3',
            'offset': '0',
            'history_limit': '1',
        })
        self._attach_bearer_session(first_request, staff_user)
        second_request = self.factory.get('/api/admin/tasks/notices/', {
            'compact': '1',
            'fields': 'basic',
            'limit': '3',
            'offset': '3',
            'history_limit': '1',
        })
        self._attach_bearer_session(second_request, staff_user)

        first_data = json.loads(notice_task_detail(first_request).content)['data']
        second_data = json.loads(notice_task_detail(second_request).content)['data']
        first_keys = {item['id'] for item in first_data['active_user_summary_items']}
        second_keys = {item['id'] for item in second_data['active_user_summary_items']}
        seen_user_ids = {
            item.get('user_id')
            for item in [*first_data['active_user_summary_items'], *second_data['active_user_summary_items']]
            if item.get('user_id') in created_user_ids
        }

        self.assertEqual(len(first_data['active_user_summary_items']), 3)
        self.assertEqual(len(second_data['active_user_summary_items']), 3)
        self.assertFalse(first_keys & second_keys)
        self.assertEqual(seen_user_ids, set(created_user_ids))

    # 功能：验证通知计划详情不会展示资产开关关闭的删机提醒。
    def test_notice_task_detail_hides_shutdown_disabled_lifecycle_notices(self):
        now = timezone.now()
        delete_at = now + timezone.timedelta(days=2)
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-SHUTDOWN-OFF-DELETE-1',
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
            status='suspended',
            public_ip='7.7.7.72',
            delete_at=delete_at,
            delete_reminder_enabled=True,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='notice-shutdown-off-delete',
            public_ip=order.public_ip,
            actual_expires_at=now - timezone.timedelta(days=5),
            status=CloudAsset.STATUS_RUNNING,
            shutdown_enabled=False,
            server_delete_enabled=False,
            is_active=True,
        )
        recycle_order = CloudServerOrder.objects.create(
            order_no='NOTICE-SHUTDOWN-OFF-RECYCLE-1',
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
            previous_public_ip='7.7.7.73',
            static_ip_name='StaticIp-notice-shutdown-off',
            ip_recycle_at=now + timezone.timedelta(days=2),
            ip_recycle_reminder_enabled=True,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=recycle_order,
            user=self.user,
            provider=recycle_order.provider,
            region_code=recycle_order.region_code,
            region_name=recycle_order.region_name,
            asset_name='StaticIp-notice-shutdown-off',
            public_ip='7.7.7.73',
            previous_public_ip='7.7.7.73',
            actual_expires_at=now - timezone.timedelta(days=5),
            status=CloudAsset.STATUS_DELETED,
            provider_status='固定IP保留中-实例已删除',
            shutdown_enabled=False,
            ip_delete_enabled=False,
            is_active=False,
        )
        staff_user = get_user_model().objects.create_user(username='staff_notice_shutdown_off', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/notices/', {'limit': 20, 'history_limit': 20})
        self._attach_bearer_session(request, staff_user)

        response = notice_task_detail(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)['data']
        visible_items = data['active_user_summary_items']
        self.assertFalse(any(item.get('order_id') == order.id and item.get('notice_type') == 'delete_notice' for item in visible_items))
        self.assertFalse(any(item.get('order_id') == recycle_order.id and item.get('notice_type') == 'recycle_notice' for item in visible_items))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_notice_write_actions_require_superuser(self):
        staff_user = get_user_model().objects.create_user(username='staff_notice_write_blocked', password='x', is_staff=True)
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-WRITE-BLOCKED-1',
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
            public_ip='7.7.7.73',
        )
        log = CloudUserNoticeLog.objects.create(
            user=self.user,
            order=order,
            batch_id='notice-write-blocked-1',
            event_type='renew_notice_batch',
            target_chat_id=123456,
            order_no=order.order_no,
            ip=order.public_ip,
            is_batch=True,
            delivered=True,
            text_preview='到期提醒：测试权限拦截',
            extra={'order_ids': [order.id]},
        )

        switch_request = self.factory.post(
            '/api/admin/tasks/notices/switches/',
            data=json.dumps({'switches': [{'key': 'cloud_daily_expiry_summary_enabled', 'enabled': False}]}),
            content_type='application/json',
        )
        self._attach_bearer_session(switch_request, staff_user)
        self.assertEqual(update_notice_switches(switch_request).status_code, 403)

        text_request = self.factory.post(
            '/api/admin/tasks/notices/text/',
            data=json.dumps({'notice_event': 'renew_notice', 'order_ids': [order.id], 'notice_text': 'blocked'}),
            content_type='application/json',
        )
        self._attach_bearer_session(text_request, staff_user)
        self.assertEqual(update_notice_plan_text(text_request).status_code, 403)

        delete_request = self.factory.post(f'/api/admin/tasks/notices/history/{log.id}/delete/')
        self._attach_bearer_session(delete_request, staff_user)
        self.assertEqual(delete_notice_history(delete_request, str(log.id)).status_code, 403)
        self.assertTrue(CloudUserNoticeLog.objects.filter(id=log.id).exists())

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_delete_notice_history_removes_notice_history_row(self):
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-PLAN-HISTORY-DELETE-1',
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
            public_ip='7.7.7.72',
        )
        log = CloudUserNoticeLog.objects.create(
            user=self.user,
            order=order,
            batch_id='notice-batch-delete-1',
            event_type='renew_notice_batch',
            target_chat_id=123456,
            order_no=order.order_no,
            ip=order.public_ip,
            is_batch=True,
            delivered=True,
            text_preview='到期提醒：测试历史删除',
            extra={'order_ids': [order.id], 'send_attempts': [{'channel': 'bot', 'channel_label': 'Bot', 'ok': True, 'error': ''}]},
        )
        staff_user = get_user_model().objects.create_user(username='staff_notice_plan_history_delete', password='x', is_staff=True, is_superuser=True)
        sync_request = self.factory.get('/api/admin/tasks/notices/', {'limit': 20, 'history_limit': 20})
        self._attach_bearer_session(sync_request, staff_user)
        sync_response = notice_task_detail(sync_request)
        self.assertEqual(sync_response.status_code, 200)
        sync_data = json.loads(sync_response.content)['data']
        self.assertTrue(any(item.get('log_id') == log.id for item in sync_data['history_items']))

        request = self.factory.post(f'/api/admin/tasks/notices/history/{log.id}/delete/')
        self._attach_bearer_session(request, staff_user)
        response = delete_notice_history(request, str(log.id))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CloudUserNoticeLog.objects.filter(id=log.id).exists())

    # 功能：验证同一批次的多条通知历史仍使用日志 ID 作为表格唯一行键。
    def test_notice_history_rows_keep_unique_log_ids_for_same_batch(self):
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-PLAN-HISTORY-UNIQUE-1',
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
            public_ip='7.7.7.73',
        )
        logs = [
            CloudUserNoticeLog.objects.create(
                user=self.user,
                order=order,
                batch_id='notice-batch-same-row-key',
                event_type='renew_notice_batch',
                target_chat_id=123456,
                order_no=order.order_no,
                ip=order.public_ip,
                is_batch=True,
                delivered=True,
                text_preview=f'到期提醒：同批次历史 {index}',
                extra={'order_ids': [order.id]},
            )
            for index in range(2)
        ]
        staff_user = get_user_model().objects.create_user(username='staff_notice_history_unique_ids', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/notices/', {
            'compact': '1',
            'fields': 'basic',
            'limit': 1,
            'history_limit': 10,
        })
        self._attach_bearer_session(request, staff_user)
        response = notice_task_detail(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)['data']
        rows = [
            item for item in data['history_items']
            if item.get('batch_id') == 'notice-batch-same-row-key'
        ]
        self.assertEqual({item.get('id') for item in rows}, {log.id for log in logs})
        self.assertEqual({item.get('log_id') for item in rows}, {log.id for log in logs})

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_ip_query_keyboard_limits_non_owner_to_renewal(self):
        from bot.keyboards import cloud_ip_query_result

        markup = cloud_ip_query_result([], [{
            'ip': '4.4.4.44',
            'order_id': 123,
            'asset_id': 0,
            'can_change_ip': False,
            'can_reinit': False,
            'can_config': False,
            'can_support': False,
        }], include_start=False, include_reinit=False)
        labels = [button.text for row in markup.inline_keyboard for button in row]

        self.assertIn('🔄 续费IP', labels)
        self.assertNotIn('🌐 更换IP', labels)
        self.assertNotIn('🛠 重新安装', labels)
        self.assertNotIn('⚙️ 修改配置', labels)
        self.assertNotIn('⚡ 开启自动续费', labels)
        self.assertNotIn('⛔ 关闭自动续费', labels)
        self.assertNotIn('👩‍💻 联系客服', labels)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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

        # 功能：处理 云资产、云订单和生命周期 中的 fake call command 业务流程。
        def fake_call_command(command_name, **kwargs):
            calls.append((command_name, kwargs))

        SiteConfig.set('cloud_asset_sync_next_account_cursor', '')
        with patch.dict(os.environ, {'AWS_REGION': '', 'ALIYUN_REGION': ''}, clear=False), patch('cloud.lifecycle.call_command', side_effect=fake_call_command):
            async_to_sync(sync_server_status_tick)()
            async_to_sync(sync_server_status_tick)()

        aliyun_call = calls[0]
        aws_call = calls[1]
        self.assertEqual(aliyun_call[0], 'sync_aliyun_assets')
        self.assertEqual(aliyun_call[1]['account_id'], str(aliyun_account.id))
        self.assertEqual(aliyun_call[1]['region'], 'cn-hongkong')
        self.assertEqual(aws_call[0], 'sync_aws_assets')
        self.assertEqual(aws_call[1]['account_id'], str(aws_account.id))
        self.assertNotIn('region', aws_call[1])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_sync_rotates_one_active_account_per_tick(self):
        first = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-lifecycle-rotate-1',
            external_account_id='acct-rotate-1',
            access_key='ak1',
            secret_key='sk1',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        second = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-lifecycle-rotate-2',
            external_account_id='acct-rotate-2',
            access_key='ak2',
            secret_key='sk2',
            region_hint='ap-southeast-1',
            is_active=True,
        )
        SiteConfig.set('cloud_asset_sync_next_account_cursor', '')
        calls = []

        # 功能：处理 云资产、云订单和生命周期 中的 fake call command 业务流程。
        def fake_call_command(command_name, **kwargs):
            calls.append((command_name, kwargs))

        with patch.dict(os.environ, {'AWS_REGION': ''}, clear=False), patch('cloud.lifecycle.call_command', side_effect=fake_call_command):
            async_to_sync(sync_server_status_tick)()
            async_to_sync(sync_server_status_tick)()

        self.assertEqual([item[1]['account_id'] for item in calls], [str(first.id), str(second.id)])
        self.assertTrue(all(item[0] == 'sync_aws_assets' for item in calls))
        self.assertTrue(all('region' not in item[1] for item in calls))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_delete_cloud_asset_only_removes_asset_record(self):
        asset_expires_at = timezone.now() + timezone.timedelta(days=31)
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
            actual_expires_at=asset_expires_at,
            price='19.00',
            status=CloudAsset.STATUS_RUNNING,
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_delete_only', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().post(f'/api/admin/cloud-assets/{asset.id}/delete/')
        request = self._attach_bearer_session(request, staff_user)

        response = delete_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        order.refresh_from_db()
        self.assertFalse(CloudAsset.objects.filter(id=asset.id).exists())
        self.assertEqual(order.status, 'completed')
        self.assertIsNone(order.public_ip)
        self.assertIsNone(order.previous_public_ip)
        self.assertEqual(order.instance_id, '')
        self.assertEqual(order.provider_resource_id, '')
        self.assertEqual(order.static_ip_name, '')
        self.assertEqual(order.mtproxy_port, 0)
        self.assertEqual(order.mtproxy_link, '')
        self.assertEqual(order.proxy_links, [])
        self.assertEqual(payload['data']['removed_servers'], 0)
        self.assertEqual(payload['data']['order_status_changed'], True)
        self.assertTrue(CloudIpLog.objects.filter(order=order, note__contains='后续云同步按全新资源处理').exists())
        self.assertTrue(CloudIpLog.objects.filter(order=order, asset_name='delete-asset-only', event_type=CloudIpLog.EVENT_DELETED, note__contains='后台手动删除代理列表记录').exists())
        from cloud.management.commands.sync_aws_assets import _resolve_order_for_ip
        self.assertIsNone(_resolve_order_for_ip('8.8.8.8'))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_delete_cloud_asset_also_removes_residual_server_record(self):
        asset_expires_at = timezone.now() + timezone.timedelta(days=31)
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
            actual_expires_at=asset_expires_at,
            price='19.00',
            status=CloudAsset.STATUS_DELETED,
            provider_status='云上未找到实例/IP',
            is_active=False,
            note='状态: 云上未找到实例/IP',
        )
        residual_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='delete-asset-residual-server',
            public_ip=None,
            previous_public_ip='8.8.4.4',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=CloudAsset.STATUS_DELETED,
            provider_status='云上未找到实例/IP',
            is_active=False,
            note='状态: 云上未找到实例/IP',
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_delete_residual', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().post(f'/api/admin/cloud-assets/{asset.id}/delete/')
        request = self._attach_bearer_session(request, staff_user)

        response = delete_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertFalse(CloudAsset.objects.filter(id=asset.id).exists())
        self.assertFalse(CloudAsset.objects.filter(id=residual_asset.id).exists())
        order.refresh_from_db()
        self.assertEqual(payload['data']['removed_servers'], 1)
        self.assertEqual(payload['data']['removed_server_ids'], [residual_asset.id])
        self.assertEqual(payload['data']['order_status_changed'], True)
        self.assertIsNone(order.public_ip)
        self.assertIsNone(order.previous_public_ip)
        self.assertEqual(order.instance_id, '')
        self.assertEqual(order.provider_resource_id, '')
        self.assertTrue(CloudIpLog.objects.filter(order=order, note__contains='后台手动删除代理列表记录').exists())








    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_delete_server_only_removes_server_record(self):
        asset_expires_at = timezone.now() + timezone.timedelta(days=31)
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
            actual_expires_at=asset_expires_at,
            price='19.00',
            status=CloudAsset.STATUS_RUNNING,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='delete-server-only',
            public_ip=order.public_ip,
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=CloudAsset.STATUS_RUNNING,
        )
        staff_user = get_user_model().objects.create_user(username='staff_server_delete_only', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().post(f'/api/admin/servers/{server.id}/delete/')
        self._attach_bearer_session(request, staff_user)

        response = delete_server(request, server.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertFalse(CloudAsset.objects.filter(id=server.id).exists())
        self.assertFalse(CloudAsset.objects.filter(id=asset.id).exists())
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order.public_ip, '9.9.9.9')
        self.assertEqual(order.instance_id, 'i-delete-server-only')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        staff_user = get_user_model().objects.create_user(username='staff_server_delete_no_fallback', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().post(f'/api/admin/servers/{asset.id}/delete/')
        self._attach_bearer_session(request, staff_user)

        response = delete_server(request, asset.id)

        self.assertEqual(response.status_code, 404)
        self.assertTrue(CloudAsset.objects.filter(id=asset.id).exists())

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_servers_list_excludes_unattached_static_ip_rows(self):
        unattached = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unattached-static-ip-row',
            public_ip='9.9.9.10',
            instance_id='',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='未附加固定IP-续费保留中',
            note='未附加固定IP',
        )
        attached = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='attached-server-row',
            public_ip='9.9.9.11',
            instance_id='i-attached-server-row',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
        )
        staff_user = get_user_model().objects.create_user(username='staff_servers_list_unattached', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/servers/')
        self._attach_bearer_session(request, staff_user)

        response = servers_list(request)
        data = json.loads(response.content)['data']
        ids = {item['id'] for item in data}

        self.assertNotIn(unattached.id, ids)
        self.assertIn(attached.id, ids)

    # 功能：验证服务器后台列表使用服务端分页返回全量资产，不再只暴露前 500 条。
    def test_servers_list_paginated_matches_cloud_asset_order(self):
        now = timezone.now()
        assets = []
        for index in range(3):
            assets.append(CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_ORDER,
                user=self.user,
                provider='aws_lightsail',
                region_code=self.plan.region_code,
                region_name=self.plan.region_name,
                asset_name=f'paginated-server-{index}',
                instance_id=f'i-paginated-server-{index}',
                public_ip=f'9.9.9.{20 + index}',
                status=CloudAsset.STATUS_RUNNING,
                actual_expires_at=now + timezone.timedelta(days=index + 1),
                is_active=True,
            ))
        staff_user = get_user_model().objects.create_user(username='staff_servers_list_paginated', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/servers/', {
            'paginated': '1',
            'dedup': '0',
            'page': '2',
            'page_size': '2',
        })
        self._attach_bearer_session(request, staff_user)

        response = servers_list(request)
        data = json.loads(response.content)['data']

        self.assertEqual(data['total'], 3)
        self.assertEqual(data['page'], 2)
        self.assertEqual(data['page_size'], 2)
        self.assertEqual(data['total_pages'], 2)
        self.assertEqual([item['id'] for item in data['items']], [assets[2].id])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_send_logged_cloud_notice_deduplicates_same_event_and_order(self):
        expires_at = timezone.now() + timezone.timedelta(days=12)
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
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='notice-dedupe-asset',
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        sent = []

        # 功能：处理 云资产、云订单和生命周期 中的 fake notify 业务流程。
        async def fake_notify(user_id, text, reply_markup=None):
            sent.append((user_id, text))
            return True

        result1 = async_to_sync(_send_logged_cloud_notice)('renew_notice', fake_notify, self.user.id, 'hello', None, order=order, notice={'ip': '8.8.8.9'})
        result2 = async_to_sync(_send_logged_cloud_notice)('renew_notice', fake_notify, self.user.id, 'hello', None, order=order, notice={'ip': '8.8.8.9'})

        self.assertTrue(result1)
        self.assertFalse(result2)
        self.assertEqual(len(sent), 1)
        self.assertEqual(CloudUserNoticeLog.objects.filter(event_type='renew_notice', user=self.user, order=order, delivered=True).count(), 1)
        self.assertEqual(CloudNoticeTask.objects.filter(notice_type=CloudNoticeTask.NOTICE_RENEW, status=CloudNoticeTask.STATUS_SENT).count(), 1)

    # 功能：验证同一订单续费进入新到期周期后，通知批次不会被上一周期挡住。
    def test_send_order_notice_batch_allows_new_expiry_cycle(self):
        from cloud.lifecycle import NOTICE_TEXT_OVERRIDES_CONFIG_KEY, _set_notice_text_override

        self.addCleanup(SiteConfig.clear_cache, NOTICE_TEXT_OVERRIDES_CONFIG_KEY)

        first_expires_at = timezone.now() + timezone.timedelta(days=3)
        second_expires_at = timezone.now() + timezone.timedelta(days=34)
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-CYCLE-RENEW-1',
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
            public_ip='8.8.8.94',
            service_started_at=timezone.now(),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='notice-cycle-renew-asset',
            public_ip=order.public_ip,
            actual_expires_at=first_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        _set_notice_text_override('renew_notice_batch', self.user.id, [order.id], 'manual first cycle')
        sent = []

        async def fake_notify(user_id, text, reply_markup=None):
            sent.append((user_id, text))
            return True

        first_result = async_to_sync(_send_order_notice_batch)(
            event='renew_notice_batch',
            field_name='renew_notice_sent_at',
            notify=fake_notify,
            user_id=self.user.id,
            orders=[order],
            payload={'text': 'first cycle', 'order_ids': [order.id], 'first_order_id': order.id, 'count': 1},
        )
        CloudServerOrder.objects.filter(id=order.id).update(renew_notice_sent_at=None)
        asset.actual_expires_at = second_expires_at
        asset.save(update_fields=['actual_expires_at', 'updated_at'])
        order.refresh_from_db()
        second_result = async_to_sync(_send_order_notice_batch)(
            event='renew_notice_batch',
            field_name='renew_notice_sent_at',
            notify=fake_notify,
            user_id=self.user.id,
            orders=[order],
            payload={'text': 'second cycle', 'order_ids': [order.id], 'first_order_id': order.id, 'count': 1},
        )

        self.assertTrue(first_result)
        self.assertTrue(second_result)
        self.assertEqual(sent, [(self.user.id, 'manual first cycle'), (self.user.id, 'second cycle')])
        self.assertEqual(CloudUserNoticeLog.objects.filter(event_type='renew_notice_batch', order=order, delivered=True).count(), 2)
        self.assertEqual(CloudNoticeTask.objects.filter(notice_type=CloudNoticeTask.NOTICE_RENEW, status=CloudNoticeTask.STATUS_SENT).count(), 2)

    # 功能：验证数据库生命周期任务能挡住同一轮计划删机重复认领。
    def test_lifecycle_delete_task_claim_blocks_same_cycle_duplicate(self):
        from cloud.lifecycle_tasks import claim_lifecycle_task_for_order, finish_lifecycle_task

        now = timezone.now()
        delete_at = now - timezone.timedelta(minutes=1)
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-CLAIM-DELETE-1',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='Singapore',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='suspended',
            public_ip='8.8.8.91',
            service_started_at=now - timezone.timedelta(days=35),
            suspend_at=now - timezone.timedelta(days=1),
            delete_at=delete_at,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='lifecycle-claim-delete-asset',
            public_ip=order.public_ip,
            actual_expires_at=now - timezone.timedelta(days=5),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        first_claim = claim_lifecycle_task_for_order(CloudLifecycleTask.TASK_DELETE, order, scheduled_at=delete_at, queue_status='scheduled_delete')
        second_claim = claim_lifecycle_task_for_order(CloudLifecycleTask.TASK_DELETE, order, scheduled_at=delete_at, queue_status='scheduled_delete')
        finish_lifecycle_task(first_claim, ok=True)
        third_claim = claim_lifecycle_task_for_order(CloudLifecycleTask.TASK_DELETE, order, scheduled_at=delete_at, queue_status='scheduled_delete')

        self.assertIsNotNone(first_claim)
        self.assertIsNone(second_claim)
        self.assertIsNone(third_claim)
        self.assertEqual(CloudLifecycleTask.objects.filter(task_type=CloudLifecycleTask.TASK_DELETE, status=CloudLifecycleTask.STATUS_DONE).count(), 1)

    # 功能：验证资产类生命周期任务同一轮计划只允许一个执行者认领。
    def test_lifecycle_asset_task_claim_blocks_same_cycle_duplicate(self):
        from cloud.lifecycle_tasks import claim_lifecycle_task_for_asset, finish_lifecycle_task

        due_at = timezone.now() - timezone.timedelta(minutes=1)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='Singapore',
            asset_name='asset-claim-unattached-ip',
            public_ip='8.8.8.92',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=True,
        )

        first_claim = claim_lifecycle_task_for_asset(CloudLifecycleTask.TASK_UNATTACHED_IP_DELETE, asset, scheduled_at=due_at, queue_status='scheduled_unattached_ip_delete')
        second_claim = claim_lifecycle_task_for_asset(CloudLifecycleTask.TASK_UNATTACHED_IP_DELETE, asset, scheduled_at=due_at, queue_status='scheduled_unattached_ip_delete')
        finish_lifecycle_task(first_claim, ok=True)
        third_claim = claim_lifecycle_task_for_asset(CloudLifecycleTask.TASK_UNATTACHED_IP_DELETE, asset, scheduled_at=due_at, queue_status='scheduled_unattached_ip_delete')

        self.assertIsNotNone(first_claim)
        self.assertIsNone(second_claim)
        self.assertIsNone(third_claim)
        self.assertEqual(CloudLifecycleTask.objects.filter(task_type=CloudLifecycleTask.TASK_UNATTACHED_IP_DELETE, status=CloudLifecycleTask.STATUS_DONE).count(), 1)

    # 功能：验证失败任务不会在保护期内被同一轮计划立即重复认领。
    def test_failed_lifecycle_and_notice_tasks_wait_retry_window(self):
        from cloud.lifecycle_tasks import FAILED_RETRY_AFTER, claim_lifecycle_task_for_order, claim_notice_task, finish_lifecycle_task, finish_notice_task

        now = timezone.now()
        delete_at = now - timezone.timedelta(minutes=1)
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-FAILED-RETRY-1',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='Singapore',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='suspended',
            public_ip='8.8.8.95',
            service_started_at=now - timezone.timedelta(days=35),
            delete_at=delete_at,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='lifecycle-failed-retry-asset',
            public_ip=order.public_ip,
            actual_expires_at=now - timezone.timedelta(days=5),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        first_lifecycle_claim = claim_lifecycle_task_for_order(CloudLifecycleTask.TASK_DELETE, order, scheduled_at=delete_at, queue_status='scheduled_delete')
        finish_lifecycle_task(first_lifecycle_claim, ok=False, error='云 API 临时失败')
        blocked_lifecycle_claim = claim_lifecycle_task_for_order(CloudLifecycleTask.TASK_DELETE, order, scheduled_at=delete_at, queue_status='scheduled_delete')
        CloudLifecycleTask.objects.filter(id=first_lifecycle_claim.id).update(last_run_at=now - FAILED_RETRY_AFTER - timezone.timedelta(seconds=1))
        retry_lifecycle_claim = claim_lifecycle_task_for_order(CloudLifecycleTask.TASK_DELETE, order, scheduled_at=delete_at, queue_status='scheduled_delete')

        first_notice_claim = claim_notice_task('delete_notice', user_id=self.user.id, order=order, batch_id='retry-window')
        finish_notice_task(first_notice_claim, delivered=False, error='通知发送器临时失败')
        blocked_notice_claim = claim_notice_task('delete_notice', user_id=self.user.id, order=order, batch_id='retry-window')
        CloudNoticeTask.objects.filter(id=first_notice_claim.id).update(last_run_at=now - FAILED_RETRY_AFTER - timezone.timedelta(seconds=1))
        retry_notice_claim = claim_notice_task('delete_notice', user_id=self.user.id, order=order, batch_id='retry-window')

        self.assertIsNotNone(first_lifecycle_claim)
        self.assertIsNone(blocked_lifecycle_claim)
        self.assertIsNotNone(retry_lifecycle_claim)
        self.assertIsNotNone(first_notice_claim)
        self.assertIsNone(blocked_notice_claim)
        self.assertIsNotNone(retry_notice_claim)

    # 功能：验证计划删机失败后，人工重试成功会把同一订单的失败任务收敛为已完成。
    def test_manual_delete_success_finishes_failed_lifecycle_delete_task(self):
        from cloud.lifecycle_execution import run_shutdown_order_delete
        from cloud.lifecycle_tasks import claim_lifecycle_task_for_order, finish_lifecycle_task

        now = timezone.now()
        delete_at = now - timezone.timedelta(minutes=1)
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-MANUAL-DELETE-FINISH-1',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='Singapore',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='suspended',
            public_ip='8.8.8.96',
            instance_id='manual-delete-finish-instance',
            service_started_at=now - timezone.timedelta(days=35),
            delete_at=delete_at,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='manual-delete-finish-asset',
            public_ip=order.public_ip,
            actual_expires_at=now - timezone.timedelta(days=5),
            status=CloudAsset.STATUS_STOPPED,
            is_active=False,
        )
        claim = claim_lifecycle_task_for_order(CloudLifecycleTask.TASK_DELETE, order, scheduled_at=delete_at, queue_status='scheduled_delete')
        finish_lifecycle_task(claim, ok=False, error='云端实例停止中')

        with patch('cloud.lifecycle.cloud_server_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._delete_instance', new=AsyncMock(return_value=(True, '删除成功'))), \
            patch('cloud.lifecycle._mark_deleted', new=AsyncMock()):
            result = run_shutdown_order_delete(order.id, enforce_schedule=False)

        task = CloudLifecycleTask.objects.get(id=claim.id)
        self.assertTrue(result['ok'])
        self.assertEqual(task.status, CloudLifecycleTask.STATUS_DONE)
        self.assertEqual(task.last_error, '')
        self.assertIsNotNone(task.completed_at)

    # 功能：验证订单固定 IP 回收入口会尊重数据库任务认领状态。
    def test_order_static_ip_release_skips_when_lifecycle_task_claimed(self):
        from cloud.lifecycle_execution import run_order_static_ip_release
        from cloud.lifecycle_tasks import claim_lifecycle_task_for_order

        now = timezone.now()
        recycle_at = now - timezone.timedelta(minutes=1)
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-CLAIM-RECYCLE-1',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='Singapore',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='deleted',
            previous_public_ip='8.8.8.93',
            static_ip_name='recycle-claimed-ip',
            service_started_at=now - timezone.timedelta(days=35),
            ip_recycle_at=recycle_at,
        )
        task_claim = claim_lifecycle_task_for_order(CloudLifecycleTask.TASK_RECYCLE, order, scheduled_at=recycle_at, queue_status='scheduled_recycle')

        with patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=True), \
            patch('cloud.lifecycle._release_order_static_ip') as release_mock:
            result = run_order_static_ip_release(order.id, queue_status='scheduled_recycle', enforce_schedule=True)

        self.assertIsNotNone(task_claim)
        self.assertFalse(result['ok'])
        self.assertIn('已被其他进程认领', result['error'])
        release_mock.assert_not_called()

    # 功能：验证订单固定 IP 回收入口会尊重资产级 IP 删除计划开关。
    def test_order_static_ip_release_respects_asset_ip_delete_disabled(self):
        from cloud.lifecycle_execution import run_order_static_ip_release

        now = timezone.now()
        recycle_at = now - timezone.timedelta(minutes=1)
        order = CloudServerOrder.objects.create(
            order_no='LIFECYCLE-RECYCLE-ASSET-OFF-1',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='Singapore',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='deleted',
            previous_public_ip='8.8.8.94',
            static_ip_name='recycle-asset-off-ip',
            service_started_at=now - timezone.timedelta(days=35),
            ip_recycle_at=recycle_at,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='Singapore',
            asset_name='recycle-asset-off-ip',
            public_ip='8.8.8.94',
            previous_public_ip='8.8.8.94',
            actual_expires_at=recycle_at,
            status=CloudAsset.STATUS_DELETED,
            provider_status='固定IP保留中-实例已删除',
            shutdown_enabled=True,
            ip_delete_enabled=False,
            is_active=False,
        )

        with patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=True), \
            patch('cloud.lifecycle._release_order_static_ip') as release_mock:
            result = run_order_static_ip_release(order.id, queue_status='scheduled_recycle', enforce_schedule=True)

        self.assertFalse(result['ok'])
        self.assertIn('IP 删除计划开关已关闭', result['error'])
        release_mock.assert_not_called()

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_group_cloud_server_list_is_scoped_to_current_group(self):
        first_user = TelegramUser.objects.create(tg_user_id=991997001, username='group_scope_first')
        second_user = TelegramUser.objects.create(tg_user_id=991997002, username='group_scope_second')
        first_group = TelegramGroupFilter.objects.create(chat_id=-1001887001, title='Scope First', enabled=True)
        second_group = TelegramGroupFilter.objects.create(chat_id=-1001887002, title='Scope Second', enabled=True)
        first_expires_at = timezone.now() + timezone.timedelta(days=5)
        second_expires_at = timezone.now() + timezone.timedelta(days=5)
        first_order = CloudServerOrder.objects.create(order_no='GROUP-SCOPE-FIRST-1', user=first_user, plan=self.plan, provider=self.plan.provider, region_code=self.plan.region_code, region_name=self.plan.region_name, plan_name=self.plan.plan_name, quantity=1, currency='USDT', total_amount='19.00', pay_amount='19.00', pay_method='balance', status='completed', public_ip='8.8.8.40')
        second_order = CloudServerOrder.objects.create(order_no='GROUP-SCOPE-SECOND-1', user=second_user, plan=self.plan, provider=self.plan.provider, region_code=self.plan.region_code, region_name=self.plan.region_name, plan_name=self.plan.plan_name, quantity=1, currency='USDT', total_amount='19.00', pay_amount='19.00', pay_method='balance', status='completed', public_ip='8.8.8.41')
        first_asset = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=first_order, user=first_user, provider=first_order.provider, region_code=first_order.region_code, region_name=first_order.region_name, asset_name='group-scope-first', public_ip='8.8.8.40', actual_expires_at=first_expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=first_group)
        second_asset = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=second_order, user=second_user, provider=second_order.provider, region_code=second_order.region_code, region_name=second_order.region_name, asset_name='group-scope-second', public_ip='8.8.8.41', actual_expires_at=second_expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=second_group)

        first_items = async_to_sync(list_group_cloud_servers)(first_group.chat_id)
        second_items = async_to_sync(list_group_cloud_servers)(second_group.chat_id)
        first_detail = async_to_sync(get_group_proxy_asset_detail)(first_asset.id, first_group.chat_id, 'asset')
        denied_detail = async_to_sync(get_group_proxy_asset_detail)(second_asset.id, first_group.chat_id, 'asset')

        self.assertEqual([item.public_ip for item in first_items], ['8.8.8.40'])
        self.assertEqual([item.public_ip for item in second_items], ['8.8.8.41'])
        self.assertIsNotNone(first_detail)
        self.assertIsNone(denied_detail)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_user_proxy_asset_detail_allows_same_bound_group_visibility(self):
        owner = TelegramUser.objects.create(tg_user_id=991997011, username='group_detail_owner')
        member = TelegramUser.objects.create(tg_user_id=991997012, username='group_detail_member')
        group = TelegramGroupFilter.objects.create(chat_id=-1001887011, title='Detail Shared Group', enabled=True)
        owner_expires_at = timezone.now() + timezone.timedelta(days=5)
        member_expires_at = timezone.now() + timezone.timedelta(days=5)
        owner_order = CloudServerOrder.objects.create(order_no='GROUP-DETAIL-OWNER-1', user=owner, plan=self.plan, provider=self.plan.provider, region_code=self.plan.region_code, region_name=self.plan.region_name, plan_name=self.plan.plan_name, quantity=1, currency='USDT', total_amount='19.00', pay_amount='19.00', pay_method='balance', status='completed', public_ip='8.8.8.44')
        member_order = CloudServerOrder.objects.create(order_no='GROUP-DETAIL-MEMBER-1', user=member, plan=self.plan, provider=self.plan.provider, region_code=self.plan.region_code, region_name=self.plan.region_name, plan_name=self.plan.plan_name, quantity=1, currency='USDT', total_amount='19.00', pay_amount='19.00', pay_method='balance', status='completed', public_ip='8.8.8.45')
        owner_asset = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=owner_order, user=owner, provider=owner_order.provider, region_code=owner_order.region_code, region_name=owner_order.region_name, asset_name='group-detail-owner', public_ip='8.8.8.44', actual_expires_at=owner_expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=group)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=member_order, user=member, provider=member_order.provider, region_code=member_order.region_code, region_name=member_order.region_name, asset_name='group-detail-member', public_ip='8.8.8.45', actual_expires_at=member_expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=group)

        member_items = async_to_sync(list_user_cloud_servers)(member.id)
        owner_detail = async_to_sync(get_user_proxy_asset_detail)(owner_asset.id, member.id, 'asset')

        self.assertIn(owner_asset.id, [getattr(item, 'asset_id', None) for item in member_items])
        self.assertIsNotNone(owner_detail)
        self.assertEqual(owner_detail.asset_id, owner_asset.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_same_bound_group_asset_renewal_uses_user_visibility(self):
        owner = TelegramUser.objects.create(tg_user_id=991997021, username='group_renew_owner')
        member = TelegramUser.objects.create(tg_user_id=991997022, username='group_renew_member')
        group = TelegramGroupFilter.objects.create(chat_id=-1001887021, title='Renew Shared Group', enabled=True)
        owner_expires_at = timezone.now() + timezone.timedelta(days=5)
        member_expires_at = timezone.now() + timezone.timedelta(days=5)
        owner_order = CloudServerOrder.objects.create(order_no='GROUP-RENEW-OWNER-1', user=owner, plan=self.plan, provider=self.plan.provider, region_code=self.plan.region_code, region_name=self.plan.region_name, plan_name=self.plan.plan_name, quantity=1, currency='USDT', total_amount='19.00', pay_amount='19.00', pay_method='balance', status='completed', public_ip='8.8.8.46', instance_id='group-renew-owner-instance')
        member_order = CloudServerOrder.objects.create(order_no='GROUP-RENEW-MEMBER-1', user=member, plan=self.plan, provider=self.plan.provider, region_code=self.plan.region_code, region_name=self.plan.region_name, plan_name=self.plan.plan_name, quantity=1, currency='USDT', total_amount='19.00', pay_amount='19.00', pay_method='balance', status='completed', public_ip='8.8.8.47')
        owner_asset = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=owner_order, user=owner, provider=owner_order.provider, region_code=owner_order.region_code, region_name=owner_order.region_name, asset_name='group-renew-owner', public_ip='8.8.8.46', instance_id='group-renew-owner-instance', actual_expires_at=owner_expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=group)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=member_order, user=member, provider=member_order.provider, region_code=member_order.region_code, region_name=member_order.region_name, asset_name='group-renew-member', public_ip='8.8.8.47', actual_expires_at=member_expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=group)
        unbound_asset = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_AWS_SYNC, user=owner, provider='aws_lightsail', region_code=self.plan.region_code, region_name=self.plan.region_name, asset_name='group-renew-unbound', public_ip='8.8.8.48', status=CloudAsset.STATUS_RUNNING, telegram_group=group)

        operation_order, operation_err = async_to_sync(ensure_cloud_asset_operation_order)(owner_asset.id, member.id)
        plan_asset, plans, plan_err = async_to_sync(list_cloud_asset_renewal_plans)(unbound_asset.id, member.id)
        renewal = async_to_sync(create_cloud_server_renewal_for_user)(owner_order.id, member.id, 31)
        owner_order.refresh_from_db()

        self.assertIsNone(operation_err)
        self.assertEqual(operation_order.id, owner_order.id)
        self.assertIsNone(plan_err)
        self.assertEqual(plan_asset.id, unbound_asset.id)
        self.assertGreaterEqual(len(plans), 1)
        self.assertIsNotNone(renewal)
        self.assertEqual(owner_order.status, 'renew_pending')
        self.assertEqual(owner_order.pay_method, 'address')
        self.assertEqual(owner_order.user_id, owner.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_group_auto_renew_bulk_toggle_is_scoped_to_current_group(self):
        first_user = TelegramUser.objects.create(tg_user_id=991997101, username='group_auto_first')
        second_user = TelegramUser.objects.create(tg_user_id=991997102, username='group_auto_second')
        first_group = TelegramGroupFilter.objects.create(chat_id=-1001887101, title='Auto Scope First', enabled=True)
        second_group = TelegramGroupFilter.objects.create(chat_id=-1001887102, title='Auto Scope Second', enabled=True)
        first_expires_at = timezone.now() + timezone.timedelta(days=5)
        second_expires_at = timezone.now() + timezone.timedelta(days=5)
        first_order = CloudServerOrder.objects.create(order_no='GROUP-AUTO-FIRST-1', user=first_user, plan=self.plan, provider=self.plan.provider, region_code=self.plan.region_code, region_name=self.plan.region_name, plan_name=self.plan.plan_name, quantity=1, currency='USDT', total_amount='19.00', pay_amount='19.00', pay_method='balance', status='completed', public_ip='8.8.8.42', auto_renew_enabled=False)
        second_order = CloudServerOrder.objects.create(order_no='GROUP-AUTO-SECOND-1', user=second_user, plan=self.plan, provider=self.plan.provider, region_code=self.plan.region_code, region_name=self.plan.region_name, plan_name=self.plan.plan_name, quantity=1, currency='USDT', total_amount='19.00', pay_amount='19.00', pay_method='balance', status='completed', public_ip='8.8.8.43', auto_renew_enabled=False)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=first_order, user=first_user, provider=first_order.provider, region_code=first_order.region_code, region_name=first_order.region_name, asset_name='group-auto-first', public_ip='8.8.8.42', actual_expires_at=first_expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=first_group)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=second_order, user=second_user, provider=second_order.provider, region_code=second_order.region_code, region_name=second_order.region_name, asset_name='group-auto-second', public_ip='8.8.8.43', actual_expires_at=second_expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=second_group)

        result = async_to_sync(set_group_cloud_server_auto_renew)(first_group.chat_id, True)
        first_order.refresh_from_db()
        second_order.refresh_from_db()

        self.assertEqual(result['updated'], 1)
        self.assertTrue(first_order.auto_renew_enabled)
        self.assertFalse(second_order.auto_renew_enabled)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_auto_renew_candidates_exclude_admin_notice_users(self):
        admin_user = TelegramUser.objects.create(tg_user_id=991998001, username='auto_admin', balance='999.00')
        other_user = TelegramUser.objects.create(tg_user_id=991998002, username='auto_group_member', balance='88.00')
        self.user.balance = '50.00'
        self.user.save(update_fields=['balance'])
        SiteConfig.set('bot_admin_chat_id', str(admin_user.tg_user_id))
        group = TelegramGroupFilter.objects.create(chat_id=-1001888991, title='Auto Renew Group', enabled=True)
        order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-EXCLUDE-ADMIN-1',
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
            public_ip='8.8.8.30',
            service_started_at=timezone.now(),
            auto_renew_enabled=True,
        )
        order_expires_at = timezone.now() + timezone.timedelta(days=2)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=order, user=self.user, provider=order.provider, region_code=order.region_code, region_name=order.region_name, asset_name='auto-renew-owner', public_ip='8.8.8.30', actual_expires_at=order_expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=group)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, user=admin_user, provider=order.provider, region_code=order.region_code, region_name=order.region_name, asset_name='auto-renew-admin', public_ip='8.8.8.31', status=CloudAsset.STATUS_RUNNING, telegram_group=group)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, user=other_user, provider=order.provider, region_code=order.region_code, region_name=order.region_name, asset_name='auto-renew-member', public_ip='8.8.8.32', status=CloudAsset.STATUS_RUNNING, telegram_group=group)

        candidates = _auto_renew_candidate_users(order)
        candidate_ids = [user.id for user in candidates]
        balance_text = '\n'.join(_group_balance_lines_for_orders([order]))

        self.assertNotIn(admin_user.id, candidate_ids)
        self.assertIn(self.user.id, candidate_ids)
        self.assertIn(other_user.id, candidate_ids)
        self.assertNotIn('auto_admin', balance_text)
        self.assertIn('svc_test', balance_text)
        self.assertIn('auto_group_member', balance_text)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_auto_renew_candidates_exclude_primary_admin_user(self):
        admin_user = TelegramUser.objects.create(tg_user_id=991998003, username='primary_admin', balance='999.00')
        member_user = TelegramUser.objects.create(tg_user_id=991998004, username='primary_group_member', balance='50.00')
        SiteConfig.set('bot_admin_chat_id', str(admin_user.tg_user_id))
        group = TelegramGroupFilter.objects.create(chat_id=-1001888992, title='Primary Admin Group', enabled=True)
        order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-EXCLUDE-PRIMARY-ADMIN-1',
            user=admin_user,
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
            public_ip='8.8.8.33',
            service_started_at=timezone.now(),
            auto_renew_enabled=True,
        )
        order_expires_at = timezone.now() + timezone.timedelta(days=2)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=order, user=admin_user, provider=order.provider, region_code=order.region_code, region_name=order.region_name, asset_name='auto-renew-primary-admin', public_ip='8.8.8.33', actual_expires_at=order_expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=group)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, user=member_user, provider=order.provider, region_code=order.region_code, region_name=order.region_name, asset_name='auto-renew-primary-member', public_ip='8.8.8.34', status=CloudAsset.STATUS_RUNNING, telegram_group=group)

        candidates = _auto_renew_candidate_users(order)

        self.assertEqual([user.id for user in candidates], [member_user.id])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_auto_renew_group_member_can_pay_when_owner_balance_insufficient(self):
        owner = self.user
        owner.balance = Decimal('0.00')
        owner.save(update_fields=['balance', 'updated_at'])
        member = TelegramUser.objects.create(tg_user_id=991998005, username='payer_group_member', balance=Decimal('100.00'))
        group = TelegramGroupFilter.objects.create(chat_id=-1001888993, title='Auto Renew Payer Group', enabled=True)
        expires_at = timezone.now() + timezone.timedelta(hours=8)
        order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-GROUP-PAYER-1',
            user=owner,
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
            public_ip='8.8.8.35',
            instance_id='auto-renew-group-payer',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            suspend_at=expires_at + timezone.timedelta(days=1),
            auto_renew_enabled=True,
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, order=order, user=owner, provider=order.provider, region_code=order.region_code, region_name=order.region_name, asset_name='auto-renew-group-owner', public_ip='8.8.8.35', actual_expires_at=expires_at, status=CloudAsset.STATUS_RUNNING, telegram_group=group)
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER, source=CloudAsset.SOURCE_ORDER, user=member, provider=order.provider, region_code=order.region_code, region_name=order.region_name, asset_name='auto-renew-group-member', public_ip='8.8.8.36', status=CloudAsset.STATUS_RUNNING, telegram_group=group)

        renewed, err, balance_change = async_to_sync(_run_auto_renew)(order.id)

        order.refresh_from_db()
        owner.refresh_from_db()
        member.refresh_from_db()
        self.assertIsNone(err)
        self.assertEqual(getattr(renewed, 'id', None), order.id)
        self.assertEqual(order.status, 'completed')
        self.assertEqual(owner.balance, Decimal('0.000000'))
        self.assertEqual(member.balance, Decimal('81.000000'))
        self.assertEqual(balance_change['payer_user_id'], member.id)

    # 功能：验证自动续费拿到订单锁后会复核到期窗口，避免旧任务把已续期订单改回待续费。
    def test_run_auto_renew_skips_when_asset_expiry_moved_out_of_due_window(self):
        from orders.models import BalanceLedger

        self.user.balance = Decimal('100.00')
        self.user.save(update_fields=['balance', 'updated_at'])
        expires_at = timezone.now() + timezone.timedelta(days=5)
        order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-SKIP-FUTURE-EXPIRY-1',
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
            public_ip='8.8.8.36',
            instance_id='auto-renew-skip-future-expiry',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            suspend_at=expires_at + timezone.timedelta(days=1),
            auto_renew_enabled=True,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='auto-renew-skip-future-expiry',
            public_ip='8.8.8.36',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
        )

        renewed, err, balance_change = async_to_sync(_run_auto_renew)(order.id)

        order.refresh_from_db()
        self.user.refresh_from_db()
        self.assertIsNone(renewed)
        self.assertEqual(err, '未到自动续费时间，跳过本轮自动续费')
        self.assertEqual(balance_change, {})
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order.pay_method, 'balance')
        self.assertEqual(self.user.balance, Decimal('100.000000'))
        self.assertFalse(BalanceLedger.objects.filter(related_type='cloud_order', related_id=order.id, type='cloud_order_balance_pay').exists())

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_send_order_notice_batch_prefers_bound_group_and_skips_private(self):
        group = TelegramGroupFilter.objects.create(
            chat_id=-1001888001,
            title='Notice Group',
            username='notice_group',
            enabled=True,
        )
        expires_at = timezone.now() + timezone.timedelta(days=3)
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-GROUP-FIRST-1',
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
            public_ip='8.8.8.10',
            service_started_at=timezone.now(),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='notice-group-first-asset',
            public_ip='8.8.8.10',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            telegram_group=group,
        )
        private_sent = []
        group_sent = []

        # 功能：处理 云资产、云订单和生命周期 中的 fake notify 业务流程。
        async def fake_notify(user_id, text, reply_markup=None):
            private_sent.append((user_id, text))
            return True

        # 功能：处理 云资产、云订单和生命周期 中的 fake notify target 业务流程。
        async def fake_notify_target(chat_id, text, reply_markup=None):
            group_sent.append((chat_id, text))
            return True

        result = async_to_sync(_send_order_notice_batch)(
            event='renew_notice_batch',
            field_name='renew_notice_sent_at',
            notify=fake_notify,
            notify_target=fake_notify_target,
            target_chat_id=group.chat_id,
            user_id=self.user.id,
            orders=[order],
            payload={'text': 'hello group', 'order_ids': [order.id], 'first_order_id': order.id, 'count': 1},
        )

        order.refresh_from_db()
        self.assertTrue(result)
        self.assertEqual(group_sent, [(group.chat_id, 'hello group')])
        self.assertEqual(private_sent, [])
        self.assertIsNotNone(order.renew_notice_sent_at)
        log = CloudUserNoticeLog.objects.get(event_type='renew_notice_batch', order=order)
        self.assertTrue(log.delivered)
        self.assertEqual(log.target_chat_id, group.chat_id)
        self.assertEqual(log.extra['notice_target'], 'telegram_group')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_send_order_notice_batch_falls_back_private_when_group_fails(self):
        expires_at = timezone.now() + timezone.timedelta(days=3)
        order = CloudServerOrder.objects.create(
            order_no='NOTICE-GROUP-FALLBACK-1',
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
            public_ip='8.8.8.11',
            service_started_at=timezone.now(),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='notice-group-fallback-asset',
            public_ip='8.8.8.11',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
        )
        private_sent = []
        group_sent = []

        # 功能：处理 云资产、云订单和生命周期 中的 fake notify 业务流程。
        async def fake_notify(user_id, text, reply_markup=None):
            private_sent.append((user_id, text))
            return True

        # 功能：处理 云资产、云订单和生命周期 中的 fake notify target 业务流程。
        async def fake_notify_target(chat_id, text, reply_markup=None):
            group_sent.append((chat_id, text))
            return False

        result = async_to_sync(_send_order_notice_batch)(
            event='renew_notice_batch',
            field_name='renew_notice_sent_at',
            notify=fake_notify,
            notify_target=fake_notify_target,
            target_chat_id=-1001888002,
            user_id=self.user.id,
            orders=[order],
            payload={'text': 'hello fallback', 'order_ids': [order.id], 'first_order_id': order.id, 'count': 1},
        )

        order.refresh_from_db()
        self.assertTrue(result)
        self.assertEqual(group_sent, [(-1001888002, 'hello fallback')])
        self.assertEqual(private_sent, [(self.user.id, 'hello fallback')])
        self.assertIsNotNone(order.renew_notice_sent_at)
        logs = list(CloudUserNoticeLog.objects.filter(event_type='renew_notice_batch', order=order).order_by('id'))
        self.assertEqual(len(logs), 2)
        self.assertFalse(logs[0].delivered)
        self.assertEqual(logs[0].target_chat_id, -1001888002)
        self.assertTrue(logs[1].delivered)
        self.assertIsNone(logs[1].target_chat_id)
        self.assertEqual(logs[1].extra['notice_target'], 'private')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_daily_expiry_summary_uses_real_cloud_status_and_target_config(self):
        self.user.first_name = '张三'
        self.user.save(update_fields=['first_name'])
        now = timezone.now()
        today_expires_at = timezone.make_aware(
            timezone.datetime.combine(timezone.localdate(now), timezone.datetime.min.time().replace(hour=9)),
            timezone.get_current_timezone(),
        )
        order_future_expires_at = now + timezone.timedelta(days=9)
        today_order = CloudServerOrder.objects.create(
            order_no='DAILY-EXPIRY-TODAY-1',
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
            public_ip='10.10.10.10',
            service_started_at=now - timezone.timedelta(days=30),
        )
        today_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=today_order,
            user=self.user,
            provider=today_order.provider,
            region_code=today_order.region_code,
            region_name=today_order.region_name,
            asset_name='daily-expiry-today',
            public_ip='10.10.10.10',
            actual_expires_at=today_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='running',
        )
        future_asset_order = CloudServerOrder.objects.create(
            order_no='DAILY-EXPIRY-FUTURE-ASSET-1',
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
            public_ip='10.10.10.12',
            service_started_at=now - timezone.timedelta(days=30),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=future_asset_order,
            user=self.user,
            provider=future_asset_order.provider,
            region_code=future_asset_order.region_code,
            region_name=future_asset_order.region_name,
            asset_name='daily-expiry-future-asset',
            public_ip='10.10.10.12',
            actual_expires_at=order_future_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='running',
        )
        expired_order = CloudServerOrder.objects.create(
            order_no='DAILY-EXPIRY-EXPIRED-1',
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
            public_ip='10.10.10.11',
            service_started_at=now - timezone.timedelta(days=60),
        )
        expired_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=expired_order,
            user=self.user,
            provider=expired_order.provider,
            region_code=expired_order.region_code,
            region_name=expired_order.region_name,
            asset_name='daily-expiry-expired',
            public_ip='10.10.10.11',
            actual_expires_at=now - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_STOPPED,
            provider_status='stopped',
        )
        SiteConfig.set('cloud_daily_expiry_summary_enabled', '1')
        SiteConfig.set('cloud_daily_expiry_summary_chat_ids', '10001')
        sent = []

        # 功能：处理 云资产、云订单和生命周期 中的 fake notify target 业务流程。
        async def fake_notify_target(chat_id, text, reply_markup=None):
            sent.append((chat_id, text))
            return True

        with patch('cloud.lifecycle.sync_server_status_tick', new_callable=AsyncMock) as sync_mock:
            result = async_to_sync(daily_expiry_summary_tick)(notify_target=fake_notify_target)

        self.assertEqual(result['sent'], 1)
        sync_mock.assert_not_called()
        self.assertEqual(len(sent), 2)
        self.assertIn('🟡 今日到期服务器', sent[0][1])
        self.assertIn('状态来自数据库当前记录。', sent[0][1])
        self.assertIn('今日到期: 1 台｜已经到期: 1 台', sent[0][1])
        self.assertIn('所属用户: svc_test｜姓名: 张三', sent[0][1])
        self.assertIn('IP: <code>10.10.10.10</code>', sent[0][1])
        self.assertIn('状态: 正在运行', sent[0][1])
        self.assertIn('🔴 已经过期服务器', sent[1][1])
        self.assertIn('所属用户: svc_test｜姓名: 张三', sent[1][1])
        self.assertIn('IP: <code>10.10.10.11</code>', sent[1][1])
        self.assertIn('状态: 已关机', sent[1][1])
        self.assertNotIn('10.10.10.12', '\n'.join(text for _, text in sent))
        self.assertNotIn('已截断', '\n'.join(text for _, text in sent))
        log = CloudUserNoticeLog.objects.get(event_type='daily_expiry_summary')
        self.assertTrue(log.delivered)
        self.assertEqual(log.target_chat_id, 10001)
        self.assertEqual(log.extra['today_count'], 1)
        self.assertEqual(log.extra['expired_count'], 1)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_tasks_overview_exposes_click_paths_for_entry_and_order_number(self):
        expires_at = timezone.now() + timezone.timedelta(days=5)
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
            auto_renew_enabled=True,
        )
        self._create_auto_renew_asset(order, expires_at=expires_at)
        staff_user = get_user_model().objects.create_user(username='staff_api_2', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/tasks/')
        self._attach_bearer_session(request, staff_user)

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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_auto_renew_retry_task_waits_for_recharge_then_retries(self):
        from cloud.lifecycle import _run_auto_renew_retry_task

        expires_at = timezone.now() + timezone.timedelta(hours=8)
        self.user.balance = Decimal('0.00')
        self.user.save(update_fields=['balance', 'updated_at'])
        order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-RETRY-AFTER-RECHARGE-1',
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
            public_ip='6.6.6.20',
            instance_id='auto-renew-retry-instance',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            suspend_at=expires_at + timezone.timedelta(days=1),
            expired_at=timezone.now() + timezone.timedelta(minutes=30),
            auto_renew_enabled=True,
        )
        self._create_auto_renew_asset(order, expires_at=expires_at)

        enqueued = async_to_sync(_enqueue_auto_renew_retry)(order.id, ip=order.public_ip, error='USDT 余额不足', balance_change={'candidate_count': 1})
        self.assertTrue(enqueued)
        task = CloudAutoRenewRetryTask.objects.get(order=order, status=CloudAutoRenewRetryTask.STATUS_PENDING)
        task.next_check_at = timezone.now() - timezone.timedelta(seconds=1)
        task.save(update_fields=['next_check_at', 'updated_at'])

        retried = async_to_sync(_process_auto_renew_retry_tasks)()
        task.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(retried, 0)
        self.assertEqual(task.status, CloudAutoRenewRetryTask.STATUS_PENDING)
        self.assertEqual(task.attempts, 1)
        self.assertEqual(order.status, 'renew_pending')

        duplicate_result = async_to_sync(_run_auto_renew_retry_task)(task.id)
        task.refresh_from_db()
        self.assertIsNone(duplicate_result)
        self.assertEqual(task.attempts, 1)

        self.user.balance = Decimal('100.00')
        self.user.save(update_fields=['balance', 'updated_at'])
        task.next_check_at = timezone.now() - timezone.timedelta(seconds=1)
        task.save(update_fields=['next_check_at', 'updated_at'])

        retried = async_to_sync(_process_auto_renew_retry_tasks)()
        task.refresh_from_db()
        order.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(retried, 1)
        self.assertEqual(task.status, CloudAutoRenewRetryTask.STATUS_SUCCEEDED)
        self.assertEqual(order.status, 'completed')
        self.assertEqual(self.user.balance, Decimal('81.000000'))
        self.assertTrue(CloudAutoRenewPatrolLog.objects.filter(order=order, is_success=True).exists())

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_task_center_counts_pending_auto_renew_retry_tasks(self):
        expires_at = timezone.now() + timezone.timedelta(days=5)
        order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-TASK-CENTER-RETRY-1',
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
            public_ip='6.6.6.21',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            suspend_at=expires_at + timezone.timedelta(days=1),
            auto_renew_enabled=True,
        )
        self._create_auto_renew_asset(order, expires_at=expires_at)
        CloudAutoRenewRetryTask.objects.create(
            order=order,
            user=self.user,
            order_no=order.order_no,
            ip=order.public_ip,
            status=CloudAutoRenewRetryTask.STATUS_PENDING,
            failure_reason='USDT 余额不足',
            attempts=1,
            next_check_at=timezone.now() + timezone.timedelta(minutes=10),
        )

        payload = task_center_payload()
        auto_renew = next(section for section in payload['sections'] if section['key'] == 'auto_renew')
        retry_item = next(item for item in auto_renew['items'] if item['order_no'] == order.order_no)

        self.assertGreaterEqual(auto_renew['total'], 1)
        self.assertGreaterEqual(auto_renew['active'], 1)
        self.assertGreaterEqual(auto_renew['warning'], 1)
        self.assertEqual(auto_renew['status_counts']['retry_pending'], 1)
        self.assertEqual(retry_item['execution_status'], 'retry_pending')
        self.assertEqual(retry_item['detail_path'], f'/admin/cloud-orders/{order.id}')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_update_cloud_asset_price_restores_auto_renew_pending_state(self):
        expires_at = timezone.now() + timezone.timedelta(hours=8)
        order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-PRICE-FIX-1',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='0.00',
            pay_amount='0.00',
            pay_method='address',
            status='renew_pending',
            public_ip='6.6.6.10',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            suspend_at=expires_at + timezone.timedelta(days=1),
            expired_at=timezone.now() + timezone.timedelta(minutes=30),
            auto_renew_enabled=True,
            auto_renew_failure_notice_sent_at=timezone.now(),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='auto-renew-price-fix-proxy',
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
        )
        CloudAutoRenewPatrolLog.objects.create(
            order=order,
            user=self.user,
            batch_id='price-missing-batch',
            order_no=order.order_no,
            ip=order.public_ip,
            provider=order.provider,
            user_display_name='svc_test',
            username_label='@svc_test',
            tg_user_id=self.user.tg_user_id,
            is_success=False,
            failure_reason='该代理缺少续费价格，请先在后台代理列表填写人工价格。',
        )
        staff_user = get_user_model().objects.create_user(username='staff_auto_renew_price_fix', password='x', is_staff=True, is_superuser=True)
        before_request = RequestFactory().get('/api/admin/tasks/')
        self._attach_bearer_session(before_request, staff_user)
        before_payload = json.loads(tasks_overview(before_request).content)
        before_pinned = next(item for item in (before_payload.get('data') or before_payload) if item['id'] == -10001)
        self.assertEqual(before_pinned['execution_status'], 'auto_renew_failed')

        request = RequestFactory().patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({'price': '29.00'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        self._attach_bearer_session(request, staff_user)
        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal('29.00'))
        self.assertEqual(order.pay_amount, Decimal('29.00'))
        self.assertIsNone(order.auto_renew_failure_notice_sent_at)
        after_request = RequestFactory().get('/api/admin/tasks/')
        self._attach_bearer_session(after_request, staff_user)
        after_payload = json.loads(tasks_overview(after_request).content)
        after_pinned = next(item for item in (after_payload.get('data') or after_payload) if item['id'] == -10001)
        self.assertEqual(after_pinned['execution_status'], 'auto_renew_pending')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_update_cloud_asset_expiry_refreshes_order_lifecycle(self):
        old_expires_at = timezone.now() + timezone.timedelta(days=8)
        new_expires_at = timezone.now() + timezone.timedelta(days=40)
        plan = CloudServerPlan.objects.create(
            provider=CloudServerPlan.PROVIDER_ALIYUN_ECS,
            region_code='cn-hongkong',
            region_name='香港',
            plan_name='Aliyun Lifecycle Test',
            price='19.00',
            currency='USDT',
            is_active=True,
        )
        order = CloudServerOrder.objects.create(
            order_no='ASSET-EXPIRY-LIFECYCLE-FIX-1',
            user=self.user,
            plan=plan,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='6.6.6.12',
            service_started_at=timezone.now() - timezone.timedelta(days=20),
            renew_notice_sent_at=timezone.now(),
            auto_renew_notice_sent_at=timezone.now(),
            auto_renew_failure_notice_sent_at=timezone.now(),
            delete_notice_sent_at=timezone.now(),
            recycle_notice_sent_at=timezone.now(),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            asset_name='asset-expiry-lifecycle-fix',
            public_ip=order.public_ip,
            actual_expires_at=old_expires_at,
            status=CloudAsset.STATUS_RUNNING,
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_expiry_lifecycle', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({'actual_expires_at': new_expires_at.isoformat()}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        expected_lifecycle = compute_order_lifecycle_fields(new_expires_at)
        self.assertEqual(asset.actual_expires_at, new_expires_at)
        self.assertEqual(order.renew_grace_expires_at, expected_lifecycle['renew_grace_expires_at'])
        self.assertEqual(order.suspend_at, expected_lifecycle['suspend_at'])
        self.assertEqual(order.delete_at, expected_lifecycle['delete_at'])
        self.assertEqual(order.ip_recycle_at, expected_lifecycle['ip_recycle_at'])
        self.assertIsNone(order.renew_notice_sent_at)
        self.assertIsNone(order.auto_renew_notice_sent_at)
        self.assertIsNone(order.auto_renew_failure_notice_sent_at)
        self.assertIsNone(order.delete_notice_sent_at)
        self.assertIsNone(order.recycle_notice_sent_at)

        clear_request = RequestFactory().patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({'actual_expires_at': None}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        self._attach_bearer_session(clear_request, staff_user)

        clear_response = update_cloud_asset(clear_request, asset.id)

        self.assertEqual(clear_response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertIsNone(asset.actual_expires_at)
        self.assertIsNone(order.renew_grace_expires_at)
        self.assertIsNone(order.suspend_at)
        self.assertIsNone(order.delete_at)
        self.assertIsNone(order.ip_recycle_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_renewal_balance_payment_uses_latest_proxy_price(self):
        self.user.balance = Decimal('100.000000')
        self.user.save(update_fields=['balance', 'updated_at'])
        expires_at = timezone.now() + timezone.timedelta(hours=8)
        order = CloudServerOrder.objects.create(
            order_no='RENEW-LATEST-PROXY-PRICE-1',
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
            public_ip='6.6.6.11',
            instance_id='i-renew-latest-price',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            suspend_at=expires_at + timezone.timedelta(days=1),
            expired_at=timezone.now() + timezone.timedelta(minutes=30),
            auto_renew_enabled=True,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='renew-latest-proxy-price',
            public_ip=order.public_ip,
            instance_id=order.instance_id,
            actual_expires_at=expires_at,
            price=Decimal('29.00'),
        )

        renewed, err = async_to_sync(pay_cloud_server_renewal_with_balance)(order.id, self.user.id, 'USDT', 31)

        self.assertIsNone(err)
        self.assertIsNotNone(renewed)
        order.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal('29.00'))
        self.assertEqual(order.pay_amount, Decimal('29.00'))
        self.assertEqual(self.user.balance, Decimal('71.000000'))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_asset_detail_exposes_related_order_click_path(self):
        expires_at = timezone.now() + timezone.timedelta(days=8)
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
            actual_expires_at=expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_api_3', password='x', is_staff=True)
        request = RequestFactory().get(f'/api/admin/cloud-assets/{asset.id}/')
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)
        payload = json.loads(response.content)
        data = payload.get('data') or payload

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['order_detail_path'], f'/admin/cloud-orders/{order.id}')
        self.assertEqual(data['order_link_path'], f'/admin/cloud-orders/{order.id}')
        self.assertEqual(data['related_order']['order_link_path'], f'/admin/cloud-orders/{order.id}')

    # 功能：验证后台资产详情返回三个生命周期单项开关，避免页面刷新后错显默认开启。
    def test_cloud_asset_detail_exposes_lifecycle_switches(self):
        order = CloudServerOrder.objects.create(
            order_no='ASSET-DETAIL-LIFECYCLE-SWITCHES',
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
            public_ip='2.2.2.22',
            service_started_at=timezone.now(),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='asset-detail-lifecycle-switches',
            public_ip='2.2.2.22',
            actual_expires_at=timezone.now() + timezone.timedelta(days=8),
            shutdown_enabled=False,
            server_delete_enabled=False,
            ip_delete_enabled=False,
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_switches', password='x', is_staff=True)
        request = RequestFactory().get(f'/api/admin/cloud-assets/{asset.id}/')
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)
        payload = json.loads(response.content)
        data = payload.get('data') or payload

        self.assertEqual(response.status_code, 200)
        self.assertFalse(data['shutdown_enabled'])
        self.assertFalse(data['server_delete_enabled'])
        self.assertFalse(data['ip_delete_enabled'])

    # 功能：验证已删除资产详情不会继续暴露历史代理链路、secret 和完整公网 IP。
    def test_deleted_cloud_asset_detail_masks_proxy_links_and_history_notes(self):
        secret = 'abcdef0123456789abcdef0123456789'
        mtproxy_link = f'tg://proxy?server=198.51.100.77&port=443&secret={secret}'
        socks_link = 'socks5://user:password@198.51.100.77:1080'
        order = CloudServerOrder.objects.create(
            order_no='ASSET-DETAIL-DELETED-MASK',
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
            public_ip='198.51.100.77',
            previous_public_ip='198.51.100.76',
            mtproxy_host='198.51.100.77',
            mtproxy_port=443,
            mtproxy_secret=secret,
            mtproxy_link=mtproxy_link,
            proxy_links=[
                {'name': '主代理 mtg', 'url': mtproxy_link, 'server': '198.51.100.77', 'port': '443', 'secret': secret},
                {'name': '备用 socks5', 'url': socks_link, 'server': '198.51.100.77', 'port': '1080'},
            ],
            provision_note=f'创建完成：{mtproxy_link}\n备用：{socks_link}\n旧IP=198.51.100.76 secret={secret}',
            service_started_at=timezone.now(),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='asset-detail-deleted-mask',
            public_ip='198.51.100.77',
            previous_public_ip='198.51.100.76',
            mtproxy_host='198.51.100.77',
            mtproxy_port=443,
            mtproxy_secret=secret,
            mtproxy_link=mtproxy_link,
            proxy_links=list(order.proxy_links),
            note=f'资产备注：{mtproxy_link} {socks_link} secret={secret} 198.51.100.76',
            status=CloudAsset.STATUS_DELETED,
            is_active=False,
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        CloudIpLog.objects.create(
            event_type=CloudIpLog.EVENT_DELETED,
            order=order,
            asset=asset,
            user=self.user,
            order_no=order.order_no,
            asset_name=asset.asset_name,
            public_ip='198.51.100.77',
            previous_public_ip='198.51.100.76',
            note=f'删除日志：{mtproxy_link}\n备用：{socks_link}\nsecret={secret}\nIP：198.51.100.77；旧IP=198.51.100.76',
        )
        staff_user = get_user_model().objects.create_user(username='staff_deleted_asset_mask', password='x', is_staff=True)
        request = RequestFactory().get(f'/api/admin/cloud-assets/{asset.id}/')
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content.decode())['data']
        response_text = response.content.decode()
        self.assertEqual(data['status'], CloudAsset.STATUS_DELETED)
        self.assertEqual(data['mtproxy_link'], '')
        self.assertEqual(data['proxy_links'], [])
        self.assertEqual(data['public_ip'], '198.51.100.*')
        self.assertEqual(data['previous_public_ip'], '198.51.100.*')
        self.assertEqual(data['mtproxy_host'], '198.51.100.*')
        self.assertIn('代理链路已脱敏', data['provision_note'])
        self.assertIn('secret已脱敏', data['provision_note'])
        self.assertTrue(data['ip_logs'])
        self.assertNotIn(mtproxy_link, response_text)
        self.assertNotIn(socks_link, response_text)
        self.assertNotIn(secret, response_text)
        self.assertNotIn('secret=', response_text)
        self.assertNotIn('198.51.100.77', response_text)
        self.assertNotIn('198.51.100.76', response_text)

    # 功能：验证后台资产详情只展示资产自己的到期事实，不从同订单其他资产兜底。
    def test_cloud_asset_detail_does_not_fallback_to_order_asset_expiry(self):
        primary_expires_at = timezone.now() + timezone.timedelta(days=8)
        order = CloudServerOrder.objects.create(
            order_no='ASSET-DETAIL-NO-ORDER-EXPIRY',
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
            public_ip='2.2.2.20',
            service_started_at=timezone.now(),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='asset-detail-primary-expiry',
            public_ip='2.2.2.20',
            actual_expires_at=primary_expires_at,
            sort_order=10,
        )
        detail_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='asset-detail-empty-expiry',
            public_ip='2.2.2.21',
            actual_expires_at=None,
            sort_order=0,
        )
        staff_user = get_user_model().objects.create_user(username='staff_api_asset_expiry_fact', password='x', is_staff=True)
        request = RequestFactory().get(f'/api/admin/cloud-assets/{detail_asset.id}/')
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, detail_asset.id)
        payload = json.loads(response.content)
        data = payload.get('data') or payload

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(data['actual_expires_at'])
        detail_asset.refresh_from_db()
        self.assertIsNone(detail_asset.actual_expires_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_asset_detail_exposes_history_orders_with_click_paths(self):
        newer_expires_at = timezone.now() + timezone.timedelta(days=20)
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
            actual_expires_at=newer_expires_at,
        )
        staff_user = get_user_model().objects.create_user(username='staff_api_4', password='x', is_staff=True)
        request = RequestFactory().get(f'/api/admin/cloud-assets/{asset.id}/')
        self._attach_bearer_session(request, staff_user)

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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_auto_renew_task_detail_includes_due_retry_and_fallback_items(self):
        due_expires_at = timezone.now() + timezone.timedelta(hours=12)
        retry_expires_at = timezone.now() + timezone.timedelta(days=2)
        fallback_expires_at = timezone.now() - timezone.timedelta(hours=1)
        resolved_expires_at = timezone.now() + timezone.timedelta(days=20)
        deleted_expires_at = timezone.now() - timezone.timedelta(hours=3)
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
            auto_renew_enabled=True,
        )
        resolved_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-RESOLVED-1',
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
            public_ip='10.0.0.4',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            auto_renew_enabled=True,
        )
        deleted_asset_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-DELETED-ASSET-1',
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
            public_ip='10.0.0.5',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            auto_renew_enabled=True,
        )
        self._create_auto_renew_asset(due_order, expires_at=due_expires_at)
        self._create_auto_renew_asset(retry_order, expires_at=retry_expires_at)
        self._create_auto_renew_asset(fallback_order, expires_at=fallback_expires_at)
        self._create_auto_renew_asset(resolved_order, expires_at=resolved_expires_at)
        self._create_auto_renew_asset(deleted_asset_order, status=CloudAsset.STATUS_DELETED, expires_at=deleted_expires_at)
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
        CloudAutoRenewPatrolLog.objects.create(
            order=resolved_order,
            user=self.user,
            batch_id='resolved-batch-1',
            order_no=resolved_order.order_no,
            ip=resolved_order.public_ip,
            provider=resolved_order.provider,
            user_display_name='svc_test',
            username_label='@svc_test',
            tg_user_id=self.user.tg_user_id,
            is_success=False,
            failure_reason='曾经失败',
        )
        CloudAutoRenewPatrolLog.objects.create(
            order=resolved_order,
            user=self.user,
            batch_id='resolved-batch-2',
            order_no=resolved_order.order_no,
            ip=resolved_order.public_ip,
            provider=resolved_order.provider,
            user_display_name='svc_test',
            username_label='@svc_test',
            tg_user_id=self.user.tg_user_id,
            is_success=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_auto_renew_detail', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/tasks/auto-renew/')
        self._attach_bearer_session(request, staff_user)

        response = auto_renew_task_detail(request)

        payload = json.loads(response.content)
        data = payload.get('data') or payload
        due_items = data['due_items']
        queue_status_map = {item['order_no']: item['queue_status'] for item in due_items}

        self.assertEqual(response.status_code, 200)
        self.assertEqual(queue_status_map[due_order.order_no], 'due_now')
        self.assertEqual(queue_status_map[retry_order.order_no], 'retry_failed')
        self.assertEqual(queue_status_map[fallback_order.order_no], 'fallback_retry')
        self.assertNotIn(resolved_order.order_no, queue_status_map)
        self.assertNotIn(deleted_asset_order.order_no, queue_status_map)
        retry_item = next(item for item in due_items if item['order_no'] == retry_order.order_no)
        self.assertEqual(retry_item['last_failure_reason'], '余额不足')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_auto_renew_detail_ignores_order_without_asset_expiry_fact(self):
        due_order = CloudServerOrder.objects.create(
            order_no='AUTO-RENEW-NO-ASSET-1',
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
            public_ip='10.0.9.1',
            service_started_at=timezone.now() - timezone.timedelta(days=30),
            auto_renew_enabled=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_auto_renew_no_asset', password='x', is_staff=True)
        request = RequestFactory().get('/api/admin/tasks/auto-renew/')
        self._attach_bearer_session(request, staff_user)

        response = auto_renew_task_detail(request)

        payload = json.loads(response.content)
        data = payload.get('data') or payload
        queue_status_map = {item['order_no']: item['queue_status'] for item in data['due_items']}

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(due_order.order_no, queue_status_map)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_run_auto_renew_tasks_executes_due_retry_and_fallback_queue(self):
        due_expires_at = timezone.now() + timezone.timedelta(hours=8)
        retry_expires_at = timezone.now() + timezone.timedelta(days=1)
        fallback_expires_at = timezone.now() - timezone.timedelta(hours=2)
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
            auto_renew_enabled=True,
        )
        self._create_auto_renew_asset(due_order, expires_at=due_expires_at)
        self._create_auto_renew_asset(retry_order, expires_at=retry_expires_at)
        self._create_auto_renew_asset(fallback_order, expires_at=fallback_expires_at)
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
        staff_user = get_user_model().objects.create_user(username='staff_auto_renew_run', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().post('/api/admin/tasks/auto-renew/run/', data='{}', content_type='application/json')
        self._attach_bearer_session(request, staff_user)

        # 功能：处理 云资产、云订单和生命周期 中的 fake run auto renew 业务流程。
        def fake_run_auto_renew(order_id):
            order = CloudServerOrder.objects.get(id=order_id)
            if order_id == retry_order.id:
                return None, '余额不足', {'currency': 'USDT', 'amount': None}
            return order, None, {'currency': 'USDT', 'amount': Decimal('19.00'), 'before': Decimal('100.00'), 'after': Decimal('81.00'), 'payer_user_id': self.user.id}

        with patch('cloud.api_tasks._run_auto_renew', new=fake_run_auto_renew):
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_run_auto_renew_order_executes_single_order(self):
        expires_at = timezone.now() + timezone.timedelta(hours=4)
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
            auto_renew_enabled=True,
        )
        self._create_auto_renew_asset(order, expires_at=expires_at)
        staff_user = get_user_model().objects.create_user(username='staff_auto_renew_single', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().post(f'/api/admin/tasks/auto-renew/orders/{order.id}/run/', data='{}', content_type='application/json')
        self._attach_bearer_session(request, staff_user)

        # 功能：处理 云资产、云订单和生命周期 中的 fake run auto renew 业务流程。
        def fake_run_auto_renew(order_id):
            renewed = CloudServerOrder.objects.get(id=order_id)
            return renewed, None, {'currency': 'USDT', 'amount': Decimal('19.00'), 'before': Decimal('50.00'), 'after': Decimal('31.00'), 'payer_user_id': self.user.id}

        with patch('cloud.api_tasks._run_auto_renew', new=fake_run_auto_renew):
            response = run_auto_renew_order(request, order.id)

        payload = json.loads(response.content)
        data = payload.get('data') or payload

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['queue_status'], 'manual_single')
        self.assertTrue(data['items'][0]['ok'])
        self.assertTrue(CloudAutoRenewPatrolLog.objects.filter(batch_id=data['batch_id'], order=order).exists())

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            order=order,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='refresh-unattached-ip-server',
            provider_resource_id='aws-static-ip-refresh-1',
            public_ip='10.9.0.9',
            actual_expires_at=old_due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        staff_user = get_user_model().objects.create_user(username='staff_refresh_unattached_plan', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({'note': '未附加固定IP\n人工刷新删除计划'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        request = self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)

        asset.refresh_from_db()
        server.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertGreater(asset.actual_expires_at, old_due_at)
        self.assertEqual(server.actual_expires_at, asset.actual_expires_at)
        self.assertEqual(order.ip_recycle_at, asset.actual_expires_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        staff_user = get_user_model().objects.create_user(username='staff_rebound_manual', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({'instance_id': 'i-rebound-manual-1'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)

        asset.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(asset.instance_id, 'i-rebound-manual-1')
        self.assertEqual(asset.provider_status, '已重新绑定实例-待人工添加时间')
        self.assertIsNone(asset.actual_expires_at)
        self.assertTrue(asset.is_active)
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.note, '未附加固定IP')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_system_note_updates_preserve_manual_primary_record_notes(self):
        from cloud.services import _update_order_primary_records

        order = CloudServerOrder.objects.create(
            order_no='NOTE-APPEND-PRIMARY',
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
            provision_note='订单旧备注',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            public_ip='10.9.9.1',
            note='资产人工备注',
        )
        _update_order_primary_records(order, asset_updates={'note': '系统追加备注'})

        asset.refresh_from_db()
        self.assertEqual(asset.note, '资产人工备注')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_cloud_asset_user_binding_uses_asset_name_tg_id(self):
        user = TelegramUser.objects.create(
            tg_user_id=21989077,
            username='syira,hashyule111,sy168',
            first_name='蜗牛',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='20260522-21989077-15-o877',
            public_ip='10.9.9.10',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        resolved = sync_cloud_asset_user_binding(asset)

        asset.refresh_from_db()
        self.assertEqual(resolved.id, user.id)
        self.assertEqual(asset.user_id, user.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_cloud_asset_user_binding_persist_false_sets_in_memory_user(self):
        user = TelegramUser.objects.create(
            tg_user_id=21989080,
            username='sync_memory_user',
            first_name='同步内存用户',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='20260522-21989080-15-o880',
            public_ip='10.9.9.13',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        resolved = sync_cloud_asset_user_binding(asset, persist=False)

        self.assertEqual(resolved.id, user.id)
        self.assertEqual(asset.user_id, user.id)
        self.assertEqual(asset.user.id, user.id)
        self.assertIsNone(CloudAsset.objects.get(id=asset.id).user_id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_asset_get_payload_does_not_mutate_manual_asset_fields(self):
        TelegramUser.objects.create(
            tg_user_id=21989079,
            username='payload_get_user',
            first_name='只读详情用户',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='20260522-21989079-15-o879',
            public_ip='10.9.9.12',
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        staff_user = get_user_model().objects.create_user(username='staff_asset_get_readonly', password='x', is_staff=True)
        request = self._attach_bearer_session(
            self.factory.get(f'/api/admin/cloud-assets/{asset.id}/'),
            staff_user,
        )

        response = update_cloud_asset(request, asset.id)
        payload = json.loads(response.content.decode('utf-8'))['data']

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload['actual_expires_at'])
        asset.refresh_from_db()
        self.assertIsNone(asset.user_id)
        self.assertIsNone(asset.actual_expires_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_toggle_auto_renew_creates_operation_order_for_bound_asset_without_order(self):
        user = TelegramUser.objects.create(
            tg_user_id=21989078,
            username='auto_renew_user',
            first_name='自动续费用户',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='20260522-21989078-15-o878',
            instance_id='i-auto-renew-test',
            public_ip='10.9.9.11',
            actual_expires_at=timezone.now() + timezone.timedelta(days=10),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        sync_cloud_asset_user_binding(asset)
        asset.refresh_from_db()
        self.assertEqual(asset.user_id, user.id)
        order, err = async_to_sync(ensure_cloud_asset_operation_order)(asset.id, user.id, True)
        self.assertIsNone(err)
        self.assertIsNotNone(order)

        updated = async_to_sync(set_cloud_server_auto_renew_admin)(order.id, True)

        asset.refresh_from_db()
        self.assertTrue(updated.auto_renew_enabled)
        self.assertEqual(asset.order_id, order.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_manual_cloud_asset_note_edit_still_overwrites(self):
        order = CloudServerOrder.objects.create(
            order_no='NOTE-MANUAL-OVERWRITE',
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
            public_ip='10.9.9.2',
            status='completed',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            public_ip='10.9.9.2',
            status=CloudAsset.STATUS_RUNNING,
            note='旧人工备注',
            is_active=True,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            public_ip='10.9.9.2',
            status=CloudAsset.STATUS_RUNNING,
            note='旧服务器备注',
            is_active=True,
        )
        staff_user = get_user_model().objects.create_user(username='staff_manual_note_overwrite', password='x', is_staff=True, is_superuser=True)
        request = RequestFactory().patch(
            f'/api/admin/cloud-assets/{asset.id}/',
            data=json.dumps({'note': '人工改后的备注'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='',
        )
        self._attach_bearer_session(request, staff_user)

        response = update_cloud_asset(request, asset.id)

        self.assertEqual(response.status_code, 200)
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(asset.note, '人工改后的备注')
        self.assertEqual(server.note, '人工改后的备注')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_missing_confirmation_note_preserves_existing_note(self):
        from cloud.sync_safety import mark_missing_confirmation_pending, missing_confirmation_state

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            public_ip='10.9.9.3',
            provider_status='running',
            note='保留人工备注',
        )

        with patch('cloud.sync_safety.get_missing_confirmation_threshold', return_value=2):
            count, threshold = mark_missing_confirmation_pending(
                asset,
                old_public_ip='10.9.9.3',
                now_iso='2026-05-08T00:00:00+08:00',
                provider_status='云上未找到实例/IP',
                pending_status='云上未找到实例/IP-待确认',
            )

        self.assertEqual((count, threshold), (1, 2))
        self.assertEqual(asset.note, '保留人工备注')
        self.assertEqual(asset.provider_status, '云上未找到实例/IP-待确认')
        self.assertEqual(missing_confirmation_state(asset)['count'], 1)
        self.assertEqual(asset.sync_state['missing_confirmation']['old_public_ip'], '10.9.9.3')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_missing_delete_threshold_is_at_least_five(self):
        with patch('cloud.sync_safety.get_runtime_config', return_value='3'):
            self.assertEqual(get_missing_confirmation_threshold(), 5)
        with patch('cloud.sync_safety.get_runtime_config', return_value='0'):
            self.assertEqual(get_missing_confirmation_threshold(), 5)
        with patch('cloud.sync_safety.get_runtime_config', return_value='7'):
            self.assertEqual(get_missing_confirmation_threshold(), 7)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_missing_confirmation_requires_interval(self):
        from cloud.sync_safety import mark_missing_confirmation_pending, missing_confirmation_count

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            public_ip='10.9.9.4',
            provider_status='running',
            note='保留人工备注',
        )

        first_count, _ = mark_missing_confirmation_pending(
            asset,
            old_public_ip='10.9.9.4',
            now_iso='2026-05-08T00:00:00+08:00',
            provider_status='云上未找到实例/IP',
            pending_status='云上未找到实例/IP-待确认',
        )
        second_count, _ = mark_missing_confirmation_pending(
            asset,
            old_public_ip='10.9.9.4',
            now_iso='2026-05-08T00:01:00+08:00',
            provider_status='云上未找到实例/IP',
            pending_status='云上未找到实例/IP-待确认',
        )

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 1)
        self.assertEqual(asset.note, '保留人工备注')
        self.assertEqual(missing_confirmation_count(asset), 1)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unattached_ip_delete_items_expose_missing_confirmation_state(self):
        from cloud.sync_safety import mark_missing_confirmation_pending

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='confirming-unattached-static-ip',
            public_ip='5.5.5.22',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        mark_missing_confirmation_pending(
            asset,
            old_public_ip='5.5.5.22',
            now_iso='2026-05-08T00:00:00+08:00',
            provider_status='云上未找到实例/IP',
            pending_status='云上未找到实例/IP-待确认',
        )
        asset.save(update_fields=['provider_status', 'sync_state', 'updated_at'])

        items = _unattached_ip_delete_items(limit=20)
        row = next(item for item in items if item.get('public_ip') == '5.5.5.22' and not item.get('is_history'))

        self.assertEqual(row['missing_confirm_count'], 1)
        self.assertGreaterEqual(row['missing_confirm_threshold'], 5)
        self.assertGreaterEqual(row['missing_confirm_remaining'], 4)
        self.assertEqual(row['missing_confirm_interval_minutes'], 60)
        self.assertTrue(row['missing_confirm_checked_at'])
        self.assertTrue(row['missing_confirm_next_check_at'])
        self.assertIn('missing_confirming', row.get('quality_flags') or [])
        self.assertIn('缺失确认 1/', row.get('quality_label') or '')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_unattached_ip_show_confirmation_progress_in_state_and_note(self):
        from cloud.sync_safety import mark_missing_confirmation_pending

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='confirming-unattached-static-ip-lifecycle',
            public_ip='5.5.5.24',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        mark_missing_confirmation_pending(
            asset,
            old_public_ip='5.5.5.24',
            now_iso='2026-05-08T00:00:00+08:00',
            provider_status='云上未找到实例/IP',
            pending_status='云上未找到实例/IP-待确认',
        )
        asset.save(update_fields=['provider_status', 'sync_state', 'updated_at'])

        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_unattached_confirm_progress', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        row = next(item for item in data['ip_delete_plan_items'] if item.get('public_ip') == '5.5.5.24')

        self.assertEqual(row['resource_state_label'], '云上缺失待确认（第1/5次）')
        self.assertIn('第1/5次删除确认', row['display_note'])
        self.assertIn('第1/5次删除确认', row['blocked_reason'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_unattached_ip_show_delete_attempt_in_state_and_note(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='attempt-unattached-static-ip-lifecycle',
            public_ip='5.5.5.25',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            note=(
                '未附加固定IP\n'
                '未附加固定IP到期，AWS 固定 IP 真实释放失败: first\n'
                '未附加固定IP到期，AWS 固定 IP 真实释放失败: second'
            ),
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
        )

        items = _unattached_ip_delete_items(limit=20)
        direct_row = next(item for item in items if item.get('asset_id') == asset.id and not item.get('is_history'))
        self.assertEqual(direct_row['delete_attempt_count'], 2)
        self.assertEqual(direct_row['delete_next_attempt'], 3)
        self.assertEqual(direct_row['delete_attempt_label'], '已尝试2次，待第3次删除')

        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_unattached_delete_attempt', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']
        row = next(item for item in data['ip_delete_plan_items'] if item.get('asset_id') == asset.id)

        self.assertIn('已尝试2次，待第3次删除', row['resource_state_label'])
        self.assertIn('删除次数：已尝试2次，待第3次删除', row['display_note'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plans_read_cached_table_after_initial_refresh(self):
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='cached-unattached-static-ip-lifecycle',
            public_ip='5.5.5.26',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_cached_table', password='x', is_staff=True)
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': '1'})
        self._attach_bearer_session(request, staff_user)
        first_response = lifecycle_plans(request)
        self.assertEqual(json.loads(first_response.content)['data']['cache_mode'], 'refreshed')

        with patch('bot.api._sync_lifecycle_plan_table') as sync_mock:
            second_request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000})
            self._attach_bearer_session(second_request, staff_user)
            second_response = lifecycle_plans(second_request)

        sync_mock.assert_not_called()
        self.assertEqual(json.loads(second_response.content)['data']['cache_mode'], 'cached')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_plan_counts_match_proxy_list_assets(self):
        active_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='lifecycle-count-active',
            external_account_id='acct-lifecycle-count-active',
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        inactive_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='lifecycle-count-disabled',
            external_account_id='acct-lifecycle-count-disabled',
            access_key='C' * 20,
            secret_key='D' * 40,
            is_active=False,
        )
        server_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=active_account,
            account_label=cloud_account_label(active_account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='proxy-count-server',
            instance_id='proxy-count-server',
            public_ip='5.5.5.41',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            actual_expires_at=timezone.now() - timezone.timedelta(days=7),
        )
        ip_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=active_account,
            account_label=cloud_account_label(active_account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='proxy-count-static-ip',
            public_ip='5.5.5.42',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        aliyun_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            user=self.user,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            asset_name='proxy-count-aliyun',
            instance_id='i-proxy-count-aliyun',
            public_ip='5.5.5.43',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=inactive_account,
            account_label=cloud_account_label(inactive_account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='proxy-count-disabled-account',
            instance_id='proxy-count-disabled-account',
            public_ip='5.5.5.44',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
            actual_expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        staff_user = get_user_model().objects.create_user(username='staff_lifecycle_visible_counts', password='x', is_staff=True)
        list_request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'page_size': '100', 'risk_status': 'all'})
        self._attach_bearer_session(list_request, staff_user)
        list_response = cloud_assets_list(list_request)
        list_payload = json.loads(list_response.content.decode('utf-8'))['data']
        request = self.factory.get('/api/admin/tasks/plans/', {'limit': 1000, 'refresh': 1})
        self._attach_bearer_session(request, staff_user)

        response = lifecycle_plans(request)
        data = json.loads(response.content)['data']

        self.assertEqual(list_payload['total'], 4)
        self.assertEqual(data['source_asset_count'], list_payload['total'])
        self.assertEqual(data['server_asset_count'], 3)
        self.assertEqual(data['unattached_ip_count'], 1)
        self.assertEqual(data['source_asset_count'], data['server_asset_count'] + data['unattached_ip_count'])
        self.assertTrue(any(item.get('asset_id') == server_asset.id for item in data['shutdown_plan_items']))
        self.assertTrue(any(item.get('asset_id') == ip_asset.id for item in data['ip_delete_plan_items']))
        self.assertTrue(any(item.get('asset_name') == 'proxy-count-disabled-account' for item in data['shutdown_plan_items']))
        aliyun_row = next(item for item in data['shutdown_plan_items'] if item.get('asset_id') == aliyun_asset.id)
        self.assertEqual(aliyun_row['plan_state_label'], '只同步/自然释放')
        self.assertFalse(aliyun_row['should_execute'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unattached_ip_delete_items_hide_confirmed_missing_ip(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='confirmed-missing-unattached-static-ip',
            public_ip='5.5.5.23',
            status=CloudAsset.STATUS_UNKNOWN,
            is_active=False,
            provider_status='云上未找到实例/IP',
            sync_state={
                'missing_confirmation': {
                    'status': 'confirmed',
                    'count': 5,
                    'threshold': 5,
                    'checked_at': timezone.now().isoformat(),
                },
            },
            note='人工备注',
            actual_expires_at=timezone.now() + timezone.timedelta(days=1),
        )

        items = _unattached_ip_delete_items(limit=20)

        self.assertFalse(any(item.get('public_ip') == '5.5.5.23' for item in items))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aws_missing_instance_requires_five_passes_before_delete(self):
        from cloud.management.commands.sync_aws_assets import _mark_deleted_when_missing_in_aws

        # 测试类：组织 DummyStyle 相关的回归测试。
        class DummyStyle:
            # 功能：处理 云资产、云订单和生命周期 中的 WARNING 业务流程。
            def WARNING(self, text):
                return text

        # 测试类：组织 DummyStdout 相关的回归测试。
        class DummyStdout:
            # 功能：初始化对象状态和依赖。
            def __init__(self):
                self.stdout = self
                self.style = DummyStyle()
            # 功能：处理 云资产、云订单和生命周期 中的 write 业务流程。
            def write(self, text):
                return text

        asset_expires_at = timezone.now() + timezone.timedelta(days=31)
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
            actual_expires_at=asset_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='aws-missing-confirm-server',
            public_ip='9.9.9.9',
            previous_public_ip='9.9.9.9',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        deleted = _mark_deleted_when_missing_in_aws(self.plan.region_code, set(), set(), DummyStdout())
        asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(deleted, [])
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.provider_status, '云上未找到实例/IP-待确认')
        self.assertEqual(asset.sync_state['missing_confirmation']['count'], 1)
        self.assertEqual(server.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(order.status, 'completed')

        with patch('cloud.sync_safety.get_missing_confirmation_interval_minutes', return_value=0):
            for _ in range(3):
                deleted = _mark_deleted_when_missing_in_aws(self.plan.region_code, set(), set(), DummyStdout())
                asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
                self.assertEqual(deleted, [])
                self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
                self.assertEqual(server.status, CloudAsset.STATUS_RUNNING)
                self.assertEqual(order.status, 'completed')

            deleted = _mark_deleted_when_missing_in_aws(self.plan.region_code, set(), set(), DummyStdout())
        asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
        self.assertTrue(deleted)
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertGreaterEqual(asset.sync_state['missing_confirmation']['count'], 5)
        self.assertEqual(server.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(order.status, 'deleted')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aws_missing_order_preserves_asset_expiry_when_migration_due_is_earlier(self):
        from cloud.lifecycle_schedule import compute_order_lifecycle_schedule, normalize_asset_expiry
        from cloud.management.commands.sync_aws_assets import _sync_order_deleted_from_cloud

        asset_expires_at = timezone.now() + timezone.timedelta(days=31)
        migration_due_at = timezone.now() + timezone.timedelta(days=3)
        expected_schedule = compute_order_lifecycle_schedule(normalize_asset_expiry(migration_due_at))
        order = CloudServerOrder.objects.create(
            order_no='AWS-MISS-MIGRATION-PRESERVE-EXPIRY',
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
            status='deleting',
            public_ip='9.9.9.19',
            previous_public_ip='9.9.9.19',
            instance_id='aws-miss-migration-preserve-expiry',
            migration_due_at=migration_due_at,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='aws-miss-migration-preserve-expiry',
            public_ip='9.9.9.19',
            previous_public_ip='9.9.9.19',
            instance_id=order.instance_id,
            actual_expires_at=asset_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        _sync_order_deleted_from_cloud(order, '9.9.9.19', asset=asset)

        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.status, 'deleted')
        self.assertEqual(order_asset_expiry(order), asset_expires_at)
        self.assertEqual(asset.actual_expires_at, asset_expires_at)
        self.assertEqual(order.renew_grace_expires_at, expected_schedule.renew_grace_expires_at)
        self.assertEqual(order.delete_at, expected_schedule.delete_at)
        self.assertEqual(order.ip_recycle_at, expected_schedule.ip_recycle_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_order_primary_asset_prefers_ip_over_stale_names(self):
        from cloud.services import _order_primary_asset

        order = CloudServerOrder.objects.create(
            order_no='PRIMARY-IP-FIRST-1',
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
            public_ip='9.9.9.20',
            previous_public_ip='9.9.9.20',
            server_name='stale-server-name',
            instance_id='stale-instance-id',
            provider_resource_id='stale-resource-id',
            service_started_at=timezone.now(),
        )
        stale_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='stale-server-name',
            instance_id='stale-instance-id',
            provider_resource_id='stale-resource-id',
            public_ip='8.8.8.8',
            status=CloudAsset.STATUS_RUNNING,
        )
        ip_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='current-server-name',
            instance_id='current-instance-id',
            public_ip='9.9.9.20',
            status=CloudAsset.STATUS_RUNNING,
        )
        stale_asset.previous_public_ip = '9.9.9.20'
        stale_asset.save(update_fields=['previous_public_ip', 'updated_at'])

        self.assertEqual(_order_primary_asset(order).id, ip_asset.id)
        self.assertNotEqual(_order_primary_asset(order).id, stale_asset.id)

    # 功能：验证主记录更新只修改当前主资产，不误写同订单历史资产。
    def test_order_primary_record_update_does_not_mutate_stale_same_order_assets(self):
        from cloud.services import _update_order_primary_records

        order = CloudServerOrder.objects.create(
            order_no='PRIMARY-UPDATE-CURRENT-ONLY',
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
            public_ip='9.9.9.40',
            previous_public_ip='9.9.9.39',
            service_started_at=timezone.now(),
        )
        stale_expiry = timezone.now() + timezone.timedelta(days=3)
        current_expiry = timezone.now() + timezone.timedelta(days=30)
        stale_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='stale-primary-update',
            public_ip='8.8.8.40',
            previous_public_ip='9.9.9.39',
            actual_expires_at=stale_expiry,
            mtproxy_host='8.8.8.40',
            status=CloudAsset.STATUS_RUNNING,
        )
        current_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='current-primary-update',
            public_ip='9.9.9.40',
            actual_expires_at=current_expiry,
            mtproxy_host='9.9.9.40',
            status=CloudAsset.STATUS_RUNNING,
        )
        new_expiry = timezone.now() + timezone.timedelta(days=45)

        selected, _ = _update_order_primary_records(
            order,
            asset_updates={'actual_expires_at': new_expiry, 'mtproxy_host': '9.9.9.99'},
        )

        stale_asset.refresh_from_db()
        current_asset.refresh_from_db()
        self.assertEqual(selected.id, current_asset.id)
        self.assertEqual(current_asset.actual_expires_at, new_expiry)
        self.assertEqual(current_asset.mtproxy_host, '9.9.9.99')
        self.assertEqual(stale_asset.actual_expires_at, stale_expiry)
        self.assertEqual(stale_asset.mtproxy_host, '8.8.8.40')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_aws_resource_resolution_prefers_ip(self):
        from cloud.lifecycle import _aws_instance_name_for_order, _aws_static_ip_name_for_asset, _delete_instance_sync, _delete_orphan_asset_instance_sync

        # 测试类：组织 FakeClient 相关的回归测试。
        class FakeClient:
            # 功能：初始化对象状态和依赖。
            def __init__(self):
                self.deleted_instances = []

            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_instances(self, **kwargs):
                return {'instances': [{'name': 'current-ip-instance', 'publicIpAddress': '9.9.9.30'}]}

            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_static_ips(self, **kwargs):
                return {'staticIps': [{'name': 'current-static-ip-name', 'ipAddress': '9.9.9.31'}]}

            # 功能：删除或标记删除相关业务对象；当前函数属于 云资产、云订单和生命周期。
            def delete_instance(self, instanceName):
                self.deleted_instances.append(instanceName)

        order = CloudServerOrder(
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            server_name='stale-server-name',
            public_ip='',
            previous_public_ip='9.9.9.30',
        )
        asset = CloudAsset(
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='stale-static-ip-name',
            public_ip='',
            previous_public_ip='9.9.9.31',
        )

        self.assertEqual(_aws_instance_name_for_order(order, FakeClient()), 'current-ip-instance')
        self.assertEqual(_aws_static_ip_name_for_asset(asset, FakeClient()), 'current-static-ip-name')
        fallback_asset = CloudAsset(
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='stale-static-ip-name',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/current-resource-static-ip-name',
            public_ip='',
            previous_public_ip='9.9.9.99',
        )
        self.assertEqual(_aws_static_ip_name_for_asset(fallback_asset, FakeClient()), 'current-resource-static-ip-name')

        delete_client = FakeClient()
        with patch('cloud.lifecycle._aws_client', return_value=delete_client):
            ok, _ = _delete_instance_sync(order)
        self.assertTrue(ok)
        self.assertEqual(delete_client.deleted_instances, ['current-ip-instance'])

        orphan_asset = CloudAsset(
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='stale-orphan-name',
            public_ip='',
            previous_public_ip='9.9.9.30',
        )
        orphan_client = FakeClient()
        with patch('cloud.lifecycle._aws_client', return_value=orphan_client):
            ok, _ = _delete_orphan_asset_instance_sync(orphan_asset)
        self.assertTrue(ok)
        self.assertEqual(orphan_client.deleted_instances, ['current-ip-instance'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aws_renewal_start_check_prefers_ip_over_stale_server_name(self):
        from cloud.services import _ensure_aws_instance_running

        started = []

        # 测试类：组织 FakeClient 相关的回归测试。
        class FakeClient:
            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_instances(self, **kwargs):
                return {'instances': [{'name': 'current-ip-instance', 'publicIpAddress': '9.9.9.40'}]}

            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_instance(self, instanceName):
                if instanceName == 'stale-server-name':
                    raise AssertionError('should not query stale server name first')
                return {'instance': {'name': instanceName, 'publicIpAddress': '9.9.9.40', 'state': {'name': 'stopped'}}}

            # 功能：启动任务、流程或云资源；当前函数属于 云资产、云订单和生命周期。
            def start_instance(self, instanceName):
                started.append(instanceName)

        order = CloudServerOrder.objects.create(
            order_no='ORDER-AWS-START-IP-FIRST',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='completed',
            server_name='stale-server-name',
            public_ip='',
            previous_public_ip='9.9.9.40',
        )

        with patch('cloud.services._aws_lightsail_client_for_order', return_value=FakeClient()):
            ok, note = _ensure_aws_instance_running(order)

        self.assertTrue(ok)
        self.assertEqual(started, ['current-ip-instance'])
        self.assertIn('已发起开机', note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_admin_start_restores_suspended_order_to_completed(self):
        from cloud.services import start_cloud_server_from_admin

        account = self._aws_test_account()
        order = CloudServerOrder.objects.create(
            order_no='ORDER-ADMIN-START-RESTORE',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='suspended',
            server_name='admin-start-instance',
            instance_id='admin-start-instance',
            public_ip='9.9.9.41',
            previous_public_ip='9.9.9.41',
            service_started_at=timezone.now() - timezone.timedelta(days=10),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='admin-start-instance',
            instance_id='admin-start-instance',
            public_ip='9.9.9.41',
            previous_public_ip='9.9.9.41',
            status=CloudAsset.STATUS_STOPPED,
            provider_status='已关机-到期延停',
            is_active=False,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='admin-start-instance',
            instance_id='admin-start-instance',
            public_ip='9.9.9.41',
            previous_public_ip='9.9.9.41',
            status=CloudAsset.STATUS_STOPPED,
            provider_status='已关机-到期延停',
            is_active=False,
        )

        # 测试类：组织 FakeClient 相关的回归测试。
        class FakeClient:
            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_instance(self, instanceName):
                return {
                    'instance': {
                        'name': instanceName,
                        'publicIpAddress': '9.9.9.41',
                        'state': {'name': 'running'},
                    }
                }

        with patch('cloud.services._aws_lightsail_client_for_order', return_value=FakeClient()), \
             patch('cloud.services._aws_instance_name_for_order_runtime', return_value='admin-start-instance'), \
             patch('cloud.services._ensure_mtproxy_after_renewal', return_value=(True, 'MTProxy OK')):
            returned_order, warning = async_to_sync(start_cloud_server_from_admin)(order.id)

        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertIsNone(warning)
        self.assertEqual(returned_order.status, 'completed')
        self.assertEqual(order.status, 'completed')
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(server.status, CloudAsset.STATUS_RUNNING)
        self.assertTrue(asset.is_active)
        self.assertTrue(server.is_active)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dashboard_asset_order_inference_scopes_duplicate_ip_by_account(self):
        first_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='infer-account-a',
            region_hint=self.plan.region_code,
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        second_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='infer-account-b',
            region_hint=self.plan.region_code,
            access_key='C' * 20,
            secret_key='D' * 40,
            is_active=True,
        )
        stale_order = CloudServerOrder.objects.create(
            order_no='ORDER-INFER-STALE',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            cloud_account=first_account,
            account_label=cloud_account_label(first_account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='completed',
            public_ip='9.9.9.50',
            server_name='stale-name-match',
        )
        target_order = CloudServerOrder.objects.create(
            order_no='ORDER-INFER-TARGET',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            cloud_account=second_account,
            account_label=cloud_account_label(second_account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='completed',
            public_ip='9.9.9.50',
            server_name='target-name',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=second_account,
            account_label=cloud_account_label(second_account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='stale-name-match',
            public_ip='9.9.9.50',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        self.assertEqual(_infer_asset_order(asset), target_order)
        self.assertNotEqual(_infer_asset_order(asset), stale_order)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aws_missing_check_uses_previous_public_ip_before_delete(self):
        from cloud.management.commands.sync_aws_assets import _mark_deleted_when_missing_in_aws

        # 测试类：组织 DummyStyle 相关的回归测试。
        class DummyStyle:
            # 功能：处理 云资产、云订单和生命周期 中的 WARNING 业务流程。
            def WARNING(self, text):
                return text

        # 测试类：组织 DummyStdout 相关的回归测试。
        class DummyStdout:
            # 功能：初始化对象状态和依赖。
            def __init__(self):
                self.stdout = self
                self.style = DummyStyle()
            # 功能：处理 云资产、云订单和生命周期 中的 write 业务流程。
            def write(self, text):
                return text

        asset_expires_at = timezone.now() + timezone.timedelta(days=31)
        order = CloudServerOrder.objects.create(
            order_no='AWS-MISS-PREV-IP-1',
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
            public_ip=None,
            previous_public_ip='9.9.9.10',
            instance_id='',
            provider_resource_id='StaticIp-prev-ip-1',
            service_started_at=timezone.now(),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='aws-prev-ip-asset',
            public_ip=None,
            previous_public_ip='9.9.9.10',
            instance_id='',
            provider_resource_id='StaticIp-prev-ip-1',
            actual_expires_at=asset_expires_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='aws-prev-ip-server',
            public_ip=None,
            previous_public_ip='9.9.9.10',
            instance_id='',
            provider_resource_id='StaticIp-prev-ip-1',
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )

        deleted = _mark_deleted_when_missing_in_aws(self.plan.region_code, set(), {'9.9.9.10'}, DummyStdout())
        asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()

        self.assertEqual(deleted, [])
        self.assertEqual(asset.status, CloudAsset.STATUS_UNKNOWN)
        self.assertEqual(server.status, CloudAsset.STATUS_UNKNOWN)
        self.assertEqual(order.status, 'completed')
        self.assertNotIn('云上未找到实例/IP-待确认', asset.provider_status or '')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aws_missing_blank_asset_does_not_delete_unrelated_blank_server(self):
        from cloud.management.commands.sync_aws_assets import _mark_deleted_when_missing_in_aws

        # 测试类：组织 DummyStyle 相关的回归测试。
        class DummyStyle:
            # 功能：处理 云资产、云订单和生命周期 中的 WARNING 业务流程。
            def WARNING(self, text):
                return text

        # 测试类：组织 DummyStdout 相关的回归测试。
        class DummyStdout:
            # 功能：初始化对象状态和依赖。
            def __init__(self):
                self.stdout = self
                self.style = DummyStyle()
            # 功能：处理 云资产、云订单和生命周期 中的 write 业务流程。
            def write(self, text):
                return text

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='aws-blank-dirty-asset',
            public_ip='',
            previous_public_ip='',
            instance_id='',
            provider_resource_id='',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='aws-unrelated-live-server',
            public_ip='9.9.9.77',
            previous_public_ip='',
            instance_id='aws-unrelated-live-instance',
            provider_resource_id='',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        with patch('cloud.sync_safety.get_missing_confirmation_interval_minutes', return_value=0):
            for _ in range(5):
                _mark_deleted_when_missing_in_aws(self.plan.region_code, {'aws-unrelated-live-instance'}, {'9.9.9.77'}, DummyStdout())

        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(server.status, CloudAsset.STATUS_RUNNING)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aliyun_order_update_recalculates_lifecycle_on_expiry_change(self):
        from cloud.management.commands.sync_aliyun_assets import _aliyun_order_updates_from_sync

        old_expires_at = timezone.now() + timezone.timedelta(days=2)
        new_expires_at = timezone.now() + timezone.timedelta(days=31)
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_ALIYUN,
            name='aliyun-expiry-sync',
            external_account_id='aliyun-expiry-sync',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='cn-hongkong',
            is_active=True,
        )
        order = CloudServerOrder.objects.create(
            order_no='ALIYUN-EXPIRY-SYNC-1',
            user=self.user,
            plan=self.plan,
            provider='aliyun_simple',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='cn-hongkong',
            region_name='中国香港',
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='completed',
            public_ip='6.6.6.70',
            instance_id='i-aliyun-expiry-sync',
            provider_resource_id='i-aliyun-expiry-sync',
            service_started_at=timezone.now() - timezone.timedelta(days=10),
            renew_notice_sent_at=timezone.now(),
            auto_renew_notice_sent_at=timezone.now(),
            auto_renew_failure_notice_sent_at=timezone.now(),
            delete_notice_sent_at=timezone.now(),
            recycle_notice_sent_at=timezone.now(),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            order=order,
            user=self.user,
            provider='aliyun_simple',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code='cn-hongkong',
            region_name='中国香港',
            asset_name='aliyun-expiry-sync',
            instance_id='i-aliyun-expiry-sync',
            provider_resource_id='i-aliyun-expiry-sync',
            public_ip='6.6.6.70',
            actual_expires_at=old_expires_at,
            status=CloudAsset.STATUS_RUNNING,
        )

        updates = _aliyun_order_updates_from_sync(
            order,
            normalized_status=CloudAsset.STATUS_RUNNING,
            expires_at=new_expires_at,
            account=account,
            account_label=cloud_account_label(account),
            region='cn-hongkong',
            item={'RegionId': 'cn-hongkong'},
            asset_name='aliyun-expiry-sync',
            instance_id='i-aliyun-expiry-sync',
            public_ip='6.6.6.70',
        )

        self.assertNotIn('actual_expires_at', updates)
        self.assertGreater(updates['suspend_at'], new_expires_at)
        self.assertGreaterEqual(updates['delete_at'], updates['suspend_at'])
        self.assertGreater(updates['ip_recycle_at'], updates['delete_at'])
        self.assertIsNone(updates['renew_notice_sent_at'])
        self.assertIsNone(updates['auto_renew_notice_sent_at'])
        self.assertIsNone(updates['delete_notice_sent_at'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aliyun_assets_preserves_existing_asset_expiry(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_ALIYUN,
            name='aliyun-preserve-asset-expiry',
            external_account_id='aliyun-preserve-asset-expiry',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='cn-hongkong',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        old_expires_at = timezone.now() + timezone.timedelta(days=3)
        cloud_expires_at = timezone.now() + timezone.timedelta(days=31)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            user=self.user,
            provider='aliyun_simple',
            cloud_account=account,
            account_label=account_label,
            region_code='cn-hongkong',
            region_name='中国香港',
            asset_name='aliyun-preserve-asset-expiry',
            instance_id='i-aliyun-preserve-asset-expiry',
            provider_resource_id='i-aliyun-preserve-asset-expiry',
            public_ip='6.6.6.71',
            actual_expires_at=old_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        fake_aliyun_module = SimpleNamespace(models=SimpleNamespace(ListInstancesRequest=lambda **kwargs: kwargs))

        # 测试类：组织 FakeAliyunClient 相关的回归测试。
        class FakeAliyunClient:
            # 功能：读取并返回列表数据；当前函数属于 云资产、云订单和生命周期。
            def list_instances_with_options(self, request, runtime_options):
                return SimpleNamespace(body=SimpleNamespace(to_map=lambda: {
                    'Instances': [{
                        'InstanceId': 'i-aliyun-preserve-asset-expiry',
                        'InstanceName': 'aliyun-preserve-asset-expiry',
                        'PublicIpAddress': '6.6.6.71',
                        'RegionId': 'cn-hongkong',
                        'Status': 'Running',
                        'BusinessStatus': 'Normal',
                        'ExpiredTime': cloud_expires_at.isoformat(),
                    }],
                    'TotalCount': 1,
                }))

        with patch.dict(sys.modules, {'alibabacloud_swas_open20200601': fake_aliyun_module}), \
            patch('cloud.management.commands.sync_aliyun_assets._build_client', return_value=FakeAliyunClient()):
            call_command('sync_aliyun_assets', region='cn-hongkong', account_id=str(account.id))

        asset.refresh_from_db()
        self.assertEqual(asset.actual_expires_at, old_expires_at)
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aliyun_missing_instance_requires_five_passes_before_delete(self):
        from cloud.management.commands.sync_aliyun_assets import _mark_deleted_when_missing_in_aliyun

        # 测试类：组织 DummyStyle 相关的回归测试。
        class DummyStyle:
            # 功能：处理 云资产、云订单和生命周期 中的 WARNING 业务流程。
            def WARNING(self, text):
                return text

        # 测试类：组织 DummyStdout 相关的回归测试。
        class DummyStdout:
            # 功能：初始化对象状态和依赖。
            def __init__(self):
                self.stdout = self
                self.style = DummyStyle()
            # 功能：处理 云资产、云订单和生命周期 中的 write 业务流程。
            def write(self, text):
                return text

        asset_expires_at = timezone.now() + timezone.timedelta(days=31)
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
            actual_expires_at=asset_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            order=order,
            user=self.user,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            region_name='中国香港',
            asset_name='aliyun-missing-confirm-server',
            public_ip='6.6.6.6',
            previous_public_ip='6.6.6.6',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        deleted = _mark_deleted_when_missing_in_aliyun('cn-hongkong', set(), DummyStdout())
        asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(deleted, [])
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.provider_status, '云上未找到实例-待确认')
        self.assertEqual(asset.sync_state['missing_confirmation']['count'], 1)
        self.assertEqual(server.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(order.status, 'completed')

        with patch('cloud.sync_safety.get_missing_confirmation_interval_minutes', return_value=0):
            for _ in range(3):
                deleted = _mark_deleted_when_missing_in_aliyun('cn-hongkong', set(), DummyStdout())
                asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
                self.assertEqual(deleted, [])
                self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
                self.assertEqual(server.status, CloudAsset.STATUS_RUNNING)
                self.assertEqual(order.status, 'completed')

            deleted = _mark_deleted_when_missing_in_aliyun('cn-hongkong', set(), DummyStdout())
        asset.refresh_from_db(); server.refresh_from_db(); order.refresh_from_db()
        self.assertTrue(deleted)
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertGreaterEqual(asset.sync_state['missing_confirmation']['count'], 5)
        self.assertEqual(server.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(order.status, 'deleted')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aliyun_missing_blank_asset_does_not_delete_unrelated_blank_server(self):
        from cloud.management.commands.sync_aliyun_assets import _mark_deleted_when_missing_in_aliyun

        # 测试类：组织 DummyStyle 相关的回归测试。
        class DummyStyle:
            # 功能：处理 云资产、云订单和生命周期 中的 WARNING 业务流程。
            def WARNING(self, text):
                return text

        # 测试类：组织 DummyStdout 相关的回归测试。
        class DummyStdout:
            # 功能：初始化对象状态和依赖。
            def __init__(self):
                self.stdout = self
                self.style = DummyStyle()
            # 功能：处理 云资产、云订单和生命周期 中的 write 业务流程。
            def write(self, text):
                return text

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            asset_name='aliyun-blank-dirty-asset',
            public_ip='',
            previous_public_ip='',
            instance_id='',
            provider_resource_id='',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            provider='aliyun_simple',
            region_code='cn-hongkong',
            asset_name='aliyun-unrelated-live-server',
            public_ip='6.6.6.77',
            previous_public_ip='',
            instance_id='aliyun-unrelated-live-instance',
            provider_resource_id='',
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        with patch('cloud.sync_safety.get_missing_confirmation_interval_minutes', return_value=0):
            for _ in range(5):
                _mark_deleted_when_missing_in_aliyun('cn-hongkong', set(), DummyStdout())

        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(server.status, CloudAsset.STATUS_RUNNING)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aliyun_order_is_not_enqueued_for_shutdown_delete_plan(self):
        from bot.api import _collect_shutdown_plan_queue

        order = CloudServerOrder.objects.create(
            order_no='ALIYUN-NO-DELETE-PLAN-1',
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
            status='suspended',
            public_ip='6.6.7.1',
            previous_public_ip='6.6.7.1',
            instance_id='i-aliyun-no-delete-plan-1',
            provider_resource_id='i-aliyun-no-delete-plan-1',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            delete_at=timezone.now() - timezone.timedelta(days=1),
        )

        queue = _collect_shutdown_plan_queue(timezone.now(), limit=20)
        order_ids = {item.get('order_id') for item in [*queue['due_items'], *queue['future_plan_items']]}

        self.assertNotIn(order.id, order_ids)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_manual_aliyun_delete_plan_is_blocked_without_local_delete(self):
        from bot.api import _run_shutdown_order_sync

        order = CloudServerOrder.objects.create(
            order_no='ALIYUN-NO-DELETE-RUN-1',
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
            status='suspended',
            public_ip='6.6.7.2',
            previous_public_ip='6.6.7.2',
            instance_id='i-aliyun-no-delete-run-1',
            provider_resource_id='i-aliyun-no-delete-run-1',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            delete_at=timezone.now() - timezone.timedelta(days=1),
        )

        result = _run_shutdown_order_sync(order.id, enforce_schedule=False)
        order.refresh_from_db()

        self.assertFalse(result['ok'])
        self.assertIn('未接入删除 API', result['error'])
        self.assertEqual(order.status, 'suspended')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_failed_aliyun_order_is_not_enqueued_for_fallback_delete(self):
        order = CloudServerOrder.objects.create(
            order_no='ALIYUN-FAILED-NO-FALLBACK-DELETE-1',
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
            status='failed',
            public_ip='6.6.7.3',
            previous_public_ip='6.6.7.3',
            instance_id='i-aliyun-failed-no-delete-1',
            provider_resource_id='i-aliyun-failed-no-delete-1',
            service_started_at=timezone.now() - timezone.timedelta(days=40),
            delete_at=timezone.now() - timezone.timedelta(days=1),
        )

        due = async_to_sync(_get_due_orders)()

        self.assertNotIn(order.id, [item.id for item in due['delete']])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        # 测试类：组织 FakeLightsailClient 相关的回归测试。
        class FakeLightsailClient:
            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_static_ips(self, **kwargs):
                return {'staticIps': [], 'nextPageToken': None}

            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
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
        self.assertEqual(asset.instance_id, 'i-rebound-sync-1')
        self.assertEqual(asset.provider_status, '已重新绑定实例-待人工添加时间')
        self.assertIsNone(asset.actual_expires_at)
        self.assertTrue(asset.is_active)
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.note, '未附加固定IP')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aws_assets_updates_retained_asset_after_renewal_recovery(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-retained-recovered',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        old_order = CloudServerOrder.objects.create(
            order_no='RETAINED-RECOVERED-OLD',
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
            public_ip='10.9.0.3',
            previous_public_ip='10.9.0.3',
            instance_id='',
            static_ip_name='recovered-static-ip',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=5),
            cloud_account=account,
            account_label=account_label,
        )
        recovery_expires_at = timezone.now() + timezone.timedelta(days=31)
        recovery_order = CloudServerOrder.objects.create(
            order_no='RETAINED-RECOVERED-NEW',
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
            public_ip='10.9.0.3',
            previous_public_ip='10.9.0.3',
            instance_id='i-recovered-sync-1',
            server_name='i-recovered-sync-1',
            static_ip_name='recovered-static-ip',
            cloud_account=account,
            account_label=account_label,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=recovery_order,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='recovered-static-ip',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/recovered-static-ip',
            public_ip='10.9.0.3',
            previous_public_ip='10.9.0.3',
            actual_expires_at=recovery_expires_at,
            status=CloudAsset.STATUS_DELETED,
            provider_status='固定IP保留中-实例已删除',
            note='固定IP保留中-实例已删除',
            is_active=False,
        )

        # 测试类：组织 FakeLightsailClient 相关的回归测试。
        class FakeLightsailClient:
            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_static_ips(self, **kwargs):
                return {'staticIps': [{
                    'name': 'recovered-static-ip',
                    'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/recovered-static-ip',
                    'ipAddress': '10.9.0.3',
                    'attachedTo': 'i-recovered-sync-1',
                    'location': {'regionName': '新加坡'},
                }], 'nextPageToken': None}

            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_instances(self, **kwargs):
                return {
                    'instances': [{
                        'name': 'i-recovered-sync-1',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-recovered-sync-1',
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
        self.assertEqual(CloudAsset.objects.filter(public_ip='10.9.0.3').count(), 1)
        self.assertEqual(asset.instance_id, 'i-recovered-sync-1')
        self.assertEqual(asset.order_id, recovery_order.id)
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.provider_status, '运行中')
        self.assertEqual(asset.actual_expires_at, recovery_expires_at)
        self.assertTrue(asset.is_active)
        self.assertEqual(asset.note, '固定IP保留中-实例已删除')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aws_retained_ip_preserves_existing_asset_user(self):
        from cloud.management.commands.sync_aws_assets import _mark_ip_retained_as_unattached

        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-retained-user-preserve',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        order_owner = TelegramUser.objects.create(tg_user_id=21989081, username='retained_order_owner')
        existing_owner = TelegramUser.objects.create(tg_user_id=21989082, username='retained_asset_owner')
        retained_order = CloudServerOrder.objects.create(
            order_no='AWS-RETAINED-OWNER-PRESERVE-1',
            user=order_owner,
            plan=self.plan,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleted',
            public_ip='10.9.0.7',
            previous_public_ip='10.9.0.7',
            static_ip_name='retain-user-ip',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=7),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=existing_owner,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='retain-user-related-instance',
            instance_id='i-retain-user-related-instance',
            public_ip='10.9.0.7',
            status=CloudAsset.STATUS_DELETED,
            provider_status='固定IP保留中-实例已删除',
            is_active=False,
        )

        updated = _mark_ip_retained_as_unattached(
            '10.9.0.7',
            'retain-user-ip',
            retained_order,
            account,
            self.plan.region_code,
            'AWS 同步测试保留固定 IP',
            timezone.now(),
            retained_order.ip_recycle_at,
        )

        asset.refresh_from_db()
        self.assertTrue(updated)
        self.assertEqual(asset.order_id, retained_order.id)
        self.assertEqual(asset.user_id, existing_owner.id)
        self.assertEqual(asset.provider_status, '固定IP仍存在但未附加')
        self.assertEqual(asset.actual_expires_at, retained_order.ip_recycle_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aws_retained_unattached_asset_is_not_missing_deleted_when_static_ip_exists(self):
        from cloud.management.commands.sync_aws_assets import _mark_deleted_when_missing_in_aws, _mark_ip_retained_as_unattached

        # 测试类：组织 DummyStyle 相关的回归测试。
        class DummyStyle:
            # 功能：处理 云资产、云订单和生命周期 中的 WARNING 业务流程。
            def WARNING(self, text):
                return text

        # 测试类：组织 DummyStdout 相关的回归测试。
        class DummyStdout:
            # 功能：初始化对象状态和依赖。
            def __init__(self):
                self.stdout = self
                self.style = DummyStyle()

            # 功能：处理 云资产、云订单和生命周期 中的 write 业务流程。
            def write(self, text):
                return text

        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-retained-static-skip-missing',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        retained_order = CloudServerOrder.objects.create(
            order_no='AWS-RETAINED-SKIP-MISSING-1',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleted',
            public_ip='10.9.0.8',
            previous_public_ip='10.9.0.8',
            static_ip_name='retain-skip-missing-ip',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=7),
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='retain-skip-missing-related-instance',
            instance_id='i-retain-skip-missing-related-instance',
            public_ip='10.9.0.8',
            status=CloudAsset.STATUS_DELETED,
            provider_status='固定IP保留中-实例已删除',
            is_active=False,
        )

        _mark_ip_retained_as_unattached(
            '10.9.0.8',
            'retain-skip-missing-ip',
            retained_order,
            account,
            self.plan.region_code,
            'AWS 同步测试保留固定 IP',
            timezone.now(),
            retained_order.ip_recycle_at,
        )
        deleted = _mark_deleted_when_missing_in_aws(
            self.plan.region_code,
            set(),
            {'10.9.0.8'},
            DummyStdout(),
            account=account,
        )

        asset.refresh_from_db()
        self.assertEqual(deleted, [])
        self.assertEqual(asset.provider_status, '固定IP仍存在但未附加')
        self.assertNotIn('missing_confirmation', asset.sync_state)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aws_assets_preserves_existing_unattached_ip_due_time(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-stale-unattached-ip',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        stale_due_at = timezone.now() - timezone.timedelta(days=1)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-stale-unattached',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-stale-unattached',
            public_ip='10.9.0.4',
            actual_expires_at=stale_due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )

        # 测试类：组织 FakeLightsailClient 相关的回归测试。
        class FakeLightsailClient:
            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_static_ips(self, **kwargs):
                return {
                    'staticIps': [{
                        'name': 'StaticIp-stale-unattached',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-stale-unattached',
                        'ipAddress': '10.9.0.4',
                        'attachedTo': '',
                        'location': {'regionName': '新加坡'},
                    }],
                    'nextPageToken': None,
                }

            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_instances(self, **kwargs):
                return {'instances': [], 'nextPageToken': None}

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1')

        asset.refresh_from_db()
        self.assertEqual(asset.provider_status, '未附加固定IP')
        self.assertEqual(asset.actual_expires_at, stale_due_at)
        self.assertEqual(asset.note, '未附加固定IP')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aws_unattached_ip_duplicate_cleanup_is_account_scoped(self):
        account_a = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-static-account-a',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_b = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-static-account-b',
            external_account_id='222222222222',
            access_key='C' * 20,
            secret_key='D' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        foreign_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
            cloud_account=account_b,
            account_label=cloud_account_label(account_b),
            region_code='ap-southeast-1',
            asset_name='foreign-static-ip',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:222222222222:StaticIp/foreign-static-ip',
            public_ip='10.9.0.40',
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )

        # 测试类：组织 FakeLightsailClient 相关的回归测试。
        class FakeLightsailClient:
            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_static_ips(self, **kwargs):
                return {
                    'staticIps': [{
                        'name': 'account-a-static-ip',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/account-a-static-ip',
                        'ipAddress': '10.9.0.40',
                        'attachedTo': '',
                        'location': {'regionName': '新加坡'},
                    }],
                    'nextPageToken': None,
                }

            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_instances(self, **kwargs):
                return {'instances': [], 'nextPageToken': None}

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), \
            patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), \
            patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1', account_id=str(account_a.id))

        foreign_asset.refresh_from_db()
        self.assertEqual(foreign_asset.status, CloudAsset.STATUS_UNKNOWN)
        self.assertEqual(foreign_asset.public_ip, '10.9.0.40')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aws_assets_preserves_existing_manual_asset_note(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-status-note-append',
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
            asset_name='i-status-note-append',
            instance_id='i-status-note-append',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-status-note-append',
            public_ip='10.9.0.5',
            actual_expires_at=timezone.now() + timezone.timedelta(days=10),
            status=CloudAsset.STATUS_STOPPED,
            provider_status='旧状态',
            note='人工备注：不要覆盖',
            is_active=False,
        )

        # 测试类：组织 FakeLightsailClient 相关的回归测试。
        class FakeLightsailClient:
            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_static_ips(self, **kwargs):
                return {'staticIps': [], 'nextPageToken': None}

            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_instances(self, **kwargs):
                return {
                    'instances': [{
                        'name': 'i-status-note-append',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-status-note-append',
                        'state': {'name': 'running'},
                        'location': {'regionName': '新加坡'},
                        'publicIpAddress': '10.9.0.5',
                        'bundleId': 'micro_1_0',
                        'blueprintId': 'debian_12',
                    }],
                    'nextPageToken': None,
                }

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1')

        asset.refresh_from_db()
        self.assertEqual(asset.provider_status, '运行中')
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.note, '人工备注：不要覆盖')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_asset_sync_interval_defaults_to_ten_minutes(self):
        from core.runtime_config import get_cloud_asset_sync_interval_seconds

        self.assertEqual(get_cloud_asset_sync_interval_seconds(), 600)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
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
        asset_expires_at = timezone.now() - timezone.timedelta(days=1)
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
            actual_expires_at=asset_expires_at,
            status=CloudAsset.STATUS_SUSPENDED,
            provider_status='已到期关机，等待删除（云端已关机）',
            is_active=False,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='i-suspended-runtime-1',
            public_ip='10.9.0.3',
            previous_public_ip='10.9.0.3',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            actual_expires_at=asset_expires_at,
            status=CloudAsset.STATUS_SUSPENDED,
            provider_status='已到期关机，等待删除（云端已关机）',
            is_active=False,
        )

        # 测试类：组织 FakeLightsailClient 相关的回归测试。
        class FakeLightsailClient:
            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_static_ips(self, **kwargs):
                return {'staticIps': [], 'nextPageToken': None}

            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
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
        self.assertEqual(server.status, CloudAsset.STATUS_RUNNING)
        self.assertTrue(server.is_active)
        self.assertEqual(order.status, 'suspended')
        self.assertIn('云端运行中', asset.provider_status or '')
        self.assertIn('已到期关机，等待删除', asset.provider_status or '')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_dirty_deleted_note_does_not_hide_live_synced_asset(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='dirty-note-live-asset',
            instance_id='dirty-note-live-asset',
            public_ip='10.9.0.7',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            note='历史脏数据：IP校验发现云上不存在，已标记删除；最新同步又确认运行中',
            is_active=True,
        )

        self.assertFalse(_cloud_asset_deleted_or_missing(asset))
        queried = async_to_sync(get_proxy_asset_by_ip_for_user)('10.9.0.7', self.user.id)
        self.assertIsNotNone(queried)
        self.assertEqual(queried.id, asset.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aws_assets_revives_dirty_deleted_asset_when_instance_exists(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-revive-dirty-deleted-asset',
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
            asset_name='i-revive-dirty-deleted-asset',
            public_ip='10.9.0.6',
            previous_public_ip='10.9.0.6',
            instance_id='i-revive-dirty-deleted-asset',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-revive-dirty-deleted-asset',
            status=CloudAsset.STATUS_DELETED,
            provider_status='云上未找到实例/IP-待确认',
            note='IP校验发现云上不存在，已标记删除',
            is_active=False,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            account_label=account_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='i-revive-dirty-deleted-asset',
            public_ip='10.9.0.6',
            previous_public_ip='10.9.0.6',
            instance_id='i-revive-dirty-deleted-asset',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-revive-dirty-deleted-asset',
            status=CloudAsset.STATUS_DELETED,
            provider_status='云上未找到实例/IP-待确认',
            note='服务器校验发现云上不存在，已标记删除',
            is_active=False,
        )

        # 测试类：组织 FakeLightsailClient 相关的回归测试。
        class FakeLightsailClient:
            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_static_ips(self, **kwargs):
                return {'staticIps': [], 'nextPageToken': None}

            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_instances(self, **kwargs):
                return {
                    'instances': [{
                        'name': 'i-revive-dirty-deleted-asset',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-revive-dirty-deleted-asset',
                        'state': {'name': 'running'},
                        'location': {'regionName': '新加坡'},
                        'publicIpAddress': '10.9.0.6',
                        'bundleId': 'micro_1_0',
                        'blueprintId': 'debian_12',
                    }],
                    'nextPageToken': None,
                }

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1')

        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(CloudAsset.objects.filter(instance_id='i-revive-dirty-deleted-asset').count(), 1)
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertTrue(asset.is_active)
        self.assertNotIn('已标记删除', asset.note or '')
        self.assertNotIn('云上不存在', asset.note or '')
        self.assertEqual(server.status, CloudAsset.STATUS_RUNNING)
        self.assertTrue(server.is_active)
        self.assertNotIn('已标记删除', server.note or '')
        queried = async_to_sync(get_proxy_asset_by_ip_for_user)('10.9.0.6', self.user.id)
        self.assertIsNotNone(queried)
        self.assertEqual(queried.id, asset.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_sync_aws_assets_revives_deleted_order_when_instance_exists(self):
        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-revive-deleted-order',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        account_label = cloud_account_label(account)
        now = timezone.now()
        order = CloudServerOrder.objects.create(
            order_no='AWS-SYNC-REVIVE-DELETED-1',
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
            status='deleted',
            public_ip=None,
            previous_public_ip='10.9.0.8',
            instance_id='i-revive-deleted-1',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-revive-deleted-1',
            server_name='i-revive-deleted-1',
            service_started_at=now - timezone.timedelta(days=20),
            suspend_at=now - timezone.timedelta(days=2),
            delete_at=now - timezone.timedelta(days=1),
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
            asset_name='i-revive-deleted-1',
            public_ip='10.9.0.8',
            previous_public_ip='10.9.0.8',
            instance_id=order.instance_id,
            provider_resource_id=order.provider_resource_id,
            actual_expires_at=now - timezone.timedelta(days=5),
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )

        # 测试类：组织 FakeLightsailClient 相关的回归测试。
        class FakeLightsailClient:
            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_static_ips(self, **kwargs):
                return {'staticIps': [], 'nextPageToken': None}

            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_instances(self, **kwargs):
                return {
                    'instances': [{
                        'name': 'i-revive-deleted-1',
                        'arn': 'arn:aws:lightsail:ap-southeast-1:123456789012:Instance/i-revive-deleted-1',
                        'state': {'name': 'running'},
                        'location': {'regionName': '新加坡'},
                        'publicIpAddress': '10.9.0.8',
                        'bundleId': 'micro_1_0',
                        'blueprintId': 'debian_12',
                    }],
                    'nextPageToken': None,
                }

        with patch('cloud.management.commands.sync_aws_assets._list_regions', return_value=['ap-southeast-1']), patch('cloud.management.commands.sync_aws_assets._aws_account_identity', return_value='123456789012'), patch('cloud.management.commands.sync_aws_assets._lightsail_client', return_value=FakeLightsailClient()):
            call_command('sync_aws_assets', region='ap-southeast-1')

        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.status, 'deleting')
        self.assertEqual(order.public_ip, '10.9.0.8')
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)
        self.assertEqual(asset.order_id, order.id)
        self.assertNotIn('已标记删除', asset.note or '')
        due = async_to_sync(_get_due_orders)()
        self.assertIn(order.id, [item.id for item in due['delete']])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_proxy_list_hides_deleted_order_retained_ip(self):
        order = CloudServerOrder.objects.create(
            order_no='DELETED-LIST-HIDDEN-1',
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
            public_ip='20.20.20.30',
            previous_public_ip='20.20.20.30',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=5),
            instance_id='',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-hidden-retained',
            public_ip='20.20.20.30',
            previous_public_ip='20.20.20.30',
            actual_expires_at=order.ip_recycle_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='固定IP保留中-实例已删除',
            is_active=False,
        )

        items = async_to_sync(list_user_cloud_servers)(self.user.id)
        from cloud.services import get_user_proxy_asset_detail
        detail = async_to_sync(get_user_proxy_asset_detail)(asset.id, self.user.id, 'asset')

        self.assertFalse(any(getattr(item, 'asset_id', None) == asset.id for item in items))
        self.assertIsNone(detail)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_sync_resolvers_ignore_deleted_ip_records(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='deleted-sync-asset',
            instance_id='deleted-sync-instance',
            provider_resource_id='deleted-sync-arn',
            public_ip=None,
            previous_public_ip='20.20.20.31',
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='deleted-sync-server',
            instance_id='deleted-sync-instance',
            provider_resource_id='deleted-sync-arn',
            public_ip=None,
            previous_public_ip='20.20.20.31',
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
        )
        from cloud.management.commands.sync_aliyun_assets import _resolve_asset as resolve_aliyun_asset
        from cloud.management.commands.sync_aliyun_assets import _resolve_server as resolve_aliyun_server
        from cloud.management.commands.sync_aws_assets import _resolve_asset as resolve_aws_asset
        from cloud.management.commands.sync_aws_assets import _resolve_server as resolve_aws_server

        self.assertIsNone(resolve_aws_asset(asset.instance_id, asset.provider_resource_id, asset.previous_public_ip, None))
        self.assertIsNone(resolve_aws_server(server.instance_id, server.provider_resource_id, server.previous_public_ip, None))
        self.assertIsNone(resolve_aliyun_asset(asset.instance_id, asset.previous_public_ip))
        self.assertIsNone(resolve_aliyun_server(server.instance_id, server.previous_public_ip))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_sync_resolvers_keep_ip_primary_when_instance_changes(self):
        aws_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-ip-primary',
            external_account_id='123456789012',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='ap-southeast-1',
            is_active=True,
        )
        aws_label = cloud_account_label(aws_account)
        aws_ip_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=aws_account,
            account_label=aws_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='aws-old-instance-for-same-ip',
            instance_id='aws-old-instance-for-same-ip',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/aws-old-instance-for-same-ip',
            public_ip='20.20.20.40',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        aws_direct_conflict = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=aws_account,
            account_label=aws_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='aws-new-instance-conflict',
            instance_id='aws-new-instance-conflict',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/aws-new-instance-conflict',
            public_ip='20.20.20.41',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        aws_ip_server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            account_label=aws_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='aws-old-instance-for-same-ip',
            instance_id='aws-old-instance-for-same-ip',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/aws-old-instance-for-same-ip',
            public_ip='20.20.20.40',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            account_label=aws_label,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='aws-new-instance-conflict',
            instance_id='aws-new-instance-conflict',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:Instance/aws-new-instance-conflict',
            public_ip='20.20.20.41',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        from cloud.management.commands.sync_aws_assets import _resolve_asset as resolve_aws_asset
        from cloud.management.commands.sync_aws_assets import _resolve_server as resolve_aws_server

        self.assertEqual(resolve_aws_asset(aws_direct_conflict.instance_id, aws_direct_conflict.provider_resource_id, '20.20.20.40', None, aws_account).id, aws_ip_asset.id)
        self.assertEqual(resolve_aws_server('aws-new-instance-conflict', aws_direct_conflict.provider_resource_id, '20.20.20.40', None, aws_account).id, aws_ip_server.id)

        aliyun_account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_ALIYUN,
            name='aliyun-ip-primary',
            external_account_id='5698076839482440',
            access_key='A' * 20,
            secret_key='B' * 40,
            region_hint='cn-hongkong',
            is_active=True,
        )
        aliyun_label = cloud_account_label(aliyun_account)
        aliyun_ip_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aliyun_simple',
            cloud_account=aliyun_account,
            account_label=aliyun_label,
            region_code='cn-hongkong',
            region_name='香港',
            asset_name='aliyun-old-instance-for-same-ip',
            instance_id='aliyun-old-instance-for-same-ip',
            provider_resource_id='aliyun-old-instance-for-same-ip',
            public_ip='20.20.20.42',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        aliyun_direct_conflict = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aliyun_simple',
            cloud_account=aliyun_account,
            account_label=aliyun_label,
            region_code='cn-hongkong',
            region_name='香港',
            asset_name='aliyun-new-instance-conflict',
            instance_id='aliyun-new-instance-conflict',
            provider_resource_id='aliyun-new-instance-conflict',
            public_ip='20.20.20.43',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        aliyun_ip_server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            user=self.user,
            provider='aliyun_simple',
            account_label=aliyun_label,
            region_code='cn-hongkong',
            region_name='香港',
            asset_name='aliyun-old-instance-for-same-ip',
            instance_id='aliyun-old-instance-for-same-ip',
            provider_resource_id='aliyun-old-instance-for-same-ip',
            public_ip='20.20.20.42',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ALIYUN,
            user=self.user,
            provider='aliyun_simple',
            account_label=aliyun_label,
            region_code='cn-hongkong',
            region_name='香港',
            asset_name='aliyun-new-instance-conflict',
            instance_id='aliyun-new-instance-conflict',
            provider_resource_id='aliyun-new-instance-conflict',
            public_ip='20.20.20.43',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        from cloud.management.commands.sync_aliyun_assets import _resolve_asset as resolve_aliyun_asset
        from cloud.management.commands.sync_aliyun_assets import _resolve_server as resolve_aliyun_server

        self.assertEqual(resolve_aliyun_asset(aliyun_direct_conflict.instance_id, '20.20.20.42', aliyun_account).id, aliyun_ip_asset.id)
        self.assertEqual(resolve_aliyun_server('aliyun-new-instance-conflict', '20.20.20.42', aliyun_account).id, aliyun_ip_server.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cloud_sync_resolvers_prefer_current_ip_over_stale_previous_ip(self):
        current_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='current-ip-owner',
            instance_id='current-ip-owner',
            public_ip='20.20.20.50',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        stale_asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='stale-previous-ip-owner',
            instance_id='stale-previous-ip-owner',
            public_ip='20.20.20.51',
            previous_public_ip='20.20.20.50',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        current_server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='current-ip-owner',
            instance_id='current-ip-owner',
            public_ip='20.20.20.50',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='stale-previous-ip-owner',
            instance_id='stale-previous-ip-owner',
            public_ip='20.20.20.51',
            previous_public_ip='20.20.20.50',
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        from cloud.management.commands.sync_aws_assets import _resolve_asset as resolve_aws_asset
        from cloud.management.commands.sync_aws_assets import _resolve_server as resolve_aws_server

        self.assertEqual(resolve_aws_asset('', '', '20.20.20.50', None).id, current_asset.id)
        self.assertEqual(resolve_aws_server('', '', '20.20.20.50', None).id, current_server.id)
        self.assertNotEqual(stale_asset.id, current_asset.id)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_delete_server_marks_instance_deleted_but_retains_static_ip(self):
        now = timezone.now()
        recycle_at = now + timezone.timedelta(days=7)
        order = CloudServerOrder.objects.create(
            order_no='DELETE-RETAIN-STATIC-1',
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
            status='deleting',
            public_ip='20.20.20.32',
            previous_public_ip='20.20.20.32',
            static_ip_name='StaticIp-delete-retain',
            instance_id='delete-retain-instance',
            provider_resource_id='delete-retain-arn',
            ip_recycle_at=recycle_at,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='delete-retain-instance',
            instance_id='delete-retain-instance',
            provider_resource_id='delete-retain-arn',
            public_ip='20.20.20.32',
            previous_public_ip='20.20.20.32',
            actual_expires_at=recycle_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='delete-retain-instance',
            instance_id='delete-retain-instance',
            provider_resource_id='delete-retain-arn',
            public_ip='20.20.20.32',
            previous_public_ip='20.20.20.32',
            actual_expires_at=recycle_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )

        async_to_sync(_mark_deleted)(order.id, '实例已删除，固定 IP 保留。')

        order.refresh_from_db()
        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(order.status, 'deleted')
        self.assertEqual(order.public_ip, '20.20.20.32')
        self.assertEqual(order.previous_public_ip, '20.20.20.32')
        self.assertEqual(order.static_ip_name, 'StaticIp-delete-retain')
        self.assertGreater(order.ip_recycle_at, recycle_at)
        self.assertGreater(order.ip_recycle_at, now + timezone.timedelta(days=14))
        self.assertEqual(asset.actual_expires_at, order.ip_recycle_at)
        self.assertEqual(server.actual_expires_at, order.ip_recycle_at)
        self.assertIn('固定IP名=StaticIp-delete-retain', order.provision_note)
        self.assertIn('未附加 IP 计划回收=', order.provision_note)
        self.assertEqual(order.instance_id, '')
        self.assertEqual(asset.public_ip, '20.20.20.32')
        self.assertIsNone(asset.instance_id)
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(asset.provider_status, '固定IP保留中-实例已删除')
        self.assertEqual(server.public_ip, '20.20.20.32')
        self.assertIsNone(server.instance_id)
        self.assertEqual(server.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(server.provider_status, '固定IP保留中-实例已删除')
        self.assertFalse(any(getattr(item, 'asset_id', None) == asset.id for item in async_to_sync(list_user_cloud_servers)(self.user.id)))
        admin = get_user_model().objects.create_user(username='admin_retained_ip_asset_filter', password='x', is_staff=True)
        request = self.factory.get('/api/admin/cloud-assets/', {'paginated': '1', 'risk_status': 'unattached_ip'})
        self._attach_bearer_session(request, admin)
        response = cloud_assets_list(request)
        payload = json.loads(response.content.decode('utf-8'))['data']
        retained_row = next(item for item in payload['items'] if item['id'] == asset.id)
        self.assertEqual(retained_row['risk_status'], 'unattached_ip')
        self.assertIn('unattached_ip', retained_row['risk_statuses'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_unattached_static_ip_is_not_auto_renewed(self):
        expires_at = timezone.now() + timezone.timedelta(hours=8)
        order = CloudServerOrder.objects.create(
            order_no='UNATTACHED-NO-AUTO-RENEW-1',
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
            public_ip='20.20.20.34',
            previous_public_ip='20.20.20.34',
            static_ip_name='StaticIp-unattached-no-auto-renew',
            auto_renew_enabled=True,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-unattached-no-auto-renew',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-unattached-no-auto-renew',
            public_ip='20.20.20.34',
            previous_public_ip='20.20.20.34',
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='未附加固定IP',
            is_active=False,
            note='未附加固定IP',
        )

        due = async_to_sync(_get_due_orders)()
        auto_renew_ids = {item.id for item in due['auto_renew']}
        auto_renew_notice_ids = {item.id for item in due['auto_renew_notice']}
        auto_renew_items = async_to_sync(list_all_auto_renew_cloud_servers)()

        self.assertNotIn(order.id, auto_renew_ids)
        self.assertNotIn(order.id, auto_renew_notice_ids)
        self.assertFalse(any(getattr(item, 'asset_id', None) == asset.id for item in auto_renew_items))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_deleted_retained_static_ip_remains_query_renewable(self):
        recycle_at = timezone.now() + timezone.timedelta(days=7)
        order = CloudServerOrder.objects.create(
            order_no='DELETE-RETAIN-QUERY-1',
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
            status='deleting',
            public_ip='20.20.20.33',
            previous_public_ip='20.20.20.33',
            static_ip_name='StaticIp-delete-retain-query',
            instance_id='delete-retain-query-instance',
            provider_resource_id='delete-retain-query-arn',
            ip_recycle_at=recycle_at,
            mtproxy_secret='eeeeeeeeeeeeeeee',
            mtproxy_port=9528,
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='delete-retain-query-instance',
            instance_id='delete-retain-query-instance',
            provider_resource_id='delete-retain-query-arn',
            public_ip='20.20.20.33',
            previous_public_ip='20.20.20.33',
            actual_expires_at=recycle_at,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            is_active=True,
        )

        async_to_sync(_mark_deleted)(order.id, '实例已删除，固定 IP 保留。')

        queried = async_to_sync(get_cloud_server_by_ip_for_user)('20.20.20.33', self.user.id)
        self.assertIsNotNone(queried)
        self.assertEqual(queried.id, order.id)
        retained_order, plans, err = async_to_sync(list_retained_ip_renewal_plans)(order.id, self.user.id)
        self.assertIsNone(err)
        self.assertIsNotNone(retained_order)
        self.assertTrue(plans)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_provision_expected_ip_failure_schedules_cleanup(self):
        order = CloudServerOrder.objects.create(
            order_no='PROVISION-IP-MISSING-CLEANUP',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=self._aws_test_account(),
            account_label=cloud_account_label(self._aws_test_account()),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            pay_method='balance',
            status='paid',
            paid_at=timezone.now(),
            public_ip='20.20.20.35',
            previous_public_ip='20.20.20.35',
            static_ip_name='StaticIp-provision-ip-missing-cleanup',
            mtproxy_secret='eeeeeeeeeeeeeeee',
            mtproxy_port=9528,
        )
        result = SimpleNamespace(
            ok=True,
            instance_id='provision-ip-missing-cleanup-instance',
            public_ip='54.54.54.54',
            login_user='admin',
            login_password='pw',
            note='AWS 实例已创建',
            static_ip_name='StaticIp-provision-ip-missing-cleanup',
            private_key_path='',
        )

        with patch('cloud.provisioning.create_aws_instance', new=AsyncMock(return_value=result)), \
            patch('cloud.provisioning.public_ip_exists', new=AsyncMock(return_value=(False, '原固定 IP 已不在 AWS 账号中'))):
            saved = async_to_sync(provision_cloud_server)(order.id)

        self.assertEqual(saved.status, 'failed')
        self.assertIsNotNone(saved.delete_at)
        self.assertIn('创建流程未完成', saved.provision_note)
        self.assertIn('原固定 IP 已不在 AWS 账号中', saved.provision_note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_retained_ip_postcheck_reuses_completed_recovery_order(self):
        recycle_at = timezone.now() + timezone.timedelta(days=7)
        source = CloudServerOrder.objects.create(
            order_no='RETAINED-POSTCHECK-SOURCE',
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
            public_ip='20.20.20.34',
            previous_public_ip='20.20.20.34',
            static_ip_name='StaticIp-retained-postcheck-source',
            instance_id='',
            ip_recycle_at=recycle_at,
            mtproxy_secret='eeeeeeeeeeeeeeee',
            mtproxy_port=9528,
        )
        completed_recovery = CloudServerOrder.objects.create(
            order_no='RETAINED-POSTCHECK-RECOVERY',
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
            public_ip='20.20.20.34',
            previous_public_ip='20.20.20.34',
            static_ip_name='StaticIp-retained-postcheck-source',
            instance_id='retained-postcheck-recovered-instance',
            replacement_for=source,
        )

        result, err = async_to_sync(run_cloud_server_renewal_postcheck)(source.id)

        self.assertEqual(result.id, completed_recovery.id)
        self.assertEqual(err, '固定 IP 保留期续费，已进入自动恢复流程。')
        self.assertEqual(CloudServerOrder.objects.filter(replacement_for=source).count(), 1)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_cleanup_old_records_keeps_deleted_order_until_retained_ip_window_ends(self):
        from core.management.commands.cleanup_old_records import Command

        cutoff = timezone.now() - timezone.timedelta(days=100)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-CLEANUP-KEEPS-RETAINED-IP',
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
            public_ip='20.20.20.36',
            previous_public_ip='20.20.20.36',
            static_ip_name='StaticIp-cleanup-retained-window',
            ip_recycle_at=timezone.now() + timezone.timedelta(days=7),
            instance_id='',
        )
        CloudServerOrder.objects.filter(id=order.id).update(created_at=cutoff - timezone.timedelta(days=1))

        cleanup_qs = CloudServerOrder.objects.filter(created_at__lt=cutoff).filter(Command._cloud_order_cleanup_filter(cutoff))
        self.assertFalse(cleanup_qs.filter(id=order.id).exists())

        CloudServerOrder.objects.filter(id=order.id).update(ip_recycle_at=cutoff - timezone.timedelta(days=1))
        self.assertFalse(cleanup_qs.filter(id=order.id).exists())

        CloudServerOrder.objects.filter(id=order.id).update(
            public_ip='',
            previous_public_ip='',
            static_ip_name='',
            server_name='',
            instance_id='',
            provider_resource_id='',
            mtproxy_host='',
        )
        self.assertTrue(cleanup_qs.filter(id=order.id).exists())

    # 功能：验证旧记录清理不会把非终态云订单纳入删除候选。
    def test_cleanup_old_records_keeps_non_terminal_cloud_orders(self):
        from core.management.commands.cleanup_old_records import Command

        cutoff = timezone.now() - timezone.timedelta(days=100)
        old_expiry = cutoff - timezone.timedelta(days=30)
        statuses = ['completed', 'expiring', 'renew_pending', 'suspended', 'deleting']
        for index, status in enumerate(statuses):
            order = CloudServerOrder.objects.create(
                order_no=f'HB-TEST-CLEANUP-KEEPS-ACTIVE-{index}',
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
                status=status,
                public_ip=f'20.20.21.{index + 1}',
                previous_public_ip=f'20.20.21.{index + 1}',
                ip_recycle_at=cutoff - timezone.timedelta(days=1),
                instance_id=f'cleanup-active-instance-{index}',
            )
            CloudServerOrder.objects.filter(id=order.id).update(created_at=cutoff - timezone.timedelta(days=1))
            CloudAsset.objects.create(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_ORDER,
                order=order,
                user=self.user,
                provider=order.provider,
                region_code=order.region_code,
                region_name=order.region_name,
                asset_name=f'cleanup-active-asset-{index}',
                public_ip=order.public_ip,
                actual_expires_at=old_expiry,
                status=CloudAsset.STATUS_RUNNING,
                is_active=True,
            )

        cleanup_qs = CloudServerOrder.objects.filter(created_at__lt=cutoff).filter(Command._cloud_order_cleanup_filter(cutoff))

        self.assertFalse(cleanup_qs.filter(status__in=statuses).exists())

    # 功能：验证旧记录清理不会断开仍有活跃资产的终态云订单。
    def test_cleanup_old_records_keeps_terminal_cloud_order_with_live_asset(self):
        from core.management.commands.cleanup_old_records import Command

        cutoff = timezone.now() - timezone.timedelta(days=100)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-CLEANUP-KEEPS-LIVE-ASSET',
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
            status='failed',
            public_ip='20.20.22.1',
            previous_public_ip='20.20.22.1',
            instance_id='cleanup-live-asset-instance',
        )
        CloudServerOrder.objects.filter(id=order.id).update(created_at=cutoff - timezone.timedelta(days=1))
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='cleanup-live-asset',
            public_ip=order.public_ip,
            actual_expires_at=cutoff - timezone.timedelta(days=30),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        cleanup_qs = CloudServerOrder.objects.filter(created_at__lt=cutoff).filter(Command._cloud_order_cleanup_filter(cutoff))

        self.assertFalse(cleanup_qs.filter(id=order.id).exists())

    # 功能：验证旧记录清理不会删除仍有待运维云资源线索的终态订单。
    def test_cleanup_old_records_keeps_terminal_cloud_order_with_pending_resource_context(self):
        from core.management.commands.cleanup_old_records import Command

        cutoff = timezone.now() - timezone.timedelta(days=100)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-CLEANUP-KEEPS-PENDING-RESOURCE',
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
            status='failed',
            public_ip='20.20.22.3',
            server_name='cleanup-pending-resource',
            instance_id='cleanup-pending-resource',
            delete_at=timezone.now() + timezone.timedelta(days=1),
        )
        CloudServerOrder.objects.filter(id=order.id).update(created_at=cutoff - timezone.timedelta(days=1))

        cleanup_qs = CloudServerOrder.objects.filter(created_at__lt=cutoff).filter(Command._cloud_order_cleanup_filter(cutoff))

        self.assertFalse(cleanup_qs.filter(id=order.id).exists())

    # 功能：验证旧记录清理不会删除仍有资源线索的已删机云订单。
    def test_cleanup_old_records_keeps_deleted_order_with_resource_context(self):
        from core.management.commands.cleanup_old_records import Command

        cutoff = timezone.now() - timezone.timedelta(days=100)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-CLEANUP-KEEPS-DELETED-RESOURCE',
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
            public_ip='20.20.22.4',
            previous_public_ip='20.20.22.4',
            static_ip_name='StaticIp-cleanup-deleted-resource',
            server_name='cleanup-deleted-resource',
            instance_id='cleanup-deleted-resource',
            provider_resource_id='cleanup-deleted-resource',
            ip_recycle_at=cutoff - timezone.timedelta(days=1),
        )
        CloudServerOrder.objects.filter(id=order.id).update(created_at=cutoff - timezone.timedelta(days=1))

        cleanup_qs = CloudServerOrder.objects.filter(created_at__lt=cutoff).filter(Command._cloud_order_cleanup_filter(cutoff))

        self.assertFalse(cleanup_qs.filter(id=order.id).exists())

    # 功能：验证终态云订单只剩已删除资产时仍可进入旧记录清理候选。
    def test_cleanup_old_records_allows_terminal_cloud_order_with_deleted_asset(self):
        from core.management.commands.cleanup_old_records import Command

        cutoff = timezone.now() - timezone.timedelta(days=100)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-CLEANUP-ALLOWS-DELETED-ASSET',
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
            status='failed',
            public_ip='',
            previous_public_ip='20.20.22.2',
            instance_id='',
        )
        CloudServerOrder.objects.filter(id=order.id).update(created_at=cutoff - timezone.timedelta(days=1))
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='cleanup-deleted-asset',
            previous_public_ip='20.20.22.2',
            actual_expires_at=cutoff - timezone.timedelta(days=30),
            status=CloudAsset.STATUS_DELETED,
            is_active=False,
        )

        cleanup_qs = CloudServerOrder.objects.filter(created_at__lt=cutoff).filter(Command._cloud_order_cleanup_filter(cutoff))

        self.assertTrue(cleanup_qs.filter(id=order.id).exists())

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_tick_releases_retained_static_ip_after_recycle_due(self):
        SiteConfig.set('cloud_ip_delete_enabled', '1')
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

        # 测试类：组织 FakeLightsailClient 相关的回归测试。
        class FakeLightsailClient:
            # 功能：处理 云资产、云订单和生命周期 中的 release static ip 业务流程。
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {'operations': [{'id': 'op-retained-release'}]}

        with patch('cloud.lifecycle._aws_client', return_value=FakeLightsailClient()), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=True):
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

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_tick_releases_retained_static_ip_when_asset_already_deleted(self):
        SiteConfig.set('cloud_ip_delete_enabled', '1')
        recycle_due_at = timezone.now() - timezone.timedelta(days=2)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RECYCLE-DELETED-ASSET',
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
            public_ip='20.20.20.21',
            previous_public_ip='20.20.20.21',
            static_ip_name='StaticIp-retained-deleted-asset',
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
            asset_name='StaticIp-retained-deleted-asset',
            public_ip='20.20.20.21',
            previous_public_ip='20.20.20.21',
            actual_expires_at=recycle_due_at,
            status=CloudAsset.STATUS_DELETED,
            provider_status='固定IP保留中-实例已删除',
            note='固定IP保留中',
            is_active=False,
        )

        released = []

        # 测试类：组织 FakeLightsailClient 相关的回归测试。
        class FakeLightsailClient:
            # 功能：处理 云资产、云订单和生命周期 中的 release static ip 业务流程。
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {'operations': [{'id': 'op-retained-release-deleted-asset'}]}

        with patch('cloud.lifecycle._aws_client', return_value=FakeLightsailClient()), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=True):
            async_to_sync(lifecycle_tick)()

        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(released, ['StaticIp-retained-deleted-asset'])
        self.assertIsNone(order.ip_recycle_at)
        self.assertEqual(order.public_ip, '')
        self.assertEqual(order.previous_public_ip, '20.20.20.21')
        self.assertEqual(order.static_ip_name, '')
        self.assertIsNone(asset.public_ip)
        self.assertEqual(asset.previous_public_ip, '20.20.20.21')
        self.assertIn('AWS 固定 IP 已真实释放', order.provision_note or '')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_tick_recycle_respects_ip_delete_time_window(self):
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RECYCLE-WINDOW-BLOCKED',
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
            public_ip='20.20.20.22',
            previous_public_ip='20.20.20.22',
            static_ip_name='StaticIp-retained-window-blocked',
            ip_recycle_at=timezone.now() - timezone.timedelta(minutes=1),
            instance_id='',
        )
        due_order = CloudServerOrder.objects.get(id=order.id)
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [due_order],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=False) as safe_time, \
            patch('cloud.lifecycle._release_order_static_ip', new_callable=AsyncMock) as release_mock:
            async_to_sync(lifecycle_tick)()

        safe_time.assert_called_once()
        release_mock.assert_not_awaited()
        order.refresh_from_db()
        self.assertEqual(order.status, 'deleted')
        self.assertIsNotNone(order.ip_recycle_at)

    # 功能：验证生命周期执行器按关机、删机、IP 删除三阶段串行推进，不在同一轮连跳破坏性步骤。
    def test_lifecycle_tick_serializes_shutdown_delete_and_ip_release_stages(self):
        now = timezone.now()
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-LIFECYCLE-STAGE-SERIAL',
            user=self.user,
            plan=self.plan,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='completed',
            public_ip='20.20.20.28',
            previous_public_ip='20.20.20.28',
            static_ip_name='StaticIp-stage-serial',
            instance_id='stage-serial-instance',
            suspend_at=now - timezone.timedelta(minutes=30),
            delete_at=now - timezone.timedelta(minutes=20),
            ip_recycle_at=now - timezone.timedelta(minutes=10),
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='stage-serial-instance',
            instance_id='stage-serial-instance',
            public_ip=order.public_ip,
            previous_public_ip=order.previous_public_ip,
            actual_expires_at=now - timezone.timedelta(days=5),
            status=CloudAsset.STATUS_RUNNING,
            shutdown_enabled=True,
            server_delete_enabled=True,
            ip_delete_enabled=True,
            is_active=True,
        )
        with patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._process_auto_renew_retry_tasks', new_callable=AsyncMock), \
            patch('cloud.lifecycle._cloud_expiry_notice_payload', new_callable=AsyncMock, return_value={'valid': True}), \
            patch('cloud.lifecycle.cloud_server_shutdown_enabled', return_value=True), \
            patch('cloud.lifecycle.cloud_server_delete_enabled', return_value=True), \
            patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_suspend_time', return_value=True), \
            patch('cloud.lifecycle._is_cloud_delete_safe_time', return_value=True), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=True), \
            patch('cloud.lifecycle._stop_instance', new=AsyncMock(return_value=(True, '关机成功'))), \
            patch('cloud.lifecycle._delete_instance', new=AsyncMock(return_value=(True, '删机成功，固定 IP 保留'))), \
            patch('cloud.lifecycle._release_order_static_ip', new=AsyncMock(return_value=(True, '释放成功'))) as release_mock:
            async_to_sync(lifecycle_tick)()
            order.refresh_from_db()

            self.assertEqual(order.status, 'suspended')
            self.assertEqual(CloudLifecycleTask.objects.filter(order=order, task_type=CloudLifecycleTask.TASK_SUSPEND, status=CloudLifecycleTask.STATUS_DONE).count(), 1)
            self.assertEqual(CloudLifecycleTask.objects.filter(order=order, task_type=CloudLifecycleTask.TASK_DELETE).count(), 0)
            release_mock.assert_not_awaited()

            async_to_sync(lifecycle_tick)()
            order.refresh_from_db()

            self.assertEqual(order.status, 'deleted')
            self.assertEqual(CloudLifecycleTask.objects.filter(order=order, task_type=CloudLifecycleTask.TASK_DELETE, status=CloudLifecycleTask.STATUS_DONE).count(), 1)
            self.assertEqual(CloudLifecycleTask.objects.filter(order=order, task_type=CloudLifecycleTask.TASK_RECYCLE).count(), 0)
            release_mock.assert_not_awaited()

    # 功能：验证启动延迟保护固定IP回收，避免服务启动检查立即执行破坏性动作。
    def test_lifecycle_tick_startup_defer_blocks_order_static_ip_release(self):
        old_recycle_at = timezone.now() - timezone.timedelta(minutes=1)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-STARTUP-DEFER-RECYCLE',
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
            public_ip='20.20.20.25',
            previous_public_ip='20.20.20.25',
            static_ip_name='StaticIp-startup-defer-recycle',
            ip_recycle_at=old_recycle_at,
            instance_id='',
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='StaticIp-startup-defer-recycle',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-startup-defer-recycle',
            public_ip='20.20.20.25',
            previous_public_ip='20.20.20.25',
            actual_expires_at=old_recycle_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='固定IP仍存在但未附加',
            is_active=False,
        )
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [CloudServerOrder.objects.get(id=order.id)],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._cloud_expiry_notice_payload', new_callable=AsyncMock, return_value={'valid': True}), \
            patch('cloud.lifecycle_execution.run_order_static_ip_release', return_value={'ok': True, 'error': None}) as release_mock:
            async_to_sync(lifecycle_tick)(defer_destructive_seconds=600)

        release_mock.assert_not_called()
        order.refresh_from_db()
        self.assertGreater(order.ip_recycle_at, old_recycle_at)

    # 功能：验证迁移旧机删机不被通知载荷的资产到期校验误挡。
    def test_lifecycle_tick_migration_delete_uses_migration_due_without_notice_payload(self):
        old_order = CloudServerOrder.objects.create(
            order_no='HB-TEST-MIGRATION-NO-NOTICE-OLD',
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
            status='deleting',
            public_ip='20.20.20.26',
            instance_id='migration-no-notice-old',
            migration_due_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        CloudServerOrder.objects.create(
            order_no='HB-TEST-MIGRATION-NO-NOTICE-NEW',
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
            public_ip='20.20.20.27',
            replacement_for=old_order,
        )
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[CloudServerOrder.objects.get(id=old_order.id)]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._cloud_expiry_notice_payload', new_callable=AsyncMock, return_value={'valid': False}) as notice_mock, \
            patch('cloud.lifecycle_execution.run_replaced_order_delete', return_value={'ok': True, 'error': None}) as delete_mock:
            async_to_sync(lifecycle_tick)()

        notice_mock.assert_not_awaited()
        delete_mock.assert_called_once_with(
            old_order.id,
            queue_status='scheduled_migration_delete',
            enforce_schedule=True,
        )

    # 功能：验证旧机迁移计划不覆盖资产实际到期事实。
    def test_source_migration_schedule_keeps_asset_actual_expiry(self):
        from cloud.services import _set_source_migration_expiry

        asset_expires_at = timezone.now() + timezone.timedelta(days=31)
        migration_due_at = timezone.now() + timezone.timedelta(days=3)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-MIGRATION-KEEPS-ASSET-EXPIRY',
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
            public_ip='20.20.20.28',
            instance_id='migration-keeps-asset-expiry',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.instance_id,
            public_ip=order.public_ip,
            actual_expires_at=asset_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )

        _set_source_migration_expiry(order, migration_due_at, '单元测试旧机迁移计划调整', '迁移测试')

        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(asset.actual_expires_at, asset_expires_at)
        self.assertEqual(order_asset_expiry(order), asset_expires_at)
        self.assertEqual(order.migration_due_at, migration_due_at)
        self.assertEqual(order.delete_at, migration_due_at + timezone.timedelta(days=3))
        self.assertIn('迁移测试', order.provision_note or '')

    # 功能：验证 AWS 同步删除迁移旧机时不把迁移截止时间写成资产到期事实。
    def test_aws_sync_deleted_migration_order_keeps_asset_actual_expiry(self):
        from cloud.management.commands.sync_aws_assets import _sync_order_deleted_from_cloud

        asset_expires_at = timezone.now() + timezone.timedelta(days=31)
        migration_due_at = timezone.now() + timezone.timedelta(days=3)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-AWS-SYNC-DELETED-KEEPS-ASSET-EXPIRY',
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
            status='deleting',
            public_ip='20.20.20.29',
            instance_id='aws-sync-deleted-keeps-asset-expiry',
            migration_due_at=migration_due_at,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name=order.instance_id,
            instance_id=order.instance_id,
            public_ip=order.public_ip,
            actual_expires_at=asset_expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        expected_lifecycle = compute_order_lifecycle_fields(migration_due_at)

        _sync_order_deleted_from_cloud(order, order.public_ip, source='单元测试 AWS 同步', asset=asset)

        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.status, 'deleted')
        self.assertIsNone(order.public_ip)
        self.assertEqual(asset.actual_expires_at, asset_expires_at)
        self.assertEqual(order_asset_expiry(order), asset_expires_at)
        self.assertEqual(order.migration_due_at, migration_due_at)
        self.assertEqual(order.renew_grace_expires_at, expected_lifecycle['renew_grace_expires_at'])
        self.assertEqual(order.delete_at, expected_lifecycle['delete_at'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_release_order_static_ip_uses_static_ip_asset_name_when_order_name_missing(self):
        from cloud.lifecycle import _release_order_static_ip_sync

        account = self._aws_test_account()
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-RECYCLE-ASSET-NAME-FALLBACK',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleted',
            public_ip='20.20.20.23',
            previous_public_ip='20.20.20.23',
            static_ip_name='',
            ip_recycle_at=timezone.now() - timezone.timedelta(minutes=1),
            instance_id='',
        )
        CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-retained-asset-fallback',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-retained-asset-fallback',
            public_ip='20.20.20.23',
            previous_public_ip='20.20.20.23',
            actual_expires_at=order.ip_recycle_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='固定IP仍存在但未附加',
            is_active=False,
        )
        released = []

        # 测试类：组织 FakeLightsailClient 相关的回归测试。
        class FakeLightsailClient:
            # 功能：读取并返回相关数据；当前函数属于 云资产、云订单和生命周期。
            def get_static_ips(self, **kwargs):
                return {'staticIps': []}

            # 功能：处理 云资产、云订单和生命周期 中的 release static ip 业务流程。
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {'operations': [{'id': 'op-retained-asset-fallback'}]}

        with patch('cloud.lifecycle._aws_client', return_value=FakeLightsailClient()):
            ok, note = _release_order_static_ip_sync(order)

        self.assertTrue(ok)
        self.assertEqual(released, ['StaticIp-retained-asset-fallback'])
        self.assertIn('AWS 固定 IP 已真实释放', note)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_tick_releases_overdue_unattached_static_ip(self):
        SiteConfig.set('cloud_ip_delete_enabled', '1')
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
        server = CloudAsset.objects.create(kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='unattached-static-ip-shadow',
            public_ip='21.21.21.21',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )

        released = []

        # 测试类：组织 FakeLightsailClient 相关的回归测试。
        class FakeLightsailClient:
            # 功能：处理 云资产、云订单和生命周期 中的 release static ip 业务流程。
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {'operations': [{'id': 'op-unattached-release'}]}

        with patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._aws_client', return_value=FakeLightsailClient()), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=True):
            async_to_sync(lifecycle_tick)()

        asset.refresh_from_db()
        server.refresh_from_db()
        self.assertEqual(released, ['StaticIp-unattached-due'])
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(asset.provider_status, '未附加固定IP-已到期删除')
        self.assertIsNone(asset.public_ip)
        self.assertEqual(asset.previous_public_ip, '21.21.21.21')
        self.assertEqual(server.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(server.provider_status, '未附加固定IP-已到期删除')
        self.assertIsNone(server.public_ip)
        self.assertEqual(server.previous_public_ip, '21.21.21.21')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_tick_unattached_ip_uses_ip_delete_time_window(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-unattached-window-blocked',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-unattached-window-blocked',
            public_ip='21.21.21.24',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[asset]), \
            patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=False) as safe_time, \
            patch('cloud.lifecycle._release_unattached_static_ip', new_callable=AsyncMock) as release_mock:
            async_to_sync(lifecycle_tick)()

        safe_time.assert_called_once()
        release_mock.assert_not_awaited()
        asset.refresh_from_db()
        self.assertNotEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(asset.public_ip, '21.21.21.24')

    # 功能：验证启动延迟保护未附加固定IP删除，避免启动检查立即释放云资源。
    def test_lifecycle_tick_startup_defer_blocks_unattached_static_ip_release(self):
        due_at = timezone.now() - timezone.timedelta(days=1)
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-unattached-startup-defer',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-unattached-startup-defer',
            public_ip='21.21.21.25',
            actual_expires_at=due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[asset]), \
            patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle_execution.run_unattached_ip_release', return_value={'ok': True, 'error': None}) as release_mock:
            async_to_sync(lifecycle_tick)(defer_destructive_seconds=600)

        release_mock.assert_not_called()
        asset.refresh_from_db()
        self.assertEqual(asset.status, CloudAsset.STATUS_UNKNOWN)
        self.assertEqual(asset.actual_expires_at, due_at)
        self.assertEqual(asset.public_ip, '21.21.21.25')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_tick_rechecks_orphan_asset_delete_time_before_cloud_delete(self):
        SiteConfig.set('cloud_suspend_after_days', '3')
        SiteConfig.set('cloud_suspend_time', '17:00')
        SiteConfig.set('cloud_delete_after_days', '3')
        SiteConfig.set('cloud_delete_time', '18:00')
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='orphan-recheck-future-delete',
            instance_id='orphan-recheck-future-delete',
            public_ip='21.21.21.22',
            actual_expires_at=timezone.now() - timezone.timedelta(days=10),
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        due_asset = CloudAsset.objects.get(id=asset.id)
        CloudAsset.objects.filter(id=asset.id).update(actual_expires_at=timezone.now() - timezone.timedelta(days=1))
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[due_asset]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle.cloud_server_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_delete_safe_time', return_value=True), \
            patch('cloud.lifecycle._delete_orphan_asset_instance', new_callable=AsyncMock) as delete_mock:
            async_to_sync(lifecycle_tick)()

        delete_mock.assert_not_awaited()
        asset.refresh_from_db()
        self.assertEqual(asset.status, CloudAsset.STATUS_RUNNING)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_lifecycle_tick_rechecks_unattached_ip_delete_time_before_release(self):
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-unattached-recheck-future',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-unattached-recheck-future',
            public_ip='21.21.21.23',
            actual_expires_at=timezone.now() - timezone.timedelta(minutes=1),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            is_active=False,
        )
        due_asset = CloudAsset.objects.get(id=asset.id)
        CloudAsset.objects.filter(id=asset.id).update(actual_expires_at=timezone.now() + timezone.timedelta(days=1))
        due = {
            'renew_notice': [],
            'auto_renew_notice': [],
            'auto_renew': [],
            'delete_notice': [],
            'recycle_notice': [],
            'expire': [],
            'suspend': [],
            'delete': [],
            'recycle': [],
        }

        with patch('cloud.lifecycle._get_due_orders', new_callable=AsyncMock, return_value=due), \
            patch('cloud.lifecycle._get_migration_due_orders', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_orphan_asset_delete_due', new_callable=AsyncMock, return_value=[]), \
            patch('cloud.lifecycle._get_unattached_static_ip_delete_due', new_callable=AsyncMock, return_value=[due_asset]), \
            patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True), \
            patch('cloud.lifecycle._is_cloud_unattached_ip_delete_time', return_value=True), \
            patch('cloud.lifecycle._release_unattached_static_ip', new_callable=AsyncMock) as release_mock:
            async_to_sync(lifecycle_tick)()

        release_mock.assert_not_awaited()
        asset.refresh_from_db()
        self.assertNotEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertEqual(asset.public_ip, '21.21.21.23')

    # 功能：验证生命周期扫描会给无到期时间的未附加固定 IP 自动补齐 15 天后删除计划。
    def test_unattached_static_ip_due_scan_fills_missing_expiry_as_future_plan(self):
        before = timezone.now()
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-unattached-missing-expiry-scan',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-unattached-missing-expiry-scan',
            public_ip='5.5.5.57',
            actual_expires_at=None,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )

        due_assets = async_to_sync(_get_unattached_static_ip_delete_due)()
        asset.refresh_from_db()

        self.assertNotIn(asset.id, {item.id for item in due_assets})
        self.assertIsNotNone(asset.actual_expires_at)
        self.assertGreater(asset.actual_expires_at, before + timezone.timedelta(days=14))
        self.assertLess(asset.actual_expires_at, before + timezone.timedelta(days=16))

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aws_sync_release_static_ip_respects_asset_ip_delete_disabled(self):
        from cloud.management.commands.sync_aws_assets import _release_static_ip_if_due

        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-sync-release-asset-disabled',
            region_hint=self.plan.region_code,
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            region_code=self.plan.region_code,
            asset_name='StaticIp-sync-disabled',
            public_ip='21.21.21.88',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            shutdown_enabled=True,
            ip_delete_enabled=False,
            is_active=False,
        )
        released = []

        # 测试类：组织 FakeClient 相关的回归测试。
        class FakeClient:
            # 功能：处理 云资产、云订单和生命周期 中的 release static ip 业务流程。
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {}

        # 测试类：组织 FakeStyle 相关的回归测试。
        class FakeStyle:
            # 功能：处理 云资产、云订单和生命周期 中的 WARNING 业务流程。
            def WARNING(self, text):
                return text

        # 测试类：组织 FakeStdout 相关的回归测试。
        class FakeStdout:
            style = FakeStyle()

            # 功能：处理 云资产、云订单和生命周期 中的 write 业务流程。
            def write(self, text):
                return None

        with patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True):
            ok = _release_static_ip_if_due(FakeClient(), self.plan.region_code, asset, 'StaticIp-sync-disabled', '', '21.21.21.88', FakeStdout())

        asset.refresh_from_db()
        self.assertFalse(ok)
        self.assertEqual(released, [])
        self.assertEqual(asset.status, CloudAsset.STATUS_UNKNOWN)
        self.assertEqual(asset.provider_status, '未附加固定IP-IP删除开关关闭')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aws_sync_release_static_ip_ignores_shutdown_disabled_account(self):
        from cloud.management.commands.sync_aws_assets import _release_static_ip_if_due

        account = CloudAccountConfig.objects.create(
            provider=CloudAccountConfig.PROVIDER_AWS,
            name='aws-sync-release-account-disabled',
            region_hint=self.plan.region_code,
            access_key='A' * 20,
            secret_key='B' * 40,
            is_active=True,
            shutdown_enabled=False,
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            region_code=self.plan.region_code,
            asset_name='StaticIp-sync-account-disabled',
            public_ip='21.21.21.87',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        released = []

        # 测试类：组织 FakeClient 相关的回归测试。
        class FakeClient:
            # 功能：处理 云资产、云订单和生命周期 中的 release static ip 业务流程。
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {'operations': [{'id': 'op-account-disabled'}]}

        # 测试类：组织 FakeStyle 相关的回归测试。
        class FakeStyle:
            # 功能：处理 云资产、云订单和生命周期 中的 WARNING 业务流程。
            def WARNING(self, text):
                return text

        # 测试类：组织 FakeStdout 相关的回归测试。
        class FakeStdout:
            style = FakeStyle()

            # 功能：处理 云资产、云订单和生命周期 中的 write 业务流程。
            def write(self, text):
                return None

        with patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True):
            ok = _release_static_ip_if_due(FakeClient(), self.plan.region_code, asset, 'StaticIp-sync-account-disabled', '', '21.21.21.87', FakeStdout())

        asset.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(released, ['StaticIp-sync-account-disabled'])
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertIsNone(asset.public_ip)
        self.assertEqual(asset.previous_public_ip, '21.21.21.87')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aws_sync_release_static_ip_respects_global_ip_delete_switch(self):
        from cloud.management.commands.sync_aws_assets import _release_static_ip_if_due

        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            user=self.user,
            provider='aws_lightsail',
            region_code=self.plan.region_code,
            asset_name='StaticIp-sync-global-disabled',
            public_ip='21.21.21.89',
            actual_expires_at=timezone.now() - timezone.timedelta(days=1),
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        released = []

        # 测试类：组织 FakeClient 相关的回归测试。
        class FakeClient:
            # 功能：处理 云资产、云订单和生命周期 中的 release static ip 业务流程。
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {}

        # 测试类：组织 FakeStyle 相关的回归测试。
        class FakeStyle:
            # 功能：处理 云资产、云订单和生命周期 中的 WARNING 业务流程。
            def WARNING(self, text):
                return text

        # 测试类：组织 FakeStdout 相关的回归测试。
        class FakeStdout:
            style = FakeStyle()

            # 功能：处理 云资产、云订单和生命周期 中的 write 业务流程。
            def write(self, text):
                return None

        with patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=False):
            ok = _release_static_ip_if_due(FakeClient(), self.plan.region_code, asset, 'StaticIp-sync-global-disabled', '', '21.21.21.89', FakeStdout())

        asset.refresh_from_db()
        self.assertFalse(ok)
        self.assertEqual(released, [])
        self.assertEqual(asset.status, CloudAsset.STATUS_UNKNOWN)
        self.assertEqual(asset.provider_status, '未附加固定IP-删除IP总开关关闭')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_aws_sync_release_static_ip_clears_retained_order_after_successful_release(self):
        from cloud.management.commands.sync_aws_assets import _release_static_ip_if_due

        account = self._aws_test_account()
        recycle_due_at = timezone.now() - timezone.timedelta(minutes=5)
        order = CloudServerOrder.objects.create(
            order_no='HB-TEST-AWS-SYNC-IP-RELEASE-CLEARS-ORDER',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount='19.00',
            pay_amount='19.00',
            status='deleted',
            public_ip='21.21.21.90',
            previous_public_ip='21.21.21.90',
            static_ip_name='StaticIp-sync-clear-retained-order',
            mtproxy_host='21.21.21.90',
            ip_recycle_at=recycle_due_at,
            ip_recycle_reminder_enabled=True,
            instance_id='',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_AWS_SYNC,
            order=order,
            user=self.user,
            provider='aws_lightsail',
            cloud_account=account,
            account_label=cloud_account_label(account),
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            asset_name='StaticIp-sync-clear-retained-order',
            provider_resource_id='arn:aws:lightsail:ap-southeast-1:123456789012:StaticIp/StaticIp-sync-clear-retained-order',
            public_ip='21.21.21.90',
            previous_public_ip='21.21.21.90',
            actual_expires_at=recycle_due_at,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            note='未附加固定IP',
            is_active=False,
        )
        released = []

        # 测试类：组织 FakeClient 相关的回归测试。
        class FakeClient:
            # 功能：处理 云资产、云订单和生命周期 中的 release static ip 业务流程。
            def release_static_ip(self, staticIpName):
                released.append(staticIpName)
                return {'operations': [{'id': 'op-aws-sync-retained-clear'}]}

        # 测试类：组织 FakeStyle 相关的回归测试。
        class FakeStyle:
            # 功能：处理 云资产、云订单和生命周期 中的 WARNING 业务流程。
            def WARNING(self, text):
                return text

        # 测试类：组织 FakeStdout 相关的回归测试。
        class FakeStdout:
            style = FakeStyle()

            # 功能：处理 云资产、云订单和生命周期 中的 write 业务流程。
            def write(self, text):
                return None

        with patch('cloud.lifecycle.cloud_ip_delete_enabled', return_value=True):
            ok = _release_static_ip_if_due(
                FakeClient(),
                self.plan.region_code,
                asset,
                'StaticIp-sync-clear-retained-order',
                '',
                '21.21.21.90',
                FakeStdout(),
            )

        asset.refresh_from_db()
        order.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(released, ['StaticIp-sync-clear-retained-order'])
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)
        self.assertIsNone(asset.public_ip)
        self.assertEqual(asset.previous_public_ip, '21.21.21.90')
        self.assertEqual(order.public_ip, '')
        self.assertEqual(order.previous_public_ip, '21.21.21.90')
        self.assertEqual(order.static_ip_name, '')
        self.assertEqual(order.mtproxy_host, '')
        self.assertIsNone(order.ip_recycle_at)
        self.assertIsNotNone(order.recycle_notice_sent_at)
        self.assertFalse(order.ip_recycle_reminder_enabled)
        self.assertIn('AWS 同步删除未附加固定 IP', order.provision_note or '')
        self.assertTrue(CloudIpLog.objects.filter(order=order, asset=asset, event_type=CloudIpLog.EVENT_RECYCLED).exists())


# 测试类：组织 CloudOrderStatusDashboardSyncTestCase 相关的回归测试。
class CloudOrderStatusDashboardSyncTestCase(TestCase):
    # 功能：处理 云资产、云订单和生命周期 中的 setUp 业务流程。
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = get_user_model().objects.create_user(username='status-admin', password='x', is_staff=True, is_superuser=True)
        self.user = TelegramUser.objects.create(tg_user_id=991001, username='status_sync_user')
        self.plan = CloudServerPlan.objects.create(
            provider='aws_lightsail',
            region_code='ap-southeast-1',
            region_name='新加坡',
            plan_name='Status Sync',
            price=Decimal('19.000000'),
            currency='USDT',
            is_active=True,
        )

    # 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
    def _create_order_with_primary_asset(self):
        expires_at = timezone.now() + timezone.timedelta(days=20)
        order = CloudServerOrder.objects.create(
            order_no='STATUS-SYNC-ORDER',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount=Decimal('19.000000'),
            pay_amount=Decimal('19.000000000'),
            pay_method='balance',
            status='completed',
            public_ip='203.0.113.10',
        )
        asset = CloudAsset.objects.create(
            kind=CloudAsset.KIND_SERVER,
            source=CloudAsset.SOURCE_ORDER,
            order=order,
            user=self.user,
            provider=order.provider,
            region_code=order.region_code,
            region_name=order.region_name,
            asset_name='status-sync-asset',
            public_ip=order.public_ip,
            actual_expires_at=expires_at,
            status=CloudAsset.STATUS_RUNNING,
            is_active=True,
        )
        return order, asset

    # 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
    def _post_json(self, view, path, payload, *args):
        request = self.factory.post(path, data=json.dumps(payload), content_type='application/json')
        SessionMiddleware(lambda req: None).process_request(request)
        request.session['_auth_user_id'] = str(self.admin.pk)
        request.session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
        request.session['_auth_user_hash'] = self.admin.get_session_auth_hash()
        request.session.save()
        request.user = AnonymousUser()
        request.META['HTTP_AUTHORIZATION'] = f'Bearer session-{request.session.session_key}'
        return view(request, *args)

    # 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
    def _get_json(self, view, path, *args):
        request = self.factory.get(path)
        SessionMiddleware(lambda req: None).process_request(request)
        request.session['_auth_user_id'] = str(self.admin.pk)
        request.session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
        request.session['_auth_user_hash'] = self.admin.get_session_auth_hash()
        request.session.save()
        request.user = AnonymousUser()
        request.META['HTTP_AUTHORIZATION'] = f'Bearer session-{request.session.session_key}'
        return view(request, *args)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_status_endpoint_syncs_primary_asset_status(self):
        order, asset = self._create_order_with_primary_asset()

        response = self._post_json(update_cloud_order_status, f'/admin/cloud-orders/{order.id}/status/', {'status': 'suspended'}, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.status, 'suspended')
        self.assertFalse(asset.is_active)
        self.assertEqual(asset.status, CloudAsset.STATUS_STOPPED)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_order_detail_status_edit_syncs_primary_asset_status(self):
        order, asset = self._create_order_with_primary_asset()

        response = self._post_json(cloud_order_detail, f'/admin/cloud-orders/{order.id}/', {'status': 'deleted'}, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.status, 'deleted')
        self.assertFalse(asset.is_active)
        self.assertEqual(asset.status, CloudAsset.STATUS_DELETED)

    # 功能：验证已删除订单详情不会继续暴露历史代理链路和完整公网 IP。
    def test_deleted_order_detail_masks_proxy_links_and_historical_ips(self):
        secret = '0123456789abcdef0123456789abcdef'
        mtproxy_link = f'tg://proxy?server=198.51.100.99&port=443&secret={secret}'
        socks_link = 'socks5://user:password@198.51.100.99:1080'
        order = CloudServerOrder.objects.create(
            order_no='STATUS-SYNC-DELETED-MASK',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount=Decimal('19.000000'),
            pay_amount=Decimal('19.000000000'),
            pay_method='balance',
            status='deleted',
            public_ip='198.51.100.99',
            previous_public_ip='198.51.100.98',
            mtproxy_host='198.51.100.99',
            mtproxy_port=443,
            mtproxy_secret=secret,
            mtproxy_link=mtproxy_link,
            proxy_links=[
                {'name': '主代理 mtg', 'url': mtproxy_link, 'server': '198.51.100.99', 'port': '443', 'secret': secret},
                {'name': '备用 socks5', 'url': socks_link, 'server': '198.51.100.99', 'port': '1080'},
            ],
            provision_note=f'创建完成：{mtproxy_link}\n备用：{socks_link}\n旧IP=198.51.100.98 secret={secret}',
        )

        response = self._get_json(cloud_order_detail, f'/admin/cloud-orders/{order.id}/', order.id)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())['data']
        response_text = response.content.decode()
        self.assertEqual(payload['status'], 'deleted')
        self.assertEqual(payload['order_no'], 'STATUS-SYNC-DELETED-MASK')
        self.assertEqual(payload['mtproxy_link'], '')
        self.assertEqual(payload['proxy_links'], [])
        self.assertEqual(payload['public_ip'], '198.51.100.*')
        self.assertEqual(payload['previous_public_ip'], '198.51.100.*')
        self.assertEqual(payload['mtproxy_host'], '198.51.100.*')
        self.assertIn('代理链路已脱敏', payload['provision_note'])
        self.assertNotIn('secret=', response_text)
        self.assertNotIn(mtproxy_link, response_text)
        self.assertNotIn(socks_link, response_text)
        self.assertNotIn(secret, response_text)
        self.assertNotIn('198.51.100.99', response_text)
        self.assertNotIn('198.51.100.98', response_text)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields(self):
        order, asset = self._create_order_with_primary_asset()
        asset_expiry = timezone.now() + timezone.timedelta(days=20)
        asset.actual_expires_at = asset_expiry
        asset.save(update_fields=['actual_expires_at'])
        expires_at = timezone.now() + timezone.timedelta(days=45)

        response = self._post_json(cloud_order_detail, f'/admin/cloud-orders/{order.id}/', {
            'server_name': 'manual-edited-name',
            'public_ip': '203.0.113.88',
            'instance_id': 'manual-edited-instance',
            'provider_resource_id': 'manual-edited-resource',
            'mtproxy_host': '203.0.113.88',
            'mtproxy_link': 'tg://proxy?server=203.0.113.88&port=443&secret=abcdef',
            'mtproxy_port': 443,
            'actual_expires_at': expires_at.isoformat(),
        }, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.previous_public_ip, '203.0.113.10')
        self.assertEqual(asset.asset_name, 'manual-edited-name')
        self.assertEqual(asset.public_ip, '203.0.113.88')
        self.assertEqual(asset.previous_public_ip, '203.0.113.10')
        self.assertEqual(asset.instance_id, 'manual-edited-instance')
        self.assertEqual(asset.provider_resource_id, 'manual-edited-resource')
        self.assertEqual(asset.mtproxy_host, '203.0.113.88')
        self.assertEqual(asset.mtproxy_link, 'tg://proxy?server=203.0.113.88&port=443&secret=abcdef')
        self.assertEqual(order.mtproxy_link, 'tg://proxy?server=203.0.113.88&port=443&secret=abcdef')
        self.assertEqual(order.mtproxy_secret, 'abcdef')
        self.assertEqual(asset.mtproxy_port, 443)
        self.assertEqual(order.mtproxy_port, 443)
        self.assertEqual(order.proxy_links[0]['url'], 'tg://proxy?server=203.0.113.88&port=443&secret=abcdef')
        self.assertEqual(asset.proxy_links[0]['url'], 'tg://proxy?server=203.0.113.88&port=443&secret=abcdef')
        self.assertEqual(asset.actual_expires_at, expires_at)

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_order_detail_manual_secret_edit_syncs_primary_asset(self):
        order, asset = self._create_order_with_primary_asset()
        order.mtproxy_secret = 'old-secret'
        order.save(update_fields=['mtproxy_secret'])
        asset.mtproxy_secret = 'old-secret'
        asset.save(update_fields=['mtproxy_secret'])

        response = self._post_json(cloud_order_detail, f'/admin/cloud-orders/{order.id}/', {
            'mtproxy_secret': 'new-secret',
        }, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.mtproxy_secret, 'new-secret')
        self.assertEqual(asset.mtproxy_secret, 'new-secret')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_order_detail_manual_secret_edit_updates_main_link_and_proxy_links(self):
        order, asset = self._create_order_with_primary_asset()
        old_link = 'tg://proxy?server=203.0.113.10&port=443&secret=old-secret&tag=keep'
        backup_link = 'tg://proxy?server=203.0.113.10&port=8443&secret=backup-secret'
        order.mtproxy_link = old_link
        order.mtproxy_secret = 'old-secret'
        order.mtproxy_port = 443
        order.proxy_links = [
            {'name': '主代理 mtg', 'url': old_link, 'server': '203.0.113.10', 'port': '443', 'secret': 'old-secret'},
            {'name': '备用代理', 'url': backup_link, 'server': '203.0.113.10', 'port': '8443', 'secret': 'backup-secret'},
        ]
        order.save(update_fields=['mtproxy_link', 'mtproxy_secret', 'mtproxy_port', 'proxy_links'])
        asset.mtproxy_link = old_link
        asset.mtproxy_secret = 'old-secret'
        asset.mtproxy_port = 443
        asset.proxy_links = list(order.proxy_links)
        asset.save(update_fields=['mtproxy_link', 'mtproxy_secret', 'mtproxy_port', 'proxy_links'])

        response = self._post_json(cloud_order_detail, f'/admin/cloud-orders/{order.id}/', {
            'mtproxy_secret': 'new-secret',
        }, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertIn('secret=new-secret', order.mtproxy_link)
        self.assertNotIn('secret=old-secret', order.mtproxy_link)
        self.assertEqual(order.proxy_links[0]['secret'], 'new-secret')
        self.assertIn('secret=new-secret', order.proxy_links[0]['url'])
        self.assertIn(backup_link, [item.get('url') for item in order.proxy_links])
        self.assertEqual(asset.mtproxy_link, order.mtproxy_link)
        self.assertEqual(asset.proxy_links[0]['secret'], 'new-secret')
        self.assertIn('secret=new-secret', asset.proxy_links[0]['url'])

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_order_detail_manual_previous_ip_edit_syncs_primary_asset(self):
        order, asset = self._create_order_with_primary_asset()

        response = self._post_json(cloud_order_detail, f'/admin/cloud-orders/{order.id}/', {
            'previous_public_ip': '203.0.113.9',
        }, order.id)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(order.public_ip, '203.0.113.10')
        self.assertEqual(order.previous_public_ip, '203.0.113.9')
        self.assertEqual(asset.previous_public_ip, '203.0.113.9')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_delete_cloud_order_blocks_physical_delete_when_cloud_records_exist(self):
        order, asset = self._create_order_with_primary_asset()

        response = self._post_json(delete_cloud_order, f'/admin/cloud-orders/{order.id}/delete/', {}, order.id)

        self.assertEqual(response.status_code, 409)
        self.assertTrue(CloudServerOrder.objects.filter(id=order.id).exists())
        self.assertTrue(CloudAsset.objects.filter(id=asset.id, order=order).exists())

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_delete_cloud_order_allows_unlinked_pending_order(self):
        order = CloudServerOrder.objects.create(
            order_no='DELETE-UNLINKED-PENDING',
            user=self.user,
            plan=self.plan,
            provider=self.plan.provider,
            region_code=self.plan.region_code,
            region_name=self.plan.region_name,
            plan_name=self.plan.plan_name,
            quantity=1,
            currency='USDT',
            total_amount=Decimal('19.000000'),
            pay_amount=Decimal('19.000000000'),
            pay_method='balance',
            status='pending',
        )

        response = self._post_json(delete_cloud_order, f'/admin/cloud-orders/{order.id}/delete/', {}, order.id)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CloudServerOrder.objects.filter(id=order.id).exists())


# 测试类：组织 DashboardTronBalanceQueryTestCase 相关的回归测试。
class DashboardTronBalanceQueryTestCase(TestCase):
    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_resource_monitor_uses_runtime_trongrid_base_url(self):
        from cloud.resource_monitor import _fetch_account_resource

        captured = {}

        # 测试类：组织 FakeResponse 相关的回归测试。
        class FakeResponse:
            status_code = 200

            # 功能：处理 云资产、云订单和生命周期 中的 raise for status 业务流程。
            def raise_for_status(self):
                return None

            # 功能：处理 云资产、云订单和生命周期 中的 json 业务流程。
            def json(self):
                return {
                    'freeNetLimit': 100,
                    'freeNetUsed': 10,
                    'NetLimit': 50,
                    'NetUsed': 5,
                    'EnergyLimit': 200,
                    'EnergyUsed': 30,
                }

        # 测试类：组织 FakeAsyncClient 相关的回归测试。
        class FakeAsyncClient:
            # 功能：初始化对象状态和依赖。
            def __init__(self, *args, **kwargs):
                pass

            # 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
            async def __aenter__(self):
                return self

            # 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
            async def __aexit__(self, exc_type, exc, tb):
                return False

            # 功能：处理 云资产、云订单和生命周期 中的 post 业务流程。
            async def post(self, url, json=None, headers=None):
                captured['url'] = url
                return FakeResponse()

        # 功能：处理 云资产、云订单和生命周期 中的 fake build headers 业务流程。
        async def fake_build_headers():
            return {'TRON-PRO-API-KEY': 'resource-key'}

        with (
            patch('cloud.resource_monitor.get_runtime_config', return_value='https://custom.trongrid.example/'),
            patch('cloud.resource_monitor.build_trongrid_headers', new=fake_build_headers),
            patch('cloud.resource_monitor.httpx.AsyncClient', new=FakeAsyncClient),
        ):
            energy, bandwidth = async_to_sync(_fetch_account_resource)('TResourceMonitorAddress')

        self.assertEqual(energy, 170)
        self.assertEqual(bandwidth, 135)
        self.assertEqual(captured['url'], 'https://custom.trongrid.example/wallet/getaccountresource')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_resource_detail_cache_is_scoped_per_user_for_same_address_time(self):
        from cloud.resource_monitor import _cache_resource_detail, get_resource_detail

        first_key = _cache_resource_detail('TResourceMonitorAddress:2026-05-16 08:00:00', {'user_id': 1, 'remark': 'first'})
        second_key = _cache_resource_detail('TResourceMonitorAddress:2026-05-16 08:00:00', {'user_id': 2, 'remark': 'second'})

        self.assertNotEqual(first_key, second_key)
        self.assertEqual(get_resource_detail(first_key)['remark'], 'first')
        self.assertEqual(get_resource_detail(second_key)['remark'], 'second')

    # 功能：验证相关业务场景和回归行为；当前函数属于 云资产、云订单和生命周期。
    def test_fetch_address_chain_balances_uses_resolved_headers(self):
        captured = {}

        # 测试类：组织 FakeResponse 相关的回归测试。
        class FakeResponse:
            status_code = 200

            # 功能：处理 云资产、云订单和生命周期 中的 raise for status 业务流程。
            def raise_for_status(self):
                return None

            # 功能：处理 云资产、云订单和生命周期 中的 json 业务流程。
            def json(self):
                return {
                    'data': [{
                        'balance': 2000000,
                        'trc20': [{'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t': '3000000'}],
                    }],
                }

        # 测试类：组织 FakeClient 相关的回归测试。
        class FakeClient:
            # 功能：初始化对象状态和依赖。
            def __init__(self, *args, **kwargs):
                pass

            # 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
            def __enter__(self):
                return self

            # 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
            def __exit__(self, exc_type, exc, tb):
                return False

            # 功能：处理 云资产、云订单和生命周期 中的 get 业务流程。
            def get(self, url, headers=None):
                captured['headers'] = headers
                return FakeResponse()

        # 功能：处理 云资产、云订单和生命周期 中的 fake get redis 业务流程。
        async def fake_get_redis():
            return None

        # 功能：处理 云资产、云订单和生命周期 中的 fake build headers 业务流程。
        async def fake_build_headers():
            return {'TRON-PRO-API-KEY': 'dashboard-key'}

        with (
            patch('cloud.api_monitors.get_redis', new=fake_get_redis),
            patch('cloud.api_monitors.build_trongrid_headers', new=fake_build_headers),
            patch('cloud.api_monitors.httpx.Client', new=FakeClient),
        ):
            usdt_balance, trx_balance, error = _fetch_address_chain_balances('TDashboardBalanceAddress')

        self.assertIsNone(error)
        self.assertEqual(captured['headers'], {'TRON-PRO-API-KEY': 'dashboard-key'})
        self.assertEqual(usdt_balance, Decimal('3'))
        self.assertEqual(trx_balance, Decimal('2'))
