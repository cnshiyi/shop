"""Synchronous lifecycle execution entrypoints shared by dashboard and scheduled actions."""

from __future__ import annotations

import asyncio

from asgiref.sync import async_to_sync
from django.utils import timezone

from core.runtime_config import get_runtime_config
from cloud.lifecycle_tasks import claim_lifecycle_task_for_asset, claim_lifecycle_task_for_order, finish_lifecycle_task
from cloud.models import CloudAsset, CloudLifecycleTask, CloudServerOrder


def _asset_is_unattached_ip(asset: CloudAsset | None) -> bool:
    return bool(
        asset
        and (
            ('未附加' in str(getattr(asset, 'provider_status', '') or ''))
            or ('未附加IP' in str(getattr(asset, 'note', '') or ''))
            or ('未附加固定IP' in str(getattr(asset, 'note', '') or ''))
            or ('StaticIp' in str(getattr(asset, 'provider_resource_id', '') or ''))
        )
    )


def with_delete_source(note, source: str) -> str:
    text = str(note or '').strip()
    if '删除来源：' in text:
        return text
    return f'删除来源：{source}；{text}' if text else f'删除来源：{source}'


def _choice_label(value, choices) -> str:
    return dict(choices).get(value, value or '-')


def _cloud_action_timeout_seconds() -> int:
    try:
        return max(10, int(str(get_runtime_config('cloud_action_timeout_seconds', '90')).strip() or 90))
    except Exception:
        return 90


async def _run_cloud_action_with_timeout(coro, *, action: str, target: str):
    timeout_seconds = _cloud_action_timeout_seconds()
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return False, f'{action}超时：云 API 超过 {timeout_seconds} 秒未返回，已跳过本轮，避免卡住机器人；下次生命周期会重试。'


def _run_cloud_action(coro, *, action: str, target: str) -> tuple[bool, str]:
    result = async_to_sync(_run_cloud_action_with_timeout)(coro, action=action, target=target)
    if isinstance(result, tuple):
        ok = bool(result[0])
        note = str(result[1] if len(result) > 1 else result[0])
        return ok, note
    return True, str(result or '')


def run_shutdown_order_suspend(order_id: int, *, queue_status='scheduled_suspend', enforce_schedule: bool = True) -> dict:
    from cloud.lifecycle import (
        _is_cloud_suspend_time,
        _mark_suspended,
        _record_lifecycle_action_failed,
        _shutdown_enabled_for_order,
        _stop_instance,
    )
    from cloud.services import _order_primary_asset

    order = CloudServerOrder.objects.select_related('user', 'cloud_account').filter(id=order_id).first()
    if not order:
        return {'order_id': order_id, 'order_no': '', 'ip': '', 'queue_status': queue_status, 'ok': False, 'error': '订单不存在'}
    ip = order.public_ip or order.previous_public_ip or '未分配'
    now = timezone.now()
    if order.status not in {'completed', 'expiring', 'renew_pending'}:
        reason = f'当前状态为 {_choice_label(order.status, CloudServerOrder.STATUS_CHOICES)}，未进入服务器关机阶段'
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'suspend_skipped', reason)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    if enforce_schedule and not _shutdown_enabled_for_order(order, _order_primary_asset(order)):
        reason = '云账号关机计划已关闭，跳过真实关机。'
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'suspend_skipped', reason)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    if enforce_schedule:
        if not order.suspend_at:
            reason = '订单没有计划关机时间，跳过真实关机。'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'suspend_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
        if order.suspend_at > now:
            reason = f'未到计划关机时间：{timezone.localtime(order.suspend_at).strftime("%Y-%m-%d %H:%M:%S")}'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'suspend_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
        if not _is_cloud_suspend_time(now):
            reason = '当前不在后台配置的服务器关机执行时间窗口'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'suspend_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    task_claim = None
    if enforce_schedule:
        task_claim = claim_lifecycle_task_for_order(CloudLifecycleTask.TASK_SUSPEND, order, scheduled_at=order.suspend_at, queue_status=queue_status)
        if not task_claim:
            reason = '本轮关机计划已被其他进程认领、已完成或正在重试保护期内，跳过重复触发。'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'suspend_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    try:
        ok, note = _run_cloud_action(_stop_instance(order), action='AWS 实例关机', target=order.order_no)
        if ok:
            async_to_sync(_mark_suspended)(order.id, note)
            finish_lifecycle_task(task_claim, ok=True)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': True, 'error': None}
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'suspend_failed', note)
        finish_lifecycle_task(task_claim, ok=False, error=note)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': note}
    except Exception as exc:
        finish_lifecycle_task(task_claim, ok=False, error=str(exc))
        raise


def run_shutdown_order_delete(order_id: int, *, queue_status='manual_single', enforce_schedule: bool = True) -> dict:
    from cloud.lifecycle import (
        _delete_instance,
        _is_cloud_delete_safe_time,
        _mark_deleted,
        _record_lifecycle_action_failed,
        _shutdown_enabled_for_order,
        cloud_server_delete_enabled,
    )
    from cloud.services import _order_primary_asset

    order = CloudServerOrder.objects.select_related('user', 'cloud_account').filter(id=order_id).first()
    if not order:
        return {'order_id': order_id, 'order_no': '', 'ip': '', 'queue_status': queue_status, 'ok': False, 'error': '订单不存在'}
    ip = order.public_ip or order.previous_public_ip or '未分配'
    now = timezone.now()
    if order.status not in {'suspended', 'deleting', 'failed'}:
        reason = f'当前状态为 {_choice_label(order.status, CloudServerOrder.STATUS_CHOICES)}，未进入服务器删除阶段'
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    if order.provider == 'aliyun_simple':
        reason = '阿里云轻量服务器当前未接入删除 API，本系统只执行创建、续费和状态同步；不会执行真实删机。'
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    if not cloud_server_delete_enabled():
        reason = '删除服务器总开关已关闭，跳过真实删机。'
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    if enforce_schedule and not _shutdown_enabled_for_order(order, _order_primary_asset(order)):
        reason = '云账号关机计划已关闭，跳过真实删机。'
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    if enforce_schedule:
        if not order.delete_at:
            reason = '订单没有计划删机时间，跳过真实删机。'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
        if order.delete_at > now:
            reason = f'服务器删除时间未到：{timezone.localtime(order.delete_at).strftime("%Y-%m-%d %H:%M:%S")}'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
        if not _is_cloud_delete_safe_time(now):
            reason = '当前不在后台配置的服务器删除执行时间窗口'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    task_claim = None
    if enforce_schedule:
        task_claim = claim_lifecycle_task_for_order(CloudLifecycleTask.TASK_DELETE, order, scheduled_at=order.delete_at, queue_status=queue_status)
        if not task_claim:
            reason = '本轮删机计划已被其他进程认领、已完成或正在重试保护期内，跳过重复触发。'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    try:
        ok, note = _run_cloud_action(_delete_instance(order), action='AWS 实例删除', target=order.order_no)
        if ok:
            source = '人工手动删除' if not enforce_schedule or str(queue_status or '').startswith('manual') else '到期自动删除'
            async_to_sync(_mark_deleted)(order.id, with_delete_source(note, source))
            finish_lifecycle_task(task_claim, ok=True)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': True, 'error': None}
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_failed', note)
        finish_lifecycle_task(task_claim, ok=False, error=note)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': note}
    except Exception as exc:
        finish_lifecycle_task(task_claim, ok=False, error=str(exc))
        raise


def run_replaced_order_delete(order_id: int, *, queue_status='scheduled_migration_delete', enforce_schedule: bool = True) -> dict:
    from cloud.lifecycle import (
        _delete_replaced_server,
        _is_cloud_delete_safe_time,
        _mark_replaced_order_deleted,
        _record_lifecycle_action_failed,
        _shutdown_enabled_for_order,
        cloud_server_delete_enabled,
    )
    from cloud.services import _order_primary_asset

    order = CloudServerOrder.objects.select_related('user', 'cloud_account').filter(id=order_id).first()
    if not order:
        return {'order_id': order_id, 'order_no': '', 'ip': '', 'queue_status': queue_status, 'ok': False, 'error': '订单不存在'}
    ip = order.public_ip or order.previous_public_ip or '未分配'
    now = timezone.now()
    if not cloud_server_delete_enabled():
        reason = '删除服务器总开关已关闭，跳过真实删机。'
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    if enforce_schedule and not _shutdown_enabled_for_order(order, _order_primary_asset(order)):
        reason = '资产或云账号关机计划已关闭，跳过迁移旧服务器真实删机。'
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    if enforce_schedule:
        if not order.migration_due_at:
            reason = '迁移旧服务器没有计划清理时间，跳过真实删机。'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
        if order.migration_due_at > now:
            reason = f'迁移旧服务器清理时间未到：{timezone.localtime(order.migration_due_at).strftime("%Y-%m-%d %H:%M:%S")}'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
        if not _is_cloud_delete_safe_time(now):
            reason = '当前不在后台配置的服务器删除执行时间窗口'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    task_claim = None
    if enforce_schedule:
        task_claim = claim_lifecycle_task_for_order(CloudLifecycleTask.TASK_MIGRATION_DELETE, order, scheduled_at=order.migration_due_at, queue_status=queue_status)
        if not task_claim:
            reason = '本轮迁移旧机删除计划已被其他进程认领、已完成或正在重试保护期内，跳过重复触发。'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    try:
        ok, note = _run_cloud_action(_delete_replaced_server(order), action='AWS 迁移旧实例删除', target=order.order_no)
        if ok:
            async_to_sync(_mark_replaced_order_deleted)(order.id, note)
            finish_lifecycle_task(task_claim, ok=True)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': True, 'error': None}
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_failed', note)
        finish_lifecycle_task(task_claim, ok=False, error=note)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': note}
    except Exception as exc:
        finish_lifecycle_task(task_claim, ok=False, error=str(exc))
        raise


def run_orphan_asset_delete(asset_id: int, *, enforce_schedule: bool = True) -> dict:
    from cloud.lifecycle import (
        _delete_orphan_asset_instance,
        _is_cloud_delete_safe_time,
        _mark_orphan_asset_deleted,
        _orphan_asset_server_delete_at,
        _orphan_asset_server_delete_blocked_until,
        cloud_server_delete_enabled,
    )

    asset = CloudAsset.objects.select_related('cloud_account', 'order').filter(id=asset_id).first()
    if not asset:
        return {'asset_id': asset_id, 'ip': '', 'ok': False, 'error': '服务器资产不存在'}
    ip = asset.public_ip or asset.previous_public_ip or ''
    now = timezone.now()
    if asset.status in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_TERMINATING}:
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '该服务器资产已删除，不需要重复执行'}
    if asset.provider == 'aliyun_simple':
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '阿里云轻量服务器当前未接入删除 API，本系统不会执行真实删机。'}
    if not cloud_server_delete_enabled():
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '删除服务器总开关已关闭，跳过真实删机。'}
    if enforce_schedule and getattr(asset, 'shutdown_enabled', True) is False:
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '资产关机计划已关闭，跳过真实删机。'}
    if enforce_schedule and asset.cloud_account_id and not getattr(asset.cloud_account, 'shutdown_enabled', True):
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '云账号关机计划已关闭，跳过真实删机。'}
    if _asset_is_unattached_ip(asset) or not str(asset.instance_id or asset.provider_resource_id or asset.asset_name or '').strip():
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '该资产不是可删服务器，请走未附加 IP 删除'}
    if enforce_schedule:
        if not asset.actual_expires_at:
            return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '服务器没有计划删除时间，跳过真实删机。'}
        blocked_until = _orphan_asset_server_delete_blocked_until(asset, now=now)
        if blocked_until:
            return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': f'未到服务器删除时间：{timezone.localtime(blocked_until).strftime("%Y-%m-%d %H:%M:%S")}'}
        if not _is_cloud_delete_safe_time(now):
            return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '当前不在后台配置的服务器删除执行时间窗口'}
    task_claim = None
    if enforce_schedule:
        scheduled_at = _orphan_asset_server_delete_at(asset) or asset.actual_expires_at
        task_claim = claim_lifecycle_task_for_asset(CloudLifecycleTask.TASK_ORPHAN_ASSET_DELETE, asset, scheduled_at=scheduled_at, queue_status='scheduled_orphan_asset_delete')
        if not task_claim:
            return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '本轮无订单资产删除计划已被其他进程认领、已完成或正在重试保护期内，跳过重复触发。'}
    try:
        ok, note = _run_cloud_action(_delete_orphan_asset_instance(asset), action='AWS 无订单实例删除', target=str(asset.id))
        if ok:
            source = '人工手动删除' if not enforce_schedule else '到期自动删除'
            async_to_sync(_mark_orphan_asset_deleted)(asset.id, with_delete_source(note, source))
            finish_lifecycle_task(task_claim, ok=True)
            return {'asset_id': asset.id, 'ip': ip, 'ok': True, 'error': None}
        if asset.provider != 'aws_lightsail':
            source = '人工手动清理' if not enforce_schedule else '到期自动清理'
            async_to_sync(_mark_orphan_asset_deleted)(asset.id, with_delete_source(note, source))
            finish_lifecycle_task(task_claim, ok=True)
            return {'asset_id': asset.id, 'ip': ip, 'ok': True, 'error': None}
        finish_lifecycle_task(task_claim, ok=False, error=note)
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': note}
    except Exception as exc:
        finish_lifecycle_task(task_claim, ok=False, error=str(exc))
        raise


def run_order_static_ip_release(order_id: int, *, queue_status='scheduled_recycle', enforce_schedule: bool = True) -> dict:
    from cloud.lifecycle import (
        _is_cloud_unattached_ip_delete_time,
        _mark_recycled,
        _record_lifecycle_action_failed,
        _release_order_static_ip,
        _shutdown_enabled_for_order,
        cloud_ip_delete_enabled,
    )
    from cloud.services import _order_primary_asset

    order = CloudServerOrder.objects.select_related('user', 'cloud_account').filter(id=order_id).first()
    if not order:
        return {'order_id': order_id, 'order_no': '', 'ip': '', 'queue_status': queue_status, 'ok': False, 'error': '订单不存在'}
    ip = order.public_ip or order.previous_public_ip or '未分配'
    now = timezone.now()
    if order.status != 'deleted':
        reason = f'订单状态为 {order.status}，不执行释放固定 IP。'
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'recycle_skipped', reason)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    if not cloud_ip_delete_enabled():
        reason = '删除IP总开关已关闭，跳过真实释放固定 IP。'
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'recycle_skipped', reason)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    if enforce_schedule and not _shutdown_enabled_for_order(order, _order_primary_asset(order)):
        reason = '资产或云账号关机计划已关闭，跳过真实释放固定 IP。'
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'recycle_skipped', reason)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    if enforce_schedule:
        if not order.ip_recycle_at:
            reason = '订单没有计划释放 IP 时间，跳过真实释放固定 IP。'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'recycle_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
        if order.ip_recycle_at > now:
            reason = f'未到计划释放 IP 时间：{timezone.localtime(order.ip_recycle_at).strftime("%Y-%m-%d %H:%M:%S")}'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'recycle_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
        if not _is_cloud_unattached_ip_delete_time(now):
            reason = '当前不在后台配置的 IP 删除执行时间窗口'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'recycle_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    task_claim = None
    if enforce_schedule:
        task_claim = claim_lifecycle_task_for_order(CloudLifecycleTask.TASK_RECYCLE, order, scheduled_at=order.ip_recycle_at, queue_status=queue_status)
        if not task_claim:
            reason = '本轮固定 IP 回收计划已被其他进程认领、已完成或正在重试保护期内，跳过重复触发。'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'recycle_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    try:
        ok, note = _run_cloud_action(_release_order_static_ip(order), action='AWS 固定 IP 释放', target=order.order_no)
        if ok:
            source = '人工手动释放' if not enforce_schedule or str(queue_status or '').startswith('manual') else '到期自动释放'
            async_to_sync(_mark_recycled)(order.id, with_delete_source(note, source))
            finish_lifecycle_task(task_claim, ok=True)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': True, 'error': None}
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'recycle_failed', note)
        finish_lifecycle_task(task_claim, ok=False, error=note)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': note}
    except Exception as exc:
        finish_lifecycle_task(task_claim, ok=False, error=str(exc))
        raise


def run_unattached_ip_release(asset_id: int, *, enforce_schedule: bool = True) -> dict:
    from cloud.lifecycle import (
        _is_cloud_unattached_ip_delete_time,
        _mark_unattached_static_ip_deleted,
        _release_unattached_static_ip,
        cloud_ip_delete_enabled,
    )

    asset = CloudAsset.objects.select_related('cloud_account').filter(id=asset_id).first()
    if not asset:
        return {'asset_id': asset_id, 'ip': '', 'ok': False, 'error': 'IP 资产不存在'}
    ip = asset.public_ip or asset.previous_public_ip or ''
    now = timezone.now()
    if asset.status in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_TERMINATING}:
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '该 IP 已删除，不需要重复执行'}
    if not cloud_ip_delete_enabled():
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '删除IP总开关已关闭，跳过真实释放固定 IP。'}
    if enforce_schedule and getattr(asset, 'shutdown_enabled', True) is False:
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '资产关机计划已关闭，跳过真实释放固定 IP。'}
    if enforce_schedule and asset.cloud_account_id and not getattr(asset.cloud_account, 'shutdown_enabled', True):
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '云账号关机计划已关闭，跳过真实释放固定 IP。'}
    if asset.instance_id:
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '该 IP 仍有关联实例，不能按未附加 IP 删除'}
    if enforce_schedule:
        if not asset.actual_expires_at:
            return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': 'IP 没有计划删除时间，跳过真实释放固定 IP。'}
        if asset.actual_expires_at > now:
            return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': f'未到 IP 删除时间：{timezone.localtime(asset.actual_expires_at).strftime("%Y-%m-%d %H:%M:%S")}'}
        if not _is_cloud_unattached_ip_delete_time(now):
            return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '当前不在后台配置的 IP 删除执行时间窗口'}
    task_claim = None
    if enforce_schedule:
        task_claim = claim_lifecycle_task_for_asset(CloudLifecycleTask.TASK_UNATTACHED_IP_DELETE, asset, scheduled_at=asset.actual_expires_at, queue_status='scheduled_unattached_ip_delete')
        if not task_claim:
            return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '本轮未附加固定 IP 删除计划已被其他进程认领、已完成或正在重试保护期内，跳过重复触发。'}
    try:
        ok, note = _run_cloud_action(_release_unattached_static_ip(asset), action='AWS 未附加固定 IP 释放', target=str(asset.id))
        if ok:
            source = '人工手动删除' if not enforce_schedule else '到期自动删除'
            async_to_sync(_mark_unattached_static_ip_deleted)(asset.id, with_delete_source(note, source))
            finish_lifecycle_task(task_claim, ok=True)
            return {'asset_id': asset.id, 'ip': ip, 'ok': True, 'error': None}
        finish_lifecycle_task(task_claim, ok=False, error=note)
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': note}
    except Exception as exc:
        finish_lifecycle_task(task_claim, ok=False, error=str(exc))
        raise
