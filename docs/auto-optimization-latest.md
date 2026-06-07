# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 03:10 CST
- 状态：已修复自动续费详情页慢加载和前端控制台告警，并完成真实页面验证。
- 本轮范围：任务中心进入自动续费详情、自动续费计划查询层、自动续费详情前端表格 rowKey、Typography 省略文本、机器人多任务并发聚焦测试。

## 修复内容

- `cloud/api_tasks.py`
  - 自动续费详情不再调用全生命周期 `_get_due_orders()` 扫描全量资产。
  - 改为直接从 `CloudAsset.actual_expires_at` 查询自动续费到期资产，继续排除已删除/终止资产、未附加固定 IP 和无公网 IP 资产。
  - 重试队列的订单资产状态改为批量查询，避免逐订单查询资产。
  - 构建待执行/未来计划项时复用 notice payload，避免重复读取资产到期事实。
  - 保留最近失败原因，直接到期订单和失败重试订单的状态口径不丢失。
- `cloud/tests.py`
  - 自动续费详情测试改为使用 `CloudAsset.actual_expires_at` 驱动，不再 patch 旧 `_get_due_orders` 入口。
  - 明确无资产到期事实的订单不会进入自动续费详情计划。
- 前端 `apps/web-antd/src/views/dashboard/tasks/auto-renew-detail.vue`
  - 表格 `row-key` 不再使用 Ant Design Vue 已废弃的 `index` 参数。
  - 带省略的 `TypographyParagraph` 改用 `content`，消除控制台 error。

## 性能对账

后端函数计时：

- 修复前：`collect_sec 54.581`，`build_sec 67.3`。
- 修复后：`collect_sec 0.606`，`build_sec 0.902`。

真实接口：

- 修复并重启临时后端前，旧进程接口约 `74.05s`。
- 重启后新代码接口约 `1.21s`。
- 返回口径保持一致：
  - `due_count=443`
  - `recent_failure_count=1026`
  - `recent_success_count=0`
  - `latest_batch_count=171`
  - `latest_batch_failure_count=171`
  - `due_items=443`
  - `history_items=200`
  - `future_plan_items=0`

## 真实页面验证

使用 Playwright 打开：

- `http://127.0.0.1:5666/admin/tasks/auto-renew`

页面确认：

- 页面标题：`续费列表 - Vben Admin Antd`。
- 顶部统计显示：
  - 最近24小时成功：`0`
  - 最近24小时失败：`1026`
  - 当前待执行 IP：`443`
  - 最新批次：`7a1c26d5a339462a / 171 条`
- 待执行 IP 表真实渲染，首屏显示失败待重试记录、订单号、到期时间、自动续费时间、余额和操作按钮。
- 历史执行记录真实渲染。
- 请求状态：
  - `/api/admin/user/info`：`200`
  - `/api/admin/tasks/auto-renew/`：`200`
  - 未再出现 `net::ERR_ABORTED`。
- 浏览器控制台：`0 error / 0 warning`。

## 验证命令

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_tasks.py cloud/tests.py cloud/tests_task_center.py bot/tests.py
cd /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd && pnpm exec vue-tsc --noEmit --skipLibCheck
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_auto_renew_task_detail_includes_due_retry_and_fallback_items cloud.tests.CloudServerServicesTestCase.test_auto_renew_detail_ignores_order_without_asset_expiry_fact cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_tasks_executes_due_retry_and_fallback_queue cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_order_executes_single_order --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center.CloudTaskCenterApiTestCase.test_auto_renew_section_counts_retry_failed_as_failed cloud.tests_task_center.CloudTaskCenterApiTestCase.test_auto_renew_section_counts_recent_failed_history_as_failed cloud.tests_task_center.CloudTaskCenterApiTestCase.test_auto_renew_section_does_not_duplicate_active_failure_history cloud.tests_task_center.CloudTaskCenterApiTestCase.test_auto_renew_section_counts_all_recent_failed_history_queryset --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated --settings=shop.settings --verbosity=1
```

SQLite `db_comment` 警告仍是已知数据库能力差异，不影响本轮结果。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。
- 本轮未打印 Telegram token、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。

## 下一步

- 继续真实浏览器巡检生命周期创建/关机/删除/IP 删除开关联动。
- 继续机器人真机多任务高并发点击测试，覆盖购买、续费、换 IP、重装迁移/重建、修改配置和返回链。
- 继续代理列表各标签翻页、跳页和数据库对账。
