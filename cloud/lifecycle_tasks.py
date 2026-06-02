"""生命周期和通知任务的数据库认领辅助逻辑。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timezone as dt_timezone

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from cloud.asset_expiry import order_asset_expiry
from cloud.models import CloudAsset, CloudLifecycleTask, CloudNoticeTask, CloudServerOrder


CLAIM_STALE_AFTER = timezone.timedelta(minutes=30)
FAILED_RETRY_AFTER = timezone.timedelta(minutes=30)


@dataclass(frozen=True)
class TaskClaim:
    id: int
    source_key: str
    claim_token: str


def _dt_key(value) -> str:
    if not value:
        return 'none'
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return timezone.localtime(value, dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _short_key(value: str) -> str:
    text = str(value or '')
    if len(text) <= 191:
        return text
    digest = hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]
    return f'{text[:174]}:{digest}'


def lifecycle_task_source_key(task_type: str, *, source_kind: str, source_id: int, scheduled_at) -> str:
    return _short_key(f'lifecycle:{task_type}:{source_kind}:{source_id}:{_dt_key(scheduled_at)}')


def notice_task_source_key(event_type: str, *, user_id: int | None, batch_id: str = '', order_id: int | None = None, target_chat_id: int | None = None) -> str:
    target = f'chat:{target_chat_id}' if target_chat_id else 'private'
    batch = batch_id or (f'order:{order_id}' if order_id else 'single')
    return _short_key(f'notice:{event_type}:user:{user_id or 0}:{target}:{batch}')


def _claim_task(model, *, source_key: str, token: str, now):
    stale_before = now - CLAIM_STALE_AFTER
    retry_before = now - FAILED_RETRY_AFTER
    updated = (
        model.objects
        .filter(source_key=source_key)
        .filter(
            Q(status=model.STATUS_PENDING)
            | Q(status=model.STATUS_FAILED, last_run_at__isnull=True)
            | Q(status=model.STATUS_FAILED, last_run_at__lt=retry_before)
            | Q(status=model.STATUS_CLAIMED, claimed_at__lt=stale_before)
        )
        .update(
            status=model.STATUS_CLAIMED,
            claim_token=token,
            claimed_at=now,
            attempt_count=F('attempt_count') + 1,
            last_error='',
            last_run_at=now,
            updated_at=now,
        )
    )
    if not updated:
        return None
    task = model.objects.only('id', 'source_key', 'claim_token').get(source_key=source_key)
    return TaskClaim(id=task.id, source_key=task.source_key, claim_token=task.claim_token)


def claim_lifecycle_task_for_order(task_type: str, order: CloudServerOrder, *, scheduled_at, queue_status: str = '') -> TaskClaim | None:
    now = timezone.now()
    source_key = lifecycle_task_source_key(
        task_type,
        source_kind=CloudLifecycleTask.SOURCE_ORDER,
        source_id=order.id,
        scheduled_at=scheduled_at,
    )
    asset = CloudAsset.objects.filter(order_id=order.id, kind=CloudAsset.KIND_SERVER).order_by('-sort_order', 'actual_expires_at', '-updated_at', '-id').first()
    defaults = {
        'task_type': task_type,
        'source_kind': CloudLifecycleTask.SOURCE_ORDER,
        'order': order,
        'asset': asset,
        'user_id': order.user_id,
        'scheduled_at': scheduled_at or now,
        'basis_actual_expires_at': order_asset_expiry(order),
        'payload': {'queue_status': queue_status or ''},
    }
    try:
        with transaction.atomic():
            task, created = CloudLifecycleTask.objects.get_or_create(source_key=source_key, defaults=defaults)
            if not created and task.status in {CloudLifecycleTask.STATUS_PENDING, CloudLifecycleTask.STATUS_FAILED}:
                for field, value in defaults.items():
                    setattr(task, field, value)
                task.save(update_fields=[*defaults.keys(), 'updated_at'])
    except IntegrityError:
        pass
    return _claim_task(CloudLifecycleTask, source_key=source_key, token=uuid.uuid4().hex, now=now)


def claim_lifecycle_task_for_asset(task_type: str, asset: CloudAsset, *, scheduled_at, queue_status: str = '') -> TaskClaim | None:
    now = timezone.now()
    source_key = lifecycle_task_source_key(
        task_type,
        source_kind=CloudLifecycleTask.SOURCE_ASSET,
        source_id=asset.id,
        scheduled_at=scheduled_at,
    )
    order = asset.order if getattr(asset, 'order_id', None) else None
    defaults = {
        'task_type': task_type,
        'source_kind': CloudLifecycleTask.SOURCE_ASSET,
        'order': order,
        'asset': asset,
        'user_id': asset.user_id or getattr(order, 'user_id', None),
        'scheduled_at': scheduled_at or now,
        'basis_actual_expires_at': asset.actual_expires_at,
        'payload': {'queue_status': queue_status or ''},
    }
    try:
        with transaction.atomic():
            task, created = CloudLifecycleTask.objects.get_or_create(source_key=source_key, defaults=defaults)
            if not created and task.status in {CloudLifecycleTask.STATUS_PENDING, CloudLifecycleTask.STATUS_FAILED}:
                for field, value in defaults.items():
                    setattr(task, field, value)
                task.save(update_fields=[*defaults.keys(), 'updated_at'])
    except IntegrityError:
        pass
    return _claim_task(CloudLifecycleTask, source_key=source_key, token=uuid.uuid4().hex, now=now)


def finish_lifecycle_task(claim: TaskClaim | None, *, ok: bool, error: str = ''):
    if not claim:
        return
    now = timezone.now()
    CloudLifecycleTask.objects.filter(id=claim.id, claim_token=claim.claim_token).update(
        status=CloudLifecycleTask.STATUS_DONE if ok else CloudLifecycleTask.STATUS_FAILED,
        last_error='' if ok else str(error or '')[:4000],
        last_run_at=now,
        completed_at=now if ok else None,
        updated_at=now,
    )


NOTICE_EVENT_TYPE_MAP = {
    'renew_notice': CloudNoticeTask.NOTICE_RENEW,
    'renew_notice_batch': CloudNoticeTask.NOTICE_RENEW,
    'auto_renew_notice': CloudNoticeTask.NOTICE_AUTO_RENEW,
    'delete_notice': CloudNoticeTask.NOTICE_DELETE,
    'recycle_notice': CloudNoticeTask.NOTICE_RECYCLE,
}


def claim_notice_task(event_type: str, *, user_id: int | None, order=None, batch_id: str = '', target_chat_id: int | None = None, payload: dict | None = None) -> TaskClaim | None:
    notice_type = NOTICE_EVENT_TYPE_MAP.get(event_type)
    if not notice_type:
        return None
    now = timezone.now()
    order_id = getattr(order, 'id', None)
    source_key = notice_task_source_key(
        event_type,
        user_id=user_id,
        batch_id=batch_id,
        order_id=order_id,
        target_chat_id=target_chat_id,
    )
    asset = CloudAsset.objects.filter(order_id=order_id, kind=CloudAsset.KIND_SERVER).order_by('-sort_order', 'actual_expires_at', '-updated_at', '-id').first() if order_id else None
    defaults = {
        'notice_type': notice_type,
        'order': order if order_id else None,
        'asset': asset,
        'user_id': user_id,
        'target_chat_id': target_chat_id,
        'notice_at': now,
        'basis_actual_expires_at': order_asset_expiry(order) if order_id else None,
        'batch_id': batch_id or '',
        'payload': payload or {},
    }
    try:
        with transaction.atomic():
            task, created = CloudNoticeTask.objects.get_or_create(source_key=source_key, defaults=defaults)
            if not created and task.status in {CloudNoticeTask.STATUS_PENDING, CloudNoticeTask.STATUS_FAILED}:
                for field, value in defaults.items():
                    setattr(task, field, value)
                task.save(update_fields=[*defaults.keys(), 'updated_at'])
    except IntegrityError:
        pass
    return _claim_task(CloudNoticeTask, source_key=source_key, token=uuid.uuid4().hex, now=now)


def finish_notice_task(claim: TaskClaim | None, *, delivered: bool, error: str = ''):
    if not claim:
        return
    now = timezone.now()
    CloudNoticeTask.objects.filter(id=claim.id, claim_token=claim.claim_token).update(
        status=CloudNoticeTask.STATUS_SENT if delivered else CloudNoticeTask.STATUS_FAILED,
        last_error='' if delivered else str(error or '')[:4000],
        last_run_at=now,
        sent_at=now if delivered else None,
        updated_at=now,
    )


def cancel_notice_task(claim: TaskClaim | None, *, reason: str = ''):
    if not claim:
        return
    CloudNoticeTask.objects.filter(id=claim.id, claim_token=claim.claim_token).update(
        status=CloudNoticeTask.STATUS_CANCELLED,
        last_error=str(reason or '')[:4000],
        updated_at=timezone.now(),
    )
