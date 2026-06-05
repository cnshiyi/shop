# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-06 06:08 CST
- 状态：`TODO.md` 当前无新的未完成明确任务，本轮按 `docs/auto-optimization-control.md` 固定巡检清单执行只读巡检，未发现需要业务代码修复的新问题；MySQL `migrate --plan` 只读验证本轮超时，已列入剩余风险。
- 本轮范围：废弃 runtime app 回流、云资产唯一到期事实、订单侧到期字段/旧计划/旧退款入口、Telegram 返回链和 `callback_data` 长度、后台任务中心状态统计、支付扫描器和资源监控详情按钮缓存、迁移计划检查；未改动业务代码、前端代码、云资源或支付链路。

## 巡检结论

- 运行时 `INSTALLED_APPS` 未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- 字段内省确认 `CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期事实字段，仅保留 `risk_expired` 风险标记。
- 过滤运行时代码扫描未命中 `service_expires_at`、旧计划快照或旧退款入口；废弃 app 名称命中主要是云账号/Telegram 账号语义、权限代号、`core.dashboard_api` 当前模块名和兼容注释，不是废弃 runtime app 回流。
- 机器人返回链和 `callback_data` 聚焦测试通过，覆盖资产详情、订单详情、续费、钱包续费、换 IP、重装、修改配置等短回调路径。
- 后台任务中心聚焦测试通过，覆盖自动续费、通知计划、生命周期计划的失败、重试、待处理统计和去重逻辑。
- 支付扫描器和资源监控详情按钮缓存相关测试通过，未发现新的详情串读风险。

## 最近验证

- 后端检查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- 字段内省：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段/废弃 app 内省..."` 通过，输出 `retired_installed []`、`CloudAsset expiry fields ['actual_expires_at']`、`CloudServerOrder actual_expires_at False`、`CloudServerOrder service_expires_at False`、`CloudAssetDashboardSnapshot expiry-like fields ['risk_expired']`。
- 编译检查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py cloud/task_center.py cloud/api_tasks.py cloud/lifecycle.py orders/payment_scanner.py cloud/api_assets.py cloud/api_orders.py cloud/api_asset_snapshots.py cloud/models.py cloud/sync_jobs.py` 通过。
- 聚焦测试：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase cloud.tests_task_center.CloudTaskCenterApiTestCase cloud.tests.CloudServerServicesTestCase.test_due_orders_use_asset_expiry_for_lightsail_lifecycle cloud.tests.CloudServerServicesTestCase.test_cloud_asset_detail_does_not_fallback_to_order_asset_expiry orders.tests.TronMonitorStatsTestCase.test_tx_detail_cache_is_scoped_per_user_for_same_hash orders.tests.TronMonitorStatsTestCase.test_resource_detail_cache_is_scoped_per_user_for_same_address_time --settings=shop.settings --verbosity=2` 通过，67 个测试全部 OK。
- 迁移计划：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan` 本轮无输出挂起；终止该只读验证进程后用 `perl -e 'alarm 20; exec @ARGV' uv run python manage.py migrate --plan` 重试，返回 `142` 超时。
- 只读扫描：本机缺少 `rg`，已改用 `git ls-files '*.py' | xargs grep -nE ...`，并对运行时代码排除 migrations/tests 后复扫旧到期字段、旧退款、旧计划和废弃 app 关键字。
- 代码检查：`git diff --check` 通过。
- SQLite 测试库输出不支持 `db_comment` / `db_table_comment` 的 warnings，属于当前测试环境预期差异；测试中故意触发的异常/警告路径已由断言覆盖。

## 剩余风险

- 本轮 MySQL `migrate --plan` 只读验证两次挂起/超时，未得到 `No planned migration operations` 结果；需要下一轮优先复查 MySQL 连接、锁等待或迁移计划输出。
- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除数据或生产发布。
- 本地 50 万压测数据仍保留；清理属于删除数据操作，需要单独确认。
- 后端仓库仍有本轮无关未跟踪文档 `docs/jisou-bot-functions.md`、`docs/telegram-search-development-plan.md`、`docs/telegram-search-large-scale-architecture.md`，本轮未处理。

## 下一步

- `TODO.md` 当前无新的未完成明确任务；下一轮继续按固定巡检清单执行，并优先复查 MySQL `migrate --plan` 超时原因。
