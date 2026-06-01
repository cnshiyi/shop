import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from cloud.models import CloudAssetSyncJobEvent

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '清理云资产同步任务事件明细，避免事件表无限增长'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=90, help='保留最近 N 天事件，默认 90 天')
        parser.add_argument('--keep-per-job', type=int, default=500, help='每个任务最多保留 N 条事件，0 表示不按任务截断')
        parser.add_argument('--dry-run', action='store_true', help='只统计将删除的数量，不实际删除')

    def handle(self, *args, **options):
        days = max(int(options.get('days') or 90), 1)
        keep_per_job = max(int(options.get('keep_per_job') or 0), 0)
        dry_run = bool(options.get('dry_run'))
        cutoff = timezone.now() - timedelta(days=days)

        old_queryset = CloudAssetSyncJobEvent.objects.filter(created_at__lt=cutoff)
        old_count = old_queryset.count()
        deleted_old = 0
        if old_count and not dry_run:
            deleted_old, _ = old_queryset.delete()

        trimmed_count = 0
        affected_jobs = 0
        if keep_per_job:
            job_rows = (
                CloudAssetSyncJobEvent.objects
                .values('job_id')
                .annotate(event_count=Count('id'))
                .filter(event_count__gt=keep_per_job)
                .order_by('job_id')
            )
            for row in job_rows.iterator():
                job_id = row['job_id']
                keep_ids = list(
                    CloudAssetSyncJobEvent.objects
                    .filter(job_id=job_id)
                    .order_by('-created_at', '-id')
                    .values_list('id', flat=True)[:keep_per_job]
                )
                prune_queryset = CloudAssetSyncJobEvent.objects.filter(job_id=job_id).exclude(id__in=keep_ids)
                count = prune_queryset.count()
                if not count:
                    continue
                affected_jobs += 1
                trimmed_count += count
                if not dry_run:
                    prune_queryset.delete()

        payload = {
            'days': days,
            'keep_per_job': keep_per_job,
            'dry_run': dry_run,
            'cutoff': cutoff.isoformat(),
            'old_events': old_count,
            'deleted_old_events': 0 if dry_run else deleted_old,
            'trimmed_events': trimmed_count,
            'affected_jobs': affected_jobs,
        }
        logger.info('CLOUD_SYNC_JOB_EVENTS_PRUNED payload=%s', payload)
        prefix = '[DRY-RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}云资产同步事件清理完成：old={old_count} trimmed={trimmed_count} affected_jobs={affected_jobs}'
        ))
