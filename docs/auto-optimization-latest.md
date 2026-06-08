# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-09 00:08 CST
- 状态：已修复并确认生命周期真实执行链，关机、删机、未附加 IP 删除不再绕过资产计划查询层。
- 后端提交：本轮完成后提交，具体哈希以 `git log -1` 为准。
- 前端提交：无前端代码变更。

## 本轮背景

- 用户要求再次确认关机、删机、删除未附加 IP 是否严格按照计划表执行。
- 开始时工作树已有生命周期计划页和执行链未提交改动，本轮继续收敛这些改动，没有执行真实云资源创建、真实删机、真实支付、链上广播、生产发布或删除数据。

## 修复内容

- 服务器关机计划、服务器删机计划、未附加 IP 删除计划统一使用 `cloud/lifecycle_plan_queries.py` 的资产计划查询层。
- 服务器计划按公网 IP 去重，同一 IP 只保留一条计划，避免脏资产重复展示或重复执行。
- `lifecycle_tick()` 的真实关机改为执行 `run_server_asset_suspend(asset.id)`，真实删机改为执行 `run_orphan_asset_delete(asset.id)`；不再执行旧订单关机、旧订单删机、旧订单固定 IP 回收分支。
- 服务器删机执行器增加硬校验：只有资产已经完成关机状态后才允许进入删机执行。
- 后台计划手动执行入口从订单删机路径改为资产关机路径：`/api/admin/tasks/plans/server-assets/<asset_id>/shutdown/run/`。
- IP 删除手动入口修复单项开关判断，只看 `ip_delete_enabled`，不再误看 `shutdown_enabled`。
- 启动延迟保护只记录并跳过本轮真实资产关机、资产删机、迁移旧机、未附加 IP 删除，不再改写旧订单关机/删机/IP 回收时间。

## 结论

- 修改前不能确认严格按计划表执行，因为执行器仍存在订单驱动破坏性执行分支。
- 修改后可以确认：关机、删机、未附加 IP 删除都从资产计划查询层取 due 项，再由执行器按同一资产时间和开关做二次校验。
- 关机受 `cloud_server_shutdown_enabled()` 和资产 `shutdown_enabled` 控制。
- 删机受 `cloud_server_delete_enabled()` 和资产 `server_delete_enabled` 控制，并且必须先完成关机阶段。
- 未附加 IP 删除受 `cloud_ip_delete_enabled()` 和资产 `ip_delete_enabled` 控制，不受关机开关影响。
- 服务器不会自动补到期时间；只有未附加 IP 缺少到期时间时才按既有逻辑补 15 天释放时间。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/lifecycle.py cloud/lifecycle_execution.py cloud/lifecycle_plan_queries.py bot/api.py shop/admin_urls.py cloud/tests.py cloud/tests_task_center.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_serializes_shutdown_delete_and_ip_release_stages cloud.tests.CloudServerServicesTestCase.test_orphan_asset_plan_run_rejects_active_linked_order_asset cloud.tests.CloudServerServicesTestCase.test_dashboard_orphan_asset_plan_run_respects_computed_delete_time cloud.tests.CloudServerServicesTestCase.test_dashboard_shutdown_plan_run_respects_asset_suspend_at --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_dedupes_ip_delete_plan_by_public_ip cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_dedupes_same_ip_server_assets cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_dedupes_server_shutdown_by_public_ip cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_deduped_server_prefers_shutdown_complete_stage cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_plan_tail_page_keeps_exact_order --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_due_queues_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_ignores_shutdown_disabled_asset --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=1
git diff --check
```

结果：

- Django 系统检查通过。
- 相关文件编译通过。
- 生命周期执行链、计划页去重、IP 删除开关、due 队列单项开关、任务中心统计聚焦测试通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。

## 剩余风险

- 本轮没有执行真实云资源操作。
- 前端仓库需要同步调用新的资产关机计划执行接口，否则旧前端按钮如果仍调用订单计划执行路径会返回 404。
