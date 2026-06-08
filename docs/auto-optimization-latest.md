# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-09 01:08 CST
- 状态：已将 `CloudAsset.shutdown_enabled` 单资产关机计划开关默认改为关闭。
- 后端提交：本轮完成后提交，具体哈希以 `git log -1` 为准。
- 前端提交：无前端代码变更。

## 本轮背景

- 用户明确要求：单资产关机开关 `CloudAsset.shutdown_enabled` 默认关。
- 本轮只改默认值和服务端判断口径，不批量修改历史资产数据。
- 本轮没有执行真实云资源创建、真实关机、真实删机、真实支付、链上广播、生产发布或删除数据。

## 修复内容

- `cloud/models.py`
  - `CloudAsset.shutdown_enabled` 默认值由 `True` 改为 `False`。
- `cloud/migrations/0064_cloudasset_shutdown_default_off.py`
  - 新增字段默认值迁移，确保数据库层新资产默认关机计划关闭。
- `cloud/lifecycle.py`
  - `asset_shutdown_enabled(None)` 改为 `False`。
  - 资产关机辅助函数只认显式 `shutdown_enabled=True`。
  - 关机 due 查询改为只取 `shutdown_enabled=True` 的资产。
- `bot/api.py`、`cloud/api_assets.py`
  - 后台计划页和资产列表展示口径改为只有显式 True 才显示关机计划开启。
- `cloud/tests.py`
  - 新增“新建服务器资产默认关闭关机开关，显式打开后才进入关机 due 队列”回归测试。
  - 修正生命周期计划页旧测试数据，不再隐式依赖单资产关机默认开启。

## 结论

- 新建 `CloudAsset` 不传 `shutdown_enabled` 时默认关闭，不会进入关机计划执行队列。
- 只有显式设置 `shutdown_enabled=True` 的服务器资产，才可能在总开关开启、计划时间到达、执行窗口满足时进入关机 due 队列。
- 服务器删除开关和 IP 删除开关默认值本轮未改，仍保持原有口径。
- 云账号级 `CloudAccountConfig.shutdown_enabled` 不是本轮的单资产开关，未改。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations cloud --name cloudasset_shutdown_default_off
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/models.py cloud/lifecycle.py cloud/api_assets.py bot/api.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
rg -n "getattr\\([^,\\n]*shutdown_enabled[^,\\n]*, True\\)|exclude\\(shutdown_enabled=False\\)|enforce_schedule|_run_shutdown_order_sync" cloud bot core orders shop --glob '!*/migrations/*'
git diff --check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_asset_shutdown_enabled_defaults_off_and_blocks_due_queue cloud.tests.CloudServerServicesTestCase.test_lifecycle_due_queues_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_execution_has_no_schedule_bypass_flag_and_respects_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_serializes_shutdown_delete_and_ip_release_stages cloud.tests.CloudServerServicesTestCase.test_global_shutdown_switch_blocks_scheduled_suspend --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ignore_account_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_asset_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_route_linked_asset_delete_to_order_item cloud.tests.CloudServerServicesTestCase.test_lifecycle_plan_server_queryset_avoids_unattached_ip_subquery cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_dedupes_server_shutdown_by_public_ip --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_page_marks_overdue_delete_as_due_now cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_split_shutdown_before_server_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_sort_shutdown_items_by_delete_time cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_future_server_plan_item cloud.tests.CloudServerServicesTestCase.test_dashboard_shutdown_plan_run_respects_asset_suspend_at --settings=shop.settings --verbosity=1
```

结果：

- Django 系统检查通过。
- 相关文件编译通过。
- 迁移检查无遗漏。
- 旧默认开启 helper、旧 `exclude(shutdown_enabled=False)` 和旧计划绕过参数扫描无命中。
- 生命周期执行链 5 个聚焦测试通过。
- 生命周期计划页 11 个相邻回归测试通过。
- `git diff --check` 通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。

## 剩余风险

- 本轮没有执行真实云资源操作。
- 历史资产的 `shutdown_enabled` 值不会被迁移批量改写；如果要让存量资产也统一关闭，需要单独执行经过确认的数据变更。
