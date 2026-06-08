# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 22:55 CST
- 状态：完成固定巡检清单只读复查；未发现需要本轮修复的明确安全问题。
- 后端提交：本轮记录待提交。
- 前端提交：无前端代码变更。

## 本轮背景

- `TODO.md` 中显式待办均已完成，本轮按 `docs/auto-optimization-control.md` 固定巡检清单执行只读巡检。
- 开始时 `git status --short` 为空，工作树干净。
- 最近提交为 `2b0cd8e docs: record trx rate cache fallback`。
- 本轮不执行真实支付、链上广播、真实云资源操作、生产发布或删除数据。

## 巡检内容

- 运行 Django 基础检查，确认当前默认配置无系统检查错误。
- 扫描订单到期字段、旧计划快照、旧退款入口和废弃 runtime app 回流风险。
- 扫描 `CloudAsset.actual_expires_at` 相关运行时代码，确认资产到期事实仍落在云资产字段及 `order_asset_expiry()` 派生读取链。
- 扫描 Telegram 回调、返回链和任务中心状态统计相关代码。
- 运行 Telegram 云资产/订单详情、续费、换 IP、重装、修改配置、自动续费返回链与 callback 64 字节限制聚焦测试。
- 运行任务中心统一总览、生命周期任务、通知任务、自动续费重试失败/待重试状态统计聚焦测试。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/keyboards.py bot/handlers.py cloud/task_center.py cloud/tests_task_center.py
```

补充巡检：

```bash
rg -n "service_expires_at|order.*actual_expires_at|actual_expires_at.*order|plan snapshot|计划快照表|refund|退款|accounts|finance|mall|monitoring|dashboard_api|biz" --glob '!docs/refactor-version-record.md' --glob '!docs/auto-optimization-latest.md' --glob '!docs/real-machine-test-report.md' --glob '!*.pyc'
rg -n "actual_expires_at" cloud orders bot core --glob '!*/migrations/*'
rg -n "callback_data|data=|CallbackQuery|InlineKeyboardButton|callback" bot cloud orders core --glob '!*/migrations/*'
rg -n "TaskCenter|task center|任务中心|retry|重试|failed|failure|status" cloud bot orders core --glob '!*/migrations/*'
```

说明：

- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。
- 红线扫描命中主要为既有文档、迁移历史、兼容 helper 命名、当前 `CloudAsset.actual_expires_at` 口径使用，以及后台权限码中的历史英文标签；本轮未恢复废弃 runtime app、订单到期字段、旧计划快照或旧退款入口。

## 剩余风险

- 本轮为只读巡检和文档记录，未执行真实支付、链上广播、真实云资源操作或生产环境长时间运行验证。
- 继续关注机器人返回链、任务中心状态统计和 MySQL/SQLite 差异。
