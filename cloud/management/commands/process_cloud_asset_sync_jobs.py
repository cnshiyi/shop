import logging
import time
import uuid

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from cloud.models import CloudAssetSyncJob, CloudAssetSyncJobEvent

logger = logging.getLogger(__name__)


def _claim_next_job(worker_id: str):
    from cloud.api import _record_sync_job_event

    while True:
        job_id = (
            CloudAssetSyncJob.objects
            .filter(status=CloudAssetSyncJob.STATUS_QUEUED)
            .order_by('created_at', 'id')
            .values_list('id', flat=True)
            .first()
        )
        if not job_id:
            return None
        now = timezone.now()
        updated = CloudAssetSyncJob.objects.filter(
            pk=job_id,
            status=CloudAssetSyncJob.STATUS_QUEUED,
        ).update(
            status=CloudAssetSyncJob.STATUS_RUNNING,
            started_at=now,
            worker_id=worker_id,
            worker_heartbeat_at=now,
            current_task=f'worker:{worker_id} 已领取任务',
            updated_at=now,
        )
        if updated:
            job = CloudAssetSyncJob.objects.get(pk=job_id)
            _record_sync_job_event(
                job,
                CloudAssetSyncJobEvent.TYPE_CLAIMED,
                f'worker:{worker_id} 已领取任务',
                payload={'worker_id': worker_id},
                status_from=CloudAssetSyncJob.STATUS_QUEUED,
                status_to=CloudAssetSyncJob.STATUS_RUNNING,
                worker_id=worker_id,
            )
            return job


def _recover_stale_running_jobs(stale_minutes: int):
    from cloud.api import _record_sync_job_event

    if stale_minutes <= 0:
        return 0
    cutoff = timezone.now() - timezone.timedelta(minutes=stale_minutes)
    stale_jobs = list(
        CloudAssetSyncJob.objects.filter(
            status=CloudAssetSyncJob.STATUS_RUNNING,
            finished_at__isnull=True,
        )
        .filter(Q(worker_heartbeat_at__lt=cutoff) | Q(worker_heartbeat_at__isnull=True, started_at__lt=cutoff))
        .order_by('started_at', 'id')[:100]
    )
    stale_ids = [job.id for job in stale_jobs]
    if not stale_ids:
        return 0
    recovered = CloudAssetSyncJob.objects.filter(pk__in=stale_ids).update(
        status=CloudAssetSyncJob.STATUS_QUEUED,
        worker_id='',
        worker_heartbeat_at=None,
        current_task='worker 恢复卡住的运行中任务',
        updated_at=timezone.now(),
    )
    for job in stale_jobs:
        _record_sync_job_event(
            job,
            CloudAssetSyncJobEvent.TYPE_WARNING,
            'worker 恢复卡住的运行中任务',
            payload={
                'previous_worker_id': job.worker_id,
                'previous_heartbeat_at': job.worker_heartbeat_at.isoformat() if job.worker_heartbeat_at else None,
                'stale_minutes': stale_minutes,
            },
            status_from=CloudAssetSyncJob.STATUS_RUNNING,
            status_to=CloudAssetSyncJob.STATUS_QUEUED,
            worker_id=job.worker_id,
            log_level=logging.WARNING,
        )
    return recovered


class Command(BaseCommand):
    help = '处理云资产后台同步任务队列'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='只处理当前队列后退出')
        parser.add_argument('--poll-interval', type=float, default=2.0, help='无任务时轮询间隔秒数')
        parser.add_argument('--batch-size', type=int, default=1, help='每轮最多处理任务数')
        parser.add_argument('--stale-running-minutes', type=int, default=90, help='运行中超过该分钟数的任务重新入队；0 表示关闭')
        parser.add_argument('--worker-id', default='', help='自定义 worker 标识')

    def handle(self, *args, **options):
        from cloud.api import _execute_cloud_asset_sync_job, _heartbeat_sync_job, _record_sync_job_event

        once = bool(options.get('once'))
        poll_interval = max(float(options.get('poll_interval') or 2.0), 0.1)
        batch_size = max(int(options.get('batch_size') or 1), 1)
        stale_minutes = max(int(options.get('stale_running_minutes') or 0), 0)
        worker_id = str(options.get('worker_id') or uuid.uuid4().hex[:8])
        self.stdout.write(f'云资产同步 worker 已启动：worker_id={worker_id} once={once} poll_interval={poll_interval}s')

        while True:
            close_old_connections()
            recovered = _recover_stale_running_jobs(stale_minutes)
            if recovered:
                self.stdout.write(self.style.WARNING(f'已恢复 {recovered} 个卡住的同步任务'))
            processed = 0
            for _ in range(batch_size):
                job = _claim_next_job(worker_id)
                if not job:
                    break
                self.stdout.write(f'开始处理云资产同步任务：job_id={job.id} run_id={job.run_id}')
                try:
                    _heartbeat_sync_job(job, worker_id=worker_id, current_task='worker 准备执行任务', record_event=True)
                    _execute_cloud_asset_sync_job(job)
                except Exception as exc:
                    logger.exception('CLOUD_SYNC_WORKER_JOB_FAILED job_id=%s run_id=%s worker_id=%s', job.id, job.run_id, worker_id)
                    now = timezone.now()
                    CloudAssetSyncJob.objects.filter(pk=job.pk).update(
                        status=CloudAssetSyncJob.STATUS_FAILED,
                        current_task='worker 执行异常',
                        errors=[str(exc)],
                        finished_at=now,
                        updated_at=now,
                    )
                    _record_sync_job_event(
                        job,
                        CloudAssetSyncJobEvent.TYPE_ERROR,
                        'worker 执行异常',
                        payload={'error': str(exc), 'worker_id': worker_id},
                        status_from=CloudAssetSyncJob.STATUS_RUNNING,
                        status_to=CloudAssetSyncJob.STATUS_FAILED,
                        worker_id=worker_id,
                        log_level=logging.ERROR,
                    )
                job.refresh_from_db()
                self.stdout.write(self.style.SUCCESS(
                    f'云资产同步任务结束：job_id={job.id} status={job.status} progress={job.progress_current}/{job.progress_total}'
                ))
                processed += 1
                close_old_connections()

            if once:
                return
            if processed == 0:
                time.sleep(poll_interval)
