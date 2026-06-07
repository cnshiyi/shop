from django.core.management.base import BaseCommand

from cloud.api_asset_snapshots import backfill_cloud_asset_dashboard_snapshots, refresh_cloud_asset_dashboard_snapshots


class Command(BaseCommand):
    help = '刷新代理列表 Dashboard 快照表'

    def add_arguments(self, parser):
        parser.add_argument('--asset-id', action='append', default=[], help='只刷新指定资产 ID，可重复传入')
        parser.add_argument('--batch-size', type=int, default=5000, help='未指定资产 ID 时，每批补齐数量，服务端会限制到安全上限')
        parser.add_argument('--max-batches', type=int, default=500, help='未指定资产 ID 时，最多补齐批次数')
        parser.add_argument('--include-stale', action='store_true', help='缺失快照补齐后继续刷新旧快照；百万数据下需单独评估耗时')

    def handle(self, *args, **options):
        asset_ids = [int(value) for value in options.get('asset_id') or [] if str(value).strip().isdigit()]
        if asset_ids:
            summary = refresh_cloud_asset_dashboard_snapshots(
                asset_ids=asset_ids,
                reason='management_command',
                full=False,
            )
            self.stdout.write(self.style.SUCCESS(
                f"代理列表快照刷新完成：资产 {summary['assets']} 条，新增 {summary['created']} 条，更新 {summary['updated']} 条，耗时 {summary['duration_seconds']}s。"
            ))
            return
        summary = backfill_cloud_asset_dashboard_snapshots(
            reason='management_command',
            batch_size=options['batch_size'],
            max_batches=options['max_batches'],
            include_stale=options['include_stale'],
        )
        self.stdout.write(self.style.SUCCESS(
            f"代理列表快照分批补齐完成：批次 {summary['batches']}，资产 {summary['assets']} 条，新增 {summary['created']} 条，更新 {summary['updated']} 条，耗时 {summary['duration_seconds']}s。"
        ))
