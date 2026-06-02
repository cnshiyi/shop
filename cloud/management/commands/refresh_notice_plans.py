from django.core.management.base import BaseCommand

from cloud.api import _build_notice_plan_bundle


class Command(BaseCommand):
    help = '生成实时通知计划'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=1000)
        parser.add_argument('--future-limit', type=int, default=200)
        parser.add_argument('--history-limit', type=int, default=1000)

    def handle(self, *args, **options):
        limit = max(1, min(int(options.get('limit') or 1000), 1000))
        future_limit = max(1, min(int(options.get('future_limit') or 200), 2000))
        history_limit = max(1, min(int(options.get('history_limit') or 1000), 5000))
        bundle = _build_notice_plan_bundle(limit=limit, future_limit=future_limit, history_limit=history_limit)
        active_items = bundle.get('active_items') or []
        due_items = [item for item in active_items if item.get('queue_status') in {'due_now', 'fallback_notice', 'within_window'}]
        future_items = [item for item in active_items if item.get('queue_status') == 'scheduled_future']
        history_items = bundle.get('history_items') or []
        self.stdout.write(self.style.SUCCESS(
            f"通知计划已生成：due={len(due_items)} future={len(future_items)} history={len(history_items)}"
        ))
