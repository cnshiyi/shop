# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-06 11:15 CST
- 状态：`TODO.md` 当前没有新的未完成明确任务，本轮按固定巡检清单做只读复查；未发现需要业务代码修复的新问题。
- 本轮范围：确认工作区和最近提交、限量读取自动化控制文档、执行 Django/MySQL 检查、字段和关键字扫描、运行返回链/任务中心/生命周期/支付缓存聚焦测试，并更新中文记录。

## 修改内容

- 未修改业务代码。
- 覆盖更新本文件，并在 `docs/refactor-version-record.md` 追加本轮中文巡检记录。
- 未处理本轮无关未跟踪文档：`docs/jisou-bot-functions.md`、`docs/telegram-search-development-plan.md`、`docs/telegram-search-large-scale-architecture.md`。

## 巡检结论

- `uv run python manage.py check` 通过。
- `DB_ENGINE=mysql uv run python manage.py check` 通过。
- `DB_ENGINE=mysql uv run python manage.py migrate --plan` 通过，输出 `No planned migration operations.`。
- 运行时 `INSTALLED_APPS` 未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- 字段内省确认 `CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`，仅有当前续费宽限字段 `renew_grace_expires_at`。
- 运行时代码扫描未命中 `service_expires_at` 或旧退款入口；废弃 app 名称扫描仅命中当前 Telegram/云账号 payload 的普通 `accounts` 键。
- `dashboard_plan_snapshots` 命中为当前后台快照刷新模块，不是旧计划快照表恢复。
- 机器人返回链、`callback_data` 长度、后台任务中心状态统计、生命周期到期事实和支付/资源监控缓存相关聚焦测试通过。

## 最近验证

- 基础检查：`UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- MySQL 检查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- 迁移计划：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 perl -e 'alarm 30; exec @ARGV' uv run python manage.py migrate --plan` 通过，无待执行迁移。
- 字段/废弃 app 内省：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "..."` 通过。
- 编译检查：`uv run python -m py_compile bot/handlers.py bot/keyboards.py cloud/task_center.py cloud/api_tasks.py cloud/lifecycle.py orders/payment_scanner.py cloud/api_assets.py cloud/api_orders.py cloud/api_asset_snapshots.py cloud/models.py cloud/sync_jobs.py shop/settings.py core/runtime_config.py` 通过。
- 聚焦测试：`DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase cloud.tests_task_center.CloudTaskCenterApiTestCase ... --verbosity=1` 通过，`Ran 67 tests ... OK`。

## 剩余风险

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除数据或生产发布。
- SQLite 聚焦测试仍输出不支持 `db_comment` / `db_table_comment` 的 warnings，属于当前测试环境预期差异；测试中故意触发的异常/警告路径已由断言覆盖。
- 本地 50 万压测数据仍保留；清理属于删除数据操作，需要单独确认。
- 后端仓库仍有本轮无关未跟踪文档 `docs/jisou-bot-functions.md`、`docs/telegram-search-development-plan.md`、`docs/telegram-search-large-scale-architecture.md`，本轮未处理。

## 下一步

- `TODO.md` 暂无新的未完成明确任务；下一轮继续按固定入口巡检，只在发现一个明确安全问题时做最小修复。
