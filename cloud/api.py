"""cloud 域后台 API 兼容聚合层。"""

import logging

from asgiref.sync import async_to_sync

from cloud.api_assets import (
    CloudAssetPayloadContext,
    _asset_payload,
    _build_cloud_asset_payload_context,
    _cloud_asset_payloads,
    _display_cloud_asset_note,
    _ensure_unattached_ip_expiry,
    _infer_asset_order,
    _parse_iso_datetime,
    _resolve_telegram_user,
    _sync_telegram_username,
    cloud_assets_list,
    cloud_assets_risk_summary,
)
from cloud.api_asset_snapshots import _ensure_cloud_asset_dashboard_snapshots, refresh_cloud_asset_dashboard_snapshots
from cloud.api_asset_edit import (
    delete_cloud_asset,
    toggle_cloud_asset_auto_renew,
    update_cloud_asset,
)
from cloud.api_monitors import (
    _fetch_address_chain_balances,
    build_trongrid_headers,
    cloud_ip_logs_list,
    get_redis,
    httpx,
    monitors_list,
)
from cloud.api_orders import (
    _apply_cloud_order_status,
    _cloud_order_detail_payload,
    _cloud_order_source_tags,
    cloud_order_detail,
    cloud_orders_list,
    delete_cloud_order,
    update_cloud_order_status,
)
from cloud.api_plans import (
    _cloud_plan_payload,
    _resolve_cloud_plan_config_id,
    _server_price_payload,
    cloud_plans_list,
    cloud_pricing_list,
    create_cloud_plan,
    delete_cloud_plan,
    update_cloud_plan,
)
from cloud.api_servers import (
    delete_server,
    rebuild_server_preserve_link,
    servers_list,
    servers_statistics,
)
from cloud.api_sync import (
    _apply_server_missing_state,
    sync_cloud_asset_status,
    sync_cloud_plans,
    sync_servers,
)
from cloud.api_tasks import (
    _build_auto_renew_plan_items,
    _build_notice_plan_bundle,
    _get_due_orders,
    _run_auto_renew,
    auto_renew_task_detail,
    delete_notice_history,
    notice_task_detail,
    refresh_notice_plan_view,
    run_auto_renew_order,
    run_auto_renew_tasks,
    tasks_overview,
    update_notice_plan_text,
    update_notice_switches,
)
from cloud.dashboard_snapshots import _refresh_dashboard_plan_snapshots, _refresh_dashboard_plan_snapshots_deferred
from cloud.lifecycle import _delete_instance, _mark_replaced_order_deleted
from cloud.provisioning import provision_cloud_server
from cloud.sync_jobs import (
    _active_sync_accounts,
    _asset_retained_static_ip_sync_scope,
    _call_command_capture,
    _cloud_asset_sync_job_payload,
    _execute_cloud_asset_sync_job,
    _heartbeat_sync_job,
    _log_sync_command_output,
    _record_dashboard_sync_log,
    _record_sync_job_event,
    _resolve_sync_account_for_asset,
    _sync_account_payload,
    _sync_log_tail,
    _sync_log_text,
    _sync_provider_for_asset,
    cancel_cloud_asset_sync_job,
    cloud_asset_sync_job_detail,
    cloud_asset_sync_jobs_list,
    cloud_asset_sync_jobs_metrics,
    cloud_assets_sync_status,
    retry_cloud_asset_sync_job,
    sync_cloud_assets,
)
from cloud.task_center import task_center_overview

logger = logging.getLogger(__name__)


def _run_rebuild_job(new_order_id: int):
    """Compatibility wrapper for older tests/imports that patched cloud.api."""
    saved = async_to_sync(provision_cloud_server)(new_order_id)
    if saved and getattr(saved, 'status', '') == 'completed' and getattr(saved, 'replacement_for_id', None):
        logger.info(
            'AWS 重装迁移后台任务完成，旧实例进入迁移保留期: new_order_id=%s replacement_for_id=%s',
            saved.id,
            saved.replacement_for_id,
        )
        return saved
    from cloud.services import run_cloud_server_rebuild_job
    return run_cloud_server_rebuild_job(new_order_id)


__all__ = [
    'auto_renew_task_detail',
    'cancel_cloud_asset_sync_job',
    'cloud_asset_sync_job_detail',
    'cloud_asset_sync_jobs_list',
    'cloud_asset_sync_jobs_metrics',
    'cloud_assets_list',
    'cloud_assets_risk_summary',
    'cloud_assets_sync_status',
    'cloud_ip_logs_list',
    'cloud_order_detail',
    'cloud_orders_list',
    'cloud_plans_list',
    'cloud_pricing_list',
    'create_cloud_plan',
    'delete_cloud_asset',
    'delete_cloud_order',
    'delete_cloud_plan',
    'delete_notice_history',
    'delete_server',
    'monitors_list',
    'notice_task_detail',
    'rebuild_server_preserve_link',
    'refresh_cloud_asset_dashboard_snapshots',
    'refresh_notice_plan_view',
    'retry_cloud_asset_sync_job',
    'run_auto_renew_order',
    'run_auto_renew_tasks',
    'servers_list',
    'servers_statistics',
    'sync_cloud_asset_status',
    'sync_cloud_assets',
    'sync_cloud_plans',
    'sync_servers',
    'task_center_overview',
    'tasks_overview',
    'toggle_cloud_asset_auto_renew',
    'update_cloud_asset',
    'update_cloud_order_status',
    'update_cloud_plan',
    'update_notice_plan_text',
    'update_notice_switches',
]
