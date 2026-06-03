# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-03 15:04 CST
- 状态：已完成本地数据库差异复查和真机测试计划复查。
- 最近提交：本轮提交后以 `git log -1` 为准。
- 本轮改动：`core/tests.py` 将 `SiteConfigCacheTestCase` 从 `TestCase` 调整为 `TransactionTestCase`，避免内存 SQLite 下同步/异步连接被外层事务锁误导；`docs/real-machine-test-report.md` 新增未授权计划复查记录；`TODO.md` 勾选后台任务中心、本地数据库差异和真机测试计划复查。
- 本轮结论：SQLite 聚焦测试路径可完整创建迁移图，`db_comment` / `db_table_comment` warning 属于后端能力差异；默认 MySQL/MariaDB 的 `migrate --plan` 在当前沙箱因无法连接 `127.0.0.1:3306` 失败，不能据此判断生产迁移图异常。未获得真实云资源成本授权，因此未执行任何真机云操作。

## 最近验证

- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile core/tests.py shop/settings.py core/models.py core/cache.py` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test core.tests --settings=shop.settings --verbosity=2` 通过，共 14 个测试。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run` 输出 `No changes detected`。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run` 输出 `No changes detected`，但仍有本地 MySQL 沙箱连接 warning。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput` 可生成完整迁移计划，但会打印 SQLite 不支持表/列注释的预期 warning。
- 默认 `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput` 在当前沙箱失败，原因为 MySQL 后端字段检查读取服务器特性时无法连接 `127.0.0.1:3306`。
- SQLite 字段内省确认 `CloudAsset` 到期字段仍只有 `actual_expires_at`，`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`，`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 真机计划复查只更新报告，未执行任何真实云资源创建、删除、IP 变更、续费、支付或链上广播。

## 剩余风险

- 工作树仍存在其它未提交路由、文档和测试路径改动，本轮未回退。
- 本轮未在真实 MySQL/MariaDB 上执行 `migrate --plan`，因为当前沙箱禁止连接本地 `127.0.0.1:3306`。
- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源、真实支付、链上广播、生产发布或不可逆操作。

## 下一步

- `TODO.md` 当前固定任务已全部勾选；下一轮如无新增明确任务，按 `docs/auto-optimization-control.md` 固定巡检清单做只读巡检，重点继续看机器人返回链、云资产生命周期唯一到期事实、后台任务中心可观测性和红线回流。
