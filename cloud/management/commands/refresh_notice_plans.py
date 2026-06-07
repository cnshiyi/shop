from django.core.management.base import BaseCommand

from cloud.api_tasks import _build_notice_plan_summary


class Command(BaseCommand):
    help = '生成实时通知计划'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=1000)
        parser.add_argument('--history-limit', type=int, default=1000)

    def handle(self, *args, **options):
        limit = max(1, min(int(options.get('limit') or 1000), 1000))
        history_limit = max(1, min(int(options.get('history_limit') or 1000), 5000))
        bundle = _build_notice_plan_summary(
            limit=limit,
            offset=0,
            history_limit=history_limit,
            history_offset=0,
            fields={'basic'},
            include_total_counts=True,
        )
        total_counts = bundle.get('total_counts') or {}
        self.stdout.write(self.style.SUCCESS(
            "通知计划已生成："
            f"due={total_counts.get('due_count', 0)} "
            f"future={total_counts.get('future_count', 0)} "
            f"active_user={total_counts.get('active_user_count', 0)} "
            f"history={bundle.get('history_count', 0)}"
        ))
