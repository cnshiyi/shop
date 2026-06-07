# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 17:03 CST
- 状态：已完成通知计划服务端分页专项只读审计。
- 本轮范围：通知计划摘要分页接口、任务中心通知汇总、通知计划刷新命令、前端通知计划页类型与调用参数。

## 审计摘要

- 后端未提交补丁已经把通知计划从旧 `due_items/future_plan_items` 明细模式切换为 `active_user_summary_items` 分组分页模式。
- 复查 `cloud/api_tasks.py`、`cloud/task_center.py`、`cloud/dashboard_snapshots.py`、`cloud/management/commands/refresh_notice_plans.py` 的调用链，确认通知总数、任务中心统计和刷新命令已统一走 `_build_notice_plan_summary()`。
- 复查前端 `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd` 未提交补丁，确认通知计划页已去掉 `future_limit/future_offset` 旧参数，并改用 `active_user_summary_items` 渲染。
- 本轮未发现必须立刻补代码的回归；当前补丁在已覆盖的通知计划聚焦测试、编译检查、类型检查和空白检查下通过。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_refresh_notice_plans_command_builds_notice_plan_view cloud.tests.CloudServerServicesTestCase.test_refresh_notice_plan_view_api_builds_notice_plan_view cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_uses_notice_plan_view cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_basic_fields_skip_batch_text_payload cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_counts_all_future_groups_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_deep_group_page_has_no_duplicates cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_hides_shutdown_disabled_lifecycle_notices cloud.tests_task_center.CloudTaskCenterApiTestCase.test_notice_section_counts_failed_retry_as_failed cloud.tests_task_center.CloudTaskCenterApiTestCase.test_notice_section_counts_recent_failed_history_as_failed --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_tasks.py cloud/dashboard_snapshots.py cloud/management/commands/refresh_notice_plans.py cloud/task_center.py cloud/tests.py cloud/tests_task_center.py
pnpm --dir /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd typecheck
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

结果：9 个通知计划聚焦测试、Django 系统检查、后端编译检查、前端类型检查、后端与前端空白检查均通过。SQLite 测试输出的 `db_comment` 警告为已知数据库能力差异。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款入口、旧 `Server` 兼容壳或旧云 API 聚合入口。

## 剩余风险

- 本轮属于只读专项审计，没有替当前未提交的通知计划重构补丁额外补代码。
- `cloud/tests_task_center.py` 和通知计划相关补丁仍在工作区，下一轮应继续确认剩余旧字段桩数据是否全部收口。
- 当前只覆盖后端接口和前端类型检查，尚未做浏览器真实翻页/跳页与控制台核验。
