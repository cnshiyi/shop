from django.core.management.base import BaseCommand

from bot.api import _sync_lifecycle_plan_table


class Command(BaseCommand):
    help = '生成实时删机计划'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=None, help='预热前 N 条计划页候选；默认使用 page-size')
        parser.add_argument('--page-size', type=int, default=1000, help='命令输出预览统计的每类计划加载数量')

    def handle(self, *args, **options):
        raw_limit = options.get('limit')
        page_size = max(1, min(int(options.get('page_size') or 1000), 1000))
        limit = page_size if raw_limit in (None, 0) else max(1, int(raw_limit))
        bundle = _sync_lifecycle_plan_table(limit=limit, page_size=page_size)
        self.stdout.write(self.style.SUCCESS(
            f"删机计划已生成：due={len(bundle.get('due_items') or [])} future={len(bundle.get('future_plan_items') or [])} history={len(bundle.get('history_items') or [])} ip_delete={len(bundle.get('ip_delete_items') or [])}"
        ))
