import logging
import time
import uuid

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from cloud.models import CloudAssetSyncJob

logger = logging.getLogger(__name__)


def _claim_next_job(worker_id: str):
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
            current_task=f'worker:{worker_id} 已领取任务',
            updated_at=now,
        )
        if updated:
            return CloudAssetSyncJob.objects.get(pk=job_id)


def _recover_stale_running_jobs(stale_minutes: int):
    if stale_minutes <= 0:
        return 0
    cutoff = timezone.now() - timezone.timedelta(minutes=stale_minutes)
    return CloudAssetSyncJob.objects.filter(
        status=CloudAssetSyncJob.STATUS_RUNNING,
        started_at__lt=cutoff,
        finished_at__isnull=True,
    ).update(
        status=CloudAssetSyncJob.STATUS_QUEUED,
        current_task='worker 恢复卡住的运行中任务',
        updated_at=timezone.now(),
    )


class Command(BaseCommand):
    help = '处理云资产后台同步任务队列'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='只处理当前队列后退出')
        parser.add_argument('--poll-interval', type=float, default=2.0, help='无任务时轮询间隔秒数')
        parser.add_argument('--batch-size', type=int, default=1, help='每轮最多处理任务数')
        parser.add_argument('--stale-running-minutes', type=int, default=90, help='运行中超过该分钟数的任务重新入队；0 表示关闭')
        parser.add_argument('--worker-id', default='', help='自定义 worker 标识')

    def handle(self, *args, **options):
        from cloud.api import _execute_cloud_asset_sync_job

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
