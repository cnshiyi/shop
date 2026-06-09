# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-10 00:07 CST
- 状态：已修复生命周期关机计划数量偏小的问题。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户指出“关机计划是未来计划，数量不对”。
- 复查后确认前端标题使用后端 `shutdown_plan_count`，不是当前页加载条数。
- 后端关机计划统计本身覆盖远期计划，但生命周期计划去重身份同时使用了 `public_ip` 和 `previous_public_ip`：
  - 旧资产只有 `previous_public_ip`，且该历史 IP 等于新资产当前 `public_ip` 时，会和新资产进入同一去重分组。
  - 原排序优先保留已关机阶段资产，可能把当前仍在运行、未来应关机的新资产挤出关机计划，导致关机计划总数偏小。

## 修复内容

- `cloud/lifecycle_plan_queries.py`
  - 生命周期计划当前资产去重只使用当前 `public_ip`。
  - 没有当前 `public_ip` 的资产按资产 ID 独立处理。
  - `previous_public_ip` 不再参与当前计划去重，避免历史 IP 压掉当前 IP。
- `cloud/tests.py`
  - 新增回归测试：旧资产 `previous_public_ip` 等于新资产 `public_ip` 时，新 running 资产仍进入关机计划，旧 stopped 资产进入删机计划。
  - 清理一个违反当前数据库硬规则的旧测试，不再构造同一当前 `public_ip` 的双资产。

## 验证

通过：

```bash
uv run python -m py_compile cloud/lifecycle_plan_queries.py bot/api.py cloud/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_previous_ip_does_not_hide_current_shutdown_plan cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_future_server_assets_beyond_loaded_limit --settings=shop.settings --verbosity=1
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_previous_ip_does_not_hide_current_shutdown_plan cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_future_server_assets_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_stopped_server_enters_delete_stage cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_handles_unique_ip_server_assets cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract --settings=shop.settings --verbosity=1
uv run python manage.py check
git diff --check
```

结果：

- 编译通过。
- 生命周期计划聚焦测试 5 条通过。
- Django 系统检查通过。
- diff 空白检查通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 告警，不属于本轮问题。

## 结论

- 关机计划继续按未来计划全量统计，不受页面加载条数截断。
- 当前 IP 是当前计划去重依据；历史 IP 只保留为历史信息，不再影响关机计划数量。
- 已关机资产仍按阶段进入服务器删除计划，不会回退到关机计划。
