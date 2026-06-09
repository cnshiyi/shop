from django.core.management.base import BaseCommand

from cloud.asset_dedupe import merge_duplicate_cloud_assets_by_ip


class Command(BaseCommand):
    help = '按公网 IP 硬去重云资产，保留一条并迁移关联记录'

    def handle(self, *args, **options):
        result = merge_duplicate_cloud_assets_by_ip()
        if not result.deleted_assets:
            self.stdout.write(self.style.SUCCESS('云资产去重：未发现重复记录。'))
            return

        self.stdout.write(self.style.SUCCESS(
            f'云资产去重完成：处理重复 IP {result.merged_groups} 组；删除重复资产 {result.deleted_assets} 条；'
            f'迁移日志 {result.relinked_ip_logs} 条；迁移生命周期任务 {result.relinked_lifecycle_tasks} 条；'
            f'迁移通知任务 {result.relinked_notice_tasks} 条；删除旧快照 {result.deleted_snapshots} 条。'
        ))
        for line in (result.details or [])[:50]:
            self.stdout.write(line)
