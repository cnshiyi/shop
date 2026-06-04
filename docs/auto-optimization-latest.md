# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-04 21:43 CST
- 状态：按用户要求补齐资产开关语义，并新增显式服务器关机总开关。
- 本轮范围：云资产生命周期开关、后台生命周期计划视图、通知计划筛选、生命周期执行器、AWS 未附加固定 IP 同步释放保护。
- 本轮修复：新增 `cloud_server_shutdown_enabled` 配置，默认开启；该总开关只阻断到期真实关机。删除服务器仍由 `cloud_server_delete_enabled` 控制，释放固定 IP 仍由 `cloud_ip_delete_enabled` 控制。资产级 `CloudAsset.shutdown_enabled` 统一作为“资产自动生命周期开关”，关闭后阻断该资产自动关机、自动删机、订单固定 IP 回收、未附加 IP 释放和 AWS 同步释放。
- 本轮结论：全局关机总开关、删机总开关、删 IP 总开关、资产开关语义已拆开，不再互相误挡。

## 最近验证

- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile core/runtime_config.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/api_tasks.py bot/api.py cloud/api_assets.py cloud/management/commands/sync_aws_assets.py cloud/tests.py` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_server_shutdown_enabled_defaults_on cloud.tests.CloudServerServicesTestCase.test_global_shutdown_switch_blocks_scheduled_suspend cloud.tests.CloudServerServicesTestCase.test_due_orders_skip_suspend_when_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_due_orders_skip_order_static_ip_recycle_when_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_due_orders_global_shutdown_switch_does_not_block_delete_or_recycle cloud.tests.CloudServerServicesTestCase.test_lifecycle_suspend_execution_guard_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_replaced_order_delete_respects_asset_shutdown_switch cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_asset_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_respects_shutdown_disabled_asset --settings=shop.settings --verbosity=2` 通过。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。

## 剩余风险

- 本轮尚未执行新的真实 AWS 生命周期关机、删机或固定 IP 释放实测。
- SQLite 测试环境仍会打印不支持 `db_comment` / `db_table_comment` 的预期 warning。

## 下一步

- 继续按用户要求执行真机生命周期矩阵：到期关机、删机、固定 IP 删除、非执行时间窗口、关机总开关、删机总开关、删 IP 总开关、资产开关。
