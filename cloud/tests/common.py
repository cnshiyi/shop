import asyncio
import json
import os
import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from bot.api import _shutdown_log_items, _unattached_ip_delete_items, lifecycle_plans, refresh_lifecycle_plan_table, update_lifecycle_plan_note
from bot.models import TelegramGroupFilter, TelegramUser
from cloud.bootstrap import _build_mtproxy_script, _extract_tg_links
from cloud.models import CloudAsset, CloudAutoRenewPatrolLog, CloudAutoRenewRetryTask, CloudIpLog, CloudLifecyclePlan, CloudLifecyclePlanNote, CloudNoticePlan, CloudServerOrder, CloudServerPlan, CloudUserNoticeLog, DailyAddressStat, Server
from cloud.lifecycle import _apply_notice_schedule_to_order, _auto_renew_candidate_users, _enqueue_auto_renew_retry, _get_due_orders, _get_migration_due_orders, _get_orphan_asset_delete_due, _get_unattached_static_ip_delete_due, _group_balance_lines_for_orders, _is_cloud_delete_safe_time, _is_cloud_suspend_time, _mark_deleted, _mark_suspended, _next_cloud_action_run_at, _notice_plan_text, _process_auto_renew_retry_tasks, _run_auto_renew, _send_logged_cloud_notice, _send_order_notice_batch, auto_renew_patrol_tick, daily_expiry_summary_tick, lifecycle_tick, sync_server_status_tick
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
    _mark_failed,
    _mark_provisioning_start,
    _mark_rebuild_source_pending_deletion,
    _mark_success,
    provision_cloud_server,
)
from cloud.services import _cloud_asset_deleted_or_missing, apply_cloud_server_renewal, create_cloud_server_order, create_cloud_server_rebuild_order, create_cloud_server_renewal, create_cloud_server_renewal_by_public_query, create_cloud_server_renewal_for_user, create_cloud_server_upgrade_order, ensure_cloud_asset_operation_order, get_cloud_server_by_ip, get_cloud_server_by_ip_for_user, get_group_proxy_asset_detail, get_proxy_asset_by_ip_for_admin, get_proxy_asset_by_ip_for_user, get_user_proxy_asset_detail, list_all_auto_renew_cloud_servers, list_cloud_asset_renewal_plans, list_cloud_server_upgrade_plans, list_group_cloud_servers, list_retained_ip_renewal_plans, list_retained_ip_renewal_plans_by_asset, list_user_cloud_servers, mark_cloud_server_ip_change_requested, mark_cloud_server_reinit_requested, pay_cloud_server_order_with_balance, pay_cloud_server_renewal_with_balance, prepare_cloud_asset_renewal_with_link, prepare_retained_ip_renewal_with_link, rebind_cloud_server_user, record_cloud_ip_log, replace_cloud_asset_order_by_admin, run_cloud_server_renewal_postcheck, set_cloud_server_auto_renew_admin, set_group_cloud_server_auto_renew, sync_cloud_asset_user_binding
from cloud.sync_safety import get_missing_confirmation_threshold
from cloud.api import _apply_server_missing_state, _cloud_order_source_tags, _display_cloud_asset_note, _fetch_address_chain_balances, auto_renew_task_detail, cloud_assets_list, cloud_order_detail, cloud_orders_list, delete_cloud_asset, delete_cloud_order, delete_notice_history, delete_server, notice_task_detail, refresh_notice_plan_table, run_auto_renew_order, run_auto_renew_tasks, servers_list, sync_cloud_asset_status, sync_cloud_assets, tasks_overview, update_cloud_asset, update_cloud_order_status, update_notice_plan_text, update_notice_switches
from core.cloud_accounts import cloud_account_label, cloud_account_label_variants, list_cloud_accounts_by_server_load
from core.models import CloudAccountConfig, SiteConfig
from core.persistence import bump_daily_address_stat
from orders.payment_scanner import _confirm_cloud_server_order


class CloudServerServicesBaseTestCase(TestCase):
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

    def _create_auto_renew_asset(self, order, *, status=None, asset_name=None):
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
            actual_expires_at=order.service_expires_at,
            status=status or CloudAsset.STATUS_RUNNING,
        )


__all__ = [name for name in globals() if not name.startswith('__')]
