from django.core.management.base import BaseCommand

from cloud.api_tasks import _build_notice_plan_summary


def _clean_log_value(value):
    text = str(value or '').strip()
    if not text:
        return '-'
    return '；'.join(line.strip() for line in text.splitlines() if line.strip())


def _notice_plan_lines(item):
    notice_type = item.get('notice_type_label') or item.get('notice_type') or '通知计划'
    user = item.get('user_display_name') or '未绑定用户'
    username = item.get('username_label') or '-'
    status = item.get('notice_status_label') or item.get('queue_status_label') or item.get('result_label') or '-'
    scheduled_at = item.get('notice_at') or item.get('next_run_at') or item.get('created_at') or '-'
    result = item.get('retry_label') or item.get('text_preview') or item.get('notice_text_preview') or '-'
    order_items = item.get('order_items') if isinstance(item.get('order_items'), list) else []
    if not order_items:
        ips = item.get('ips') if isinstance(item.get('ips'), list) else []
        order_items = [{'ip': ip or item.get('ip') or '-'} for ip in ips] or [{'ip': item.get('ip') or '-'}]
    lines = []
    for detail in order_items:
        account = detail.get('account_label') or item.get('notice_channel_label') or '-'
        region = detail.get('region_name') or detail.get('region_code') or '-'
        name = detail.get('asset_name') or detail.get('server_name') or detail.get('instance_id') or detail.get('order_no') or '-'
        detail_status = detail.get('asset_status_label') or detail.get('order_status_label') or detail.get('provider_status') or ''
        line_status = f'{status}；资源状态={detail_status}' if detail_status else status
        lines.append(
            f'任务={notice_type}；账号={account}；地区={region}；用户={user}；用户名={username}；'
            f'实例/资源名={name}；IP={detail.get("ip") or "-"}；计划时间={scheduled_at}；'
            f'状态={line_status}；结果={_clean_log_value(result)}；订单ID={detail.get("order_id") or item.get("order_id") or "-"}'
        )
    return lines


def _history_line(item):
    account = item.get('account_label') or item.get('notice_channel_label') or '-'
    region = item.get('region_name') or item.get('region_code') or '-'
    name = item.get('server_name') or item.get('instance_id') or item.get('order_no') or '-'
    status = '已送达' if item.get('delivered') else '失败/未送达'
    order_status = item.get('order_status_label') or ''
    line_status = f'{status}；订单状态={order_status}' if order_status else status
    return (
        f'任务={item.get("notice_type_label") or item.get("event_label") or item.get("notice_event") or item.get("event_type") or "通知历史"}；账号={account}；地区={region}；'
        f'用户={item.get("user_display_name") or "未绑定用户"}；用户名={item.get("username_label") or "-"}；'
        f'实例/资源名={name}；IP={item.get("ip") or "-"}；'
        f'计划时间={item.get("created_at") or "-"}；状态={line_status}；结果={_clean_log_value(item.get("text_preview") or "-")}；'
        f'订单ID={item.get("order_id") or "-"}'
    )


def _write_notice_section(stdout, title, items):
    stdout.write(f'{title}（{len(items)}）:')
    for item in items:
        lines = _notice_plan_lines(item)
        for line in lines:
            stdout.write(f'  - {line}')


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
            fields={'basic', 'channels', 'ips', 'retry', 'text', 'full'},
            include_total_counts=True,
        )
        total_counts = bundle.get('total_counts') or {}
        active_items = bundle.get('active_user_summary_items') or []
        history_items = bundle.get('history_items') or []
        self.stdout.write(self.style.SUCCESS(
            "通知计划已生成："
            f"due={total_counts.get('due_count', 0)} "
            f"future={total_counts.get('future_count', 0)} "
            f"active_user={total_counts.get('active_user_count', 0)} "
            f"history={bundle.get('history_count', 0)}"
        ))
        _write_notice_section(self.stdout, '通知计划', active_items)
        self.stdout.write(f'通知历史（{len(history_items)}）:')
        for item in history_items:
            self.stdout.write(f'  - {_history_line(item)}')
