# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 13:31 CST
- 状态：已完成无明确 TODO 后的一轮只读巡检；未发现需要修改业务代码的问题。
- 本轮范围：生命周期计划、任务中心、机器人返回链、红线扫描、默认/MySQL 检查、前端类型检查。

## 巡检结论

- 后端和前端工作区在巡检开始前均为干净状态。
- `TODO.md` 已无未完成任务；按 `docs/auto-optimization-control.md` 固定巡检清单执行只读巡检。
- 生命周期计划上一轮四表契约仍有效，相关聚焦测试通过。
- 机器人 callback/返回链测试通过，覆盖资产详情、订单详情、续费、钱包支付续费、换 IP、重装、修改配置等既有回归用例。
- 前端 `@vben/web-antd` 类型检查通过。
- 默认 MySQL 直接跑全量 `bot.tests` 时命中已有测试库 `test_a`，Django 需要交互确认删除；本轮未自动删除测试库，改用 `DJANGO_TEST_SQLITE=1` 隔离测试库完成验证。

## 验证

本地已通过：

```bash
uv run python manage.py check
uv run python -m py_compile bot/api.py bot/handlers.py cloud/lifecycle_plan_queries.py cloud/task_center.py cloud/management/commands/refresh_lifecycle_plans.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center.CloudTaskCenterApiTestCase cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_fields_basic_omits_notes_and_execution_payload cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_future_server_assets_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_reuses_cached_count_snapshot_after_refresh cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_split_shutdown_before_server_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_separate_ip_delete_plan_and_history_items cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_sort_shutdown_items_by_delete_time cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_group_same_delete_time_by_user cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_lifecycle_plan_counts_match_proxy_list_assets --settings=shop.settings --verbosity=1
DB_ENGINE=mysql uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests --settings=shop.settings --verbosity=1
/Users/a399/.homebrew/bin/pnpm -C /Users/a399/Desktop/data/vue-shop-admin --filter @vben/web-antd typecheck
```

结果：默认和 MySQL `manage.py check` 通过；后端核心文件编译通过；生命周期/任务中心聚焦测试 25 条通过；机器人测试 104 条通过；前端 typecheck 通过。SQLite 测试中的字段/表注释警告为已知数据库能力差异。

## 红线扫描

```bash
rg -n "service_expires_at" shop core bot orders cloud -g '!*/migrations/*'
rg -n "old_refund|legacy_refund|refund_cloud_order|refund_order|apply_refund|process_refund|create_refund" shop core bot orders cloud -g '!*/migrations/*'
rg -n "['\"](accounts|finance|mall|monitoring|dashboard_api|biz)['\"]|include\\(['\"](accounts|finance|mall|monitoring|dashboard_api|biz)" shop core bot orders cloud -g '!*/migrations/*'
rg -n "lifecycle_plan_projection|0058_lifecycle_task_plan_page_index|plan_projection|page_lifecycle_plan_tasks|sync_lifecycle_plan_projection" bot cloud docs -g '!*/migrations/*'
```

结果：未发现 `service_expires_at`、旧退款入口或废弃 runtime app 回流；`accounts` 命中为 Telegram/同步接口普通字段名；计划投影命中仅在历史文档记录中。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照或旧退款入口。

## 剩余风险

- `CloudLifecycleTask` 作为计划页最终/可选数据源的路线仍暂缓，后续需单独补丁明确刷新策略和执行器边界。
- 任务中心生命周期、通知、自动续费统计仍建议后续抽 domain metrics，避免计数口径再次分叉。
- 机器人返回链仍需后续抽 callback source 编解码模块，集中处理 64 字节限制。
- 本地高数据压测数据仍保留，清理需要单独确认。
- 默认 MySQL 测试库 `test_a` 已存在；如后续需要跑 MySQL 全量测试，应先人工确认是否可以删除或改用独立测试库名。
