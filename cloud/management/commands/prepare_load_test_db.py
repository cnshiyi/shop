"""Prepare an isolated database for load and pagination tests."""

import math
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone


LOAD_TEST_DIR = '.shop-load-tests'
LOAD_TEST_ENV_FLAG = 'SHOP_LOAD_TEST_DB'


class Command(BaseCommand):
    help = '创建或校验独立压测 SQLite 数据库，避免复用业务库。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sqlite-name',
            default=f'{LOAD_TEST_DIR}/shop-loadtest.sqlite3',
            help='压测 SQLite 数据库路径，必须位于 .shop-load-tests/ 且文件名包含 loadtest。',
        )
        parser.add_argument(
            '--migrate',
            action='store_true',
            help='在隔离压测库上运行 migrate --noinput。',
        )
        parser.add_argument(
            '--seed-assets',
            type=int,
            default=0,
            help='向隔离压测库写入指定数量的 CloudAsset 测试资产。',
        )
        parser.add_argument(
            '--confirm-isolated',
            action='store_true',
            help='确认本次操作只针对独立压测库；实际迁移或造数必须提供。',
        )
        parser.add_argument(
            '--seed-only',
            action='store_true',
            help='内部参数：当前进程已切换到隔离压测库后执行造数。',
        )

    def handle(self, *args, **options):
        sqlite_path = _resolve_loadtest_sqlite_path(options['sqlite_name'])
        seed_assets = max(int(options['seed_assets'] or 0), 0)
        mutate = bool(options['migrate'] or seed_assets or options['seed_only'])

        if mutate and not options['confirm_isolated']:
            raise CommandError('实际迁移或造数必须传入 --confirm-isolated。')

        if options['seed_only']:
            return self._seed_current_loadtest_db(sqlite_path, seed_assets)

        env = _loadtest_env(sqlite_path)
        self.stdout.write('压测数据库隔离配置：')
        self.stdout.write(f'- DB_ENGINE=sqlite')
        self.stdout.write(f'- SQLITE_NAME={_relative_to_base(sqlite_path)}')
        self.stdout.write(f'- {LOAD_TEST_ENV_FLAG}=1')
        self.stdout.write(f'- migrate={bool(options["migrate"])}')
        self.stdout.write(f'- seed_assets={seed_assets}')
        self.stdout.write('- 清理策略：删除 .shop-load-tests/ 下本轮生成的 loadtest SQLite 文件。')

        if not mutate:
            self.stdout.write(self.style.SUCCESS('dry-run 通过：未创建数据库，未写入数据。'))
            return

        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        if options['migrate']:
            self._run_manage_py(['migrate', '--noinput'], env)
        if seed_assets:
            self._run_manage_py(
                [
                    'prepare_load_test_db',
                    '--sqlite-name',
                    _relative_to_base(sqlite_path),
                    '--seed-assets',
                    str(seed_assets),
                    '--seed-only',
                    '--confirm-isolated',
                ],
                env,
            )
        self.stdout.write(self.style.SUCCESS('隔离压测数据库准备完成。'))

    def _run_manage_py(self, args, env):
        command = [sys.executable, str(Path(settings.BASE_DIR) / 'manage.py'), *args]
        self.stdout.write(f'执行：{" ".join(command[1:])}')
        subprocess.run(command, cwd=settings.BASE_DIR, env=env, check=True)

    def _seed_current_loadtest_db(self, sqlite_path: Path, count: int):
        if count <= 0:
            self.stdout.write('seed_assets=0，跳过造数。')
            return
        _assert_current_connection_is_loadtest(sqlite_path)

        from cloud.api_asset_snapshots import backfill_cloud_asset_dashboard_snapshots
        from cloud.models import CloudAsset

        now = timezone.now()
        start_index = CloudAsset.objects.filter(
            kind=CloudAsset.KIND_SERVER,
            asset_name__startswith='loadtest-asset-',
        ).count()
        rows = []
        batch_size = 1000
        for offset in range(count):
            index = start_index + offset + 1
            days_delta = (index % 90) - 30
            rows.append(
                CloudAsset(
                    kind=CloudAsset.KIND_SERVER,
                    source=CloudAsset.SOURCE_AWS_SYNC,
                    provider='aws_lightsail',
                    account_label='loadtest-account',
                    region_code='loadtest-region',
                    region_name='Load Test Region',
                    asset_name=f'loadtest-asset-{index:08d}',
                    instance_id=f'loadtest-instance-{index:08d}',
                    provider_resource_id=f'loadtest-resource-{index:08d}',
                    public_ip=_loadtest_ip(index),
                    mtproxy_port=443,
                    actual_expires_at=now + timezone.timedelta(days=days_delta),
                    status=CloudAsset.STATUS_RUNNING if index % 7 else CloudAsset.STATUS_STOPPED,
                    is_active=True,
                    sort_order=index % 100,
                    note='loadtest isolated seed',
                    sync_state={'source': 'prepare_load_test_db'},
                )
            )
            if len(rows) >= batch_size:
                CloudAsset.objects.bulk_create(rows, batch_size=batch_size)
                rows = []
        if rows:
            CloudAsset.objects.bulk_create(rows, batch_size=batch_size)

        max_batches = max(math.ceil(count / batch_size), 1)
        snapshot_summary = backfill_cloud_asset_dashboard_snapshots(
            reason='loadtest-seed',
            batch_size=batch_size,
            max_batches=max_batches,
            include_stale=True,
        )
        self.stdout.write(
            self.style.SUCCESS(
                '造数完成：assets=%s snapshots=%s db=%s'
                % (count, snapshot_summary.get('assets'), _relative_to_base(sqlite_path))
            )
        )


def _resolve_loadtest_sqlite_path(raw_name: str) -> Path:
    if not raw_name:
        raise CommandError('--sqlite-name 不能为空。')
    if raw_name == ':memory:':
        raise CommandError('压测数据库不能使用 :memory:，必须使用可记录和可清理的独立文件。')

    path = Path(raw_name).expanduser()
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    path = path.resolve()

    loadtest_root = (Path(settings.BASE_DIR) / LOAD_TEST_DIR).resolve()
    try:
        path.relative_to(loadtest_root)
    except ValueError as exc:
        raise CommandError('压测 SQLite 文件必须位于 .shop-load-tests/ 目录下。') from exc
    if 'loadtest' not in path.name.lower():
        raise CommandError('压测 SQLite 文件名必须包含 loadtest 标记。')
    if path.name in {'db.sqlite3', 'db.sqlite3.bak'}:
        raise CommandError('压测数据库不能复用默认 SQLite 文件名。')

    current_name = str(connection.settings_dict.get('NAME') or '')
    if current_name and current_name != ':memory:' and path == Path(current_name).expanduser().resolve():
        if os.getenv(LOAD_TEST_ENV_FLAG) != '1':
            raise CommandError('当前默认数据库不是已标记的隔离压测库，拒绝复用。')
    return path


def _assert_current_connection_is_loadtest(sqlite_path: Path):
    if os.getenv(LOAD_TEST_ENV_FLAG) != '1':
        raise CommandError(f'缺少 {LOAD_TEST_ENV_FLAG}=1，拒绝造数。')
    engine = str(connection.settings_dict.get('ENGINE') or '')
    if not engine.endswith('sqlite3'):
        raise CommandError('造数只允许写入隔离 SQLite 压测库。')
    current_name = Path(str(connection.settings_dict.get('NAME') or '')).expanduser().resolve()
    if current_name != sqlite_path:
        raise CommandError('当前数据库连接与目标压测库不一致，拒绝造数。')


def _loadtest_env(sqlite_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            'DB_ENGINE': 'sqlite',
            'SQLITE_NAME': _relative_to_base(sqlite_path),
            LOAD_TEST_ENV_FLAG: '1',
        }
    )
    return env


def _relative_to_base(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path(settings.BASE_DIR).resolve()))
    except ValueError:
        return str(path)


def _loadtest_ip(index: int) -> str:
    normalized = max(int(index), 1)
    second = 64 + ((normalized // 65536) % 64)
    third = (normalized // 256) % 256
    fourth = normalized % 256
    if fourth == 0:
        fourth = 1
    return f'10.{second}.{third}.{fourth}'
