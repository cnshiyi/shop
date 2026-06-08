import re

from django.core.management.base import BaseCommand

from bot.api import _sync_lifecycle_plan_table


def _clean_log_value(value):
    text = str(value or '').strip()
    if not text:
        return '-'
    return '；'.join(line.strip() for line in text.splitlines() if line.strip())


def _extract_log_field(text, key):
    match = re.search(rf'{re.escape(key)}[=：]([^；\n]+)', str(text or ''))
    return match.group(1).strip() if match else ''


def _plan_item_line(*, task, item):
    raw_note = item.get('source_note') or item.get('note') or item.get('display_note') or ''
    account = item.get('account_label') or item.get('cloud_account_name') or item.get('cloud_account_label') or _extract_log_field(raw_note, '账号') or '-'
    region = item.get('region_label') or item.get('region_name') or item.get('region_code') or _extract_log_field(raw_note, '地区') or '-'
    name = item.get('asset_name') or item.get('server_name') or item.get('instance_id') or item.get('order_no') or '-'
    public_ip = item.get('public_ip') or item.get('ip') or '-'
    status = (
        item.get('plan_state_label')
        or item.get('queue_status_label')
        or item.get('execution_status')
        or item.get('execution_status_label')
        or item.get('result_label')
        or item.get('status_label')
        or item.get('provider_status')
        or ('历史记录' if item.get('is_history') else '')
        or '-'
    )
    scheduled_at = (
        item.get('suspend_at')
        or item.get('delete_at')
        or item.get('next_run_at')
        or item.get('actual_expires_at')
        or item.get('logged_at')
        or '-'
    )
    result = (
        item.get('blocked_reason')
        or item.get('state_summary')
        or item.get('source_note')
        or item.get('note')
        or item.get('display_note')
        or '-'
    )
    return (
        f'任务={task}；账号={account}；地区={region}；实例/资源名={name}；IP={public_ip}；'
        f'计划时间={scheduled_at}；状态={status}；结果={_clean_log_value(result)}；'
        f'资产ID={item.get("asset_id") or item.get("id") or "-"}；订单={item.get("order_no") or "-"}'
    )


def _write_plan_section(stdout, title, items):
    stdout.write(f'{title}（{len(items)}）:')
    for item in items:
        stdout.write(f'  - {_plan_item_line(task=title, item=item)}')


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
        shutdown_items = bundle.get('shutdown_plan_items') or []
        server_delete_items = bundle.get('server_delete_items') or []
        server_history_items = bundle.get('server_history_items') or []
        ip_delete_items = bundle.get('ip_delete_plan_items') or []
        ip_delete_history_items = bundle.get('ip_delete_history_items') or []
        self.stdout.write(self.style.SUCCESS(
            f"生命周期计划已生成：关机计划={len(shutdown_items)} "
            f"删机计划={len(server_delete_items)} "
            f"服务器删除历史={len(server_history_items)} "
            f"IP删除计划={len(ip_delete_items)} "
            f"IP删除历史={len(ip_delete_history_items)}"
        ))
        _write_plan_section(self.stdout, '关机计划', shutdown_items)
        _write_plan_section(self.stdout, '删机计划', server_delete_items)
        _write_plan_section(self.stdout, '服务器删除历史', server_history_items)
        _write_plan_section(self.stdout, 'IP删除计划', ip_delete_items)
        _write_plan_section(self.stdout, 'IP删除历史', ip_delete_history_items)
