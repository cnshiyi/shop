# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 13:25 CST
- 状态：已完成生命周期计划页“不要兼容旧字段”的重构收口。
- 本轮范围：后端生命周期计划 API 严格四表契约、任务中心生命周期来源收敛、停用云账号资产可见口径修复、前端计划页旧字段删除。

## 修改内容

- 生命周期计划 API：
  - 删除旧兼容字段 `due_items`、`future_plan_items`、`history_items`、`shutdown_items`、混合 `ip_delete_items`。
  - 删除旧 `_build_lifecycle_plan_bundle()` / `_collect_lifecycle_plan_rows()` 路径。
  - 响应只保留四张表：`shutdown_plan_items`、`server_delete_items`、`ip_delete_plan_items`、`ip_delete_history_items`。
  - 刷新接口只返回四张表 loaded/count 统计，不再返回旧 due/future/history/shutdown 兼容计数。
- 生命周期查询层：
  - `cloud/lifecycle_plan_queries.py` 中服务器关机计划只包含未关机资产，服务器删除计划只包含关机完成资产。
  - 排序统一按 `actual_expires_at/user_id/id`，分页契约继续使用 `pagination.{table}.page/page_size/total/loaded`。
  - 不再把未关联云账号或停用云账号的资产从计划查询/统计里过滤掉，避免代理列表可见但计划页不可管理的孤儿资产。
- 计划页去重：
  - 同 IP 多条资产时，优先保留真实运行/已关机资产，避免后台人工编辑生成的 pending 审计资产覆盖真实资产。
- 任务中心：
  - 生命周期区块不再调用旧 bundle，改为读取当前关机计划、服务器删除计划、IP 删除计划和近期失败历史。
- 前端计划页：
  - TypeScript 类型删除旧兼容字段。
  - 计划页不再 fallback 到 `shutdown_items` / 混合 `ip_delete_items`。
  - 移除旧“服务器删除历史记录”卡片，保留当前的关机计划、删除计划、IP 删除计划、IP 删除历史记录。

## 验证

本地已通过：

```bash
uv run python -m py_compile bot/api.py cloud/lifecycle_plan_queries.py cloud/task_center.py cloud/management/commands/refresh_lifecycle_plans.py cloud/tests.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center.CloudTaskCenterApiTestCase cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_fields_basic_omits_notes_and_execution_payload cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_future_server_assets_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_reuses_cached_count_snapshot_after_refresh cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_split_shutdown_before_server_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_separate_ip_delete_plan_and_history_items cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_sort_shutdown_items_by_delete_time cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_group_same_delete_time_by_user cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_lifecycle_plan_counts_match_proxy_list_assets --settings=shop.settings --verbosity=1
uv run python manage.py check
DB_ENGINE=mysql uv run python manage.py check
DB_ENGINE=mysql uv run python manage.py migrate --plan
/Users/a399/.homebrew/bin/pnpm -C /Users/a399/Desktop/data/vue-shop-admin --filter @vben/web-antd typecheck
git diff --check
```

结果：后端编译通过；25 条聚焦测试通过；默认和 MySQL `manage.py check` 通过；MySQL 无待执行迁移；前端 typecheck 通过；diff 空白检查通过。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照或旧退款入口。
- 红线扫描未发现 `service_expires_at`、旧退款入口或废弃 runtime app 回流；命中的 `accounts` 均为 Telegram/同步接口普通字段名，非废弃 app。

## 剩余风险

- `CloudLifecycleTask` 作为计划页最终/可选数据源的路线仍暂缓，后续需单独补丁明确刷新策略和执行器边界。
- 任务中心生命周期、通知、自动续费统计仍建议后续抽 domain metrics，避免计数口径再次分叉。
- 机器人返回链仍需后续抽 callback source 编解码模块，集中处理 64 字节限制。
- 本地高数据压测数据仍保留，清理需要单独确认。
