from django.core.management.base import BaseCommand

from bot.api import _sync_lifecycle_plan_table


class Command(BaseCommand):
    help = '生成实时删机计划'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=1000)

    def handle(self, *args, **options):
        limit = max(1, min(int(options.get('limit') or 1000), 1000))
        bundle = _sync_lifecycle_plan_table(limit=limit)
        self.stdout.write(self.style.SUCCESS(
            f"删机计划已生成：due={len(bundle.get('due_items') or [])} future={len(bundle.get('future_plan_items') or [])} history={len(bundle.get('history_items') or [])} ip_delete={len(bundle.get('ip_delete_items') or [])}"
        ))
