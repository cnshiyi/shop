# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 17:10 CST
- 状态：已完成通知计划服务端分页重构、代理列表分组分页前端修复和真实页面对账。
- 本轮范围：通知计划、任务中心通知统计、代理列表 IP 视图分组分页、50 万级数据页面显示真实性。

## 修改摘要

- 通知计划删除旧 `_build_notice_plan_bundle` 路径，统一改为 `_build_notice_plan_summary`。
- 通知计划接口不再返回旧 `due_items`、`future_plan_items`、`due_user_summary_items`、`future_user_summary_items` 字段，只保留 `active_user_summary_items` 和 `history_items`。
- 通知计划分页改为服务端用户分组分页，前端只请求当前页，不再按旧近期/未来两套分页口径加载。
- 通知删除提醒只受服务器删除开关影响，IP 回收提醒只受 IP 删除开关影响，不再被关机开关误挡。
- 任务中心通知统计改用新分组计划口径，避免和通知计划页再次分叉。
- 代理列表分组分页前端改为直接使用后端返回的当前页 `groups`，不再用当前页 `items` 重新建组和二次排序。
- 代理列表在 IP 视图下保留后端排序，避免前端默认排序导致翻页显示和数据库页序不一致。
- 清理本轮真实页面测试产生的 `.playwright-cli/` 临时产物和临时会话文件。

## 真实页面对账

本轮不只调 API，已打开真实前端页面 `/admin/cloud-assets` 验证显示：

- 前端：`http://127.0.0.1:5666/admin/cloud-assets`
- 后端：`http://127.0.0.1:8000`
- 数据规模：页面显示全部 `500000`，分组分页总数 `499492`。
- 第 1 页显示 `huangyating6748`、`压测Y用户00000`、`压测Y用户00075` 等，IP 为 `52.221.62.194`、`198.19.0.0`、`198.19.0.75` 等。
- 第 2 页显示 `压测Y用户01425`、`压测Y用户01500`、`压测Y用户01575` 等，IP 为 `198.19.5.145`、`198.19.5.220`、`198.19.6.39` 等。
- 第 3 页显示 `压测Y用户02925`、`压测Y用户03000`、`压测Y用户03075` 等，IP 为 `198.19.11.109`、`198.19.11.184`、`198.19.12.3` 等。
- 最后一页 `24975` 显示 12 组，用户为 `压测用户004799`、`压测用户004819`、`压测用户004839` 等，IP 为 `198.51.18.209`、`198.51.18.229`、`198.51.18.249` 等。
- 上述页面可见数据已和数据库只读分页结果对上，未发现翻页丢数据或重复显示第 1 页。

## 验证

本地已通过：

```bash
uv run python -m py_compile cloud/api_tasks.py cloud/task_center.py cloud/dashboard_snapshots.py cloud/management/commands/refresh_notice_plans.py cloud/tests.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_counts_all_future_groups_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_deep_group_page_has_no_duplicates cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_basic_fields_skip_batch_text_payload cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_hides_shutdown_disabled_lifecycle_notices cloud.tests_task_center.CloudTaskCenterApiTestCase.test_notice_section_counts_failed_retry_as_failed cloud.tests_task_center.CloudTaskCenterApiTestCase.test_notice_section_counts_recent_failed_history_as_failed --settings=shop.settings --verbosity=1
uv run python manage.py check
git diff --check
pnpm --dir /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd typecheck
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

结果：编译检查、6 个聚焦测试、Django 系统检查、前端类型检查和空白检查均通过。SQLite 测试输出的 `db_comment` 警告为已知数据库能力差异。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款入口、旧 `Server` 兼容壳或旧云 API 聚合入口。

## 剩余风险

- 本轮完成通知计划和代理列表分页真实性修复；生命周期真实云资源执行仍未在本轮触发。
- 自动续费计划和生命周期计划仍保留各自业务字段 `due_items/future_plan_items`，这不是通知计划兼容残留。
- 下一轮建议继续做任务中心、生命周期计划、通知计划在 50 万到 100 万数据下的页面跳页耗时对账。
