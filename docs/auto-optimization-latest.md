# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 23:16 CST
- 状态：完成生命周期计划强身份去重修复，避免同一真实服务器因同步脏资产和订单资产同时进入关机/删机计划。
- 后端提交：本轮完成后提交，具体哈希以 `git log -1` 为准。
- 前端提交：无前端代码变更。

## 本轮背景

- 本轮执行 `continue to next task` 自动优化流程。
- 开始时 `git status --short` 显示 `cloud/lifecycle_plan_queries.py`、`cloud/tests.py` 已有未提交改动；本轮先审查并沿用这些改动，没有回退用户改动。
- 最近提交为 `72a9f1d docs: record fixed checklist audit`。
- `TODO.md` 中显式待办均已完成；本轮处理工作区中已存在的生命周期计划去重修复，并补齐任务中心口径和验证。
- 本轮不执行真实支付、链上广播、真实云资源操作、生产发布、删除数据、性能压测或批量造数。

## 本轮修复

- `cloud/lifecycle_plan_queries.py`
  - 新增服务器生命周期计划强身份去重查询。
  - 按 `provider_resource_id` 优先、`instance_id` 次之、资产 `id` 兜底生成服务器身份。
  - 同一身份内优先选择已进入关机完成/删机阶段的资产，再优先选择有关联订单的资产，避免阶段倒退和重复计数。
  - 关机计划、删机计划分页和总数统计统一使用去重后的查询。
- `cloud/task_center.py`
  - 任务中心生命周期预览和 DB 任务重叠判断统一使用去重查询，避免后台总览和计划页口径不一致。
- `cloud/tests.py`
  - 增加同一云资源同步脏资产不重复进入关机计划的回归测试。
  - 增加同一云资源已关机脏行优先进入删机阶段、不倒退到关机计划的回归测试。
- `cloud/tests_task_center.py`
  - 增加任务中心生命周期预览去重回归测试。

## 结论

- 生命周期计划页不会再因同一真实服务器的重复 `CloudAsset` 行重复展示或重复计数。
- 已关机/已暂停等删机阶段事实优先于仍显示运行中的旧行，避免同一服务器同时出现在关机计划和删机计划之间倒退。
- 后台任务中心生命周期总览预览项与计划页统计保持同一去重口径。
- 本轮没有恢复废弃 runtime app。
- 本轮没有恢复 `CloudServerOrder.service_expires_at`、订单侧 `actual_expires_at` 或旧计划快照表。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plan_server_queryset_avoids_unattached_ip_subquery cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_keep_same_ip_orphan_servers_visible_across_pages cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_dedupes_server_shutdown_by_strong_cloud_identity cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_future_server_assets_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_rebuilds_cached_count_snapshot_when_asset_changes --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_deduped_server_prefers_shutdown_complete_stage --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/lifecycle_plan_queries.py cloud/task_center.py cloud/tests.py cloud/tests_task_center.py
git diff --check
```

结果：

- `cloud.tests_task_center` 18 个任务中心聚焦测试通过。
- 生命周期计划 7 个相关聚焦测试通过。
- `manage.py check` 通过。
- 编译检查通过。
- `git diff --check` 通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。

## 剩余风险

- 本轮未执行 10 万级深分页压测。
- 后续如继续做性能压测或大数据分页验证，仍必须先创建全新的独立测试数据库，并记录数据库名、端口、造数规模、命令、结果和清理策略。
