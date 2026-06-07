# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 06:52 CST
- 状态：完成一轮机器人多任务高并发、云资产同步 worker、代理列表风险标签和真实页面巡检；发现并修复代理列表分组页旧快照 payload 缺字段导致的 500，并优化 IP 视图默认加载路径。
- 本轮范围：Telegram 机器人并发隔离、云资产同步任务队列/worker/重试/取消/指标、异常云账号资产可见性、未附加 IP 缺失确认、代理列表风险标签真实 HTTP 与真实前端页面。

## 修复

- 后端 `cloud/api_asset_snapshots.py`
  - 分组构造不再假设旧快照 payload 一定包含 `tg_user_id`、`user_display_name`、`username_label`、`actual_expires_at`。
  - 修复 `grouped=1&risk_status=unattached_ip` 在旧快照数据下触发 `KeyError` 并返回 500 的问题。
- 后端 `cloud/tests.py`
  - 新增旧快照 payload 缺少用户展示字段和到期展示字段时，风险标签分组分页仍返回 200 的回归测试。
- 前端 `apps/web-antd/src/views/dashboard/cloud-assets/index.vue`
  - 代理列表 IP 视图默认关闭分组，首屏和标签切换直接走服务端行分页。
  - 从其他视图切回 IP 视图时自动关闭分组，避免 250 万数据下分组 distinct 拖慢首屏。

## 巡检结论

- 机器人多任务高并发专项通过：
  - 通知复制 wrapper 并发发送隔离通过。
  - 云服务器后台钱包直付/补付高并发隔离通过。
- 云资产同步专项通过：
  - 同步入队、执行、详情、列表、重试、取消、指标汇总、选定资产同步、worker 认领执行均通过。
  - 云账号停用或缺失的资产仍出现在默认全部列表，避免孤儿资产。
  - 未附加 IP 缺失确认状态继续暴露在删除计划项中。
- 真实 HTTP：
  - IP 视图非分组标签分页：全部 `2489998`、未附加固定 IP `100001`、云账号异常 `1145002`、关机计划关闭 `100384`、未绑定群组 `100013`、续费关闭 `104558`，均加载 20 行。
  - `compact=1` 分组接口：全部 `2489996` 组、云账号异常 `1145001` 组、未附加固定 IP `100001` 组，均返回 20 行且无 500。
- 真实前端页面：
  - `/admin/cloud-assets` 初始进入 IP 视图为非分组，显示 `共 2489998 条代理`，20 行。
  - 实际点击未附加固定 IP、云账号异常、关机计划关闭、未绑定群组、续费关闭、全部，每个标签都返回对应 API 响应并更新页面分页总数。
  - 页面控制台错误数：`0`。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_all_includes_disabled_or_missing_cloud_account_assets cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_runs_enabled_accounts_and_merges_results cloud.tests.CloudServerServicesTestCase.test_cloud_asset_sync_jobs_metrics_returns_operational_summary cloud.tests.CloudServerServicesTestCase.test_cancel_queued_cloud_asset_sync_job_marks_terminal_and_events cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_with_selected_assets_uses_asset_scoped_tasks cloud.tests.CloudServerServicesTestCase.test_process_cloud_asset_sync_jobs_worker_processes_queued_job cloud.tests.CloudServerServicesTestCase.test_sync_missing_confirmation_note_preserves_existing_note cloud.tests.CloudServerServicesTestCase.test_sync_missing_delete_threshold_is_at_least_five cloud.tests.CloudServerServicesTestCase.test_sync_missing_confirmation_requires_interval cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_expose_missing_confirmation_state --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_risk_page_tolerates_old_snapshot_payload_missing_user_fields cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_paginated_uses_twenty_user_groups_per_page cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_total_counts_distinct_groups_only --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
pnpm --filter @vben/web-antd typecheck
git diff --check
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\." cloud bot orders core shop -g '!**/migrations/**'
```

说明：

- SQLite 的 `db_comment` warnings 仍是已知测试噪声。
- 临时后台 session 已删除。
- Playwright 临时截图目录已删除。

## 受限项

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮没有打印密钥、Telegram session、支付密钥或完整代理链接。

## 下一步

- 继续不停轮巡检，下一轮优先覆盖生命周期创建服务器/关机/删除链路的开关联动、代理列表深分页跳页对账、机器人全功能真机可操作路径。
- 继续关注云账号异常标签冷缓存加载时间，必要时为非 compact 云资源/操作视图补专用分页索引或后台预热统计。
