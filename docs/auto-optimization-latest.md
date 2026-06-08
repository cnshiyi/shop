# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 23:10 CST
- 状态：`TODO.md` 显式待办均已完成，本轮按固定巡检清单完成只读复查，未发现需要立即修复的明确问题。
- 后端提交：本轮完成后提交，具体哈希以 `git log -1` 为准。
- 前端提交：无前端代码变更。

## 本轮背景

- 本轮执行 `continue to next task` 自动优化流程。
- 开始时 `git status --short` 为空，工作树干净。
- 最近提交为 `490e30c docs: record fixed checklist audit`。
- `TODO.md` 中所有明确任务均已勾选完成，因此按 `docs/auto-optimization-control.md` 固定巡检清单执行只读巡检。
- 本轮不执行真实支付、链上广播、真实云资源操作、生产发布、删除数据、性能压测或批量造数。

## 本轮巡检

- 运行 Django 基础检查，确认默认配置无系统检查错误。
- 扫描订单到期字段、旧计划快照、旧退款入口和废弃 runtime app 回流风险。
- 扫描 `CloudAsset.actual_expires_at` 相关运行时代码，确认资产到期事实仍落在 `CloudAsset.actual_expires_at` 及 `order_asset_expiry()` 派生读取链。
- 扫描 Telegram callback、返回链、任务中心状态统计、失败和重试口径相关代码。
- 运行任务中心统一总览、生命周期任务、通知任务、自动续费重试失败/待重试状态统计聚焦测试。
- 运行机器人资产详情、订单详情、续费、换 IP、重装、修改配置、钱包续费返回链和 callback 长度聚焦测试。
- 运行相关文件编译检查。

## 结论

- 本轮未发现需要立即修复的明确安全问题。
- 本轮没有恢复废弃 runtime app。
- 本轮没有恢复 `CloudServerOrder.service_expires_at`、订单侧 `actual_expires_at` 或旧计划快照表。
- 本轮没有恢复旧退款入口或旧退款函数名。
- 红线扫描命中主要为既有文档、迁移历史、当前 `CloudAsset.actual_expires_at` 口径使用、`core.dashboard_api` 共享 helper 命名、Telegram 账号/云账号正常业务命名。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/keyboards.py bot/handlers.py cloud/task_center.py cloud/tests_task_center.py cloud/management/commands/prepare_load_test_db.py cloud/tests_load_test_db.py
```

补充巡检：

```bash
rg -n "service_expires_at|order.*actual_expires_at|actual_expires_at.*order|plan snapshot|计划快照表|refund|退款|accounts|finance|mall|monitoring|dashboard_api|biz" --glob '!docs/refactor-version-record.md' --glob '!docs/auto-optimization-latest.md' --glob '!docs/real-machine-test-report.md' --glob '!*.pyc'
rg -n "actual_expires_at" cloud orders bot core --glob '!*/migrations/*'
rg -n "callback_data|data=|CallbackQuery|InlineKeyboardButton|callback" bot cloud orders core --glob '!*/migrations/*'
rg -n "TaskCenter|task center|任务中心|retry|重试|failed|failure|status" cloud bot orders core --glob '!*/migrations/*'
```

结果：

- `manage.py check` 通过。
- `cloud.tests_task_center` 17 个任务中心聚焦测试通过。
- `bot.tests.RetainedIpRenewalUiTestCase` 51 个机器人返回链和 callback 长度聚焦测试通过。
- 编译检查通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。

## 剩余风险

- 本轮为只读巡检，未执行 10 万级深分页压测。
- 后续如继续做性能压测或大数据分页验证，仍必须先创建全新的独立测试数据库，并记录数据库名、端口、造数规模、命令、结果和清理策略。
