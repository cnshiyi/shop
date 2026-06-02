# 重构版本记录

## 2026-06-02 18:41 自动监工：开通日志 secret 尾部脱敏复查

### 范围

本轮继续监工 Shop Django 后端仓库，起始时工作树已有 `cloud/provisioning.py` 和 `cloud/tests.py` 的开通日志脱敏相关改动，最近提交为 `a329b0d 记录开通日志测试夹具补强`。本轮在不覆盖现有改动的前提下，继续复查云资产到期事实源、订单旧到期字段、旧计划快照表、退款旧入口、废弃 app 回流和生命周期任务认领冲突保护。

### 修复

- 将异步开通成功日志中的 `mtproxy_link` 预览从通用 `_mask_log_value()` 改为 `_mask_proxy_log_preview()`，避免整串裁剪时保留代理 `secret` 尾部。
- 新增 `test_proxy_log_preview_masks_secret_tail`，确认完整 secret 和末尾 12 位都不会出现在日志预览中，同时保留 `secret=***` 的可诊断占位。

### 复查结论

- `CloudServerOrder` 仍未恢复 `service_expires_at` 模型字段；生产代码未发现对旧订单到期列的危险 ORM 字段定义、查询或写入。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；订单接口中的 `service_expires_at` 只作为兼容 payload 字段，显式编辑写入资产事实字段。
- 未发现 `refund_to_balance`、`refund_balance`、`STATUS_REFUNDED`、`refunded` 旧状态、`CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan` 或旧生命周期 plan snapshot 函数回流。
- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 四个当前运行时 app；旧 `accounts/finance/mall/monitoring/dashboard_api/biz` 目录未恢复，`dashboard_api` 命中仅为 URL namespace。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/provisioning.py cloud/tests.py cloud/asset_expiry.py cloud/api_orders.py cloud/lifecycle.py cloud/services.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_provision_result_log_uses_cached_asset_expiry cloud.tests.CloudServerServicesTestCase.test_provision_result_log_masks_proxy_secrets cloud.tests.CloudServerServicesTestCase.test_proxy_log_preview_masks_secret_tail --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_lifecycle_asset_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_skips_when_lifecycle_task_claimed --noinput --verbosity 1
```

剩余风险：本轮未跑完整测试套件，也未覆盖真实 MySQL、真实 AWS Lightsail 或真实阿里云 API。

## 2026-06-02 18:34 自动监工：开通日志测试夹具补齐

### 范围

本轮继续监工 Shop Django 后端仓库，起始时工作树包含开通日志脱敏与到期缓存相关改动；运行期间当前分支已新增 `c1fcb9f 修复开通结果异步日志回归`、`4b36bb6 补充开通结果日志回归测试`、`ee43ddf 记录实机开通删除回归修复` 和 `fd97302 补强开通日志测试数据`。本轮在当前 HEAD 上继续复查云资产到期事实源、订单旧到期字段、旧计划快照表、退款旧入口、废弃 app 回流和生命周期任务认领冲突保护。

### 修复

- 补齐两条开通结果日志回归测试订单的必填 `plan=self.plan`，避免 SQLite 聚焦测试因 `cloud_order.plan_id` 非空约束失败。
- 保留开通结果日志读取 `_asset_expires_at` 缓存的行为，不在异步开通流程返回后重新查询 `CloudAsset` 到期时间。
- 保留代理链接、`secret` 和 SOCKS5 凭据的日志脱敏行为。

### 复查结论

- `CloudServerOrder` 仍未恢复 `service_expires_at` 模型字段；生产代码未发现对旧订单到期列的危险 ORM 字段定义、查询或写入。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；订单接口中的 `service_expires_at` 只作为兼容 payload 字段，显式编辑写入资产事实字段。
- 未发现 `refund_to_balance`、`refund_balance`、`STATUS_REFUNDED`、`refunded` 旧状态、`CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan` 或旧生命周期 plan snapshot 函数回流。
- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 四个当前运行时 app；旧 `accounts/finance/mall/monitoring/dashboard_api/biz` 目录未恢复，`dashboard_api` 命中仅为 URL namespace。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/provisioning.py cloud/tests.py cloud/services.py cloud/api_orders.py cloud/asset_expiry.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_provision_result_log_uses_cached_asset_expiry cloud.tests.CloudServerServicesTestCase.test_provision_result_log_masks_proxy_secrets --noinput --verbosity 2
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_lifecycle_asset_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_skips_when_lifecycle_task_claimed --noinput --verbosity 1
git diff --check
```

说明：新增日志测试首次因测试订单缺少 `total_amount`、`user`、`plan` 三个必填字段失败；已在 `fd97302` 和本轮补丁中补齐后重跑通过。

剩余风险：本轮未跑完整测试套件，也未覆盖真实 MySQL 和真实云厂商 API。

## 2026-06-02 18:22 自动监工：到期事实与生命周期复查通过

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `ec33fc4 记录生命周期回归复查通过`。重点复查云资产到期事实源、订单旧到期字段、旧计划快照表、退款旧入口、废弃 app 回流、云同步保留人工到期时间和生命周期任务认领冲突保护。

### 复查结论

- 本轮未修改生产代码；未发现需要最小修复的新增缺陷。
- `CloudServerOrder` 仍未恢复 `service_expires_at` 模型字段；生产代码未发现对旧订单到期列的危险 ORM 字段定义、查询或写入。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；订单详情接口中的 `service_expires_at` 只是前端兼容 payload 字段，显式编辑会写入资产事实字段并同步兼容 server 记录。
- 阿里云同步回归测试确认云端过期时间不会覆盖已有 `CloudAsset.actual_expires_at` 手工值。
- 未发现 `refund_to_balance`、`refund_balance`、`STATUS_REFUNDED`、`refunded` 旧状态、旧退款函数、`CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan` 或旧生命周期 plan snapshot 函数回流。
- `CloudLifecyclePlanNote` 仍只是删除计划备注表，不是旧派生计划快照表恢复；`_refresh_dashboard_plan_snapshots` 是当前仪表盘缓存刷新入口，不承载旧计划快照表。
- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 四个当前运行时 app；旧 `accounts/finance/mall/monitoring/dashboard_api/biz` 目录未恢复，`dashboard_api` 命中仅为 URL namespace。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/models.py cloud/lifecycle.py cloud/dashboard_snapshots.py cloud/api_asset_edit.py cloud/api_orders.py cloud/sync_jobs.py orders/payment_scanner.py orders/services.py bot/api.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_for_aws_creates_single_replace_order_for_expiry_and_price cloud.tests.CloudServerServicesTestCase.test_refresh_lifecycle_plans_command_builds_lifecycle_plan_view cloud.tests.CloudServerServicesTestCase.test_refresh_lifecycle_plan_view_api_builds_lifecycle_plan_view cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_update_unattached_ip_release_time_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_lifecycle_asset_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_skips_when_lifecycle_task_claimed --verbosity 2
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields --verbosity 2
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry --verbosity 2
```

说明：`makemigrations --check --dry-run` 在沙箱内因无法连接本地 MySQL 打印一致性历史检查警告，但命令退出成功且显示 `No changes detected`。首次聚焦测试命令曾误把 `CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields` 写到 `CloudServerServicesTestCase` 下，导致测试选择器错误；已用正确测试类单独重跑并通过。

剩余风险：本轮未跑完整测试套件，也未覆盖真实 MySQL、真实 AWS Lightsail 或真实阿里云 API；继续依赖 SQLite 聚焦测试、静态扫描和后续自动监工增量复查。

## 2026-06-02 18:11 自动监工：生命周期回归复查通过

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `3c90d5e 记录生命周期计划视图命名收口`。重点复查云资产到期事实源、订单旧到期字段、旧计划快照表、退款旧入口、废弃 app 回流和生命周期任务认领冲突保护。

### 复查结论

- 本轮未修改生产代码；未发现需要最小修复的新增缺陷。
- `CloudServerOrder` 仍未恢复 `service_expires_at` 模型字段；运行代码未发现对旧订单到期列的危险 ORM 查询、写入或创建。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；订单接口中的 `service_expires_at` 仅保留为兼容 payload 字段，显式编辑会写入资产事实字段。
- 未发现 `normalize_service_expiry`、`service_expired_at`、`CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan`、退款旧函数名或 `refunded` 旧状态回流。
- `CloudLifecyclePlanNote` 仍只是删除计划备注表，不是旧派生计划快照表恢复。
- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 四个当前运行时 app；`dashboard_api` 命中仍只是 URL namespace 和 `core.dashboard_api` helper。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py bot/handlers.py cloud/asset_expiry.py cloud/dashboard_snapshots.py cloud/services.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/lifecycle_tasks.py cloud/api.py cloud/api_assets.py cloud/api_asset_edit.py cloud/api_orders.py cloud/api_tasks.py cloud/api_sync.py cloud/api_monitors.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py orders/services.py orders/payment_scanner.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_lifecycle_asset_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_skips_when_lifecycle_task_claimed --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-monitor-plan-view-20260602-run2.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_refresh_lifecycle_plans_command_builds_lifecycle_plan_view cloud.tests.CloudServerServicesTestCase.test_refresh_lifecycle_plan_view_api_builds_lifecycle_plan_view cloud.tests.CloudServerServicesTestCase.test_refresh_notice_plans_command_builds_notice_plan_view cloud.tests.CloudServerServicesTestCase.test_refresh_notice_plan_view_api_builds_notice_plan_view cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_uses_notice_plan_view --noinput --verbosity 1
```

剩余风险：本轮未跑完整测试套件，也未覆盖真实 MySQL 和真实云厂商 API；继续依赖 SQLite 聚焦测试、静态扫描和后续自动监工增量复查。

## 2026-06-02 18:03 自动监工：生命周期计划视图命名收口

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `5e5ab16 记录快照聚合层清理复查`。重点复查云资产到期事实源、订单旧到期字段、旧计划快照表、退款旧入口和废弃 app 回流。

### 修复

- 将内部辅助函数 `_refresh_lifecycle_plan_snapshot` 改名为 `_refresh_lifecycle_plan_view`。
- 同步后台资产编辑接口的导入和调用；实际行为仍是重建实时生命周期计划 bundle，不写入旧 `cloud_lifecycle_plan`、`cloud_notice_plan` 或 `cloud_auto_renew_plan` 派生快照表。
- 保留 `CloudAssetDashboardSnapshot` 作为代理列表分页、搜索和风险统计的看板查询快照；它不承载资产到期事实，资产到期仍只读写 `CloudAsset.actual_expires_at`。

### 复查结论

- 未发现 `CloudServerOrder.service_expires_at` 数据库列危险 ORM 查询、写入或创建；订单接口中的 `service_expires_at` 仍是兼容 payload 字段，显式编辑会写入 `CloudAsset.actual_expires_at`。
- 未发现 `normalize_service_expiry`、`service_expired_at`、`CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan`、退款旧函数名或退款旧入口回流。
- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 四个当前运行时 app；`dashboard_api` 命中仍只是 `core.dashboard_api` helper 和 URL namespace，不是废弃 app 恢复。
- 测试代码仅剩一处 `service_expires_at=`，为 `test_order_rejects_removed_service_expiry_field` 的负向测试，用于确认订单旧字段不能再写入。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/dashboard_snapshots.py cloud/api_asset_edit.py cloud/services.py cloud/api.py cloud/api_assets.py cloud/api_tasks.py bot/api.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_lifecycle_asset_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_skips_when_lifecycle_task_claimed cloud.tests.CloudServerServicesTestCase.test_send_logged_cloud_notice_deduplicates_same_event_and_order cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_allows_new_expiry_cycle --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-monitor-plan-view-20260602.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_refresh_lifecycle_plans_command_builds_lifecycle_plan_view cloud.tests.CloudServerServicesTestCase.test_refresh_lifecycle_plan_view_api_builds_lifecycle_plan_view cloud.tests.CloudServerServicesTestCase.test_refresh_notice_plans_command_builds_notice_plan_view cloud.tests.CloudServerServicesTestCase.test_refresh_notice_plan_view_api_builds_notice_plan_view cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_uses_notice_plan_view --noinput --verbosity 1
git diff --check
```

剩余风险：本轮未跑完整测试套件；默认 `uv` 缓存目录仍受沙箱限制，验证继续显式使用 `/private/tmp/uv-cache-shop`。

## 2026-06-02 17:51 自动监工：快照聚合层清理复查

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `13ee1ce 记录快照刷新聚合层清理`。重点复查快照刷新不再依赖 `cloud.api` 聚合层，以及云资产生命周期重构中旧到期字段、旧计划快照、退款旧入口和废弃 app 是否回流。

### 复查结论

- `cloud/dashboard_snapshots.py` 仍通过真实模块局部导入快照刷新、自动续费计划和通知计划构建逻辑，未发现生产代码重新依赖 `cloud.api` 聚合层。
- 未发现 `CloudServerOrder.service_expires_at` 数据库列危险查询、写入或创建；订单接口中的 `service_expires_at` 仍是兼容展示字段，资产到期事实继续由 `CloudAsset.actual_expires_at` 承载。
- 未发现 `normalize_service_expiry`、`service_expired_at`、旧计划快照模型、退款旧函数名或退款旧入口回流。
- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 四个当前运行时 app；废弃 app 未恢复。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py bot/handlers.py cloud/services.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/lifecycle_tasks.py cloud/api.py cloud/api_assets.py cloud/api_asset_edit.py cloud/api_orders.py cloud/api_tasks.py cloud/api_sync.py cloud/api_monitors.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py orders/services.py orders/payment_scanner.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/dashboard_snapshots.py cloud/api_asset_snapshots.py cloud/api_tasks.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_defers_snapshot_refresh cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_tasks_executes_due_retry_and_fallback_queue cloud.tests.CloudServerServicesTestCase.test_refresh_notice_plan_view_api_builds_notice_plan_view --noinput --verbosity 1
```

剩余风险：本轮未跑完整测试套件；默认 MySQL 未覆盖，继续使用 SQLite 聚焦测试和静态扫描兜底。

## 2026-06-02 17:18 自动监工：生产历史匹配移除测试标记

### 范围

本轮继续用 `codex-cli` 和本地命令复查最新 `HEAD`。CLI 只读复查发现生产历史分类条件中仍有 `真机测试：未附加IP删除` 文本，属于测试/演练标记混入生产匹配逻辑。

### 修复

- 从未附加固定 IP 删除历史分类条件中移除 `真机测试：未附加IP删除`。
- 保留通用 `未附加IP`、`未附加固定IP` 和真实释放/云端不存在等生产语义匹配；被移除文本已经被通用条件覆盖，不影响现有历史归类。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_manual_unattached_ip_delete_writes_log_and_history_item cloud.tests.CloudServerServicesTestCase.test_legacy_unattached_ip_delete_log_without_known_note_shows_history cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_ip_delete_history_item --noinput --verbosity 1
```

- 旧订单到期字段、旧计划快照模型、旧退款符号和废弃 app 回流窄扫描无新增危险命中。
- 生产代码中已无 `真机测试：未附加IP删除` 或其他 `真机测试` 历史匹配标记。

`codex-cli` 只读复查 session：`019e879e-8aca-71e1-bad5-fb8fdd9fde3b`。

## 2026-06-02 17:11 自动监工：计划刷新接口命名收口

### 范围

本轮继续监工云资产生命周期重构，起始工作树已有计划刷新接口命名调整：生命周期计划和通知计划的后台刷新函数从 `table` 命名收口为 `view` 命名，避免继续暗示旧计划快照表恢复。

### 复查结论

- `refresh_lifecycle_plan_table` 和 `refresh_notice_plan_table` 在运行代码中已改为 `refresh_lifecycle_plan_view`、`refresh_notice_plan_view`；URL 和 API 聚合导出同步更新。
- 相关测试名称同步改为“构建计划视图”，不再使用“填充计划表/快照表”语义。
- 未发现 `CloudServerOrder.service_expires_at` 数据库列危险 ORM、`normalize_service_expiry`、`service_expired_at`、旧计划快照模型或退款函数名回流。
- `CloudAsset.actual_expires_at` 仍是唯一结构化服务到期事实；`service_expires_at` 仍仅作为兼容 payload 字段、日志标签或测试断言中的视图字段名存在。
- 废弃 app 未回到 `INSTALLED_APPS` 或运行时导入；`dashboard_api` 仍只是现有 URL namespace / helper 命名。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py cloud/api.py cloud/api_tasks.py shop/dashboard_urls.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-monitor-plan-view.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_refresh_lifecycle_plans_command_builds_lifecycle_plan_view cloud.tests.CloudServerServicesTestCase.test_refresh_lifecycle_plan_view_api_builds_lifecycle_plan_view cloud.tests.CloudServerServicesTestCase.test_refresh_notice_plans_command_builds_notice_plan_view cloud.tests.CloudServerServicesTestCase.test_refresh_notice_plan_view_api_builds_notice_plan_view cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_uses_notice_plan_view --noinput --verbosity 1
```

## 2026-06-02 16:52 自动监工：固定 IP 回收回流复查

### 范围

本轮继续监工云资产生命周期重构后的固定 IP 回收、旧订单到期字段、旧计划快照表、退款入口和废弃 app 回流。运行中确认当前分支已推进到 `64c3180 补充固定IP回收测试和旧字段收口记录`，固定 IP 回收候选和执行入口已补齐资产级/云账号级关机计划开关保护。

### 复查结论

- 当前运行时代码仍以 `CloudAsset.actual_expires_at` 作为唯一结构化服务到期事实；订单接口里的 `service_expires_at` 仍只是兼容 payload 字段。
- 未发现 `CloudServerOrder.service_expires_at` 数据库列、`normalize_service_expiry`、`service_expired_at` 或旧计划快照模型回流。
- 未发现退款函数名、退款入口或 `refunded` 运行时状态筛选回流。
- `cloud/tests.py` 仅剩 1 处 `service_expires_at=`，为旧字段删除的负向测试；`.service_expires_at` 测试命中为 0。
- 废弃 app 未回到 `INSTALLED_APPS` 或运行时导入；`dashboard_api` 仅作为现有 URL namespace / helper 命名保留。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python -m py_compile cloud/lifecycle.py cloud/lifecycle_execution.py cloud/services.py cloud/api_asset_edit.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_due_orders_skip_order_static_ip_recycle_when_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry --noinput --verbosity 1
git diff --check
```

因默认 `uv` 缓存目录受沙箱限制，后续命令继续显式使用 `UV_CACHE_DIR=/private/tmp/uv-cache-shop`。

## 2026-06-02 16:36 自动监工：删除计划关机开关与资产编辑收口

### 范围

本轮按最新要求调整关机计划入口：不在代理列表每行新增开关，改为放在删除计划表和代理详情页，并且两处都走同一个资产级字段 `CloudAsset.shutdown_enabled` 和同一个后台资产编辑接口。同步处理后端遗留脏改动，把旧 `service_expires_at` 测试构造继续收口到资产到期事实。

### 修复

- 后台资产编辑接口在更新公网 IP 时，旧 IP 来源改为依次读取资产当前 IP、资产历史 IP、订单当前 IP，避免 `Server` 兼容层预同步后订单 `previous_public_ip` 无法回填。
- 删除计划和代理详情页使用同一套资产关机计划开关，后端 payload 均返回 `shutdown_enabled`，执行逻辑和计划候选查询均识别资产级关闭状态。
- IP 回收提醒、回收候选和真实固定 IP 释放执行也识别资产级/云账号级关机计划开关，避免关闭关机计划后仍进入回收队列或真实释放。
- 测试继续移除多处对已删除订单到期字段的构造，改为使用 `CloudAsset.actual_expires_at`。
- 调整旧 `Server` 独立表语义下的测试断言：当前 `Server.objects` 已是 `CloudAsset` 兼容入口，删除兼容服务器记录就是删除对应资产记录，不再假设存在另一张运行时服务器表。
- 继续收口日报、任务概览、自动续费重试、资产价格/到期编辑、续费价格读取、资产详情点击路径、自动续费执行、阿里云同步和恢复订单相关测试中的旧订单到期字段写法；当前 `cloud/tests.py` 剩余 `service_expires_at=` 命中仅保留 1 处负向测试，`.service_expires_at` 测试命中已清零。

### 验证

本地已通过：

```bash
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_dashboard_asset_ip_update_uses_asset_old_ip_when_server_was_pre_synced cloud.tests.CloudServerServicesTestCase.test_due_orders_skip_suspend_when_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_asset_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_delete_cloud_asset_only_removes_asset_record cloud.tests.CloudServerServicesTestCase.test_delete_cloud_asset_also_removes_residual_server_record cloud.tests.CloudServerServicesTestCase.test_reconcile_cloud_assets_skips_deleted_server_residual cloud.tests.CloudServerServicesTestCase.test_delete_server_only_removes_server_record cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_instance_requires_five_passes_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_check_uses_previous_public_ip_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_missing_instance_requires_five_passes_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_keeps_runtime_running_when_order_is_suspended cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_revives_deleted_order_when_instance_exists --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_daily_expiry_summary_uses_real_cloud_status_and_target_config cloud.tests.CloudServerServicesTestCase.test_tasks_overview_exposes_click_paths_for_entry_and_order_number cloud.tests.CloudServerServicesTestCase.test_auto_renew_retry_task_waits_for_recharge_then_retries cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_price_restores_auto_renew_pending_state cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_cloud_asset_detail_exposes_related_order_click_path cloud.tests.CloudServerServicesTestCase.test_cloud_asset_detail_exposes_history_orders_with_click_paths --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_renewal_balance_payment_uses_latest_proxy_price --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_auto_renew_task_detail_includes_due_retry_and_fallback_items cloud.tests.CloudServerServicesTestCase.test_auto_renew_detail_keeps_valid_order_without_asset cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_tasks_executes_due_retry_and_fallback_queue cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_order_executes_single_order cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_refreshes_unattached_ip_delete_plan cloud.tests.CloudServerServicesTestCase.test_order_primary_records_prefer_ip_over_stale_names cloud.tests.CloudServerServicesTestCase.test_admin_start_restores_suspended_order_to_completed cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_order_update_recalculates_lifecycle_on_expiry_change cloud.tests.CloudServerServicesTestCase.test_aliyun_order_is_not_enqueued_for_shutdown_delete_plan cloud.tests.CloudServerServicesTestCase.test_manual_aliyun_delete_plan_is_blocked_without_local_delete cloud.tests.CloudServerServicesTestCase.test_failed_aliyun_order_is_not_enqueued_for_fallback_delete cloud.tests.CloudServerServicesTestCase.test_unattached_static_ip_is_not_auto_renewed --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_updates_retained_asset_after_renewal_recovery --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_due_orders_skip_order_static_ip_recycle_when_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_order_primary_records_prefer_ip_over_stale_names cloud.tests.CloudServerServicesTestCase.test_admin_start_restores_suspended_order_to_completed --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py check
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py makemigrations --check --dry-run
git diff --check
rg -n "service_expires_at__|(filter|exclude|update|order_by|values|values_list)\([^\n)]*service_expires_at" --glob '!cloud/migrations/**' --glob '!docs/**' --glob '!cloud/tests.py' --glob '!bot/tests.py' --glob '!orders/tests.py' .
```

前端 `@vben/web-antd` 类型检查未能运行，原因是当前终端为 Node `v26.0.0`、pnpm `9.15.9`，而前端仓库要求 Node `^20.19.0 || ^22.18.0 || ^24.0.0` 和 pnpm `>=10.0.0`。

## 2026-06-02 16:29 自动监工：资产关机开关和旧到期字段继续收口

### 范围

本轮继续监工 Shop Django 后端云资产生命周期重构状态。起始最近提交为 `6d1bd09 收口未绑定资产续费测试旧字段`，工作树已有 `cloud/tests.py` 未提交改动；运行期间同时发现资产级关机计划开关相关改动进入工作树，因此一并做导入错误修复和聚焦验证。

### 修复

- 继续收口 `cloud/tests.py` 中一批旧 `service_expires_at` 测试写法，不再向 `CloudServerOrder.objects.create()` 传已移除字段，改用 `CloudAsset.actual_expires_at` 或 `order_asset_expiry(order)`。
- 修复后台资产编辑接口从 `cloud.models` 导入 `Server` 的错误，改为从 `cloud.server_records` 兼容入口导入，避免 `manage.py check` 在 URL 导入阶段失败。
- 接入并验证 `CloudAsset.shutdown_enabled` 资产级关机计划开关：生命周期候选查询、计划 payload、执行保护、资产风险原因和 AWS 未附加固定 IP 释放保护均识别资产级关闭状态。
- 修正相关测试 patch 目标，删除执行逻辑已从 `cloud.lifecycle` 动态导入安全窗口函数，测试不再 patch 已不存在的 `bot.api._is_cloud_delete_safe_time`。

### 复查结论

- 当前运行时代码未发现恢复 `CloudServerOrder.service_expires_at` 数据库列写入或查询；剩余 `service_expires_at` 命中为兼容 API 字段名、日志标签或资产视图别名。
- 未发现 `normalize_service_expiry`、`service_expired_at`、旧计划快照模型、退款函数名/入口或废弃 app 运行时回流。
- `cloud/tests.py` 中旧 `service_expires_at=` / `.service_expires_at` 测试命中降至 53 处，仍需后续分批收口。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
DB_ENGINE=sqlite SQLITE_NAME=:memory: UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/tests.py cloud/api_asset_edit.py cloud/lifecycle.py cloud/lifecycle_execution.py bot/api.py cloud/api_assets.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_server_ip_query_requires_owner_identity cloud.tests.CloudServerServicesTestCase.test_cloud_server_public_renewal_allows_stranger_payment_entry cloud.tests.CloudServerServicesTestCase.test_retained_deleted_asset_renewal_plans_are_available_by_asset_button cloud.tests.CloudServerServicesTestCase.test_retained_deleted_asset_renewal_plans_allow_same_group_visibility cloud.tests.CloudServerServicesTestCase.test_ip_query_displays_matched_asset_ip_not_order_ip cloud.tests.CloudServerServicesTestCase.test_ip_query_displays_matched_previous_ip_not_order_ip cloud.tests.CloudServerServicesTestCase.test_cloud_server_ip_change_requires_owner_identity cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_manual_order_delete_enters_lifecycle_success_history cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_future_server_plan_item --keepdb --noinput --verbosity 1
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_manual_order_delete_bypasses_schedule_limits cloud.tests.CloudServerServicesTestCase.test_manual_orphan_asset_delete_bypasses_schedule_limits cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_respects_shutdown_disabled_account cloud.tests.CloudServerServicesTestCase.test_due_orders_skip_suspend_when_account_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_lifecycle_suspend_execution_guard_respects_account_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_shutdown_disabled_account --keepdb --noinput --verbosity 1
git diff --check
```

默认 MySQL 连接在沙箱中仍不能访问 `127.0.0.1:3306`；使用 SQLite 迁移检查确认无待生成迁移。

## 2026-06-02 16:14 自动监工：未绑定资产续费测试旧字段收口

### 范围

本轮继续监工 Shop Django 后端云资产生命周期重构状态。起始工作树干净，最近提交为 `b1f281d 记录codex生命周期只读复查`；本轮聚焦云资产到期唯一事实源、订单旧到期字段测试回流、旧计划快照表回流、退款入口回流和废弃 app 误用。

### 修复

- 修复 `cloud/tests.py` 中 8 条会真实失败或继续误导到期事实源的测试：
  - `test_aws_notice_schedule_does_not_override_manual_order_expiry`
  - `test_prepare_unbound_asset_renewal_creates_pending_payment_order`
  - `test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery`
  - `test_unbound_asset_renewal_wallet_payment_repairs_completed_unpaid_state`
  - `test_completed_asset_recovery_order_renews_without_reprovisioning`
  - `test_unbound_asset_renewal_chain_payment_marks_paid_for_recovery`
  - `test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing`
  - `test_proxy_asset_ip_query_exposes_manual_expiry_for_admin_and_user`
- 这些用例不再向 `CloudServerOrder.objects.create()` 传入已移除的 `service_expires_at`，改为显式创建或读取关联 `CloudAsset.actual_expires_at`。
- 相关断言改为使用 `order_asset_expiry(order)` 或视图对象的 `actual_expires_at`，避免把订单表旧字段当成事实源。
- 本轮未修改生产代码；运行时代码仍通过 `CloudAsset.actual_expires_at` 和 `order_asset_expiry()` 读取服务到期事实。

### 复查结论

- 当前运行时代码未发现对已移除 `CloudServerOrder.service_expires_at` 数据库列的危险 ORM 查询。
- 未恢复 `normalize_service_expiry`、`service_expired_at`、旧计划快照模型 `CloudLifecyclePlan` / `CloudNoticePlan` / `CloudAutoRenewPlan`。
- 未发现退款函数名、退款入口或 `refunded` 运行时状态筛选回流。
- `CloudLifecyclePlanNote` 仍只是现有备注模型，不是旧计划快照表恢复。
- `cloud/tests.py` 仍有旧 `service_expires_at=` 测试构造约 75 处，后续应继续分批收口到 `CloudAsset.actual_expires_at` 或 `order_asset_expiry(order)`。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/tests.py cloud/models.py cloud/asset_expiry.py cloud/lifecycle.py cloud/services.py
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_aws_notice_schedule_does_not_override_manual_order_expiry cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_repairs_completed_unpaid_state cloud.tests.CloudServerServicesTestCase.test_completed_asset_recovery_order_renews_without_reprovisioning cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_chain_payment_marks_paid_for_recovery cloud.tests.CloudServerServicesTestCase.test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing cloud.tests.CloudServerServicesTestCase.test_proxy_asset_ip_query_exposes_manual_expiry_for_admin_and_user --noinput --verbosity 1
rg -n "service_expires_at__|(filter|exclude|update|order_by|values|values_list)\([^\n)]*service_expires_at|CloudServerOrder\([^\n)]*service_expires_at|CloudServerOrder\.objects\.create\([^\n)]*service_expires_at|CloudServerOrder\.objects\.filter\([^\n)]*service_expires_at" --glob '!cloud/migrations/**' --glob '!docs/**' --glob '!CHANGELOG.md' --glob '!cloud/tests.py' --glob '!bot/tests.py' --glob '!orders/tests.py' .
rg -n "class Cloud(LifecyclePlan|NoticePlan|AutoRenewPlan)|service_expires_at\s*=\s*models|service_expired_at|normalize_service_expiry|refunded|refund_" cloud orders bot core shop --glob '!**/migrations/**'
git diff --check
```

`makemigrations --check --dry-run` 仍因当前沙箱禁止连接默认 MySQL `127.0.0.1:3306` 打印迁移历史一致性检查警告，但最终报告 `No changes detected`。

## 2026-06-02 16:07 自动监工：codex-cli 生命周期只读复查

### 范围

本轮继续监工 Shop Django 后端云资产生命周期重构状态。起始工作树干净，最近提交为 `39d5769 修复生命周期计划刷新测试旧字段`；自动化 `shop` 仍为 ACTIVE，每 10 分钟运行一次，模型为 `gpt-5.5`。终端版 codex 为 `codex-cli 0.135.0-alpha.1`，本轮用只读沙箱调用 codex-cli 复查，没有让 CLI 修改文件或提交。

### 复查结论

- codex-cli 未发现真实运行时 bug。
- 当前运行时代码仍以 `CloudAsset.actual_expires_at` 作为唯一结构化到期事实；订单接口里的 `service_expires_at` 仅作为兼容 payload 字段，取值来自 `order_asset_expiry()` 或资产事实字段。
- 删除计划、通知计划、代理列表仍读同一到期事实：订单路径经 `order_asset_expiry(order)`，资产路径直接读 `CloudAsset.actual_expires_at`。
- `CloudLifecycleTask` 的生命周期任务 key 包含任务类型、来源和计划时间；`CloudNoticeTask` 的通知 key 经 batch id 纳入订单当前到期周期，未发现同一周期重复执行或续费后跨周期污染的运行时问题。
- 启动延迟保护已覆盖关机、删机、迁移旧机删除、无订单资产删除、订单固定 IP 回收、未附加固定 IP 删除，并会清空本轮待执行列表，避免启动检查继续执行破坏性动作。
- 未恢复 `CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan` 旧计划快照模型，未恢复 `CloudServerOrder.service_expires_at` 数据库字段或危险 ORM 查询，未恢复退款函数名、退款入口或 `refunded` 状态。

### 剩余风险

- 测试文件仍有大量旧 `service_expires_at` 构造和断言，属于测试债；自动化已在 `39d5769` 收口其中一处计划刷新测试，后续应继续按用例改为 `CloudAsset.actual_expires_at` 或 `order_asset_expiry(order)`。
- 本轮没有运行完整测试套件，只做了聚焦复查和基础验证。

### 验证

本地已通过：

```bash
/Applications/Codex.app/Contents/Resources/codex --version
/Applications/Codex.app/Contents/Resources/codex exec --cd /Users/a399/Desktop/data/shop --sandbox read-only -m gpt-5.5 -o /tmp/shop-codex-review-latest.txt '<只读生命周期复查提示>'
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py check
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "\bnormalize_service_expiry\b|service_expired_at|class (CloudLifecyclePlan|CloudNoticePlan|CloudAutoRenewPlan)\b|CloudLifecyclePlan\.|CloudNoticePlan\.|CloudAutoRenewPlan\.|refunded|refund|退款" --glob '!cloud/migrations/**' --glob '!docs/**' .
rg -n "service_expires_at__|(filter|exclude|update|order_by|values|values_list)\([^\n)]*service_expires_at" --glob '!cloud/migrations/**' --glob '!docs/**' --glob '!cloud/tests.py' --glob '!bot/tests.py' --glob '!orders/tests.py' .
git diff --check
```

## 2026-06-02 自动监工：计划刷新测试旧字段回流修复

### 范围

本轮继续监工云资产生命周期重构后的唯一到期事实源、旧字段回流、旧计划快照表回流、退款入口回流和废弃 app 误用。起始工作树干净，最近提交为 `850590e 补充生命周期回流复查记录`。

### 修复

- 修复 `test_refresh_lifecycle_plans_command_populates_cloud_lifecycle_plan` 的测试数据回流：不再向 `CloudServerOrder.objects.create()` 传入已移除的 `service_expires_at`，改为在测试中保存 `expires_at` 变量并写入关联 `CloudAsset.actual_expires_at`。
- 本轮未修改生产代码；计划刷新命令和接口仍从实时资产事实源生成结果，不恢复旧计划快照表。

### 复查结论

- 当前运行时代码仍以 `CloudAsset.actual_expires_at` 作为唯一结构化服务到期事实。
- 未发现 `normalize_service_expiry` 旧符号、`service_expired_at` 拼写残留，未发现运行代码对已移除 `CloudServerOrder.service_expires_at` 数据库列做危险 ORM 查询。
- 未恢复 `CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan` 旧计划快照模型；`refresh_lifecycle_plans` 和 `refresh_notice_plans` 仍生成实时 bundle。
- 未发现退款函数名、退款入口或 `refunded` 运行时状态筛选回流。
- 废弃 app 未重新加入 `INSTALLED_APPS`；相关命中仍为现有权限码、路由命名空间、helper 名称或历史迁移上下文。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/asset_expiry.py cloud/lifecycle_schedule.py cloud/lifecycle.py cloud/lifecycle_tasks.py cloud/services.py cloud/api_orders.py cloud/api_tasks.py cloud/api_asset_edit.py cloud/management/commands/refresh_lifecycle_plans.py cloud/management/commands/refresh_notice_plans.py
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-monitor-notice-cycle.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_allows_new_expiry_cycle cloud.tests.CloudServerServicesTestCase.test_send_logged_cloud_notice_deduplicates_same_event_and_order cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_prefers_bound_group_and_skips_private cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_falls_back_private_when_group_fails --noinput
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-monitor-startup-defer.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_startup_defer_reschedules_static_ip_cleanup_without_release cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_startup_defer_blocks_order_static_ip_release cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_startup_defer_blocks_unattached_static_ip_release --noinput
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-monitor-plan-refresh.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_refresh_lifecycle_plans_command_populates_cloud_lifecycle_plan cloud.tests.CloudServerServicesTestCase.test_refresh_lifecycle_plan_table_api_populates_cloud_lifecycle_plan cloud.tests.CloudServerServicesTestCase.test_refresh_notice_plans_command_populates_cloud_notice_plan cloud.tests.CloudServerServicesTestCase.test_refresh_notice_plan_table_api_populates_cloud_notice_plan --noinput
git diff --check
```

默认 MySQL 连接仍被当前沙箱禁止访问 `127.0.0.1:3306`，`makemigrations --check --dry-run` 因此打印迁移历史一致性检查警告，但最终报告 `No changes detected`。启动延迟聚焦测试仍会打印 SQLite 快照刷新锁等待日志，断言通过，属于现有测试环境噪声。

## 2026-06-02 自动监工：生命周期回流复查补验

### 范围

本轮继续监工云资产生命周期重构后的唯一到期事实源、旧字段回流、旧计划快照表回流、退款入口回流和废弃 app 误用。起始工作树只有 `docs/refactor-version-record.md` 未提交改动，最近提交为 `df9f5f5 修正生命周期监工验证记录`；本轮保留已有版本记录补充，只追加本轮复查结果。

### 复查结论

- 未修改生产代码；当前运行时代码仍以 `CloudAsset.actual_expires_at` 作为唯一结构化服务到期事实。
- 未发现 `normalize_service_expiry` 旧符号、`service_expired_at` 拼写残留，未发现对已移除 `CloudServerOrder.service_expires_at` 数据库列的危险 ORM 查询。
- 未恢复 `CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan` 旧计划快照模型；`refresh_lifecycle_plans` 和 `refresh_notice_plans` 仍只生成实时 bundle，`CloudLifecycleTask` / `CloudNoticeTask` 的 `basis_actual_expires_at` 仍只作审计字段。
- 未发现退款函数名、退款入口或 `refunded` 运行时状态筛选回流。
- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 四个运行 app；`dashboard_api`、`finance`、`monitoring` 等命中仅为现有权限码、路由命名空间、helper 名称或测试/历史迁移上下文。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/asset_expiry.py cloud/lifecycle_schedule.py cloud/lifecycle.py cloud/services.py cloud/api_orders.py cloud/api_tasks.py cloud/api_asset_edit.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/refresh_lifecycle_plans.py cloud/management/commands/refresh_notice_plans.py
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_allows_new_expiry_cycle cloud.tests.CloudServerServicesTestCase.test_send_logged_cloud_notice_deduplicates_same_event_and_order cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_prefers_bound_group_and_skips_private cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_falls_back_private_when_group_fails --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_startup_defer_reschedules_static_ip_cleanup_without_release cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_startup_defer_blocks_order_static_ip_release cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_startup_defer_blocks_unattached_static_ip_release --noinput --verbosity 1
! rg -n "\bnormalize_service_expiry\b|service_expired_at|class (CloudLifecyclePlan|CloudNoticePlan|CloudAutoRenewPlan)\b|CloudLifecyclePlan\.|CloudNoticePlan\.|CloudAutoRenewPlan\.|refunded|refund|退款" --glob '!cloud/migrations/**' --glob '!docs/**' .
! rg -n "service_expires_at__|(filter|exclude|update|order_by|values|values_list)\([^\n)]*service_expires_at" --glob '!cloud/migrations/**' --glob '!docs/**' --glob '!cloud/tests.py' --glob '!bot/tests.py' --glob '!orders/tests.py' .
```

两组聚焦测试均通过；启动延迟保护测试会打印现有生命周期快照刷新日志，属于已知测试输出，不影响断言结果。两条旧符号和危险 ORM 的 `rg` 复查均无命中。

## 2026-06-02 自动监工：通知覆盖与启动延迟保护收口

### 范围

本轮继续监工云资产生命周期唯一事实源、通知批次冲突、旧字段回流、旧计划快照表回流和退款入口回流。起始工作树已有 `cloud/asset_expiry.py`、`cloud/lifecycle.py`、`cloud/lifecycle_schedule.py`、`cloud/tests.py` 未提交改动，最近提交为 `7ef9df6 记录生命周期唯一事实源监工结果`；本轮在理解这些改动后继续收口，没有覆盖其他用户改动。

### 运行变更

- 将到期归一化函数命名从 `normalize_service_expiry` 收口为 `normalize_asset_expiry`，避免代码语义继续暗示订单表服务到期字段。
- 通知批次手工文案覆盖 key 改为按“事件 + 用户 + 订单 + 当前 `CloudAsset.actual_expires_at` 到期周期”生成；同一订单续费进入新到期周期后，旧周期手工文案不会挡住新周期默认提醒。
- 通知批次订单列表排序后再生成批次号，避免同一批订单因列表顺序不同产生不同任务键。
- 启动破坏性动作延迟保护补齐固定 IP 回收和未附加固定 IP 删除：服务启动检查命中这些动作时，本轮不释放真实云资源；固定 IP 回收只顺延 `CloudServerOrder.ip_recycle_at`，未附加固定 IP 不改写 `CloudAsset.actual_expires_at`。
- 补充回归测试，验证首个到期周期可使用手工文案，新到期周期仍会正常发送新文案，并覆盖启动延迟保护下固定 IP 回收、未附加固定 IP 删除不会立即执行真实释放。

### 复查结论

- 未发现 `normalize_service_expiry` 旧符号残留。
- 未发现 `CloudServerOrder.service_expires_at`、`service_expired_at` 等订单表到期字段恢复。
- 未发现旧计划快照表模型恢复；当前命中均为现有 dashboard/lifecycle 刷新函数或测试名。
- 未发现退款函数名和退款运行时入口恢复。
- 废弃 app 未重新加入 `INSTALLED_APPS`；`dashboard_api` 仅作为现有后台路由命名空间和 `core.dashboard_api` helper 名称存在。
- 补充抽查 `refresh_lifecycle_plans` / `refresh_notice_plans` 管理命令，确认仍生成实时 bundle，不恢复旧计划快照表；`CloudLifecycleTask` / `CloudNoticeTask` 的 `basis_actual_expires_at` 仍只作审计字段。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/asset_expiry.py cloud/lifecycle_schedule.py cloud/lifecycle.py cloud/services.py cloud/api_orders.py cloud/api_asset_edit.py cloud/api_tasks.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/refresh_lifecycle_plans.py cloud/management/commands/refresh_notice_plans.py
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-notice-cycle-test.sqlite3 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_allows_new_expiry_cycle cloud.tests.CloudServerServicesTestCase.test_send_logged_cloud_notice_deduplicates_same_event_and_order cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_prefers_bound_group_and_skips_private cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_falls_back_private_when_group_fails --noinput
UV_CACHE_DIR=/private/tmp/uv-cache-shop DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-startup-defer-test.sqlite3 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_startup_defer_reschedules_static_ip_cleanup_without_release cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_startup_defer_blocks_order_static_ip_release cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_startup_defer_blocks_unattached_static_ip_release --noinput
git diff --check
```

文件 SQLite 启动延迟用例会打印现有 dashboard snapshot 后台刷新锁等待日志，但测试断言通过；两条旧符号和危险 ORM 的 `rg` 复查无命中。

## 2026-06-02 自动监工：生命周期唯一事实源复查

### 范围

本轮继续监工云资产生命周期重构后的唯一到期事实源、任务认领冲突保护、废弃 app 回流、计划快照表回流和退款入口回流。重点确认 `CloudAsset.actual_expires_at` 仍是唯一结构化服务到期事实，订单表未恢复旧到期字段，旧计划快照表和退款函数名未恢复。

### 复查结论

- 当前工作树起始干净，最近提交为 `dd87a12 记录旧到期函数测试收口`。
- 运行时代码未发现对已移除 `CloudServerOrder.service_expires_at` 数据库列的过滤、排序、批量更新或 values 查询；保留命中为日志字段名、API 兼容 payload 字段和测试数据。
- 模型导出中未恢复 `CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan` 旧计划快照模型；当前仅保留 `CloudLifecyclePlanNote` 手工备注模型。
- 未发现退款入口、退款函数名或 `refunded` 运行时状态筛选重新接入。
- 废弃 app 未重新加入 `INSTALLED_APPS`；`dashboard_api` 仅作为后台路由 namespace 和 `core.dashboard_api` 公共辅助命名继续存在。
- 本轮未修改生产代码，只补充监工记录。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/models.py cloud/asset_expiry.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/lifecycle_tasks.py cloud/api_assets.py cloud/api_asset_snapshots.py cloud/api_orders.py cloud/api_asset_edit.py cloud/provisioning.py cloud/services.py bot/api.py bot/handlers.py orders/services.py orders/payment_scanner.py
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run cloud
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_dashboard_order_expiry_update_syncs_asset_expiry_and_lifecycle_plan cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_allows_new_expiry_cycle cloud.tests.CloudServerServicesTestCase.test_apply_cloud_server_renewal_keeps_original_service_started_at cloud.tests.CloudServerServicesTestCase.test_lifecycle_asset_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_skips_when_lifecycle_task_claimed cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_task_claim_blocks_same_cycle_duplicate --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields --noinput --verbosity 1
```

## 2026-06-02 自动监工：资产生命周期任务认领补齐

### 范围

本轮继续检查云资产生命周期重构后的冲突逻辑、通知去重、到期事实源和废弃字段回流，重点确认无订单资产删除、未附加 IP 删除、固定 IP 回收、后台订单到期编辑和续费后新通知周期不会绕开唯一到期事实或数据库任务认领。

### 运行变更

- 新增资产维度生命周期任务认领入口，按“动作 + 资产 + 计划时间”生成唯一任务键；无订单服务器删除和未附加固定 IP 删除在执行云 API 前先认领 `CloudLifecycleTask`。
- 固定 IP 回收入口在真实释放前认领 `CloudLifecycleTask`，同一轮计划已认领、已完成或处于失败重试保护期时直接跳过，避免多进程重复释放。
- 通知批次键加入订单当前 `CloudAsset.actual_expires_at` 到期周期；同一订单续费进入新周期后允许重新发送提醒，不被上一周期的通知任务挡住。
- 续费成功后清空续费、自动续费预提醒、自动续费失败、删机和 IP 回收提醒发送时间，避免旧周期状态影响新周期提醒。
- 后台订单详情显式修改服务到期时间时，直接同步主资产 `CloudAsset.actual_expires_at` 和 Server 兼容记录，再按同一到期事实重算订单生命周期字段，避免订单计划字段和资产事实字段打架。
- 移除提醒汇总里已废弃的 `refunded` 状态残留，退款语义不再参与运行时筛选。
- 复查运行模型和迁移：未恢复 `CloudServerOrder.service_expires_at` 字段，未恢复旧计划快照表，未恢复退款逻辑或退款函数名；`CloudAsset.actual_expires_at` 仍是唯一结构化到期事实。

### 验证

本地已通过：

```bash
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python -m py_compile cloud/models.py cloud/lifecycle_tasks.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/services.py cloud/api_orders.py cloud/tests.py
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py check
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py makemigrations --check --dry-run
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_dashboard_order_expiry_update_syncs_asset_expiry_and_lifecycle_plan cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_allows_new_expiry_cycle cloud.tests.CloudServerServicesTestCase.test_apply_cloud_server_renewal_keeps_original_service_started_at cloud.tests.CloudServerServicesTestCase.test_lifecycle_asset_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_skips_when_lifecycle_task_claimed cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_task_claim_blocks_same_cycle_duplicate --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_notice_batches_multiple_ips_for_same_user cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_prefers_bound_group_and_skips_private cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_falls_back_private_when_group_fails --noinput --verbosity 1
```

## 2026-06-02 自动监工：生命周期任务表迁移补齐

### 范围

本轮继续检查云资产生命周期重构后的并发认领、通知去重、到期事实源和迁移完整性，重点确认新增任务表不会绕回旧计划快照表，也不会恢复订单到期字段。

### 运行变更

- 补齐 `CloudLifecycleTask` 和 `CloudNoticeTask` 的迁移 `cloud/migrations/0047_lifecycle_task_notice_task.py`，避免模型已加入但测试库或部署库缺表。
- 复查生命周期动作和通知发送入口：计划关机、计划删机、迁移旧机删除会先认领生命周期任务；通知发送会先认领通知任务，再检查历史送达日志。
- 复查到期事实源：运行时代码仍以 `CloudAsset.actual_expires_at` 作为结构化服务到期事实；新增任务表里的 `basis_actual_expires_at` 仅用于审计排查。
- 未恢复 `CloudServerOrder.service_expires_at` 字段，未恢复 `CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan` 旧快照表，未恢复退款逻辑或退款函数名。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python -m py_compile cloud/lifecycle.py cloud/lifecycle_execution.py cloud/lifecycle_tasks.py cloud/models.py cloud/tests.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py makemigrations --check --dry-run cloud
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-lifecycle-new-tests.sqlite UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_send_logged_cloud_notice_deduplicates_same_event_and_order cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_task_claim_blocks_same_cycle_duplicate -v 2 --noinput
git diff --check
```

受限但已定位：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_send_logged_cloud_notice_deduplicates_same_event_and_order cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_task_claim_blocks_same_cycle_duplicate -v 2
```

默认 MySQL 测试库连接被当前沙箱禁止访问 `127.0.0.1:3306`，需用 sqlite 测试开关或可访问的 MySQL 测试库运行。

```bash
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_order_delete_at_before_cloud_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_order_ip_recycle_at_before_release cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_unattached_ip_uses_ip_delete_time_window -v 2
```

内存 sqlite 下现有 `thread_sensitive=False` 生命周期执行路径会跨线程打开新连接，导致测试线程内未提交表不可见；文件 sqlite 下同组用例又受 `TestCase` 事务和 sqlite 写锁影响，不作为本轮新增任务表回归失败处理。

## 2026-06-02 手动重构：生命周期和通知任务表支撑

### 范围

本轮按“删除计划和通知计划要有数据库支撑”的方向收口生命周期并发风险，重点处理实时计算结果直接执行时可能出现的重复删机、重复关机和重复通知问题。

### 运行变更

- 新增 `CloudLifecycleTask` 任务表，表名 `cloud_lifecycle_task`，用于记录计划关机、计划删机、迁移旧机删除等生命周期动作的计划时间、认领状态、尝试次数、错误原因和完成时间。
- 新增 `CloudNoticeTask` 任务表，表名 `cloud_notice_task`，用于记录到期提醒、自动续费预提醒、删机提醒和 IP 回收提醒的发送认领状态、目标会话、批次、尝试次数和发送结果。
- 新增 `cloud/lifecycle_tasks.py` 统一处理任务 `source_key`、数据库认领、失败重试和完成状态写入；同一轮计划按“动作 + 资源 + 计划时间”生成唯一键，续费后新的计划时间会形成新一轮任务。
- 计划触发的关机、删机和迁移旧机删除在执行云 API 前必须先认领 `CloudLifecycleTask`；同一轮任务已被认领或已完成时，本轮重复触发会跳过。
- 通知发送入口在真正发送前先认领 `CloudNoticeTask`，再检查历史送达日志；这样避免两个进程同时“查不到日志”后双发。
- 群组通知和私聊 fallback 使用不同任务来源键：群组失败后仍允许私聊兜底，群组成功后仍由历史送达日志阻止私聊重复发送。
- `CloudAsset.actual_expires_at` 仍是唯一结构化服务到期事实；两个新任务表的 `basis_actual_expires_at` 只用于审计和排查，不作为第二套到期事实。
- 本轮没有恢复 `CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan` 旧快照表，也没有恢复 `CloudServerOrder.service_expires_at` 字段或退款逻辑。

### 验证

本地已通过：

```bash
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python -m py_compile cloud/models.py cloud/lifecycle_tasks.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/tests.py
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py check
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py makemigrations --check --dry-run
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_send_logged_cloud_notice_deduplicates_same_event_and_order cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_reads_suspend_time_config_outside_async_loop --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_notice_batches_multiple_ips_for_same_user cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_prefers_bound_group_and_skips_private cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_falls_back_private_when_group_fails --noinput --verbosity 1
```

## 2026-06-02 自动监工：群组通知测试到期字段同步

### 范围

本轮继续监工云资产生命周期唯一事实源，重点复查 `CloudAsset.actual_expires_at` 是否仍是唯一到期事实、订单表旧到期字段是否恢复、计划快照表和退款入口是否回流，并抽查上一轮剩余的群组代理、自动续费和通知批量测试。

### 运行变更

- `CloudServerOrder.service_expires_at` 仍未恢复为模型字段；运行代码继续通过 `order_asset_expiry()` 和 `CloudAsset.actual_expires_at` 读取服务到期事实。
- 旧字段危险 ORM 扫描未发现运行代码继续对订单旧字段做 `filter/update/order_by/values` 查询；命中主要是历史迁移、API payload 键和测试残留。
- 废弃 app 扫描仍只命中 `core.dashboard_api` 共享工具、后台 URL namespace、权限码和历史文档命名，没有发现重新注册 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz` 运行时 app。
- 修正 `cloud/tests.py` 中群组代理列表、同群可见性、同群续费、群组批量自动续费、自动续费候选人和通知批量发送用例，不再向 `CloudServerOrder.objects.create()` 传旧 `service_expires_at`，改为显式创建关联 `CloudAsset.actual_expires_at`。
- 保留并验证订单旧到期字段被拒绝的回归测试，确保测试侧也不再把旧字段当模型字段写入。
- 当前 `cloud/tests.py` 剩余旧 `service_expires_at=` 测试写法降至 82 处，后续继续分批同步；`actual_expires_at=order.service_expires_at` 形式剩余 18 处。
- 工作区另有未提交的 `cloud/lifecycle_execution.py` 生命周期动作缓存锁草稿，以及 `cloud/models.py` 生命周期/通知任务模型草稿；本轮未覆盖、未纳入提交。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/models.py cloud/asset_expiry.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/api_assets.py cloud/api_asset_snapshots.py cloud/api_orders.py cloud/api_asset_edit.py cloud/provisioning.py cloud/services.py bot/api.py bot/handlers.py orders/services.py orders/payment_scanner.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_group_cloud_server_list_is_scoped_to_current_group cloud.tests.CloudServerServicesTestCase.test_user_proxy_asset_detail_allows_same_bound_group_visibility cloud.tests.CloudServerServicesTestCase.test_same_bound_group_asset_renewal_uses_user_visibility cloud.tests.CloudServerServicesTestCase.test_group_auto_renew_bulk_toggle_is_scoped_to_current_group cloud.tests.CloudServerServicesTestCase.test_auto_renew_candidates_exclude_admin_notice_users cloud.tests.CloudServerServicesTestCase.test_auto_renew_candidates_exclude_primary_admin_user cloud.tests.CloudServerServicesTestCase.test_auto_renew_group_member_can_pay_when_owner_balance_insufficient cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_prefers_bound_group_and_skips_private cloud.tests.CloudServerServicesTestCase.test_send_order_notice_batch_falls_back_private_when_group_fails --noinput --verbosity 1
git diff --check
```

`makemigrations --check --dry-run` 仍因当前沙箱禁止连接本地 MySQL 输出一致性历史检查警告，但结果为 `No changes detected`。

## 2026-06-02 自动监工：资产续费完成到期推进修复

### 范围

本轮继续监工云资产生命周期唯一事实源，重点复查 `CloudAsset.actual_expires_at` 是否仍是唯一到期事实、订单表旧到期字段是否被恢复、计划快照表和退款入口是否回流，以及当前测试草稿在未绑定代理资产续费完成后的到期推进语义。

### 运行变更

- `CloudServerOrder.service_expires_at` 未恢复为模型字段；运行代码继续通过 `order_asset_expiry()` / `CloudAsset.actual_expires_at` 读取到期事实。
- `cloud/provisioning.py` 在 `_mark_success()` 开始时固定“未绑定代理资产续费”判定，避免保存完成状态、实例 ID 和开始时间后再次判定变成普通订单。
- 未绑定代理资产续费完成时，`_upsert_server_asset()` 不再保留旧资产到期时间，而是写入以本次完成时间和 `lifecycle_days` 计算的新 `CloudAsset.actual_expires_at`。
- 同步当前测试草稿中一组续费、配置调整、AWS 同步解析和生命周期到期用例，测试数据不再向订单创建参数传入旧 `service_expires_at`，改为显式创建或更新关联 `CloudAsset.actual_expires_at`。
- 继续同步 `cloud/tests.py` 中删除提醒批量通知、资产风险搜索、分页搜索、禁用/恢复关机账号、延后关机、缺失资产到期和通知文本用例；测试数据统一通过 `CloudAsset.actual_expires_at` 承载到期时间。
- 资产风险搜索测试拆分普通资产用户，避免快照搜索命中用户后按设计扩展出同一用户全部资产，测试意图回到资产标识搜索本身。
- 当前 `bot/tests.py` 已无订单旧到期字段测试残留；`cloud/tests.py` 剩余旧测试写法降至 151 处，后续继续分批同步。
- 废弃 app 扫描命中主要为 `core.dashboard_api` 共享工具、后台 URL namespace、权限码和历史迁移命名，没有发现重新注册 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz` 运行时 app。
- 旧字段危险 ORM 扫描只命中历史迁移 `0043_backfill_cloud_asset_expiry`，运行代码未发现对订单旧字段做 `filter/update/order_by/values` 查询。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/provisioning.py cloud/services.py cloud/asset_expiry.py cloud/models.py cloud/api_orders.py cloud/api_asset_edit.py cloud/lifecycle.py bot/api.py bot/handlers.py bot/tests.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_apply_cloud_server_renewal_keeps_original_service_started_at cloud.tests.CloudServerServicesTestCase.test_renewal_postcheck_skips_running_records cloud.tests.CloudServerServicesTestCase.test_cloud_upgrade_wallet_payment_is_idempotent cloud.tests.CloudServerServicesTestCase.test_config_change_success_does_not_steal_old_server_record cloud.tests.CloudServerServicesTestCase.test_asset_renewal_mark_success_starts_new_service_period cloud.tests.CloudServerServicesTestCase.test_aws_sync_resolver_does_not_match_replacement_by_old_ip cloud.tests.CloudServerServicesTestCase.test_aws_sync_resolver_prefers_ip_over_changed_instance_name cloud.tests.CloudServerServicesTestCase.test_cloud_config_change_lists_and_creates_downgrade_order cloud.tests.CloudServerServicesTestCase.test_cloud_config_change_ceil_custom_price_to_plan_tier cloud.tests.CloudServerServicesTestCase.test_due_orders_use_asset_expiry_for_lightsail_lifecycle bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_suspend_execution_guard_respects_account_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_due_orders_skip_asset_when_expiry_missing cloud.tests.CloudServerServicesTestCase.test_due_orders_respect_deferred_suspend_at cloud.tests.CloudServerServicesTestCase.test_due_orders_restore_suspend_after_account_shutdown_reenabled cloud.tests.CloudServerServicesTestCase.test_mark_suspended_only_updates_latest_asset_and_server cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_reads_suspend_time_config_outside_async_loop cloud.tests.CloudServerServicesTestCase.test_notice_plan_text_shows_configured_execution_time cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_notice_batches_multiple_ips_for_same_user cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_filters_by_risk_and_searches_asset_identifiers cloud.tests.CloudServerServicesTestCase.test_cloud_assets_search_filters_full_dataset_before_pagination --noinput --verbosity 1
git diff --check
```

`makemigrations --check --dry-run` 仍因当前沙箱禁止连接本地 MySQL 输出一致性历史检查警告，但结果为 `No changes detected`。

## 2026-06-02 手动监工：资产到期唯一化与计划快照表移除

### 范围

本轮按“大改、不兼容旧字段”的方向继续收口云资产生命周期重构，重点处理订单到期字段残留、计划快照表、自助退款逻辑和派生到期列。

### 运行变更

- `CloudServerOrder` 不再暴露 `service_expires_at` 兼容 property/setter，订单表层面不再承载服务到期事实。
- 删除 `CloudServerOrder.normalize_expiry_time()` 和 `save()/refresh_from_db()` 中旧到期兼容分支；运行代码再次传入订单旧到期字段会直接暴露错误。
- 当前服务到期事实只读取和写入 `CloudAsset.actual_expires_at`；迁移、续费、重装、修改配置等新订单如果需要预置到期，会创建或更新关联资产记录承载到期时间。
- 删除 `CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan` 三个派生快照模型，并新增 `0045_delete_plan_snapshot_tables` 迁移删除对应表。
- 新增 `0046_remove_derived_expiry_columns` 迁移，删除 `CloudAssetDashboardSnapshot.actual_expires_at` 和 `CloudAutoRenewPatrolLog.service_expires_at` 两个派生到期列；结构化到期事实只保留 `CloudAsset.actual_expires_at`。
- 代理列表快照的排序、分组改为通过关联资产读取 `CloudAsset.actual_expires_at`，快照表不再复制到期列。
- 删除自助退款函数入口、Bot 退款回调、退款按钮参数和退款测试；退款逻辑不再保留函数名。
- 删除计划、通知计划、自动续费计划和任务中心改为实时从订单、资产、通知日志和自动续费巡检日志生成，不再依赖快照表。
- 删除旧快照表相关的 `_sync_*_plan_table`、`*_plan_row_*`、`_upsert_*_plan_rows`、`_cloud_*_plan_items` 空壳函数名；后台任务中心、快照协调和刷新命令改为调用实时生成函数。
- 同步 `bot/tests.py` 中订单列表和管理员改到期测试，测试数据改为通过 `CloudAsset.actual_expires_at` 承载到期时间，不再向 `CloudServerOrder.objects.create()` 传旧字段。
- 同步 `cloud/tests.py` 前部一批订单/资产测试，新增 `_attach_order_expiry_asset()` 测试辅助方法，订单到期测试数据改为创建关联资产承载到期时间。
- 终端版 `codex exec` 已按只读模式启动检查；本地检查确认 CLI 版本为 `codex-cli 0.135.0-alpha.1`，模型参数为 `gpt-5.5`。本次 CLI 过程因远端 429 未给最终总结，但搜索输出命中了 Bot 测试旧字段和命名文档残留，已据此处理。
- 已更新 Codex App 自动化 `Shop 自动优化监工`，保持 10 分钟一次，并补充本轮数据库重构重点和“只写中文记录”的要求。

### 验证

本地已通过：

```bash
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python -m py_compile cloud/models.py cloud/asset_expiry.py cloud/services.py cloud/provisioning.py cloud/lifecycle.py bot/api.py bot/handlers.py bot/keyboards.py cloud/api_tasks.py cloud/task_center.py cloud/api_asset_edit.py cloud/api_asset_snapshots.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py orders/payment_scanner.py orders/tests.py
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py makemigrations --check --dry-run
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py check
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py migrate --plan
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_refresh_lifecycle_plan_table_api_populates_cloud_lifecycle_plan cloud.tests.CloudServerServicesTestCase.test_refresh_notice_plan_table_api_populates_cloud_notice_plan cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_snapshot cloud.tests.CloudServerServicesTestCase.test_update_unattached_ip_release_time_refreshes_delete_plan_snapshot cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_uses_cloud_notice_plan_table --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_asset_dashboard_snapshot_refresh_materializes_paginated_list --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.BotOrderAndBalanceFilterTestCase bot.tests.BotAdminExpiryUpdateTestCase --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_orders_list_exposes_auto_renew_enabled cloud.tests.CloudServerServicesTestCase.test_manual_order_delete_writes_server_history_item cloud.tests.CloudServerServicesTestCase.test_aliyun_create_and_renew_require_bound_account cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_uses_bulk_order_inference_without_per_asset_fallback cloud.tests.CloudServerServicesTestCase.test_rebind_cloud_server_user_syncs_order_asset_and_server cloud.tests.CloudServerServicesTestCase.test_cloud_renewal_address_order_uses_usdt_even_after_trx_wallet_order --noinput --verbosity 1
git diff --check
```

说明：完整测试文件仍有 187 处旧 `service_expires_at` 测试写法需要下一轮集中同步；本轮先确保运行代码、模型状态、迁移检测、Django 系统检查和本轮触碰的计划/通知/Bot/cloud 回归通过。

## 2026-06-02 自动监工：订单到期兼容复查

### 范围

本轮继续复查 `CloudServerOrder.service_expires_at` 移除后的运行状态，重点覆盖废弃 app 误用、旧字段危险 ORM 模式、订单/资产到期清空语义和上一轮兼容缓存修复后的聚焦回归。

### 运行变更

- 未修改生产代码；本轮只补充监工记录。
- 废弃 app 扫描命中主要为文档、后台 URL namespace、权限码和 `core.dashboard_api` 共享工具，不是重新注册或恢复旧 `accounts/finance/mall/monitoring/dashboard_api/biz` 运行时 app。
- 旧 `service_expires_at` 危险 ORM 模式扫描未发现运行代码继续对 `CloudServerOrder` 已移除字段做 `filter/update/order_by/values` 等查询；测试中的旧字段创建参数仍属于兼容属性覆盖。
- 复核订单详情和资产编辑的清空到期路径：订单生命周期会在空到期时清空；订单编辑按当前语义不反向覆盖 `CloudAsset.actual_expires_at` 手工字段。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/models.py cloud/api_orders.py cloud/api_asset_edit.py cloud/services.py cloud/provisioning.py bot/api.py bot/handlers.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.BotAdminExpiryUpdateTestCase cloud.tests.CloudServerServicesTestCase.test_order_save_backfills_blank_asset_expiry_only cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
```

`makemigrations --check --dry-run` 仍因当前沙箱禁止连接本地 MySQL 输出一致性历史检查警告，但结果为 `No changes detected`。

## 2026-06-02 自动监工：订单到期兼容缓存清理

### 范围

本轮继续复查 `CloudServerOrder.service_expires_at` 移除后的兼容属性路径，重点覆盖 Bot 管理员修改代理到期时间后的订单、资产和 Server 兼容视图一致性。

### 运行变更

- `CloudServerOrder.save()` 处理过兼容 `service_expires_at` 输入后，会清掉“待写入”标记，避免后续普通保存继续把旧兼容值当成新输入重新计算生命周期。
- `CloudServerOrder.refresh_from_db()` 会清理兼容到期时间缓存，确保刷新后的 `order.service_expires_at` 重新读取 `CloudAsset.actual_expires_at`，不再返回实例上残留的旧值。
- 修复 Bot 管理员修改订单到期时间后，同一个订单实例刷新仍显示旧到期时间的断点；资产和 Server 兼容入口继续以 `CloudAsset.actual_expires_at` 为事实源。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/models.py bot/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.BotOrderAndBalanceFilterTestCase bot.tests.BotAdminExpiryUpdateTestCase --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_save_backfills_blank_asset_expiry_only cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle bot.tests.BotAdminExpiryUpdateTestCase.test_admin_expiry_update_syncs_order_asset_and_server --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
git diff --check
```

`makemigrations --check --dry-run` 仍因当前沙箱禁止连接本地 MySQL 输出一致性历史检查警告，但结果为 `No changes detected`。

## 2026-06-02 自动监工：资产到期兼容测试收口

### 范围

本轮继续检查 `CloudServerOrder.service_expires_at` 移除后的兼容状态，重点覆盖仍把旧字段当数据库列写入的测试断点，以及未绑定固定 IP 续费恢复流程的资产到期断言。

### 运行变更

- 未绑定固定 IP 续费钱包支付测试不再通过 `CloudServerOrder.objects.update(service_expires_at=...)` 写已移除字段。
- 相关断言改为确认 `service_expires_at` 兼容属性读取关联 `CloudAsset.actual_expires_at`，匹配当前资产事实源。
- 后台云资产编辑清空 `actual_expires_at` 时，同步清空关联订单生命周期字段，避免旧关机、删机、IP 回收计划残留。
- 字段引用扫描确认当前运行代码不再对订单旧字段做 ORM `filter/update/order_by/values` 操作；仅 `0043` 历史迁移在 `0044` 删除字段前读取旧列用于回填资产到期。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py bot/handlers.py bot/keyboards.py cloud/api_orders.py cloud/api_tasks.py cloud/api_asset_edit.py cloud/lifecycle.py cloud/provisioning.py cloud/services.py cloud/models.py cloud/asset_expiry.py cloud/tests.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py orders/payment_scanner.py orders/services.py core/management/commands/cleanup_old_records.py orders/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_save_backfills_blank_asset_expiry_only cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_repairs_completed_unpaid_state --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_save_backfills_blank_asset_expiry_only cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry cloud.tests.CloudServerServicesTestCase.test_shutdown_log_items_prefer_order_lifecycle_schedule cloud.tests.CloudServerServicesTestCase.test_daily_expiry_summary_uses_real_cloud_status_and_target_config cloud.tests.CloudServerServicesTestCase.test_aws_sync_server_resolution_accepts_legacy_account_label cloud.tests.CloudServerServicesTestCase.test_aws_sync_resolver_prefers_ip_over_changed_instance_name cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_write_requires_superuser cloud.tests.CloudServerServicesTestCase.test_dashboard_order_ip_and_name_update_syncs_asset_server --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_price_restores_auto_renew_pending_state cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_refreshes_unattached_ip_delete_plan cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_rebinds_unattached_ip_to_instance --noinput --verbosity 1
git diff --check
```

`makemigrations --check --dry-run` 仍因当前沙箱禁止连接本地 MySQL 输出一致性历史检查警告，但结果为 `No changes detected`。

## 2026-06-02 自动监工：订单到期字段移除兼容收口

### 范围

本轮继续处理 `CloudServerOrder.service_expires_at` 移除草稿，重点收口仍会生成 ORM 查询错误或运行时写旧字段的后台任务、订单 API 和清理命令。

### 运行变更

- `CloudServerOrder.service_expires_at` 改为对象级兼容属性，读取主 `CloudAsset.actual_expires_at`，旧代码赋值后保存只补齐空资产到期并刷新生命周期字段，不覆盖已有手工资产到期。
- 后台自动续费计划、通知未来计划、用户提醒摘要和旧记录清理命令不再按已移除的订单字段过滤或排序，改为使用 `CloudAsset.actual_expires_at` 或现有订单生命周期字段。
- 后台云订单详情处理 `service_expires_at` 输入时，不再用 `CloudServerOrder.objects.update()` 写已移除字段；仅刷新订单生命周期字段，并只为缺失到期时间的关联资产补齐资产到期。
- 后台云资产编辑处理 `actual_expires_at` 输入时，不再把已移除的 `service_expires_at` 混入 `CloudServerOrder.objects.update()`；关联订单只同步生命周期字段和提醒重置，避免 FieldError 被捕获后跳过同步。
- 个人中心云订单列表移除旧字段排序，避免字段删除后列表查询报错。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py bot/handlers.py bot/keyboards.py cloud/api_orders.py cloud/api_tasks.py cloud/api_asset_edit.py cloud/lifecycle.py cloud/provisioning.py cloud/services.py cloud/models.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py orders/payment_scanner.py orders/services.py core/management/commands/cleanup_old_records.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_save_backfills_blank_asset_expiry_only cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_save_backfills_blank_asset_expiry_only cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry cloud.tests.CloudServerServicesTestCase.test_shutdown_log_items_prefer_order_lifecycle_schedule cloud.tests.CloudServerServicesTestCase.test_daily_expiry_summary_uses_real_cloud_status_and_target_config cloud.tests.CloudServerServicesTestCase.test_aws_sync_server_resolution_accepts_legacy_account_label cloud.tests.CloudServerServicesTestCase.test_aws_sync_resolver_prefers_ip_over_changed_instance_name cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_write_requires_superuser cloud.tests.CloudServerServicesTestCase.test_dashboard_order_ip_and_name_update_syncs_asset_server --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_price_restores_auto_renew_pending_state cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_refreshes_unattached_ip_delete_plan cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_rebinds_unattached_ip_to_instance --noinput --verbosity 1
git diff --check
```

`makemigrations --check --dry-run` 输出 `No changes detected`，但一致性历史检查因当前沙箱禁止连接本地 MySQL 出现警告。尝试执行后台任务查询 smoke 时，默认 MySQL 连接同样被当前沙箱网络权限拦截；改用 `DJANGO_TEST_SQLITE=1` 的普通 shell 又因非测试流程未创建 SQLite 表而无法执行实际查询。本轮已用编译、系统检查和聚焦测试覆盖字段移除后的主要断点。

## 2026-06-02 自动监工：Server 兼容入口保护手工资产字段

### 范围

本轮继续检查云资产生命周期重构后的兼容入口，重点看旧 `Server` 包装层复用 `CloudAsset` 时是否会破坏当前资产事实源。

### 运行变更

- `cloud.server_records.Server.objects.create()` 复用已有 `CloudAsset` 时，保留已有手工绑定用户和 `actual_expires_at`，不再被旧入口传入的 `user` / `expires_at` 覆盖。
- 保持旧入口仍可同步资源名、状态等运行字段，避免为了保护手工字段而阻断兼容命令更新。
- 新增回归测试覆盖旧 `Server` 入口按实例/IP 复用资产时，资产 owner 和实际到期时间保持不变。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/server_records.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry --noinput --verbosity 1
```

当前工作树另有未提交的订单到期字段迁移草稿（`CloudServerOrder.service_expires_at` 移除方向），旧的 `test_order_save_backfills_blank_asset_expiry_only` 已不适配该迁移状态，未纳入本轮提交。

## 2026-06-02 自动监工：资产到期与 Server 兼容层收口

### 范围

本轮继续检查云资产生命周期重构后的测试失败、导入断点和到期时间来源不一致问题。

### 运行变更

- `CloudServerOrder.save()` 在订单到期时间变更后，只回填仍为空的关联 `CloudAsset.actual_expires_at`，不覆盖已有手工资产到期时间。
- 新增数据迁移 `cloud.0043_backfill_cloud_asset_expiry`，为历史上资产到期为空但订单到期存在的 server 资产补齐 `actual_expires_at`。
- 关机日志和每日到期汇总继续统一优先读取 `CloudAsset.actual_expires_at`，订单时间仅作为资产时间缺失时的兜底。
- AWS/Aliyun 同步命令恢复 `_resolve_server` 薄兼容别名，指向当前 `_resolve_asset`，避免旧测试/兼容导入失败。
- `cloud.server_records.Server.objects.create()` 改为按订单、IP、实例名或云资源 ID 复用现有 `CloudAsset`，避免旧 `Server` 入口在同一资源上制造重复资产。
- `cloud.tests` 中后台 RequestFactory 调用改用已有 bearer session helper，匹配当前写请求必须带 dashboard bearer session 的鉴权规则。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py cloud/lifecycle.py cloud/models.py cloud/server_records.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_save_backfills_blank_asset_expiry_only cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry cloud.tests.CloudServerServicesTestCase.test_shutdown_log_items_prefer_order_lifecycle_schedule cloud.tests.CloudServerServicesTestCase.test_daily_expiry_summary_uses_real_cloud_status_and_target_config cloud.tests.CloudServerServicesTestCase.test_aws_sync_server_resolution_accepts_legacy_account_label cloud.tests.CloudServerServicesTestCase.test_aws_sync_resolver_prefers_ip_over_changed_instance_name cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_write_requires_superuser cloud.tests.CloudServerServicesTestCase.test_dashboard_order_ip_and_name_update_syncs_asset_server --noinput --verbosity 1
```

全量 `cloud.tests` 已从本轮开始时的 `failures=51, errors=26` 收敛到 `failures=28, errors=18`，但仍未全绿；剩余集中在旧 patch 目标、生命周期云操作测试和部分同步/删除历史语义。

## 2026-06-02 通知计划到期时间统一修复

### 范围

本轮按用户要求确认并统一“通知计划、删除计划、代理列表”的到期时间来源，避免同一台代理在不同页面出现不同到期时间。

### 运行变更

- 通知计划对有关联资产的云订单，改为优先读取 `CloudAsset.actual_expires_at`。
- 删除计划和代理列表原本已经读取 `CloudAsset.actual_expires_at`，本轮保持不变。
- 当资产实际到期时间缺失时，通知计划仍回退读取订单 `service_expires_at`，避免历史订单没有资产时间时完全丢失通知。
- 新增回归测试，故意制造订单时间和资产时间不同，断言通知计划、删除计划、代理列表都使用资产实际到期时间。

### 验证

本地已通过：

```bash
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python -m py_compile cloud/lifecycle.py cloud/tests.py bot/api.py cloud/api_assets.py
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry cloud.tests.CloudServerServicesTestCase.test_notice_plan_text_shows_configured_execution_time cloud.tests.CloudServerServicesTestCase.test_aws_notice_schedule_does_not_override_manual_order_expiry --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py check
```

## 2026-06-02 群内保留固定 IP 续费套餐按钮授权修复

### 范围

本轮根据终端版 Codex 诊断，修复同群用户从保留固定 IP 资产详情看到续费套餐后，点击套餐按钮仍被误判“该代理不属于当前群”的断点。

### 运行变更

- 新增 `is_retained_ip_order_visible_in_group()`，只按当前启用群绑定校验保留固定 IP 续费订单可见性。
- Telegram 机器人群聊订单可见性在普通代理列表未命中后，追加保留固定 IP 续费授权检查。
- 普通群代理列表仍沿用启用资产过滤，不把已删除或停用的保留固定 IP 加回列表。
- 保留 `bot/tests.py` 中当前处理器补丁目标改为 `get_config` 的测试修正，匹配现有导入名。

### 验证

本地已通过：

```bash
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python -m py_compile bot/handlers.py cloud/services.py cloud/tests.py bot/tests.py
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_retained_deleted_asset_renewal_plans_allow_same_group_visibility cloud.tests.CloudServerServicesTestCase.test_proxy_list_hides_deleted_order_retained_ip --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TronGridFallbackTestCase bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py check
```

## 2026-06-02 同群保留固定 IP 资产续费入口修复

### 范围

本轮根据上一轮终端版 Codex 诊断候选，修复保留固定 IP 资产按资产按钮进入续费时仍只按资产所属人过滤的问题。

### 运行变更

- `list_retained_ip_renewal_plans_by_asset()` 增加群聊上下文参数。
- 私聊场景复用用户资产可见性，同一绑定群组内可见用户可以取到保留固定 IP 续费套餐。
- 群聊场景只允许当前启用绑定群内的资产通过，不使用宽泛管理员绕过。
- Telegram 机器人的 `cloud:assetaction:renew` 兜底调用会传入当前群 ID，避免同群保留固定 IP 资产看得到入口却取不到套餐。
- 新增回归测试覆盖资产所属人原路径、同群私聊可见路径、群聊路径、无关用户和错误群拒绝路径。

### 验证

本地已通过：

```bash
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python -m py_compile bot/handlers.py cloud/services.py cloud/tests.py
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_retained_deleted_asset_renewal_plans_are_available_by_asset_button cloud.tests.CloudServerServicesTestCase.test_retained_deleted_asset_renewal_plans_allow_same_group_visibility --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py check
```

## 2026-06-02 后台手工密钥编辑同步主链接修复

### 范围

本轮根据 Codex CLI 只读诊断，修复后台云订单只修改 `mtproxy_secret` 时，主代理链接和代理链路列表仍保留旧密钥的问题。

### 运行变更

- 后台云订单保存新密钥时，会同步重写 `mtproxy_link` 里的 `secret` 参数。
- 同步重建 `proxy_links` 的主代理项，避免 Bot 详情显示“新密钥、旧链接”的不一致状态。
- 继续复用已有主记录同步逻辑，把新主链接、新密钥和代理链路同步到关联 `CloudAsset`。
- 新增回归测试覆盖 secret-only 后台保存时订单与资产主链接、主代理链路、备用链路的同步行为。

### 验证

本地已通过：

```bash
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python -m py_compile cloud/api_orders.py cloud/tests.py
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_secret_edit_syncs_primary_asset cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_secret_edit_updates_main_link_and_proxy_links --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py check
```

## 2026-06-02 私聊同群资产续费链路修复

### 范围

本轮根据 Codex CLI 诊断，修复同一绑定群组内可见代理资产在私聊续费时仍被订单所属人限制的问题。

### 运行变更

- `ensure_cloud_asset_operation_order()` 改为使用用户资产可见性过滤，允许同群可见用户为可见资产取得操作订单。
- `create_cloud_server_renewal_for_user()` 在找不到本人订单时，会回退到同群可见资产并用该资产关联订单创建续费单。
- `list_cloud_asset_renewal_plans()` 对未绑定资产续费套餐查询复用同一套资产可见性规则。
- Bot 钱包续费回调在私聊场景下增加“我的代理”可见订单校验，保证同群可见资产生成的续费按钮可以继续支付。
- 新增同群可见资产续费回归测试，覆盖操作订单、未绑定资产套餐、订单续费创建和原订单状态变更。

### 验证

本地已通过：

```bash
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python -m py_compile bot/handlers.py cloud/services.py cloud/tests.py
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_same_bound_group_asset_renewal_uses_user_visibility cloud.tests.CloudServerServicesTestCase.test_user_proxy_asset_detail_allows_same_bound_group_visibility cloud.tests.CloudServerServicesTestCase.test_cloud_server_public_renewal_allows_stranger_payment_entry cloud.tests.CloudServerServicesTestCase.test_public_unattached_asset_renewal_plans_are_available --noinput --verbosity 1
PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py check
```

## 2026-06-02 私聊同群资产详情可见性修复

### 范围

修复 Telegram 私聊代理列表与代理详情之间的同群资产可见性不一致问题。

### 运行时变化

- `get_user_proxy_asset_detail` 改为复用代理列表同一套用户可见性过滤。
- 用户在私聊代理列表中能看到的同绑定群组资产，现在点击详情时不会再被误判为不存在。
- 仍然保留现有无效订单、删除资产、停用云账号等基础过滤条件。
- 新增回归测试覆盖同群资产在私聊列表与详情中的一致性。

### 验证

本地已通过 `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`：

```bash
uv run python -m py_compile cloud/services.py cloud/tests.py
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_user_proxy_asset_detail_allows_same_bound_group_visibility cloud.tests.CloudServerServicesTestCase.test_group_cloud_server_list_is_scoped_to_current_group cloud.tests.CloudServerServicesTestCase.test_group_auto_renew_bulk_toggle_is_scoped_to_current_group --noinput --verbosity 1
uv run python manage.py check
```

## 2026-06-02 cloud-order-secret-edit-sync

### Scope

Small dashboard sync fix for manual MTProxy secret edits on cloud orders.

### Runtime Changes

- `cloud_order_detail` now accepts a standalone non-empty `mtproxy_secret` edit.
- Manual secret edits are persisted to `CloudServerOrder` and propagated to the linked primary `CloudAsset`.
- Secret-only saves keep the existing non-empty-only behavior and do not clear stored secrets on blank payloads.
- Added a focused regression test for secret-only order edits.

### Verification

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/api_orders.py cloud/tests.py
uv run python manage.py test cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_secret_edit_syncs_primary_asset cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_previous_ip_edit_syncs_primary_records --noinput --verbosity 1
uv run python manage.py check
```

## 2026-06-02 public-asset-renewal-no-pending-owner-claim

### Scope

Small ownership safety fix for public unbound asset renewal orders.

### Runtime Changes

- Creating a pending public unbound `CloudAsset` renewal order no longer writes the payer onto `CloudAsset.user`.
- The asset is still linked to the pending renewal order to prevent duplicate checkout attempts, but ownership remains unchanged until successful recovery.
- Payment-timeout cleanup can now safely unlink the pending order without leaving an unpaid public asset claimed by the attempted payer.
- Added focused regression coverage for public renewal timeout on an unowned unattached static IP asset.

### Verification

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/services.py orders/tests.py
uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_public_asset_renewal_expiry_does_not_claim_unowned_asset orders.tests.ChainPaymentScannerTestCase.test_expired_asset_renewal_payment_unbinds_asset_for_retry cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_address_order_forces_usdt_from_trx_source --noinput --verbosity 1
uv run python manage.py check
```

## 2026-06-02 asset-renewal-expiry-retry-note

### Scope

Small payment timeout recovery fix for unbound asset renewal orders.

### Runtime Changes

- When an unbound `CloudAsset` renewal address-payment order expires, the scanner now unbinds the asset and appends a retry note to the asset.
- Existing asset notes are preserved, and the retry note is appended uniquely to avoid duplicate timeout text.
- This makes the existing retry-state expectation explicit after payment-window expiry.

### Verification

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile orders/payment_scanner.py orders/tests.py
uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_expired_asset_renewal_payment_unbinds_asset_for_retry orders.tests.ChainPaymentScannerTestCase.test_renew_pending_cloud_with_previous_ip_is_candidate --noinput --verbosity 1
uv run python manage.py check
```

## 2026-06-02 aws-sync-ip-release-order-cleanup

### Scope

Small retained static IP cleanup fix for AWS sync release handling.

### Runtime Changes

- Successful AWS sync release of an unattached static IP now reuses the lifecycle cleanup path.
- Linked deleted retained orders have stale `public_ip`, `static_ip_name`, `mtproxy_host`, and `ip_recycle_at` cleared after the AWS release succeeds.
- The released asset keeps `previous_public_ip`, clears `public_ip`, and records a single recycled IP history row linked to both the asset and order.
- Added focused regression coverage for the AWS sync release helper.

### Verification

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/tests.py
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_clears_retained_order_after_successful_release cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_shutdown_disabled_account cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_global_ip_delete_switch --noinput --verbosity 1
uv run python manage.py check
```

## 2026-06-02 cleanup-keeps-retained-ip-orders

### Scope

Small cleanup safety fix for retained static IP order history.

### Runtime Changes

- `cleanup_old_records` no longer treats every `deleted` cloud order as immediately cleanup-eligible.
- Deleted cloud orders with a future retained-IP `ip_recycle_at` are preserved until the configured retention cutoff has passed their IP recycle time.
- This keeps retained-IP renewal context and linked `CloudIpLog` history available while the static IP is still recoverable.
- Added focused regression coverage for the cleanup filter.

### Verification

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile core/management/commands/cleanup_old_records.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_deleted_order_until_retained_ip_window_ends --noinput --verbosity 1
```

## 2026-06-02 unattached-ip-release-order-cleanup

### Scope

Small retained static IP cleanup fix for manual/dashboard asset-level releases.

### Runtime Changes

- Successful unattached static IP release now also clears a linked deleted retained order's `public_ip`, `static_ip_name`, `mtproxy_host`, and `ip_recycle_at`.
- The linked order is marked as recycle-notified and has IP recycle reminders disabled after the IP is actually released.
- Recycle history logs now keep both the released `CloudAsset` and linked `CloudServerOrder`, preventing stale renewal/recycle state from remaining visible.
- Added focused regression coverage for manual retained-IP release through the dashboard helper path.

### Verification

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/lifecycle.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_manual_unattached_ip_delete_clears_retained_order_after_successful_release cloud.tests.CloudServerServicesTestCase.test_manual_unattached_ip_delete_writes_log_and_history_item --noinput --verbosity 1
```

## 2026-06-02 retained-ip-real-release-history

### Scope

Small lifecycle-plan history display fix for AWS retained static IP releases.

### Runtime Changes

- Lifecycle IP-delete history now recognizes `AWS 固定 IP 已真实释放` log notes as completed retained-IP release records.
- Released retained static IPs can appear in lifecycle plan history even when no active lifecycle-plan row existed before the release.
- Added focused regression coverage for a real-release history row rebuilt from `CloudIpLog`.

### Verification

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile bot/api.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_real_released_retained_ip_history_without_active_row cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_ip_delete_history_item cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_move_deleted_unattached_ip_active_row_to_history --noinput --verbosity 1
```

## 2026-06-02 aws-retained-ip-missing-skip

### Scope

Small AWS retained static IP sync consistency fix.

### Runtime Changes

- AWS missing-instance verification now treats `固定IP仍存在但未附加` and `固定IP保留中` provider states as static-IP-backed assets.
- A retained static IP that still exists remotely will not be moved into missing-confirmation state just because the old instance id no longer appears.
- Added focused regression coverage for the retained-IP preservation path followed by missing verification in the same sync cycle.

### Verification

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_aws_retained_ip_preserves_existing_asset_user cloud.tests.CloudServerServicesTestCase.test_aws_retained_unattached_asset_is_not_missing_deleted_when_static_ip_exists --noinput --verbosity 1
```

## 2026-06-02 sync-user-binding-persist-false

### Scope

Small cloud sync ownership binding fix.

### Runtime Changes

- `sync_cloud_asset_user_binding(..., persist=False)` now updates the in-memory `CloudAsset.user` / `user_id` fields without issuing its own database write.
- AWS and Aliyun sync paths that call the helper before `asset.save()` can now fill blank asset owners while still preserving existing owners.
- Added focused regression coverage that `persist=False` mutates only the Python object until the caller saves.

### Verification

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/services.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_user_binding_uses_asset_name_tg_id cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_user_binding_persist_false_sets_in_memory_user cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry --noinput --verbosity 1
```

## 2026-06-02 early-provisioning-asset-field-preservation

### Scope

Small follow-up to the provisioning asset preservation pass.

### Runtime Changes

- Early provisioning asset writes now preserve existing asset owner, expiry, MTProxy link, secret, host, port, proxy-link list, price, and currency when updating an existing asset.
- `_upsert_server_asset()` and early provisioning helpers share the same default-value preservation helper.
- Added focused regression coverage for `_mark_provisioning_start()` and `_mark_instance_created()` so manual asset fields are not clobbered before final success handling.

### Verification

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/provisioning.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_mark_success_preserves_existing_manual_asset_fields_on_update cloud.tests.CloudServerServicesTestCase.test_early_provisioning_steps_preserve_existing_manual_asset_fields --noinput --verbosity 1
```

## 2026-06-02 provisioning-asset-field-preservation

### Scope

Small provisioning write-path safety pass for existing cloud assets.

### Runtime Changes

- `_mark_success` no longer runs a duplicate `CloudAsset.update_or_create()` before the shared asset upsert helper.
- `_upsert_server_asset()` now preserves existing asset owner, expiry, MTProxy link, secret, host, port, and proxy-link list when updating an existing asset, while still filling blank fields from the order.
- New asset creation still receives order runtime fields including MTProxy data, price, and currency.
- Added focused regression coverage that provisioning success does not duplicate assets or overwrite existing manual asset fields.

### Verification

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/provisioning.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_mark_success_updates_existing_server_asset_instead_of_creating_duplicate cloud.tests.CloudServerServicesTestCase.test_mark_success_preserves_existing_manual_asset_fields_on_update cloud.tests.CloudServerServicesTestCase.test_asset_renewal_mark_success_starts_new_service_period --noinput --verbosity 1
```

## 2026-06-02 mtproxy-link-write-consistency

### Scope

Small dashboard write-path safety pass for MTProxy link edits.

### Runtime Changes

- Dashboard cloud order edits now parse submitted `mtproxy_link` values and keep `mtproxy_secret`, host, port, and `proxy_links` aligned with the main link.
- Dashboard cloud asset edits now apply the same main-link normalization to both the asset and its linked order.
- Main-link replacement removes stale `主代理` / `主链路` entries from `proxy_links` so old secrets are not copied after a manual link edit.
- Added focused regression coverage for order detail edits and asset edits that update MTProxy links.

### Verification

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/api_orders.py cloud/api_asset_edit.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_blank_mtproxy_secret_preserves_existing_secret cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_mtproxy_link_refreshes_secret_and_proxy_links cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields --noinput --verbosity 1
```

## 2026-06-02 cloud-sync-manual-field-preservation

### Scope

Small sync safety pass for retained asset ownership and expiry preservation.

### Runtime Changes

- AWS retained-IP sync no longer overwrites an existing `CloudAsset.user` when attaching a retained order to an orderless asset.
- Aliyun sync no longer overwrites an existing `CloudAsset.actual_expires_at` on already tracked assets.
- Empty asset owners are still backfilled from the retained order when appropriate.
- Added focused regression coverage for AWS retained-IP owner preservation and Aliyun retained-asset expiry preservation.

### Verification

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python manage.py check
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/tests.py
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_aws_retained_ip_preserves_existing_asset_user cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_updates_retained_asset_after_renewal_recovery --noinput --verbosity 1
```

## 2026-06-02 cloud-asset-payload-readonly-guard

### Scope

Removed a read-path side effect from cloud asset payload building.

### Runtime Changes

- `CloudAssetPayloadContext` now defaults to read-only payload rendering.
- Cloud asset GET/detail payloads no longer auto-write `CloudAsset.user` or `CloudAsset.actual_expires_at` while computing display data.
- Added a regression test covering the read-only asset payload path.

### Verification

Passed locally with `UV_CACHE_DIR=/private/tmp/shop-uv-cache`:

```bash
uv run python manage.py check
uv run python -m py_compile cloud/api_assets.py cloud/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_asset_get_payload_does_not_mutate_manual_asset_fields cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_user_binding_uses_asset_name_tg_id --noinput --verbosity 1
```

## 2026-06-02 trongrid-api-key-secret-preservation

### Scope

Small sensitive-config hardening pass after the runtime-field preservation guard.

### Runtime Changes

- Treat `trongrid_api_key` as a sensitive site config key.
- Blank dashboard saves for `trongrid_api_key` now preserve the existing API keys instead of clearing them.
- Dashboard config responses no longer return the full TRON API key list in `value_preview`.
- Added focused regression coverage for blank TRON API key saves and response masking.

### Verification

Passed locally with `UV_CACHE_DIR=/private/tmp/shop-uv-cache`:

```bash
uv run python manage.py check
uv run python -m py_compile core/runtime_config.py bot/api_site_configs.py bot/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.DashboardAuthSurfaceTestCase.test_sensitive_site_config_blank_value_preserves_existing_secret bot.tests.DashboardAuthSurfaceTestCase.test_trongrid_api_key_blank_value_preserves_and_masks_existing_secret --noinput --verbosity 1
```

## 2026-06-02 runtime-field-preservation-guard

### Scope

Small safety pass after the backend refactor to guard runtime ownership, expiry, and sensitive-field persistence.

### Runtime Changes

- Dashboard cloud order edits no longer reverse-sync `CloudServerOrder.user` or `service_expires_at` into `CloudAsset.user` / `actual_expires_at`.
- Dashboard cloud asset edits preserve existing `mtproxy_secret` when the submitted value is blank.
- Sensitive site config updates preserve the existing value when the submitted value is blank.
- Order primary-record updates now apply cloud identity, status, and proxy-field changes to all server-like `CloudAsset` records tied to the same order, while still preserving manual owner and expiry fields.
- Added focused regression coverage for blank sensitive config saves, blank MTProxy secret saves, order expiry edits, and multi-record order detail sync.

### Verification

Passed locally with `UV_CACHE_DIR=/private/tmp/shop-uv-cache`:

```bash
uv run python manage.py check
uv run python -m py_compile bot/api.py bot/handlers.py cloud/services.py cloud/bootstrap.py cloud/api.py cloud/api_asset_edit.py cloud/sync_jobs.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/process_cloud_asset_sync_jobs.py orders/payment_scanner.py orders/tron_parser.py
uv run python -m py_compile bot/api_site_configs.py cloud/api_orders.py
uv run python -m py_compile bot/tests.py cloud/tests.py cloud/services.py
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test bot.tests.DashboardAuthSurfaceTestCase.test_sensitive_site_config_blank_value_preserves_existing_secret cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_blank_mtproxy_secret_preserves_existing_secret cloud.tests.CloudServerServicesTestCase.test_dashboard_order_expiry_update_recomputes_lifecycle_plan cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields --keepdb --noinput --verbosity 1
```

## 2026-06-01 task-center-and-monitor-split

### Scope

This pass kept splitting the oversized cloud API surface, added a unified task center API, and moved monitor/IP-log endpoints into a dedicated module.

### Runtime Changes

- Added `cloud/api_monitors.py` for cloud IP logs and address monitor APIs.
- Added `cloud/task_center.py` for a unified backend task center overview.
- `cloud/api.py` now re-exports the monitor APIs and task center API for URL compatibility.
- Added `GET /admin/tasks/center/` and kept `GET /admin/tasks/` as the legacy task list.
- Added a refactor worktree boundary document so future passes can distinguish owned edits from existing dirty files.

### Frontend Changes

- Upgraded `/admin/tasks` into a task center page with health cards and a searchable task table.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/api.py cloud/api_monitors.py cloud/task_center.py shop/dashboard_urls.py
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests_task_center.CloudTaskCenterApiTestCase --keepdb --noinput --verbosity 1
```

Frontend validation passed in `/Users/a399/Desktop/data/vue-shop-admin`:

```bash
./node_modules/.bin/vue-tsc --noEmit --skipLibCheck -p apps/web-antd/tsconfig.json
```

## 2026-06-01 cloud-sync-runtime-split

### Scope

This refactor split cloud asset sync execution out of `cloud/api.py` and made sync jobs easier to operate, observe, and clean up.

### Runtime Changes

- Added `cloud/sync_jobs.py` as the cloud asset sync job runtime module.
- `cloud/api.py` now keeps cloud asset/order/dashboard API logic and re-exports sync job endpoints for existing dashboard URL aggregation.
- `process_cloud_asset_sync_jobs` imports execution helpers from `cloud.sync_jobs`, no longer from `cloud.api`.
- Bulk sync job subtasks now run serially instead of using a thread pool, so progress updates, event ordering, heartbeat, and cancellation are deterministic.
- Added `cloud_asset_sync_jobs_metrics` API at `cloud-assets/sync-jobs/metrics/`.
- `cloud_assets_sync_status` now embeds the same metrics summary used by the frontend.
- Added `prune_cloud_sync_job_events` for event-table cleanup by age and per-job retention.

### Frontend Changes

- Added `/admin/cloud-sync-jobs/:id` as a dedicated sync job detail page in `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd`.
- Proxy list sync drawer now shows task metrics and links each job row to the detail page.
- Frontend API types now include `DashboardCloudAssetSyncJobsMetrics`.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/api.py cloud/sync_jobs.py cloud/management/commands/process_cloud_asset_sync_jobs.py cloud/management/commands/prune_cloud_sync_job_events.py shop/dashboard_urls.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_runs_enabled_accounts_and_merges_results cloud.tests.CloudServerServicesTestCase.test_cloud_asset_sync_jobs_metrics_returns_operational_summary cloud.tests.CloudServerServicesTestCase.test_cancel_queued_cloud_asset_sync_job_marks_terminal_and_events cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_with_selected_assets_uses_asset_scoped_tasks cloud.tests.CloudServerServicesTestCase.test_process_cloud_asset_sync_jobs_worker_processes_queued_job --keepdb --noinput --verbosity 1
```

Frontend validation passed in `/Users/a399/Desktop/data/vue-shop-admin`:

```bash
./node_modules/.bin/vue-tsc --noEmit --skipLibCheck -p apps/web-antd/tsconfig.json
```

## 2026-06-01 cloud-asset-lifecycle-refactor

### Scope

This version is an aggressive backend refactor around cloud asset lifecycle, table ownership, and runtime dependency cleanup.

### Database Changes

- `cloud_server` physical table was removed.
- Historical server data was migrated into `cloud_asset`.
- `cloud_asset` is now the only cloud resource fact table.
- `CloudIpLog.server` / `cloud_ip_log.server_id` was removed.
- Django migration chain:
  - `0037_server_table_to_cloud_asset`
  - `0038_drop_server_model_and_iplog_server`

### Runtime Model Direction

- `CloudAsset(kind='server')` is the canonical server asset record.
- `CloudServerOrder` is business context for purchase, renewal, migration, rebuild, deletion, and audit.
- `Server` is no longer a Django model. A small import compatibility facade remains in `cloud.models` so older scripts/tests do not fail immediately on import, but new runtime code should not use it.

### Lifecycle Refactor

- Added `cloud/lifecycle_schedule.py`:
  - central lifecycle time calculation
  - order schedule fields
  - orphan asset delete time
  - unattached static IP release time
  - runtime config helpers
- Added `cloud/lifecycle_execution.py`:
  - scheduled/manual shutdown
  - delete order
  - delete migrated/replaced order
  - delete orphan asset
  - release retained static IP
  - release unattached static IP
  - cloud API timeout handling
- `cloud/lifecycle.py` now scans due work and dispatches to execution helpers.

### Runtime Dependency Cleanup

- `cloud/services.py` now writes primary record updates to `CloudAsset`.
- `cloud/provisioning.py` no longer creates/upserts `Server` rows; provisioning writes `CloudAsset`.
- `cloud/api.py` keeps server endpoint names for compatibility but queries `CloudAsset(kind='server')`.
- `bot/api.py` no longer syncs notes to `Server`.
- `record_cloud_ip_log` records asset/order context only.

### Documentation Updated

- `ARCHITECTURE.md`
- `docs/DATA_FLOW_AND_PERSISTENCE.md`
- `docs/DB_NAMING_CONVENTIONS.md`
- `docs/refactor-mapping.md`
- `docs/table-rename-plan.md`
- `docs/project-overview.md`

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/models.py cloud/services.py cloud/lifecycle.py cloud/provisioning.py cloud/api.py bot/api.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check --verbosity 2
uv run python manage.py migrate --plan
uv run python manage.py migrate cloud 0038
```

Local database probe after migration:

- `cloud_server_exists`: `False`
- `cloud_ip_log.server_id`: removed
- Django registered `cloud.Server` model: `None`

### Known Follow-up

- Some tests and compatibility management commands still reference the `Server` facade.
- `sync_aws_assets.py` and `sync_aliyun_assets.py` still need a deeper pass to rename local variables and remove old wording, although the `Server` facade currently routes writes to `CloudAsset`.
- Full Django tests are still blocked locally by MySQL test database permission:

```sql
GRANT ALL PRIVILEGES ON test_a.* TO 'a'@'localhost';
FLUSH PRIVILEGES;
```

## 2026-06-01 cloud-asset-runtime-cleanup

### Scope

Second refactor pass after the table migration. This pass removes the `Server` compatibility facade from `cloud.models`, moves old command/test compatibility to an explicit command-side wrapper, and adds indexes/state helpers.

### Runtime Changes

- Removed `Server` from `cloud.models` and `__all__`.
- Added `cloud/server_records.py` as an explicit compatibility wrapper over `CloudAsset(kind='server')` for legacy commands and tests.
- Updated sync and maintenance commands to import `Server` from `cloud.server_records`, not from `cloud.models`.
- Added `cloud/lifecycle_state.py` for order-status to asset-status mapping.
- `cloud/api.py` now uses `primary_record_updates_for_order_status` from `cloud.lifecycle_state`.

### Database Changes

- Added `0039_cloud_asset_indexes`:
  - `ca_kind_status_active_idx`
  - `ca_provider_acct_inst_idx`
  - `ca_provider_acct_ip_idx`
  - `ca_order_status_idx`
  - `ca_kind_user_status_idx`

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/lifecycle_state.py cloud/models.py cloud/api.py cloud/server_records.py
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/upsert_cloud_asset.py cloud/management/commands/dedupe_servers.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check --verbosity 2
uv run python manage.py migrate cloud 0039
uv run python manage.py migrate --plan
```

### Remaining Big Refactors

- Physically split `cloud/api.py`.
- Physically split `bot/api.py`.
- Rename legacy server wording inside sync commands and tests from `Server` to `CloudAsset` once test coverage is adjusted.

## 2026-06-01 cloud-dashboard-api-split

### Scope

Third refactor pass focused on shrinking the oversized dashboard cloud API module while preserving existing URL imports.

### Runtime Changes

- Added `cloud/api_servers.py` for server-shaped `CloudAsset(kind='server')` dashboard endpoints:
  - server list payloads
  - server rebuild preserve-link action
  - server delete action
  - server statistics
- Added `cloud/api_plans.py` for cloud plan/pricing dashboard endpoints:
  - provider pricing list
  - custom cloud plan list
  - plan create/update/delete
- `cloud/api.py` now imports these endpoint names at the bottom as compatibility exports, so `shop/dashboard_urls.py` can continue using `cloud_api.<view_name>`.

### Cleanup

- Removed remaining runtime writes to the retired `server` variable inside `update_cloud_asset`.
- Removed removed ORM paths:
  - `order__server__server_name`
  - `order__server__note`
  - `CloudIpLog.select_related('server')`
  - `Q(server__isnull=False)`

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/api.py cloud/api_servers.py cloud/api_plans.py
uv run python manage.py check
```

## 2026-06-01 bot-product-api-split

### Scope

Fourth refactor pass started splitting the oversized `bot/api.py` dashboard module.

### Runtime Changes

- Added `bot/api_products.py` for product dashboard endpoints:
  - product list
  - product create
  - product update
- `bot/api.py` keeps compatibility exports for `products_list`, `create_product`, and `update_product`, so existing dashboard URL imports continue to work.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_products.py
uv run python manage.py check
```

## 2026-06-01 bot-admin-api-split

### Scope

Fifth refactor pass continued splitting `bot/api.py` by moving admin account management endpoints.

### Runtime Changes

- Added `bot/api_admin_users.py` for dashboard admin account endpoints:
  - admin user list
  - admin create/update/delete
  - current admin password change
- `bot/api.py` keeps compatibility exports for the moved endpoints, so `shop/dashboard_urls.py` continues resolving the same attributes.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_admin_users.py bot/api_products.py
uv run python manage.py check
```

## 2026-06-01 bot-site-config-api-split

### Scope

Sixth refactor pass moved site configuration and button/text configuration dashboard endpoints out of `bot/api.py`.

### Runtime Changes

- Added `bot/api_site_configs.py` for:
  - site config list/group/update/init
  - text config initialization
  - button config read/update/init
  - daily expiry summary notification test
- Preserved compatibility exports from `bot/api.py` for the moved view names and private payload helpers.
- Removed now-unused config/text/button imports from `bot/api.py`.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_site_configs.py
uv run python manage.py check
```

## 2026-06-01 bot-cloud-account-api-split

### Scope

Seventh refactor pass moved cloud account dashboard management out of `bot/api.py`.

### Runtime Changes

- Added `bot/api_cloud_accounts.py` for:
  - cloud account list/detail
  - create/update/delete
  - AWS and Alibaba Cloud account verification
  - cloud account payloads, duplicate detection, external sync log payloads
- Preserved compatibility exports from `bot/api.py` for moved public views and private helper names.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_cloud_accounts.py bot/api_site_configs.py
uv run python manage.py check
```

## 2026-06-01 bot-auth-api-split

### Scope

Eighth refactor pass moved dashboard authentication and current-user endpoints out of `bot/api.py`.

### Runtime Changes

- Added `bot/api_auth.py` for:
  - login/logout/refresh
  - auth code list
  - TOTP start/bind
  - user info and current user metadata
- Preserved compatibility exports from `bot/api.py` for all moved auth view names.
- Removed unused `authenticate`, `login`, and `logout` imports from `bot/api.py`.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_auth.py bot/api_cloud_accounts.py bot/api_site_configs.py
uv run python manage.py check
```

## 2026-06-01 bot-user-balance-api-split

### Scope

Ninth refactor pass moved Telegram user listing and balance management endpoints out of `bot/api.py`.

### Runtime Changes

- Added `bot/api_users.py` for:
  - user list
  - manual USDT/TRX balance update
  - cloud discount update
  - user balance detail timeline
  - balance ledger payload and manual ledger recording helpers
- Preserved compatibility exports from `bot/api.py` for moved public views and private ledger helper names.
- Removed unused balance/query imports from `bot/api.py`.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_users.py
uv run python manage.py check
```

## 2026-06-01 bot-operation-log-api-split

### Scope

Tenth refactor pass moved bot operation log dashboard endpoints out of `bot/api.py`.

### Runtime Changes

- Added `bot/api_operation_logs.py` for operation log payloads and search/list view.
- Preserved compatibility exports from `bot/api.py`.
- Removed the now-unused `BotOperationLog` import from `bot/api.py`.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_operation_logs.py
uv run python manage.py check
```

## 2026-06-01 bot-telegram-api-split

### Scope

Eleventh refactor pass moved Telegram dashboard login, chat, message, and group-filter endpoints out of `bot/api.py`.

### Runtime Changes

- Added `bot/api_telegram.py` for:
  - Telegram account overview
  - personal account login/code/password/status flows
  - account notification toggles
  - group filter list/detail/create/update
  - chat message send/archive/list
  - Telegram payload and validation helpers
- Preserved compatibility exports from `bot/api.py` for moved public views and private helper names.
- Removed Telegram-specific model/service imports from `bot/api.py`.

### Verification

Passed locally:

```bash
uv run python -m py_compile bot/api.py bot/api_telegram.py
uv run python manage.py check
```

## 2026-06-01 dashboard-api-core-extraction

### Scope

Twelfth refactor pass addressed the cross-domain coupling where cloud and orders dashboard APIs imported private helpers from `bot/api.py`.

### Runtime Changes

- Added `core/dashboard_api.py` as the shared dashboard API utility module.
- Moved generic helpers into core:
  - response helpers: `_ok`, `_error`
  - formatting helpers: `_iso`, `_decimal_to_str`, `_parse_decimal`
  - request/query helpers: `_read_payload`, `_get_keyword`, `_apply_keyword_filter`
  - payload/label helpers: `_split_usernames`, `_user_payload`, `_status_label`, `_days_left`, `_countdown_label`, `_provider_label`, `_provider_status_label`, `_region_label`, `_server_source_label`
  - dashboard session/auth helpers and decorators
- `bot/api.py` now re-exports those helpers for compatibility.
- `cloud/api.py`, `cloud/api_servers.py`, `cloud/api_plans.py`, and `orders/api.py` import shared helpers/decorators from `core.dashboard_api`, removing their `bot.api` helper dependency.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/dashboard_api.py bot/api.py bot/api_auth.py bot/api_users.py bot/api_operation_logs.py bot/api_cloud_accounts.py bot/api_site_configs.py bot/api_admin_users.py bot/api_products.py bot/api_telegram.py cloud/api.py cloud/api_servers.py cloud/api_plans.py orders/api.py
uv run python manage.py check
```

## 2026-06-01 provisioning-structured-result-logging

### Scope

Thirteenth refactor pass removed production `print('[PROVISION_RESULT]', ...)` calls from cloud provisioning.

### Runtime Changes

- Added `_log_provision_result()` in `cloud/provisioning.py`.
- Replaced all provision result `print()` calls with `logger.log(...)`.
- Provision result logs now include structured `extra={'provision_result': ...}` fields.
- MTProxy links are logged as masked previews instead of full raw links in the result payload.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/provisioning.py core/dashboard_api.py bot/api.py cloud/api.py cloud/api_servers.py cloud/api_plans.py orders/api.py
uv run python manage.py check
```

## 2026-06-01 cloud-dashboard-api-helper-extraction

### Scope

Fourteenth refactor pass reduced reverse dependencies where split cloud dashboard API modules treated `cloud/api.py` as a shared helper library.

### Runtime Changes

- Added `cloud/dashboard_snapshots.py` as the single dashboard snapshot refresh coordinator.
- `cloud/services.py` and `cloud/lifecycle.py` now refresh dashboard snapshots through `cloud.dashboard_snapshots` instead of importing `cloud.api`.
- Added `cloud/dashboard_api_helpers.py` for cloud dashboard display helpers:
  - cloud plan config id generation
  - preserve-link status labels
  - dashboard sort direction and expiry ordering
- `cloud/api_servers.py` and `cloud/api_plans.py` no longer import `cloud.api` through `_api_helpers()`.
- Moved rebuild background retry execution from `cloud/api.py` into `cloud/services.py` as `run_cloud_server_rebuild_job()`.
- Kept `cloud/api.py` importing the extracted helper names so existing internal references and compatibility imports continue to work.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/dashboard_api_helpers.py cloud/dashboard_snapshots.py cloud/api.py cloud/api_servers.py cloud/api_plans.py cloud/services.py cloud/lifecycle.py
uv run python manage.py check
```

## 2026-06-01 async-runtime-config-fix

### Scope

Fifteenth refactor pass addressed the P0 issue where `get_runtime_config()` returns env/default values in a running async event loop and can miss updated `SiteConfig` values.

### Runtime Changes

- Replaced async runtime config reads in `bot/runner.py`, `bot/handlers.py`, and `cloud/resource_monitor.py` with `await core.cache.get_config(...)`.
- Removed `asyncio.to_thread(get_runtime_config, ...)` and `sync_to_async(get_runtime_config, ...)` usage from async runtime paths.
- Refactored `core/cache.py:get_config()` so sync DB/default fallback happens inside a dedicated thread helper.
- Verified there are no remaining direct `get_runtime_config()` calls inside `async def` bodies, and no remaining `to_thread/sync_to_async(get_runtime_config)` adapters.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/cache.py core/runtime_config.py bot/runner.py bot/handlers.py cloud/resource_monitor.py cloud/dashboard_api_helpers.py cloud/dashboard_snapshots.py cloud/api.py cloud/api_servers.py cloud/api_plans.py cloud/services.py cloud/lifecycle.py
uv run python manage.py check
```

## 2026-06-01 dashboard-bearer-write-auth

### Scope

Sixteenth refactor pass addressed the CSRF/auth boundary risk where csrf-exempt dashboard write APIs could still authenticate through cookie session state.

### Runtime Changes

- `core/dashboard_api.py` now treats unsafe dashboard methods (`POST`, `PUT`, `PATCH`, `DELETE`, etc.) as bearer-only.
- Dashboard write requests must provide `Authorization: Bearer session-...`; cookie-authenticated `request.user` alone is no longer accepted for write views.
- Safe read methods still support existing cookie/session authentication for compatibility.
- Updated dashboard auth tests so write tests attach explicit bearer session headers.
- Added a regression test proving cookie-only dashboard writes are rejected with 401.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/dashboard_api.py bot/tests.py bot/api_auth.py bot/api.py bot/api_admin_users.py
uv run python manage.py check
```

Blocked locally:

```bash
uv run python manage.py test bot.tests.DashboardSessionExpiryTestCase bot.tests.DashboardAuthSurfaceTestCase --keepdb
```

The focused test run is blocked by local MySQL test database permissions:

```text
Access denied for user 'a'@'localhost' to database 'test_a'
```

## 2026-06-02 开通日志测试夹具补强

### 范围

第二十一轮监工复查自动化补丁，确认新增开通日志回归测试需要完整订单夹具，避免测试订单缺少用户和套餐关联时掩盖真实断言。

### 测试变更

- `cloud/tests.py` 的开通结果日志缓存测试补齐 `user` 和 `plan`。
- `cloud/tests.py` 的代理密钥脱敏测试补齐 `user` 和 `plan`。
- 保持测试代码只在测试文件中，不改动运行逻辑。

### 验证命令

已通过：

```bash
uv run python -m py_compile cloud/tests.py cloud/provisioning.py cloud/services.py cloud/lifecycle.py cloud/lifecycle_execution.py
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

受本地 MySQL 权限阻塞：

```bash
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_provision_result_log_uses_cached_asset_expiry cloud.tests.CloudServerServicesTestCase.test_provision_result_log_masks_proxy_secrets --keepdb
```

阻塞原因：

```text
Access denied for user 'a'@'localhost' to database 'test_a'
```

## 2026-06-01 cloud-sync-structured-state

### Scope

Twentieth refactor pass replaced the cloud missing-delete confirmation marker text with structured cloud asset sync state.

### Runtime Changes

- Added `CloudAsset.sync_state` JSON field and migration `0040_cloudasset_sync_state`.
- `cloud/sync_safety.py` now treats `sync_state['missing_confirmation']` as the source of truth.
- Removed parsing and writing of legacy `[missing_sync_count:...]` / `[msc_at:...]` provider-status markers.
- AWS and Alibaba Cloud missing-resource sync now:
  - increments structured confirmation count on each missing pass
  - keeps the asset/server running while count is below threshold
  - deletes only after the structured count reaches the configured threshold
  - clears missing confirmation state when a later sync sees the resource live again
- Dashboard lifecycle/delete-plan views now read confirmation progress from item/asset `sync_state`.
- Updated affected tests to assert structured `sync_state` instead of provider-status marker text.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/sync_safety.py cloud/models.py cloud/migrations/0040_cloudasset_sync_state.py cloud/server_records.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py bot/api.py cloud/tests.py
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py check
```

Blocked locally:

```bash
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_missing_confirmation_note_preserves_existing_note cloud.tests.CloudServerServicesTestCase.test_sync_missing_confirmation_requires_interval cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_expose_missing_confirmation_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_unattached_ip_show_confirmation_progress_in_state_and_note cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_instance_requires_five_passes_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_missing_instance_requires_five_passes_before_delete --keepdb
```

The focused DB test run is blocked by local MySQL test database permissions:

```text
Access denied for user 'a'@'localhost' to database 'test_a'
```

## 2026-06-02 实机开通删除回归修复

### 范围

第二十轮重构验证使用后台新增的 AWS 云账号执行真实创建、初始化、删机和固定 IP 释放。实机过程中发现开通保存成功后，结果日志在异步上下文里再次同步查询资产到期时间，导致订单被错误标记为失败。

### 运行变更

- `cloud/provisioning.py` 的开通结果日志改为读取同步保存阶段写入的 `_asset_expires_at` 缓存，不再在异步开通流程返回后隐式查询 `CloudAsset`。
- `_mark_success()` 和 `_mark_failed()` 返回订单前都会附带资产到期时间缓存，成功、失败日志统一使用这一份缓存值。
- 开通、重试初始化和失败日志里的代理链接、`secret`、SOCKS5 凭据统一脱敏，避免实机输出泄漏代理密钥。
- 新增聚焦回归测试，确保开通结果日志不会再次调用资产到期时间查询函数。

### 实机验证

- AWS Lightsail 新加坡区真实创建测试实例成功，订单号 `SRV20260602101856384117`，实例名 `20260602-990000000001-5-o75`。
- 复用同一台测试实例执行重试初始化，订单成功回写为 `completed`，资产到期时间保持为 `2026-07-03 10:22:05 UTC`。
- 手动打开本地删除开关后，业务删机入口真实删除实例成功。
- 固定 IP 释放入口真实释放固定 IP 成功。
- AWS 端复查实例和固定 IP 均返回不存在，本地订单和资产均已进入删除/回收完成状态。

### 验证命令

已通过：

```bash
uv run python -m py_compile cloud/provisioning.py cloud/tests.py
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

受本地 MySQL 权限阻塞：

```bash
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_provision_result_log_uses_cached_asset_expiry
```

阻塞原因：

```text
Access denied for user 'a'@'localhost' to database 'test_a'
```

## 2026-06-02 生产测试边界清理

### 范围

本轮复查功能代码和测试代码是否混在一起，重点检查生产模块中是否存在测试类、测试函数、Mock、patch、RequestFactory、TestCase、真机测试标记，以及测试文件是否通过生产聚合层保留旧测试入口。

### 变更

- 删除 `cloud/api.py` 中只被旧测试引用的 `_run_rebuild_job` 兼容函数。
- 移除该兼容函数带来的 `logging`、`async_to_sync`、`cloud.lifecycle`、`cloud.provisioning` 冗余导入。
- `cloud/tests.py` 中的重建任务测试改为直接调用 `cloud.services.run_cloud_server_rebuild_job`，并将 patch 目标改为真实服务函数实际导入的 `cloud.provisioning.provision_cloud_server`。
- 保留后台“每日到期汇总测试通知”接口；该处是人工触发通知发送的真实功能，不属于测试代码混入生产模块。

### 验证

已通过：

```bash
rg -n "\\b(TestCase|SimpleTestCase|TransactionTestCase|APITestCase|RequestFactory|AsyncMock|MagicMock|Mock|patch\\(|pytest|unittest|def test_|class (Fake|Dummy|Stub)|真机测试|测试用例|仅测试|for test|test only|older tests/imports)\\b" cloud bot orders core shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py' --glob '!**/test_*.py' -S
rg -n "_run_rebuild_job|older tests/imports|cloud\\.api\\.provision_cloud_server|cloud\\.api\\._delete_instance|cloud\\.api\\._mark_replaced_order_deleted" cloud bot orders core shop --glob '!**/migrations/**' -S
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/api.py cloud/tests.py cloud/services.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_rebuild_job_keeps_old_instance_until_migration_due
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
```

## 2026-06-02 快照刷新聚合层依赖清理

### 范围

终端版 Codex 复查当前提交时指出 `cloud/dashboard_snapshots.py` 仍通过 `cloud.api` 聚合层调用快照刷新、自动续费计划和通知计划构建逻辑。该路径会被同步、生命周期和服务模块调用，属于生产代码重新依赖兼容聚合层。

### 变更

- `cloud/dashboard_snapshots.py` 改为局部导入真实模块：
  - `cloud.api_asset_snapshots.refresh_cloud_asset_dashboard_snapshots`
  - `cloud.api_tasks._build_auto_renew_plan_items`
  - `cloud.api_tasks._build_notice_plan_bundle`
- `DEVELOPMENT.md` 和 `docs/project-overview.md` 修正当前架构说明：`cloud/api.py` 只保留兼容导出，不再作为运行时、管理命令或测试替换入口。

### 验证

已通过：

```bash
rg -n "from cloud import api(\\s|$)|import cloud\\.api|from cloud\\.api import|cloud_api|cloud\\.api\\..*patch|测试 patch|patch/import|re-export|兼容导出和测试" cloud bot orders core shop DEVELOPMENT.md docs/project-overview.md --glob '!**/migrations/**' -S
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/dashboard_snapshots.py cloud/api_asset_snapshots.py cloud/api_tasks.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_defers_snapshot_refresh cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_tasks_executes_due_retry_and_fallback_queue cloud.tests.CloudServerServicesTestCase.test_refresh_notice_plan_view_api_builds_notice_plan_view
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
```

## 2026-06-02 生产测试覆盖钩子清理

### 范围

继续复查功能代码和测试代码边界，重点处理生产模块中为了兼容测试 patch `cloud.api` 聚合层而保留的 `_cloud_api_override()`。

### 变更

- 移除 `cloud/api_assets.py`、`cloud/api_asset_snapshots.py`、`cloud/api_asset_edit.py`、`cloud/api_sync.py`、`cloud/api_monitors.py`、`cloud/api_tasks.py` 中的 `_cloud_api_override()`。
- 生产模块改为直接调用真实依赖，不再回看 `cloud.api` 聚合层。
- 两个管理命令改为从真实模块导入，不再依赖 `cloud.api` 聚合层。
- 测试文件改为从真实模块导入 API，并将 patch 目标调整到真实模块路径。

### 验证

已通过：

```bash
rg -n "def _cloud_api_override|_cloud_api_override\\(|from cloud\\.api import|import cloud\\.api|cloud\\.api\\." cloud bot orders core shop --glob '!**/migrations/**' -S
rg -n "\\b(TestCase|SimpleTestCase|TransactionTestCase|APITestCase|RequestFactory|AsyncMock|MagicMock|Mock|patch\\(|pytest|unittest|def test_|真机测试|测试用例|仅测试|for test|test only|older tests/imports)\\b" cloud bot orders core shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py' --glob '!**/test_*.py' -S
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/api_sync.py cloud/api_asset_snapshots.py cloud/api_assets.py cloud/api_asset_edit.py cloud/api_monitors.py cloud/api_tasks.py cloud/tests.py cloud/management/commands/refresh_cloud_asset_dashboard_snapshots.py cloud/management/commands/refresh_notice_plans.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_uses_bulk_order_inference_without_per_asset_fallback cloud.tests.CloudServerServicesTestCase.test_cloud_asset_dashboard_snapshot_refresh_materializes_paginated_list cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_defers_snapshot_refresh cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_status_uses_asset_scope cloud.tests.CloudServerServicesTestCase.test_sync_retained_ip_asset_uses_order_account_and_static_ip_scope cloud.tests.CloudServerServicesTestCase.test_auto_renew_task_detail_includes_due_retry_and_fallback_items cloud.tests.CloudServerServicesTestCase.test_auto_renew_detail_keeps_valid_order_without_asset cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_tasks_executes_due_retry_and_fallback_queue cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_order_executes_single_order cloud.tests.CloudServerServicesTestCase.test_dashboard_asset_order_inference_scopes_duplicate_ip_by_account cloud.tests.DashboardTronBalanceQueryTestCase.test_fetch_address_chain_balances_uses_resolved_headers
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
```

## 2026-06-02 生产测试覆盖钩子监工复查

### 范围

本轮在最新提交 `d618846` 上继续复查生产测试覆盖钩子清理结果，重点确认测试直连真实模块后没有恢复 `cloud.api` 聚合层 patch 入口，云资产生命周期仍以 `CloudAsset.actual_expires_at` 作为唯一结构化服务到期事实。

### 结论

- 未发现 `_cloud_api_override()`、`cloud.api.*` 测试覆盖钩子、旧 `older tests/imports` 兼容注释或生产模块测试混入回流。
- 未发现 `CloudServerOrder.service_expires_at` 运行时 ORM 写入、`normalize_service_expiry`、`service_expired_at`、旧计划快照模型或退款旧入口回流。
- `shop/settings.py` 的 `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 当前运行域，未恢复废弃 app。
- `service_expires_at` 运行时代码命中均为 API 兼容字段、日志字段或从 `order_asset_expiry()` / `CloudAsset.actual_expires_at` 派生的展示值；订单编辑接口仍显式丢弃订单表旧字段并只写资产到期事实。
- `cloud/tests.py` 中唯一 `service_expires_at=` 是负向回归用例，用于确认订单模型拒绝旧字段。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/api.py cloud/api_asset_edit.py cloud/api_asset_snapshots.py cloud/api_assets.py cloud/api_monitors.py cloud/api_orders.py cloud/api_servers.py cloud/api_sync.py cloud/api_tasks.py cloud/sync_jobs.py cloud/services.py cloud/lifecycle.py bot/api.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_uses_bulk_order_inference_without_per_asset_fallback cloud.tests.CloudServerServicesTestCase.test_cloud_asset_dashboard_snapshot_refresh_materializes_paginated_list cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_defers_snapshot_refresh cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_status_uses_asset_scope cloud.tests.CloudServerServicesTestCase.test_sync_retained_ip_asset_uses_order_account_and_static_ip_scope cloud.tests.CloudServerServicesTestCase.test_auto_renew_task_detail_includes_due_retry_and_fallback_items cloud.tests.CloudServerServicesTestCase.test_auto_renew_detail_keeps_valid_order_without_asset cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_tasks_executes_due_retry_and_fallback_queue cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_order_executes_single_order cloud.tests.CloudServerServicesTestCase.test_dashboard_asset_order_inference_scopes_duplicate_ip_by_account cloud.tests.DashboardTronBalanceQueryTestCase.test_fetch_address_chain_balances_uses_resolved_headers --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field --noinput --verbosity 1
rg -n "def _cloud_api_override|_cloud_api_override\\(|from cloud\\.api import|import cloud\\.api|cloud\\.api\\." cloud bot orders core shop --glob '!**/migrations/**' -S
rg -n "\\b(TestCase|SimpleTestCase|TransactionTestCase|APITestCase|RequestFactory|AsyncMock|MagicMock|Mock|patch\\(|pytest|unittest|def test_|真机测试|测试用例|仅测试|for test|test only|older tests/imports)\\b" cloud bot orders core shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py' --glob '!**/test_*.py' -S
git diff --check
```

剩余风险：本轮未跑完整测试套件；默认 MySQL 未覆盖，继续使用 SQLite 聚焦测试和静态扫描兜底。

## 2026-06-02 生产代码和测试代码边界复查

### 范围

本轮复查后台生产代码和测试代码的边界，避免业务接口使用测试命名，导致生产代码被误判为测试代码。

### 调整

- 将后台“每日到期汇总测试通知”接口函数从 `test_daily_expiry_summary_notification` 改为 `send_daily_expiry_summary_test_notification`。
- 保留原后台接口路径 `/settings/site-configs/daily-expiry-summary/test/` 和路由名不变，前端调用地址不受影响。
- 测试文件只保留测试用例和测试辅助构造，生产接口实现继续留在 `bot/api_site_configs.py`。
- 对应测试改为按当前后台写接口鉴权规则构造 bearer session，避免测试绕过真实鉴权行为。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api_site_configs.py bot/api.py shop/dashboard_urls.py bot/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.DashboardNotificationTestCase.test_daily_expiry_summary_test_endpoint_forces_send --noinput --verbosity 1
```

边界扫描结果：

```text
非测试文件测试混入数量: 0
测试文件非测试顶层类数量: 0
```

## 2026-06-02 cloud-asset-edit-api-split

### Scope

Twenty-second refactor pass split cloud asset mutation endpoints out of the large asset list API module and tightened status/log behavior around dangerous asset operations.

### Runtime Changes

- Added `cloud/api_asset_edit.py` for cloud asset detail, manual edit, auto-renew toggle, and dashboard delete endpoints.
- Kept `cloud/api.py` as a compatibility facade that re-exports old `cloud.api.*` names and patch points for existing imports/tests.
- `shop/dashboard_urls.py` now imports cloud dashboard route handlers from domain modules directly instead of routing through `cloud.api`.
- `cloud/api_assets.py` now owns asset list, risk summary, snapshot refresh, and asset payload helpers only.
- Manual refresh of unattached static IP delete plans now updates related same-order/same-resource records and logs `CLOUD_UNATTACHED_IP_DELETE_DUE_REFRESHED`.
- Dashboard asset deletion now deletes same-order/same-resource residual records, clears the order cloud binding, writes `CloudIpLog`, and logs removed residual ids through structured logger fields.
- Updated legacy direct-view tests to attach the current dashboard bearer session for write endpoints.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/api.py cloud/api_assets.py cloud/api_asset_edit.py shop/dashboard_urls.py cloud/tests.py
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_uses_bulk_order_inference_without_per_asset_fallback cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_status_uses_asset_scope cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_defers_snapshot_refresh cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_refreshes_unattached_ip_delete_plan cloud.tests.CloudServerServicesTestCase.test_delete_cloud_asset_only_removes_asset_record cloud.tests.CloudServerServicesTestCase.test_delete_cloud_asset_also_removes_residual_server_record cloud.tests_task_center.CloudTaskCenterApiTestCase --keepdb --noinput --verbosity 1
```

## 2026-06-02 cloud-asset-snapshot-api-split

### Scope

Twenty-third refactor pass split cloud asset dashboard snapshot refresh/query/pagination logic out of the cloud asset list endpoint module.

### Runtime Changes

- Added `cloud/api_asset_snapshots.py` for `CloudAssetDashboardSnapshot` refresh, search, risk counts, ordering, pagination, and grouped page construction.
- `cloud/api_assets.py` now focuses on asset list endpoints and asset payload construction.
- Removed obsolete in-memory payload pagination/risk filtering helpers that were no longer used after the snapshot-backed list path became the runtime path.
- `cloud/api.py` imports snapshot refresh compatibility exports from `cloud/api_asset_snapshots.py` directly.
- `cloud/api_assets.py` dropped snapshot table imports and no longer owns snapshot persistence logic.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/api.py cloud/api_assets.py cloud/api_asset_snapshots.py cloud/api_asset_edit.py shop/dashboard_urls.py cloud/tests.py
git diff --check
```

## 2026-06-02 cloud-dashboard-api-domain-split

### Scope

Twenty-second refactor pass split the remaining cloud dashboard API monolith into asset, order, and task modules while preserving `cloud.api` as the URL compatibility facade.

### Runtime Changes

- Added `cloud/api_assets.py` for proxy/asset list payloads, asset risk summaries, asset editing, auto-renew toggles, and dashboard snapshot refreshes.
- Added `cloud/api_orders.py` for cloud order list/detail payloads, order status updates, order detail saves, and protected order deletion.
- Added `cloud/api_tasks.py` for legacy task overview, notice plan detail/refresh, notice switch/text APIs, auto-renew detail, and manual auto-renew execution.
- Reduced `cloud/api.py` from 4249 lines to 460 lines; it now keeps compatibility imports plus single-asset status sync, server sync, cloud plan sync, and delete-asset handling.
- Kept legacy `cloud.api.*` patch/import points for existing tests and operators by routing patched symbols back into the new modules.
- Added structured logs for cloud order status application, order detail updates, cloud asset deletion, and server sync start/finish.
- Fixed a latent `sync_servers()` `cancelled` local variable error by explicitly initializing the flag.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/api.py cloud/api_assets.py cloud/api_orders.py cloud/api_tasks.py
uv run python manage.py check
git diff --check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_orders_list_exposes_auto_renew_enabled cloud.tests.CloudServerServicesTestCase.test_sync_servers_missing_state_does_not_bypass_provider_confirmation cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_uses_bulk_order_inference_without_per_asset_fallback cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_status_uses_asset_scope cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_uses_cloud_notice_plan_table cloud.tests.CloudServerServicesTestCase.test_auto_renew_task_detail_includes_due_retry_and_fallback_items cloud.tests_task_center.CloudTaskCenterApiTestCase --keepdb --noinput --verbosity 1
```

Notes:

- The older direct `RequestFactory` POST tests that do not attach dashboard Bearer credentials still return 401 under the current dashboard write-auth policy; they were not used as pass/fail gates for this split.

## 2026-06-02 cloud-api-sync-facade-split

### Scope

Twenty-third refactor pass removed the remaining real sync/delete implementations from `cloud/api.py`, leaving it as a compatibility facade.

### Runtime Changes

- Added `cloud/api_sync.py` for dashboard server sync, single cloud asset status sync, cloud plan/price sync, and missing-state confirmation helpers.
- Moved `delete_cloud_asset()` into `cloud/api_assets.py` so asset deletion now lives with the rest of the asset dashboard API.
- Reduced `cloud/api.py` from 460 lines to 148 lines; it now only re-exports domain modules and old private patch/import points.
- Kept legacy patch compatibility for:
  - `cloud.api._call_command_capture`
  - `cloud.api._apply_server_missing_state`
  - `cloud.api._refresh_dashboard_plan_snapshots_deferred`
  - `cloud.api.get_redis`
  - `cloud.api.build_trongrid_headers`
  - `cloud.api.httpx`
- Added structured logs for cloud plan/price sync start, completion, and failure.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/api.py cloud/api_assets.py cloud/api_sync.py cloud/api_monitors.py cloud/api_servers.py cloud/api_plans.py
uv run python manage.py check
git diff --check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_servers_missing_state_does_not_bypass_provider_confirmation cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_status_uses_asset_scope cloud.tests.CloudServerServicesTestCase.test_rebuild_job_keeps_old_instance_until_migration_due cloud.tests.DashboardTronBalanceQueryTestCase.test_fetch_address_chain_balances_uses_resolved_headers cloud.tests_task_center.CloudTaskCenterApiTestCase --keepdb --noinput --verbosity 1
```

## 2026-06-01 cloud-sync-worker-and-status-tracking

### Scope

Latest refactor pass made dashboard-triggered proxy synchronization durable and explicitly observable.

### Runtime Changes

- `/admin/cloud-assets/sync/` now only creates a `CloudAssetSyncJob` queue record and returns immediately.
- Added `process_cloud_asset_sync_jobs` as the persistent DB-backed worker for queued sync jobs.
- `run.py worker` starts the sync worker, and `run.py all` now starts web, bot, and the sync worker together.
- Added sync job list and retry APIs:
  - `/admin/cloud-assets/sync-jobs/`
  - `/admin/cloud-assets/sync-jobs/<id>/retry/`
  - `/admin/cloud-assets/sync-jobs/<id>/cancel/`
- Sync job status is now the durable status surface:
  - `queued`
  - `running`
  - `succeeded`
  - `partial`
  - `failed`
  - `cancelled`
- Added `cloud_asset_sync_job_event` for detailed sync event timelines:
  - queued / claimed / status / task / progress / log / warning / error / cancel / retry / heartbeat
  - the event table stores `job_id` as an indexed scalar instead of a foreign key so detailed logging cannot lock or block the main job status row
- Worker and sync execution update `worker_id`, `worker_heartbeat_at`, `progress_current`, `progress_total`, `current_task`, `errors`, `warnings`, `logs`, `started_at`, `finished_at`, and cancel request fields throughout execution.
- Dashboard snapshot refreshes are now scoped:
  - full cloud sync refreshes the complete `cloud_asset_dashboard_snapshot`
  - selected asset sync and single-asset updates refresh only the affected asset IDs
- The admin frontend has a sync job drawer for status, progress, worker heartbeat, results, detailed events, logs, cancel, retry, status filters, and failed-only filtering; polling updates the visible job row without blocking the whole proxy list after enqueue.
- `lefthook.yml` no longer hardcodes `/opt/homebrew/bin/pnpm`, so Git hooks can use the current shell `pnpm`.

### Verification

Passed locally:

```bash
uv run python -m py_compile run.py cloud/api.py cloud/dashboard_snapshots.py cloud/models.py cloud/tests.py cloud/management/commands/process_cloud_asset_sync_jobs.py shop/dashboard_urls.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check
uv run python manage.py sqlmigrate cloud 0042
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_runs_enabled_accounts_and_merges_results cloud.tests.CloudServerServicesTestCase.test_cancel_queued_cloud_asset_sync_job_marks_terminal_and_events cloud.tests.CloudServerServicesTestCase.test_process_cloud_asset_sync_jobs_worker_processes_queued_job --keepdb --noinput --verbosity 1
(cd /Users/a399/Desktop/data/vue-shop-admin && ./node_modules/.bin/vue-tsc --noEmit --skipLibCheck -p apps/web-antd/tsconfig.json)
git diff --check
```

## 2026-06-01 cloud-asset-list-and-sync-performance

### Scope

Twenty-second refactor pass optimized the slow proxy asset list and dashboard-triggered cloud sync path.

### Runtime Changes

- Added `cloud_asset_dashboard_snapshot` as a materialized dashboard list table for cloud assets.
- `cloud_assets_list()` and `cloud_assets_risk_summary()` now read snapshot rows for search, risk filters, grouping, counts, and database pagination instead of rebuilding every row on each request.
- Added `refresh_cloud_asset_dashboard_snapshots` management command and wired dashboard snapshot refreshes after sync/service changes.
- Added `cloud_asset_sync_job` to queue dashboard sync requests, track progress/result/log tails, and expose `/admin/cloud-assets/sync-jobs/<id>/`.
- `/admin/cloud-assets/sync/` now returns immediately with a queued job; the background thread executes account/asset scoped sync tasks and records the final result.
- AWS and Alibaba Cloud sync commands no longer maintain the retired `Server` compatibility mirror; `cloud_asset` remains the single cloud resource truth.
- The admin frontend now uses true server-side pagination in non-grouped proxy list mode and polls cloud sync jobs until terminal status.
- The "show deleted" toggle is sent to the backend so pagination totals match the visible list.
- Database naming and data-flow docs now list the new snapshot/job tables.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/api.py cloud/dashboard_snapshots.py cloud/models.py cloud/tests.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/refresh_cloud_asset_dashboard_snapshots.py shop/dashboard_urls.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check
cd /Users/a399/Desktop/data/vue-shop-admin && ./node_modules/.bin/vue-tsc --noEmit --skipLibCheck -p apps/web-antd/tsconfig.json
```

Blocked locally:

```bash
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_asset_dashboard_snapshot_refresh_materializes_paginated_list ... --keepdb --noinput
```

The focused DB test run is still blocked by local MySQL test database permissions:

```text
Access denied for user 'a'@'localhost' to database 'test_a'
```

## 2026-06-01 proxy-list-and-sync-performance

### Scope

Twenty-seventh refactor pass optimized dashboard proxy list loading and selected-asset cloud sync.

### Runtime Changes

- Added `core.cloud_accounts.list_cloud_account_labels()` so dashboard payload rendering can load active cloud account labels once per request instead of once per asset.
- Added a `CloudAssetPayloadContext` for proxy list payloads:
  - bulk infers missing `CloudServerOrder` links by IP/name/resource identifiers;
  - disables per-row order fallback queries in list/risk-summary reads;
  - avoids `sync_cloud_asset_user_binding()` writes during list rendering;
  - computes missing unattached-IP expiry for display without saving during a GET.
- `cloud_assets_list` and `cloud_assets_risk_summary` now build payloads through the shared context.
- `sync_cloud_assets` now treats selected `asset_ids` as real asset-scoped sync tasks instead of widening to full account sync. Multi-select creates scoped tasks with `asset_id`, `instance_id`, `public_ip`, account, and region.
- Sync task locks include the scoped asset/resource key, so two selected assets in the same account/region do not skip each other.
- Removed the runtime reconcile command call from dashboard sync because `CloudAsset(kind='server')` is now canonical.
- Dashboard sync snapshot refresh now uses the deferred refresh path.
- AWS/Aliyun sync command visible-count summaries use a cheap active asset count instead of full dashboard dedupe scans.
- Frontend proxy list load now uses `risk_counts` returned by the list endpoint and avoids the duplicate concurrent risk-summary request.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/cloud_accounts.py cloud/api.py cloud/api_servers.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
uv run python manage.py check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_uses_bulk_order_inference_without_per_asset_fallback cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_does_not_persist_unattached_ip_expiry cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_with_selected_assets_uses_asset_scoped_tasks --keepdb --noinput --verbosity 1
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_runs_enabled_accounts_and_merges_results cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_status_uses_asset_scope cloud.tests.CloudServerServicesTestCase.test_sync_retained_ip_asset_uses_order_account_and_static_ip_scope --keepdb --noinput --verbosity 1
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_dedupes_same_cloud_account_label_variants cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_keeps_same_user_on_same_page cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_keeps_same_telegram_group_on_same_page cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_paginated_uses_twenty_user_groups_per_page --keepdb --noinput --verbosity 1
(cd /Users/a399/Desktop/data/vue-shop-admin && ./node_modules/.bin/vue-tsc --noEmit --skipLibCheck -p apps/web-antd/tsconfig.json)
```

Frontend note: `pnpm -F @vben/web-antd typecheck` is blocked by local engine mismatch (`pnpm 9.15.9`, Node `v26.0.0`), so the same `vue-tsc` command was run directly.

## 2026-06-01 aws-lightsail-structured-create-log

### Scope

Twenty-second refactor pass removed the remaining runtime `print` from AWS Lightsail provisioning and routed the result through structured logging.

### Runtime Changes

- Replaced the `print('[AWS_CREATE_RESULT]', ...)` stdout dump in `cloud/aws_lightsail.py` with a structured `logger.info(...)` event.
- The creation log now carries `order_no`, `server_name`, `region`, `bundle_id`, `blueprint_id`, `public_ip`, and `static_ip_name` as log fields.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/aws_lightsail.py
uv run python manage.py check
```

## 2026-06-01 cache-redis-fallback-observability

### Scope

Twenty-fourth refactor pass made Redis daily-stat fallback paths observable without changing the local fallback behavior.

### Runtime Changes

- `core/cache.py` now logs debug entries when Redis daily-stat increment, read, or close operations fail.
- The in-process fallback counters still run exactly as before when Redis is unavailable.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/cache.py
uv run python manage.py check
git diff --check
```

## 2026-06-01 aws-missing-confirmation-duplicate-guard

### Scope

Twenty-fifth refactor pass fixed duplicate confirmation increments when the AWS missing-resource sync sees both a canonical `CloudAsset` row and a legacy `Server` compatibility row for the same cloud resource.

### Runtime Changes

- AWS missing confirmation now copies structured `sync_state` from the primary row to the related compatibility row instead of incrementing both independently.
- `_mark_deleted_when_missing_in_aws()` tracks rows already handled in the current sync pass and skips duplicate compatibility rows.
- Local focused tests can run in this aggressive refactor branch with `DJANGO_TEST_REUSE_DB=1`, which reuses the current MySQL database instead of trying to create `test_a`.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_missing_confirmation_note_preserves_existing_note cloud.tests.CloudServerServicesTestCase.test_sync_missing_confirmation_requires_interval cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_expose_missing_confirmation_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_unattached_ip_show_confirmation_progress_in_state_and_note cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_instance_requires_five_passes_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_missing_instance_requires_five_passes_before_delete --keepdb --noinput --verbosity 1
```

## 2026-06-01 server-compat-runtime-shrink

### Scope

Twenty-sixth refactor pass removed more runtime dependency on the `cloud.server_records.Server` compatibility wrapper.

### Runtime Changes

- `core.cloud_accounts.list_cloud_accounts_by_server_load()` now counts `CloudAsset(kind='server')` directly.
- `upsert_cloud_asset` no longer writes a duplicate compatibility `Server` row after creating/updating the canonical asset.
- `dedupe_servers` now de-duplicates canonical server assets in `cloud_asset`.
- `reconcile_cloud_assets_from_servers` is now an explicit no-op compatibility command because `cloud_server` has already been removed.
- Remaining runtime compatibility wrapper imports are limited to AWS/Aliyun sync commands; historical migrations and tests still reference old labels intentionally.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/cloud_accounts.py cloud/management/commands/upsert_cloud_asset.py cloud/management/commands/dedupe_servers.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
uv run python manage.py check
uv run python manage.py reconcile_cloud_assets_from_servers
uv run python manage.py dedupe_servers
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test core.tests.CloudAccountSelectionTestCase --keepdb --noinput --verbosity 1
```

## 2026-06-01 dashboard-api-helper-extraction

### Scope

Twenty-third refactor pass removed the dashboard API submodules' reverse dependency on the `bot.api` aggregation module.

### Runtime Changes

- Added `core/dashboard_totp.py` for dashboard TOTP secret normalization, generation, otpauth URL building, and token verification.
- Added `bot/user_stats.py` for active cloud asset and per-user proxy count queries.
- Moved generic dashboard payload helpers (`_json_payload`, `_payload_bool`, `_parse_runtime_time_point`) into `core/dashboard_api.py`.
- `bot/api_auth.py`, `bot/api_admin_users.py`, `bot/api_cloud_accounts.py`, `bot/api_operation_logs.py`, `bot/api_products.py`, `bot/api_site_configs.py`, `bot/api_telegram.py`, and `bot/api_users.py` no longer import from `bot.api`.
- `bot/api.py` now consumes the extracted helpers and remains a route/export aggregation point.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/dashboard_api.py core/dashboard_totp.py bot/user_stats.py bot/api.py bot/api_auth.py bot/api_admin_users.py bot/api_cloud_accounts.py bot/api_operation_logs.py bot/api_products.py bot/api_site_configs.py bot/api_telegram.py bot/api_users.py
uv run python manage.py check
git diff --check
```

## 2026-06-01 cloud-asset-query-helper

### Scope

Twenty-first refactor pass moved shared cloud asset list visibility and de-duplication logic out of dashboard API modules.

### Runtime Changes

- Added `cloud/asset_queries.py` for canonical `CloudAsset` visible-list and de-duplication helpers.
- `cloud/api.py` now consumes the shared asset query helpers instead of owning them.
- AWS sync, Alibaba Cloud sync, and asset reconciliation commands no longer import `cloud.api` just to count visible assets.
- AWS and Alibaba Cloud sync commands now import `_provider_status_label` from `core.dashboard_api` instead of `bot.api`.

### Verification

Passed locally:

```bash
uv run python -m py_compile cloud/asset_queries.py cloud/api.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
uv run python manage.py check
git diff --check
```

## 2026-06-01 db-naming-convention-alignment

### Scope

Seventeenth refactor pass corrected database naming documentation so it matches the actual runtime schema.

### Runtime Changes

- Updated `docs/DB_NAMING_CONVENTIONS.md` from the previous idealized plural-table convention to the real project convention:
  - `core_*`
  - `bot_*`
  - `order_*`
  - `cloud_*`
- Documented the current `db_table` inventory for `core`, `bot`, `orders`, and `cloud`.
- Clarified that new runtime tables should use `域前缀_单数语义名`.
- Explicitly marked plural alternatives such as `cloud_assets`, `cloud_server_orders`, and `balance_ledgers` as non-default unless part of a planned migration.
- Reconfirmed `cloud_asset` as the cloud resource source-of-truth table.

### Verification

Documentation-only change. Source table list was checked with:

```bash
rg -n "db_table\\s*=|class Meta:" core bot orders cloud -g'*.py'
```

## 2026-06-01 encrypted-config-invalid-token-handling

### Scope

Eighteenth refactor pass tightened encrypted configuration handling so broken Fernet-looking ciphertext is not silently treated as plaintext.

### Runtime Changes

- `core/crypto.py:decrypt_text()` still returns legacy plaintext values unchanged when they do not look encrypted.
- Values starting with the Fernet token prefix `gAAAA` now log `CONFIG_DECRYPT_INVALID_TOKEN` and return an empty string when decryption fails.
- Added focused tests for:
  - legacy plaintext fallback
  - invalid Fernet-like token handling after an encryption key mismatch
- Fixed `core/tests.py` to import the `Server` compatibility model from `cloud.server_records`, matching the current cloud asset architecture.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/crypto.py core/tests.py core/models.py bot/models.py bot/api_site_configs.py
uv run python manage.py test core.tests.CryptoDecryptTestCase --keepdb
uv run python manage.py check
```

## 2026-06-01 site-config-cache-invalidation

### Scope

Nineteenth refactor pass reduced configuration cache split-brain between `SiteConfig` local cache and `core.cache` async config cache.

### Runtime Changes

- Added explicit `core.cache` helpers:
  - `get_cached_config_value()`
  - `cache_config_value()`
  - `invalidate_config_cache()`
- `SiteConfig.clear_cache()` now invalidates the async config cache as well as the model-local 30-second cache.
- Replaced direct `_cached_config` writes/reads in bot text/config paths with helper functions.
- Added a focused regression test for `SiteConfig.set()` invalidating the async config cache.

### Verification

Passed locally:

```bash
uv run python -m py_compile core/cache.py core/models.py core/texts.py core/tests.py bot/api_site_configs.py bot/handlers.py
uv run python manage.py check
```

Blocked locally:

```bash
uv run python manage.py test core.tests.SiteConfigCacheTestCase --keepdb
```

The focused DB test run is blocked by local MySQL test database permissions:

```text
Access denied for user 'a'@'localhost' to database 'test_a'
```
