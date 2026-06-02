from django.core.management.base import BaseCommand

from cloud.api_asset_snapshots import refresh_cloud_asset_dashboard_snapshots


class Command(BaseCommand):
    help = '刷新代理列表 Dashboard 快照表'

    def add_arguments(self, parser):
        parser.add_argument('--asset-id', action='append', default=[], help='只刷新指定资产 ID，可重复传入')

    def handle(self, *args, **options):
        asset_ids = [int(value) for value in options.get('asset_id') or [] if str(value).strip().isdigit()]
        summary = refresh_cloud_asset_dashboard_snapshots(
            asset_ids=asset_ids or None,
            reason='management_command',
            full=not bool(asset_ids),
        )
        self.stdout.write(self.style.SUCCESS(
            f"代理列表快照刷新完成：资产 {summary['assets']} 条，新增 {summary['created']} 条，更新 {summary['updated']} 条，耗时 {summary['duration_seconds']}s。"
        ))
