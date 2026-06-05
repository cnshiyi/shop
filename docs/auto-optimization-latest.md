# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-06 06:39 CST
- 状态：本轮按 `TODO.md` 无未完成明确任务后的固定巡检执行，并优先复查上一轮 MySQL `migrate --plan` 超时风险；已修复 MySQL 半开连接导致 Django 管理命令无期限等待的问题。
- 本轮范围：MySQL/PyMySQL 连接超时配置、默认环境示例、设置单测、废弃 runtime app 和到期字段回流巡检、机器人返回链与 `callback_data`、任务中心状态统计、支付详情缓存隔离验证。

## 修改内容

- `shop/settings.py` 新增 `_mysql_timeout_options()`，默认向 PyMySQL 传入 `connect_timeout`、`read_timeout`、`write_timeout` 各 10 秒；可通过 `MYSQL_CONNECT_TIMEOUT`、`MYSQL_READ_TIMEOUT`、`MYSQL_WRITE_TIMEOUT` 调整，设置为 `0` 或负数可关闭对应项。
- `.env.example` 增加三个 MySQL timeout 示例变量，避免本地和部署环境继续使用无限等待的连接行为。
- `core/tests.py` 新增 MySQL timeout 配置测试，覆盖默认值、自定义值、关闭值和非法值回退默认值。

## 巡检结论

- `migrate --plan` 的 `faulthandler` 追踪确认上一轮超时卡点在 PyMySQL 连接握手读取 server information，尚未进入迁移计划或锁等待阶段。
- 修复后 `DB_ENGINE=mysql uv run python manage.py migrate --plan` 不再无输出挂起，本机当前 MySQL 握手仍无响应，约 10 秒内明确失败为 `OperationalError: Lost connection to MySQL server during query (timed out)`；需要后续检查本机 MySQL 服务监听、代理或连接目标状态，才能得到迁移计划完成态。
- 运行时 `INSTALLED_APPS` 未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- 字段内省确认 `CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期事实字段，仅保留 `risk_expired`。
- 过滤运行时代码扫描未命中 `service_expires_at`、旧计划快照或旧退款入口；命中的 `CloudServerOrder.objects.update(ip_recycle_at=asset.actual_expires_at)` 属于固定 IP 保留/删除流程的既有操作时间维护，不是订单到期事实回流。
- 机器人返回链、`callback_data` 长度、后台任务中心状态统计、支付扫描器和资源监控详情按钮缓存相关聚焦测试通过。

## 最近验证

- 后端检查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- 字段内省：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段/废弃 app 内省..."` 通过，输出 `retired_installed []`、`CloudAsset expiry fields ['actual_expires_at']`、`CloudServerOrder expiry fields {'actual_expires_at': False, 'service_expires_at': False}`、`CloudAssetDashboardSnapshot expiry-like fields ['risk_expired']`。
- 编译检查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/settings.py core/tests.py bot/handlers.py bot/keyboards.py cloud/task_center.py cloud/api_tasks.py cloud/lifecycle.py orders/payment_scanner.py cloud/api_assets.py cloud/api_orders.py cloud/api_asset_snapshots.py cloud/models.py cloud/sync_jobs.py` 通过。
- 设置单测：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test core.tests.MySqlSqlModeSettingsTestCase core.tests.MySqlTimeoutSettingsTestCase --settings=shop.settings --verbosity=2` 通过，8 个测试 OK。
- 聚焦测试：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase cloud.tests_task_center.CloudTaskCenterApiTestCase cloud.tests.CloudServerServicesTestCase.test_due_orders_use_asset_expiry_for_lightsail_lifecycle cloud.tests.CloudServerServicesTestCase.test_cloud_asset_detail_does_not_fallback_to_order_asset_expiry orders.tests.TronMonitorStatsTestCase.test_tx_detail_cache_is_scoped_per_user_for_same_hash orders.tests.TronMonitorStatsTestCase.test_resource_detail_cache_is_scoped_per_user_for_same_address_time --settings=shop.settings --verbosity=2` 通过，67 个测试 OK。
- 迁移计划复查：修复前 `DB_ENGINE=mysql ... perl -e 'alarm 60; exec @ARGV' uv run python manage.py migrate --plan` 返回 `142`；`faulthandler` 追踪定位在 PyMySQL 握手读取；修复后 `DB_ENGINE=mysql ... uv run python manage.py migrate --plan` 约 10 秒内失败为 MySQL 握手超时，不再无限挂起。
- 只读扫描：本机缺少 `rg`，本轮使用 `git ls-files '*.py' | grep ... | xargs grep -nE ...` 扫描旧到期字段、旧计划快照和旧退款入口。
- 代码检查：`git diff --check` 通过。
- SQLite 测试库输出不支持 `db_comment` / `db_table_comment` 的 warnings，属于当前测试环境预期差异；测试中故意触发的异常/警告路径已由断言覆盖。

## 剩余风险

- 当前 MySQL 服务或连接目标仍在握手阶段无响应；代码已避免无限等待，但本机仍未得到 `migrate --plan` 的成功完成态。
- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除数据或生产发布。
- 本地 50 万压测数据仍保留；清理属于删除数据操作，需要单独确认。
- 后端仓库仍有本轮无关未跟踪文档 `docs/jisou-bot-functions.md`、`docs/telegram-search-development-plan.md`、`docs/telegram-search-large-scale-architecture.md`，本轮未处理。

## 下一步

- 下一轮继续按固定巡检清单执行，并优先检查本机 MySQL 监听、代理或服务状态，使 `migrate --plan` 恢复成功完成态。
