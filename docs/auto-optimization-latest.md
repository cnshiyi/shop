# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-06 10:45 CST
- 状态：`TODO.md` 当前无未完成明确任务，本轮按固定巡检清单执行只读巡检；未发现新的安全业务代码修复点，仅更新自动优化记录。
- 本轮范围：Django 基础检查、MySQL 检查与迁移计划、云资产到期事实和废弃 app 回流巡检、旧计划快照和旧退款入口扫描、机器人返回链和 `callback_data` 聚焦测试、后台任务中心状态统计、支付详情缓存隔离验证。

## 修改内容

- 未修改业务代码。
- 覆盖更新本文件，并在 `docs/refactor-version-record.md` 追加本轮中文巡检记录。

## 巡检结论

- `uv run python manage.py check` 通过，系统检查无问题。
- `DB_ENGINE=mysql uv run python manage.py check` 通过，系统检查无问题。
- `DB_ENGINE=mysql uv run python manage.py migrate --plan` 通过，输出 `No planned migration operations.`。
- 运行时 `INSTALLED_APPS` 未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- 字段内省确认 `CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 仅保留 `asset_due_sort_at` 和 `risk_due_soon` 等到期/风险相关字段。
- 运行时代码扫描未命中 `service_expires_at`、旧计划快照或旧退款入口；废弃 app 名称扫描仅命中当前 Telegram/云账号 payload 的普通 `accounts` 键，不是废弃 runtime app 回流。
- 运行时代码中 `actual_expires_at` 命中集中在 `CloudAsset`、API 输出、生命周期、同步、后台排序缓存、手动编辑路径和测试断言，未发现订单侧到期事实字段回流。
- 机器人返回链、`callback_data` 长度、后台任务中心状态统计、支付扫描器和资源监控详情按钮缓存相关聚焦测试通过。

## 最近验证

- 后端检查：`UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- MySQL 后端检查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- 迁移计划复查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 perl -e 'alarm 25; exec @ARGV' uv run python manage.py migrate --plan` 通过，输出 `Planned operations: No planned migration operations.`。
- 字段内省：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段/废弃 app 内省..."` 通过，输出 `retired_installed []`、`CloudAsset expiry fields ['actual_expires_at']`、`CloudServerOrder expiry flags {'actual_expires_at': False, 'service_expires_at': False}`、`CloudAssetDashboardSnapshot expiry-like fields ['asset_due_sort_at', 'risk_due_soon']`。
- 编译检查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py cloud/task_center.py cloud/api_tasks.py cloud/lifecycle.py orders/payment_scanner.py cloud/api_assets.py cloud/api_orders.py cloud/api_asset_snapshots.py cloud/models.py cloud/sync_jobs.py shop/settings.py core/runtime_config.py` 通过。
- 聚焦测试：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase cloud.tests_task_center.CloudTaskCenterApiTestCase cloud.tests.CloudServerServicesTestCase.test_due_orders_use_asset_expiry_for_lightsail_lifecycle cloud.tests.CloudServerServicesTestCase.test_cloud_asset_detail_does_not_fallback_to_order_asset_expiry orders.tests.TronMonitorStatsTestCase.test_tx_detail_cache_is_scoped_per_user_for_same_hash orders.tests.TronMonitorStatsTestCase.test_resource_detail_cache_is_scoped_per_user_for_same_address_time --settings=shop.settings --verbosity=1` 通过，67 个测试 OK。
- `git diff --check` 通过。
- 只读扫描：本机仍缺少 `rg`，本轮使用 `git grep` 的文件列表、计数和限量输出扫描旧到期字段、旧退款入口、旧计划快照、废弃 app 回流和 `actual_expires_at` 使用范围。
- SQLite 测试库输出不支持 `db_comment` / `db_table_comment` 的 warnings，属于当前测试环境预期差异；测试中故意触发的异常/警告路径已由断言覆盖。

## 剩余风险

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除数据或生产发布。
- 本地 50 万压测数据仍保留；清理属于删除数据操作，需要单独确认。
- 后端仓库仍有本轮无关未跟踪文档 `docs/jisou-bot-functions.md`、`docs/telegram-search-development-plan.md`、`docs/telegram-search-large-scale-architecture.md`，本轮未处理。

## 下一步

- 下一轮继续按 `TODO.md` 首个未完成任务执行；如果仍无明确任务，则按固定巡检清单继续只读巡检并只修复一个明确安全问题。
