"""Seed and validate lifecycle plan tables in an isolated load-test DB."""

import json
import math
import os
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from cloud.management.commands.prepare_load_test_db import LOAD_TEST_DIR, LOAD_TEST_ENV_FLAG


class Command(BaseCommand):
    help = '在隔离压测库中造数并校验生命周期计划页各表分页。'

    def add_arguments(self, parser):
        parser.add_argument('--target', type=int, default=100000, help='每张计划表目标行数。')
        parser.add_argument('--page-size', type=int, default=1000, help='完整分页校验每页数量。')
        parser.add_argument('--frontend-page-size', type=int, default=20, help='API 抽查使用的前端页大小。')
        parser.add_argument('--seed', action='store_true', help='写入压测数据。')
        parser.add_argument('--validate', action='store_true', help='校验计数和分页。')
        parser.add_argument('--confirm-isolated', action='store_true', help='确认只操作隔离压测库。')
        parser.add_argument('--report-json', default='', help='报告 JSON 路径，必须位于 .shop-load-tests/ 且文件名包含 loadtest。')

    def handle(self, *args, **options):
        if not options['confirm_isolated']:
            raise CommandError('必须传入 --confirm-isolated。')
        _assert_loadtest_database()
        target = max(int(options['target'] or 0), 1)
        page_size = max(int(options['page_size'] or 1), 1)
        frontend_page_size = max(int(options['frontend_page_size'] or 1), 1)
        report_path = _resolve_report_path(options.get('report_json') or '') if options.get('report_json') else None

        if not options['seed'] and not options['validate']:
            raise CommandError('至少传入 --seed 或 --validate。')

        report = {
            'database': _relative_to_base(Path(connection.settings_dict['NAME'])),
            'target_per_table': target,
            'page_size': page_size,
            'frontend_page_size': frontend_page_size,
            'seed': {},
            'validation': {},
        }
        if options['seed']:
            report['seed'] = self._seed(target)
        if options['validate']:
            report['validation'] = self._validate(target, page_size, frontend_page_size)
        report['ok'] = True
        if report_path:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS('生命周期计划页压测完成：%s' % json.dumps(report, ensure_ascii=False)))

    def _seed(self, target: int) -> dict:
        from bot.models import TelegramUser
        from cloud.models import CloudAsset, CloudIpLog, CloudServerOrder, CloudServerPlan

        started = time.perf_counter()
        user, _created = TelegramUser.objects.get_or_create(
            tg_user_id=880000000002,
            defaults={'username': 'loadtest_lifecycle_plans', 'first_name': '计划页压测用户'},
        )
        plan, _created = CloudServerPlan.objects.get_or_create(
            provider=CloudServerPlan.PROVIDER_AWS_LIGHTSAIL,
            region_code='loadtest-region',
            config_id='loadtest-lifecycle-plans',
            defaults={
                'region_name': 'Load Test Region',
                'provider_plan_id': 'loadtest-lifecycle-plan',
                'plan_name': 'Load Test Lifecycle Plan',
                'display_plan_name': 'Load Test Lifecycle Plan',
                'price': '19.00',
                'currency': 'USDT',
                'is_active': True,
            },
        )
        now = timezone.now()
        batch_size = 1000
        seed_report = {}

        seed_report['shutdown_plan'] = _bulk_assets(
            target,
            prefix='loadtest-shutdown-plan',
            user=user,
            now=now,
            batch_size=batch_size,
            status=CloudAsset.STATUS_RUNNING,
            provider_status='运行中',
            ip_block=10,
            expires_offset_seconds=0,
        )
        seed_report['server_delete'] = _bulk_assets(
            target,
            prefix='loadtest-server-delete',
            user=user,
            now=now,
            batch_size=batch_size,
            status=CloudAsset.STATUS_STOPPED,
            provider_status='已关机',
            ip_block=20,
            expires_offset_seconds=target + 1000,
        )
        seed_report['ip_delete'] = _bulk_assets(
            target,
            prefix='loadtest-ip-delete',
            user=user,
            now=now,
            batch_size=batch_size,
            status=CloudAsset.STATUS_UNKNOWN,
            provider_status='未附加固定IP',
            ip_block=30,
            expires_offset_seconds=(target * 2) + 1000,
            static_ip=True,
        )

        order_target = target // 2
        asset_history_target = target - order_target
        seed_report['server_history_orders'] = _bulk_deleted_orders(
            order_target,
            user=user,
            plan=plan,
            now=now,
            batch_size=batch_size,
            ip_block=40,
        )
        seed_report['server_history_assets'] = _bulk_assets(
            asset_history_target,
            prefix='loadtest-server-history',
            user=user,
            now=now,
            batch_size=batch_size,
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            ip_block=50,
            expires_offset_seconds=(target * 3) + 1000,
            deleted=True,
        )
        seed_report['ip_delete_history'] = _bulk_ip_logs(
            target,
            user=user,
            now=now,
            batch_size=batch_size,
            ip_block=60,
        )
        seed_report['seconds'] = round(time.perf_counter() - started, 4)
        return seed_report

    def _validate(self, target: int, page_size: int, frontend_page_size: int) -> dict:
        from bot.api import lifecycle_plans
        from cloud.lifecycle_plan_queries import (
            ip_delete_history_page_sources,
            ip_delete_plan_counts,
            server_delete_history_counts,
            server_delete_history_page_sources,
            server_lifecycle_plan_counts,
            server_lifecycle_plan_page,
            unattached_ip_delete_active_unique_queryset,
            unattached_ip_delete_history_log_queryset,
            unattached_ip_delete_plan_page,
        )

        started = time.perf_counter()
        server_counts = server_lifecycle_plan_counts()
        ip_counts = ip_delete_plan_counts()
        history_counts = server_delete_history_counts()
        expected_counts = {
            'shutdown_plan': server_counts['shutdown_plan_count'],
            'server_delete': server_counts['server_delete_count'],
            'server_history': history_counts['server_history_count'],
            'ip_delete': ip_counts['ip_delete_count'],
            'ip_delete_history': ip_counts['ip_delete_history_count'],
        }
        for table, count in expected_counts.items():
            if count != target:
                raise CommandError(f'{table} 计数不等于目标：count={count} target={target}')

        validations = {
            'shutdown_plan': _validate_asset_pages(
                table='shutdown_plan',
                expected_ids=_asset_expected_ids('loadtest-shutdown-plan'),
                page_func=lambda page, size: server_lifecycle_plan_page(plan_stage='shutdown', page=page, page_size=size),
                page_size=page_size,
            ),
            'server_delete': _validate_asset_pages(
                table='server_delete',
                expected_ids=_asset_expected_ids('loadtest-server-delete'),
                page_func=lambda page, size: server_lifecycle_plan_page(plan_stage='delete', page=page, page_size=size),
                page_size=page_size,
            ),
            'ip_delete': _validate_asset_pages(
                table='ip_delete',
                expected_ids=_asset_expected_ids('loadtest-ip-delete'),
                page_func=lambda page, size: unattached_ip_delete_plan_page(page=page, page_size=size),
                page_size=page_size,
            ),
            'server_history': _validate_source_pages(
                table='server_history',
                expected_keys=_server_history_expected_keys(),
                page_func=lambda page, size: server_delete_history_page_sources(page=page, page_size=size),
                page_size=page_size,
            ),
            'ip_delete_history': _validate_source_pages(
                table='ip_delete_history',
                expected_keys=[('log', row_id) for row_id in unattached_ip_delete_history_log_queryset().filter(asset_name__startswith='loadtest-ip-history').order_by('-created_at', '-id').values_list('id', flat=True)],
                page_func=lambda page, size: ip_delete_history_page_sources(page=page, page_size=size),
                page_size=page_size,
            ),
        }

        api_checks = {}
        for table in ['shutdown_plan', 'server_delete', 'server_history', 'ip_delete', 'ip_delete_history']:
            total_pages = max(math.ceil(target / frontend_page_size), 1)
            pages = sorted({1, max(total_pages // 2, 1), total_pages})
            api_checks[table] = [_api_check(lifecycle_plans, table, page, frontend_page_size, target) for page in pages]

        return {
            'counts': expected_counts,
            'tables': validations,
            'api_checks': api_checks,
            'seconds': round(time.perf_counter() - started, 4),
        }


def _bulk_assets(count, *, prefix, user, now, batch_size, status, provider_status, ip_block, expires_offset_seconds, static_ip=False, deleted=False):
    from cloud.models import CloudAsset

    started = time.perf_counter()
    existing = CloudAsset.objects.filter(asset_name__startswith=prefix).count()
    rows = []
    for offset in range(count):
        index = existing + offset + 1
        due_at = now + timezone.timedelta(seconds=expires_offset_seconds + index)
        public_ip = None if deleted else _stress_ip(ip_block, index)
        previous_public_ip = _stress_ip(ip_block, index)
        rows.append(
            CloudAsset(
                kind=CloudAsset.KIND_SERVER,
                source=CloudAsset.SOURCE_AWS_SYNC,
                user=user,
                provider='aws_lightsail',
                account_label='loadtest-account',
                region_code='loadtest-region',
                region_name='Load Test Region',
                asset_name=f'{prefix}-{index:08d}',
                instance_id='' if static_ip else f'{prefix}-instance-{index:08d}',
                provider_resource_id=f'StaticIp-{prefix}-{index:08d}' if static_ip else f'{prefix}-resource-{index:08d}',
                public_ip=public_ip,
                previous_public_ip=previous_public_ip,
                actual_expires_at=due_at,
                status=status,
                provider_status=provider_status,
                is_active=not deleted,
                note=f'{prefix} loadtest row',
                sync_state={'source': 'stress_lifecycle_plans', 'table': prefix},
            )
        )
        if len(rows) >= batch_size:
            CloudAsset.objects.bulk_create(rows, batch_size=batch_size)
            rows = []
    if rows:
        CloudAsset.objects.bulk_create(rows, batch_size=batch_size)
    return {'created': count, 'seconds': round(time.perf_counter() - started, 4)}


def _bulk_deleted_orders(count, *, user, plan, now, batch_size, ip_block):
    from cloud.models import CloudServerOrder

    started = time.perf_counter()
    existing = CloudServerOrder.objects.filter(order_no__startswith='LOAD-SERVER-HISTORY-').count()
    rows = []
    for offset in range(count):
        index = existing + offset + 1
        row_time = now - timezone.timedelta(seconds=index * 2)
        rows.append(
            CloudServerOrder(
                order_no=f'LOAD-SERVER-HISTORY-{index:08d}',
                user=user,
                plan=plan,
                provider=plan.provider,
                region_code=plan.region_code,
                region_name=plan.region_name,
                plan_name=plan.plan_name,
                quantity=1,
                currency='USDT',
                total_amount='19.00',
                pay_amount='19.00',
                status='deleted',
                account_label='loadtest-account',
                public_ip=_stress_ip(ip_block, index),
                previous_public_ip=_stress_ip(ip_block, index),
                delete_at=row_time,
                provision_note='loadtest server history order deleted',
            )
        )
        if len(rows) >= batch_size:
            CloudServerOrder.objects.bulk_create(rows, batch_size=batch_size)
            rows = []
    if rows:
        CloudServerOrder.objects.bulk_create(rows, batch_size=batch_size)
    return {'created': count, 'seconds': round(time.perf_counter() - started, 4)}


def _bulk_ip_logs(count, *, user, now, batch_size, ip_block):
    from cloud.models import CloudIpLog

    started = time.perf_counter()
    existing = CloudIpLog.objects.filter(asset_name__startswith='loadtest-ip-history').count()
    rows = []
    for offset in range(count):
        index = existing + offset + 1
        rows.append(
            CloudIpLog(
                user=user,
                event_type=CloudIpLog.EVENT_RECYCLED,
                provider='aws_lightsail',
                region_code='loadtest-region',
                region_name='Load Test Region',
                asset_name=f'loadtest-ip-history-{index:08d}',
                instance_id='',
                provider_resource_id=f'StaticIp-loadtest-ip-history-{index:08d}',
                public_ip=None,
                previous_public_ip=_stress_ip(ip_block, index),
                note='未附加固定IP；固定 IP 已真实释放；loadtest-ip-history',
                created_at=now - timezone.timedelta(seconds=index),
            )
        )
        if len(rows) >= batch_size:
            CloudIpLog.objects.bulk_create(rows, batch_size=batch_size)
            rows = []
    if rows:
        CloudIpLog.objects.bulk_create(rows, batch_size=batch_size)
    return {'created': count, 'seconds': round(time.perf_counter() - started, 4)}


def _validate_asset_pages(*, table, expected_ids, page_func, page_size):
    return _validate_pages(
        table=table,
        expected_keys=[('asset', item_id) for item_id in expected_ids],
        actual_keys=lambda rows: [('asset', row.id) for row in rows],
        page_func=page_func,
        page_size=page_size,
    )


def _validate_source_pages(*, table, expected_keys, page_func, page_size):
    return _validate_pages(
        table=table,
        expected_keys=expected_keys,
        actual_keys=lambda rows: [(kind, item.id) for kind, item in rows],
        page_func=page_func,
        page_size=page_size,
    )


def _validate_pages(*, table, expected_keys, actual_keys, page_func, page_size):
    started = time.perf_counter()
    total = len(expected_keys)
    if len(set(expected_keys)) != total:
        raise CommandError(f'{table} 基准存在重复。')
    page_count = math.ceil(total / page_size)
    middle = max(page_count // 2, 1)
    sample_pages = sorted({
        1,
        2 if page_count >= 2 else 1,
        max(middle - 1, 1),
        middle,
        min(middle + 1, page_count),
        max(page_count - 1, 1),
        page_count,
    })
    max_seconds = 0
    checked_rows = 0
    for page in sample_pages:
        page_started = time.perf_counter()
        rows = page_func(page, page_size)
        keys = actual_keys(rows)
        start = (page - 1) * page_size
        expected = expected_keys[start:start + page_size]
        if keys != expected:
            raise CommandError(f'{table} 第 {page} 页不一致：expected={expected[:3]} actual={keys[:3]}')
        checked_rows += len(keys)
        max_seconds = max(max_seconds, time.perf_counter() - page_started)
    return {
        'total': total,
        'page_size': page_size,
        'page_count': page_count,
        'checked_rows': checked_rows,
        'sample_pages': sample_pages,
        'max_page_seconds': round(max_seconds, 4),
        'seconds': round(time.perf_counter() - started, 4),
    }


def _asset_expected_ids(prefix: str):
    from cloud.models import CloudAsset

    return list(
        CloudAsset.objects
        .filter(asset_name__startswith=prefix)
        .order_by('actual_expires_at', 'user_id', 'id')
        .values_list('id', flat=True)
    )


def _server_history_expected_keys():
    from cloud.lifecycle_plan_queries import server_delete_history_asset_queryset, server_delete_history_order_queryset

    rows = [
        ('order', row_id, updated_at)
        for row_id, updated_at in server_delete_history_order_queryset().filter(order_no__startswith='LOAD-SERVER-HISTORY-').values_list('id', 'updated_at')
    ]
    rows.extend(
        ('asset', row_id, updated_at)
        for row_id, updated_at in server_delete_history_asset_queryset().filter(asset_name__startswith='loadtest-server-history').values_list('id', 'updated_at')
    )
    rows.sort(key=lambda row: (row[2].timestamp() if row[2] else 0.0, int(row[1] or 0)), reverse=True)
    return [(kind, row_id) for kind, row_id, _updated_at in rows]


def _api_check(lifecycle_plans, table: str, page: int, page_size: int, expected_total: int):
    started = time.perf_counter()
    params = {
        'compact': '1',
        'fields': 'basic,execution',
        'tables': table,
        f'{_table_param_prefix(table)}_page': str(page),
        f'{_table_param_prefix(table)}_page_size': str(page_size),
    }
    response = lifecycle_plans(_loadtest_request(params))
    payload = json.loads(response.content)
    if response.status_code != 200 or payload.get('code') != 0:
        raise CommandError(f'{table} API 异常：status={response.status_code} payload={payload}')
    data = payload.get('data') or {}
    count_key = {
        'shutdown_plan': 'shutdown_plan_count',
        'server_delete': 'server_delete_count',
        'server_history': 'server_history_count',
        'ip_delete': 'ip_delete_count',
        'ip_delete_history': 'ip_delete_history_count',
    }[table]
    items_key = {
        'shutdown_plan': 'shutdown_plan_items',
        'server_delete': 'server_delete_items',
        'server_history': 'server_history_items',
        'ip_delete': 'ip_delete_plan_items',
        'ip_delete_history': 'ip_delete_history_items',
    }[table]
    if int(data.get(count_key) or 0) != expected_total:
        raise CommandError(f'{table} API 数量不一致：api={data.get(count_key)} expected={expected_total}')
    page_meta = (data.get('pagination') or {}).get(table) or {}
    if int(page_meta.get('total') or 0) != expected_total:
        raise CommandError(f'{table} API total 不一致：api={page_meta.get("total")} expected={expected_total}')
    return {
        'page': page,
        'page_size': page_size,
        'loaded': len(data.get(items_key) or []),
        'seconds': round(time.perf_counter() - started, 4),
    }


def _table_param_prefix(table: str) -> str:
    return {
        'shutdown_plan': 'shutdown',
        'server_delete': 'server_delete',
        'server_history': 'server_history',
        'ip_delete': 'ip_delete',
        'ip_delete_history': 'ip_delete_history',
    }[table]


def _loadtest_request(params: dict[str, str]):
    from django.contrib.auth import get_user_model
    from django.test import RequestFactory

    user_model = get_user_model()
    staff_user, _created = user_model.objects.get_or_create(
        username='loadtest_lifecycle_staff',
        defaults={'is_staff': True, 'is_superuser': True},
    )
    request = RequestFactory().get('/api/admin/tasks/plans/', params)
    request.user = staff_user
    return request


def _assert_loadtest_database():
    if os.getenv(LOAD_TEST_ENV_FLAG) != '1':
        raise CommandError(f'缺少 {LOAD_TEST_ENV_FLAG}=1，拒绝操作。')
    engine = str(connection.settings_dict.get('ENGINE') or '')
    if not engine.endswith('sqlite3'):
        raise CommandError('计划页压测只允许写入隔离 SQLite 压测库。')
    path = Path(str(connection.settings_dict.get('NAME') or '')).expanduser().resolve()
    loadtest_root = (Path(settings.BASE_DIR) / LOAD_TEST_DIR).resolve()
    try:
        path.relative_to(loadtest_root)
    except ValueError as exc:
        raise CommandError('当前数据库不在 .shop-load-tests/ 下，拒绝操作。') from exc
    if 'loadtest' not in path.name.lower():
        raise CommandError('当前数据库文件名必须包含 loadtest 标记。')


def _resolve_report_path(raw_name: str) -> Path:
    path = Path(raw_name).expanduser()
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    path = path.resolve()
    loadtest_root = (Path(settings.BASE_DIR) / LOAD_TEST_DIR).resolve()
    try:
        path.relative_to(loadtest_root)
    except ValueError as exc:
        raise CommandError('压测报告文件必须位于 .shop-load-tests/ 目录下。') from exc
    if 'loadtest' not in path.name.lower():
        raise CommandError('压测报告文件名必须包含 loadtest 标记。')
    return path


def _relative_to_base(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path(settings.BASE_DIR).resolve()))
    except ValueError:
        return str(path)


def _stress_ip(block: int, index: int) -> str:
    normalized = max(int(index), 1)
    zero_based = normalized - 1
    block_size = 255 * 255
    second = (int(block) + (zero_based // block_size)) % 256
    third = (zero_based // 255) % 255
    fourth = (zero_based % 255) + 1
    if fourth == 0:
        fourth = 1
    return f'10.{second}.{third}.{fourth}'
