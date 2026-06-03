# 重构版本记录

## 2026-06-03 11:32 自动监工：修复任务中心执行记录漏报

### 范围

本轮从提交 `e4749aa 记录自动续费返回链巡检` 后继续监工。起始读取 git 状态时工作树存在上一轮自动化未提交的 `cloud/task_center.py`、`cloud/tests_task_center.py` 修改，本轮在确认差异后继续完成验证和收尾。

重点复查后台任务中心、云生命周期执行记录、通知计划执行记录、`CloudAsset.actual_expires_at` 唯一到期事实、订单旧到期字段、计划快照、旧退款入口、废弃 app 回流，以及机器人返回链和 Telegram `callback_data` 64 字节限制。

### 修改

- 修复后台任务中心 `lifecycle` 分区只依赖计划 bundle 和 `CloudIpLog` 历史的问题：现在会纳入最近 24 小时内 `CloudLifecycleTask` 的执行中和失败记录，避免执行器失败但未写入历史日志时后台总览漏报。
- 修复后台任务中心 `notices` 分区只依赖通知计划和用户通知日志的问题：现在会纳入最近 24 小时内 `CloudNoticeTask` 的通知中和失败记录，避免 Bot 通知任务失败但未写入用户通知日志时漏报。
- 生命周期 DB 执行记录会按订单、资产或 IP 与计划项去重；当同一对象同时存在计划项和执行器失败记录时，总览优先展示执行器错误，避免 total、active、failed 和 `status_counts` 重复计数。
- DB 执行记录统一补充 `last_error`、`failure_reason`、`note`、`detail_path` 和 `related_path`，后台任务中心卡片可以直接展示失败原因和跳转目标。
- 补充 `cloud.tests_task_center` 聚焦测试，覆盖生命周期 DB 失败无历史日志、生命周期 DB 失败与计划项重复、通知 DB 失败无用户通知日志。

### 监工结果

- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期事实。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`。
- `CloudAssetDashboardSnapshot` 未恢复资产到期字段；仅保留 `risk_expired` 风险布尔字段。
- 旧退款函数名、旧退款状态、旧端口入口和废弃 app runtime 导入扫描无命中；`dashboard_api` 仍仅为当前 URL namespace。
- 机器人返回链聚焦测试继续通过，覆盖资产详情、订单详情、续费、换 IP、重装、修改配置和 IP 查询结果等 callback 边界。
- 真机测试未执行：本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py check
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python -m py_compile bot/keyboards.py bot/handlers.py cloud/api_tasks.py bot/api.py cloud/task_center.py cloud/tests_task_center.py
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --verbosity 1
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity 1
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('asset_expiry_fields', [f.name for f in CloudAsset._meta.fields if 'expire' in f.name or 'expiry' in f.name]); print('order_removed_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'actual_expires_at','service_expires_at'}]); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at'])"
rg -n "service_expires_at|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_cloud_server_order|refund_cloud_order|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded|allow_client_port|set_cloud_server_port|custom:port|cloud:ipport" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py'
rg -n "from (accounts|finance|mall|monitoring|dashboard_api|biz)\b|include\(.*(accounts|finance|mall|monitoring|dashboard_api|biz)" shop core bot orders cloud --glob '!**/migrations/**'
git diff --check
```

SQLite 聚焦测试仍会打印不支持 `db_comment` 的预期 warning；`RetainedIpRenewalUiTestCase` 仍会打印 `SimpleTestCase` 禁止数据库查询配置的既有容错日志和 mocked postcheck 异常日志，最终均为 OK。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后单独执行，并写中文报告，云资源 ID 需脱敏。

## 2026-06-03 10:34 自动监工：复查生命周期事实与任务总览

### 范围

本轮从提交 `abeeee4 记录生命周期回调巡检结果` 后继续监工。起始读取 git 状态时工作树干净，分支为 `codex/cloud-asset-lifecycle-refactor`。

重点复查云资产生命周期唯一到期事实、订单表旧到期字段、计划快照表、旧退款入口、废弃 app 回流、机器人返回链和 Telegram `callback_data` 64 字节限制，以及任务中心对同步、生命周期、通知计划和自动续费失败状态的总览统计。

### 修改

- 本轮未修改运行代码。
- 仅追加本中文版本记录，记录字段 introspection、旧入口扫描、任务中心和机器人回调聚焦测试结果。

### 监工结果

- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 等流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复到期字段，仅有 `risk_expired` 风险布尔字段。
- runtime 代码扫描未发现旧计划快照模型、旧退款函数名、旧退款状态、旧端口入口或废弃 app 运行时回流；`dashboard_api` 命中仍只是当前 URL namespace 和 `core.dashboard_api` helper 命名。
- 任务中心聚焦测试通过，覆盖 `cloud_sync`、`cloud_orders`、`lifecycle`、`notices`、`auto_renew` 分区，以及近 24 小时失败历史、`failed_retry`、`retry_failed` 的失败统计。
- 机器人返回链聚焦测试通过，覆盖资产详情、订单详情、续费支付、换 IP、重装、修改配置、IP 查询结果和极端长 callback 的 64 字节限制。
- 真机测试未执行：本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py bot/handlers.py cloud/services.py cloud/bootstrap.py cloud/api.py cloud/task_center.py cloud/api_tasks.py
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_current.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_callbacks_current.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_introspect_current.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}]); print('asset_expiry_fields', [f.name for f in CloudAsset._meta.fields if 'expire' in f.name or 'expiry' in f.name]); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at'])"
rg -n "service_expires_at|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded|allow_client_port|set_cloud_server_port|custom:port|cloud:ipport" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py'
rg -n "from (accounts|finance|mall|monitoring|dashboard_api|biz)\b|include\(.*(accounts|finance|mall|monitoring|dashboard_api|biz)" shop core bot orders cloud --glob '!**/migrations/**'
```

第一次直接使用默认 MySQL 配置运行 `cloud.tests_task_center` 时，沙箱禁止连接 `127.0.0.1`，随后改用 SQLite 通过。SQLite 聚焦测试仍会打印不支持 `db_comment` 的预期告警；`RetainedIpRenewalUiTestCase` 仍会打印 `SimpleTestCase` 禁止数据库查询配置的预期日志，最终 44 条通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后单独执行，并写中文报告，云资源 ID 需脱敏。

## 2026-06-03 10:11 自动监工：复查生命周期与任务中心状态

### 范围

本轮从提交 `e70135a 记录后台任务中心巡检结果` 后继续监工。起始读取 git 状态时工作树干净，分支为 `codex/cloud-asset-lifecycle-refactor`。

重点复查云资产生命周期唯一到期事实、订单表旧到期字段、计划快照表、旧退款入口、废弃 app 回流、机器人返回链和 Telegram `callback_data` 64 字节限制，以及后台任务中心对同步、生命周期、通知计划和自动续费失败状态的统计。

### 修改

- 本轮未修改运行代码。
- 仅追加本中文版本记录，记录字段 introspection、旧入口扫描、任务中心/Bot/生命周期聚焦测试和基础检查结果。

### 监工结果

- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 作为流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复到期字段。
- runtime 代码扫描未发现旧计划模型、旧计划快照表、旧退款函数名、旧端口入口或废弃 app 目录回流。
- 任务中心 `cloud_sync`、`cloud_orders`、`lifecycle`、`notices`、`auto_renew` 分区聚焦测试通过，近 24 小时失败历史和 `retry_failed` 统计仍能进入失败总览。
- 机器人返回链聚焦测试通过，覆盖资产详情、订单详情、续费、换 IP、重装、修改配置等短 callback 边界。
- 真机测试未执行：本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py bot/handlers.py bot/keyboards.py cloud/services.py cloud/lifecycle.py cloud/api_orders.py cloud/api_assets.py cloud/api_tasks.py cloud/task_center.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py orders/payment_scanner.py
DJANGO_SETTINGS_MODULE=shop.settings DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_introspect_<时间戳>.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python - <<'PY'
# 模型字段 introspection：CloudAsset=['actual_expires_at']；CloudServerOrder=['renew_grace_expires_at']；CloudAssetDashboardSnapshot=[]
PY
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_<时间戳>.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_callbacks_<时间戳>.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_lifecycle_fact_<时间戳>.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_lists_plans_without_creating_order cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_completed_asset_recovery_order_renews_without_reprovisioning cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_sync_aws_retained_ip_preserves_existing_asset_user --noinput --verbosity 1
rg -n "service_expires_at\\s*=|order\\.(service_expires_at|actual_expires_at)|CloudServerOrder\\([^\\n]*(service_expires_at|actual_expires_at)|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_cloud_server_order|refund_cloud_order|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
```

SQLite 聚焦测试仍会打印不支持 `db_comment` 的预期告警；`RetainedIpRenewalUiTestCase` 仍会打印 `SimpleTestCase` 禁止数据库查询配置的预期日志，最终 44 条通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后单独执行，并写中文报告，云资源 ID 需脱敏。

## 2026-06-03 08:20 当前会话自动监工：修正自动续费失败总览统计

### 范围

本轮按用户要求继续执行当前会话监工，并在中途按用户要求恢复 `Shop 自动优化监工` 自动化。起始工作树干净，最新提交为 `9e36dc5 收窄订单主资产更新范围`。

重点复查后台任务中心、自动续费详情、通知计划、云资产生命周期唯一到期事实、旧到期字段、旧计划快照、旧退款入口、旧端口入口和废弃 app 回流。

### 修改

- 修复任务中心“自动续费”分区失败统计漏报 `retry_failed` 的问题。
- 自动续费详情 API 中 `queue_status=retry_failed` 表示“失败待重试”，任务中心现在会把它计入 `failed` 并把分区健康状态标记为 `error`。
- 新增 `test_auto_renew_section_counts_retry_failed_as_failed`，防止后续再次把自动续费失败待重试任务显示为非失败状态。
- 已恢复 Codex App 自动化 `Shop 自动优化监工`，状态为 `ACTIVE`，仍按每 10 分钟运行。

### 监工结果

- 修复前新增测试会失败：自动续费 `retry_failed` 项在任务中心 `failed` 中计为 0。
- 修复后任务中心自动续费总览、自动续费详情 due/retry/fallback 聚焦测试均通过。
- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- 运行代码扫描未发现旧计划模型、旧退款函数名、旧端口入口或废弃 app 目录回流。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_auto_renew_failed_before.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center.CloudTaskCenterApiTestCase.test_auto_renew_section_counts_retry_failed_as_failed --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_auto_renew_failed_after.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_auto_renew_task_detail_session.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_auto_renew_task_detail_includes_due_retry_and_fallback_items cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_tasks_executes_due_retry_and_fallback_queue --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/task_center.py cloud/tests_task_center.py cloud/api_tasks.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\s*=|order\.(service_expires_at|actual_expires_at)|CloudServerOrder\([^\n]*(service_expires_at|actual_expires_at)|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_cloud_server_order|refund_cloud_order|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
```

第一条测试是修复前复现用例，按预期失败；修复后 `cloud.tests_task_center` 3 条通过。`makemigrations --check --dry-run` 仍出现本机无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后单独执行，并写中文报告，云资源 ID 需脱敏。

## 2026-06-03 07:44 当前会话自动监工：收窄主资产更新范围

### 范围

本轮按用户要求在当前会话持续执行自动优化任务。起始工作树干净，最新提交为 `9f47e47 修正资产详情到期事实兜底`。

重点继续复查云资产生命周期唯一到期事实、后台资产详情只读展示、订单详情同步主资产、兼容 `Server` 包装、旧到期字段、旧计划快照、旧退款入口、旧端口入口和废弃 app 回流。

### 修改

- 修复 `_update_order_primary_records()` 批量更新同订单所有云资产的问题。
- 主记录同步现在只更新 `_order_primary_asset(order)` 选出的当前主资产，避免后台订单状态/详情编辑把同订单下历史资产或非主资产的到期时间、代理字段一起覆盖。
- 新增 `test_order_primary_record_update_does_not_mutate_stale_same_order_assets`，复现并锁定“同订单历史资产不应被主记录更新误写”的边界。

### 监工结果

- 修复前新增测试会失败：同订单历史资产的 `actual_expires_at` 被主资产新到期时间覆盖。
- 修复后当前主资产会正确同步新到期和代理字段，历史资产保留自己的到期事实和代理字段。
- 订单状态同步、订单详情手工编辑、主代理密钥同步、历史 IP 同步等周边路径继续通过。
- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 作为流程时间字段。
- 运行代码扫描未发现旧计划模型、旧退款函数名、旧端口入口或废弃 app 目录回流。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_primary_update_scope_before.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_primary_record_update_does_not_mutate_stale_same_order_assets --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_primary_update_scope_after.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_primary_record_update_does_not_mutate_stale_same_order_assets cloud.tests.CloudServerServicesTestCase.test_order_primary_records_prefer_ip_over_stale_names --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_order_status_sync_after.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_status_endpoint_syncs_primary_asset_and_server_status cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_status_edit_syncs_primary_asset_and_server_status cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_secret_edit_syncs_primary_asset cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_previous_ip_edit_syncs_primary_records --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_asset_payload_fact_session_final.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_asset_detail_does_not_fallback_to_order_asset_expiry cloud.tests.CloudServerServicesTestCase.test_cloud_asset_detail_exposes_related_order_click_path cloud.tests.CloudServerServicesTestCase.test_cloud_asset_get_payload_does_not_mutate_manual_asset_fields --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/services.py cloud/tests.py cloud/api_orders.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\s*=|order\.(service_expires_at|actual_expires_at)|CloudServerOrder\([^\n]*(service_expires_at|actual_expires_at)|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_cloud_server_order|refund_cloud_order|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
```

第一条测试是修复前复现用例，按预期失败；修复后相关聚焦测试全部通过。`makemigrations --check --dry-run` 仍出现本机无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后单独执行，并写中文报告，云资源 ID 需脱敏。

## 2026-06-03 07:34 自动监工：收紧资产详情到期事实展示

### 范围

本轮从提交 `bb1fb2e 记录回调边界生命周期复查` 后继续监工。起始读取 git 状态时工作树干净，分支为 `codex/cloud-asset-lifecycle-refactor`。

重点复查云资产生命周期是否仍只以 `CloudAsset.actual_expires_at` 作为唯一结构化到期事实，订单表是否恢复旧到期字段，计划快照表是否恢复，退款逻辑和旧退款函数名是否回流，废弃 app 是否误用，以及机器人返回链和 Telegram `callback_data` 64 字节限制。

### 修改

- `cloud/api_asset_edit.py` 移除后台资产详情 GET 中对 `order_asset_expiry(order)` 的展示兜底，避免当前资产 `actual_expires_at` 为空时从同订单其他资产推导出到期值。
- 资产详情仍沿用 `_asset_payload()` 的资产自身到期展示；未附加固定 IP 的只读计算展示继续保留，且不会写回资产字段。
- `cloud/tests.py` 新增 `test_cloud_asset_detail_does_not_fallback_to_order_asset_expiry`，覆盖同一订单下其他资产有到期、当前资产无到期时，后台详情仍返回空到期事实。

### 监工结果

- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 作为流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复到期字段。
- 运行代码扫描未发现旧计划模型、旧计划快照表、旧退款函数名、旧端口入口 `allow_client_port` / `set_cloud_server_port` / `custom:port:` / `cloud:ipport:` 回流。
- 仓库根目录未发现废弃 app 目录 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- 机器人返回链聚焦测试继续通过，覆盖资产详情、订单详情、续费、换 IP、重装、修改配置等短 callback 边界。
- 真机测试未执行：本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/api_asset_edit.py cloud/tests.py cloud/api_assets.py cloud/asset_expiry.py bot/handlers.py bot/keyboards.py orders/payment_scanner.py
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_asset_detail_expiry_fact_retry_<进程>.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_asset_detail_does_not_fallback_to_order_asset_expiry cloud.tests.CloudServerServicesTestCase.test_cloud_asset_detail_exposes_related_order_click_path cloud.tests.CloudServerServicesTestCase.test_cloud_asset_get_payload_does_not_mutate_manual_asset_fields --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_lifecycle_fact_<进程>.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_lists_plans_without_creating_order cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_completed_asset_recovery_order_renews_without_reprovisioning --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_callback_fact_<进程>.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DJANGO_SETTINGS_MODULE=shop.settings DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_introspect_fact_<进程>.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python - <<'PY'
# 模型字段 introspection：CloudAsset=['actual_expires_at']；CloudServerOrder=['renew_grace_expires_at']；CloudAssetDashboardSnapshot=[]
PY
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\\s*=|order\\.(service_expires_at|actual_expires_at)|CloudServerOrder\\([^\\n]*(service_expires_at|actual_expires_at)|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_cloud_server_order|refund_cloud_order|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。`RetainedIpRenewalUiTestCase` 仍会打印 `SimpleTestCase` 禁止数据库查询配置的预期日志，最终 44 条通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后单独执行，并写中文报告，云资源 ID 需脱敏。

## 2026-06-03 07:23 自动监工：复查回调边界和生命周期事实

### 范围

本轮从提交 `9c07360 记录任务中心极端回调复查` 后继续监工。起始读取 git 状态时工作树干净，分支为 `codex/cloud-asset-lifecycle-refactor`。

重点复查云资产生命周期是否仍只以 `CloudAsset.actual_expires_at` 作为唯一结构化到期事实，订单表是否恢复旧到期字段，计划快照表是否恢复，退款逻辑和旧退款函数名是否回流，废弃 app 是否误用，以及机器人资产详情、订单详情、续费、换 IP、重装、修改配置、只读订单详情等返回链是否仍满足 Telegram `callback_data` 64 字节限制。

### 修改

- 本轮未修改运行代码。
- 仅追加本中文版本记录，记录本轮字段 introspection、旧入口扫描、极端 callback 探针、迁移检查和聚焦测试结果。

### 监工结果

- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 作为流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复到期字段。
- 运行代码扫描未发现旧计划模型、旧计划快照表、旧退款函数名、旧端口入口 `allow_client_port` / `set_cloud_server_port` / `custom:port:` / `cloud:ipport:` 回流。
- 仓库根目录未发现废弃 app 目录 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`；`dashboard_api` 仅作为当前路由 namespace 使用。
- 机器人返回链独立探针覆盖极端 18 位订单 ID、18 位资产 ID、18 位页码、嵌套订单详情、嵌套资产详情、续费支付、换 IP、重装、修改配置、只读订单详情、IP 查询结果和未知超长来源共 61 个 callback 样本，无超限，最大 64 字节。
- 真机测试未执行：本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py bot/handlers.py bot/keyboards.py cloud/services.py cloud/lifecycle.py cloud/api_orders.py cloud/api_assets.py cloud/api_tasks.py cloud/task_center.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py
DJANGO_SETTINGS_MODULE=shop.settings SQLITE_NAME=/private/tmp/shop_introspect_<进程>.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python - <<'PY'
# 模型字段 introspection：CloudAsset=['actual_expires_at']；CloudServerOrder=['renew_grace_expires_at']；CloudAssetDashboardSnapshot=[]
PY
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_callbacks_<时间戳>.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_<时间戳>.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests_task_center.CloudTaskCenterApiTestCase core.tests.MySqlSqlModeSettingsTestCase --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_lifecycle_<时间戳>.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_lists_plans_without_creating_order cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_completed_asset_recovery_order_renews_without_reprovisioning cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_sync_aws_retained_ip_preserves_existing_asset_user --noinput --verbosity 1
DJANGO_SETTINGS_MODULE=shop.settings DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_callback_probe_<时间戳>.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python - <<'PY'
# 极端 callback 样本探针，覆盖资产详情、订单详情、续费支付、换 IP、重装、修改配置、只读订单详情和列表入口。
PY
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
rg -n "refund_cloud_server_order|refund_cloud_order|CloudPlanSnapshot|CloudLifecyclePlanSnapshot|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot cloud orders core shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

首次直接运行 `uv run python manage.py check` 时，本机 `/Users/a399/.cache/uv` 缓存目录被沙箱拒绝访问；改用 `UV_CACHE_DIR=/private/tmp/uv-cache-shop` 后 `manage.py check` 正常通过。`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。`RetainedIpRenewalUiTestCase` 仍会打印 `SimpleTestCase` 禁止数据库查询配置的预期日志，最终 44 条通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后单独执行，并写中文报告，云资源 ID 需脱敏。

## 2026-06-03 07:13 自动监工：复查任务中心和极端回调

### 范围

本轮从提交 `2a14f90 修正任务中心通知失败统计` 后继续监工。起始读取 git 状态时工作树干净，分支为 `codex/cloud-asset-lifecycle-refactor`。

重点复查云资产生命周期是否仍只以 `CloudAsset.actual_expires_at` 作为唯一结构化到期事实，订单表是否恢复旧到期字段，计划快照表是否恢复，退款逻辑和旧退款函数名是否回流，废弃 app 是否误用，以及机器人资产详情、订单详情、续费、换 IP、重装、修改配置等返回链是否仍满足 Telegram `callback_data` 64 字节限制。

### 修改

- 本轮未修改运行代码。
- 仅追加本中文版本记录，记录任务中心回归测试、生命周期字段 introspection、旧入口扫描、极端 callback 探针和聚焦测试结果。

### 监工结果

- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 作为流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复到期字段。
- 运行代码扫描未发现旧计划模型、旧计划快照表、旧退款函数名、旧端口入口 `allow_client_port` / `set_cloud_server_port` / `custom:port:` / `cloud:ipport:` 回流。
- 仓库根目录未发现废弃 app 目录 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`；`dashboard_api` 仅作为当前路由 namespace 使用。
- 任务中心通知计划 `failed_retry` 失败统计回归测试通过。
- 极端 18 位订单 ID、18 位资产 ID、18 位页码、嵌套订单详情、嵌套资产详情和未知超长来源共 144 个 callback 样本无超限，最大 64 字节。
- 真机测试未执行：本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/services.py cloud/lifecycle.py cloud/lifecycle_tasks.py cloud/task_center.py cloud/tests.py cloud/tests_task_center.py orders/payment_scanner.py orders/tests.py shop/settings.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('CloudAsset', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('CloudServerOrder', [f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('CloudAssetDashboardSnapshot', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name])"
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_callbacks_monitor_<时间戳>.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_monitor_ok_<时间戳>.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center.CloudTaskCenterApiTestCase core.tests.MySqlSqlModeSettingsTestCase --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_lifecycle_monitor_<时间戳>.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_lists_plans_without_creating_order cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_completed_asset_recovery_order_renews_without_reprovisioning --noinput --verbosity 1
DJANGO_SETTINGS_MODULE=shop.settings DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_callback_probe_<时间戳>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python - <<'PY'
# 极端 callback 样本探针，覆盖资产详情、订单详情、续费支付、换 IP、重装、修改配置、只读订单详情和列表入口。
PY
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\\s*=|order\\.(service_expires_at|actual_expires_at)|CloudServerOrder\\([^\\n]*(service_expires_at|actual_expires_at)|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。`RetainedIpRenewalUiTestCase` 仍会打印 `SimpleTestCase` 禁止数据库查询配置的预期日志，最终 44 条通过。任务中心测试第一次误用旧类名 `TaskCenterNotificationTestCase`，极端 callback 探针第一次误从 `bot.keyboards` 导入续费套餐键盘且第二次传入字典套餐，均已按当前代码结构更正并重跑通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后单独执行，并写中文报告，云资源 ID 需脱敏。

## 2026-06-03 02:43 自动监工：复查生命周期事实和极端回调

### 范围

本轮从提交 `4839cf1 记录通知价格返回链复查` 后继续监工。起始读取 git 状态时工作树干净，分支为 `codex/cloud-asset-lifecycle-refactor`。

重点复查云资产生命周期是否仍只以 `CloudAsset.actual_expires_at` 作为唯一结构化到期事实，订单表是否恢复旧到期字段，计划快照表是否恢复，退款逻辑和旧退款函数名是否回流，废弃 app 是否误用，以及机器人资产详情、订单详情、续费、换 IP、重装、修改配置等返回链是否仍满足 Telegram `callback_data` 64 字节限制。

### 修改

- 本轮未修改运行代码。
- 仅追加本中文版本记录，记录本轮静态扫描、字段 introspection、极端 callback 探针和聚焦测试结果。

### 监工结果

- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 作为流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复到期字段。
- 运行代码扫描未发现旧计划模型、旧计划快照表、旧退款函数名、旧端口入口 `allow_client_port` / `set_cloud_server_port` / `custom:port:` / `cloud:ipport:` 回流。
- 仓库根目录未发现废弃 app 目录 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`；`dashboard_api` 仅作为当前路由 namespace 使用。
- 极端 18 位订单 ID、18 位资产 ID、18 位页码、嵌套订单详情、嵌套资产详情和未知超长来源共 128 个 callback 样本无超限，最大 64 字节。
- 真机测试未执行：本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/services.py cloud/provisioning.py cloud/api_orders.py cloud/api_servers.py cloud/lifecycle.py cloud/api_tasks.py orders/payment_scanner.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print([f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name])"
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_callbacks_watch_20260603_<进程>.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_lifecycle_watch_20260603_<进程>.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_lists_plans_without_creating_order cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_completed_asset_recovery_order_renews_without_reprovisioning --noinput --verbosity 1
DJANGO_SETTINGS_MODULE=shop.settings UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_callback_probe_20260603_retry_<进程>.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python - <<'PY'
# 极端 callback 样本探针，覆盖资产详情、订单详情、续费支付、换 IP、重装、修改配置、只读订单详情和列表入口。
PY
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\\s*=|order\\.(service_expires_at|actual_expires_at)|CloudServerOrder\\([^\\n]*(service_expires_at|actual_expires_at)|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/__pycache__/**'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。`RetainedIpRenewalUiTestCase` 仍会打印 `SimpleTestCase` 禁止数据库查询配置的预期日志，最终 44 条通过。极端 callback 探针第一次误从 `bot.keyboards` 导入续费套餐键盘失败，确认该键盘定义在 `bot.handlers` 后已用正确导入重跑通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后单独执行，并写中文报告，云资源 ID 需脱敏。

## 2026-06-03 02:25 自动监工：收紧详情返回按钮兜底压缩

### 范围

本轮从提交 `b56d3bd 记录默认端口生命周期监工` 后继续监工。起始读取 git 状态时工作树干净；自动化配置仍为 `ACTIVE`，`rrule` 为每 10 分钟一次，模型为 `gpt-5.5`，后端 runserver 仍在 `127.0.0.1:8000` 运行。

本轮启动终端版 `codex-cli 0.135.0-alpha.1` 只读巡检，报告保存到 `/private/tmp/shop_codex_review_20260603_0219.md`。巡检结论为未发现高置信、可复现、会影响运行的 bug；同时工作树出现机器人返回链相关脏改动，本轮已复查并保留为有效修复。

### 修改

- `bot/handlers.py` 中资产详情返回来源和管理员修改到期时间后的“返回原页面”统一走 `_compact_back_button_callback()`，避免超长来源直接进入返回按钮。
- `bot/keyboards.py` 中只读订单详情返回按钮也统一走 `_compact_back_button_callback()`，超长未知来源回退到 `cloud:list`。
- `bot/tests.py` 新增聚焦断言，覆盖资产详情、管理员改到期和只读订单详情的超长来源兜底，确保返回按钮不超过 Telegram `callback_data` 64 字节限制。

### 监工结果

- `codex-cli` 确认旧 app 未回流，仓库根目录无 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；`CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`。
- `CloudLifecycleTask` 和 `CloudNoticeTask` 仍有数据库任务支撑和 `source_key` 唯一认领保护。
- 机器人短回调 `cad/csd/ar/ac/au/ai/r/rnp/arp/p/upp/ri/exp/clp/poc` 均有 handler 覆盖；本轮额外收紧返回按钮兜底压缩。
- 新购端口仍固定为 `443`，付款成功后会提交默认端口创建流程。
- 运行代码未发现退款逻辑或旧退款函数名回流。
- 真机测试未执行：本轮未执行真实云资源创建、删除、IP 变更、真实支付、链上广播、生产发布或不可逆操作。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/lifecycle_tasks.py cloud/lifecycle_execution.py orders/payment_scanner.py
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_back_compact_<时间戳>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
git diff --check
```

`RetainedIpRenewalUiTestCase` 共 44 条通过。测试日志中 `SiteConfig.get` 的 `DatabaseOperationForbidden` 来自 `SimpleTestCase` 内读取按钮配置时的兜底路径，测试最终为 `OK`，不是失败。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后单独执行，并写中文报告，云资源 ID 需脱敏。

## 2026-06-03 02:16 自动监工：复核默认端口和生命周期任务

### 范围

本轮从提交 `2c8abd1 补充端口收口监工验证记录` 后继续监工。起始读取 git 状态时工作树干净；自动化配置仍为 `ACTIVE`，`rrule` 为每 10 分钟一次，模型为 `gpt-5.5`。后端 runserver 仍在 `127.0.0.1:8000` 运行。

本轮启动终端版 `codex-cli 0.135.0-alpha.1` 只读巡检，报告保存到 `/private/tmp/shop_codex_review_20260603_0213.md`，重点复查云资产唯一到期事实、通知/生命周期任务数据库支撑、防重复执行、旧计划快照和退款逻辑回流、机器人返回链、Telegram `callback_data` 64 字节限制，以及新购默认端口 443 和付款后直接创建流程。

### 修改

- 本轮未修改运行代码。
- 仅追加本中文版本记录，记录本轮 `codex-cli` 监工结论、本地验证结果和只读沙箱限制。

### 监工结果

- `codex-cli` 结论：未发现高置信、可复现、会影响运行的 bug。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；`CloudServerOrder` 当前模型没有 `service_expires_at` 或 `actual_expires_at`，订单编辑收到 `actual_expires_at` 时会从订单更新字段剔除并写回资产表。
- `CloudLifecycleTask` 和 `CloudNoticeTask` 仍有 `source_key` 唯一键、认领 token、状态和重试字段；认领逻辑使用唯一 key 加条件 update 防重复。
- 当前运行代码未发现 `CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan` 旧计划快照模型回流；`INSTALLED_APPS` 仍只保留当前核心 app。
- 机器人短回调 `ar/ac/au/r/i/ri/u/p/im/ir/ai` 均有 handler；生成侧仍有 64 字节压缩保护。
- 新购默认端口仍为 `443`；链上支付确认后会写入默认端口并进入创建流程。
- 真机测试未执行：本轮未执行真实云资源创建、删除、IP 变更、真实支付、链上广播、生产发布或不可逆操作。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py core/texts.py core/tests.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/lifecycle_tasks.py cloud/api_asset_edit.py cloud/api_orders.py cloud/services.py orders/payment_scanner.py
git diff --check
```

只读 `codex-cli` 沙箱内无法初始化 `uv` 缓存，因此它没有实际运行 Django 命令；上述验证已在当前可写开发环境完成。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后单独执行，并写中文报告，云资源 ID 需脱敏。

## 2026-06-03 01:45 自动监工：收紧主代理链接保存端口校验

### 范围

本轮从提交 `814c1a1 收口旧主代理端口校验` 后继续监工。起始读取 git 状态时工作树干净；随后复查机器人返回链、Telegram `callback_data` 64 字节限制、云资产唯一到期事实、订单旧到期字段、旧计划快照、旧退款入口、旧端口入口和废弃 app 回流。

### 修改

- `bot/handlers.py` 新增主代理链接保存前的记录端口校验：资产和订单补充主代理链接时，必须匹配当前记录的 `mtproxy_port`；缺省端口按默认 `443` 处理，避免用户通过查询/重装补链路把旧 `9528` 或其它客户端端口重新写回。
- 保存资产主链接和订单主链接时使用已记录端口写入 `mtproxy_port`，不再信任客户端链接中的端口覆盖记录值。
- `bot/tests.py` 新增保存路径数据库测试，覆盖资产/订单未记录旧端口时拒绝 `9528`，以及已有记录旧端口时继续允许，避免破坏历史真实旧端口资产。

### 监工结果

- 机器人返回链复查通过：`r/i/ri/u/p/ir/im/ar/ac/au/ai` 等短回调入口均已注册，极端 18 位订单 ID、18 位资产 ID、18 位页码下换 IP地区按钮最长样本 61 字节，未超过 Telegram 64 字节限制。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；模型 introspection 显示 `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at` 字段，`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 运行时代码扫描未发现旧计划快照模型、旧退款函数名、旧端口入口或废弃 app 回流；仓库根目录也未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- 真机测试未执行：本轮没有新增用户授权真实云资源成本；未执行真实云资源创建、删除、IP 变更、真实支付或链上广播。

### 验证

已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/tests.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py cloud/services.py cloud/provisioning.py cloud/lifecycle.py cloud/api_orders.py orders/payment_scanner.py
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_monitor_callbacks_<进程号>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_monitor_bot_order_<进程号>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.BotOrderAndBalanceFilterTestCase --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_monitor_lifecycle_<进程号>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_due_orders_use_asset_expiry_for_lightsail_lifecycle cloud.tests.CloudServerServicesTestCase.test_dashboard_order_expiry_update_syncs_asset_expiry_and_lifecycle_plan cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_skips_when_asset_expiry_moved_out_of_due_window cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "<模型字段唯一到期事实 introspection>"
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "<极端 callback 长度样本验证>"
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
git diff --check
```

结果：`manage.py check`、关键模块编译、机器人返回 UI 聚焦测试 41 条、订单/余额/主链接保存聚焦测试 9 条、生命周期/唯一到期事实聚焦测试 10 条、模型字段 introspection、旧字段/旧计划/旧退款/旧端口/废弃 app 扫描、`makemigrations --check --dry-run` 和 `git diff --check` 均通过。`makemigrations --check --dry-run` 仍因沙箱禁止连接本机 MySQL 输出迁移历史检查警告，但最终显示 `No changes detected`。

### 剩余风险

- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 后续若要继续真机验证，需要用户再次明确授权真实云资源成本，并单独写中文报告，脱敏记录云资源 ID。

## 2026-06-03 01:23 自动监工：复核嵌套回调和生命周期事实源

### 范围

本轮从提交 `38c6c37 记录嵌套回调监工复查` 后继续监工。起始读取自动化记忆和 git 状态时工作树干净；复查过程中发现 HEAD 已被并行推进到 `c09526c 记录默认端口监工结果`，该提交仅追加中文版本记录、没有运行代码改动，本轮基于该提交继续记录，没有回滚或覆盖。

本轮使用本地命令复查机器人资产详情、订单详情、续费、续费支付、换 IP、重装、修改配置返回链，继续核对 Telegram `callback_data` 64 字节限制、云资产唯一到期事实、订单旧到期字段、旧计划快照、旧退款入口、旧端口入口和废弃 app 回流。

### 修改

- 本轮未修改运行代码。
- 仅追加本中文版本记录，记录本轮复查、验证结果和并行提交观察。

### 监工结果

- `cloud_detail_callback()`、`cloud_asset_detail_callback()`、`cloud_previous_detail_callback()`、`append_back_callback()` 和短码解析仍有 64 字节兜底；18 位订单 ID、18 位资产 ID、18 位页码和长订单筛选来源组合下，订单详情、资产详情、嵌套资产详情、续费、续费支付、换 IP、重装、修改配置、管理员改到期和返回上一层样本均未超过 64 字节。
- `RetainedIpRenewalUiTestCase` 41 条继续覆盖资产详情、订单详情、续费支付、换 IP、重装、修改配置、默认端口和重装链接端口校验等返回链场景；测试中的预期异常日志来自 mocked postcheck 失败路径，不是本轮新增 bug。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；模型 introspection 显示 `CloudServerOrder` 仅有 `renew_grace_expires_at` 流程字段，未恢复 `service_expires_at` 或 `actual_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 运行时代码扫描未发现旧计划快照模型、旧退款函数名、旧端口入口或废弃 app 回流；命中 `CloudLifecyclePlanNote` 为当前计划备注模型，不是已删除的计划快照表；旧端口命中仅来自测试断言。
- `INSTALLED_APPS` 仍只有 `core`、`bot`、`orders`、`cloud` 等当前 app；仓库根目录未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- 真机测试未执行：本轮没有用户新增授权，也没有明确允许真实云资源成本；未触发真实云资源创建、删除、IP 变更、真实支付或链上广播。

### 验证

已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python -m py_compile bot/api.py bot/handlers.py bot/keyboards.py cloud/services.py cloud/provisioning.py cloud/lifecycle.py cloud/api_orders.py orders/payment_scanner.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py shell -c "<18位ID极端 callback 长度样本验证>"
DJANGO_TEST_SQLITE=1 SQLITE_NAME=/private/tmp/shop_bot_callbacks_monitor_<进程号>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput
DJANGO_TEST_SQLITE=1 SQLITE_NAME=/private/tmp/shop_lifecycle_monitor_<进程号>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_due_orders_use_asset_expiry_for_lightsail_lifecycle cloud.tests.CloudServerServicesTestCase.test_dashboard_order_expiry_update_syncs_asset_expiry_and_lifecycle_plan cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_skips_when_asset_expiry_moved_out_of_due_window cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery --noinput
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py shell -c "<模型字段唯一到期事实 introspection>"
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python - <<'PY'
# INSTALLED_APPS 和废弃 app 目录检查
PY
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py makemigrations --check --dry-run
git diff --check
```

结果：`manage.py check`、关键模块编译、极端 callback 样本、机器人返回 UI 聚焦测试 41 条、生命周期/唯一到期事实聚焦测试 10 条、模型字段 introspection、废弃 app 检查和 `git diff --check` 均通过。`makemigrations --check --dry-run` 仍因沙箱禁止连接本机 MySQL 输出迁移历史检查警告，但最终显示 `No changes detected`。

### 剩余风险

- 未执行真实 Telegram 点击、真实云资源、真实支付、链上广播、生产发布或不可逆操作。
- 后续仍需在用户明确授权真实云资源成本后，单独用中文报告记录服务器创建、删除、IP 变更、附加 IP/固定 IP 变更、人工无订单资产续费、生命周期变化、通知计划和删除计划执行情况，并脱敏云资源 ID。

## 2026-06-03 01:20 自动监工：默认端口与返回链只读复核

### 范围

本轮接续长期自动优化目标，先确认自动化仍为 `ACTIVE`、10 分钟一次、模型 `gpt-5.5`，终端版 `codex-cli` 版本为 `0.135.0-alpha.1`，当前分支为 `codex/cloud-asset-lifecycle-refactor`，工作树起始干净。随后启动终端版 `codex exec` 只读复核机器人返回链、默认端口创建、旧端口入口、旧到期字段、旧计划快照、退款逻辑、废弃 app 回流和测试代码混放。

### 修改

- 本轮未修改运行代码。
- 仅追加本中文版本记录，记录本轮复查、验证结果和 `codex-cli` 监工状态。

### 监工结果

- 本地扫描未发现运行时代码中的旧用户自定义端口入口；默认端口仍为 `443`，钱包直付、余额补付、链上付款确认和换 IP 创建仍走默认端口并直接进入创建流程。
- 本地扫描未发现运行时代码中的旧退款函数、旧到期字段、旧计划模型或废弃 app 目录回流；`CloudAsset.actual_expires_at` 仍是结构化资产到期事实。
- `CloudAssetDashboardSnapshot` 判定为当前代理列表查询快照，不是已移除的计划快照表；真正的 `PlanSnapshot` 运行时代码未发现回流。
- 功能代码与测试代码混放扫描未发现运行时代码中的测试类；`cloud/tests_task_center.py` 是测试文件命名噪音，不属于功能代码混入测试。
- `codex-cli` 中途重点复核短回调、确认按钮、订单列表返回、资产列表返回和手写 callback。中间怀疑“短回调只取单段返回路径”，经源码复核和本地样本验证，相关入口大多使用带上限的 `split(':', N)` 保留完整返回路径，未形成明确可复现 bug。
- 本轮 `codex-cli` 只读会话长时间继续扩展源码阅读但未输出最终 bug 列表；为避免后台残留，已终止该只读进程。终止前未看到明确可复现运行时 bug。

### 验证

已通过：

```bash
uv run python manage.py check
uv run python -m py_compile bot/handlers.py bot/keyboards.py cloud/services.py cloud/provisioning.py orders/payment_scanner.py cloud/api_orders.py
DJANGO_TEST_SQLITE=1 SQLITE_NAME=/private/tmp/shop-monitor-goal-continuation.sqlite3 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase bot.tests.BotOrderAndBalanceFilterTestCase.test_balance_pay_existing_cloud_order_auto_submits_default_port bot.tests.BotOrderAndBalanceFilterTestCase.test_paid_cloud_order_prepare_submits_default_port_directly orders.tests.ChainPaymentScannerTestCase.test_cloud_chain_payment_auto_submits_default_port_provision cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_early_provisioning_steps_preserve_existing_manual_asset_fields --noinput --verbosity 1
DJANGO_SETTINGS_MODULE=shop.settings PYTHONDONTWRITEBYTECODE=1 uv run python -c "<极端 callback 长度样本验证>"
```

结果：`manage.py check` 和关键模块编译通过；聚焦测试 46 条通过；极端 callback 样本均不超过 Telegram 64 字节限制，最长样本为 63 字节。工作树除本版本记录外未产生运行代码改动。

### 剩余风险

- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- `codex-cli` 本轮未自然输出最终报告，而是在长时间只读源码阅读后被手动终止；后续自动化应继续用新的只读会话复核。

## 2026-06-03 01:12 自动监工：复查嵌套回调和唯一到期事实

### 范围

本轮从提交 `5bb3799 整理嵌套回调监工记录` 后继续监工。起始工作树干净；先读取自动化记忆、当前 git 状态和最近提交，再复查机器人返回链、Telegram `callback_data` 64 字节限制、云资产唯一到期事实、订单旧到期字段、旧计划、旧退款入口和废弃 app 回流。

### 修改

- 本轮未修改运行代码。
- 仅追加本中文版本记录，记录本轮复查和验证结果。

### 监工结果

- `cloud_asset_detail_callback()`、`cloud_detail_callback()`、`append_back_callback()` 和短码解析仍覆盖资产详情、订单详情、续费、续费支付、换 IP、重装、修改配置等返回链；18 位 ID 极端样本仍在测试中保持 64 字节以内。
- 确认重新安装按钮使用 `token_urlsafe(6)`，确认 callback 不携带返回链；18 位 ID 下仍低于 Telegram 64 字节限制。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at` 字段；`CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；`CloudAssetDashboardSnapshot` 仅有 `risk_expired` 风险字段。
- 运行时代码扫描旧计划模型、旧退款函数名、旧端口入口和废弃 app，未发现回流；废弃 app 目录也未恢复。
- `makemigrations --check --dry-run` 显示 `No changes detected`，本轮没有表结构变更。

### 验证

已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py cloud/services.py cloud/provisioning.py cloud/api_orders.py
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_monitor_bot.sqlite UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_monitor_lifecycle.sqlite UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_mark_success_preserves_existing_manual_asset_fields_on_update cloud.tests.CloudServerServicesTestCase.test_early_provisioning_steps_preserve_existing_manual_asset_fields cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry cloud.tests.CloudServerServicesTestCase.test_dashboard_order_expiry_update_syncs_asset_expiry_and_lifecycle_plan --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_monitor_asset_api.sqlite UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_proxy_asset_ip_query_exposes_manual_expiry_for_admin_and_user --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudServerOrder, CloudAsset, CloudAssetDashboardSnapshot; print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}]); print('asset_actual_fields', [f.name for f in CloudAsset._meta.fields if f.name == 'actual_expires_at']); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at']); print('default_port', CloudServerOrder._meta.get_field('mtproxy_port').default)"
rg -n "service_expires_at__|\bservice_expires_at\b|\bactual_expires_at\s*=\s*models|\bCloudLifecyclePlan\b|\bCloudNoticePlan\b|\bCloudAutoRenewPlan\b|\bnormalize_service_expiry\b|service_expired_at|\brefund_order\b|\bprocess_refund\b|\bcreate_refund\b|\bissue_refund\b|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded|\brefunded\b|set_cloud_server_port|custom:port|cloud:ipport|bot_set_port|waiting_port" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
```

结果：机器人返回 UI 聚焦测试 41 条通过；生命周期/唯一到期事实聚焦测试 6 条通过；模型字段 introspection 显示 `retired_apps=[]`、订单旧到期字段为空、资产到期字段仅 `actual_expires_at`、默认端口 `443`。旧字段扫描仅命中预期的 `cloud/models.py` 中 `CloudAsset.actual_expires_at` 模型字段。迁移 dry-run 仍因沙箱禁止连接本机 MySQL 输出迁移历史检查警告，但最终显示 `No changes detected`。

### 备注

- 首次运行机器人 SimpleTestCase 未指定 SQLite，导入期配置读取尝试连接本机 MySQL 并输出沙箱拒绝日志，测试最终通过；随后用 SQLite 环境重跑 41 条通过。
- 两组生命周期测试第一次使用了已失效选择器或错误环境变量；已改用当前有效测试名和 `SQLITE_NAME` 临时库重跑通过。
- 本轮未执行真实云资源、真实 Telegram 点击、真实支付、链上广播、生产发布或不可逆操作。

## 2026-06-03 01:02 自动监工：兜底压缩资产详情返回回调

### 范围

本轮接续提交 `60e3173 压缩订单详情列表回调` 后继续监工。先确认自动化仍为 ACTIVE、10 分钟一次、模型 `gpt-5.5`，本地后端 `runserver` 仍在 `127.0.0.1:8000`，未发现 `run.py all` 或 `bot.runner` 常驻。随后调用终端版 `codex exec` 只读复核机器人返回链、默认 443 创建、唯一到期事实、旧端口入口、旧计划和退款回流。

### 修改

- `cloud_asset_detail_callback()` 增加最终 64 字节兜底：普通 `cad:/csd:` 形态不变，遇到资产详情嵌套订单详情、长筛选页码等极端来源时继续压缩来源；仍超长时保留资产详情本身。
- `_compact_back_callback_for_nested_action()` 对深层订单详情来源增加降级：无法完整保留列表页来源时降级为 `d:<订单ID>`，确保后续按钮不超过 Telegram `callback_data` 限制。
- 新增测试覆盖 18 位资产 ID、18 位订单 ID、长订单列表来源组合，确认资产详情返回按钮压缩为 `cad:<资产ID>:d:<订单ID>` 且不超过 64 字节。

### 监工结果

- codex-cli 只读复核指出：`cloud_asset_detail_callback()` 在接收“订单详情 + 长订单列表来源”作为返回路径时，可能生成 90 字节 callback。已在提交 `6bbd550 压缩嵌套资产详情回调` 中修复并补测。
- 旧用户自定义端口入口未发现运行时代码回流；默认端口仍为 `443`，钱包直付、余额补付、链上付款和换 IP 创建仍直接进入创建流程。
- 运行时代码扫描旧到期字段、旧计划模型、退款函数名和废弃 app，未发现回流；`CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- `makemigrations --check --dry-run` 显示 `No changes detected`，本轮没有表结构变更。

### 验证

已通过：

```bash
uv run python -m py_compile bot/keyboards.py bot/tests.py bot/handlers.py
DJANGO_TEST_SQLITE=1 SQLITE_NAME=/private/tmp/shop-monitor-asset-callback.sqlite3 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DJANGO_SETTINGS_MODULE=shop.settings PYTHONDONTWRITEBYTECODE=1 uv run python <极端回调样本检查>
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
git diff --check
```

结果：机器人返回 UI 聚焦测试 40 条通过；极端样本检查 57 个 callback 无超过 64 字节项，最大 64 字节；`manage.py check`、迁移检查和 diff 空白检查均通过。codex-cli 最终只读复核未发现新的明确运行时 bug。

### 剩余风险

- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 极端超长返回来源为满足 Telegram 限制，会保留“返回详情”核心路径并丢弃更深层列表页来源，这是有意降级。

## 2026-06-03 00:55 自动监工：压缩订单详情列表回调并补测余额补付

### 范围

本轮从提交 `14354d8 移除旧端口兼容入口` 后继续监工。起始工作树已有 `bot/handlers.py`、`bot/keyboards.py`、`bot/tests.py` 未提交改动，方向为订单列表详情按钮改用统一回调生成函数；本轮保留并补强该方向，没有回滚用户改动。重点复查机器人资产/订单返回链、Telegram `callback_data` 64 字节限制、用户付款成功后默认 443 直接创建、旧端口入口和迁移状态。

### 修改

- `cloud_server_list()` 的普通订单详情按钮统一使用 `cloud_detail_callback()`，不再手拼 `cloud:detail:<id>:<prefix>:<page>` 长回调。
- `cloud_detail_callback()` 在常规回调超过 64 字节时降级为短订单详情入口 `d:<订单ID>:<短返回来源>`；普通长度仍保持旧 `cloud:detail:*` 形态。
- `cb_cloud_detail` 注册并解析 `d:` 短入口，短来源 `o:/l:` 会在处理器入口还原为现有返回路径。
- `cloud_previous_detail_callback()` 遇到过长订单详情返回路径时复用短回调生成逻辑，避免把超长 `cloud:detail:*` 继续传给下一层按钮。
- 新增聚焦测试锁定 18 位订单 ID、18 位页码和 `profile:orders:cloud:filter:*` 来源组合下订单详情按钮不超过 Telegram 64 字节限制。
- 补齐余额补付已有云服务器订单的普通购买分支测试：余额付款成功后立即调用 `prepare_cloud_server_order_instances(..., 443)`，并提交 `_provision_cloud_server_and_notify` 创建任务。

### 监工结果

- 发现并修复普通订单列表项在 18 位订单 ID、长筛选名和 18 位页码组合下会生成 67 字节 `cloud:detail:*` callback 的风险。
- `_pay_cloud_server_order_with_balance_and_notify()` 的普通余额补付分支已补测，确认不会回到用户自定义端口流程。
- 资产详情、续费、续费支付、更换 IP、更多地区、重新安装、修改配置和订单详情返回链继续保持短回调。
- 旧用户自定义端口入口未发现运行时代码回流；`cloud/ports.py` 中默认端口仍为 `443`，链上付款、钱包直付、余额补付和 IP 变更创建均走默认端口。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；`CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at` 字段；`CloudAssetDashboardSnapshot` 未恢复到期字段，仅有 `risk_expired` 风险字段。
- `makemigrations --check --dry-run` 显示 `No changes detected`，本轮没有表结构变更。

### 验证

已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/keyboards.py bot/handlers.py bot/tests.py cloud/services.py cloud/provisioning.py cloud/api_orders.py bot/api.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput
DB_ENGINE=sqlite DB_NAME=/private/tmp/shop_monitor_balance_<进程号>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.BotOrderAndBalanceFilterTestCase.test_balance_pay_existing_cloud_order_auto_submits_default_port bot.tests.BotOrderAndBalanceFilterTestCase.test_paid_cloud_order_prepare_submits_default_port_directly --noinput
DB_ENGINE=sqlite DB_NAME=/private/tmp/shop_monitor_<进程号>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.BotAdminExpiryUpdateTestCase cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery cloud.tests.CloudServerServicesTestCase.test_early_provisioning_steps_preserve_existing_manual_asset_fields --noinput
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudServerOrder, CloudAsset, CloudAssetDashboardSnapshot; print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}]); print('asset_actual_fields', [f.name for f in CloudAsset._meta.fields if f.name == 'actual_expires_at']); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at']); print('default_port', CloudServerOrder._meta.get_field('mtproxy_port').default)"
rg -n "service_expires_at__|\bservice_expires_at\b|\bactual_expires_at\s*=\s*models|\bCloudLifecyclePlan\b|\bCloudNoticePlan\b|\bCloudAutoRenewPlan\b|\bnormalize_service_expiry\b|service_expired_at|\brefund_order\b|\bprocess_refund\b|\bcreate_refund\b|\bissue_refund\b|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded|\brefunded\b|set_cloud_server_port|custom:port|cloud:ipport|bot_set_port|waiting_port" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
git diff --check
```

结果：机器人返回 UI 聚焦测试 39 条通过；余额补付和默认端口创建聚焦测试 2 条通过；管理员到期修改、兼容入口保留手工到期、无订单资产续费和同步保留聚焦测试 5 条通过；`manage.py check`、关键模块编译和 `git diff --check` 通过；`makemigrations --check --dry-run` 显示 `No changes detected`，但仍因沙箱禁止连接本机 MySQL 输出迁移历史检查警告。

### 剩余风险

- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后继续覆盖删除、附加 IP/固定 IP 变化、生命周期通知计划和删除计划执行情况。

## 2026-06-03 00:42 自动监工：超长返回回调二次压缩

### 范围

本轮从提交 `14354d8 移除旧端口兼容入口` 后继续监工。起始工作树已有 `bot/handlers.py`、`bot/keyboards.py`、`bot/tests.py`、`cloud/services.py` 同方向未提交改动；本轮先核对 diff，再按相关改动继续验证和记录，没有回滚。重点复查机器人返回链、Telegram `callback_data` 64 字节限制、默认端口创建、云资产唯一到期事实、旧计划快照、旧退款入口和废弃 app 回流。

### 修改

- `append_back_callback()` 增加超长保护：常规 callback 保持原格式，只有超过 64 字节时才降级为短动作码和短来源码。
- 新增并注册短动作码：续费 `r:`、更换 IP `i:`、重新安装 `ri:`、修改配置 `u:`、续费支付 `p:`、地区选择 `ir:`、更多地区 `im:`、资产续费/换 IP/改配置 `ar:/ac:/au:`、资产重装 `ai:`。
- 新增短来源码：资产详情 `a:`、云服务器资产详情 `s:`、订单详情 `d:`、代理列表 `l:`、云订单列表 `o:`；短来源会在处理器入口还原为现有详情/列表 callback。
- AWS 常见 region 在超长更换 IP callback 中压缩为短码，处理器收到后还原为原 region code。
- 未绑定资产续费、保留 IP 续费、修改配置支付、修改到期时间继续使用短入口 `arp:`、`rnp:`、`upp:`、`exp:`，并补齐旧入口兼容解析。
- 删除旧 `set_cloud_server_port()` 导出和运行函数，默认端口创建备注统一改为“使用默认端口 443”语义。

### 监工结果

- 18 位订单 ID、18 位资产 ID、18 位列表页码和嵌套资产详情来源组合下，续费、续费支付、更换 IP、更多地区、地区选择、重新安装、修改配置、套餐选择、修改到期时间 callback 均保持不超过 64 字节。
- 普通长度 callback 仍保持原有 `cloud:*` 形态，避免无必要改变旧消息和现有测试预期。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；`CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at` 字段；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 旧计划快照模型、旧退款入口、退款函数名、旧端口 callback、旧端口状态和废弃 app 未发现运行时代码回流。

### 验证

已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/services.py core/texts.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite DB_NAME=/private/tmp/shop_bot_ui_<时间戳>.sqlite3 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --keepdb --noinput
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite DB_NAME=/private/tmp/shop_default_port_<时间戳>.sqlite3 uv run python manage.py test bot.tests.BotOrderAndBalanceFilterTestCase.test_paid_cloud_order_prepare_submits_default_port_directly bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_query_keyboard_includes_reinstall_and_expiry_actions --keepdb --noinput
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite DB_NAME=/private/tmp/shop_chain_port_<时间戳>.sqlite3 uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_cloud_chain_payment_auto_submits_default_port_provision --keepdb --noinput
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite DB_NAME=/private/tmp/shop_sync_<时间戳>.sqlite3 uv run python manage.py test cloud.tests.CloudOrderStatusDashboardSyncTestCase --keepdb --noinput
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite DB_NAME=/private/tmp/shop_cloud_services_<时间戳>.sqlite3 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_aws_notice_schedule_does_not_override_manual_order_expiry cloud.tests.CloudServerServicesTestCase.test_unattached_asset_operation_order_enters_retained_renewal_flow cloud.tests.CloudServerServicesTestCase.test_mark_success_preserves_existing_manual_asset_fields_on_update --keepdb --noinput
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudServerOrder, CloudAsset, CloudAssetDashboardSnapshot; print('CloudAsset expiry fields:', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('CloudServerOrder expiry fields:', [f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('Snapshot expiry fields:', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name])"
rg -n "service_expires_at|service_expired_at|normalize_service_expiry|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|set_cloud_server_port|custom:port|cloud:ipport|bot_set_port|bot_custom_port" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

结果：机器人返回 UI 聚焦测试 37 条通过；默认端口和管理员查询聚焦测试 2 条通过；链上支付默认端口聚焦测试 1 条通过；订单状态同步聚焦测试 8 条通过；手工资产到期保留、无订单资产续费和同步保留聚焦测试 4 条通过；`manage.py check` 通过；`makemigrations --check --dry-run` 显示 `No changes detected`，但仍因沙箱禁止连接本机 MySQL 输出迁移历史检查警告。一次生命周期测试误用旧类名 `CloudAssetManualExpiryPreservationTestCase`，已换当前有效选择器重跑通过。

### 剩余风险

- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 极端超长 callback 采用短来源码时，部分场景为满足 64 字节会只保留“返回详情”，不再保留原列表页码；这是 Telegram 硬限制下的有意降级。

## 2026-06-03 00:28 自动监工：移除旧端口兼容入口

### 范围

本轮继续用 `codex-cli` 只读复查机器人端口流程、生命周期到期事实、计划任务防重复、旧计划快照、退款逻辑、废弃 app 和返回链风险。复查结果里旧端口 callback 仍被识别为兼容入口；结合“不用兼容、直接大改”的目标，本轮彻底移除旧端口入口。

### 修改

- 删除 `CustomServerStates.waiting_port`，机器人不再存在用户端口输入状态。
- 删除旧 `custom:port:default:*`、`custom:port:custom:*`、`cloud:ipport:default:*`、`cloud:ipport:custom:*` callback handler。
- 删除旧端口输入、旧端口兼容相关 Bot 文案键，避免后台文案配置继续出现旧端口流程概念。
- 新增回归测试，锁定旧端口状态、旧端口 callback 和旧端口文案键不得回流。

### 监工结果

- 当前新购、余额支付、链上支付和换 IP 路径均直接使用默认端口 `443`，付款成功后进入创建或恢复流程。
- 精确扫描运行时代码未发现 `waiting_port`、`custom:port:*`、`cloud:ipport:*`、旧端口输入、旧端口按钮或用户自定义端口入口残留。
- `CloudAsset.actual_expires_at` 仍是资产唯一结构化到期事实；`CloudServerOrder.expired_at` 仅用于未付款订单超时，不是服务到期字段。
- 删除/通知执行层已有 `CloudLifecycleTask`、`CloudNoticeTask` 数据库任务支撑和认领去重；后台计划展示仍是实时构建加缓存，不承担防重复。
- 旧计划快照模型、退款入口、退款函数名和废弃 app 未发现运行时代码回流。
- `codex-cli` 仍提示深层二级动作 callback 在超长 ID 下可能逼近 64 字节限制，后续继续收口为短来源码或短 token。

### 验证

本地已通过：

```bash
uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/states/custom.py bot/tests.py core/texts.py
uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --keepdb
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
git diff --check
rg -n "waiting_port|custom:port|cloud:ipport|bot_custom_port_invalid|bot_set_port_failed|bot_custom_port_hint|bot_custom_port_success|旧端口输入|旧端口按钮|自定义端口" bot core cloud orders --glob '!**/migrations/**' --glob '!**/tests.py'
```

结果：机器人返回 UI 聚焦测试 35 条通过；运行时代码未发现旧端口状态、旧端口按钮或用户自定义端口入口残留。

### 剩余风险

- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 深层 callback 长度边界仍需下一轮继续压缩。

## 2026-06-03 00:18 自动监工：返回来源二次压缩

### 范围

提交 `6630481 收口旧端口按钮兼容文案` 后，工作树出现新的机器人返回链收口改动。本轮按相关外部改动处理，未回滚，先核对差异并补充验证。

### 修改

- 续费、续费钱包支付、更换 IP、更多地区、修改配置、修改配置支付和重新安装处理器读取 `back_callback` 时统一调用 `compact_callback_path()`，避免旧长路径继续向下传递。
- `cloud_server_detail()` 中重新安装、继续初始化和修改配置按钮改为 `append_back_callback()`，避免空返回来源时生成尾随冒号，同时继续压缩长返回路径。

### 验证

已通过：

```bash
uv run python -m py_compile bot/handlers.py bot/keyboards.py core/texts.py
uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --keepdb
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
git diff --check
```

结果：机器人返回 UI 聚焦测试 33 条通过，确认资产详情、订单详情、续费、更换 IP、重新安装、修改配置等返回链仍保持短回调并满足 Telegram `callback_data` 64 字节限制。

## 2026-06-03 00:15 自动监工：旧端口文案兼容收口

### 范围

本轮从提交 `f00ad77 补充默认端口自动创建复查` 后继续监工。起始工作树干净，重点复查云资产生命周期唯一到期事实、机器人返回链、Telegram `callback_data` 64 字节限制、旧端口选择残留、订单旧到期字段、旧计划快照、退款旧入口和废弃 app 回流。

### 修改

- 将 `core/texts.py` 中旧端口输入相关提示从“输入/设置端口”收口为“旧端口输入流程已取消，按默认 443 继续创建任务”。
- 将旧 `custom:port:custom:*` 和 `cloud:ipport:custom:*` 兼容入口的用户提示和路由标签改为“旧端口按钮兼容为默认端口 443”，避免运行文本继续表现为自定义端口流程。
- 保留旧 `custom:port:*` 和 `cloud:ipport:*` callback 兼容入口，不删除用户旧消息上的按钮兼容能力；这些入口仍统一按默认 443 提交创建或换 IP 任务。

### 复查结论

- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at` 字段；`CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- `CloudAssetDashboardSnapshot` 未恢复派生到期列，仅保留 `risk_expired` 风险布尔字段。
- 运行代码扫描旧字段、旧计划、旧退款入口后，仅命中预期的 `CloudAsset.actual_expires_at` 模型字段。
- 废弃 app 目录 `accounts/finance/mall/monitoring/dashboard_api/biz` 未恢复；`INSTALLED_APPS` 中也未出现这些废弃 app。
- 机器人返回链聚焦测试确认资产详情、订单详情、续费、更换 IP、重新安装、修改配置等按钮仍保持短回调，并通过 64 字节限制检查。
- 本轮未执行真实云资源、真实 Telegram 点击、真实支付、链上广播、生产发布或不可逆操作。

### 验证

已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py core/texts.py bot/tests.py orders/tests.py cloud/tests.py
DJANGO_TEST_SQLITE=1 SQLITE_NAME=/private/tmp/shop-bot-ui-after-port-text.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DJANGO_TEST_SQLITE=1 SQLITE_NAME=/private/tmp/shop-lifecycle-focus.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_due_orders_use_asset_expiry_for_lightsail_lifecycle cloud.tests.CloudServerServicesTestCase.test_dashboard_order_expiry_update_syncs_asset_expiry_and_lifecycle_plan cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_skips_when_asset_expiry_moved_out_of_due_window cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_read_cached_table_after_initial_refresh cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_order_delete_at_before_cloud_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_order_ip_recycle_at_before_release cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_orphan_asset_delete_time_before_cloud_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_unattached_ip_delete_time_before_release cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_releases_retained_static_ip_after_recycle_due cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_releases_overdue_unattached_static_ip cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_use_actual_expiry_as_delete_plan cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_excludes_cloud_missing_orphan_server --noinput --verbosity 1
DJANGO_TEST_SQLITE=1 SQLITE_NAME=/private/tmp/shop-payment-focus-current.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_chain_payment_conflict_is_not_auto_confirmed orders.tests.ChainPaymentScannerTestCase.test_duplicate_tx_hash_is_not_reused_across_payment_types orders.tests.ChainPaymentScannerTestCase.test_expired_asset_renewal_payment_unbinds_asset_for_retry orders.tests.ChainPaymentScannerTestCase.test_public_asset_renewal_expiry_does_not_claim_unowned_asset orders.tests.ChainPaymentScannerTestCase.test_cloud_chain_payment_auto_submits_default_port_provision cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_skips_when_asset_expiry_moved_out_of_due_window bot.tests.RetainedIpRenewalUiTestCase.test_wallet_balance_purchase_auto_submits_default_port --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudServerOrder, CloudAsset, CloudAssetDashboardSnapshot; print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}]); print('asset_actual_fields', [f.name for f in CloudAsset._meta.fields if f.name == 'actual_expires_at']); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at']); print('default_port', CloudServerOrder._meta.get_field('mtproxy_port').default)"
rg -n "自定义端口|选择端口|端口格式不正确|无法设置端口|端口选择|custom:port|cloud:ipport|cloud_server_port|set_cloud_server_port\\(" bot cloud orders core shop --glob '!**/migrations/**' --glob '!**/tests.py'
rg -n "service_expires_at__|\\bservice_expires_at\\b|\\bactual_expires_at\\s*=\\s*models|\\bCloudLifecyclePlan\\b|\\bCloudNoticePlan\\b|\\bCloudAutoRenewPlan\\b|\\bnormalize_service_expiry\\b|service_expired_at|\\brefund_order\\b|\\bprocess_refund\\b|\\bcreate_refund\\b|\\bissue_refund\\b|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded|\\brefunded\\b" cloud orders bot core shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!docs/**'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

说明：第一次链上/续费聚焦测试使用了旧测试选择器，返回测试类或方法不存在；随后改用当前有效选择器重跑通过。`makemigrations --check --dry-run` 仍因沙箱禁止连接本机 MySQL 输出迁移历史一致性检查警告，但最终显示 `No changes detected`。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail、阿里云、TRONGrid 或 Telegram，未执行真实 Telegram 回调、真实支付、链上广播、云端删机、固定 IP 释放或生产发布。真机方向仍待用户明确授权真实云资源成本后继续验证无订单资产续费、生命周期变化、通知计划、删除计划执行和资源清理。

## 2026-06-03 00:02 自动监工：默认 443 自动创建和生命周期复查

### 范围

本轮继续监工 Shop Django 后端仓库。起始最近提交为 `4441bde 记录短回调返回链复查`，端口策略、链上支付自动创建、默认端口迁移和真机测试报告已在后续提交 `69aeef6 移除机器人自定义端口流程` 中落地。本轮继续复查云服务器购买、链上支付、更换 IP 不再回到端口选择，默认使用 443 自动提交创建；同时复查机器人返回链、Telegram `callback_data` 长度、云资产唯一到期事实、旧到期字段、旧计划快照表、退款旧入口和废弃 app 回流。

### 修改

- 将 `MTPROXY_DEFAULT_PORT` 改为 443，并补充 `CloudServerOrder.mtproxy_port` 默认值迁移。
- 钱包直付、钱包补付、链上云服务器订单支付成功后，直接用默认 443 拆分并提交创建任务，不再要求用户选择 MTProxy 端口。
- 更换 IP 选择地区后直接按默认 443 创建同配置新服务器；旧 `custom:port:*` 和 `cloud:ipport:*` 回调仍兼容，但统一按默认 443 执行。
- 删除已不再使用的端口选择键盘引用，清理运行代码中把缺省端口写死为 9528 的 fallback；已有订单或资产自身保存的旧端口仍优先保留。
- 新增链上云服务器支付聚焦测试，锁定“到账后默认 443 自动提交创建任务”的行为。
- 新增钱包余额直付聚焦测试，锁定“余额支付成功后默认 443 自动提交创建任务”的行为。
- 真机测试报告已纳入仓库，本轮继续对云实例名、固定 IP 名称和公网 IP 做脱敏修正。

### 复查结论

- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at` 字段；`CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- `CloudAssetDashboardSnapshot` 未恢复派生到期列，仅保留 `risk_expired` 风险布尔字段。
- 运行代码扫描未发现旧端口选择文案、旧端口选择键盘引用、旧退款函数名、旧退款状态、旧计划快照模型或旧订单到期字段回流。
- 仓库根下未发现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app 目录恢复；`INSTALLED_APPS` 中也未出现这些废弃 app。
- 本轮未执行真实云资源、真实 Telegram 点击、真实支付或链上广播。

### 验证

已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py orders/payment_scanner.py orders/tests.py cloud/services.py cloud/provisioning.py cloud/bootstrap.py cloud/aws_lightsail.py cloud/aliyun_simple.py cloud/ports.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_test_bot.sqlite3 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --keepdb
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_test_orders.sqlite3 uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_cloud_chain_payment_auto_submits_default_port_provision orders.tests.ChainPaymentScannerTestCase.test_expired_address_payments_are_not_candidates_and_renewal_status_restores orders.tests.ChainPaymentScannerTestCase.test_expired_asset_renewal_payment_unbinds_asset_for_retry orders.tests.ChainPaymentScannerTestCase.test_public_asset_renewal_expiry_does_not_claim_unowned_asset orders.tests.ChainPaymentScannerTestCase.test_renew_pending_cloud_with_previous_ip_is_candidate --keepdb
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_test_cloud.sqlite3 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_mark_success_preserves_existing_manual_asset_fields_on_update cloud.tests.CloudServerServicesTestCase.test_early_provisioning_steps_preserve_existing_manual_asset_fields cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_chain_payment_marks_paid_for_recovery --keepdb
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudServerOrder, CloudAsset, CloudAssetDashboardSnapshot; print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}]); print('asset_actual_fields', [f.name for f in CloudAsset._meta.fields if f.name == 'actual_expires_at']); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at']); print('default_port', CloudServerOrder._meta.get_field('mtproxy_port').default)"
rg -n "or 9528|main_port or 9528|mtproxy_port or 9528|默认端口 9528|默认端口为 9528|默认端口是 9528|等待用户确认 MTProxy 端口|请选择 MTProxy 端口|custom_port_keyboard|cloud_server_change_ip_port_keyboard" bot/handlers.py bot/keyboards.py orders/payment_scanner.py orders/tests.py cloud/services.py cloud/provisioning.py cloud/bootstrap.py cloud/ports.py core/texts.py
rg -n "service_expires_at__|\\bservice_expires_at\\b|\\bactual_expires_at\\s*=\\s*models|\\bCloudLifecyclePlan\\b|\\bCloudNoticePlan\\b|\\bCloudAutoRenewPlan\\b|\\bnormalize_service_expiry\\b|service_expired_at|\\brefund_order\\b|\\bprocess_refund\\b|\\bcreate_refund\\b|\\bissue_refund\\b|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded|\\brefunded\\b" cloud orders bot core shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!docs/**'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

结果：Django 系统检查、关键模块编译、机器人返回 UI 聚焦测试 32 条、链上支付/续费过期聚焦测试 5 条、生命周期/人工字段保留聚焦测试 6 条、迁移 dry-run、模型字段 introspection、端口旧文案/旧键盘扫描、旧计划/旧退款扫描、废弃 app 目录检查和空白检查均通过。旧字段扫描仅命中 `CloudAsset.actual_expires_at` 这个预期唯一资产到期事实字段。`makemigrations --check --dry-run` 仍因沙箱禁止连接本机 MySQL 输出迁移历史检查警告，但最终显示 `No changes detected`。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、阿里云、TRONGrid 或 Telegram，未执行真实 Telegram 点击、真实支付、链上广播、云端删除、固定 IP 释放或生产发布。`docs/real-machine-test-report.md` 已在提交 `69aeef6` 中纳入版本记录并在本轮补充脱敏；后续仍需继续完成无订单资产、灰色地带续费、通知计划、删除计划执行和真实资源清理验证。

## 2026-06-03 00:10 自动监工：端口提交后状态复核

### 范围

本轮从提交 `69aeef6 移除机器人自定义端口流程` 后继续监工。重点确认终端版 Codex、自动化监工配置、工作区脏改动和端口重构后的残留冲突。

### 复查结论

- 终端版 Codex 可执行，版本为 `codex-cli 0.135.0-alpha.1`。
- 自动化 `Shop 自动优化监工` 状态为 `ACTIVE`，工作目录为 `/Users/a399/Desktop/data/shop`，配置为每 10 分钟运行一次。
- 当前运行服务中只看到后端 `manage.py runserver 127.0.0.1:8000 --noreload`，未发现新的 `run.py all` 或 `bot.runner` 常驻进程。
- 运行代码扫描未发现端口选择键盘函数、旧端口选择文案、`custom_port_keyboard` 或 `cloud_server_change_ip_port_keyboard` 回流。
- 测试代码和历史迁移中仍保留 9528 用例，用于验证历史端口兼容；运行代码中旧 `custom:port:*` 和 `cloud:ipport:*` callback 仅作为旧消息兼容入口保留。
- 新增余额支付默认 443 聚焦测试，锁定钱包直付成功后直接提交创建任务，不再进入端口选择。
- 真机测试报告已补充脱敏要求，并将真实云实例名、固定 IP 名称和公网 IP 改为脱敏展示。
- 本轮未执行真实云资源删除、真实支付、链上广播、生产发布或不可逆操作。

### 验证

已通过：

```bash
command -v codex && codex --version
git status --short && git log --oneline -5
rg -n "custom_port_keyboard|cloud_server_change_ip_port_keyboard|默认端口是 9528|等待用户确认 MTProxy|请选择 MTProxy 端口|使用默认端口 9528|输入自定义端口" bot orders cloud core -S
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/tests.py bot/handlers.py orders/payment_scanner.py orders/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-bot-monitor.sqlite3 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity 1 --noinput
UV_CACHE_DIR=/private/tmp/uv-cache-shop DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-payment-monitor.sqlite3 uv run python manage.py test orders.tests.ChainPaymentScannerTestCase --verbosity 1 --noinput
git diff --check
```

结果：自动化配置和终端版 Codex 状态可确认；端口选择旧入口未回流到新机器人流程；关键模块编译、Django 系统检查、迁移 dry-run、机器人聚焦测试 32 条、链上支付扫描聚焦测试 12 条和空白检查均通过。

## 2026-06-02 23:43 自动监工：复查短回调返回链和生命周期事实

### 范围

本轮继续监工 Shop Django 后端仓库。起始最近提交为 `79584b7 压缩资产详情返回回调`；工作树中已有未跟踪文件 `docs/real-machine-test-report.md`，按用户真机测试报告改动处理，本轮未纳入提交。重点复查机器人资产详情、订单详情、续费、更换 IP、重新安装、修改配置等返回链，Telegram `callback_data` 64 字节限制，云资产生命周期唯一到期事实、订单旧到期字段、计划快照表、退款旧入口和废弃 app 回流。

### 复查结论

- 机器人 UI 聚焦测试显示长资产详情来源会压缩为 `cad:<资产ID>:clp:<页码>`，续费、换 IP、重装、修改配置、订单详情等按钮均保持在 Telegram `callback_data` 64 字节限制内。
- 复核 `split(':', N)` 解析后确认当前处理器会把短返回链作为最后一段保留，未发现需要改动的运行代码。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at` 字段；`CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- `CloudAssetDashboardSnapshot` 未恢复派生到期列，仅保留 `risk_expired` 风险布尔字段。
- 严格排除迁移、测试和文档后，运行时代码未发现旧计划快照模型、旧退款函数名、旧退款状态或旧订单到期字段 ORM 回流。
- 仓库根下未发现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app 目录恢复；`INSTALLED_APPS` 中也未出现这些废弃 app。
- 本轮未执行真实云资源、真实 Telegram 点击、真实支付或链上广播。

### 验证

已通过：

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/services.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/api_tasks.py cloud/api_asset_edit.py cloud/api_orders.py orders/payment_scanner.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --keepdb --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_due_orders_use_asset_expiry_for_lightsail_lifecycle cloud.tests.CloudServerServicesTestCase.test_mark_success_preserves_existing_manual_asset_fields_on_update cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_sync_aws_retained_ip_preserves_existing_asset_user orders.tests.ChainPaymentScannerTestCase.test_expired_asset_renewal_payment_unbinds_asset_for_retry orders.tests.ChainPaymentScannerTestCase.test_public_asset_renewal_expiry_does_not_claim_unowned_asset --keepdb --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudServerOrder, CloudAsset, CloudAssetDashboardSnapshot; print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}]); print('asset_actual_fields', [f.name for f in CloudAsset._meta.fields if f.name == 'actual_expires_at']); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at'])"
rg -n "service_expires_at|service_expired_at|normalize_service_expiry|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

结果：Django 系统检查、关键模块编译、机器人返回 UI 聚焦测试 31 条、生命周期/同步/支付聚焦测试 8 条、迁移 dry-run、模型字段 introspection、旧字段/旧计划/旧退款扫描、废弃 app 目录检查和空白检查均通过。`makemigrations --check --dry-run` 仍因沙箱禁止连接本机 MySQL 输出迁移历史检查警告，但最终显示 `No changes detected`。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、真实 AWS Lightsail、阿里云、TRONGrid 或 Telegram，未执行真实 Telegram 点击、真实支付、链上广播、云端删除、固定 IP 释放或生产发布。`docs/real-machine-test-report.md` 仍为进入本轮前已有未跟踪文件，需真机测试流程单独确认和提交。

## 2026-06-02 23:22 自动监工：复查生命周期唯一到期事实

### 范围

本轮继续监工 Shop Django 后端仓库。起始工作树干净，最近提交为 `1bdec9d 记录机器人返回链复查`；重点复查云资产生命周期唯一到期事实、订单旧到期字段、计划快照表、退款旧入口、废弃 app 回流，以及上一轮机器人详情返回链修复后的聚焦回归。

### 复查结论

- `CloudServerOrder` 当前模型未恢复 `service_expires_at` 或 `actual_expires_at` 字段，`CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- `CloudAssetDashboardSnapshot` 未恢复派生到期列，当前只保留 `risk_expired` 风险布尔字段。
- `INSTALLED_APPS` 未出现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app，仓库根目录也未恢复这些旧 app 目录。
- 严格排除迁移、测试和文档后，运行时代码未发现旧计划快照模型、旧退款函数名、旧退款状态或旧订单到期字段 ORM 回流。
- 本轮未修改运行代码；只补充本轮中文版本记录。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py bot/handlers.py cloud/models.py cloud/asset_expiry.py cloud/dashboard_snapshots.py cloud/services.py cloud/provisioning.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/lifecycle_tasks.py cloud/api.py cloud/api_assets.py cloud/api_asset_edit.py cloud/api_orders.py cloud/api_tasks.py cloud/api_sync.py cloud/api_monitors.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py orders/services.py orders/payment_scanner.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --keepdb --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_due_orders_use_asset_expiry_for_lightsail_lifecycle cloud.tests.CloudServerServicesTestCase.test_mark_success_preserves_existing_manual_asset_fields_on_update cloud.tests.CloudServerServicesTestCase.test_early_provisioning_steps_preserve_existing_manual_asset_fields cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_sync_aws_retained_ip_preserves_existing_asset_user --keepdb --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_expired_asset_renewal_payment_unbinds_asset_for_retry orders.tests.ChainPaymentScannerTestCase.test_public_asset_renewal_expiry_does_not_claim_unowned_asset --keepdb --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudServerOrder, CloudAsset, CloudAssetDashboardSnapshot; print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}]); print('asset_actual_fields', [f.name for f in CloudAsset._meta.fields if f.name == 'actual_expires_at']); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at'])"
rg -n "service_expires_at__|\\bservice_expires_at\\b|\\bactual_expires_at\\s*=\\s*models|\\bCloudLifecyclePlan\\b|\\bCloudNoticePlan\\b|\\bCloudAutoRenewPlan\\b|\\bnormalize_service_expiry\\b|service_expired_at|\\brefund_order\\b|\\bprocess_refund\\b|\\bcreate_refund\\b|\\bissue_refund\\b|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded|\\brefunded\\b" cloud orders bot core shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!docs/**'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
```

结果：Django 系统检查、关键模块编译、机器人返回 UI 聚焦测试 29 条、云资产到期事实源/同步保留聚焦测试 7 条、支付续费过期聚焦测试 2 条、迁移 dry-run、模型字段 introspection、旧字段/旧计划/旧退款扫描和废弃 app 目录检查均通过。`makemigrations --check --dry-run` 仍因沙箱禁止连接本机 MySQL 输出迁移历史检查警告，但最终显示 `No changes detected`。首次误用了旧测试类选择器，已改用当前有效测试选择器重跑通过。

剩余风险：本轮未执行真实 Telegram 点击、钱包扣款、云端创建删除、固定 IP 释放、生产发布、合并或不可逆操作。

## 2026-06-02 23:18 自动监工：复查机器人返回链和自动化状态

### 范围

本轮继续执行 10 分钟自动优化监工，重点确认最新机器人详情返回链修复没有引入新回归，同时复核自动化配置、前后端本地服务、废弃 app、旧到期字段、旧计划快照表和退款逻辑未回流。

### 复查结论

- 当前分支为 `codex/cloud-asset-lifecycle-refactor`，本轮开始和结束工作区均为干净状态。
- `Shop 自动优化监工` 自动化仍为 `ACTIVE`，计划为 `FREQ=MINUTELY;INTERVAL=10`，运行目录为 `/Users/a399/Desktop/data/shop`。
- 后端 `manage.py runserver 127.0.0.1:8000 --noreload` 和前端 Vite 开发服务仍在运行。
- 终端版 `codex-cli` 使用模型 `gpt-5.5` 对 `HEAD~2` 之后的机器人返回链改动完成 review，结论为未发现引入的正确性问题。
- 本轮未发现需要修改的运行代码；未恢复旧 app、旧到期字段、旧计划快照表或退款逻辑。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/services.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/api_tasks.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at|service_expired_at|normalize_service_expiry|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

结果：Django 系统检查、关键模块编译、机器人返回 UI 聚焦测试 29 条、迁移 dry-run、旧字段/旧计划/旧退款扫描、废弃 app 目录检查和空白检查均通过。

剩余风险：本轮未执行真实 Telegram 点击、钱包扣款、云端创建删除、固定 IP 释放、生产发布、合并或不可逆操作。

## 2026-06-02 23:14 自动监工：修复机器人详情返回链

### 范围

本轮针对机器人“有的返回没有正确返回上一层”的问题，继续复查云代理详情、订单详情、续费、更换 IP、重新安装、修改配置、管理员改时间等按钮链路，重点处理旧回调格式和 Telegram `callback_data` 64 字节限制导致的返回失败。

### 修复内容

- 资产详情入口同时兼容 `cloud:ad:<kind>:<id>`、旧 `cloud:assetdetail:<id>`、旧 `cloud:assetdetail:<kind>:<id>` 三种格式，参数错误时直接提示，不再让旧按钮触发异常。
- 旧资产详情返回路径进入详情后立即压缩，子按钮继续沿用同一条短返回链，避免续费、重装、修改配置、管理员改时间回到错误页面。
- 订单详情列表按钮改为 `cloud:orderdetail:<id>:poc:<筛选>:<页码>`，详情处理器直接压缩后缀返回路径，避免订单 ID 较长时超过 Telegram 限制。
- 资产续费套餐、保留 IP 续费套餐、等待用户补充代理链接、资产重装、管理员改时间等状态流转都会重新压缩已保存的返回路径。
- 补充回归测试，覆盖订单详情短回调、旧资产详情双格式、短返回处理器和按钮长度限制。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py
git diff --check
rg -n "service_expires_at|service_expired_at|normalize_service_expiry|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
```

结果：机器人返回 UI 聚焦测试 29 条、Django 系统检查、关键文件编译、空白检查、旧字段/旧计划/旧退款扫描、废弃 app 目录检查均通过。

剩余风险：本轮未执行真实 Telegram 点击、钱包扣款、云端删机、固定 IP 释放或真实云账号创建删除。

## 2026-06-02 23:03 自动监工：压缩机器人订单返回回调

### 范围

本轮接续机器人云代理返回链收口，重点复查嵌套订单列表来源 callback 在续费、更换 IP、重新安装、修改配置等二级按钮中可能接近或超过 Telegram `callback_data` 长度限制的问题，同时确认旧资产详情回调、云资产到期事实源、旧计划快照表、旧退款入口和废弃 app 未回流。

### 修复内容

- 新增 `poc:<筛选>:<页码>` 短返回路径，用于压缩 `profile:orders:cloud...` 订单列表来源；机器人处理器注册 `poc:` 回调并恢复到对应云订单列表筛选页。
- `cloud_detail_callback()`、`cloud_asset_detail_callback()`、`append_back_callback()`、订单详情和只读订单详情键盘统一压缩可识别的返回路径，减少嵌套操作按钮长度。
- 旧 `cloud:assetdetail:<id>` 返回路径压缩为新 `cloud:ad:asset:<id>` 形态；`cloud:ad:`、`cloud:detail:` 和旧资产详情嵌套返回路径会递归压缩，并修复处理器解析旧格式时的 `kind/id` 兼容分支。
- 补充回归测试，确认订单筛选页来源会压缩为 `poc:`、按钮 callback 长度不超过 64 字节、旧资产详情格式和新短格式都可被处理器识别。

### 复查结论

- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；`CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at` 服务到期字段。
- 严格运行时代码扫描未发现旧计划快照模型、旧退款函数名、旧退款状态或旧到期字段命名回流。
- 仓库根下未发现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app 目录恢复；当前 `dashboard_api` 命中仍为 `core.dashboard_api` 和 URL namespace。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/services.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/api_tasks.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity 2
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at|service_expired_at|normalize_service_expiry|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|refund_to_balance|refund_balance|refund_order|process_refund|create_refund|issue_refund|STATUS_REFUNDED|status\\s*=\\s*['\\\"]refunded['\\\"]|STATUS_CHOICES.*refunded" bot orders cloud core shop -g '!**/migrations/**' -g '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \)
git diff --check
```

结果：Django 系统检查、关键模块编译、机器人返回 UI 聚焦测试 27 条、迁移 dry-run、旧字段/旧计划/旧退款扫描、废弃 app 目录检查和空白检查均通过。迁移 dry-run 仍因沙箱禁止连接本机 MySQL 输出一致性历史检查警告，但无模型变更。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail、阿里云、TRONGrid 或 Telegram，未执行真实 Telegram 回调、钱包扣款、自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 22:40 自动监工：本地巡检和 CLI 监工状态

### 范围

本轮继续执行自动优化监工目标，重点确认当前仓库状态、10 分钟自动化配置、终端版 Codex 可用性、生命周期计划修复后的聚焦测试、机器人返回 UI 回归、旧到期字段/旧计划/旧退款入口回流情况。

### 复查结论

- 当前分支工作区起始状态干净，最近提交停在 `ed4d8f3 补充流程时间验证记录`。
- `Shop 自动优化监工` 自动化仍为 `ACTIVE`，运行目录为 `/Users/a399/Desktop/data/shop`，计划为每 10 分钟一次。
- 终端版 Codex 可用，版本为 `codex-cli 0.135.0-alpha.1`，默认 review 使用配置模型 `gpt-5.5`。
- 本地验证未发现新的明确代码缺陷；旧字段、旧计划快照表和旧退款入口扫描无命中。
- `codex exec review --base HEAD~3` 已启动并读取了最近三次提交和生命周期相关代码，但最终因服务端 `525` 中断，未产出完整 review 结论；该问题属于外部 CLI/API 连接失败，本轮不基于不完整结论改代码。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_schedule_preserves_stored_delete_and_recycle_after_status_progress cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_revives_deleted_order_when_instance_exists bot.tests.RetainedIpRenewalUiTestCase --verbosity 1
rg -n "service_expires_at|service_expired_at|normalize_service_expiry|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
```

未完成：

```bash
codex exec review --base HEAD~3
```

结果：Django 系统检查、生命周期/AWS 恢复已删除订单/机器人返回 UI 聚焦测试 25 条、旧字段/旧计划/旧退款扫描均通过。Codex CLI 独立 review 因上游 `525` 中断，等待下一轮自动化继续复查。

剩余风险：本轮未连接真实 MySQL、AWS Lightsail、阿里云或 TRONGrid，未执行真实后台编辑、Telegram 回调、钱包扣款、自动续费支付、云端删机或固定 IP 释放；CLI review 未拿到完整结论。

## 2026-06-02 22:31 自动监工：保留已进入流程的生命周期时间

### 范围

本轮在 `2c159ab 记录兼容备注匹配测试` 后继续处理新出现的 `cloud/lifecycle.py` 同主题改动，重点确认已进入关机、删机或固定 IP 回收流程的订单不会因为资产侧到期时间变化而让展示/通知计划重新漂移，同时复查旧到期字段、旧计划快照表、旧退款入口和废弃 app 回流。

### 复查结论

- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；本轮只是控制从资产事实派生出的流程时间展示优先级，未恢复订单到期字段。
- 订单状态已进入 `suspended/deleting/deleted` 后，已写入的 `delete_at` 或 `ip_recycle_at` 是当前流程执行计划，应优先于重新按资产到期时间派生出的未来时间。
- 严格运行时代码扫描未发现旧字段、旧计划、旧退款入口回流；废弃 app 目录未恢复。

### 修复内容

- `_deferred_lifecycle_time()` 增加 `prefer_stored` 参数；`_notice_schedule()` 对 `suspended/deleting` 订单优先保留 `delete_at`，对 `deleted` 订单优先保留 `ip_recycle_at`。
- 补充聚焦测试，确认资产到期时间被推远后，已进入流程的删机和 IP 回收计划仍保留订单侧已存执行时间。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py bot/handlers.py bot/keyboards.py cloud/api_asset_edit.py cloud/api_servers.py cloud/lifecycle.py cloud/server_records.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py cloud/management/commands/refresh_lifecycle_plans.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_schedule_preserves_stored_delete_and_recycle_after_status_progress cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_order_delete_at_before_cloud_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_order_ip_recycle_at_before_release
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests --verbosity 1 --failfast
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity 1
rg -n "service_expires_at|service_expired_at|normalize_service_expiry|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

结果：Django 系统检查、关键模块编译、迁移 dry-run、通知计划保留已存执行时间、资产到期事实源和生命周期执行前复核 4 条聚焦测试、`cloud.tests` 全量 360 条、机器人返回 UI 回归 23 条、旧字段/旧计划/旧退款扫描、废弃 app 目录检查和空白检查均通过。

剩余风险：本轮未连接真实 MySQL、AWS Lightsail、阿里云或 TRONGrid，未执行真实后台编辑、Telegram 回调、钱包扣款、自动续费支付、云端删机或固定 IP 释放。

## 2026-06-02 22:26 自动监工：兼容记录创建保护人工备注

### 范围

本轮在 `69c7775 补充资产备注同步记录` 后继续处理新出现的 `cloud/server_records.py` 同主题改动，重点确认兼容 `Server.objects.create()` 不会把同订单的新实例/IP 写入误合并到已有人工备注记录，同时继续复查旧到期字段、旧计划快照表、旧退款入口和废弃 app 回流。

### 复查结论

- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；本轮未修改订单到期字段、计划快照表或退款逻辑。
- 兼容服务器记录在有明确新 IP、实例 ID、资源 ID 或名称时，不应仅凭同订单回退覆盖任意旧兼容记录；带人工备注的新记录应新建，避免污染历史备注和身份字段。
- 严格运行时代码扫描未发现旧字段、旧计划、旧退款入口回流；废弃 app 目录未恢复。

### 修复内容

- `Server.objects.create()` 的同订单回退匹配仅在 payload 没有 IP/实例/资源 ID/名称身份时使用；有身份但未命中现有记录时允许创建新兼容记录。
- 补充聚焦测试，确认同订单新身份且带备注的兼容服务器创建不会覆盖已有人工备注记录。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/server_records.py cloud/tests.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_server_create_with_new_identity_does_not_overwrite_same_order_note cloud.tests.CloudServerServicesTestCase.test_upsert_cloud_asset_keeps_same_instance_with_different_ips cloud.tests.CloudServerServicesTestCase.test_manual_cloud_asset_note_edit_still_overwrites
```

结果：Django 系统检查、相关模块编译、同订单新身份兼容记录保护、同实例不同 IP 分离和人工备注同步 3 条聚焦测试均通过。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail、阿里云或 TRONGrid，未执行真实后台编辑、Telegram 回调、钱包扣款、自动续费支付、云端删机或固定 IP 释放。

## 2026-06-02 22:30 自动监工：保留订单流程时间优先级

### 范围

本轮继续处理新出现的 `cloud/lifecycle.py` 同主题改动，重点确认 suspended/deleting/deleted 订单的已存删机时间和 IP 回收时间不会被资产到期重新计算覆盖，同时复查旧到期字段、旧计划快照表、旧退款入口和废弃 app 回流。

### 复查结论

- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；本轮只调整订单流程时间在通知计划中的选择优先级，不恢复订单服务到期字段。
- suspended/deleting 订单的 `delete_at`、deleted 订单的 `ip_recycle_at` 属于已进入流程后的事实时间，通知计划应优先展示这些已存值。

### 修复内容

- `_deferred_lifecycle_time()` 增加 `prefer_stored` 参数；`_notice_schedule()` 对 suspended/deleting 的 `delete_at` 和 deleted 的 `ip_recycle_at` 优先使用订单已存值。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/lifecycle.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_plan_text_shows_configured_execution_time cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry cloud.tests.CloudServerServicesTestCase.test_shutdown_log_items_prefer_order_lifecycle_schedule cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_separate_order_plan_note cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_order_delete_at_before_cloud_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_order_ip_recycle_at_before_release
```

结果：生命周期编译、通知文本/资产到期计划/自定义流程时间/独立计划备注/执行前复核 6 条聚焦测试均通过。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail、阿里云或 TRONGrid，未执行真实通知发送、云端删机或固定 IP 释放。

## 2026-06-02 22:25 自动监工：手工备注同步兼容记录

### 范围

本轮在 `d1f8b53 补充生命周期验证记录` 后继续收口后台资产编辑路径；期间代码改动已由外部提交为 `dbb87d9 同步资产编辑备注到兼容记录`，本条记录补齐对应中文版本说明。重点确认人工备注编辑、系统备注更新、AWS 同步保留人工备注、旧到期字段、旧计划快照表、旧退款入口和废弃 app 回流。

### 复查结论

- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；本轮未修改订单到期字段、计划快照表或退款逻辑。
- 后台人工编辑资产 `note` 属于显式人工保存，应同步到关联兼容 `Server` 记录；系统备注和云同步路径仍保留既有人工备注，不覆盖 `CloudAsset.note` 或兼容记录备注。
- 严格运行时代码扫描未发现旧字段、旧计划、旧退款入口回流；废弃 app 目录未恢复。

### 修复内容

- 后台资产编辑在 payload 显式包含 `note` 时，将备注加入关联兼容 `Server` 记录同步字段，和资产名、IP、实例 ID、资源 ID 保持一致。
- 复用已有聚焦测试确认人工备注编辑会覆盖资产与兼容记录备注，同时系统备注更新和 AWS 同步仍不覆盖人工备注。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_asset_edit.py cloud/tests.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_manual_cloud_asset_note_edit_still_overwrites cloud.tests.CloudServerServicesTestCase.test_system_note_updates_preserve_manual_primary_record_notes cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_manual_asset_note
```

结果：Django 系统检查、相关模块编译、人工备注覆盖/系统备注保留/AWS 同步保留人工备注 3 条聚焦测试均通过。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail、阿里云或 TRONGrid，未执行真实后台编辑、Telegram 回调、钱包扣款、自动续费支付、云端删机或固定 IP 释放。

## 2026-06-02 22:22 自动监工：生命周期执行线程和固定 IP 名称回退

### 范围

本轮继续监工 Shop Django 后端仓库。起始工作树已有同主题未提交改动，最近提交先后从 `d1e8f44 同步固定IP保留期关联资产状态` 前移到外部提交 `fbe4136 收口未附加IP执行窗口和风险识别`；本轮在不回滚外部改动的前提下，重点复查未附加固定 IP 删除窗口、生命周期异步执行、固定 IP 名称解析、旧到期字段、旧计划快照表、旧退款入口和废弃 app 回流。

### 复查结论

- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；`CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at` 服务到期字段。
- 严格运行时代码扫描未发现旧订单到期字段 ORM 查询或写入、旧计划快照模型、旧退款函数名、`STATUS_REFUNDED/refunded` 状态回流。
- 仓库根下未发现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app 目录恢复；当前 `core.dashboard_api` 和 `core.cloud_accounts` 命中仍属于共享 helper。

### 修复内容

- 生命周期 tick 在 `DJANGO_TEST_SQLITE=1` 时对同步数据库执行函数启用 `thread_sensitive`，避免 SQLite 聚焦测试在异步线程间复用连接时出现不稳定。
- 未附加固定 IP 到期候选增加稳定排序，优先处理带 `StaticIp` 资源 ID 的资产，再按资产到期时间和更新时间排序，减少保留期影子资产与普通未附加资产混排。
- AWS 固定 IP 释放名称回退改为优先从 `CloudAsset.provider_resource_id` 的 `StaticIp/...` 解析，只有无法识别固定 IP 资产时才回退资产名，降低陈旧 `asset_name` 导致真实释放目标错误的风险。
- 后台资产手工编辑备注时同步关联兼容 server 记录，保持代理资产详情、删除计划备注和兼容服务器列表展示一致。
- `Server` 兼容写入带实例、资源 ID、IP 或名称身份字段但找不到匹配记录时，不再退回覆盖同订单任意旧兼容记录，避免备注或身份同步误写不相关 server 记录。
- 补充后台资产风险识别测试，锁定原始 `provider_status` 中的“固定IP保留中”能进入未附加固定 IP 风险筛选；补充固定 IP 名称回退测试，锁定资源 ID 优先于陈旧资产名。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py cloud/api_assets.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_dashboard_unattached_ip_plan_run_uses_ip_delete_time_window cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_respects_shutdown_disabled_asset cloud.tests.CloudServerServicesTestCase.test_cloud_asset_unattached_filter_uses_raw_provider_status cloud.tests.CloudServerServicesTestCase.test_lifecycle_aws_resource_resolution_prefers_ip
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_releases_retained_static_ip_after_recycle_due cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_releases_retained_static_ip_when_asset_already_deleted cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_releases_overdue_unattached_static_ip cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_unattached_ip_uses_ip_delete_time_window
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_system_note_updates_preserve_manual_primary_record_notes cloud.tests.CloudServerServicesTestCase.test_manual_cloud_asset_note_edit_still_overwrites cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_refreshes_unattached_ip_delete_plan cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_upsert_cloud_asset_keeps_server_records_separated_by_account cloud.tests.CloudServerServicesTestCase.test_reconcile_cloud_assets_matches_legacy_account_label_variants cloud.tests.CloudServerServicesTestCase.test_reconcile_cloud_assets_does_not_match_cross_region_same_instance_without_ip
rg -n "service_expires_at|service_expired_at|normalize_service_expiry|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

结果：Django 系统检查、关键模块编译、迁移 dry-run、未附加固定 IP 后台执行窗口/关机计划/风险识别和 AWS 资源名称解析 4 条聚焦测试、保留/未附加固定 IP 生命周期释放 4 条聚焦测试、4 条备注/资产编辑同步测试、4 条兼容 server 归一和匹配边界测试、旧字段/旧计划/旧退款扫描、废弃 app 目录检查和空白检查均通过。`makemigrations --check --dry-run` 仍因沙箱无法连接默认 MySQL 输出迁移历史检查警告，但最终无模型变更。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail、阿里云或 TRONGrid，未执行真实 Telegram 回调、钱包扣款、自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 22:00 自动监工：兼容服务器记录和生命周期计划收口

### 范围

本轮继续监工 Shop Django 后端仓库。起始工作树已有同主题未提交改动，最近提交为 `5a5bc33 记录旧到期命名清零复验`；本轮在不回滚外部改动的前提下，重点复查云资产生命周期计划缓存、未附加固定 IP 删除历史、Server 兼容记录、AWS/阿里云同步缺失确认、废弃 app 回流、旧到期字段、旧计划快照表和旧退款入口。

### 复查结论

- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；`CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at` 服务到期字段。
- 严格运行时代码扫描未发现旧订单到期字段 ORM 查询或写入、旧计划快照模型、旧退款函数名、`STATUS_REFUNDED/refunded` 状态，或云账号级关机计划判断回流。
- `INSTALLED_APPS` 未出现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app；本轮新增的 `Server` 兼容标记只用于当前 `CloudAsset` 表内的历史兼容记录识别，不恢复旧 app 或旧表。

### 修复内容

- 生命周期计划接口增加进程内缓存和按 `CloudAsset.updated_at`、`CloudIpLog.created_at` 的失效判断；刷新命令改为写入同一缓存路径，避免重复构建计划。
- 未附加固定 IP 删除项改为直接从 `CloudAsset` 查询，补齐非活跃、已释放和历史删除记录，并给删除项补充 `PLAN_KIND_UNATTACHED_IP_DELETE`，避免删除尝试次数在接口装饰时丢失。
- `Server` 兼容写入统一打 `sync_state.compat_server_record` 标记；后台删除服务器列表记录只允许删除兼容记录，并按订单、实例、资源 ID 或 IP 清理关联兼容记录，不把真实资产误当旧服务器入口。
- `Server.objects.get()` 在历史重复兼容记录未归一前增加容错选择，优先返回唯一兼容记录或最新候选，避免兼容壳因重复记录直接抛出 `MultipleObjectsReturned`。
- 资产后台编辑同步关联兼容记录的名称、IP、实例 ID、资源 ID 和备注；手工用户、手工到期和人工备注继续保留，不覆盖 `CloudAsset.actual_expires_at`。
- 归一命令可合并重复兼容记录、解析云账号标签并清理活跃但无效的兼容记录；已终态 deleted/terminated 的残留记录保留为历史状态，不硬删。
- AWS 同步解析真实资产时优先非兼容 `CloudAsset`，云端实例恢复后清理旧“已标记删除”备注，并只从真实资产向兼容记录传播状态；阿里云缺失确认继续处理有订单绑定的兼容记录，但跳过无订单的空白兼容记录。
- 实例删除进入固定 IP 保留期时，同订单的其他服务器资产同步标记为 deleted、清空实例标识，并以 `order.ip_recycle_at` 写入资产侧 `actual_expires_at`，避免订单主资产与兼容资产在保留期内状态分叉。
- 生命周期 tick 在 `DJANGO_TEST_SQLITE=1` 下使用 thread-sensitive DB 调用，避免 SQLite 测试线程新连接看不到表；AWS 固定 IP 名称回退复用资产资源 ID 解析，减少固定 IP 释放时使用过期资产名。
- 后台手动执行未附加固定 IP 删除时复用生命周期 IP 删除时间窗口，但仅在资产已到删除时间且关机计划允许时拦截，保留“未到 IP 删除时间”和“关机计划已关闭”的具体错误。
- 后台资产风险识别同时读取展示态和原始 `provider_status`，避免状态标签折叠后漏掉“固定IP保留中”等未附加固定 IP 资产。
- TRON 资源监控的 `trongrid_base_url` 优先读取运行时配置，未配置时再回退原异步配置。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py cloud/api_asset_edit.py cloud/api_servers.py cloud/management/commands/reconcile_cloud_assets_from_servers.py cloud/management/commands/refresh_lifecycle_plans.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/sync_aws_assets.py cloud/resource_monitor.py cloud/server_records.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_separate_order_plan_note cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_excludes_cloud_missing_orphan_server cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_keeps_asset_remarks_out_of_execution_status cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ignore_account_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_asset_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_route_linked_asset_delete_to_order_item cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_move_deleted_orphan_server_out_of_future cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_compact_request_keeps_ip_delete_history_item cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_ip_delete_history_item cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_real_released_retained_ip_history_without_active_row cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_sort_shutdown_items_by_delete_time cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_group_same_delete_time_by_user cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_move_deleted_unattached_ip_active_row_to_history cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_future_server_plan_item cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_compute_orphan_server_delete_after_suspend_window cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_unattached_ip_show_confirmation_progress_in_state_and_note cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_unattached_ip_show_delete_attempt_in_state_and_note cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_read_cached_table_after_initial_refresh cloud.tests.CloudServerServicesTestCase.test_lifecycle_plan_counts_match_proxy_list_assets --noinput
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_delete_server_only_removes_server_record cloud.tests.CloudServerServicesTestCase.test_delete_server_does_not_fallback_to_asset_id cloud.tests.CloudServerServicesTestCase.test_reconcile_cloud_assets_skips_deleted_server_residual cloud.tests.CloudServerServicesTestCase.test_reconcile_cloud_assets_does_not_match_cross_provider_instance_id cloud.tests.CloudServerServicesTestCase.test_reconcile_cloud_assets_preserves_server_account_label cloud.tests.CloudServerServicesTestCase.test_reconcile_cloud_assets_skips_inactive_cloud_account_server cloud.tests.CloudServerServicesTestCase.test_reconcile_cloud_assets_skips_server_marked_cloud_missing cloud.tests.CloudServerServicesTestCase.test_reconcile_cloud_assets_matches_legacy_account_label_variants cloud.tests.CloudServerServicesTestCase.test_reconcile_cloud_assets_does_not_match_cross_region_same_instance_without_ip cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry --noinput
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_rebinds_unattached_ip_when_instance_reappears cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_updates_retained_asset_after_renewal_recovery cloud.tests.CloudServerServicesTestCase.test_sync_aws_retained_ip_preserves_existing_asset_user cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_sync_aws_unattached_ip_duplicate_cleanup_is_account_scoped cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_manual_asset_note cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_keeps_runtime_running_when_order_is_suspended cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_revives_dirty_deleted_asset_when_instance_exists cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_revives_deleted_order_when_instance_exists cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry --noinput
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_instance_requires_five_passes_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_check_uses_previous_public_ip_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_blank_asset_does_not_delete_unrelated_blank_server cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_missing_instance_requires_five_passes_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_missing_blank_asset_does_not_delete_unrelated_blank_server --noinput
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print([app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudServerOrder, CloudAsset; print([f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}]); print([f.name for f in CloudAsset._meta.fields if f.name == 'actual_expires_at'])"
rg -n "service_expires_at__|(filter|exclude|update|order_by|values|values_list)\([^\n)]*service_expires_at|service_expires_at\s*=\s*models|\bnormalize_service_expiry\b|service_expired_at|class Cloud(LifecyclePlan|NoticePlan|AutoRenewPlan)\b|CloudLifecyclePlan\.|CloudNoticePlan\.|CloudAutoRenewPlan\.|\brefund_order\b|\bprocess_refund\b|\bcreate_refund\b|\bissue_refund\b|\brefund_to_balance\b|\brefund_balance\b|STATUS_REFUNDED|status=['\"]refunded|cloud_account__shutdown_enabled|CloudAccountConfig\.shutdown_enabled" cloud orders bot core shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!docs/**'
git diff --check
```

结果：Django 系统检查、关键模块编译、迁移 dry-run、19 条生命周期计划测试、11 条兼容 Server/归一/手工到期测试、10 条 AWS/阿里云同步恢复与保留测试、5 条缺失确认测试、模型字段 introspection、旧字段/旧计划/旧退款扫描和空白检查均通过。`makemigrations --check --dry-run` 仍因沙箱无法连接默认 MySQL 输出迁移历史检查警告，但最终无模型变更。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail、阿里云或 TRONGrid，未执行真实 Telegram 回调、钱包扣款、自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 21:42 自动监工：旧到期命名清零复验

### 范围

本轮继续监工 Shop Django 后端仓库。起始读取状态时捕捉到同主题外部改动，刷新后工作树已由 `bd4fec6 记录运行时旧到期命名清零` 收口并保持干净；本轮以当前 HEAD 为基线，复查旧到期字段命名、订单表到期字段、旧计划快照表、旧退款入口、废弃 app 回流、Bot 返回路径和 AWS 固定 IP 释放回归。

### 复查结论

- 非迁移、非测试运行时代码中 `service_expires_at` 已无命中；严格旧字段/旧计划/旧退款扫描无命中。
- 模型 introspection 确认 `CloudAsset` 只有 `actual_expires_at` 作为资产到期字段，`CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at` 服务到期字段，订单侧仅保留 `renew_grace_expires_at`、`expired_at` 等状态/流程时间。
- 仓库根下未发现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app 目录恢复；运行代码中的 `core.dashboard_api`、`core.cloud_accounts` 属于当前共享 helper 和路由命名空间。
- 最新代码变更只清理日志、临时 payload 和代理视图兼容属性中的旧键名，未修改迁移历史和数据库结构。

### 修复内容

本轮未修改运行时代码；仅追加本条中文版本记录。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/provisioning.py cloud/services.py cloud/management/commands/sync_aws_assets.py cloud/lifecycle.py cloud/api_tasks.py cloud/tests.py bot/handlers.py bot/keyboards.py bot/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity 1
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_ignores_shutdown_disabled_account cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_global_ip_delete_switch cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_clears_retained_order_after_successful_release --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudServerOrder; print([f.name for f in CloudAsset._meta.fields if 'expire' in f.name or 'expiry' in f.name or 'expires' in f.name]); print([f.name for f in CloudServerOrder._meta.fields if 'expire' in f.name or 'expiry' in f.name or 'expires' in f.name])"
rg -n "service_expires_at__|\bservice_expires_at\b|\bCloudLifecyclePlan\b|\bCloudNoticePlan\b|\bCloudAutoRenewPlan\b|\brefund_order\b|\bprocess_refund\b|\bcreate_refund\b|\bissue_refund\b|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded|\brefunded\b" cloud orders bot core shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!docs/**'
git diff --check
```

结果：Django 系统检查、关键模块编译、迁移 dry-run、23 条 Bot 返回路径测试、5 条云生命周期/AWS 固定 IP 回归、模型字段 introspection、废弃 app 目录检查、旧字段/旧计划/旧退款扫描和空白检查均通过。`makemigrations --check --dry-run` 仍因沙箱无法连接默认 MySQL 输出迁移历史检查警告，但最终无模型变更；Bot 测试中的巡检异常日志是测试用例故意模拟失败路径，结果为 OK。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实 Telegram 回调、钱包扣款、自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 21:39 自动监工：运行时旧到期命名清零

### 范围

本轮继续监工 Shop Django 后端仓库，起始后端工作树干净，最近提交为 `798b8db 记录机器人修改配置返回来源复查`。先确认自动化和终端版 codex 状态，再用本地命令与 codex-cli `0.135.0-alpha.1`、模型 `gpt-5.5` 做只读复核，重点检查到期事实源、删除/通知计划防重复、废弃 app、旧计划快照表、退款逻辑和机器人返回路径。

### 复查结论

- 自动化 `/Users/a399/.codex/automations/shop/automation.toml` 仍为 `ACTIVE`，10 分钟一次，模型为 `gpt-5.5`。
- codex-cli 未发现高/中风险回流：`CloudServerOrder` 未恢复 `service_expires_at`，旧 `CloudLifecyclePlan/CloudNoticePlan/CloudAutoRenewPlan`、退款/refunded 逻辑和废弃 app 未回流。
- 删除计划、通知计划和代理列表继续以 `CloudAsset.actual_expires_at` 或 `order_asset_expiry()` 为到期来源；执行侧通过 `CloudLifecycleTask.source_key` 和 `CloudNoticeTask.source_key` 认领，并配合已送达通知日志避免重复执行。
- codex-cli 只指出信息级残留：运行时代码里还有 `service_expires_at` 作为日志/payload 键名或兼容属性。该值来自资产事实，不构成数据库字段回流，但会继续造成误审。

### 修复内容

- 将 `cloud/provisioning.py`、`cloud/services.py`、`cloud/management/commands/sync_aws_assets.py` 中运行时日志、临时 dict key 和 structured payload 的旧键名从 `service_expires_at` 改为 `actual_expires_at`。
- 删除 `_proxy_asset_view()` 上的 `service_expires_at=asset.actual_expires_at` 兼容属性，只保留 `actual_expires_at`。
- 不修改历史迁移；迁移历史仍保留旧字段的删除过程。

### 验证

已通过：

```bash
/Applications/Codex.app/Contents/Resources/codex exec --sandbox read-only --model gpt-5.5 -C /Users/a399/Desktop/data/shop ...
uv run python -m py_compile cloud/provisioning.py cloud/services.py cloud/management/commands/sync_aws_assets.py
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity 1
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_ignores_shutdown_disabled_account cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_global_ip_delete_switch cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_clears_retained_order_after_successful_release --verbosity 1
rg -n "service_expires_at" cloud bot orders core shop -g '*.py' -g '!**/migrations/**' -g '!**/tests.py'
rg -n "service_expires_at__|(filter|exclude|update|order_by|values|values_list)\([^\n)]*service_expires_at|service_expires_at\s*=\s*models|\bCloudLifecyclePlan\b|\bCloudNoticePlan\b|\bCloudAutoRenewPlan\b|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded|\brefunded\b" cloud orders bot core shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!docs/**'
git diff --check
```

结果：codex-cli 只读复核完成；关键模块编译、Django 系统检查、迁移 dry-run、23 条机器人返回路径测试、5 条云生命周期聚焦回归、运行时旧字段扫描、旧字段/旧计划/旧退款扫描和空白检查均通过。Bot 测试中的巡检异常日志是测试用例故意模拟失败路径，测试结果为 OK。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实 Telegram 回调、钱包扣款、自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 21:29 自动监工：机器人修改配置返回来源补漏

### 范围

本轮继续监工 Shop Django 后端仓库，起始后端工作树干净，最近提交为 `f34402d 统一后台到期响应字段`。按要求顺带复查机器人返回逻辑，重点检查代理详情、资产详情、续费、更换 IP、重新安装、修改配置这些链路是否把原始返回来源一路带到后续按钮。

### 复查结论

- 续费、更换 IP、重新安装主链路已使用 `split(':', maxsplit)` 或 `':'.join(...)` 保留嵌套返回来源，未发现再次截断为单段 `profile` 或 `cloud` 的问题。
- 发现一处可优化漏点：修改配置进入套餐列表后，确认调整按钮 `cloud:upgradepay` 未携带原返回来源；提交成功后只能回到底部主菜单，无法直接回到原代理详情来源。
- 到期字段命名继续收口：机器人详情展示中的两个局部变量从旧名 `service_expires_at` 改为 `expires_at_label`，展示文案不变。

### 修复内容

- 订单详情和资产详情的“修改配置”套餐按钮统一通过 `append_back_callback()` 携带返回来源。
- `cloud:upgradepay` handler 改为可解析附带的返回来源；提交成功后如果来源存在，返回按钮指向原代理详情并保留原列表或查询入口。
- 补充 `RetainedIpRenewalUiTestCase.test_cloud_upgrade_payment_keeps_back_path`，锁住修改配置支付按钮必须继续携带来源、支付 handler 必须继续按 `maxsplit` 解析并生成返回原代理按钮。

### 验证

已通过：

```bash
uv run python -m py_compile bot/handlers.py bot/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_cloud_upgrade_payment_keeps_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_change_ip_keyboards_keep_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_ip_query_actions_return_to_query_menu
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at" bot/handlers.py bot/tests.py bot/api.py cloud/api_asset_edit.py cloud/api_orders.py cloud/api_tasks.py cloud/tests.py
rg -n "service_expires_at" /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src -g '!**/*.md'
git diff --check
```

结果：机器人聚焦测试、Django 系统检查、迁移 dry-run、后端/前端旧字段扫描和空白检查均通过。`rg service_expires_at` 对本轮关注文件无命中，前端运行代码旧字段无命中。

剩余风险：本轮未跑完整测试套件，未执行真实 Telegram 回调、真实修改配置扣款、云端配置调整、钱包支付、云端删机或固定 IP 释放。

## 2026-06-02 21:24 自动监工：后台到期字段统一巡检

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `fb206fe 记录资产更换IP返回来源复查`。重点复查云资产生命周期重构后的旧订单到期字段、旧计划快照表、退款旧入口、废弃 app 回流、Bot 返回路径和固定 IP 释放回归。验证过程中工作树出现同主题未提交改动，内容为后台订单、任务、删机计划和 IP 删除日志 payload 从 `service_expires_at` 收口为 `actual_expires_at`，本轮按资产唯一到期事实方向一起验证并收口。

### 复查结论

- `INSTALLED_APPS` 仅包含 `core/bot/orders/cloud` 当前运行域；仓库根下未发现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app 目录恢复。
- 模型 introspection 确认 `CloudServerOrder` 没有 `service_expires_at` 或 `actual_expires_at` 字段，`CloudAsset` 只有 `actual_expires_at` 这一类到期字段。
- 严格排除迁移和测试后，运行时代码未发现旧订单到期字段 ORM 查询或写入、旧计划快照模型、旧退款函数名、`STATUS_REFUNDED/refunded` 状态回流。
- 后台订单列表/详情、资产编辑关联订单、生命周期计划、自动续费任务、删机历史和未附加固定 IP 删除项的到期响应字段继续收口为 `actual_expires_at`；前端 `vue-shop-admin/apps/web-antd` 对这些页面和类型也使用 `actual_expires_at`，未发现 `service_expires_at` 依赖。

### 修复内容

- 收口后台 API payload 和对应测试断言中的派生到期字段名：订单、任务、删机计划、删机历史、未附加固定 IP 删除项统一返回或接收 `actual_expires_at`。
- 订单详情后台编辑到期时间继续写入 `CloudAsset.actual_expires_at`，并同步重算订单生命周期字段和 Server 兼容记录；不恢复订单表到期字段。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py bot/handlers.py bot/keyboards.py cloud/services.py cloud/lifecycle.py cloud/api.py cloud/api_orders.py cloud/api_asset_edit.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudServerOrder; print([f.name for f in CloudServerOrder._meta.fields]); print([f.name for f in CloudAsset._meta.fields if 'expire' in f.name or 'expires' in f.name])"
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_ignores_shutdown_disabled_account cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_global_ip_delete_switch cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_clears_retained_order_after_successful_release --noinput
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_linked_active_order_asset_delete_plan_uses_order_payload cloud.tests.CloudServerServicesTestCase.test_dashboard_order_expiry_update_syncs_asset_expiry_and_lifecycle_plan cloud.tests.CloudServerServicesTestCase.test_shutdown_log_items_prefer_order_lifecycle_schedule cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_include_name_expiry_and_detail_path cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_order_update_recalculates_lifecycle_on_expiry_change cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry --noinput
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields --noinput
rg -n "service_expires_at__|service_expires_at\s*=|CloudLifecyclePlan|CloudNoticePlan|CloudAutoRenewPlan|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded|\brefunded\b" cloud orders bot core shop --glob '!**/migrations/**' --glob '!**/tests.py'
rg -n "service_expires_at|actual_expires_at" /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src --glob '!node_modules/**'
git diff --check
```

结果：Django 系统检查、关键模块编译、模型字段 introspection、迁移 dry-run、22 条 Bot 返回路径测试、4 条 AWS 固定 IP 释放回归、10 条到期字段和同步聚焦回归、旧字段/旧计划/旧退款扫描、前端字段依赖扫描和空白检查均通过。首次字段聚焦测试命令误用了一个不存在的测试选择器，已改为 `test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields` 单独重跑通过。`makemigrations --check --dry-run` 无模型变更；默认 MySQL 迁移历史检查因沙箱无法连接 `127.0.0.1` 输出警告。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实 Telegram Bot 回调、钱包扣款、自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 21:15 自动监工：资产更换 IP 返回来源补漏

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `8c47cee 整理记忆文档标题`。使用终端版 codex-cli `0.135.0-alpha.1`、模型 `gpt-5.5` 做只读复核，重点检查废弃 app 回流、订单旧到期字段、旧计划快照、退款状态/函数、Bot 代理详情/续费/更换 IP/支付后的返回路径。

### 复查结论

- codex-cli 和本地扫描一致确认：`INSTALLED_APPS` 未恢复 `accounts/finance/mall/monitoring/dashboard_api/biz`，运行时旧 app 目录未恢复。
- 模型 introspection 确认 `CloudServerOrder` 没有 `service_expires_at` 或 `actual_expires_at` 字段，`CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- 严格排除迁移和测试后的旧字段/旧计划/旧退款扫描未命中运行时代码；旧 app 导入扫描只剩 `cloud/services.py` 注释提到旧兼容壳。
- codex-cli 发现一处中风险 Bot 返回路径漏点：从代理资产详情点击“更换IP”时，`cb_cloud_asset_action` 已解析 `back_callback`，但打开地区菜单时未传给 `cloud_server_change_ip_region_menu()`，会导致后续地区/端口页丢失 `cloud:querymenu` 或 `profile:orders:...` 来源。

### 修复内容

- 修复资产详情“更换IP”进入地区菜单时漏传 `back_callback` 的问题，确保资产操作链路和订单详情链路使用同一套返回来源逻辑。
- 补充 `RetainedIpRenewalUiTestCase` 回归守卫，锁住 `cb_cloud_asset_action` 的资产更换 IP 地区菜单必须继续透传来源。

### 验证

已通过：

```bash
/Applications/Codex.app/Contents/Resources/codex exec --sandbox read-only --model gpt-5.5 -C /Users/a399/Desktop/data/shop ...
uv run python -m py_compile bot/handlers.py bot/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print([app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudServerOrder, CloudAsset; print([f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}]); print([f.name for f in CloudAsset._meta.fields if f.name == 'actual_expires_at'])"
rg -n "service_expires_at\s*=\s*models|service_expires_at__|\bCloudLifecyclePlan\b|\bCloudNoticePlan\b|\bCloudAutoRenewPlan\b|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded|\brefunded\b" cloud orders bot core shop -g '!**/migrations/**' -g '!**/tests.py' -g '!*.pyc'
rg -n "accounts\.models|finance\.models|mall\.models|monitoring\.models|dashboard_api\.|biz\." cloud orders bot core shop -g '!**/migrations/**' -g '!*.pyc'
git diff --check
```

结果：codex-cli 只读复核完成；Django 系统检查、迁移 dry-run、Bot 返回路径聚焦测试、模型字段 introspection、旧字段/旧计划/旧退款扫描和空白检查均通过。Bot 测试中的巡检异常日志为用例故意模拟失败路径，不是测试失败。

剩余风险：本轮未跑完整测试套件，未执行真实 Telegram 回调、真实钱包扣款、云端换 IP 创建、云端删机或固定 IP 释放。

## 2026-06-02 21:10 自动监工：云资产生命周期稳定巡检

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `8c47cee 整理记忆文档标题`。重点复查云资产生命周期重构后的冲突逻辑、导入错误、废弃 app 回流、订单旧到期字段、旧计划快照表、旧退款入口，以及 Bot 返回路径和 AWS 固定 IP 释放回归是否稳定。

### 复查结论

- `INSTALLED_APPS` 未出现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app；仓库根下未发现这些废弃 app 目录恢复。
- `CloudServerOrder` 模型 introspection 返回空列表，确认没有 `service_expires_at` 或 `actual_expires_at` 字段；`CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- 严格运行时代码扫描未发现旧订单到期字段 ORM 查询或写入、`normalize_service_expiry`、`service_expired_at`、旧计划快照模型、旧退款函数名、`STATUS_REFUNDED/refunded` 状态，或云账号级关机计划判断回流。
- `service_expires_at` 命中仍为兼容展示 payload 或从资产事实派生的接口字段；`CloudLifecyclePlanNote` 仍只是当前删除计划备注表，不是旧派生计划快照表恢复。

### 修复内容

本轮未修改运行时代码，仅补充本次自动监工复查记录。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/api.py cloud/api_orders.py cloud/api_tasks.py cloud/services.py core/management/commands/cleanup_old_records.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py shell -c "from django.conf import settings; print([app for app in settings.INSTALLED_APPS if app.split('.')[0] in {'accounts','finance','mall','monitoring','dashboard_api','biz'}]); from cloud.models import CloudServerOrder, CloudAsset; print([f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}]); print([f.name for f in CloudAsset._meta.fields if f.name == 'actual_expires_at'])"
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_query_keyboard_includes_reinstall_and_expiry_actions --noinput
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_ignores_shutdown_disabled_account cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_global_ip_delete_switch cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_clears_retained_order_after_successful_release --noinput
rg -n "service_expires_at__|(filter|exclude|update|order_by|values|values_list)\([^\n)]*service_expires_at|service_expires_at\s*=\s*models|\bnormalize_service_expiry\b|service_expired_at|class Cloud(LifecyclePlan|NoticePlan|AutoRenewPlan)\b|CloudLifecyclePlan\.|CloudNoticePlan\.|CloudAutoRenewPlan\.|\brefund_to_balance\b|\brefund_balance\b|STATUS_REFUNDED|status=['\"]refunded|cloud_account__shutdown_enabled|资产或云账号关机计划|云账号关机计划" cloud orders bot core shop --glob '!**/migrations/**' --glob '!docs/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

结果：Django 系统检查、关键模块编译、模型字段 introspection、迁移 dry-run、22 条 Bot UI/返回路径聚焦测试、4 条 AWS 固定 IP 释放回归、旧字段/旧计划/旧退款/废弃 app 扫描和空白检查均通过。`makemigrations --check --dry-run` 无模型变更；默认 MySQL 迁移历史检查因沙箱无法连接 `127.0.0.1` 输出警告。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实 Telegram Bot 回调、钱包扣款、自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 21:04 自动监工：Bot 续费与更换 IP 返回路径收口

### 范围

本轮继续监工 Shop Django 后端仓库，起始最近提交为 `78395ea 记录云资产生命周期稳定复查`，工作树已有 Bot 返回路径和中文文档相关未提交改动。重点复查云资产生命周期旧字段、旧计划、旧退款入口、废弃 app 回流，并收口 Bot 续费、更换 IP、保留 IP 续费套餐和钱包支付链路中的嵌套返回 callback。

### 复查结论

- `INSTALLED_APPS` 未出现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app；仓库根下也未发现这些废弃 app 目录恢复。
- `CloudServerOrder` 模型 introspection 继续确认没有 `service_expires_at` 或 `actual_expires_at` 字段；`CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- 旧字段扫描命中均为兼容展示 payload、当前 `CloudLifecyclePlanNote` 备注表或当前 `core.dashboard_api` 辅助模块，不是旧订单到期字段、旧计划快照表、旧退款函数名或废弃 app 运行时回流。

### 修复内容

- 修复 `cloud:renewpay:*:*:cloud:querymenu`、`cloud:ipregion:*:*:cloud:querymenu`、`cloud:ipport:*:*:*:cloud:querymenu` 等带冒号来源 callback 的解析，避免普通 `split(':')` 截断或抛出 `ValueError`。
- 续费、保留 IP 套餐选择、未绑定资产续费、更换 IP 地区/端口选择、钱包支付失败返回和续费成功详情按钮继续透传来源页。
- IP 查询结果中的订单续费与更换 IP 操作统一带回 `cloud:querymenu`，避免从查询结果进入二级操作后返回默认代理列表。
- 补充 `RetainedIpRenewalUiTestCase` 键盘回归测试，覆盖续费详情按钮、续费支付按钮、保留 IP 套餐、未绑定资产套餐、更换 IP 地区/端口和 IP 查询操作返回路径。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py shell -c "from django.conf import settings; print([app for app in settings.INSTALLED_APPS if app.split('.')[0] in {'accounts','finance','mall','monitoring','dashboard_api','biz'}]); from cloud.models import CloudServerOrder, CloudAsset; print([f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}]); print([f.name for f in CloudAsset._meta.fields if f.name == 'actual_expires_at'])"
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python - <<'PY'
from pathlib import Path
root = Path('.')
for name in ['accounts','finance','mall','monitoring','dashboard_api','biz']:
    p = root / name
    if p.exists():
        print(f'{name}: exists')
PY
git diff --check
```

结果：Django 系统检查、Bot 模块编译、21 条 Bot 返回路径聚焦测试、模型字段 introspection、废弃 app 目录检查和空白检查均通过。`makemigrations --check --dry-run` 无模型变更；默认 MySQL 迁移历史检查因沙箱无法连接 `127.0.0.1` 输出警告。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实 Telegram Bot 回调、钱包扣款、自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 20:52 自动监工：云资产生命周期稳定复查

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `e872911 记录云资产生命周期与代理返回复查`。重点复查云资产生命周期重构后的冲突逻辑、导入错误、废弃 app 回流、订单旧到期字段、旧计划快照表、旧退款入口、固定 IP 释放资产级关机开关，以及上一轮 Bot 代理详情返回路径修复是否稳定。

### 复查结论

- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 当前运行域；仓库根下未发现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app 目录恢复。
- `CloudServerOrder` 模型 introspection 返回空列表，确认没有 `service_expires_at` 或 `actual_expires_at` 字段；`CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- 严格运行时代码扫描未发现旧订单到期字段 ORM 查询或写入、`normalize_service_expiry`、`service_expired_at`、旧计划快照模型、旧退款函数名、`STATUS_REFUNDED/refunded` 状态，或云账号级关机计划判断回流。
- `CloudLifecyclePlanNote` 仍只是删除计划备注表，不是旧派生计划快照表恢复；`dashboard_api` 命中仍为当前 `core.dashboard_api` 公共模块和 URL namespace，不是废弃 app 回流。
- Bot 代理详情、IP 查询、重新安装、修改配置和资产操作按钮继续保留完整来源 callback；AWS 未附加固定 IP 释放仍按资产级 `CloudAsset.shutdown_enabled` 和全局 IP 删除开关执行。

### 修复内容

本轮未修改运行时代码，仅补充本次自动监工复查记录。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/api.py cloud/api_orders.py cloud/api_tasks.py cloud/services.py core/management/commands/cleanup_old_records.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py shell -c "from cloud.models import CloudServerOrder; print([f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}])"
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_query_keyboard_includes_reinstall_and_expiry_actions --noinput
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_ignores_shutdown_disabled_account cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_global_ip_delete_switch cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_clears_retained_order_after_successful_release --noinput
rg -n "service_expires_at__|(filter|exclude|update|order_by|values|values_list)\([^\n)]*service_expires_at|service_expires_at\s*=\s*models|\bnormalize_service_expiry\b|service_expired_at|class Cloud(LifecyclePlan|NoticePlan|AutoRenewPlan)\b|CloudLifecyclePlan\.|CloudNoticePlan\.|CloudAutoRenewPlan\.|\brefund_to_balance\b|\brefund_balance\b|STATUS_REFUNDED|status=['\"]refunded|cloud_account__shutdown_enabled|资产或云账号关机计划|云账号关机计划" cloud orders bot core shop --glob '!**/migrations/**' --glob '!docs/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

结果：Django 系统检查、关键模块编译、模型字段 introspection、迁移 dry-run、19 条 Bot UI/返回路径聚焦测试、4 条 AWS 固定 IP 释放回归、旧字段/旧计划/旧退款/废弃 app 扫描和空白检查均通过。`makemigrations --check --dry-run` 无模型变更；默认 MySQL 迁移历史检查因沙箱无法连接 `127.0.0.1` 输出警告。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实 Telegram Bot 回调、自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 20:44 自动监工：云资产生命周期与代理返回复查

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `07ab661 修复代理详情返回路径`。重点复查云资产生命周期重构后的旧到期字段、旧计划快照、旧退款入口、废弃 app 回流、固定 IP 释放资产级关机开关，以及上一笔 Bot 代理详情返回路径修复是否引入新的回调解析或导入问题。

### 复查结论

- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 当前运行域；仓库根下未发现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app 目录恢复。
- `CloudServerOrder` 模型 introspection 返回空列表，确认没有 `service_expires_at` 或 `actual_expires_at` 字段；`CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- 运行时代码未发现 `normalize_service_expiry`、`service_expired_at`、旧计划快照模型 `CloudLifecyclePlan/CloudNoticePlan/CloudAutoRenewPlan`、旧退款函数名、`STATUS_REFUNDED` 或 `refunded` 状态回流。
- Bot 详情、资产详情、资产操作、重新安装和修改配置回调解析继续保留冒号后的完整来源路径，`cloud:querymenu` 与 `profile:orders:cloud:...` 这类返回路径未再被截断。
- AWS 未附加固定 IP 释放路径仍按资产级 `CloudAsset.shutdown_enabled` 和全局 IP 删除开关执行，未恢复云账号级关机开关阻断条件。

### 修复内容

本轮未修改运行时代码，仅补充本次自动监工复查记录。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/api.py cloud/api_orders.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py shell -c "from cloud.models import CloudServerOrder; print([f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}])"
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_query_keyboard_includes_reinstall_and_expiry_actions --noinput
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_ignores_shutdown_disabled_account cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_global_ip_delete_switch cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_clears_retained_order_after_successful_release --noinput
rg -n "service_expires_at|service_expired_at|normalize_service_expiry|CloudLifecyclePlan|CloudNoticePlan|CloudAutoRenewPlan|refund_to_balance|refund_balance|STATUS_REFUNDED|refunded" --glob '!**/migrations/**' --glob '!docs/refactor-version-record.md' .
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

结果：Django 系统检查、关键模块编译、迁移 dry-run、19 条 Bot UI/返回路径聚焦测试、4 条 AWS 固定 IP 释放回归、旧字段/旧计划/旧退款/废弃 app 扫描和空白检查均通过。`makemigrations --check --dry-run` 无模型变更；默认 MySQL 迁移历史检查因沙箱无法连接 `127.0.0.1` 输出警告。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实 Telegram Bot 回调、自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 20:37 自动监工：代理详情返回路径收口

### 范围

本轮继续监工 Shop Django 后端仓库，起始最近提交为 `7ec48ba 记录云资产生命周期回归复查`，工作树已有 Bot 云代理按钮和固定 IP 资产级关机开关测试相关改动。复查重点仍是云资产生命周期重构后的旧字段、旧计划、旧退款入口、废弃 app 回流和资产级 `CloudAsset.actual_expires_at` 到期事实源，同时检查代理详情、IP 查询和重新安装/修改配置按钮的返回路径是否丢失。

### 复查结论

- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 当前运行域；仓库根下未发现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app 目录恢复。
- `CloudServerOrder` 模型 introspection 确认没有 `service_expires_at` 或 `actual_expires_at` 字段；`service_expires_at` 命中仍为兼容 API 字段、日志字段或从资产到期事实派生的展示值。
- 未发现 `normalize_service_expiry`、`service_expired_at`、旧计划快照模型 `CloudLifecyclePlan/CloudNoticePlan/CloudAutoRenewPlan`、旧退款函数名、`STATUS_REFUNDED` 或 `refunded` 运行时状态回流。
- AWS 未附加固定 IP 释放路径仍只检查资产级 `CloudAsset.shutdown_enabled`；云账号级 `shutdown_enabled` 未恢复为释放阻断条件。

### 修复内容

- 修复 `cloud:detail` 和 `cloud:assetdetail` 回调解析：对 `cloud:querymenu`、`profile:orders:cloud:...` 等不带传统页码尾段的来源路径，改为保留完整嵌套返回 callback，避免详情页返回默认云代理列表。
- 补齐代理详情、IP 查询结果、未绑定资产续费、重新安装确认取消、修改配置返回等按钮的来源透传，避免从 IP 查询或订单列表进入后操作完返回路径丢失。
- 补充 Bot UI 聚焦测试，覆盖嵌套返回 callback、详情页操作按钮、重新安装取消按钮、资产续费套餐返回按钮和 IP 查询操作按钮。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/management/commands/sync_aws_assets.py cloud/lifecycle.py cloud/api.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_ignores_shutdown_disabled_account cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_global_ip_delete_switch --noinput
rg -n "service_expires_at|service_expired_at|normalize_service_expiry|CloudLifecyclePlan|CloudNoticePlan|CloudAutoRenewPlan|refund_to_balance|refund_balance|STATUS_REFUNDED|refunded" bot orders cloud core shop --glob '!**/migrations/**'
git diff --check
```

结果：Django 系统检查、关键模块编译、18 条 Bot UI 测试、3 条 AWS 固定 IP 释放回归、迁移 dry-run 和空白检查通过。`makemigrations --check --dry-run` 无模型变更；默认 MySQL 迁移历史检查因沙箱无法连接 `127.0.0.1` 输出警告。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实 Telegram Bot 回调、自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 20:31 自动监工：固定 IP 资产级关机开关测试收口

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `7ec48ba 记录云资产生命周期回归复查`。重点复查云资产生命周期重构后的冲突逻辑、导入错误、废弃 app 回流、订单旧到期字段、旧计划快照表、旧退款入口、AWS/阿里云同步保留手工到期，以及 AWS 未附加固定 IP 释放是否仍只使用资产级 `CloudAsset.shutdown_enabled`。

### 复查结论

- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 当前运行域；仓库根下未发现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app 目录恢复。
- 运行时代码未发现 `CloudServerOrder.service_expires_at` 模型字段、危险 ORM 查询或写入恢复；`service_expires_at` 命中仍为兼容 API 字段、日志字段或从 `CloudAsset.actual_expires_at` 派生的展示值。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；AWS/阿里云同步已有资产路径继续保留现有 `CloudAsset.user` 和 `CloudAsset.actual_expires_at`。
- 未发现 `normalize_service_expiry`、`service_expired_at`、旧计划快照模型 `CloudLifecyclePlan/CloudNoticePlan/CloudAutoRenewPlan`、退款旧函数名、退款旧入口、`STATUS_REFUNDED` 或 `refunded` 运行时状态回流。
- 严格扫描未发现 `cloud_account__shutdown_enabled`、`云账号关机计划` 或 `资产或云账号关机计划` 运行时代码命中；生命周期仍按资产级开关执行。

### 修复内容

- 修正 `cloud/tests.py` 中 AWS 未附加固定 IP 释放的旧测试断言：原测试仍期待云账号级 `shutdown_enabled=False` 阻止释放，这与当前只看资产级 `CloudAsset.shutdown_enabled` 的规则冲突。
- 将该用例收口为资产级开关关闭时阻止释放，并补充“云账号级关机开关关闭但资产开关开启时仍可释放”的回归测试，防止后续把云账号级判断误接回 AWS 同步释放路径。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/tests.py cloud/management/commands/sync_aws_assets.py cloud/lifecycle.py cloud/lifecycle_execution.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/api_tasks.py cloud/services.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_ignores_shutdown_disabled_account cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_global_ip_delete_switch cloud.tests.CloudServerServicesTestCase.test_due_orders_ignore_account_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_due_orders_skip_suspend_when_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_due_orders_skip_order_static_ip_recycle_when_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_lifecycle_suspend_execution_guard_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_respects_asset_shutdown_disabled --noinput
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_rebinds_unattached_ip_when_instance_reappears cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_updates_retained_asset_after_renewal_recovery cloud.tests.CloudServerServicesTestCase.test_sync_aws_retained_ip_preserves_existing_asset_user cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_manual_asset_note cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_ignores_shutdown_disabled_account --noinput
rg -n "service_expires_at__|(filter|exclude|update|order_by|values|values_list)\([^\n)]*service_expires_at|service_expires_at\s*=\s*models|\bnormalize_service_expiry\b|service_expired_at|class Cloud(LifecyclePlan|NoticePlan|AutoRenewPlan)\b|CloudLifecyclePlan\.|CloudNoticePlan\.|CloudAutoRenewPlan\.|\brefund_to_balance\b|\brefund_balance\b|STATUS_REFUNDED|status=['\"]refunded|cloud_account__shutdown_enabled|资产或云账号关机计划|云账号关机计划" cloud orders bot core shop --glob '!**/migrations/**' --glob '!**/tests.py'
git diff --check
```

结果：Django 系统检查、关键模块编译、迁移 dry-run、8 条资产级关机计划回归、7 条 AWS 同步/固定 IP 释放回归和严格回流扫描均通过。`makemigrations --check --dry-run` 无模型变更；默认 MySQL 迁移历史检查因沙箱无法连接 `127.0.0.1` 输出警告。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 20:11 自动监工：云资产生命周期回归复查

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `e343109 记录资产级关机开关收敛`。重点复查云资产生命周期重构后的到期事实源、订单旧到期字段、旧计划快照表、退款旧入口、废弃 app 回流、云同步保留手工到期，以及上一轮资产级关机开关收敛是否稳定。

### 复查结论

- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 当前运行域；仓库根下未发现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app 目录恢复。
- 运行时代码未发现 `CloudServerOrder.service_expires_at` 模型字段、危险 ORM 查询或写入恢复；`service_expires_at` 命中仍为兼容 API 字段、日志字段或从 `CloudAsset.actual_expires_at` 派生的展示值。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；AWS/阿里云同步已有资产路径继续保留现有 `CloudAsset.user` 和 `CloudAsset.actual_expires_at`，不会用云端或订单派生时间覆盖手工事实。
- 未发现 `normalize_service_expiry`、`service_expired_at`、旧计划快照模型 `CloudLifecyclePlan/CloudNoticePlan/CloudAutoRenewPlan`、退款旧函数名、退款旧入口、`STATUS_REFUNDED` 或 `refunded` 运行时状态回流。
- `CloudLifecyclePlanNote` 仍只是删除计划备注表，不是旧派生计划快照表恢复；`dashboard_api` 命中为当前 `core.dashboard_api` 公共模块和 URL namespace，不是废弃 Django app 回流。
- 删除计划、通知计划、生命周期执行和 AWS 未附加固定 IP 释放继续使用资产级 `CloudAsset.shutdown_enabled`；云账号级关机开关未恢复到运行时判断。

### 功能变更

本轮未修改运行时代码；仅补充本次中文版本记录。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py bot/api_cloud_accounts.py cloud/api_assets.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/services.py cloud/api_orders.py cloud/api_tasks.py core/management/commands/cleanup_old_records.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_mark_success_preserves_existing_manual_asset_fields_on_update cloud.tests.CloudServerServicesTestCase.test_early_provisioning_steps_preserve_existing_manual_asset_fields cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_instance_requires_five_passes_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_check_uses_previous_public_ip_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_blank_asset_does_not_delete_unrelated_blank_server cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_route_linked_asset_delete_to_order_item cloud.tests.CloudServerServicesTestCase.test_linked_active_order_asset_delete_plan_uses_order_payload cloud.tests.CloudServerServicesTestCase.test_orphan_asset_delete_refuses_linked_active_order_when_enforced cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_respects_shutdown_disabled_asset cloud.tests.CloudServerServicesTestCase.test_due_orders_ignore_account_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_due_orders_skip_suspend_when_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_lifecycle_suspend_execution_guard_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_due_orders_restore_suspend_after_asset_shutdown_reenabled cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ignore_account_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_asset_shutdown_disabled_plan_state --verbosity 1
rg -n "cloud_account__shutdown_enabled|云账号关机计划|资产或云账号关机计划|CloudLifecyclePlan\.|CloudNoticePlan\.|CloudAutoRenewPlan\.|refunded|refund_to_balance|refund_balance|service_expires_at\s*=\s*models|service_expired_at|normalize_service_expiry|STATUS_REFUNDED" cloud orders bot core shop --glob '!**/migrations/**'
git diff --check
```

结果：Django 系统检查、关键模块编译、17 条聚焦回归、迁移检查和空白检查均通过；旧字段/旧模型/旧退款/云账号关机开关回流扫描无命中。`makemigrations --check --dry-run` 无模型变更；默认 MySQL 迁移历史检查因沙箱无法连接 `127.0.0.1` 输出警告。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 20:09 自动监工：收敛资产级关机开关

### 范围

本轮继续使用终端版 `codex` 做只读复审，并人工复核删除计划、通知计划、代理详情、云账号配置和生命周期执行入口。CLI 复审结论指出：上一轮关联资产删机入口已稳定，但关机计划仍同时读取云账号级 `shutdown_enabled` 和资产级 `CloudAsset.shutdown_enabled`，不符合“删除计划和代理详情使用同一套开关”的目标。

### 修复内容

- 删除计划、通知计划、代理详情风险态、订单关机/删机、迁移旧机删除、订单固定 IP 回收、孤立服务器删除、未附加固定 IP 释放，现在只以 `CloudAsset.shutdown_enabled` 作为单条资产关机计划开关。
- 保留“删除服务器总开关”和“删除 IP 总开关”作为危险动作总闸；它们不是单条资产开关。
- 云账号接口不再读写或返回 `shutdown_enabled`，避免前端或后续代码再次把云账号开关接入生命周期判断。
- AWS 同步释放未附加固定 IP 时，不再用云账号级关机开关拦截，只检查资产自身开关和删除 IP 总开关。
- 前端云账号 API 类型移除 `DashboardCloudAccountConfigItem.shutdown_enabled` 和创建 payload 里的云账号关机字段；代理详情和删除计划仍使用资产级字段。

### 验证

已通过：

```bash
uv run python -m py_compile bot/api.py bot/api_cloud_accounts.py cloud/api_assets.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/management/commands/sync_aws_assets.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_respects_shutdown_disabled_asset cloud.tests.CloudServerServicesTestCase.test_due_orders_ignore_account_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_due_orders_skip_suspend_when_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_lifecycle_suspend_execution_guard_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_due_orders_restore_suspend_after_asset_shutdown_reenabled cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ignore_account_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_asset_shutdown_disabled_plan_state --verbosity 2
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_hides_shutdown_disabled_lifecycle_notices cloud.tests.CloudServerServicesTestCase.test_dashboard_shutdown_plan_run_respects_delete_at cloud.tests.CloudServerServicesTestCase.test_dashboard_orphan_asset_plan_run_respects_computed_delete_time cloud.tests.CloudServerServicesTestCase.test_linked_active_order_asset_delete_plan_uses_order_payload cloud.tests.CloudServerServicesTestCase.test_orphan_asset_delete_refuses_linked_active_order_when_enforced cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_route_linked_asset_delete_to_order_item cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_respects_shutdown_disabled_asset --verbosity 2
uv run python manage.py makemigrations --check --dry-run
rg -n "cloud_account__shutdown_enabled|云账号关机计划|资产或云账号关机计划|CloudLifecyclePlan\.|CloudNoticePlan\.|CloudAutoRenewPlan\.|refunded|refund_to_balance|refund_balance|service_expires_at\s*=\s*models|service_expired_at|normalize_service_expiry" cloud orders bot core shop --glob '!**/migrations/**'
git diff --check
```

结果：后端编译、系统检查、两组聚焦回归、迁移检查和空白检查均通过；旧字段/旧模型/退款回流扫描无命中。

前端类型检查受本机工具链版本阻断，未执行成功：

```bash
pnpm -F @vben/web-antd run typecheck
```

阻断原因：当前全局 `pnpm` 为 `9.15.9`、Node 为 `v26.0.0`；前端仓库要求 `pnpm >=10.0.0` 且 Node `^20.19.0 || ^22.18.0 || ^24.0.0`。本轮未修改全局工具链，也未触碰前端仓库已有的 `pnpm-lock.yaml` 脏改动。

剩余风险：本轮未跑完整测试套件，未执行真实云端删机、固定 IP 释放、支付或生产清理；真实危险动作仍受总开关、计划时间窗口、任务认领和资产级关机开关保护。

## 2026-06-02 20:01 自动监工：云资产生命周期回归复查

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `f225f56 校正关联资产删机计划记录`。重点复查云资产到期事实源、订单旧到期字段、旧计划快照表、退款旧入口、废弃 app 回流，以及上一轮关联订单资产删机计划修复是否保持稳定。

### 复查结论

- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 当前运行域；仓库根下未发现 `accounts/finance/mall/monitoring/dashboard_api/biz` 废弃 app 目录恢复。
- 运行时代码未发现 `CloudServerOrder.service_expires_at` 模型字段、危险 ORM 查询或写入恢复；`service_expires_at` 命中仍为兼容 API 字段、日志字段或从 `CloudAsset.actual_expires_at` 派生的展示值。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；AWS/阿里云同步已有资产路径继续保留现有 `CloudAsset.actual_expires_at`，不会用云端或订单派生时间覆盖手工到期。
- 未发现 `normalize_service_expiry`、`service_expired_at`、旧计划快照模型 `CloudLifecyclePlan/CloudNoticePlan/CloudAutoRenewPlan`、`refund_to_balance/refund_balance`、`STATUS_REFUNDED` 或 `refunded` 运行时状态回流。
- `CloudLifecyclePlanNote` 仍只是删除计划备注表，不是旧派生计划快照表恢复；`dashboard_api` 命中为当前 `core.dashboard_api` 公共模块和 URL namespace，不是废弃 Django app 回流。
- 上一轮有关联有效订单的资产删机保护继续通过聚焦回归：计划展示回到订单 payload，孤立资产删机执行入口在强制计划模式下拒绝绕过订单。

### 功能变更

本轮未修改运行时代码；仅补充本次中文版本记录。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/lifecycle.py cloud/services.py cloud/api_orders.py cloud/api_tasks.py bot/api.py cloud/lifecycle_execution.py core/management/commands/cleanup_old_records.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_mark_success_preserves_existing_manual_asset_fields_on_update cloud.tests.CloudServerServicesTestCase.test_early_provisioning_steps_preserve_existing_manual_asset_fields --verbosity 1
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_instance_requires_five_passes_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_check_uses_previous_public_ip_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_blank_asset_does_not_delete_unrelated_blank_server --verbosity 1
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_orphan_asset_plan_run_rejects_active_linked_order_asset cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_route_linked_asset_delete_to_order_item cloud.tests.CloudServerServicesTestCase.test_linked_active_order_asset_delete_plan_uses_order_payload cloud.tests.CloudServerServicesTestCase.test_orphan_asset_delete_refuses_linked_active_order_when_enforced --verbosity 1
git diff --check
```

说明：`makemigrations --check --dry-run` 无模型变更；默认 MySQL 迁移历史检查因沙箱无法连接 `127.0.0.1` 输出警告。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 19:55 自动监工：修复关联订单资产误走孤立删机

### 范围

本轮继续使用终端版 `codex` 做只读复审，并人工复核删除计划、代理列表详情、生命周期任务认领和删机执行入口。CLI 明确指出：删除计划按资产生成时，仍有关联有效订单的资产会被标成孤立资产执行项，存在绕过订单删机语义的风险。

### 修复内容

- 删除计划中，仍有关联有效订单的资产现在返回 `item_type=order`，执行入口应走订单删机计划。
- 订单删除计划 payload 透出资产级 `shutdown_enabled`，继续和代理详情、删除计划使用同一套关机开关逻辑。
- 有关联有效订单的资产如果误调用孤立资产删机执行器，会被直接拒绝，避免只标记资产删除而留下订单状态不同步。
- 删除计划队列对同一有效订单做去重，避免多资产行重复生成同一个订单删除计划。
- 阿里云只同步资产仍保持 `sync_only` 展示，不接入真实删机。

### 验证

已通过：

```bash
uv run python -m py_compile bot/api.py cloud/lifecycle_execution.py cloud/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_route_linked_asset_delete_to_order_item cloud.tests.CloudServerServicesTestCase.test_orphan_asset_plan_run_rejects_active_linked_order_asset --verbosity 2
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_linked_active_order_asset_delete_plan_uses_order_payload cloud.tests.CloudServerServicesTestCase.test_orphan_asset_delete_refuses_linked_active_order_when_enforced --verbosity 1
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_dashboard_shutdown_plan_run_respects_delete_at cloud.tests.CloudServerServicesTestCase.test_dashboard_orphan_asset_plan_run_respects_computed_delete_time cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_asset_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_future_server_plan_item --verbosity 1
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
git diff --check
```

结果：新增和相关旧测试均通过；Django 系统检查通过；无模型迁移；diff 空白检查通过。

剩余风险：本轮未跑完整测试套件，未执行真实云端删机；真实删除仍受总开关、计划时间窗口、任务认领和云账号/资产关机开关保护。

## 2026-06-02 19:52 自动监工：AWS 同步收敛后回归复查

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `7b4cd58 记录AWS实机测试同步收敛`。重点复查实机测试记录之后，云资产到期事实源、同步收敛保护、订单旧到期字段、旧计划快照表、退款旧入口、废弃 app 是否出现回流，以及有关联订单的资产是否可能绕过订单删机计划进入孤立资产删机。

### 复查结论

- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 当前运行域；未发现旧 `accounts/finance/mall/monitoring/dashboard_api/biz` 运行时 import 或 include 回流。
- 未发现 `CloudServerOrder.service_expires_at` 模型字段恢复，也未发现对已移除订单到期列的危险 ORM 过滤、排序、批量更新、values 或 values_list 查询。
- `CloudAsset.actual_expires_at` 仍是唯一结构化云资产到期事实；`service_expires_at` 命中仍为兼容 payload、日志字段或从资产事实派生的展示值。
- AWS 同步已有资产路径继续保留既有 `CloudAsset.user` 和 `CloudAsset.actual_expires_at`；阿里云同步已有资产路径也继续沿用资产自身 `actual_expires_at`，不会用云端或订单派生时间覆盖手工事实。
- AWS 缺失确认保护仍要求连续确认达到阈值后才把资产、兼容 Server 和订单收敛到删除状态；历史 IP 命中时不会误删未附加固定 IP 资产。
- 发现并收口一类计划口径风险：有关联有效订单的资产不应作为孤立资产删机执行，否则可能让资产删除和订单生命周期状态分叉；这类资产现在回到订单删机计划展示，执行入口也会拒绝强制计划模式下绕过订单。
- 未发现 `normalize_service_expiry`、`service_expired_at`、旧计划快照模型 `CloudLifecyclePlan/CloudNoticePlan/CloudAutoRenewPlan`、退款旧函数名、退款旧入口或 `refunded` 运行时状态回流。

### 功能变更

- `bot/api.py` 的资产删除计划载荷在资产仍关联有效订单时，改为复用订单删机计划 payload，并保留资产 ID、资产名称、云账号 ID 和资产详情入口；计划队列按关联订单去重，避免同一订单多资产重复展示。
- `cloud/lifecycle_execution.py` 的 `run_orphan_asset_delete(..., enforce_schedule=True)` 增加关联有效订单保护：资产仍有关联订单且订单未结束时，拒绝走孤立资产删机入口，提示改走订单删机计划。
- `cloud/tests.py` 补充四条回归测试，覆盖有关联有效订单的资产计划展示回到订单 payload、计划列表回到订单项、后台计划运行入口拒绝误删，以及强制计划模式下孤立资产删机入口拒绝绕过订单。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/lifecycle.py cloud/services.py cloud/api_orders.py cloud/api_tasks.py core/management/commands/cleanup_old_records.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py cloud/lifecycle_execution.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_server_compat_create_preserves_manual_asset_owner_and_expiry cloud.tests.CloudServerServicesTestCase.test_mark_success_preserves_existing_manual_asset_fields_on_update cloud.tests.CloudServerServicesTestCase.test_early_provisioning_steps_preserve_existing_manual_asset_fields --verbosity 1
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_orphan_asset_plan_run_rejects_active_linked_order_asset cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_route_linked_asset_delete_to_order_item cloud.tests.CloudServerServicesTestCase.test_linked_active_order_asset_delete_plan_uses_order_payload cloud.tests.CloudServerServicesTestCase.test_orphan_asset_delete_refuses_linked_active_order_when_enforced --verbosity 1
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_instance_requires_five_passes_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_check_uses_previous_public_ip_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_blank_asset_does_not_delete_unrelated_blank_server --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-lifecycle-test.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_dashboard_orphan_asset_plan_run_respects_computed_delete_time cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_orphan_asset_delete_time_before_cloud_delete cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view --verbosity 1 --noinput
git diff --check
```

说明：`makemigrations --check --dry-run` 无模型变更；默认 MySQL 迁移历史检查因沙箱无法连接 `127.0.0.1` 输出警告。首次用 `DJANGO_TEST_SQLITE=1` 跑包含 `lifecycle_tick()` 的线程型测试时，SQLite 内存库在新线程连接中没有测试表，已改用 `/private/tmp/shop-lifecycle-test.sqlite3` 文件型 SQLite 重跑通过；该组测试期间出现一次快照刷新 `database is locked` 日志，但测试结果为 OK。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 19:47 实机测试：AWS 开通删除与同步收敛

### 范围

本轮使用后台已添加的 AWS 云账号做真实 Lightsail 开通、初始化、删除和同步收敛验证，地区为 `ap-southeast-1`，测试套餐为 `实机测试 Nano`。

### 实测结果

- 创建真实测试订单 `LIVE-CODEX-20260602113912-9317`，订单 ID 为 `76`。
- AWS Lightsail 实例开通成功，实例名为 `20260602-************-*-o76`。
- 固定 IP 分配成功，固定 IP 名为 `20260602-************-*-o76-ip`。
- 服务器初始化链路通过：系统初始化、BBR、主代理、备用代理、Telemt、SOCKS5 均完成。
- 已定向删除真实实例并释放固定 IP。
- 删除后运行 AWS 同步，云端实例数为 0，未附加固定 IP 数为 0。
- 缺失确认保护按设计工作：短时间连续同步不会累计确认次数；本轮仅对测试资产 `#322` 回拨确认时间，继续通过正式 AWS 同步命令推进到 `5/5` 后收敛。
- 本地资产 `#322` 已标记为 `deleted`，`is_active=False`，公网 IP 已转入历史 IP。
- 本地订单 `76` 已随同步链标记为 `deleted`。
- 再次全量同步后，当前可见代理数保持为 0，未发现云端资源残留或本地误恢复。

### 验证

已通过：

```bash
uv run python manage.py sync_aws_assets --region ap-southeast-1 --account-id 55
uv run python manage.py sync_aws_assets --region ap-southeast-1 --account-id 55 --asset-id 322
curl -sS -o /dev/null -w 'backend=%{http_code}\n' http://127.0.0.1:8000/
curl -sS -o /dev/null -w 'frontend=%{http_code}\n' http://127.0.0.1:5667/
```

结果：后端返回 `302`，前端返回 `200`；AWS 同步扫描实例和未附加 IP 均为 0。

剩余建议：后续可以继续补一条后台操作入口，把单个测试资产的缺失确认状态以管理员动作展示和推进，避免实机测试时手动回拨确认时间。

## 2026-06-02 19:41 自动监工：云资产到期事实源回归复查

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `a1399a5 记录云订单清理和自动续费保护`。重点复查云资产生命周期到期事实源、订单旧到期字段、旧计划快照表、退款旧入口、废弃 app 回流，以及上一轮迁移旧机删除、旧记录清理和自动续费窗口复核保护是否仍稳定。

### 复查结论

- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 当前运行域；未发现旧 `accounts/finance/mall/monitoring/dashboard_api/biz` 运行时 app 回流。
- 运行时代码未发现 `CloudServerOrder.service_expires_at` 模型字段恢复，也未发现对已移除订单到期列的危险 ORM 过滤、排序、批量更新或 values 查询。
- `CloudAsset.actual_expires_at` 仍是唯一结构化云资产到期事实；`service_expires_at` 命中仍是兼容 API 字段、日志字段或从资产事实派生的展示值。
- 未发现 `normalize_service_expiry`、`service_expired_at`、旧计划快照模型 `CloudLifecyclePlan/CloudNoticePlan/CloudAutoRenewPlan`、退款旧函数名、退款旧入口或 `refunded` 运行时状态回流。
- 自动续费手动执行、批量执行和重试路径最终仍落到 `_run_auto_renew()`，事务内会重新锁定订单并复核当前资产到期窗口；资产到期被推远时不会扣款或创建续费订单。
- 迁移旧机删除仍以 `CloudServerOrder.migration_due_at` 作为事实源，并由 `run_replaced_order_delete(..., enforce_schedule=True)` 做最终执行保护。
- 旧记录清理仍会保留带有当前 IP、固定 IP 名、实例名、实例 ID、云资源 ID、代理 host 或未完成资源上下文的云订单。

### 功能变更

本轮未修改运行时代码；仅补充本次中文版本记录。

### 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/lifecycle.py cloud/services.py cloud/api_tasks.py cloud/api_orders.py cloud/provisioning.py core/management/commands/cleanup_old_records.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_auto_renew_group_member_can_pay_when_owner_balance_insufficient cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_skips_when_asset_expiry_moved_out_of_due_window cloud.tests.CloudServerServicesTestCase.test_auto_renew_retry_task_waits_for_recharge_then_retries --verbosity 2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_get_migration_due_orders_is_distinct cloud.tests.CloudServerServicesTestCase.test_get_migration_due_orders_skips_non_deleting_orders cloud.tests.CloudServerServicesTestCase.test_replaced_order_delete_respects_asset_shutdown_switch cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_deletes_migration_due_order_with_deleting_asset cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_migration_delete_uses_migration_due_without_notice_payload --verbosity 1
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_deleted_order_until_retained_ip_window_ends cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_non_terminal_cloud_orders cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_terminal_cloud_order_with_live_asset cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_terminal_cloud_order_with_pending_resource_context cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_deleted_order_with_resource_context cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_allows_terminal_cloud_order_with_deleted_asset --verbosity 1
git diff --check
```

说明：首次编译命令漏带 `UV_CACHE_DIR`，`uv` 试图访问用户缓存目录导致沙箱权限错误；已使用 `/private/tmp/uv-cache-shop` 重跑通过。`makemigrations --check --dry-run` 无模型变更，但默认 MySQL 迁移历史检查因沙箱无法连接 `127.0.0.1` 输出警告。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 19:35 自动监工：自动续费窗口复核保护

### 范围

本轮在已删除云订单清理保护提交后，继续处理并验证自动续费执行路径的并发保护改动。重点确认自动续费任务拿到订单后会重新读取当前资产到期事实，避免旧任务或并发任务在资产已续期后仍按过期队列执行续费。

### 复查结论

- 自动续费执行入口继续以 `_notice_payload_for_order()` 读取 `CloudAsset.actual_expires_at` 派生的通知载荷，不恢复订单表到期字段。
- 新增的到期窗口判断只接受“到期前 1 天内”或“已到期但还在关机前宽限窗口内”的订单执行自动续费；资产到期被推远后会跳过本轮。
- `_run_auto_renew()` 在事务内使用 `select_for_update()` 重新锁定订单并复核自动续费开关、资产可见性和当前到期窗口，降低旧队列任务重复改写订单状态的风险。

### 功能变更

- 新增 `_auto_renew_notice_due_now()`，集中判断自动续费执行窗口。
- `_run_auto_renew()` 增加事务锁和执行前窗口复核；未到自动续费时间时返回“未到自动续费时间，跳过本轮自动续费”，不创建续费订单、不扣款、不写余额流水。
- 补充 `test_run_auto_renew_skips_when_asset_expiry_moved_out_of_due_window`，覆盖资产到期被推远后自动续费跳过且钱包余额不变。

### 验证

已通过：

```bash
uv run python -m py_compile cloud/lifecycle.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_auto_renew_group_member_can_pay_when_owner_balance_insufficient cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_skips_when_asset_expiry_moved_out_of_due_window cloud.tests.CloudServerServicesTestCase.test_auto_renew_retry_task_waits_for_recharge_then_retries --verbosity 2
git diff --check
```

说明：第一次运行这组自动续费用例时，测试文件并发落盘尚未包含 `BalanceLedger` 局部导入，出现一次 `NameError`；复查文件后确认导入已存在，重跑同一命令通过。

剩余风险：未跑完整测试套件，未在真实 MySQL 上验证 `select_for_update()` 锁等待表现，未执行真实自动续费支付。

## 2026-06-02 19:31 自动监工：已删除云订单清理保护复查

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `9a94c4a 记录迁移删除和重试认领保护`。重点复查云资产到期事实源、订单旧到期字段、旧计划快照表、退款旧入口、废弃 app 回流，以及迁移旧机删除、自动续费重试认领和旧记录清理保护是否仍符合重构目标。

### 复查结论

- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 当前运行域；未发现旧 `accounts/finance/mall/monitoring/dashboard_api/biz` 运行时 app 回流。
- 未发现 `CloudServerOrder.service_expires_at` 模型字段恢复；运行时代码没有对已移除订单到期列做危险 ORM 过滤、排序、批量更新或 values 查询。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；`service_expires_at` 命中仍是 API 兼容字段、日志字段或从资产事实派生的展示值。
- 未发现 `normalize_service_expiry`、`service_expired_at`、旧计划快照模型 `CloudLifecyclePlan/CloudNoticePlan/CloudAutoRenewPlan`、退款旧函数名、退款旧入口或 `refunded` 运行时状态回流。
- 迁移旧机删除仍只以 `CloudServerOrder.migration_due_at` 作为迁移旧机删机事实源，最终执行入口继续由 `run_replaced_order_delete(..., enforce_schedule=True)` 检查删除窗口、关机计划开关和任务认领。
- 自动续费重试任务仍在执行前用 `status=pending` 和 `next_check_at<=now` 原子认领，避免同一条 pending 任务在同轮或并发场景中重复增加 attempts。

### 功能变更

- `cleanup_old_records` 的已删除云订单清理条件继续收窄：即使 `ip_recycle_at` 已早于保留截止线，只要订单仍有当前 IP、固定 IP 名、实例名、实例 ID、云资源 ID 或代理 host 线索，也不会进入历史清理候选。
- 补充已删除云订单资源线索回归测试，覆盖 `deleted` 状态订单仍保留云资源上下文时不被清理。
- 调整固定 IP 保留窗口测试：确认保留窗口结束后仍需清空订单资源线索，才允许已删除订单进入清理候选。

### 验证

已通过：

```bash
uv run python manage.py check
uv run python -m py_compile cloud/lifecycle.py cloud/services.py cloud/api_tasks.py core/management/commands/cleanup_old_records.py cloud/tests.py
uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_get_migration_due_orders_is_distinct cloud.tests.CloudServerServicesTestCase.test_get_migration_due_orders_skips_non_deleting_orders cloud.tests.CloudServerServicesTestCase.test_replaced_order_delete_respects_asset_shutdown_switch cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_deletes_migration_due_order_with_deleting_asset cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_migration_delete_uses_migration_due_without_notice_payload --verbosity 2
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_auto_renew_retry_task_waits_for_recharge_then_retries --verbosity 2
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_deleted_order_until_retained_ip_window_ends cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_non_terminal_cloud_orders cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_terminal_cloud_order_with_live_asset cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_terminal_cloud_order_with_pending_resource_context cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_deleted_order_with_resource_context cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_allows_terminal_cloud_order_with_deleted_asset --verbosity 2
git diff --check
```

说明：首次聚焦测试命令误用不存在的 `cloud.tests.CloudLifecycleTests` 选择器，已改用 `CloudServerServicesTestCase` 重跑通过。`makemigrations --check --dry-run` 无模型变更，但默认 MySQL 迁移历史检查因沙箱无法连接 `127.0.0.1` 输出警告。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 19:22 自动监工：迁移旧机删除与终态订单清理保护

### 范围

本轮继续监工 Shop Django 后端仓库，起始最近提交为 `1131f74 记录云订单清理和计划保护优化`。重点复查云资产到期事实源、订单旧到期字段、旧计划快照表、退款旧入口、废弃 app 回流、迁移旧机删除是否被普通通知 payload 的资产可见性校验误挡、旧记录清理是否会误删仍保留云资源线索的终态订单，以及自动续费重试任务是否可能被重复执行。

### 复查结论

- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 当前运行域，未发现旧 `accounts/finance/mall/monitoring/dashboard_api/biz` 运行时 app 回流。
- 未发现 `CloudServerOrder.service_expires_at` 模型字段恢复；运行代码没有对已移除订单到期列做危险 ORM 过滤、排序、批量更新或 values 查询。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；`service_expires_at` 命中仍是 API 兼容字段、日志字段或从资产事实派生的展示值。
- 未发现 `normalize_service_expiry`、`service_expired_at`、旧计划快照模型 `CloudLifecyclePlan/CloudNoticePlan/CloudAutoRenewPlan`、退款旧函数名、退款旧入口或 `refunded` 运行时状态回流。
- 迁移旧机删除的事实源是迁移流程写入的 `CloudServerOrder.migration_due_at`，不是普通服务到期通知 payload；执行入口 `run_replaced_order_delete(..., enforce_schedule=True)` 仍会二次检查迁移时间、删除时间窗口、关机计划开关和任务认领。
- 旧记录清理对 `cancelled/expired/failed` 终态云订单仍需确认没有当前 IP、实例名、实例 ID、云资源 ID 或代理 host 线索，避免失败开通、取消或过期订单仍待云端清理时被本地历史清理提前删掉。
- 自动续费重试任务在执行前需要先原子认领，否则同一轮或并发调用可能重复检查同一条 pending 任务并重复增加 attempts。

### 功能变更

- `lifecycle_tick()` 执行 `migration_due_orders` 时不再先调用 `_cloud_expiry_notice_payload()` 判断 `valid`，避免旧机资产已经进入删除中、不可出现在普通代理通知列表时，迁移旧机删机计划被错误跳过。
- `_run_auto_renew_retry_task()` 执行前用 `status=pending` 和 `next_check_at<=now` 原子更新认领任务，并把 `next_check_at` 临时推进 30 分钟；未认领成功时直接返回，避免重复执行同一条自动续费重试任务。
- `cleanup_old_records` 的云订单清理过滤进一步收窄：`cancelled/expired/failed` 终态订单只有在没有当前云资源线索，且没有待执行删机时间或删机时间已早于保留截止线时，才进入清理候选。
- 补充迁移旧机生命周期回归测试：覆盖资产状态为删除中时仍会按迁移计划调用旧机删除入口。
- 补充迁移旧机入口单元回归测试：覆盖通知 payload 返回 `valid=False` 时，迁移旧机删除仍按 `migration_due_at` 进入 `run_replaced_order_delete(enforce_schedule=True)`，且不再调用通知载荷校验。
- 补充旧记录清理回归测试：覆盖终态订单仍有 IP、server name、instance ID 和未来删机时间时不会被清理。
- 补强自动续费重试回归测试：覆盖余额仍不足后的重复调用不会在认领 TTL 内再次增加 attempts，充值后仍可按下一次检查时间继续重试成功。

### 验证

已通过：

```bash
uv run python -m py_compile cloud/lifecycle.py cloud/tests.py core/management/commands/cleanup_old_records.py
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_deletes_migration_due_order_with_deleting_asset cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_terminal_cloud_order_with_pending_resource_context cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_terminal_cloud_order_with_live_asset cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_allows_terminal_cloud_order_with_deleted_asset cloud.tests.CloudServerServicesTestCase.test_auto_renew_retry_task_waits_for_recharge_then_retries --verbosity 1
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_migration_delete_uses_migration_due_without_notice_payload cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_deletes_migration_due_order_with_deleting_asset cloud.tests.CloudServerServicesTestCase.test_replaced_order_delete_respects_asset_shutdown_switch cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_terminal_cloud_order_with_pending_resource_context cloud.tests.CloudServerServicesTestCase.test_auto_renew_retry_task_waits_for_recharge_then_retries --verbosity 1
git diff --check
```

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，未执行真实云端删机。

## 2026-06-02 18:58 自动监工：旧记录清理云订单保护

### 范围

本轮继续监工 Shop Django 后端仓库，重点复查废弃 app、旧到期字段、旧计划快照表、退款入口、删除计划和通知计划认领保护、通知计划与关机开关一致性，以及定时任务里可能影响云订单生命周期的数据清理逻辑。

### 复查结论

- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 当前运行域，未发现旧 `accounts/finance/mall/monitoring/dashboard_api/biz` 运行时 app 回流。
- 未发现 `CloudServerOrder.service_expires_at` 模型字段恢复；运行代码中的 `service_expires_at` 仍是兼容展示字段或日志字段，真实到期事实读写 `CloudAsset.actual_expires_at`。
- 未发现旧 `CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan` 快照模型或退款运行入口回流。
- 发现 `cleanup_old_records` 定时清理命令会把 `completed/expiring/renew_pending/suspended/deleting` 等非终态云订单纳入删除候选，只要关联资产到期早于保留期就可能被清理；该命令由 `bot/runner.py` 定时注册，属于生产风险。
- 终端版 Codex 只读复查指出 `cloud/api_tasks.py` 的通知计划未来计划没有复用运行时同一套 `shutdown_enabled` 过滤，可能展示实际不会发送的删机或固定 IP 回收提醒。

### 功能变更

- `core/management/commands/cleanup_old_records.py` 的云订单清理条件收窄为只清理终态订单：`cancelled/expired/failed`，以及已删除且固定 IP 回收窗口已结束的订单。
- 非终态云订单不再因为资产 `actual_expires_at` 早于保留期而进入清理候选。
- 移除该命令里不再需要的 `CloudAsset` 导入。
- `cloud/api_tasks.py` 的通知计划详情补齐关机计划开关判断：资产或云账号关闭关机计划时，不再展示删机提醒和固定 IP 回收提醒的未来计划项。

### 验证

已通过：

```bash
uv run python -m py_compile cloud/api_tasks.py core/management/commands/cleanup_old_records.py cloud/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_deleted_order_until_retained_ip_window_ends cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_non_terminal_cloud_orders cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_hides_shutdown_disabled_lifecycle_notices --verbosity 1
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

剩余风险：本轮没有执行真实旧记录清理命令，也没有跑完整测试套件。

## 2026-06-02 18:52 自动监工：任务失败重试保护

### 范围

本轮继续监工 Shop Django 后端仓库，起始工作树干净，最近提交为 `7559bd4 记录CLI只读审查和敏感文本收口`。本轮重点复查云资产到期事实源、订单旧到期字段、旧计划快照表、退款旧入口、废弃 app 回流，以及删除计划和通知计划的数据库任务认领冲突保护。

### 复查结论

- `CloudServerOrder` 仍未恢复 `service_expires_at` 模型字段；生产代码未发现对旧订单到期列的危险 ORM 字段定义、过滤、排序、批量更新或 values 查询。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；订单接口中的 `service_expires_at` 只作为兼容 payload 字段，显式编辑写入资产事实字段。
- 未发现 `refund_to_balance`、`refund_balance`、`STATUS_REFUNDED`、`refunded` 旧状态、`CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan`、`normalize_service_expiry` 或 `service_expired_at` 回流。
- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 四个当前运行时 app；旧 `accounts/finance/mall/monitoring/dashboard_api/biz` 目录未恢复，`dashboard_api` 命中仍只是 URL namespace 和现有 helper 命名。
- 代理列表详情、删除计划展示和生命周期执行入口仍共同读取 `CloudAsset.shutdown_enabled` 与云账号 `shutdown_enabled`，没有出现两套开关。

### 功能变更

- `cloud/lifecycle_tasks.py` 增加失败任务重试保护窗口。`CloudLifecycleTask` 和 `CloudNoticeTask` 失败后不会被同一轮计划立即再次认领，只有超过保护窗口或旧任务没有运行时间时才允许重试。
- 完成任务时同步刷新 `last_run_at`，让失败冷却按最后一次实际结束时间计算，而不是只按认领时间计算。
- `cloud/tests.py` 新增回归用例，覆盖生命周期任务和通知任务失败后保护期内不可重复认领、保护期后可重试。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py bot/handlers.py cloud/models.py cloud/asset_expiry.py cloud/dashboard_snapshots.py cloud/services.py cloud/provisioning.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/lifecycle_tasks.py cloud/api.py cloud/api_assets.py cloud/api_asset_edit.py cloud/api_orders.py cloud/api_tasks.py cloud/api_sync.py cloud/api_monitors.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py orders/services.py orders/payment_scanner.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_failed_lifecycle_and_notice_tasks_wait_retry_window cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_lifecycle_asset_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_skips_when_lifecycle_task_claimed --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_proxy_log_preview_masks_secret_tail cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_dashboard_order_expiry_update_syncs_asset_expiry_and_lifecycle_plan cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_public_asset_renewal_expiry_does_not_claim_unowned_asset --noinput --verbosity 1
```

说明：首次聚焦测试命令误把订单续费超时用例挂在不存在的 `orders.tests.PublicAssetRenewalOrderTests` 类下，导致选择器错误；已用正确类 `orders.tests.ChainPaymentScannerTestCase` 单独重跑并通过。

剩余风险：本轮未跑完整测试套件，也未覆盖真实 MySQL、真实 AWS Lightsail 或真实阿里云 API。

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
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py cloud/api.py cloud/api_tasks.py shop/admin_urls.py cloud/tests.py
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

### 范围

修复后台手动编辑云订单 MTProxy 密钥后的同步问题。

### 运行时变化

- `cloud_order_detail` now accepts a standalone non-empty `mtproxy_secret` edit.
- 手动密钥编辑会保存到 `CloudServerOrder`，并同步到关联的主 `CloudAsset`。
- 仅保存密钥时继续保持“只接受非空值”的行为，空 payload 不会清空已保存密钥。
- 新增聚焦回归测试，覆盖只编辑密钥的订单保存。

### 验证

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/api_orders.py cloud/tests.py
uv run python manage.py test cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_secret_edit_syncs_primary_asset cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_previous_ip_edit_syncs_primary_records --noinput --verbosity 1
uv run python manage.py check
```

## 2026-06-02 public-asset-renewal-no-pending-owner-claim

### 范围

修复公共未绑定资产续费订单的归属安全问题。

### 运行时变化

- 创建待支付的公共未绑定 `CloudAsset` 续费订单时，不再把付款人写入 `CloudAsset.user`。
- 资产仍会关联到待支付续费订单，以防止重复下单；但在恢复成功前，资产归属保持不变。
- 支付超时清理现在可以安全解除待支付订单关联，不会让未支付的公共资产被尝试付款的人占用。
- 新增聚焦回归覆盖，验证无主未附加固定 IP 资产的公共续费超时场景。

### 验证

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/services.py orders/tests.py
uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_public_asset_renewal_expiry_does_not_claim_unowned_asset orders.tests.ChainPaymentScannerTestCase.test_expired_asset_renewal_payment_unbinds_asset_for_retry cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_address_order_forces_usdt_from_trx_source --noinput --verbosity 1
uv run python manage.py check
```

## 2026-06-02 asset-renewal-expiry-retry-note

### 范围

修复未绑定资产续费订单的支付超时恢复问题。

### 运行时变化

- 未绑定 `CloudAsset` 的地址支付续费订单过期时，扫描器现在会解除资产绑定，并给资产追加重试备注。
- 既有资产备注会保留，重试备注会去重追加，避免重复超时文本。
- 这让支付窗口过期后的重试状态预期变成显式状态。

### 验证

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile orders/payment_scanner.py orders/tests.py
uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_expired_asset_renewal_payment_unbinds_asset_for_retry orders.tests.ChainPaymentScannerTestCase.test_renew_pending_cloud_with_previous_ip_is_candidate --noinput --verbosity 1
uv run python manage.py check
```

## 2026-06-02 aws-sync-ip-release-order-cleanup

### 范围

修复 AWS 同步释放保留固定 IP 后的清理问题。

### 运行时变化

- AWS 同步成功释放未附加固定 IP 后，现在复用生命周期清理路径。
- AWS 释放成功后，关联的已删除保留订单会清空陈旧的 `public_ip`、`static_ip_name`、`mtproxy_host` 和 `ip_recycle_at`。
- 被释放资产会保留 `previous_public_ip`，清空 `public_ip`，并记录一条同时关联资产和订单的 IP 回收历史。
- 新增聚焦回归覆盖 AWS 同步释放辅助函数。

### 验证

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/tests.py
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_clears_retained_order_after_successful_release cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_shutdown_disabled_account cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_global_ip_delete_switch --noinput --verbosity 1
uv run python manage.py check
```

## 2026-06-02 cleanup-keeps-retained-ip-orders

### 范围

修复保留固定 IP 订单历史的清理安全问题。

### 运行时变化

- `cleanup_old_records` no longer treats every `deleted` cloud order as immediately cleanup-eligible.
- 带有未来保留 IP `ip_recycle_at` 的已删除云订单，会保留到配置的清理截止时间超过其 IP 回收时间之后。
- 这会在固定 IP 仍可恢复期间，保留保留 IP 续费上下文和关联的 `CloudIpLog` 历史。
- 新增聚焦回归覆盖清理过滤条件。

### 验证

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile core/management/commands/cleanup_old_records.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_deleted_order_until_retained_ip_window_ends --noinput --verbosity 1
```

## 2026-06-02 unattached-ip-release-order-cleanup

### 范围

修复手动/后台资产级释放保留固定 IP 后的清理问题。

### 运行时变化

- 成功释放未附加固定 IP 后，现在也会清空关联已删除保留订单的 `public_ip`、`static_ip_name`、`mtproxy_host` 和 `ip_recycle_at`。
- IP 真实释放后，关联订单会标记为已发送回收通知，并关闭 IP 回收提醒。
- 回收历史日志现在同时保留被释放的 `CloudAsset` 和关联的 `CloudServerOrder`，避免陈旧续费/回收状态继续可见。
- 新增聚焦回归覆盖后台辅助路径触发的手动保留 IP 释放。

### 验证

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/lifecycle.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_manual_unattached_ip_delete_clears_retained_order_after_successful_release cloud.tests.CloudServerServicesTestCase.test_manual_unattached_ip_delete_writes_log_and_history_item --noinput --verbosity 1
```

## 2026-06-02 retained-ip-real-release-history

### 范围

修复 AWS 保留固定 IP 释放在生命周期计划历史中的展示问题。

### 运行时变化

- 生命周期 IP 删除历史现在会把 `AWS 固定 IP 已真实释放` 日志备注识别为已完成的保留 IP 释放记录。
- 即使释放前没有活跃生命周期计划行，已释放的保留固定 IP 也能出现在生命周期计划历史中。
- 新增聚焦回归覆盖从 `CloudIpLog` 重建真实释放历史行的场景。

### 验证

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile bot/api.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_real_released_retained_ip_history_without_active_row cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_ip_delete_history_item cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_move_deleted_unattached_ip_active_row_to_history --noinput --verbosity 1
```

## 2026-06-02 aws-retained-ip-missing-skip

### 范围

修复 AWS 保留固定 IP 同步一致性问题。

### 运行时变化

- AWS 缺失实例校验现在会把 `固定IP仍存在但未附加` 和 `固定IP保留中` 云厂商状态视为固定 IP 支撑的资产。
- 远端仍存在的保留固定 IP，不会仅因为旧实例 ID 不再出现就进入缺失确认状态。
- 新增聚焦回归覆盖同一同步周期内先保留 IP、再执行缺失校验的路径。

### 验证

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_aws_retained_ip_preserves_existing_asset_user cloud.tests.CloudServerServicesTestCase.test_aws_retained_unattached_asset_is_not_missing_deleted_when_static_ip_exists --noinput --verbosity 1
```

## 2026-06-02 sync-user-binding-persist-false

### 范围

修复云同步归属绑定问题。

### 运行时变化

- `sync_cloud_asset_user_binding(..., persist=False)` now updates the in-memory `CloudAsset.user` / `user_id` fields without issuing its own database write.
- AWS 和阿里云同步路径在 `asset.save()` 前调用该辅助函数时，现在可以填充空资产归属，同时保留既有归属。
- 新增聚焦回归覆盖，确认 `persist=False` 在调用方保存前只修改 Python 对象。

### 验证

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/services.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_user_binding_uses_asset_name_tg_id cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_user_binding_persist_false_sets_in_memory_user cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry --noinput --verbosity 1
```

## 2026-06-02 early-provisioning-asset-field-preservation

### 范围

开通资产保留改造的后续小修。

### 运行时变化

- 早期开通资产写入在更新已有资产时，现在会保留既有资产归属、到期时间、MTProxy 链接、密钥、host、port、代理链接列表、价格和币种。
- `_upsert_server_asset()` and early provisioning helpers share the same default-value preservation helper.
- 新增聚焦回归覆盖 `_mark_provisioning_start()` 和 `_mark_instance_created()`，避免最终成功处理前覆盖手工资产字段。

### 验证

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/provisioning.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_mark_success_preserves_existing_manual_asset_fields_on_update cloud.tests.CloudServerServicesTestCase.test_early_provisioning_steps_preserve_existing_manual_asset_fields --noinput --verbosity 1
```

## 2026-06-02 provisioning-asset-field-preservation

### 范围

针对已有云资产的开通写入路径安全修正。

### 运行时变化

- `_mark_success` no longer runs a duplicate `CloudAsset.update_or_create()` before the shared asset upsert helper.
- `_upsert_server_asset()` now preserves existing asset owner, expiry, MTProxy link, secret, host, port, and proxy-link list when updating an existing asset, while still filling blank fields from the order.
- 新建资产仍会接收订单运行时字段，包括 MTProxy 数据、价格和币种。
- 新增聚焦回归覆盖，确认开通成功不会重复创建资产或覆盖已有手工资产字段。

### 验证

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/provisioning.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_mark_success_updates_existing_server_asset_instead_of_creating_duplicate cloud.tests.CloudServerServicesTestCase.test_mark_success_preserves_existing_manual_asset_fields_on_update cloud.tests.CloudServerServicesTestCase.test_asset_renewal_mark_success_starts_new_service_period --noinput --verbosity 1
```

## 2026-06-02 mtproxy-link-write-consistency

### 范围

修复后台 MTProxy 链接编辑写入路径安全问题。

### 运行时变化

- 后台云订单编辑现在会解析提交的 `mtproxy_link`，并让 `mtproxy_secret`、host、port 和 `proxy_links` 与主链接保持一致。
- 后台云资产编辑现在会对资产及其关联订单应用同一套主链接规范化。
- 主链接替换会从 `proxy_links` 中移除陈旧的 `主代理` / `主链路` 条目，避免手动编辑链接后复制旧密钥。
- 新增聚焦回归覆盖更新 MTProxy 链接的订单详情编辑和资产编辑。

### 验证

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python -m py_compile cloud/api_orders.py cloud/api_asset_edit.py cloud/tests.py
uv run python manage.py check
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_blank_mtproxy_secret_preserves_existing_secret cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_mtproxy_link_refreshes_secret_and_proxy_links cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields --noinput --verbosity 1
```

## 2026-06-02 cloud-sync-manual-field-preservation

### 范围

保留资产归属和到期时间的同步安全修正。

### 运行时变化

- AWS 保留 IP 同步在把保留订单挂到无订单资产上时，不再覆盖既有 `CloudAsset.user`。
- 阿里云同步不再覆盖已跟踪资产的既有 `CloudAsset.actual_expires_at`。
- 合适时仍会从保留订单回填空资产归属。
- 新增聚焦回归覆盖 AWS 保留 IP 归属保留和阿里云保留资产到期时间保留。

### 验证

Passed locally with `PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1`:

```bash
uv run python manage.py check
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/tests.py
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_aws_retained_ip_preserves_existing_asset_user cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_updates_retained_asset_after_renewal_recovery --noinput --verbosity 1
```

## 2026-06-02 cloud-asset-payload-readonly-guard

### 范围

移除云资产 payload 构建中的读取路径副作用。

### 运行时变化

- `CloudAssetPayloadContext` now defaults to read-only payload rendering.
- 云资产 GET/详情 payload 在计算展示数据时，不再自动写入 `CloudAsset.user` 或 `CloudAsset.actual_expires_at`。
- 新增回归测试覆盖只读资产 payload 路径。

### 验证

Passed locally with `UV_CACHE_DIR=/private/tmp/shop-uv-cache`:

```bash
uv run python manage.py check
uv run python -m py_compile cloud/api_assets.py cloud/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_asset_get_payload_does_not_mutate_manual_asset_fields cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_user_binding_uses_asset_name_tg_id --noinput --verbosity 1
```

## 2026-06-02 trongrid-api-key-secret-preservation

### 范围

运行时字段保留保护后的敏感配置加固。

### 运行时变化

- 将 `trongrid_api_key` 作为敏感站点配置 key 处理。
- 后台对 `trongrid_api_key` 的空值保存现在会保留既有 API key，而不是清空。
- 后台配置响应不再在 `value_preview` 中返回完整 TRON API key 列表。
- 新增聚焦回归覆盖空 TRON API key 保存和响应脱敏。

### 验证

Passed locally with `UV_CACHE_DIR=/private/tmp/shop-uv-cache`:

```bash
uv run python manage.py check
uv run python -m py_compile core/runtime_config.py bot/api_site_configs.py bot/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.DashboardAuthSurfaceTestCase.test_sensitive_site_config_blank_value_preserves_existing_secret bot.tests.DashboardAuthSurfaceTestCase.test_trongrid_api_key_blank_value_preserves_and_masks_existing_secret --noinput --verbosity 1
```

## 2026-06-02 runtime-field-preservation-guard

### 范围

后端重构后的安全修正，用于保护运行时归属、到期时间和敏感字段持久化。

### 运行时变化

- 后台云订单编辑不再把 `CloudServerOrder.user` 或 `service_expires_at` 反向同步到 `CloudAsset.user` / `actual_expires_at`。
- 后台云资产编辑在提交值为空时会保留既有 `mtproxy_secret`。
- 敏感站点配置更新在提交值为空时会保留既有值。
- 订单主记录更新现在会把云身份、状态和代理字段变更应用到同一订单关联的所有服务器型 `CloudAsset` 记录，同时继续保留手工归属和到期字段。
- 新增聚焦回归覆盖空敏感配置保存、空 MTProxy 密钥保存、订单到期时间编辑和多记录订单详情同步。

### 验证

Passed locally with `UV_CACHE_DIR=/private/tmp/shop-uv-cache`:

```bash
uv run python manage.py check
uv run python -m py_compile bot/api.py bot/handlers.py cloud/services.py cloud/bootstrap.py cloud/api.py cloud/api_asset_edit.py cloud/sync_jobs.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/process_cloud_asset_sync_jobs.py orders/payment_scanner.py orders/tron_parser.py
uv run python -m py_compile bot/api_site_configs.py cloud/api_orders.py
uv run python -m py_compile bot/tests.py cloud/tests.py cloud/services.py
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test bot.tests.DashboardAuthSurfaceTestCase.test_sensitive_site_config_blank_value_preserves_existing_secret cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_blank_mtproxy_secret_preserves_existing_secret cloud.tests.CloudServerServicesTestCase.test_dashboard_order_expiry_update_recomputes_lifecycle_plan cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields --keepdb --noinput --verbosity 1
```

## 2026-06-01 task-center-and-monitor-split

### 范围

本轮继续拆分过大的云 API 面，新增统一任务中心 API，并把监控/IP 日志端点迁入独立模块。

### 运行时变化

- 新增 `cloud/api_monitors.py`，承接云 IP 日志和地址监控 API。
- 新增 `cloud/task_center.py`，承接统一后台任务中心概览。
- `cloud/api.py` now re-exports the monitor APIs and task center API for URL compatibility.
- 新增 `GET /admin/tasks/center/`，并保留 `GET /admin/tasks/` 作为旧任务列表。
- 新增重构工作区边界文档，方便后续轮次区分接管修改和既有脏文件。

### 前端变化

- 将 `/admin/tasks` 升级为任务中心页面，包含健康卡片和可搜索任务表。

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/api.py cloud/api_monitors.py cloud/task_center.py shop/admin_urls.py
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests_task_center.CloudTaskCenterApiTestCase --keepdb --noinput --verbosity 1
```

前端验证已通过，目录为 `/Users/a399/Desktop/data/vue-shop-admin`:

```bash
./node_modules/.bin/vue-tsc --noEmit --skipLibCheck -p apps/web-antd/tsconfig.json
```

## 2026-06-01 cloud-sync-runtime-split

### 范围

This refactor split cloud asset sync execution out of `cloud/api.py` and made sync jobs easier to operate, observe, and clean up.

### 运行时变化

- 新增 `cloud/sync_jobs.py` 作为云资产同步任务运行时模块。
- `cloud/api.py` now keeps cloud asset/order/dashboard API logic and re-exports sync job endpoints for existing dashboard URL aggregation.
- `process_cloud_asset_sync_jobs` imports execution helpers from `cloud.sync_jobs`, no longer from `cloud.api`.
- 批量同步任务子任务现在串行运行，不再使用线程池，因此进度更新、事件顺序、心跳和取消行为更确定。
- 在 `cloud-assets/sync-jobs/metrics/` 新增 `cloud_asset_sync_jobs_metrics` API。
- `cloud_assets_sync_status` now embeds the same metrics summary used by the frontend.
- 新增 `prune_cloud_sync_job_events`，支持按时间和单任务保留量清理事件表。

### 前端变化

- 在 `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd` 新增 `/admin/cloud-sync-jobs/:id` 专用同步任务详情页。
- 代理列表同步抽屉现在显示任务指标，并把每个任务行链接到详情页。
- 前端 API 类型现在包含 `DashboardCloudAssetSyncJobsMetrics`。

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/api.py cloud/sync_jobs.py cloud/management/commands/process_cloud_asset_sync_jobs.py cloud/management/commands/prune_cloud_sync_job_events.py shop/admin_urls.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_runs_enabled_accounts_and_merges_results cloud.tests.CloudServerServicesTestCase.test_cloud_asset_sync_jobs_metrics_returns_operational_summary cloud.tests.CloudServerServicesTestCase.test_cancel_queued_cloud_asset_sync_job_marks_terminal_and_events cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_with_selected_assets_uses_asset_scoped_tasks cloud.tests.CloudServerServicesTestCase.test_process_cloud_asset_sync_jobs_worker_processes_queued_job --keepdb --noinput --verbosity 1
```

前端验证已通过，目录为 `/Users/a399/Desktop/data/vue-shop-admin`:

```bash
./node_modules/.bin/vue-tsc --noEmit --skipLibCheck -p apps/web-antd/tsconfig.json
```

## 2026-06-01 cloud-asset-lifecycle-refactor

### 范围

本版本围绕云资产生命周期、表归属和运行时依赖清理进行了一轮较大后端重构。

### 数据库变化

- `cloud_server` physical table was removed.
- 历史服务器数据已迁入 `cloud_asset`。
- `cloud_asset` is now the only cloud resource fact table.
- `CloudIpLog.server` / `cloud_ip_log.server_id` was removed.
- Django migration 链：
  - `0037_server_table_to_cloud_asset`
  - `0038_drop_server_model_and_iplog_server`

### 运行时模型方向

- `CloudAsset(kind='server')` is the canonical server asset record.
- `CloudServerOrder` is business context for purchase, renewal, migration, rebuild, deletion, and audit.
- `Server` is no longer a Django model. A small import compatibility facade remains in `cloud.models` so older scripts/tests do not fail immediately on import, but new runtime code should not use it.

### 生命周期重构

- 新增 `cloud/lifecycle_schedule.py`：
  - central lifecycle time calculation
  - order schedule fields
  - orphan asset delete time
  - unattached static IP release time
  - runtime config helpers
- 新增 `cloud/lifecycle_execution.py`：
  - scheduled/manual shutdown
  - delete order
  - delete migrated/replaced order
  - delete orphan asset
  - release retained static IP
  - release unattached static IP
  - cloud API timeout handling
- `cloud/lifecycle.py` now scans due work and dispatches to execution helpers.

### 运行时依赖清理

- `cloud/services.py` now writes primary record updates to `CloudAsset`.
- `cloud/provisioning.py` no longer creates/upserts `Server` rows; provisioning writes `CloudAsset`.
- `cloud/api.py` keeps server endpoint names for compatibility but queries `CloudAsset(kind='server')`.
- `bot/api.py` no longer syncs notes to `Server`.
- `record_cloud_ip_log` records asset/order context only.

### 已更新文档

- `ARCHITECTURE.md`
- `docs/DATA_FLOW_AND_PERSISTENCE.md`
- `docs/DB_NAMING_CONVENTIONS.md`
- `docs/refactor-mapping.md`
- `docs/table-rename-plan.md`
- `docs/project-overview.md`

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/models.py cloud/services.py cloud/lifecycle.py cloud/provisioning.py cloud/api.py bot/api.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check --verbosity 2
uv run python manage.py migrate --plan
uv run python manage.py migrate cloud 0038
```

迁移后的本地数据库探查：

- `cloud_server_exists`: `False`
- `cloud_ip_log.server_id`: removed
- Django 注册的 `cloud.Server` 模型：`None`

### 已知后续事项

- 部分测试和兼容管理命令仍引用 `Server` 门面。
- `sync_aws_assets.py` and `sync_aliyun_assets.py` still need a deeper pass to rename local variables and remove old wording, although the `Server` facade currently routes writes to `CloudAsset`.
- 完整 Django 测试仍受本地 MySQL 测试库权限阻塞：

```sql
GRANT ALL PRIVILEGES ON test_a.* TO 'a'@'localhost';
FLUSH PRIVILEGES;
```

## 2026-06-01 cloud-asset-runtime-cleanup

### 范围

Second refactor pass after the table migration. This pass removes the `Server` compatibility facade from `cloud.models`, moves old command/test compatibility to an explicit command-side wrapper, and adds indexes/state helpers.

### 运行时变化

- 从 `cloud.models` 和 `__all__` 中移除 `Server`。
- 新增 `cloud/server_records.py`，作为面向旧命令和测试的显式 `CloudAsset(kind='server')` 兼容包装。
- 更新同步和维护命令，从 `cloud.server_records` 导入 `Server`，不再从 `cloud.models` 导入。
- 新增 `cloud/lifecycle_state.py`，承接订单状态到资产状态的映射。
- `cloud/api.py` now uses `primary_record_updates_for_order_status` from `cloud.lifecycle_state`.

### 数据库变化

- 新增 `0039_cloud_asset_indexes`：
  - `ca_kind_status_active_idx`
  - `ca_provider_acct_inst_idx`
  - `ca_provider_acct_ip_idx`
  - `ca_order_status_idx`
  - `ca_kind_user_status_idx`

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/lifecycle_state.py cloud/models.py cloud/api.py cloud/server_records.py
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/upsert_cloud_asset.py cloud/management/commands/dedupe_servers.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check --verbosity 2
uv run python manage.py migrate cloud 0039
uv run python manage.py migrate --plan
```

### 剩余大型重构

- 物理拆分 `cloud/api.py`。
- 物理拆分 `bot/api.py`。
- 待测试覆盖调整后，把同步命令和测试中的旧服务器措辞从 `Server` 改为 `CloudAsset`。

## 2026-06-01 cloud-dashboard-api-split

### 范围

第三轮重构聚焦缩小过大的后台云 API 模块，同时保留现有 URL 导入兼容。

### 运行时变化

- 新增 `cloud/api_servers.py`，承接服务器型 `CloudAsset(kind='server')` 后台端点：
  - server list payloads
  - server rebuild preserve-link action
  - server delete action
  - server statistics
- 新增 `cloud/api_plans.py`，承接云套餐/价格后台端点：
  - provider pricing list
  - custom cloud plan list
  - plan create/update/delete
- `cloud/api.py` now imports these endpoint names at the bottom as compatibility exports, so `shop/admin_urls.py` can continue using `cloud_api.<view_name>`.

### 清理

- 移除 `update_cloud_asset` 中对已退役 `server` 变量的剩余运行时写入。
- 移除已删除的 ORM 路径：
  - `order__server__server_name`
  - `order__server__note`
  - `CloudIpLog.select_related('server')`
  - `Q(server__isnull=False)`

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/api.py cloud/api_servers.py cloud/api_plans.py
uv run python manage.py check
```

## 2026-06-01 bot-product-api-split

### 范围

Fourth refactor pass started splitting the oversized `bot/api.py` dashboard module.

### 运行时变化

- 新增 `bot/api_products.py`，承接商品后台端点：
  - product list
  - product create
  - product update
- `bot/api.py` keeps compatibility exports for `products_list`, `create_product`, and `update_product`, so existing dashboard URL imports continue to work.

### 验证

本地已通过:

```bash
uv run python -m py_compile bot/api.py bot/api_products.py
uv run python manage.py check
```

## 2026-06-01 bot-admin-api-split

### 范围

Fifth refactor pass continued splitting `bot/api.py` by moving admin account management endpoints.

### 运行时变化

- 新增 `bot/api_admin_users.py`，承接后台管理员账号端点：
  - admin user list
  - admin create/update/delete
  - current admin password change
- `bot/api.py` keeps compatibility exports for the moved endpoints, so `shop/admin_urls.py` continues resolving the same attributes.

### 验证

本地已通过:

```bash
uv run python -m py_compile bot/api.py bot/api_admin_users.py bot/api_products.py
uv run python manage.py check
```

## 2026-06-01 bot-site-config-api-split

### 范围

Sixth refactor pass moved site configuration and button/text configuration dashboard endpoints out of `bot/api.py`.

### 运行时变化

- 新增 `bot/api_site_configs.py`，承接：
  - site config list/group/update/init
  - text config initialization
  - button config read/update/init
  - daily expiry summary notification test
- 在 `bot/api.py` 中保留已迁移 view 名称和私有 payload 辅助函数的兼容导出。
- 从 `bot/api.py` 移除现已不用的配置/文本/按钮导入。

### 验证

本地已通过:

```bash
uv run python -m py_compile bot/api.py bot/api_site_configs.py
uv run python manage.py check
```

## 2026-06-01 bot-cloud-account-api-split

### 范围

Seventh refactor pass moved cloud account dashboard management out of `bot/api.py`.

### 运行时变化

- 新增 `bot/api_cloud_accounts.py`，承接：
  - cloud account list/detail
  - create/update/delete
  - AWS and Alibaba Cloud account verification
  - cloud account payloads, duplicate detection, external sync log payloads
- 在 `bot/api.py` 中保留已迁移公共 view 和私有辅助函数名的兼容导出。

### 验证

本地已通过:

```bash
uv run python -m py_compile bot/api.py bot/api_cloud_accounts.py bot/api_site_configs.py
uv run python manage.py check
```

## 2026-06-01 bot-auth-api-split

### 范围

Eighth refactor pass moved dashboard authentication and current-user endpoints out of `bot/api.py`.

### 运行时变化

- 新增 `bot/api_auth.py`，承接：
  - login/logout/refresh
  - auth code list
  - TOTP start/bind
  - user info and current user metadata
- 在 `bot/api.py` 中保留全部已迁移鉴权 view 名称的兼容导出。
- 从 `bot/api.py` 移除未使用的 `authenticate`、`login` 和 `logout` 导入。

### 验证

本地已通过:

```bash
uv run python -m py_compile bot/api.py bot/api_auth.py bot/api_cloud_accounts.py bot/api_site_configs.py
uv run python manage.py check
```

## 2026-06-01 bot-user-balance-api-split

### 范围

Ninth refactor pass moved Telegram user listing and balance management endpoints out of `bot/api.py`.

### 运行时变化

- 新增 `bot/api_users.py`，承接：
  - user list
  - manual USDT/TRX balance update
  - cloud discount update
  - user balance detail timeline
  - balance ledger payload and manual ledger recording helpers
- 在 `bot/api.py` 中保留已迁移公共 view 和私有流水辅助函数名的兼容导出。
- 从 `bot/api.py` 移除未使用的余额/查询导入。

### 验证

本地已通过:

```bash
uv run python -m py_compile bot/api.py bot/api_users.py
uv run python manage.py check
```

## 2026-06-01 bot-operation-log-api-split

### 范围

Tenth refactor pass moved bot operation log dashboard endpoints out of `bot/api.py`.

### 运行时变化

- 新增 `bot/api_operation_logs.py`，承接操作日志 payload 和搜索/列表 view。
- 在 `bot/api.py` 中保留兼容导出。
- 从 `bot/api.py` 移除现已不用的 `BotOperationLog` 导入。

### 验证

本地已通过:

```bash
uv run python -m py_compile bot/api.py bot/api_operation_logs.py
uv run python manage.py check
```

## 2026-06-01 bot-telegram-api-split

### 范围

Eleventh refactor pass moved Telegram dashboard login, chat, message, and group-filter endpoints out of `bot/api.py`.

### 运行时变化

- 新增 `bot/api_telegram.py`，承接：
  - Telegram account overview
  - personal account login/code/password/status flows
  - account notification toggles
  - group filter list/detail/create/update
  - chat message send/archive/list
  - Telegram payload and validation helpers
- 在 `bot/api.py` 中保留已迁移公共 view 和私有辅助函数名的兼容导出。
- 从 `bot/api.py` 移除 Telegram 专属模型/服务导入。

### 验证

本地已通过:

```bash
uv run python -m py_compile bot/api.py bot/api_telegram.py
uv run python manage.py check
```

## 2026-06-01 dashboard-api-core-extraction

### 范围

Twelfth refactor pass addressed the cross-domain coupling where cloud and orders dashboard APIs imported private helpers from `bot/api.py`.

### 运行时变化

- 新增 `core/dashboard_api.py` 作为共享后台 API 工具模块。
- 将通用辅助函数迁入 core：
  - response helpers: `_ok`, `_error`
  - formatting helpers: `_iso`, `_decimal_to_str`, `_parse_decimal`
  - request/query helpers: `_read_payload`, `_get_keyword`, `_apply_keyword_filter`
  - payload/label helpers: `_split_usernames`, `_user_payload`, `_status_label`, `_days_left`, `_countdown_label`, `_provider_label`, `_provider_status_label`, `_region_label`, `_server_source_label`
  - dashboard session/auth helpers and decorators
- `bot/api.py` now re-exports those helpers for compatibility.
- `cloud/api.py`, `cloud/api_servers.py`, `cloud/api_plans.py`, and `orders/api.py` import shared helpers/decorators from `core.dashboard_api`, removing their `bot.api` helper dependency.

### 验证

本地已通过:

```bash
uv run python -m py_compile core/dashboard_api.py bot/api.py bot/api_auth.py bot/api_users.py bot/api_operation_logs.py bot/api_cloud_accounts.py bot/api_site_configs.py bot/api_admin_users.py bot/api_products.py bot/api_telegram.py cloud/api.py cloud/api_servers.py cloud/api_plans.py orders/api.py
uv run python manage.py check
```

## 2026-06-01 provisioning-structured-result-logging

### 范围

Thirteenth refactor pass removed production `print('[PROVISION_RESULT]', ...)` calls from cloud provisioning.

### 运行时变化

- 在 `cloud/provisioning.py` 中新增 `_log_provision_result()`。
- 将所有开通结果 `print()` 调用替换为 `logger.log(...)`。
- 开通结果日志现在包含结构化 `extra={'provision_result': ...}` 字段。
- 结果 payload 中的 MTProxy 链接改为记录脱敏预览，不再记录完整原始链接。

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/provisioning.py core/dashboard_api.py bot/api.py cloud/api.py cloud/api_servers.py cloud/api_plans.py orders/api.py
uv run python manage.py check
```

## 2026-06-01 cloud-dashboard-api-helper-extraction

### 范围

Fourteenth refactor pass reduced reverse dependencies where split cloud dashboard API modules treated `cloud/api.py` as a shared helper library.

### 运行时变化

- 新增 `cloud/dashboard_snapshots.py`，作为唯一后台快照刷新协调器。
- `cloud/services.py` and `cloud/lifecycle.py` now refresh dashboard snapshots through `cloud.dashboard_snapshots` instead of importing `cloud.api`.
- 新增 `cloud/dashboard_api_helpers.py`，承接云后台展示辅助函数：
  - cloud plan config id generation
  - preserve-link status labels
  - dashboard sort direction and expiry ordering
- `cloud/api_servers.py` and `cloud/api_plans.py` no longer import `cloud.api` through `_api_helpers()`.
- 将重建后台重试执行逻辑从 `cloud/api.py` 迁入 `cloud/services.py`，命名为 `run_cloud_server_rebuild_job()`。
- 保留 `cloud/api.py` 对已抽取辅助函数名的导入，确保既有内部引用和兼容导入继续可用。

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/dashboard_api_helpers.py cloud/dashboard_snapshots.py cloud/api.py cloud/api_servers.py cloud/api_plans.py cloud/services.py cloud/lifecycle.py
uv run python manage.py check
```

## 2026-06-01 async-runtime-config-fix

### 范围

Fifteenth refactor pass addressed the P0 issue where `get_runtime_config()` returns env/default values in a running async event loop and can miss updated `SiteConfig` values.

### 运行时变化

- 将 `bot/runner.py`、`bot/handlers.py` 和 `cloud/resource_monitor.py` 中的异步运行时配置读取替换为 `await core.cache.get_config(...)`。
- 从异步运行时路径移除 `asyncio.to_thread(get_runtime_config, ...)` 和 `sync_to_async(get_runtime_config, ...)` 用法。
- 重构 `core/cache.py:get_config()`，让同步 DB/default 兜底逻辑在专用线程辅助函数中执行。
- 已确认 `async def` 函数体中没有残留直接 `get_runtime_config()` 调用，也没有残留 `to_thread/sync_to_async(get_runtime_config)` 适配器。

### 验证

本地已通过:

```bash
uv run python -m py_compile core/cache.py core/runtime_config.py bot/runner.py bot/handlers.py cloud/resource_monitor.py cloud/dashboard_api_helpers.py cloud/dashboard_snapshots.py cloud/api.py cloud/api_servers.py cloud/api_plans.py cloud/services.py cloud/lifecycle.py
uv run python manage.py check
```

## 2026-06-01 dashboard-bearer-write-auth

### 范围

第十六轮重构处理 CSRF/鉴权边界风险：csrf-exempt 后台写 API 仍可能通过 cookie session 状态完成认证。

### 运行时变化

- `core/dashboard_api.py` now treats unsafe dashboard methods (`POST`, `PUT`, `PATCH`, `DELETE`, etc.) as bearer-only.
- 后台写请求必须提供 `Authorization: Bearer session-...`；仅依赖 cookie 认证的 `request.user` 不再被写 view 接受。
- 安全读方法仍支持既有 cookie/session 认证，以保持兼容。
- 更新后台鉴权测试，让写测试附带显式 bearer session header。
- 新增回归测试，证明仅 cookie 的后台写请求会以 401 拒绝。

### 验证

本地已通过:

```bash
uv run python -m py_compile core/dashboard_api.py bot/tests.py bot/api_auth.py bot/api.py bot/api_admin_users.py
uv run python manage.py check
```

本地受阻:

```bash
uv run python manage.py test bot.tests.DashboardSessionExpiryTestCase bot.tests.DashboardAuthSurfaceTestCase --keepdb
```

聚焦测试受本地 MySQL 测试库权限阻塞:

```text
Access denied for user 'a'@'localhost' to database 'test_a'
```

## 2026-06-02 CLI 只读审查与敏感搜索文本收口

### 范围

第二十二轮监工使用终端版 `codex exec` 以只读沙箱复查重构回流风险，并结合本地扫描处理两个最小安全修复点。

### 功能变更

- `cloud/provisioning.py` 的开通成功日志改用代理链接专用脱敏预览，避免普通预览保留 `secret` 尾部。
- `cloud/api_asset_snapshots.py` 的云资产列表快照搜索文本不再持久化完整 `mtproxy_link` 或 `proxy_links`，仅保留代理链路名称、模式、server 和 port 等非敏感搜索词。
- `cloud/cache.py` 的监控缓存初始化把 QuerySet 构造和求值完整包进 `sync_to_async` 线程，避免 async 函数体里残留同步 ORM 构造风险。

### CLI 复查结论

- 终端版 Codex：`codex-cli 0.135.0-alpha.1`。
- 自动化仍为 `ACTIVE`，10 分钟周期，模型 `gpt-5.5`。
- CLI 只读复查未发现废弃 app 恢复、旧到期字段危险 ORM、旧计划快照模型恢复、退款逻辑恢复或 `cloud.api` patch 目标回流。
- CLI 指出的快照搜索文本敏感副本风险和 `cloud/cache.py` async ORM 包裹建议已在本轮处理。

### 验证命令

已通过：

```bash
uv run python -m py_compile cloud/api_asset_snapshots.py cloud/cache.py cloud/provisioning.py cloud/tests.py
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py shell -c "from cloud.provisioning import _mask_proxy_log_preview; secret='ee0123456789abcdef0123456789abcdef'; link=f'tg://proxy?server=10.0.0.93&port=9528&secret={secret}'; preview=_mask_proxy_log_preview(link, visible=12); assert secret not in preview; assert secret[-12:] not in preview; assert 'secret=***' in preview; print({'ok': True, 'preview': preview})"
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_proxy_log_preview_masks_secret_tail cloud.tests.CloudServerServicesTestCase.test_cloud_asset_dashboard_snapshot_search_text_masks_proxy_secret --verbosity 1
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

### 范围

第二十轮重构把云资源缺失删除确认标记文本替换为结构化云资产同步状态。

### 运行时变化

- 新增 `CloudAsset.sync_state` JSON 字段和 migration `0040_cloudasset_sync_state`。
- `cloud/sync_safety.py` now treats `sync_state['missing_confirmation']` as the source of truth.
- 移除旧 `[missing_sync_count:...]` / `[msc_at:...]` provider-status 标记的解析和写入。
- AWS 和阿里云缺失资源同步现在会：
  - increments structured confirmation count on each missing pass
  - keeps the asset/server running while count is below threshold
  - deletes only after the structured count reaches the configured threshold
  - clears missing confirmation state when a later sync sees the resource live again
- 后台生命周期/删除计划 view 现在从 item/asset 的 `sync_state` 读取确认进度。
- 更新受影响测试，改为断言结构化 `sync_state`，不再断言 provider-status 标记文本。

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/sync_safety.py cloud/models.py cloud/migrations/0040_cloudasset_sync_state.py cloud/server_records.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py bot/api.py cloud/tests.py
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py check
```

本地受阻:

```bash
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_missing_confirmation_note_preserves_existing_note cloud.tests.CloudServerServicesTestCase.test_sync_missing_confirmation_requires_interval cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_expose_missing_confirmation_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_unattached_ip_show_confirmation_progress_in_state_and_note cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_instance_requires_five_passes_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_missing_instance_requires_five_passes_before_delete --keepdb
```

聚焦 DB 测试受本地 MySQL 测试库权限阻塞:

```text
Access denied for user 'a'@'localhost' to database 'test_a'
```

## 2026-06-02 机器人返回链路收敛

### 范围

本轮复查代理列表、订单详情、IP 查询结果进入二级操作后的返回逻辑，重点处理带冒号的来源 callback，避免从筛选页或指定页进入详情后被带回默认列表第一页。

### 运行时变化

- 订单详情的重新安装、继续初始化、修改配置按钮会携带原详情页的返回路径。
- 资产详情的续费、更换 IP、重新安装、修改配置、修改到期时间按钮会携带原列表或查询入口。
- 重新安装确认页的取消按钮、资产续费套餐页、修改配置页、修改到期时间成功结果会返回原页面。
- IP 查询结果进入订单或资产操作时统一回到到期时间查询入口，不再回到默认代理列表第一页。
- 详情 callback 解析改为保留冒号后的完整来源路径，支持 `profile:orders:cloud:filter:...` 这类嵌套路径。

### 验证

已通过：

```bash
uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_query_keyboard_includes_reinstall_and_expiry_actions
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
git diff --check
```

## 2026-06-02 云订单旧记录清理资产保护

### 范围

本轮继续监工 Shop Django 后端仓库，重点处理 codex-cli 只读审查发现的三个风险：旧记录清理可能断开仍存在的服务器资产、迁移旧服务器自动删机未受同一套关机计划开关保护、通知计划未来队列上限未真正生效。

### 复查结论

- `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud` 当前运行域，未发现旧 `accounts/finance/mall/monitoring/dashboard_api/biz` 运行时 app 回流。
- 运行代码未发现 `CloudServerOrder.service_expires_at` 模型字段、危险 ORM 查询或写入恢复；`service_expires_at` 仍只作为兼容接口字段或日志标签存在。
- 未发现 `normalize_service_expiry`、`service_expired_at`、`CloudLifecyclePlan`、`CloudNoticePlan`、`CloudAutoRenewPlan`、`refund_to_balance`、`refund_balance`、`STATUS_REFUNDED` 或 `refunded` 旧状态回流。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；本轮新增旧记录清理条件没有引入订单表到期事实。

### 运行变化

- `cleanup_old_records` 清理云订单时，先排除仍有关联服务器资产且资产状态不是删除/终止流程的订单。
- `failed`、`cancelled`、`expired` 等终态云订单只有在没有运行中、停机中、过期宽限等仍需运维追踪的服务器资产时，才会进入清理候选。
- 已删除、删除中、终止中、已终止资产不阻止终态订单按原保留规则清理。
- 迁移旧机自动删机入口也使用同一套资产/云账号关机计划开关；资产或云账号关闭时不会执行真实删机。
- 通知计划未来队列现在会真正按调用方传入的上限截断，避免订单多时继续扫描并生成过多计划项。

### 验证

已通过：

```bash
uv run python -m py_compile core/management/commands/cleanup_old_records.py cloud/lifecycle_execution.py cloud/api_tasks.py cloud/tests.py
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_deleted_order_until_retained_ip_window_ends cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_non_terminal_cloud_orders cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_keeps_terminal_cloud_order_with_live_asset cloud.tests.CloudServerServicesTestCase.test_cleanup_old_records_allows_terminal_cloud_order_with_deleted_asset cloud.tests.CloudServerServicesTestCase.test_replaced_order_delete_respects_asset_shutdown_switch cloud.tests.CloudServerServicesTestCase.test_notice_task_future_items_respects_future_limit --verbosity 1
git diff --check
```

说明：`makemigrations --check --dry-run` 输出 `No changes detected`，本轮无模型结构变更。

剩余风险：本轮未跑完整测试套件，未连接真实 MySQL、AWS Lightsail 或阿里云 API，也未执行真实旧记录删除。

## 2026-06-02 实机开通删除回归修复

### 范围

第二十轮重构验证使用后台新增的 AWS 云账号执行真实创建、初始化、删机和固定 IP 释放。实机过程中发现开通保存成功后，结果日志在异步上下文里再次同步查询资产到期时间，导致订单被错误标记为失败。

### 运行变更

- `cloud/provisioning.py` 的开通结果日志改为读取同步保存阶段写入的 `_asset_expires_at` 缓存，不再在异步开通流程返回后隐式查询 `CloudAsset`。
- `_mark_success()` 和 `_mark_failed()` 返回订单前都会附带资产到期时间缓存，成功、失败日志统一使用这一份缓存值。
- 开通、重试初始化和失败日志里的代理链接、`secret`、SOCKS5 凭据统一脱敏，避免实机输出泄漏代理密钥。
- 新增聚焦回归测试，确保开通结果日志不会再次调用资产到期时间查询函数。

### 实机验证

- AWS Lightsail 新加坡区真实创建测试实例成功，订单号 `SRV20260602101856384117`，实例名 `20260602-************-*-o75`。
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
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api_site_configs.py bot/api.py shop/admin_urls.py bot/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.DashboardNotificationTestCase.test_daily_expiry_summary_test_endpoint_forces_send --noinput --verbosity 1
```

边界扫描结果：

```text
非测试文件测试混入数量: 0
测试文件非测试顶层类数量: 0
```

## 2026-06-02 cloud-asset-edit-api-split

### 范围

第二十二轮重构把云资产变更端点从大型资产列表 API 模块拆出，并收紧危险资产操作周边的状态/日志行为。

### 运行时变化

- 新增 `cloud/api_asset_edit.py`，承接云资产详情、手动编辑、自动续费开关和后台删除端点。
- 保留 `cloud/api.py` 作为兼容门面，重新导出旧 `cloud.api.*` 名称和既有导入/测试 patch 点。
- `shop/admin_urls.py` now imports cloud dashboard route handlers from domain modules directly instead of routing through `cloud.api`.
- `cloud/api_assets.py` now owns asset list, risk summary, snapshot refresh, and asset payload helpers only.
- 手动刷新未附加固定 IP 删除计划时，现在会更新同订单/同资源的相关记录，并记录 `CLOUD_UNATTACHED_IP_DELETE_DUE_REFRESHED`。
- 后台资产删除现在会删除同订单/同资源残留记录，清空订单云绑定，写入 `CloudIpLog`，并通过结构化 logger 字段记录被删除的残留 id。
- 更新旧 direct-view 测试，为写端点附带当前后台 bearer session。

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/api.py cloud/api_assets.py cloud/api_asset_edit.py shop/admin_urls.py cloud/tests.py
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_uses_bulk_order_inference_without_per_asset_fallback cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_status_uses_asset_scope cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_defers_snapshot_refresh cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_refreshes_unattached_ip_delete_plan cloud.tests.CloudServerServicesTestCase.test_delete_cloud_asset_only_removes_asset_record cloud.tests.CloudServerServicesTestCase.test_delete_cloud_asset_also_removes_residual_server_record cloud.tests_task_center.CloudTaskCenterApiTestCase --keepdb --noinput --verbosity 1
```

## 2026-06-02 cloud-asset-snapshot-api-split

### 范围

第二十三轮重构把云资产后台快照刷新/查询/分页逻辑从云资产列表端点模块拆出。

### 运行时变化

- 新增 `cloud/api_asset_snapshots.py`，承接 `CloudAssetDashboardSnapshot` 刷新、搜索、风险计数、排序、分页和分组页面构建。
- `cloud/api_assets.py` now focuses on asset list endpoints and asset payload construction.
- 移除过时的内存 payload 分页/风险过滤辅助函数；快照支撑的列表路径成为运行时路径后，这些函数已不再使用。
- `cloud/api.py` imports snapshot refresh compatibility exports from `cloud/api_asset_snapshots.py` directly.
- `cloud/api_assets.py` dropped snapshot table imports and no longer owns snapshot persistence logic.

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/api.py cloud/api_assets.py cloud/api_asset_snapshots.py cloud/api_asset_edit.py shop/admin_urls.py cloud/tests.py
git diff --check
```

## 2026-06-02 cloud-dashboard-api-domain-split

### 范围

Twenty-second refactor pass split the remaining cloud dashboard API monolith into asset, order, and task modules while preserving `cloud.api` as the URL compatibility facade.

### 运行时变化

- 新增 `cloud/api_assets.py`，承接代理/资产列表 payload、资产风险汇总、资产编辑、自动续费开关和后台快照刷新。
- 新增 `cloud/api_orders.py`，承接云订单列表/详情 payload、订单状态更新、订单详情保存和受保护订单删除。
- 新增 `cloud/api_tasks.py`，承接旧任务概览、通知计划详情/刷新、通知开关/文本 API、自动续费详情和手动自动续费执行。
- 将 `cloud/api.py` 从 4249 行缩减到 460 行；现在只保留兼容导入、单资产状态同步、服务器同步、云套餐同步和删除资产处理。
- 通过把被 patch 的符号路由回新模块，保留既有测试和运维使用的旧 `cloud.api.*` patch/import 点。
- 为云订单状态应用、订单详情更新、云资产删除和服务器同步开始/结束新增结构化日志。
- 显式初始化标志位，修复 `sync_servers()` 潜在的 `cancelled` 局部变量错误。

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/api.py cloud/api_assets.py cloud/api_orders.py cloud/api_tasks.py
uv run python manage.py check
git diff --check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_orders_list_exposes_auto_renew_enabled cloud.tests.CloudServerServicesTestCase.test_sync_servers_missing_state_does_not_bypass_provider_confirmation cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_uses_bulk_order_inference_without_per_asset_fallback cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_status_uses_asset_scope cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_uses_cloud_notice_plan_table cloud.tests.CloudServerServicesTestCase.test_auto_renew_task_detail_includes_due_retry_and_fallback_items cloud.tests_task_center.CloudTaskCenterApiTestCase --keepdb --noinput --verbosity 1
```

说明:

- 较旧的 direct `RequestFactory` POST 测试未附带后台 Bearer 凭据，在当前后台写鉴权策略下仍返回 401；这些测试未作为本次拆分的通过/失败门禁。

## 2026-06-02 cloud-api-sync-facade-split

### 范围

第二十三轮重构从 `cloud/api.py` 移除剩余真实同步/删除实现，使其只保留兼容门面职责。

### 运行时变化

- 新增 `cloud/api_sync.py`，承接后台服务器同步、单云资产状态同步、云套餐/价格同步和缺失状态确认辅助函数。
- 将 `delete_cloud_asset()` 迁入 `cloud/api_assets.py`，让资产删除和其余资产后台 API 放在一起。
- 将 `cloud/api.py` 从 460 行缩减到 148 行；现在只重新导出领域模块和旧私有 patch/import 点。
- 保留旧 patch 兼容：
  - `cloud.api._call_command_capture`
  - `cloud.api._apply_server_missing_state`
  - `cloud.api._refresh_dashboard_plan_snapshots_deferred`
  - `cloud.api.get_redis`
  - `cloud.api.build_trongrid_headers`
  - `cloud.api.httpx`
- 为云套餐/价格同步的开始、完成和失败新增结构化日志。

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/api.py cloud/api_assets.py cloud/api_sync.py cloud/api_monitors.py cloud/api_servers.py cloud/api_plans.py
uv run python manage.py check
git diff --check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_servers_missing_state_does_not_bypass_provider_confirmation cloud.tests.CloudServerServicesTestCase.test_sync_cloud_asset_status_uses_asset_scope cloud.tests.CloudServerServicesTestCase.test_rebuild_job_keeps_old_instance_until_migration_due cloud.tests.DashboardTronBalanceQueryTestCase.test_fetch_address_chain_balances_uses_resolved_headers cloud.tests_task_center.CloudTaskCenterApiTestCase --keepdb --noinput --verbosity 1
```

## 2026-06-01 cloud-sync-worker-and-status-tracking

### 范围

最新一轮重构让后台触发的代理同步具备持久化队列，并且状态显式可观测。

### 运行时变化

- `/admin/cloud-assets/sync/` now only creates a `CloudAssetSyncJob` queue record and returns immediately.
- 新增 `process_cloud_asset_sync_jobs`，作为基于数据库的持久化 worker 处理排队同步任务。
- `run.py worker` starts the sync worker, and `run.py all` now starts web, bot, and the sync worker together.
- 新增同步任务列表和重试 API：
  - `/admin/cloud-assets/sync-jobs/`
  - `/admin/cloud-assets/sync-jobs/<id>/retry/`
  - `/admin/cloud-assets/sync-jobs/<id>/cancel/`
- 同步任务状态现在是持久化状态面：
  - `queued`
  - `running`
  - `succeeded`
  - `partial`
  - `failed`
  - `cancelled`
- 新增 `cloud_asset_sync_job_event`，记录详细同步事件时间线：
  - queued / claimed / status / task / progress / log / warning / error / cancel / retry / heartbeat
  - the event table stores `job_id` as an indexed scalar instead of a foreign key so detailed logging cannot lock or block the main job status row
- worker 和同步执行过程会持续更新 `worker_id`、`worker_heartbeat_at`、`progress_current`、`progress_total`、`current_task`、`errors`、`warnings`、`logs`、`started_at`、`finished_at` 和取消请求字段。
- 后台快照刷新现在按范围执行：
  - full cloud sync refreshes the complete `cloud_asset_dashboard_snapshot`
  - selected asset sync and single-asset updates refresh only the affected asset IDs
- 管理前端新增同步任务抽屉，展示状态、进度、worker 心跳、结果、详细事件、日志、取消、重试、状态过滤和仅失败过滤；入队后轮询只更新可见任务行，不阻塞整个代理列表。
- `lefthook.yml` no longer hardcodes `/opt/homebrew/bin/pnpm`, so Git hooks can use the current shell `pnpm`.

### 验证

本地已通过:

```bash
uv run python -m py_compile run.py cloud/api.py cloud/dashboard_snapshots.py cloud/models.py cloud/tests.py cloud/management/commands/process_cloud_asset_sync_jobs.py shop/admin_urls.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check
uv run python manage.py sqlmigrate cloud 0042
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_cloud_assets_runs_enabled_accounts_and_merges_results cloud.tests.CloudServerServicesTestCase.test_cancel_queued_cloud_asset_sync_job_marks_terminal_and_events cloud.tests.CloudServerServicesTestCase.test_process_cloud_asset_sync_jobs_worker_processes_queued_job --keepdb --noinput --verbosity 1
(cd /Users/a399/Desktop/data/vue-shop-admin && ./node_modules/.bin/vue-tsc --noEmit --skipLibCheck -p apps/web-antd/tsconfig.json)
git diff --check
```

## 2026-06-01 cloud-asset-list-and-sync-performance

### 范围

第二十二轮重构优化缓慢的代理资产列表和后台触发云同步路径。

### 运行时变化

- 新增 `cloud_asset_dashboard_snapshot`，作为云资产后台列表的物化表。
- `cloud_assets_list()` and `cloud_assets_risk_summary()` now read snapshot rows for search, risk filters, grouping, counts, and database pagination instead of rebuilding every row on each request.
- 新增 `refresh_cloud_asset_dashboard_snapshots` 管理命令，并在同步/服务变更后接入后台快照刷新。
- 新增 `cloud_asset_sync_job`，用于排队后台同步请求、跟踪进度/结果/日志尾部，并暴露 `/admin/cloud-assets/sync-jobs/<id>/`。
- `/admin/cloud-assets/sync/` now returns immediately with a queued job; the background thread executes account/asset scoped sync tasks and records the final result.
- AWS 和阿里云同步命令不再维护已退役的 `Server` 兼容镜像；`cloud_asset` 仍是唯一云资源事实表。
- 管理前端在非分组代理列表模式下改用真正的服务端分页，并轮询云同步任务直到终态。
- “显示已删除”开关会发送到后端，让分页总数匹配可见列表。
- 数据库命名和数据流文档现在列出新的快照/任务表。

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/api.py cloud/dashboard_snapshots.py cloud/models.py cloud/tests.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/refresh_cloud_asset_dashboard_snapshots.py shop/admin_urls.py
uv run python manage.py check
uv run python manage.py makemigrations cloud --dry-run --check
cd /Users/a399/Desktop/data/vue-shop-admin && ./node_modules/.bin/vue-tsc --noEmit --skipLibCheck -p apps/web-antd/tsconfig.json
```

本地受阻:

```bash
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_asset_dashboard_snapshot_refresh_materializes_paginated_list ... --keepdb --noinput
```

聚焦 DB 测试仍受本地 MySQL 测试库权限阻塞：

```text
Access denied for user 'a'@'localhost' to database 'test_a'
```

## 2026-06-01 proxy-list-and-sync-performance

### 范围

第二十七轮重构优化后台代理列表加载和选中资产云同步。

### 运行时变化

- 新增 `core.cloud_accounts.list_cloud_account_labels()`，让后台 payload 渲染每次请求只加载一次活跃云账号标签，而不是每个资产加载一次。
- 为代理列表 payload 新增 `CloudAssetPayloadContext`：
  - bulk infers missing `CloudServerOrder` links by IP/name/resource identifiers;
  - disables per-row order fallback queries in list/risk-summary reads;
  - avoids `sync_cloud_asset_user_binding()` writes during list rendering;
  - computes missing unattached-IP expiry for display without saving during a GET.
- `cloud_assets_list` and `cloud_assets_risk_summary` now build payloads through the shared context.
- `sync_cloud_assets` now treats selected `asset_ids` as real asset-scoped sync tasks instead of widening to full account sync. Multi-select creates scoped tasks with `asset_id`, `instance_id`, `public_ip`, account, and region.
- 同步任务锁包含限定范围的资产/资源 key，因此同账号/区域的两个选中资产不会互相跳过。
- 从后台同步中移除运行时 reconcile 命令调用，因为 `CloudAsset(kind='server')` 已是标准事实。
- 后台同步快照刷新现在使用延迟刷新路径。
- AWS/阿里云同步命令的可见数量汇总改用低成本活跃资产计数，不再做完整后台去重扫描。
- 前端代理列表加载现在使用列表端点返回的 `risk_counts`，避免重复并发请求风险汇总。

### 验证

本地已通过:

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

### 范围

Twenty-second refactor pass removed the remaining runtime `print` from AWS Lightsail provisioning and routed the result through structured logging.

### 运行时变化

- 将 `cloud/aws_lightsail.py` 中的 `print('[AWS_CREATE_RESULT]', ...)` stdout 输出替换为结构化 `logger.info(...)` 事件。
- 创建日志现在以日志字段携带 `order_no`、`server_name`、`region`、`bundle_id`、`blueprint_id`、`public_ip` 和 `static_ip_name`。

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/aws_lightsail.py
uv run python manage.py check
```

## 2026-06-01 cache-redis-fallback-observability

### 范围

第二十四轮重构在不改变本地兜底行为的前提下，让 Redis 日统计兜底路径可观测。

### 运行时变化

- `core/cache.py` now logs debug entries when Redis daily-stat increment, read, or close operations fail.
- Redis 不可用时，进程内兜底计数器仍按原行为运行。

### 验证

本地已通过:

```bash
uv run python -m py_compile core/cache.py
uv run python manage.py check
git diff --check
```

## 2026-06-01 aws-missing-confirmation-duplicate-guard

### 范围

Twenty-fifth refactor pass fixed duplicate confirmation increments when the AWS missing-resource sync sees both a canonical `CloudAsset` row and a legacy `Server` compatibility row for the same cloud resource.

### 运行时变化

- AWS 缺失确认现在会把结构化 `sync_state` 从主记录复制到相关兼容记录，而不是让两边独立递增。
- `_mark_deleted_when_missing_in_aws()` tracks rows already handled in the current sync pass and skips duplicate compatibility rows.
- 本地聚焦测试可在这条激进重构分支上使用 `DJANGO_TEST_REUSE_DB=1` 运行，从而复用当前 MySQL 数据库，而不是尝试创建 `test_a`。

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_missing_confirmation_note_preserves_existing_note cloud.tests.CloudServerServicesTestCase.test_sync_missing_confirmation_requires_interval cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_expose_missing_confirmation_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_unattached_ip_show_confirmation_progress_in_state_and_note cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_instance_requires_five_passes_before_delete cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_missing_instance_requires_five_passes_before_delete --keepdb --noinput --verbosity 1
```

## 2026-06-01 server-compat-runtime-shrink

### 范围

Twenty-sixth refactor pass removed more runtime dependency on the `cloud.server_records.Server` compatibility wrapper.

### 运行时变化

- `core.cloud_accounts.list_cloud_accounts_by_server_load()` now counts `CloudAsset(kind='server')` directly.
- `upsert_cloud_asset` no longer writes a duplicate compatibility `Server` row after creating/updating the canonical asset.
- `dedupe_servers` now de-duplicates canonical server assets in `cloud_asset`.
- `reconcile_cloud_assets_from_servers` is now an explicit no-op compatibility command because `cloud_server` has already been removed.
- 剩余运行时兼容包装导入仅限 AWS/阿里云同步命令；历史 migrations 和测试仍会有意引用旧 label。

### 验证

本地已通过:

```bash
uv run python -m py_compile core/cloud_accounts.py cloud/management/commands/upsert_cloud_asset.py cloud/management/commands/dedupe_servers.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
uv run python manage.py check
uv run python manage.py reconcile_cloud_assets_from_servers
uv run python manage.py dedupe_servers
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test core.tests.CloudAccountSelectionTestCase --keepdb --noinput --verbosity 1
```

## 2026-06-01 dashboard-api-helper-extraction

### 范围

Twenty-third refactor pass removed the dashboard API submodules' reverse dependency on the `bot.api` aggregation module.

### 运行时变化

- 新增 `core/dashboard_totp.py`，承接后台 TOTP 密钥规范化、生成、otpauth URL 构建和 token 校验。
- 新增 `bot/user_stats.py`，承接活跃云资产和单用户代理数量查询。
- 将通用后台 payload 辅助函数（`_json_payload`、`_payload_bool`、`_parse_runtime_time_point`）迁入 `core/dashboard_api.py`。
- `bot/api_auth.py`, `bot/api_admin_users.py`, `bot/api_cloud_accounts.py`, `bot/api_operation_logs.py`, `bot/api_products.py`, `bot/api_site_configs.py`, `bot/api_telegram.py`, and `bot/api_users.py` no longer import from `bot.api`.
- `bot/api.py` now consumes the extracted helpers and remains a route/export aggregation point.

### 验证

本地已通过:

```bash
uv run python -m py_compile core/dashboard_api.py core/dashboard_totp.py bot/user_stats.py bot/api.py bot/api_auth.py bot/api_admin_users.py bot/api_cloud_accounts.py bot/api_operation_logs.py bot/api_products.py bot/api_site_configs.py bot/api_telegram.py bot/api_users.py
uv run python manage.py check
git diff --check
```

## 2026-06-01 cloud-asset-query-helper

### 范围

第二十一轮重构把共享云资产列表可见性和去重逻辑从后台 API 模块中移出。

### 运行时变化

- 新增 `cloud/asset_queries.py`，承接标准 `CloudAsset` 可见列表和去重辅助函数。
- `cloud/api.py` now consumes the shared asset query helpers instead of owning them.
- AWS 同步、阿里云同步和资产 reconcile 命令不再为了统计可见资产而导入 `cloud.api`。
- AWS 和阿里云同步命令现在从 `core.dashboard_api` 导入 `_provider_status_label`，不再从 `bot.api` 导入。

### 验证

本地已通过:

```bash
uv run python -m py_compile cloud/asset_queries.py cloud/api.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
uv run python manage.py check
git diff --check
```

## 2026-06-01 db-naming-convention-alignment

### 范围

第十七轮重构修正数据库命名文档，使其匹配真实运行时 schema。

### 运行时变化

- 将 `docs/DB_NAMING_CONVENTIONS.md` 从之前理想化的复数表约定更新为真实项目约定：
  - `core_*`
  - `bot_*`
  - `order_*`
  - `cloud_*`
- 记录当前 `core`、`bot`、`orders` 和 `cloud` 的 `db_table` 清单。
- 明确新增运行时表应使用 `域前缀_单数语义名`。
- 明确标注 `cloud_assets`、`cloud_server_orders`、`balance_ledgers` 等复数替代名不是默认约定，除非属于计划中的迁移。
- 重新确认 `cloud_asset` 是云资源事实表。

### 验证

仅文档变更。源表清单通过以下命令检查:

```bash
rg -n "db_table\\s*=|class Meta:" core bot orders cloud -g'*.py'
```

## 2026-06-01 encrypted-config-invalid-token-handling

### 范围

第十八轮重构收紧加密配置处理，避免损坏的 Fernet 形态密文被静默当作明文处理。

### 运行时变化

- `core/crypto.py:decrypt_text()` still returns legacy plaintext values unchanged when they do not look encrypted.
- 以 Fernet token 前缀 `gAAAA` 开头的值在解密失败时，现在会记录 `CONFIG_DECRYPT_INVALID_TOKEN` 并返回空字符串。
- 新增聚焦测试覆盖：
  - legacy plaintext fallback
  - invalid Fernet-like token handling after an encryption key mismatch
- 修复 `core/tests.py`，改为从 `cloud.server_records` 导入 `Server` 兼容模型，匹配当前云资产架构。

### 验证

本地已通过:

```bash
uv run python -m py_compile core/crypto.py core/tests.py core/models.py bot/models.py bot/api_site_configs.py
uv run python manage.py test core.tests.CryptoDecryptTestCase --keepdb
uv run python manage.py check
```

## 2026-06-01 site-config-cache-invalidation

### 范围

Nineteenth refactor pass reduced configuration cache split-brain between `SiteConfig` local cache and `core.cache` async config cache.

### 运行时变化

- 新增显式 `core.cache` 辅助函数：
  - `get_cached_config_value()`
  - `cache_config_value()`
  - `invalidate_config_cache()`
- `SiteConfig.clear_cache()` now invalidates the async config cache as well as the model-local 30-second cache.
- 将 bot 文本/配置路径中对 `_cached_config` 的直接写读替换为辅助函数。
- 新增聚焦回归测试，覆盖 `SiteConfig.set()` 使异步配置缓存失效的行为。

### 验证

本地已通过:

```bash
uv run python -m py_compile core/cache.py core/models.py core/texts.py core/tests.py bot/api_site_configs.py bot/handlers.py
uv run python manage.py check
```

本地受阻:

```bash
uv run python manage.py test core.tests.SiteConfigCacheTestCase --keepdb
```

聚焦 DB 测试受本地 MySQL 测试库权限阻塞:

```text
Access denied for user 'a'@'localhost' to database 'test_a'
```

## 2026-06-02 机器人返回链修复

### 范围

本轮巡检查了一次机器人代理列表、代理详情、续费支付、更换 IP、修改配置和未附加固定 IP 续费相关返回链。

### 运行时变化

- 新增 `cloud_previous_detail_callback()`，用于区分“回订单详情”和“回资产详情”。
- 代理资产详情回调继续兼容旧 `cloud:assetdetail:`，并支持新的短回调 `cloud:ad:`；查询页资产操作支持短回调 `cloud:aa:`，避免资产详情嵌套到支付、更换 IP、修改配置按钮后超过 Telegram 64 字节限制。
- 从代理资产详情进入续费支付、更换 IP、修改配置、重新安装确认和固定 IP 续费套餐页时，下一层的返回按钮会回到原资产详情。
- 从订单详情进入同样流程时，仍回订单详情，再由订单详情返回原列表或查询页。
- 修正资产入口修改配置提交后的“返回原代理”，避免跳到订单详情。

### 监工结果

- 本轮使用本地命令复查机器人返回链、云资产到期事实源、旧计划模型、旧退款入口和废弃 app 回流。
- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段，`CloudServerOrder` 未恢复服务到期字段。
- 未发现旧订单到期字段、旧计划快照模型、旧退款函数名、`refunded` 状态或废弃 app 目录回流。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity 1
git diff --check
rg -n "service_expires_at|service_expired_at|normalize_service_expiry|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
```

## 2026-06-02 订单详情短返回回调收口

### 范围

本轮继续巡检机器人订单返回链、云资产生命周期事实源、旧计划快照、旧退款入口和废弃 app 回流。

### 运行时变化

- 订单列表进入订单详情时，返回来源通过 `append_back_callback()` 压缩，避免 `profile:orders:cloud:filter:*:page:*` 嵌套后超过 Telegram 64 字节限制。
- 订单详情处理器对嵌套来源再次执行 `compact_callback_path()`，让短回调 `poc:<筛选>:<页码>` 能稳定返回原云订单列表。
- 资产详情回调兼容 `cloud:assetdetail:<id>`、`cloud:assetdetail:asset:<id>` 和 `cloud:ad:asset:<id>`，避免旧按钮或外部入口传入显式类型时解析失败。

### 监工结果

- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at` 服务到期字段，仅保留 `renew_grace_expires_at` 等流程时间字段。
- 非迁移运行时代码未发现旧计划快照模型、旧退款函数名、旧退款状态或废弃 app 目录回流。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py bot/handlers.py bot/keyboards.py cloud/services.py cloud/lifecycle.py cloud/lifecycle_tasks.py cloud/lifecycle_execution.py cloud/api.py cloud/api_assets.py cloud/api_orders.py cloud/api_asset_edit.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_due_orders_use_asset_expiry_for_lightsail_lifecycle cloud.tests.CloudServerServicesTestCase.test_dashboard_order_expiry_update_syncs_asset_expiry_and_lifecycle_plan cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_skips_when_asset_expiry_moved_out_of_due_window cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_read_cached_table_after_initial_refresh cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_order_delete_at_before_cloud_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_order_ip_recycle_at_before_release cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_orphan_asset_delete_time_before_cloud_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_unattached_ip_delete_time_before_release cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_releases_retained_static_ip_after_recycle_due cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_releases_overdue_unattached_static_ip cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_use_actual_expiry_as_delete_plan cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_excludes_cloud_missing_orphan_server --noinput
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
git diff --check
```

迁移检查仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的历史一致性警告，但结果为无模型变更。

### 剩余风险

- 未跑完整测试套件。
- 未连接真实 MySQL、AWS Lightsail、阿里云、TRONGrid 或 Telegram。
- 未执行真实 Telegram 回调、钱包扣款、自动续费支付、云端删机、固定 IP 释放或历史数据清理。

## 2026-06-02 资产详情短返回回调收口

### 范围

本轮继续用 `codex-cli` 监工机器人返回链。审查发现资产详情作为返回来源时，续费支付、重装、修改配置和更换 IP 地区按钮仍可能超过 Telegram `callback_data` 64 字节限制。

### 运行时变化

- 资产详情新增短回调 `cad:<资产ID>:<返回>`，云服务器资产详情新增短回调 `csd:<资产ID>:<返回>`，并继续兼容旧 `cloud:assetdetail:` 和 `cloud:ad:`。
- 代理列表分页新增短回调 `clp:<页码>`，并注册到原代理列表分页处理器。
- 续费钱包支付新增短回调 `cloud:rp:<订单ID>:<币种>:<返回>`，并继续兼容旧 `cloud:renewpay:`。
- `compact_callback_path()` 会把旧资产详情、旧代理列表分页和旧云订单筛选分页压缩到短格式，避免二级按钮继续嵌套长路径。

### 监工结果

- `codex-cli` 复现了长资产详情返回链下的超限样例：续费支付 72 字节、重装 65 字节、修改配置 66 字节、更换 IP 地区 82 字节。
- 修复后用 7 位订单 ID、7 位资产 ID、5 位页码复查：续费、换 IP、重装、修改配置、支付和地区选择按钮均不超过 64 字节。
- 自动化“Shop 自动优化监工”已加入真机测试方向：服务器创建、删除、IP 变更、附加 IP 变更、无订单资产续费、生命周期变化和删除计划执行情况。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-bot-callback-test.sqlite3 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity 1 --noinput
UV_CACHE_DIR=/private/tmp/uv-cache-shop DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-callback-length.sqlite3 uv run python manage.py shell -c "from bot.keyboards import cloud_asset_detail_callback, cloud_server_detail, cloud_server_change_ip_region_menu, cloud_server_renew_payment; from decimal import Decimal; b=cloud_asset_detail_callback(9999999,'cloud:list:page:12345'); samples=[btn.callback_data for row in cloud_server_detail(9999999, True, True, True, b, True).inline_keyboard for btn in row if btn.callback_data]; samples += [btn.callback_data for row in cloud_server_renew_payment(9999999, Decimal('12.3'), Decimal('45.6'), False, b).inline_keyboard for btn in row if btn.callback_data]; samples += [btn.callback_data for row in cloud_server_change_ip_region_menu(9999999, [('ap-southeast-1','新加坡')], back_callback=b).inline_keyboard for btn in row if btn.callback_data]; assert all(len(v.encode()) <= 64 for v in samples)"
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
git diff --check
rg -n "service_expires_at|service_expired_at|normalize_service_expiry|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
```

### 剩余风险

- 本轮仍是本地机器人回调和 SQLite 聚焦测试，尚未执行真实 Telegram 点击。
- 真机云资源创建、删除、IP 变更、附加 IP 变更和删除计划执行将单独写实测报告，避免和普通单元测试混在一起。

## 2026-06-03 资产详情直接操作按钮返回链收口

### 范围

本轮继续巡检 Shop Django 后端的机器人返回链、Telegram `callback_data` 64 字节限制、云资产生命周期唯一到期事实、废弃 app 回流、旧计划快照和旧退款入口。

### 运行时变化

- 资产详情页里的“重新安装”和管理员“修改时间”按钮不再直接拼接 `back_callback`，统一改用 `append_back_callback()`。
- 该入口会先通过 `compact_callback_path()` 压缩嵌套来源，再生成下一层回调，避免从长资产详情路径进入重装或修改时间时接近 Telegram 64 字节限制。
- 新增聚焦测试锁定资产详情直接操作按钮使用压缩辅助函数，并校验 7 位资产 ID、5 位页码场景下仍不超过 64 字节。

### 监工结果

- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 等流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复派生到期字段。
- 旧计划快照、旧退款函数名和废弃 app 未在运行时代码中回流；扫描命中的 `ip_recycle_at=asset.actual_expires_at` 是固定 IP 回收计划派生时间，不是订单服务到期字段恢复。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python -m py_compile bot/handlers.py bot/tests.py bot/keyboards.py cloud/services.py cloud/provisioning.py cloud/api_orders.py cloud/api_assets.py cloud/api_asset_edit.py cloud/lifecycle.py cloud/lifecycle_execution.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite DB_NAME=/private/tmp/shop_bot_callbacks_after_patch.sqlite3 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite DB_NAME=/private/tmp/shop_lifecycle_focus.sqlite3 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_separate_order_plan_note cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_read_cached_table_after_initial_refresh cloud.tests.CloudServerServicesTestCase.test_lifecycle_plan_counts_match_proxy_list_assets cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite DB_NAME=/private/tmp/shop_asset_renewal_after_patch_<时间戳>.sqlite3 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_lists_plans_without_creating_order cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery cloud.tests.CloudServerServicesTestCase.test_completed_asset_recovery_order_renews_without_reprovisioning cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_manual_asset_note cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print([f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name])"
git diff --check
```

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 后台任务中心与续费回链复核

### 范围

本轮继续监工 Shop Django 后端，重点复核 `CloudAsset.actual_expires_at` 唯一到期事实、订单表和快照表是否恢复旧到期字段、后台任务中心失败统计、机器人资产详情/订单详情/续费/换 IP/重装/修改配置返回链、Telegram `callback_data` 64 字节限制、废弃 app 回流、旧退款入口和旧计划快照。

### 监工结果

- 当前工作树起始干净，最近提交为 `f70355e 修复续费钱包支付返回链`。
- 未发现需要修改运行代码的新问题；本轮仅追加中文版本记录。
- `CloudAsset` 继续以 `actual_expires_at` 作为资产到期事实；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz` 未作为运行 app 回流；命中的 `dashboard_api` 仍是当前 URL namespace 和 `core.dashboard_api` helper。
- 收窄扫描未发现旧退款函数名、旧退款逻辑、旧计划快照表或订单服务到期字段回流。
- 机器人回链聚焦测试覆盖资产详情、订单详情、续费、换 IP、重装、修改配置、自动续费、IP 查询结果和续费结果返回按钮，仍满足 Telegram `callback_data` 64 字节限制。
- 后台任务中心聚焦测试覆盖同步任务、生命周期、通知计划和自动续费失败统计；未发现后台总览漏报或失败状态不一致的新问题。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache uv run python -m py_compile bot/api.py bot/handlers.py cloud/services.py cloud/bootstrap.py cloud/api.py cloud/task_center.py cloud/api_tasks.py cloud/sync_jobs.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
UV_CACHE_DIR=/private/tmp/uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center.CloudTaskCenterApiTestCase -v 2
UV_CACHE_DIR=/private/tmp/uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase -v 2
UV_CACHE_DIR=/private/tmp/uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_expired_address_payments_are_not_candidates_and_renewal_status_restores orders.tests.ChainPaymentScannerTestCase.test_expired_asset_renewal_payment_unbinds_asset_for_retry orders.tests.ChainPaymentScannerTestCase.test_public_asset_renewal_expiry_does_not_claim_unowned_asset -v 2
UV_CACHE_DIR=/private/tmp/uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields -v 2
UV_CACHE_DIR=/private/tmp/uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py makemigrations --check --dry-run
```

第一次未指定 SQLite 的聚焦测试仍会尝试连接本机 MySQL 并被沙箱拒绝；已改用 `DJANGO_TEST_SQLITE=1` 重跑通过。SQLite 测试仍会打印不支持 `db_comment` 的预期 warning；bot SimpleTestCase 仍会打印配置读取被禁止数据库访问拦截的既有容错日志，最终测试通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 续费钱包支付恢复返回链修复

### 范围

本轮继续监工 Shop Django 后端，重点复核云资产生命周期唯一到期事实、后台任务中心状态统计、机器人续费钱包支付结果页返回链、Telegram `callback_data` 64 字节限制、旧计划快照、旧退款入口和废弃 app 回流。

### 修改

- 发现续费钱包支付在固定 IP 恢复、已支付巡检和恢复资料不完整分支中只更新文字，不带返回键盘；用户从资产详情、订单详情或 IP 查询结果进入续费支付后，异常/恢复结果页会丢失上一层入口。
- 新增 `_cloud_renewal_result_keyboard`，复用 `cloud_previous_detail_callback` 和现有 callback 压缩逻辑；有返回上下文时回“原代理”，无返回上下文时保持主菜单行为。
- `cloud:renewwallet:*` 和 `cloud:rp:*`/`cloud:renewpay:*` 两个钱包支付处理器的恢复中、已支付巡检、恢复资料不完整、恢复创建成功分支均接入结果键盘。
- 补充 `bot.tests.RetainedIpRenewalUiTestCase` 聚焦测试，覆盖极端 18 位 ID + 嵌套资产详情来源下续费结果返回 callback 不超过 64 字节，并检查两个处理器的结果分支都传入返回键盘。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python -m py_compile bot/handlers.py bot/tests.py bot/keyboards.py cloud/task_center.py cloud/tests_task_center.py cloud/api_tasks.py
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py check
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase cloud.tests_task_center --verbosity 2
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py shell -c "from django.apps import apps; from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('retired_apps', [a for a in ['accounts','finance','mall','monitoring','dashboard_api','biz'] if apps.is_installed(a)]); print('cloudasset_expiry_fields', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('order_has_actual_expires_at', hasattr(CloudServerOrder, 'actual_expires_at')); print('order_has_service_expires_at', hasattr(CloudServerOrder, 'service_expires_at')); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name])"
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py shell -c "from decimal import Decimal; from bot.handlers import _cloud_renewal_result_keyboard; from bot.keyboards import cloud_server_detail, cloud_server_renew_payment, cloud_server_change_ip_region_menu, cloud_ip_query_result; item=999999999999999999; back=f'cloud:ad:asset:{item}:cloud:list:page:{item}'; regions=[('ap-southeast-1','SG'),('ap-northeast-1','JP'),('us-east-1','US'),('eu-west-2','UK'),('ap-south-1','IN'),('ca-central-1','CA')]; markups=[cloud_server_detail(item, True, True, True, back, True), cloud_server_renew_payment(item, Decimal('12.3'), Decimal('45.6'), back_callback=back), cloud_server_change_ip_region_menu(item, regions, back_callback=back), _cloud_renewal_result_keyboard(item, back), cloud_ip_query_result([], [{'ip':'1.2.3.4','order_id':item,'asset_id':0,'can_change_ip':True,'can_reinit':True,'can_config':True,'can_auto_renew':True,'auto_renew_enabled':False}], include_start=True, include_reinit=True)]; callbacks=[b.callback_data for m in markups for row in m.inline_keyboard for b in row if b.callback_data]; over=[c for c in callbacks if len(c.encode())>64]; print('callback_count', len(callbacks)); print('max_callback_bytes', max(len(c.encode()) for c in callbacks)); print('over_limit', over[:5])"
git diff --check
```

结果：`manage.py check` 通过；SQLite 下 `bot.tests.RetainedIpRenewalUiTestCase` 和 `cloud.tests_task_center` 共 61 条通过；字段 introspection 确认废弃 app 未安装、`CloudAsset` 到期字段仅 `actual_expires_at`、`CloudServerOrder` 未恢复 `actual_expires_at/service_expires_at`、`CloudAssetDashboardSnapshot` 无到期字段；极端 callback 枚举 24 个，最大 62 字节，无超过 64 字节限制；`makemigrations --check --dry-run` 为 `No changes detected`；`git diff --check` 通过。

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。第一次 callback 枚举未指定 SQLite，触发本地 MySQL 沙箱连接日志；已用 `DJANGO_TEST_SQLITE=1` 重跑通过。SQLite 测试仍会打印不支持 `db_comment` 的预期 warning；bot SimpleTestCase 配置读取容错日志和 mocked postcheck 异常日志仍为既有测试输出，最终 OK。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 重装提交返回链修复

### 范围

本轮继续巡检 Shop Django 后端，重点检查机器人资产详情、订单详情、IP 查询结果进入重装流程后的返回链，以及 Telegram `callback_data` 64 字节限制；同时复核云资产生命周期唯一到期事实、后台任务中心统计、旧计划快照、旧退款入口和废弃 app 回流。

### 运行时变化

- 修复订单重装确认提交成功后固定返回主菜单的问题：确认处理器现在读取 FSM 中保存的 `reinstall_back`，提交结果页提供“返回原代理”。
- 修复资产重装确认提交成功后固定返回主菜单的问题：资产确认处理器同样复用 `reinstall_back`，提交结果页返回资产详情。
- 新增 `_reinstall_submitted_keyboard()` 与 `_asset_reinstall_submitted_keyboard()`，无返回上下文时仍保持原主菜单行为；有返回上下文时压缩后再生成详情回调，避免超过 Telegram 64 字节限制。
- 新增聚焦测试覆盖重装提交结果键盘、极端 18 位 ID 嵌套来源和确认处理器复用 `reinstall_back` 的源码路径。

### 监工结果

- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- `CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`。
- `CloudAssetDashboardSnapshot` 未恢复 `actual_expires_at`，仅保留风险标记字段。
- `INSTALLED_APPS` 未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- 收窄扫描未发现旧计划快照表、旧退款函数名、旧端口入口或订单服务到期字段回流；命中的 `ip_recycle_at=asset.actual_expires_at` 仍是固定 IP 回收计划派生时间。
- 重装提交结果 callback 枚举 16 个极端样本，最大 58 字节，无超过 64 字节。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python -m py_compile bot/handlers.py bot/tests.py bot/keyboards.py cloud/task_center.py cloud/tests_task_center.py cloud/api_tasks.py
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py check
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity 2
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --verbosity 2
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py shell -c "from django.apps import apps; from django.conf import settings; checks=[('cloud','CloudAsset','actual_expires_at'),('cloud','CloudServerOrder','actual_expires_at'),('cloud','CloudServerOrder','service_expires_at'),('cloud','CloudAssetDashboardSnapshot','actual_expires_at'),('cloud','CloudAssetDashboardSnapshot','risk_expired')]; print('installed_retired_apps=', [a for a in ['accounts','finance','mall','monitoring','dashboard_api','biz'] if a in settings.INSTALLED_APPS]); [print(f'{model}.{field}=', field in {f.name for f in apps.get_model(app, model)._meta.get_fields()}) for app, model, field in checks]"
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py shell -c "from bot.handlers import _reinstall_submitted_keyboard,_asset_reinstall_submitted_keyboard; ids=[88,999999999999999999]; backs=['cloud:querymenu','cloud:list:page:3','cloud:ad:asset:999999999999999999:cloud:list:page:999999999999999999','profile:orders:cloud:filter:provisioning:page:999999999999999999']; callbacks=[]; [callbacks.extend([button.callback_data for row in _reinstall_submitted_keyboard(i,b).inline_keyboard for button in row if button.callback_data]) for i in ids for b in backs]; [callbacks.extend([button.callback_data for row in _asset_reinstall_submitted_keyboard(i,b).inline_keyboard for button in row if button.callback_data]) for i in ids for b in backs]; oversized=[c for c in callbacks if len(c.encode())>64]; print('callback_count=', len(callbacks)); print('max_callback_bytes=', max(len(c.encode()) for c in callbacks)); print('oversized=', oversized)"
rg -n "class .*PlanSnapshot|PlanSnapshot|service_expires_at\\s*=\\s*models\\.|CloudServerOrder.*actual_expires_at|CloudAssetDashboardSnapshot.*actual_expires_at|def .*refund|refund_order|process_refund|create_refund|cloud:ipport|custom:port|waiting_port" --glob '!**/migrations/**' --glob '!CHANGELOG.md' --glob '!docs/**'
git diff --check
```

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终为 `No changes detected`。SQLite 测试仍打印不支持 `db_comment` 的预期 warning，bot SimpleTestCase 配置读取容错日志和 mocked postcheck 异常日志仍为既有测试输出，最终通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 管理员开机返回链修复

### 范围

本轮继续监工 Shop Django 后端，重点检查机器人返回链、Telegram `callback_data` 64 字节限制、云资产生命周期唯一到期事实、后台任务中心统计、废弃 app 回流、旧计划快照和旧退款入口。

### 监工结果

- 当前工作树起始干净，最近提交为 `c96a4a7 记录生命周期返回链巡检`。
- 发现管理员在 IP 查询结果里点击“开机”时，按钮只生成 `cloud:start:<订单ID>`，处理结果页也没有返回按钮；从查询菜单进入后会断开上一层返回链。
- 修复 `bot/keyboards.py`：IP 查询结果中的订单开机和资产关联订单开机按钮统一携带 `cloud:querymenu` 返回路径，并复用现有 `append_back_callback` 压缩逻辑。
- 修复 `bot/handlers.py`：管理员开机处理器解析 `cloud:start:<订单ID>:<返回路径>`，成功和失败结果页都展示“返回原页面”按钮；没有返回路径时默认回到 `cloud:querymenu`。
- 补充 `bot/tests.py`：覆盖管理员查询键盘中的开机 callback、64 字节限制，以及开机处理器保留返回路径的源码断言。
- 结构化字段确认：`CloudAsset` 仍只有 `actual_expires_at` 作为资产到期事实；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 无到期字段。
- `INSTALLED_APPS` 未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`；仓库根部也未发现这些废弃 app 目录回流。
- 收窄扫描未发现旧计划快照、旧退款函数名或订单服务到期字段恢复；命中的 `ip_recycle_at=asset.actual_expires_at` 仍是未附加固定 IP 回收派生时间，不是订单到期事实恢复。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py check
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python -m py_compile bot/keyboards.py bot/handlers.py bot/tests.py cloud/task_center.py cloud/tests_task_center.py cloud/api_tasks.py
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_order_balance_start_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.BotOrderAndBalanceFilterTestCase --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_callbacks_start_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_start_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_introspect_start_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('asset_expiry_fields', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('default_port', CloudServerOrder._meta.get_field('mtproxy_port').default)"
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\\s*=|actual_expires_at\\s*=.*order\\.|order\\..*actual_expires_at|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

另外用 `DJANGO_SETTINGS_MODULE=shop.settings` 动态枚举极端 18 位 ID、嵌套来源和管理员开机查询入口的 callback。第一次脚本把 `cloud_server_renew_payment` 的返回路径误传为位置参数，修正为关键字参数后重跑通过，枚举 83 个 callback，最大 64 字节，无超限。

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。SQLite 测试仍会打印不支持 `db_comment` 的预期系统警告；bot SimpleTestCase 配置读取容错日志和 mocked postcheck 异常日志仍为既有测试输出，最终通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 12:02 生命周期唯一到期事实与返回链巡检

### 范围

本轮从 `c0011b7` 继续监工 Shop Django 后端，重点复核云资产生命周期唯一到期事实、订单旧到期字段和计划快照回流、退款旧入口、废弃 app 误用、后台任务中心状态统计，以及机器人资产详情、订单详情、续费、换 IP、重装、修改配置等返回链和 Telegram `callback_data` 64 字节限制。

### 监工结果

- 起始工作树干净，未发现需要修改运行代码的问题。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段，仅有 `risk_expired` 风险布尔字段。
- `INSTALLED_APPS` 未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- 收窄扫描未发现旧计划快照、旧退款函数名、旧退款状态、旧端口入口或废弃 app runtime 导入回流。
- 动态枚举 96 个极端 ID 和嵌套来源 callback，最大长度 64 字节，无超过 Telegram 限制；生成出的短码前缀覆盖 `r`、`i`、`im`、`ir`、`ri`、`u`、`p`、`ao`、`af`、`ar`、`ac`、`au`、`cad`、`d`、`poc` 等现有处理器或返回入口。
- 复核后台任务中心测试覆盖，通知计划、生命周期计划和自动续费失败历史计数仍保持通过。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py check
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python -m py_compile bot/keyboards.py bot/handlers.py bot/tests.py cloud/task_center.py cloud/tests_task_center.py cloud/api_tasks.py
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('asset_expiry_fields', [f.name for f in CloudAsset._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at']); print('order_removed_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'actual_expires_at','service_expires_at'}]); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at'])"
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py shell -c "...动态枚举 96 个 callback，最大 64 字节，bad_count=0..."
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase cloud.tests_task_center --verbosity 2
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\\s*=|order\\.(service_expires_at|actual_expires_at)|CloudServerOrder\\([^\\n]*(service_expires_at|actual_expires_at)|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_cloud_server_order|refund_cloud_order|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py' --glob '!**/__pycache__/**'
```

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。第一次动态 callback 枚举脚本因未加载 Django settings 失败，改用 `manage.py shell` 后通过。SQLite 聚焦测试仍打印不支持 `db_comment` 的预期 warning、bot SimpleTestCase 配置读取容错日志和 mocked postcheck 异常日志，最终 58 条测试通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 更换 IP 返回链修复

### 范围

本轮继续监工 Shop Django 后端，重点复查机器人返回链、Telegram `callback_data` 64 字节限制、任务中心统计、云资产生命周期唯一到期事实、旧退款入口、旧计划快照和废弃 app 回流。

### 监工结果

- 当前工作树起始干净，最近提交为 `29de4d0 记录任务中心与回调巡检结果`。
- 发现更换 IP 的地区确认回调已经在按钮中携带上一层路径，但 `cb_cloud_change_ip_region` 只解析订单和地区，没有保留短回调 `ir:*` 或长回调 `cloud:ipregion:*` 后面的返回路径；成功提交更换 IP 新订单后固定返回主菜单，导致从资产详情、订单列表或 IP 查询进入时丢失上一层。
- 修复 `bot/handlers.py`：地区确认处理器解析并压缩 `back_callback`，成功提交后在消息底部提供“返回原代理”按钮，目标使用 `cloud_previous_detail_callback(order_id, back_callback)`；没有返回路径时仍使用主菜单。
- 补充 `bot/tests.py` 聚焦测试，覆盖地区确认处理器必须解析短/长回调中的返回路径，并把地区确认处理器纳入回调路径压缩检查。
- 结构化字段确认：`CloudAsset` 仍只有 `actual_expires_at` 作为资产到期事实；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段，仅有 `risk_expired` 风险布尔字段。
- 收窄扫描未发现旧计划快照、旧退款函数名、订单旧到期字段、旧端口入口或废弃 app 运行时回流。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python -m py_compile bot/handlers.py bot/tests.py bot/keyboards.py cloud/task_center.py cloud/tests_task_center.py
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_cloud_change_ip_region_submission_keeps_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_action_handlers_compact_nested_back_callback_before_reuse bot.tests.RetainedIpRenewalUiTestCase.test_cloud_change_ip_keyboards_keep_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_change_ip_from_asset_detail_returns_to_asset_detail --verbosity 1
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py check
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity 1
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --verbosity 1
UV_CACHE_DIR=/private/tmp/shop-uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_introspect_20260603.sqlite3 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('asset_expiry_fields', [f.name for f in CloudAsset._meta.fields if 'expire' in f.name or 'expiry' in f.name]); print('order_removed_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'actual_expires_at','service_expires_at'}]); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at'])"
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py shell -c "from types import SimpleNamespace; from decimal import Decimal; from bot.keyboards import cloud_server_change_ip_region_menu, cloud_server_detail, cloud_server_renew_payment, cloud_ip_query_result, cloud_auto_renew_callback; item_id=999999999999999999; back=f'cloud:ad:asset:{item_id}:cloud:list:page:{item_id}'; regions=[('us-east-1','美国'),('ap-southeast-1','新加坡'),('eu-west-2','英国'),('ca-central-1','加拿大'),('ap-south-1','印度'),('ap-northeast-1','日本')]; callbacks=[]; markups=[cloud_server_change_ip_region_menu(item_id, regions, back_callback=back), cloud_server_detail(item_id, True, True, True, back, True), cloud_server_renew_payment(item_id, Decimal('12.3'), Decimal('45.6'), auto_renew_enabled=False, back_callback=back), cloud_ip_query_result([], [{'ip':'1.2.3.4','order_id':item_id,'can_renew':True,'can_change_ip':True,'can_reinit':True,'can_config':True,'can_auto_renew':True}], include_reinit=True)]; callbacks.extend([cloud_auto_renew_callback('on', item_id, back), cloud_auto_renew_callback('off', item_id, back)]); [callbacks.append(button.callback_data) for markup in markups for row in (getattr(markup, 'inline_keyboard', []) or []) for button in row if getattr(button, 'callback_data', None)]; over=[data for data in callbacks if len(data.encode())>64]; print('callback_count', len(callbacks)); print('max_callback_bytes', max(len(data.encode()) for data in callbacks)); print('over_limit', over[:5])"
rg -n "service_expires_at\\s*=|order\\.(service_expires_at|actual_expires_at)|CloudServerOrder\\([^\\n]*(service_expires_at|actual_expires_at)|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_cloud_server_order|refund_cloud_order|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py'
```

结果：新增 4 条聚焦测试通过，完整 `RetainedIpRenewalUiTestCase` 46 条通过，`cloud.tests_task_center` 12 条通过；极端 18 位 ID 回调枚举 23 个 callback，最大 62 字节，无超过 64 字节。SQLite 任务中心测试仍打印不支持 `db_comment` 的预期 warning；bot SimpleTestCase 仍打印配置读取被禁止数据库查询拦截的既有容错日志；均不影响测试结果。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 后台任务中心与回调容量巡检

### 范围

本轮从 `f1635a7` 继续监工 Shop Django 后端，重点复核云资产生命周期唯一到期事实、后台任务中心失败状态统计、通知计划和生命周期执行任务漏报、机器人返回链与 Telegram `callback_data` 64 字节限制，以及废弃 app、旧计划快照、旧退款入口回流风险。

### 监工结果

- 当前工作树起始干净，最近提交为 `f1635a7 修复任务中心执行记录漏报`。
- 未发现需要修改运行代码的问题；本轮仅追加中文版本记录。
- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期事实；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`，仅保留 `renew_grace_expires_at` 等流程时间；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- `INSTALLED_APPS` 未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- 后台任务中心的通知计划、生命周期计划、自动续费状态统计聚焦测试通过；`status_counts` 对计划项、最近失败历史和 DB 执行任务记录的归一化仍能覆盖 `failed_retry`、`failed`、`retry_failed` 等失败状态。
- bot 回调短码生成与处理器注册一致：`ao:`/`af:`、`p:`、`r:`、`i:`、`ri:`、`u:`、`arp:`、`rnp:`、`cad:`、`csd:`、`d:` 均有对应解析路径。
- 使用 18 位极端 ID 枚举订单详情、资产详情、续费支付、IP 查询订单/资产动作、自动续费和资产操作按钮，未发现超过 Telegram 64 字节限制的 callback。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py check
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python -m py_compile bot/api.py bot/handlers.py bot/keyboards.py cloud/services.py cloud/bootstrap.py cloud/api.py cloud/task_center.py cloud/api_tasks.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py shell -c "from django.apps import apps; from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('retired_installed=', [x for x in ['accounts','finance','mall','monitoring','biz','dashboard_api'] if apps.is_installed(x)]); print('CloudAsset expiry fields=', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name]); print('CloudServerOrder expiry fields=', [f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name]); print('Snapshot expiry fields=', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name])"
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase cloud.tests_task_center --keepdb --noinput --verbosity 1
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py shell -c "from decimal import Decimal; from bot.keyboards import cloud_server_detail, cloud_server_renew_payment, cloud_ip_query_result, cloud_auto_renew_callback, cloud_asset_action_callback; long_id=999999999999999999; back=f'cloud:ad:asset:{long_id}:profile:orders:cloud:filter:provisioning:page:{long_id}'; order_item={'ip':'1.1.1.1','order_id':long_id,'can_change_ip':True,'can_reinit':True,'can_config':True,'can_auto_renew':True,'auto_renew_enabled':False}; asset_item={'ip':'1.1.1.2','asset_id':long_id,'can_change_ip':True,'can_reinit':True,'can_config':True,'can_support':True}; markups=[cloud_server_detail(long_id, True, True, True, back, True), cloud_server_renew_payment(long_id, Decimal('12.3'), Decimal('45.6'), back_callback=back), cloud_ip_query_result([], [order_item, asset_item], 1, 1, include_reinit=True)]; extra=[cloud_auto_renew_callback('on', long_id, back), cloud_auto_renew_callback('off', long_id, back), cloud_asset_action_callback('changeip', long_id, back), cloud_asset_action_callback('upgrade', long_id, back)]; callbacks=extra[:]; [callbacks.append(b.callback_data) for m in markups for row in m.inline_keyboard for b in row if b.callback_data]; too_long=[(len(c.encode()), c) for c in callbacks if len(c.encode())>64]; print('callback_count=', len(callbacks)); print('too_long=', too_long); print('max_len=', max(len(c.encode()) for c in callbacks)); print('max_callback=', max(callbacks, key=lambda c: len(c.encode())));"
```

SQLite 测试仍会打印不支持 `db_comment` 的预期系统警告；bot SimpleTestCase 仍会打印配置读取被禁止数据库查询拦截的既有容错日志；mocked postcheck 异常日志仍为测试覆盖输出，最终测试通过。两次 callback 枚举脚本先后因参数形状写错失败，修正为 `cloud_ip_query_result([], [order_item, asset_item], ...)` 后通过且无超长 callback。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 自动续费返回链与任务中心巡检

### 范围

本轮从提交 `fa20b69 修复IP查询自动续费返回链` 后继续监工，重点复查机器人资产详情、订单详情、续费、换 IP、重装、修改配置和自动续费返回链，确认 Telegram `callback_data` 64 字节限制、云资产生命周期唯一到期事实、后台任务中心失败统计、旧计划快照、旧退款入口和废弃 app 回流情况。

### 监工结果

- 起始工作树干净，未发现需要修改运行代码的问题；本轮仅追加中文版本记录。
- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期事实。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，默认端口仍为 443。
- `CloudAssetDashboardSnapshot` 未恢复到期字段，仅保留 `risk_expired` 等风险展示字段。
- `INSTALLED_APPS` 未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- 收窄扫描未发现旧计划快照、旧退款函数名、旧退款状态、旧端口入口或订单服务到期字段恢复。
- bot 回调聚焦测试确认资产详情、订单详情、续费支付、换 IP、重装、修改配置、自动续费和 IP 查询入口仍保持返回路径并满足 64 字节限制。
- 任务中心聚焦测试确认通知计划、生命周期计划和自动续费最近失败历史仍纳入 failed 与 `status_counts`，未发现后台总览漏报回退。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/keyboards.py bot/handlers.py bot/api.py cloud/task_center.py cloud/api_tasks.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/services.py cloud/provisioning.py
rg -n "service_expires_at\\s*=|order\\.(service_expires_at|actual_expires_at)|CloudServerOrder\\([^\\n]*(service_expires_at|actual_expires_at)|\\bCloudLifecyclePlan\\b|\\bCloudNoticePlan\\b|\\bCloudAutoRenewPlan\\b|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_cloud_server_order|refund_cloud_order|\\brefund_order\\b|\\bprocess_refund\\b|\\bcreate_refund\\b|\\bissue_refund\\b|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py'
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_introspect_current.sqlite3 UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('asset_expiry_fields', [f.name for f in CloudAsset._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at']); print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}]); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at']); print('default_port', CloudServerOrder._meta.get_field('mtproxy_port').default)"
DJANGO_TEST_SQLITE=1 DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_tests.sqlite3 UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity 2
DJANGO_TEST_SQLITE=1 DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_tests.sqlite3 UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --verbosity 2
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_migrations_check.sqlite3 UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
git diff --check
```

SQLite 聚焦测试仍会打印不支持 `db_comment` 的预期系统警告；bot SimpleTestCase 仍会打印配置读取被禁止数据库访问拦截的既有容错日志和 mocked postcheck 异常日志，最终测试结果均为 OK。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 IP 查询自动续费返回链修复

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态和最近提交，再检查云资产生命周期唯一到期事实、机器人返回链、Telegram `callback_data` 64 字节限制、后台任务中心统计、废弃 app 回流、旧计划快照和旧退款入口。

### 修改

- 修复 `cloud_ip_query_result` 中 IP 查询结果的自动续费按钮：改用统一的 `cloud_auto_renew_callback()` 构造 callback，并携带 `cloud:querymenu` 返回上下文。
- 补充 `RetainedIpRenewalUiTestCase.test_cloud_ip_query_actions_return_to_query_menu`，断言 IP 查询里的自动续费按钮返回到查询菜单，并继续满足 Telegram 64 字节限制。

### 监工结果

- 当前工作树起始干净，最近提交为 `66c26e3 修复自动续费回调返回路径`。
- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期事实；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`，仅保留 `renew_grace_expires_at` 等流程时间；`CloudAssetDashboardSnapshot` 无到期字段。
- `INSTALLED_APPS` 未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`；仓库根部未发现这些废弃 app 目录回流。
- 收窄扫描未发现旧计划快照、旧退款函数名、旧退款状态、订单服务到期字段或旧自定义端口入口回流。
- 后台任务中心本轮未发现新的状态统计漏报；上一轮覆盖的通知计划、生命周期计划、自动续费失败历史统计测试仍通过。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/keyboards.py bot/tests.py bot/handlers.py cloud/task_center.py cloud/api_tasks.py
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_ipquery_autorenew_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_cloud_ip_query_actions_return_to_query_menu --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_callbacks_ipquery_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_ipquery_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_introspect_ipquery_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('asset_expiry_fields', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name])"
UV_CACHE_DIR=/private/tmp/shop-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\s*=|order\.(service_expires_at|actual_expires_at)|CloudServerOrder\([^\n]*(service_expires_at|actual_expires_at)|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_cloud_server_order|refund_cloud_order|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性 warning，但最终结果为 `No changes detected`。SQLite 测试仍打印不支持 `db_comment` 的预期 warning；bot SimpleTestCase 仍打印配置读取被禁止数据库访问拦截的既有容错日志，最终测试 OK。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 自动续费返回链修复巡检

### 范围

本轮从 `8f7dca6 修复任务中心失败历史状态计数` 继续监工，重点复查云资产生命周期唯一到期事实、后台任务中心统计、机器人资产详情返回链和 Telegram `callback_data` 64 字节限制。

### 修复

- 修复群聊资产详情里的自动续费按钮不携带上一层返回路径的问题，改为统一使用紧凑 callback helper。
- 新增自动续费短 callback：开启使用 `ao:`，关闭使用 `af:`，在极端大 ID 和嵌套资产详情返回路径下仍保持不超过 64 字节。
- 自动续费处理器现在同时解析长格式和短格式，并在切换后保留原按钮的返回路径，避免用户从资产详情切换自动续费后丢失上一层上下文。

### 巡检结果

- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 收窄扫描未发现废弃 app 目录或旧退款入口回流；`dashboard_api` 命中仅为当前 URL namespace/helper，不是废弃 app 恢复。
- 后台任务中心失败历史计数测试仍通过。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python -m py_compile bot/keyboards.py bot/handlers.py bot/tests.py cloud/task_center.py cloud/tests_task_center.py
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py check
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity 2
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --verbosity 2
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('CloudAsset.actual_expires_at', any(f.name == 'actual_expires_at' for f in CloudAsset._meta.fields)); print('CloudServerOrder.actual_expires_at', any(f.name == 'actual_expires_at' for f in CloudServerOrder._meta.fields)); print('CloudServerOrder.service_expires_at', any(f.name == 'service_expires_at' for f in CloudServerOrder._meta.fields)); print('CloudAssetDashboardSnapshot.actual_expires_at', any(f.name == 'actual_expires_at' for f in CloudAssetDashboardSnapshot._meta.fields))"
```

SQLite 聚焦测试仍会打印不支持 `db_comment` 的预期 warning；bot SimpleTestCase 仍会打印配置读取被禁止数据库访问拦截的既有容错日志，最终测试通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 任务中心失败历史状态计数修复

### 范围

本轮继续监工 Shop Django 后端，先确认工作树起始干净、最近提交为 `8edac4f 记录生命周期任务总览巡检结果`，再重点巡检云资产生命周期唯一到期事实、机器人 callback 返回链、后台任务中心状态统计和废弃入口回流。

### 修复

- 修复后台任务中心 `status_counts` 漏报最近失败历史的问题。通知计划、生命周期计划、自动续费的 `total` 和 `failed` 原本已纳入最近失败历史，但状态分布只统计当前计划队列；现在最近失败历史也会进入状态分布。
- 自动续费历史是 QuerySet 时，展示列表仍限制最多 8 条，但 `status_counts.failed` 使用完整最近失败数量，避免 9 条以上失败只显示 8 的统计偏差。
- 增加任务中心聚焦测试，覆盖通知失败历史、自动续费失败历史、自动续费 QuerySet 多条失败历史和生命周期失败历史的 `status_counts`。

### 巡检结果

- 字段 introspection 确认 `CloudAsset.actual_expires_at` 存在且仍是资产唯一真实到期事实；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复 `actual_expires_at`。
- 扫描未发现运行时废弃 app 目录回流；`dashboard_api` 命中仍是当前 `core.dashboard_api` helper 和 URL namespace。
- 机器人返回链聚焦测试确认资产详情、订单详情、续费、换 IP、重装、修改配置等 callback 仍保持在 Telegram 64 字节限制内。
- 未发现旧计划快照、旧退款函数名或订单服务到期字段恢复。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py check
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python -m py_compile cloud/task_center.py cloud/tests_task_center.py bot/keyboards.py bot/handlers.py cloud/api_tasks.py bot/api.py
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py shell -c "from cloud.models import CloudServerOrder, CloudAssetDashboardSnapshot, CloudAsset; print('CloudAsset.actual_expires_at', any(f.name == 'actual_expires_at' for f in CloudAsset._meta.fields)); print('CloudServerOrder.actual_expires_at', any(f.name == 'actual_expires_at' for f in CloudServerOrder._meta.fields)); print('CloudServerOrder.service_expires_at', any(f.name == 'service_expires_at' for f in CloudServerOrder._meta.fields)); print('CloudAssetDashboardSnapshot.actual_expires_at', any(f.name == 'actual_expires_at' for f in CloudAssetDashboardSnapshot._meta.fields))"
```

第一次直接运行 `uv run ...` 时默认缓存目录 `/Users/a399/.cache/uv` 被沙箱拒绝，已改用 `/private/tmp/shop-uv-cache` 重跑。未指定 SQLite 的 Django 测试仍会尝试连接本机 MySQL 并被沙箱拒绝，已用 `DJANGO_TEST_SQLITE=1` 重跑通过。SQLite 测试会打印不支持 `db_comment` 的预期系统警告；bot SimpleTestCase 会打印一次禁止数据库查询的配置读取日志，最终测试通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 本地 MySQL 恢复与默认测试库权限修复

### 范围

本轮按用户“修复 mysql”要求，优先处理当前会话中聚焦测试无法连接或无法创建 MySQL 测试库的问题，并继续确认当前仓库状态、最近提交和自动化监工配置。

### 处理结果

- 起始工作树干净，最新提交为 `f1fd96f 校准任务中心最近失败总数`。
- 已恢复 `Shop 自动优化监工` 自动化为启用状态，保持每 10 分钟在 `/Users/a399/Desktop/data/shop` 当前项目上下文运行。
- 定位默认 `127.0.0.1:3306` 不是 Docker 临时容器，而是 OrbStack `database` 机器中的 MariaDB 10.11 服务。
- 未停用或删除 OrbStack `database` 机器中的 MariaDB 数据；只通过 `orb sudo mysql` 给本地 `a` 用户补齐 `a` 与 `test_a` 库权限，修复 Django 创建测试库时报 `Access denied for user 'a'@'localhost' to database 'test_a'` 的问题。
- 排障期间曾使用 AWS Public ECR 拉取官方 MySQL 8 镜像，在 `127.0.0.1:3307` 临时启动 `shop-mysql` 容器验证 MySQL 8 连通性；默认 3306 权限修复后，已停止并移除该临时容器，避免留下额外服务。
- `127.0.0.1:3306` 的 `a` 库已有 41 张表，迁移计划为空，未执行清库或数据删除。

### 验证

本地已通过:

```bash
uv run python manage.py migrate --plan
uv run python manage.py check
uv run python manage.py test cloud.tests_task_center bot.tests.TronGridFallbackTestCase bot.tests.RetainedIpRenewalUiTestCase --verbosity 1
MYSQL_PORT=3307 uv run python manage.py check
MYSQL_PORT=3307 uv run python manage.py test cloud.tests_task_center bot.tests.TronGridFallbackTestCase bot.tests.RetainedIpRenewalUiTestCase --verbosity 1
```

默认 3306 验证结果：

- `uv run python manage.py migrate --plan`：`No planned migration operations`
- `uv run python manage.py check`：`System check identified no issues`
- `uv run python manage.py test cloud.tests_task_center bot.tests.TronGridFallbackTestCase bot.tests.RetainedIpRenewalUiTestCase --verbosity 1`：56 个测试通过

并行只读 `codex-cli` 巡检结果：

- 未发现高置信问题。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`。
- 旧退款函数名、旧计划快照、废弃 app 运行时回流未发现高置信问题。
- Telegram `callback_data` 长路径已有短码压缩和 64 字节保护。

### 剩余风险

- 默认 3306 当前是 OrbStack `database` 机器的 MariaDB 10.11，而不是 MySQL 8；Django 当前验证可用，但若后续必须严格使用 MySQL 8，需要先迁移或停用该 MariaDB 服务，再把 MySQL 8 绑定回 3306。
- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。

## 2026-06-03 后台任务中心与生命周期巡检

### 范围

本轮继续巡检 Shop Django 后端的云资产生命周期唯一到期事实、订单/计划快照字段回流、旧退款入口、废弃 app 误用、Telegram 返回链 64 字节限制、云资产同步 worker、后台任务中心、通知计划、自动续费和生命周期计划状态统计。

### 监工结果

- 当前工作树起始状态干净，最近提交为 `f1fd96f 校准任务中心最近失败总数`。
- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 等流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复派生到期字段；生命周期任务和通知任务仅保留 `basis_actual_expires_at` 作为生成任务时引用快照，不作为到期事实。
- 未发现旧计划快照模型、旧退款函数名或退款状态在 runtime 代码中回流；相关旧字段命中仅存在于历史迁移。
- 未发现废弃 runtime app 重新加入 `INSTALLED_APPS`；`dashboard_api` 命中为当前 URL namespace 和 `core.dashboard_api` helper 命名。
- 回调返回链聚焦测试覆盖资产详情、订单详情、续费、换 IP、重装、修改配置等路径，未发现超过 Telegram `callback_data` 64 字节限制的回退。
- 后台任务中心、通知计划、自动续费和生命周期计划聚焦测试通过，未发现本轮漏报或失败状态统计回退。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py bot/handlers.py bot/keyboards.py cloud/services.py cloud/bootstrap.py cloud/api.py cloud/task_center.py cloud/api_tasks.py cloud/lifecycle.py cloud/asset_expiry.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py cloud/management/commands/process_cloud_asset_sync_jobs.py cloud/management/commands/refresh_lifecycle_plans.py cloud/management/commands/refresh_notice_plans.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests_task_center --noinput --verbosity 1
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_lists_plans_without_creating_order cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_completed_asset_recovery_order_renews_without_reprovisioning --noinput --verbosity 1
DJANGO_SETTINGS_MODULE=shop.settings DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python - <<'PY'
import django
django.setup()
from django.conf import settings
from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot
retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}
print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired])
print('asset_expiry_fields', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name])
print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name])
print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name])
PY
rg -n "refund|退款|apply_refund|refund_order|refund_cloud|service_expires_at|CloudLifecyclePlan|CloudNoticePlan|CloudAutoRenewPlan" cloud orders bot core shop -g '*.py'
rg -n "INSTALLED_APPS|include\\(|urlpatterns|dashboard_api|accounts|finance|mall|monitoring|biz" shop core bot orders cloud -g '*.py'
git diff --check
```

第一次直接运行 `uv run` 时，默认缓存目录 `/Users/a399/.cache/uv` 被沙箱拒绝访问；已改用 `/private/tmp/uv-cache-shop` 后通过。第一次未设置 SQLite 运行聚焦测试时，沙箱禁止连接本机 MySQL `127.0.0.1`；已切到 `DJANGO_TEST_SQLITE=1` 重跑通过。SQLite 测试期间的 `db_comment` 告警为测试后端能力差异，不是业务失败。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 任务中心最近失败总数校准

### 范围

本轮继续巡检 Shop Django 后端的后台任务中心、云资产生命周期唯一到期事实、废弃 app 回流、旧计划快照、旧退款入口，以及机器人返回链和 Telegram `callback_data` 64 字节限制。

### 运行时变化

- `cloud.task_center` 的生命周期计划、通知计划、自动续费 section 现在把近 24 小时最近失败历史计入 `total`。
- 修复前这些失败历史已经进入 `failed` 和明细 `items`，但 `total` 仍只统计当前计划项，可能出现后台总览中 `failed > total` 的不一致。
- 已补充 `cloud.tests_task_center` 聚焦断言，覆盖通知、自动续费、生命周期三类只有最近失败历史时 `total` 与 `failed` 一致；自动续费 QuerySet 路径 9 条失败历史也会统计为 `total=9`。

### 监工结果

- 机器人返回链聚焦测试继续通过，未发现资产详情、订单详情、续费、换 IP、重装、修改配置返回上一层回调超 64 字节。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at` 字段；`CloudAssetDashboardSnapshot` 未恢复资产到期字段，仅有 `risk_expired` 风险布尔字段。
- 未发现旧计划快照、旧退款函数名、旧端口配置入口或废弃 app 目录回流。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/task_center.py cloud/tests_task_center.py bot/handlers.py bot/keyboards.py cloud/api_tasks.py bot/api.py cloud/lifecycle.py cloud/sync_jobs.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_total_20260603.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_callbacks_task_center_20260603.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudServerOrder, CloudAsset, CloudAssetDashboardSnapshot; print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}]); print('asset_actual_fields', [f.name for f in CloudAsset._meta.fields if f.name == 'actual_expires_at']); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at'])"
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\\s*=|order\\.(service_expires_at|actual_expires_at)|CloudServerOrder\\([^\\n]*(service_expires_at|actual_expires_at)|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

`cloud.tests_task_center` 在 SQLite 下仍输出数据库注释不支持的 `fields.W163` / `models.W046` 预期警告，最终 9 条通过。`RetainedIpRenewalUiTestCase` 仍会打印 SimpleTestCase 禁止数据库查询配置的预期日志，最终 44 条通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 后台任务中心与回调链巡检

### 范围

本轮从 `9a00779 添加数据库中文注释` 开始，继续巡检 Shop Django 后端的数据库注释迁移漂移、云资产生命周期唯一到期事实、后台任务中心状态统计、机器人返回链和 Telegram `callback_data` 64 字节限制。

### 监工结果

- 本轮未发现需要修改运行时代码的新缺陷。
- `makemigrations --check --dry-run` 结果为 `No changes detected`，上一轮中文注释迁移未产生新的模型漂移。
- 后台任务中心聚焦测试继续覆盖云资产同步、生命周期计划、通知计划和自动续费失败统计；本轮 53 条聚焦测试全部通过。
- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at`、`expired_at` 等订单流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复派生到期字段，仅保留风险布尔字段 `risk_expired`。
- 未发现旧计划快照、旧退款函数名、废弃 app 目录或废弃 app 注册回流。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python -m py_compile bot/api.py bot/handlers.py cloud/services.py cloud/bootstrap.py cloud/api.py cloud/task_center.py cloud/lifecycle.py cloud/lifecycle_execution.py orders/payment_scanner.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center bot.tests.RetainedIpRenewalUiTestCase --verbosity=2
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python - <<'PY'
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')
import django
django.setup()
from django.apps import apps
from django.conf import settings
retired = {'accounts','finance','mall','monitoring','dashboard_api','biz'}
print('retired_installed=', sorted(retired & set(settings.INSTALLED_APPS)))
for label, model_name in [('cloud','CloudAsset'), ('cloud','CloudServerOrder'), ('cloud','CloudAssetDashboardSnapshot')]:
    model = apps.get_model(label, model_name)
    fields = [f.name for f in model._meta.get_fields()]
    print(model_name, [name for name in fields if 'expire' in name or 'expires' in name])
PY
rg -n "service_expires_at|CloudAssetDashboardSnapshot.*expires|refund_order|refund_cloud|legacy_refund|PlanSnapshot|CloudLifecyclePlanSnapshot|CloudNoticePlanSnapshot|CloudAutoRenewPlanSnapshot" cloud bot orders core shop -g '!**/migrations/**'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

SQLite 测试环境仍会输出数据库注释不受支持的 `fields.W163` / `models.W046` 警告，最终测试结果为通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布、数据删除或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 数据库中文注释补齐

### 范围

本轮按用户要求为当前运行时 Django 后端数据库补中文注释，并确保没有未提交孤儿代码。改动范围限定在当前活跃 app：`core`、`bot`、`orders`、`cloud`。

### 运行时变化

- 为当前活跃模型补齐 `db_table_comment`，覆盖系统配置、Telegram、订单、云资产、生命周期任务、通知任务、自动续费任务、链上监控等运行时表。
- 为模型字段补齐 `db_comment`，优先复用现有中文字段名；对密钥、余额、链上交易哈希、任务认领、生命周期依据等关键字段改用更明确的数据库注释。
- `CloudAsset.actual_expires_at` 的数据库注释明确标注为“云资产唯一真实到期事实，生命周期计划和续费判断以此字段为准”。
- `CloudServerOrder` 的 `service_started_at`、`renew_grace_expires_at`、`suspend_at`、`delete_at`、`ip_recycle_at`、`migration_due_at` 均标注为订单流程时间，不作为资产到期事实。
- `CloudAssetDashboardSnapshot` 的表注释明确为后台列表快照，不保存资产到期事实。
- 新增迁移：`bot.0016`、`core.0013`、`cloud.0050`、`orders.0006`，仅记录表注释和字段注释状态变化。

### 监工结果

- `CloudAsset` 仍只有 `actual_expires_at` 一个结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`。
- `CloudAssetDashboardSnapshot` 未恢复资产到期事实字段，仅保留风险布尔字段 `risk_expired`。
- 旧计划快照表、旧退款函数名和废弃 app 未回流。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python -m py_compile cloud/models.py core/models.py bot/models.py orders/models.py cloud/migrations/0050_alter_addressmonitor_table_comment_and_more.py core/migrations/0013_alter_cloudaccountconfig_table_comment_and_more.py bot/migrations/0016_alter_adminreplylink_table_comment_and_more.py orders/migrations/0006_alter_balanceledger_table_comment_and_more.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python - <<'PY'
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')
import django
django.setup()
from django.apps import apps
from cloud.models import CloudAsset, CloudAssetDashboardSnapshot, CloudServerOrder
print([app for app in ['accounts', 'finance', 'mall', 'monitoring', 'dashboard_api', 'biz'] if apps.is_installed(app)])
print([f.name for f in CloudAsset._meta.fields if 'expire' in f.name or 'expires' in f.name])
print([name for name in ['service_expires_at', 'actual_expires_at'] if hasattr(CloudServerOrder, name)])
print([f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expire' in f.name or 'expires' in f.name])
print(CloudAsset._meta.get_field('actual_expires_at').db_comment)
PY
git diff --check
```

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行生产数据库迁移；上线执行这些注释迁移时仍需按 MySQL 表结构变更窗口评估锁表影响。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。

## 2026-06-03 模型注释改动风险巡检

### 范围

本轮从 `324791e 补齐自动续费失败历史总览` 开始巡检，先确认工作树已有 `bot/models.py`、`cloud/models.py`、`core/models.py`、`orders/models.py` 四个未提交改动。改动主要是批量添加 `db_comment` 和表注释，属于本轮开始前已有的模型注释变更，本轮未覆盖或回退这些改动。

### 监工结果

- `uv run python manage.py check` 通过。
- 相关后端模块 `py_compile` 通过。
- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 等流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复派生到期字段。
- 废弃 app 未进入 `INSTALLED_APPS`；旧计划快照、旧退款函数名、旧端口入口和废弃 app 目录未发现回流。
- 机器人资产详情、订单详情、续费、换 IP、重装、修改配置等返回链聚焦测试仍满足 Telegram `callback_data` 64 字节限制。

### 发现的问题

- 当前未提交模型注释改动会触发迁移差异：`makemigrations --check --dry-run` 提示需要新增 `bot.0016`、`core.0013`、`cloud.0050`、`orders.0006` 等注释迁移。
- SQLite 测试环境会因为这些 `db_comment` / `db_table_comment` 输出大量 `fields.W163` 和 `models.W046` 警告；本轮任务中心测试仍通过，但日志噪音明显。
- `makemigrations --check --dry-run` 仍有本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，随后能列出缺失迁移。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py bot/handlers.py cloud/services.py cloud/bootstrap.py cloud/api.py cloud/task_center.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print([a for a in settings.INSTALLED_APPS if a in {'accounts','finance','mall','monitoring','dashboard_api','biz'}]); print([f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name])"
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --verbosity=2
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity=2
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\\s*=|order\\.(service_expires_at|actual_expires_at)|CloudServerOrder\\([^\\n]*(service_expires_at|actual_expires_at)|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_cloud_server_order|refund_cloud_order|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_*.py'
git diff --check
```

`cloud.tests_task_center` 9 条通过，`bot.tests.RetainedIpRenewalUiTestCase` 44 条通过。`makemigrations --check --dry-run` 预期失败，原因是本轮开始前已有模型注释改动尚未生成迁移。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未生成或提交模型注释迁移，避免把本轮开始前已有的模型改动混入自动化提交；后续需要由该模型注释改动的作者补迁移或撤回注释改动。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 自动续费失败历史总览补报

### 范围

本轮继续巡检 Shop Django 后端的云资产生命周期唯一到期事实、废弃 app 回流、旧计划快照、旧退款入口、机器人返回链、Telegram `callback_data` 64 字节限制，以及后台任务中心的状态统计和可观测性。

### 运行时变化

- `task_center_overview` 的自动续费区块在计入 24 小时内失败历史时，同步把最近失败历史补进 `items`，避免后台总览显示错误健康状态但列表看不到具体失败订单。
- 自动续费区块会按订单排除已经在当前失败队列中的历史失败，避免同一订单既作为 `retry_failed` 当前项又作为历史失败重复计数。
- 删除自动续费区块中不再使用的历史失败计数辅助函数，统一按最近失败历史总数驱动 `failed` 统计，列表最多展示前 8 条。

### 监工结果

- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 等流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复派生到期字段。
- 未发现旧计划快照、旧退款函数名或废弃 app 目录回流。
- 本轮未改动机器人回调生成逻辑；沿用上一轮已验证的资产详情返回链压缩边界。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/task_center.py cloud/tests_task_center.py cloud/api_tasks.py bot/api.py bot/handlers.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_20260603_next.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_retained_ip_20260603_next.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print([f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name])"
rg -n "service_expires_at\\s*=|order\\.(service_expires_at|actual_expires_at)|CloudServerOrder\\([^\\n]*(service_expires_at|actual_expires_at)|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

字段检查输出为:

```text
['actual_expires_at']
['renew_grace_expires_at']
[]
```

### 剩余风险

- 本轮未跑完整测试套件。
- `bot.tests.RetainedIpRenewalUiTestCase` 仍会打印 SimpleTestCase 禁止数据库查询配置的预期日志，最终 44 条通过。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 任务中心最近失败历史补报

### 范围

本轮继续巡检 Shop Django 后端的云资产生命周期唯一到期事实、任务中心状态统计、通知计划、自动续费、生命周期计划、机器人返回链和 Telegram `callback_data` 64 字节限制。

### 运行时变化

- `cloud.task_center` 的通知计划、自动续费、生命周期计划总览现在会把近 24 小时失败历史纳入失败统计，避免详情页已有失败记录但后台任务中心总览仍显示正常。
- 通知计划和生命周期计划的最近失败历史会作为兜底 item 展示，备注优先取失败原因、重试说明或失败结果标签。
- 已对 active 待重试项和最近失败历史做订单/资产/IP 维度去重，避免同一失败同时出现在待执行计划和历史里时重复放大失败数。

### 监工结果

- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`。
- `CloudAssetDashboardSnapshot` 未恢复到期字段。
- 废弃 app 未进入 `INSTALLED_APPS`；扫描命中的 `dashboard_api` 仅为当前 `shop.admin_urls` 命名空间，命中的 `finance`/`monitoring` 为权限码或缓存键文字，不是旧 app 回流。
- 旧退款函数名、旧计划快照模型未发现回流；`ip_recycle_at=asset.actual_expires_at` 仍是固定 IP 回收计划派生时间，不是订单到期事实恢复。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/task_center.py cloud/tests_task_center.py cloud/api_tasks.py bot/api.py
DJANGO_SETTINGS_MODULE=shop.settings DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python - <<'PY'
import django
from django.conf import settings
django.setup()
from cloud.models import CloudAsset, CloudAssetDashboardSnapshot, CloudServerOrder
retired = {'accounts', 'finance', 'mall', 'monitoring', 'dashboard_api', 'biz'}
installed = {app.split('.')[0] for app in settings.INSTALLED_APPS}
print('retired_installed=', sorted(retired & installed))
print('CloudAsset_has_actual_expires_at=', 'actual_expires_at' in {f.name for f in CloudAsset._meta.fields})
print('CloudServerOrder_removed_expiry_fields=', sorted({'service_expires_at', 'actual_expires_at'} & {f.name for f in CloudServerOrder._meta.fields}))
print('CloudAssetDashboardSnapshot_expiry_fields=', sorted({f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name}))
PY
rg -n "service_expires_at|CloudServerOrder.*actual_expires_at|CloudAssetDashboardSnapshot.*expires|refund_cloud|refund_order|process_refund|refund_server" cloud bot orders shop core -g '!**/migrations/**'
rg -n "INSTALLED_APPS|accounts|finance|mall|monitoring|dashboard_api|biz" shop/settings.py shop/urls.py core bot orders cloud -g '!**/migrations/**'
git diff --check
```

第一次直接运行 `uv run` 时，默认缓存目录 `/Users/a399/.cache/uv` 被沙箱拒绝访问；已改用 `/private/tmp/uv-cache-shop` 重跑通过。第一次不带 `DJANGO_TEST_SQLITE=1` 运行测试时，沙箱禁止连接本机 MySQL `127.0.0.1`；已切到项目支持的 SQLite 测试模式重跑通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 后台任务中心失败备注补齐

### 范围

本轮继续巡检 Shop Django 后端的云资产生命周期唯一到期事实、废弃 app 回流、旧计划快照、旧退款入口、机器人返回链和 Telegram `callback_data` 64 字节限制，并重点复查后台任务中心、通知计划、自动续费和生命周期计划的失败状态可观测性。

### 运行时变化

- `cloud.task_center._plan_item()` 统一从 `last_failure_reason`、`failure_reason`、`last_error`、`error`、`execution_status`、`notice_status_label` 中提取首个非空备注。
- 后台任务中心的生命周期计划、通知计划、自动续费失败项现在不再只显示失败统计，也会在列表项 `note` 中暴露对应失败原因，避免总览显示红色但明细备注为空。
- 新增聚焦测试覆盖通知失败重试、自动续费失败重试和生命周期逾期失败的备注展示。

### 监工结果

- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 等流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复派生到期字段。
- `INSTALLED_APPS` 未恢复旧 `accounts/finance/mall/monitoring/dashboard_api/biz` app，工作树也未发现这些废弃目录回流。
- 机器人返回链聚焦测试通过，资产详情、订单详情、续费、换 IP、重装、修改配置相关回调仍保持在 Telegram 64 字节限制内。
- 未发现旧计划快照、旧退款函数名、旧端口入口或订单服务到期字段回流。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py cloud/services.py cloud/provisioning.py cloud/api_orders.py cloud/api_tasks.py cloud/task_center.py cloud/sync_jobs.py cloud/lifecycle.py cloud/lifecycle_tasks.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_callbacks_monitor_<进程>.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_notes_<进程>.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/task_center.py cloud/tests_task_center.py
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_monitor_introspect_<进程>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('asset_expiry_fields', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name]);"
rg -n "service_expires_at\\s*=|order\\.(service_expires_at|actual_expires_at)|CloudServerOrder\\([^\\n]*(service_expires_at|actual_expires_at)|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 任务中心通知失败状态复查

### 范围

本轮继续巡检 Shop Django 后端，重点从云资产同步 worker、后台任务中心入口和数据库连接设置切入，复查同步任务状态、任务中心聚合统计、通知计划失败状态、MySQL 严格模式、旧到期字段、旧计划快照、旧退款入口、旧端口入口和废弃 app 回流情况。

### 运行时变化

- 修复后台任务中心“通知计划”分区失败统计漏报 `failed_retry` 的问题。
- 通知计划详情中失败发送会以 `notice_status=failed_retry` 表示“通知失败，待重试”，任务中心现在会把它计入 `failed` 并将分区健康状态标记为 `error`。
- 新增聚焦测试覆盖 `failed_retry` 通知计划项，避免任务中心总览后续再次把失败待重试通知显示为非失败状态。
- MySQL 连接默认通过 `MYSQL_SQL_MODE` 设置 `STRICT_TRANS_TABLES`，防止 MySQL 在日期、数值等字段上静默截断或降级写入；如需兼容特殊环境，可显式把 `MYSQL_SQL_MODE` 置空关闭。
- `MYSQL_SQL_MODE` 会去重、大写并拒绝非字母数字下划线字符，避免把未校验内容拼入 `init_command`。

### 监工结果

- 云资产同步任务队列、worker 领取、卡住任务恢复、取消、重试、指标和任务中心入口完成静态复查，未发现新的可复现运行时缺陷。
- `CloudAsset` 仍以 `actual_expires_at` 作为结构化资产到期事实。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`。
- `CloudAssetDashboardSnapshot` 未恢复到期字段。
- 未发现旧计划快照、旧退款函数名、`allow_client_port`、`set_cloud_server_port`、旧端口 callback 或废弃 app 目录回流。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_notice_failed_before.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center.CloudTaskCenterApiTestCase.test_notice_section_counts_failed_retry_as_failed --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_notice_check2.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_core_mysql_sql_mode.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test core.tests.MySqlSqlModeSettingsTestCase --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_retained_ui_notice_run.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_lifecycle_notice_run.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_aliyun_assets_preserves_existing_asset_expiry cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_rebinds_unattached_ip_when_instance_reappears cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_refreshes_unattached_ip_delete_plan --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile core/tests.py cloud/task_center.py cloud/tests_task_center.py cloud/api_tasks.py cloud/sync_jobs.py cloud/management/commands/process_cloud_asset_sync_jobs.py shop/settings.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_introspection_notice_run.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; [print(model.__name__, [f.name for f in model._meta.fields if 'expires' in f.name or 'expiry' in f.name]) for model in (CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot)]"
rg -n "service_expires_at\s*=|order\.(service_expires_at|actual_expires_at)|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py' --glob '!**/tests_task_center.py'
```

第一条测试在修复前按预期失败，确认 `failed_retry` 会被漏算为 0 个失败；修复后 `cloud.tests_task_center` 2 条通过。`RetainedIpRenewalUiTestCase` 44 条通过，仍会打印 SimpleTestCase 禁止数据库查询配置的预期日志但不影响结果。生命周期/同步保留 4 条聚焦测试通过，确认阿里云同步不覆盖资产到期、AWS 重新发现资产保留到期、未附加 IP 到期计划刷新仍使用资产字段。模型字段 introspection 显示 `CloudAsset=['actual_expires_at']`、`CloudServerOrder=['renew_grace_expires_at']`、`CloudAssetDashboardSnapshot=[]`。`makemigrations --check --dry-run` 仍出现本地无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 生命周期通知文本导入修复

### 范围

本轮按 10 分钟监工任务继续调用 `codex-cli` 巡检 Shop 后端，重点复查机器人默认端口创建流程、付款后直接创建、返回按钮短回调、生命周期计划和唯一到期字段。

### 运行时变化

- 修复 `cloud.lifecycle._notice_plan_text()` 中续费价格展示的嵌套 f-string 引号冲突。
- 续费价格先格式化为局部变量，再传入 HTML code 包装函数，避免 `cloud.lifecycle` 因语法错误无法导入。
- 默认端口流程继续保持 `MTPROXY_DEFAULT_PORT = 443`，机器人新购和付款成功后的创建路径仍直接提交默认端口。

### 监工结果

- `codex-cli` 报告路径：`/private/tmp/shop_codex_review_20260603_0227.md`。
- 本轮发现 1 个高置信运行级问题：`cloud.lifecycle` 语法错误会影响 `bot.runner`、`cloud.api_tasks` 和后台任务 URL 导入。
- 只读巡检未发现旧用户自定义端口入口、旧计划快照、退款函数名或废弃 app 回流。
- 端口扫描未发现 `waiting_port`、`custom:port:`、`cloud:ipport:` 或 `allow_client_port` 回流。

### 验证

本地已通过:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -c "import ast, pathlib; ast.parse(pathlib.Path('cloud/lifecycle.py').read_text(), filename='cloud/lifecycle.py'); print('AST_OK')"
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/lifecycle.py cloud/lifecycle_tasks.py cloud/lifecycle_execution.py cloud/api_tasks.py orders/payment_scanner.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_default_port_fix_20260603_b.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_cloud_chain_payment_auto_submits_default_port_provision bot.tests.RetainedIpRenewalUiTestCase.test_wallet_balance_purchase_auto_submits_default_port bot.tests.BotOrderAndBalanceFilterTestCase.test_paid_cloud_order_prepare_submits_default_port_directly bot.tests.BotOrderAndBalanceFilterTestCase.test_balance_pay_existing_cloud_order_auto_submits_default_port --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_lifecycle_ast_fix_20260603.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle --noinput --verbosity 1
git diff --check
```

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。

## 2026-06-03 详情返回按钮超长来源兜底

### 范围

本轮继续巡检 Shop Django 后端的机器人返回链、Telegram `callback_data` 64 字节限制、云资产生命周期唯一到期事实、订单旧到期字段、旧计划快照表、旧退款入口和废弃 app 回流。

### 运行时变化

- 资产详情处理器收到未知超长返回来源时，不再把原始来源直接放进“返回代理列表”按钮，而是复用现有返回按钮兜底压缩，无法识别时回落到 `cloud:list`。
- 管理员“修改到期时间”完成后的“返回原页面”按钮同样接入返回按钮兜底压缩，避免 FSM 中保存的异常长来源恢复为超长 `callback_data`。
- 只读订单详情 `cloud_order_readonly_detail()` 的“返回订单列表”按钮从普通路径压缩改为返回按钮兜底压缩；未知超长来源回落到 `cloud:list`。
- 补充聚焦测试，锁定资产详情处理器、管理员修改到期处理器和只读订单详情返回按钮都不会恢复未知超长来源。

### 监工结果

- 极端 18 位订单 ID、18 位资产 ID、18 位页码、嵌套资产详情来源、订单详情来源和未知 120 字符来源共 96 个回调样本无超过 64 字节。
- 运行时代码扫描未发现 `service_expires_at`、旧计划快照类、旧退款函数名、`allow_client_port`、`set_cloud_server_port`、旧 `custom:port` 或 `cloud:ipport` 入口回流。
- 模型字段 introspection 确认 `CloudAsset` 仍只有 `actual_expires_at` 到期字段；`CloudServerOrder` 仍仅有 `renew_grace_expires_at` 流程到期字段；`CloudAssetDashboardSnapshot` 无到期字段。
- 废弃 app 未恢复到运行时代码扫描范围；`dashboard_api` 仍仅作为当前路由 namespace/公共 helper 命名存在。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python -m py_compile bot/keyboards.py bot/handlers.py bot/tests.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_sync_aws_retained_ip_preserves_existing_asset_user cloud.tests.CloudServerServicesTestCase.test_lifecycle_delete_task_claim_blocks_same_cycle_duplicate cloud.tests.CloudServerServicesTestCase.test_lifecycle_asset_task_claim_blocks_same_cycle_duplicate --verbosity=2
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('CloudAsset expiry fields', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('CloudServerOrder expiry fields', [f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('Snapshot expiry fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name])"
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py shell <<'PY'
from decimal import Decimal
from types import SimpleNamespace
from bot.keyboards import append_back_callback, cloud_asset_detail_callback, cloud_detail_callback, cloud_previous_detail_callback, cloud_server_change_ip_region_menu, cloud_server_detail, cloud_server_renew_payment
from bot.handlers import _asset_renewal_plan_keyboard, _retained_ip_renewal_plan_keyboard
item_id = 999999999999999999
sources = [
    f'cloud:detail:{item_id}:profile:orders:cloud:filter:provisioning:page:{item_id}',
    f'cloud:ad:asset:{item_id}:cloud:list:page:{item_id}',
    f'cad:{item_id}:d:{item_id}:o:provisioning:{item_id}',
    'x' * 120,
]
regions = [('ap-southeast-1', 'Singapore'), ('us-east-1', 'N. Virginia'), ('eu-central-1', 'Frankfurt')]
plans = [SimpleNamespace(id=item_id, name='Large')]
callbacks = []
for source in sources:
    callbacks.extend([
        cloud_detail_callback(item_id, source),
        cloud_asset_detail_callback(item_id, source),
        cloud_previous_detail_callback(item_id, source),
        append_back_callback(f'cloud:renew:{item_id}', source),
        append_back_callback(f'cloud:ip:{item_id}', source),
        append_back_callback(f'cloud:reinit:{item_id}', source),
        append_back_callback(f'cloud:upgrade:{item_id}', source),
        append_back_callback(f'exp:a:{item_id}', source),
    ])
    for markup in [
        cloud_server_detail(item_id, True, True, True, source, True),
        cloud_server_renew_payment(item_id, Decimal('12.3'), Decimal('45.6'), back_callback=source),
        cloud_server_change_ip_region_menu(item_id, regions, back_callback=source),
        _asset_renewal_plan_keyboard(item_id, plans, source),
        _retained_ip_renewal_plan_keyboard(item_id, plans, source),
    ]:
        callbacks.extend(button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data)
violations = sorted({cb for cb in callbacks if cb and len(cb.encode()) > 64})
print('callback_count', len(callbacks))
print('violations', violations)
PY
rg -n "service_expires_at|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_order|process_refund|create_refund|allow_client_port|set_cloud_server_port|custom:port|cloud:ipport" bot cloud core orders shop -S --glob '*.py' --glob '!*/migrations/*' --glob '!*tests.py' && exit 1 || exit 0
git diff --check
```

`manage.py test bot.tests.RetainedIpRenewalUiTestCase` 和极端 callback shell 脚本导入处理器时仍会触发本机 MySQL 沙箱拒绝日志，这是导入期读取 `SiteConfig` 的既有现象；最终测试通过且极端脚本输出 `violations []`。`makemigrations --check --dry-run` 仍有本机 MySQL 迁移历史检查警告，但最终结果为 `No changes detected`。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 生命周期唯一到期事实监工复查

### 范围

本轮继续巡检 Shop Django 后端的云资产生命周期唯一到期事实、订单旧到期字段回流、计划快照回流、旧退款入口、废弃 app 误用，以及机器人资产详情、订单详情、续费、换 IP、重装、修改配置返回链和 Telegram `callback_data` 64 字节限制。

### 监工结果

- 未发现新的运行时代码 bug，本轮未修改运行代码。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 等流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复派生到期字段。
- 旧计划模型、旧退款函数名、旧端口入口和废弃 app 目录扫描未发现运行时代码回流；扫描命中的 `order.ip_recycle_at = asset.actual_expires_at` 属于未附加固定 IP 回收计划派生时间，不是订单服务到期字段恢复。
- 极端 18 位 ID、18 位页码、嵌套资产详情来源、订单详情来源和未知超长来源组合共 110 个 callback 样本均未超过 64 字节。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python -m py_compile bot/api.py bot/handlers.py bot/keyboards.py cloud/services.py cloud/provisioning.py cloud/lifecycle.py cloud/api.py cloud/api_orders.py cloud/api_asset_snapshots.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_monitor_retained_<进程号>.sqlite uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --keepdb --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_lifecycle_current_<进程号>.sqlite uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_address_order_forces_usdt_from_trx_source cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_rejects_link_port_override cloud.tests.CloudServerServicesTestCase.test_retained_ip_renewal_address_order_forces_usdt_from_trx_order cloud.tests.CloudServerServicesTestCase.test_retained_ip_renewal_rejects_link_port_override cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle orders.tests.ChainPaymentScannerTestCase.test_public_asset_renewal_expiry_does_not_claim_unowned_asset --keepdb --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print([f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name])"
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DJANGO_SETTINGS_MODULE=shop.settings uv run python - <<'PY'
import django
django.setup()
from decimal import Decimal
from bot.keyboards import cloud_asset_detail_callback, cloud_detail_callback, cloud_server_change_ip_region_menu, cloud_server_detail, cloud_server_renew_payment, append_back_callback, compact_callback_path
from bot.handlers import _asset_renewal_plan_keyboard, _retained_ip_renewal_plan_keyboard

class Plan:
    def __init__(self, item_id):
        self.id = item_id
        self.display_plan_name = '测试套餐'
        self.display_cpu = '1C'
        self.display_memory = '1G'
        self.display_storage = '20G'
        self.price = Decimal('1.23')
        self.currency = 'USDT'

item_id = 123456789012345678
backs = [
    f'profile:orders:cloud:filter:provisioning:page:{item_id}',
    f'cloud:detail:{item_id}:profile:orders:cloud:filter:provisioning:page:{item_id}',
    f'cloud:ad:asset:{item_id}:profile:orders:cloud:filter:provisioning:page:{item_id}',
    f'cad:{item_id}:d:{item_id}:o:provisioning:{item_id}',
    'x' * 200,
]
regions = [('us-east-1', '美国东部'), ('ap-southeast-1', '新加坡')]
plans = [Plan(item_id)]
callbacks = []
for back in backs:
    callbacks.extend([
        cloud_asset_detail_callback(item_id, back),
        cloud_detail_callback(item_id, back),
        append_back_callback(f'cloud:renew:{item_id}', back),
        append_back_callback(f'cloud:ip:{item_id}', back),
        append_back_callback(f'cloud:reinit:{item_id}', back),
        append_back_callback(f'cloud:assetinit:{item_id}', back),
        append_back_callback(f'exp:a:{item_id}', back),
    ])
    for markup in [
        cloud_server_detail(item_id, True, True, True, back, True, True),
        cloud_server_renew_payment(item_id, Decimal('12.3'), Decimal('45.6'), back_callback=back),
        cloud_server_change_ip_region_menu(item_id, regions, back_callback=back),
        _asset_renewal_plan_keyboard(item_id, plans, back),
        _retained_ip_renewal_plan_keyboard(item_id, plans, back),
    ]:
        callbacks.extend(button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data)
assert not [value for value in callbacks if len(value.encode()) > 64]
print('callbacks', len(callbacks), 'violations', 0)
print('sample_compact', compact_callback_path(backs[1]))
PY
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
```

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。一次极端 callback 脚本未设置 Django 环境、一次生命周期测试使用旧选择器失败，均已换成当前有效命令重跑通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 机器人端口流程最终收口

### 范围

本轮按“机器人移除用户自己设置端口逻辑，默认端口改为 443，其他端口不变，用户付款成功后直接进入创建流程”继续检查运行路径。

### 运行时变化

- `CloudServerOrder.mtproxy_port` 和 `MTPROXY_DEFAULT_PORT` 已保持为 443。
- 备用、Telemt 和 SOCKS5 端口保持 9529-9534 不变。
- 机器人状态机继续保持无 `waiting_port`，运行代码无 `custom:port:*`、`cloud:ipport:*` 或 `set_cloud_server_port()` 用户端口设置入口。
- 修正重装主链接、未附加固定 IP 续费旧链接两条默认文案，不再提示“以用户发送的主链接端口为准”，统一表达为端口必须与系统记录一致，未记录时使用默认 443。
- 新增 `core.0012_remove_user_port_override_texts` 数据迁移，只替换数据库中仍等于旧默认值的两条站点文案，不覆盖后台自定义文案，也不在反向迁移里恢复旧端口覆盖话术。

### 付款后创建流程确认

- 余额支付路径继续在付款成功后按默认 443 调用 `prepare_cloud_server_order_instances()`，并立即调度 `_provision_cloud_server_and_notify()`。
- 链上地址支付路径继续在确认付款后把订单端口写为 443，备注“使用默认端口 443”，并立即调度 `_provision_paid_cloud_order()` 进入创建流程。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile core/texts.py core/tests.py bot/tests.py core/migrations/0012_remove_user_port_override_texts.py orders/payment_scanner.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_port_flow_20260603.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_legacy_custom_port_flow_is_removed bot.tests.RetainedIpRenewalUiTestCase.test_wallet_balance_purchase_auto_submits_default_port --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_order_chain_default_port_20260603.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_cloud_chain_payment_auto_submits_default_port_provision --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_core_port_text_migration_20260603.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test core.tests.PortOverrideTextMigrationTestCase --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_migration_check_20260603.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_migrate_plan_20260603.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan core
rg -n "以你发送的主链接端口为准|系统记录的主端口不对|waiting_port|custom:port:|cloud:ipport:|bot_custom_port_invalid|bot_set_port_failed|bot_custom_port_hint|bot_custom_port_success|set_cloud_server_port" bot core cloud orders shop --glob '!**/migrations/**' --glob '!**/tests.py'
git diff --check
```

`rg` 残留扫描在运行代码中无命中。期间曾误跑不存在的测试名 `test_cloud_chain_payment_auto_submits_default_port`，随后已用真实测试名 `test_cloud_chain_payment_auto_submits_default_port_provision` 重跑通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。

### 补充监工验证

本轮在提交 `556e02f` 后继续补验机器人返回链、生命周期唯一到期事实和旧入口回流，未发现需要追加运行时代码修复的问题。

补充确认:

- `RetainedIpRenewalUiTestCase` 43 条通过，资产详情、订单详情、续费支付、换 IP、重装和修改配置相关按钮仍未生成超过 Telegram 64 字节限制的 `callback_data`。
- 生命周期抽样测试 5 条通过，AWS 同步仍保留人工 `CloudAsset.actual_expires_at`，订单生命周期刷新仍从资产到期事实派生。
- 模型 introspection 显示 `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at` 字段，`CloudAsset` 到期字段仍仅有 `actual_expires_at`，`CloudAssetDashboardSnapshot` 仅有 `risk_expired` 风险字段。
- 运行代码扫描未发现 `allow_client_port`、`set_cloud_server_port`、旧端口 callback、旧计划模型、旧退款函数名或废弃 app 目录回流。
- 极端 18 位订单/资产 ID、18 位页码和长订单筛选来源组合下，机器人按钮样本无超过 64 字节的 callback。

补充验证命令:

```bash
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_ui_text_<进程>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_lifecycle_text_<进程>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_manual_asset_note cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time orders.tests.ChainPaymentScannerTestCase.test_expired_asset_renewal_payment_unbinds_asset_for_retry --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_introspect_text_<进程>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'service_expires_at','actual_expires_at'}]); print('asset_actual_fields', [f.name for f in CloudAsset._meta.fields if f.name == 'actual_expires_at']); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expire' in f.name or 'expiry' in f.name or f.name == 'actual_expires_at']); print('default_port', CloudServerOrder._meta.get_field('mtproxy_port').default)"
DJANGO_SETTINGS_MODULE=shop.settings UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python - <<'PY'
import django
from decimal import Decimal
from types import SimpleNamespace

django.setup()
from bot.keyboards import cloud_server_detail, cloud_server_renew_payment, cloud_server_change_ip_region_menu, cloud_order_list, cloud_server_list, cloud_ip_query_result
item_id = 999999999999999999
regions = [('ap-southeast-1', '新加坡'), ('ap-northeast-1', '日本'), ('eu-central-1', '德国'), ('ap-northeast-3', '大阪'), ('me-central-1', '阿联酋')]
long_back = f'cloud:detail:{item_id}:profile:orders:cloud:filter:provisioning:page:{item_id}'
order = SimpleNamespace(id=item_id, status='completed', public_ip='1.1.1.1', previous_public_ip='', pay_amount=Decimal('1'), total_amount=Decimal('1'), currency='USDT', get_status_display=lambda: '已完成')
markups = [
    cloud_server_detail(item_id, True, True, True, long_back, True),
    cloud_server_renew_payment(item_id, Decimal('1'), Decimal('2'), back_callback=long_back),
    cloud_server_change_ip_region_menu(item_id, regions, expanded=True, back_callback=long_back),
    cloud_order_list([order], page=item_id, total_pages=item_id, prefix='profile:orders:cloud:filter:provisioning:page', order_filter='paid'),
    cloud_server_list([order], page=item_id, total_pages=item_id, prefix='profile:orders:cloud:filter:provisioning:page'),
    cloud_ip_query_result([], [{'order_id': item_id, 'asset_id': 0, 'can_change_ip': True, 'can_reinit': True, 'can_config': True, 'can_auto_renew': True, 'can_support': True}], include_reinit=True),
]
violations = []
for markup in markups:
    for row in markup.inline_keyboard:
        for button in row:
            data = getattr(button, 'callback_data', None)
            if data and len(data.encode()) > 64:
                violations.append((len(data.encode()), data))
assert not violations, violations
PY
rg -n "service_expires_at\\s*=|actual_expires_at\\s*=.*order\\.|order\\..*actual_expires_at|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
```

极端回调脚本读取客服按钮配置时仍会触发本机 MySQL 沙箱拒绝日志，但脚本最终确认违规列表为空。

## 2026-06-03 详情页返回按钮极端回调压缩

### 范围

本轮继续巡检 Shop Django 后端的机器人返回链、Telegram `callback_data` 64 字节限制、云资产生命周期唯一到期事实、废弃 app 回流、旧计划快照和旧退款入口。

### 运行时变化

- `cloud_server_detail()` 的底部“返回列表”按钮不再直接使用一次压缩后的返回来源，而是通过专用兜底函数再次检查字节长度。
- 当返回来源是极端嵌套订单详情，例如 `cloud:detail:<18位订单ID>:profile:orders:cloud:filter:provisioning:page:<18位页码>` 时，返回按钮会降级为 `poc:provisioning:<页码>`，避免生成 67 字节 callback。
- 常规来源、短资产详情来源和已在 64 字节内的返回路径保持原有行为。
- 新增聚焦测试覆盖 18 位订单 ID + 18 位页码 + 长筛选来源下的详情页返回按钮，防止后续恢复超长回调。

### 监工结果

- 极端回调样本复查 27 个按钮，修复后未发现超过 64 字节的 `callback_data`。
- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 流程字段。
- `CloudAssetDashboardSnapshot` 未恢复派生到期字段。
- 未发现旧计划快照、旧退款函数名、`allow_client_port` 或废弃 app 目录回流；`dashboard_api` 仅作为现有路由 namespace。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/services.py cloud/models.py cloud/api_orders.py orders/services.py orders/tests.py
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_auto_bot_ui_<进程>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_auto_lifecycle_<进程>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_due_orders_use_asset_expiry_for_lightsail_lifecycle cloud.tests.CloudServerServicesTestCase.test_notice_delete_plan_and_proxy_list_use_asset_expiry cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_manual_asset_note cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudOrderStatusDashboardSyncTestCase.test_order_detail_manual_edit_syncs_cloud_identity_and_proxy_fields orders.tests.ChainPaymentScannerTestCase.test_expired_asset_renewal_payment_unbinds_asset_for_retry --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_auto_introspect_<进程>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print([f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name])"
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_auto_migrations_<进程>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=shop.settings UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache uv run python - <<'PY'
import django
from decimal import Decimal
from types import SimpleNamespace

django.setup()
from bot.keyboards import cloud_server_detail, cloud_server_renew_payment, cloud_server_change_ip_region_menu, cloud_order_list, cloud_server_list, cloud_ip_query_result
item_id = 999999999999999999
regions = [('ap-southeast-1', '新加坡'), ('ap-northeast-1', '日本'), ('eu-central-1', '德国'), ('ap-northeast-3', '大阪'), ('me-central-1', '阿联酋')]
long_back = f'cloud:detail:{item_id}:profile:orders:cloud:filter:provisioning:page:{item_id}'
order = SimpleNamespace(id=item_id, status='completed', public_ip='1.1.1.1', previous_public_ip='', pay_amount=Decimal('1'), total_amount=Decimal('1'), currency='USDT', get_status_display=lambda: '已完成')
markups = [
    cloud_server_detail(item_id, True, True, True, long_back, True),
    cloud_server_renew_payment(item_id, Decimal('1'), Decimal('2'), back_callback=long_back),
    cloud_server_change_ip_region_menu(item_id, regions, expanded=True, back_callback=long_back),
    cloud_order_list([order], page=item_id, total_pages=item_id, prefix='profile:orders:cloud:filter:provisioning:page', order_filter='paid'),
    cloud_server_list([order], page=item_id, total_pages=item_id, prefix='profile:orders:cloud:filter:provisioning:page'),
    cloud_ip_query_result([], [{'order_id': item_id, 'asset_id': 0, 'can_change_ip': True, 'can_reinit': True, 'can_config': True, 'can_auto_renew': True, 'can_support': True}], include_reinit=True),
]
violations = []
for markup in markups:
    for row in markup.inline_keyboard:
        for button in row:
            data = getattr(button, 'callback_data', None)
            if data and len(data.encode()) > 64:
                violations.append((len(data.encode()), data))
assert not violations, violations
PY
rg -n "service_expires_at|allow_client_port|refund_cloud_server|refund_cloud_order|CloudServerPlanSnapshot|plan_snapshot|snapshot_plan" bot cloud orders shop core -g '!**/migrations/**'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

第一次未设置 SQLite 的回调 UI 测试会在导入期尝试读取本机 MySQL `SiteConfig` 并打印沙箱拒绝日志，随后已用 `DB_ENGINE=sqlite` 和 `SQLITE_NAME` 重跑通过。极端回调脚本调用客服按钮配置时也会打印同类本机 MySQL 沙箱日志，但脚本最终确认违规列表为空。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 生命周期通知价格文本与返回链复查

### 范围

本轮继续巡检 Shop Django 后端的云资产生命周期唯一到期事实、订单旧到期字段回流、计划快照回流、旧退款入口、废弃 app 误用，以及机器人资产详情、订单详情、续费、换 IP、重装、修改配置返回链和 Telegram `callback_data` 64 字节限制。

### 测试与记录变化

- 本轮未新增运行时代码改动；复查确认生命周期通知计划文本输出仍保持 `价格: <code>金额</code> USDT`。
- 为 `_notice_plan_text()` 现有聚焦测试补充价格行断言，锁定通知计划价格文本不因格式整理发生回归。

### 监工结果

- 机器人详情、续费支付、换 IP 地区、重装、修改配置、资产详情和只读订单详情极端 18 位 ID 回调样本共 100 个，最大 64 字节，无超过 Telegram 限制。
- `CloudAsset.actual_expires_at` 仍是唯一结构化资产到期事实。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 流程字段。
- `CloudAssetDashboardSnapshot` 未恢复派生到期字段。
- 运行代码扫描未发现旧计划模型、旧退款函数名、旧端口入口、`allow_client_port`、`set_cloud_server_port` 或废弃 app 目录回流；`dashboard_api` 仍仅作为现有路由 namespace/公共 helper 命名存在。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/models.py cloud/services.py cloud/provisioning.py cloud/api_orders.py cloud/lifecycle.py cloud/lifecycle_tasks.py orders/payment_scanner.py
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_introspect_<进程>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('asset_expiry_fields', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('default_port', CloudServerOrder._meta.get_field('mtproxy_port').default)"
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_ui_<进程>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_lifecycle_watch_<进程>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_lists_plans_without_creating_order cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_completed_asset_recovery_order_renews_without_reprovisioning --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_notice_text_<进程>.sqlite3 UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_plan_text_shows_configured_execution_time --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_callback_extreme_<进程>.sqlite3 DJANGO_SETTINGS_MODULE=shop.settings UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python - <<'PY'
import django
from decimal import Decimal

django.setup()
from bot.keyboards import (
    append_back_callback,
    cloud_asset_action_callback,
    cloud_asset_detail_callback,
    cloud_detail_callback,
    cloud_order_readonly_detail,
    cloud_server_change_ip_region_menu,
    cloud_server_detail,
    cloud_server_renew_payment,
)

item_id = 999999999999999999
backs = [
    f'cloud:detail:{item_id}:profile:orders:cloud:filter:provisioning:page:{item_id}',
    f'cloud:ad:asset:{item_id}:profile:orders:cloud:filter:provisioning:page:{item_id}',
    f'cad:{item_id}:d:{item_id}:o:provisioning:{item_id}',
    'x' * 120,
]
callbacks = []
for back in backs:
    detail = cloud_detail_callback(item_id, back)
    asset_detail = cloud_asset_detail_callback(item_id, back)
    callbacks.extend([detail, asset_detail])
    markups = [
        cloud_server_detail(item_id, True, True, True, detail, True),
        cloud_server_detail(item_id, True, True, True, asset_detail, True),
        cloud_server_renew_payment(item_id, Decimal('12.3'), Decimal('45.6'), False, asset_detail),
        cloud_server_change_ip_region_menu(item_id, [('ap-southeast-1', '新加坡'), ('us-east-1', '美国')], back_callback=asset_detail),
        cloud_order_readonly_detail(item_id, back),
    ]
    callbacks.extend([
        append_back_callback(f'cloud:assetinit:{item_id}', back),
        append_back_callback(f'exp:a:{item_id}', back),
        cloud_asset_action_callback('renew', item_id, back),
        cloud_asset_action_callback('changeip', item_id, back),
        cloud_asset_action_callback('upgrade', item_id, back),
    ])
    for markup in markups:
        callbacks.extend(button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data)
violations = [(value, len(value.encode())) for value in callbacks if len(value.encode()) > 64]
print('sample_count', len(callbacks))
print('max_len', max(len(value.encode()) for value in callbacks))
assert not violations, violations
PY
rg -n "service_expires_at\\s*=|order\\.(service_expires_at|actual_expires_at)|CloudServerOrder\\([^\\n]*(service_expires_at|actual_expires_at)|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|CloudAssetPlanSnapshot|CloudOrderPlanSnapshot|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|normalize_service_expiry|service_expired_at|allow_client_port|set_cloud_server_port|custom:port:|cloud:ipport:" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。`RetainedIpRenewalUiTestCase` 仍会打印 SimpleTestCase 禁止数据库查询配置的预期日志，最终 44 条通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 旧主代理链接端口一致性收口

### 范围

本轮继续巡检 Shop Django 后端的机器人返回链、Telegram `callback_data` 64 字节限制、云资产生命周期唯一到期事实、废弃 app 回流、旧计划快照、旧退款入口，以及未附加固定 IP 续费和重装时旧主代理链接校验逻辑。

### 运行时变化

- `_validate_reinstall_proxy_link()` 不再支持 `allow_client_port`，重装和未附加固定 IP 续费统一要求用户发送的主代理链接端口与系统记录端口一致。
- 未附加固定 IP 续费提示文案从“系统记录不对时以用户链接端口为准”改为“端口必须与系统记录一致”。
- `prepare_cloud_asset_renewal_with_link()` 和 `prepare_retained_ip_renewal_with_link()` 在生成续费支付订单前校验链接端口；不再把用户链接端口写回订单或资产主端口。
- 保留已有成功路径：历史记录端口为 9528 的无订单资产、公开资产续费和链上支付超时解绑测试继续显式记录 9528，避免把端口一致性收口误判为默认 443 业务失败。

### 监工结果

- 机器人返回链聚焦测试仍通过，未发现本轮端口收口影响资产详情、订单详情、续费、换 IP、重装、修改配置的返回上一层逻辑。
- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 等流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复派生到期字段。
- 未发现旧计划快照、旧退款函数名、`allow_client_port` 或废弃 app 目录回流。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/tests.py bot/keyboards.py cloud/services.py cloud/tests.py orders/tests.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print([f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name])"
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite DB_NAME=/private/tmp/shop_bot_port_strict_20260603.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_cloud_port_strict_pass_<时间戳>.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_address_order_forces_usdt_from_trx_source cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_rejects_link_port_override cloud.tests.CloudServerServicesTestCase.test_retained_ip_renewal_address_order_forces_usdt_from_trx_order cloud.tests.CloudServerServicesTestCase.test_retained_ip_renewal_rejects_link_port_override cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_orders_port_strict_ok_<时间戳>.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_public_asset_renewal_expiry_does_not_claim_unowned_asset --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\\s*=|order\\.(service_expires_at|actual_expires_at)|CloudServerOrder\\([^\\n]*(service_expires_at|actual_expires_at)|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|allow_client_port" bot core orders cloud shop --glob '!**/migrations/**'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。本轮还确认数据库测试临时库应使用 `SQLITE_NAME`，使用 `DB_NAME` 会落回默认 SQLite 测试库并可能遇到只读库残留。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 嵌套资产详情返回链二次压缩

### 范围

本轮继续巡检 Shop Django 后端的机器人返回链、Telegram `callback_data` 64 字节限制、云资产生命周期唯一到期事实、废弃 app 回流、旧计划快照和旧退款入口。

### 运行时变化

- `cloud_asset_detail_callback()` 在收到已经压缩过的资产详情返回路径时，会先判断是否仍超过 64 字节；若超限则继续把内部返回来源压缩为短动作码。
- 极端 18 位资产 ID、18 位订单 ID、长订单筛选来源和 18 位页码组合下，资产详情返回资产详情不再保留超长嵌套路径，而是降级为 `cad:<资产ID>:d:<订单ID>`。
- 新增聚焦测试覆盖 `cad:<资产ID>:d:<订单ID>:o:<筛选>:<页码>` 再次进入资产详情时的二次压缩，防止后续恢复超长回调。

### 监工结果

- 18 位 ID 边界样本验证：资产详情回调压缩后为 43 字节，详情页续费、换 IP、重装、修改配置和返回按钮最大 54 字节。
- `CloudAsset` 仍只有 `actual_expires_at` 作为结构化资产到期字段。
- `CloudServerOrder` 未恢复 `service_expires_at` 或 `actual_expires_at`，仅保留 `renew_grace_expires_at` 等流程时间字段。
- `CloudAssetDashboardSnapshot` 未恢复派生到期字段。
- 未发现旧计划快照、旧退款函数名或废弃 app 目录回流；扫描命中的 `ip_recycle_at=asset.actual_expires_at` 仍是固定 IP 回收计划派生时间，不是订单服务到期字段恢复。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py cloud/services.py cloud/provisioning.py cloud/api_orders.py cloud/api_assets.py cloud/api_asset_edit.py cloud/lifecycle.py cloud/lifecycle_tasks.py
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite DB_NAME=/private/tmp/shop_bot_callbacks_nested_20260603.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache DB_ENGINE=sqlite DB_NAME=/private/tmp/shop_lifecycle_nested_20260603.sqlite3 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_lists_plans_without_creating_order cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_completed_asset_recovery_order_renews_without_reprovisioning --noinput --verbosity 1
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print([f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print([f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name])"
UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=shop.settings UV_CACHE_DIR=/Users/a399/Desktop/data/shop/.uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python - <<'PY'
import django
django.setup()
from bot.keyboards import cloud_asset_detail_callback, cloud_server_detail
item_id = 999999999999999999
backs = [
    f'cloud:detail:{item_id}:profile:orders:cloud:filter:provisioning:page:{item_id}',
    f'cad:{item_id}:cloud:detail:{item_id}:profile:orders:cloud:filter:provisioning:page:{item_id}',
    f'cad:{item_id}:d:{item_id}:o:provisioning:{item_id}',
]
for back in backs:
    callback = cloud_asset_detail_callback(item_id, back)
    callbacks = [button.callback_data for row in cloud_server_detail(item_id, True, True, True, callback, True).inline_keyboard for button in row if button.callback_data]
    assert len(callback.encode()) <= 64
    assert all(len(value.encode()) <= 64 for value in callbacks)
PY
rg -n "service_expires_at\\s*=|actual_expires_at\\s*=.*order\\.|order\\..*actual_expires_at|CloudLifecyclePlan\\b|CloudNoticePlan\\b|CloudAutoRenewPlan\\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\\\"]refunded['\\\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 生命周期与回调返回链巡检

### 范围

本轮继续监工 Shop Django 后端，重点检查云资产生命周期唯一到期事实、后台任务中心、机器人返回链、Telegram `callback_data` 64 字节限制、废弃 app 回流、旧计划快照和旧退款入口。

### 监工结果

- 当前工作树起始干净，最近提交为 `9b8b694 记录本地 MySQL 权限修复`。
- 未修改运行代码；本轮仅追加中文版本记录。
- `manage.py check` 通过，关键 bot/cloud 模块编译通过。
- 结构化字段确认：`CloudAsset` 只有 `actual_expires_at` 作为资产到期事实；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`，仅保留 `renew_grace_expires_at` 等流程时间；`CloudAssetDashboardSnapshot` 无到期字段。
- `INSTALLED_APPS` 未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`；仓库根部也未发现这些废弃 app 目录回流。
- 收窄扫描未发现旧计划快照、旧退款函数名或订单服务到期字段恢复；命中的固定 IP 回收和迁移到期写入均仍落在 `CloudAsset.actual_expires_at` 或生命周期派生字段上。
- bot 回调测试确认资产详情、订单详情、续费、换 IP、重装、修改配置等返回链仍能保持在 Telegram 64 字节限制内。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py bot/handlers.py bot/keyboards.py cloud/services.py cloud/bootstrap.py cloud/api.py cloud/api_tasks.py cloud/lifecycle_execution.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_bot_callbacks_auto_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_task_center_auto_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_lifecycle_auto_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_lists_plans_without_creating_order cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_completed_asset_recovery_order_renews_without_reprovisioning cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_sync_aws_retained_ip_preserves_existing_asset_user --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_orders_auto_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_public_asset_renewal_expiry_does_not_claim_unowned_asset orders.tests.ChainPaymentScannerTestCase.test_expired_asset_renewal_payment_unbinds_asset_for_retry --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_introspect_auto_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; print('retired_apps', [app for app in settings.INSTALLED_APPS if app.split('.')[0] in retired]); from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('asset_expiry_fields', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('order_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('snapshot_expiry_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('default_port', CloudServerOrder._meta.get_field('mtproxy_port').default)"
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
```

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。第一次未指定 SQLite 的聚焦测试会尝试连接本机 MySQL 并被沙箱拒绝；已改用 `DB_ENGINE=sqlite` 和 `SQLITE_NAME` 重跑通过。SQLite 测试仍会打印不支持 `db_comment` 的预期系统警告；bot SimpleTestCase 仍会打印禁止数据库查询配置的预期日志，最终通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 机器人返回链与生命周期字段巡检

### 范围

本轮继续监工 Shop Django 后端，重点复核云资产生命周期唯一到期事实、后台任务中心状态统计、机器人资产详情/订单详情/续费/换 IP/重装/修改配置返回链、Telegram `callback_data` 64 字节限制、旧计划快照、旧退款入口和废弃 app 回流。

### 监工结果

- 当前工作树起始干净，最近提交为 `6cdd789 修复重装提交返回链`。
- 未发现需要修改运行代码的问题；本轮仅追加中文版本记录。
- `CloudAsset` 仍只有 `actual_expires_at` 作为资产到期事实；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段，仅保留风险布尔字段 `risk_expired`。
- 废弃 app `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz` 未安装；`dashboard_api` 命名空间和 helper 命中仍属于当前后台 API 聚合。
- 收窄扫描未发现旧退款函数名、旧退款状态、旧计划快照或订单服务到期字段回流；命中的 `ip_recycle_at=asset.actual_expires_at` 仍是固定 IP 回收派生时间。
- 动态枚举 109 个极端 18 位 ID 和嵌套来源 callback，覆盖资产详情、订单详情、续费、换 IP、重装、修改配置、自动续费、IP 查询结果和重装提交结果；最大 63 字节，无超过 Telegram 64 字节限制。
- 任务中心聚焦测试继续覆盖通知、生命周期、自动续费状态统计；未发现后台总览漏报或失败状态不一致的新问题。

### 验证

本地已通过:

```bash
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py check
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python -m py_compile bot/keyboards.py bot/handlers.py bot/tests.py cloud/task_center.py cloud/tests_task_center.py cloud/api_tasks.py cloud/lifecycle_tasks.py cloud/lifecycle_execution.py cloud/provisioning.py cloud/asset_expiry.py
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings
UV_CACHE_DIR=/private/tmp/shop-uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py shell -c "from django.apps import apps; from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; installed={c.label for c in apps.get_app_configs()}; print('retired_installed', sorted(retired & installed)); print('CloudAsset expiry fields', [f.name for f in CloudAsset._meta.fields if 'expires_at' in f.name]); print('CloudServerOrder has actual_expires_at', any(f.name=='actual_expires_at' for f in CloudServerOrder._meta.fields)); print('CloudServerOrder has service_expires_at', any(f.name=='service_expires_at' for f in CloudServerOrder._meta.fields)); print('CloudAssetDashboardSnapshot expiry fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or f.name == 'risk_expired']);"
UV_CACHE_DIR=/private/tmp/shop-uv-cache uv run python manage.py makemigrations --check --dry-run
git diff --check
```

`makemigrations --check --dry-run` 仍出现本地沙箱无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告，但最终结果为 `No changes detected`。第一次动态 callback 枚举脚本因本地参数名写错失败，按当前 `cloud_ip_query_result(result_items, renewable_items, ...)` 签名修正后通过。SQLite 测试仍会打印不支持 `db_comment` 的预期 warning；bot SimpleTestCase 仍会打印配置读取被禁止数据库访问拦截的既有容错日志；mocked postcheck 异常日志仍为既有覆盖输出，最终测试均通过。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 自动优化复查入口收敛

### 背景

用户反馈自动化分散在多个会话后，复查和修改方向都很麻烦。本轮将自动化上下文收敛到仓库内固定中文文件，减少跨会话丢失上下文的问题。

### 修改

- 新增 `docs/auto-optimization-control.md`，作为后续自动化每轮必须先读取的任务方向、红线、巡检清单和下一轮优先事项入口。
- 新增 `docs/auto-optimization-latest.md`，作为最近一轮状态摘要，便于快速复查，不需要翻完整版本记录。
- 新增根目录 `AGENTS.md`，定义 Codex CLI 收到 `continue to next task` 后的读取顺序、执行边界、验证和提交流程。
- 新增根目录 `TODO.md`，把持续优化方向拆成可验证、可领取的小任务，方便短工人会话循环执行。
- 准备同步更新 `shop` 自动化提示词，要求每轮先读取上述文件和版本记录末尾，再执行巡检、修复、验证、记录和提交。

### 验证

- 本轮为中文文档和自动化提示词调整，不涉及运行代码逻辑。

### 剩余风险

- 需要观察下一轮自动化是否稳定覆盖更新 `docs/auto-optimization-latest.md`。
- 代码层面的持续巡检仍由后续每 10 分钟自动化执行。

## 2026-06-03 自动续费重试任务中心漏报修复

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、当前 git 状态和最近提交。重点复核云资产生命周期唯一到期事实、废弃 app 回流、旧计划快照/旧退款入口、机器人返回链、Telegram `callback_data` 64 字节限制、后台任务中心和自动续费重试状态。

### 发现

- `CloudAsset.actual_expires_at` 仍是资产到期唯一事实；运行代码未恢复 `CloudServerOrder.actual_expires_at`、`CloudServerOrder.service_expires_at` 或计划快照表。
- 搜索未发现旧退款函数名、旧计划快照、废弃 runtime app 直接回流。
- 机器人续费、换 IP、重装、修改配置和钱包支付短回调用例继续通过，未发现超过 Telegram 64 字节限制的新问题。
- 后台任务中心自动续费 section 只聚合计划项和巡检日志，没有直接纳入 `CloudAutoRenewRetryTask` 持久化重试队列；当存在“等待充值后重试”的待重试任务且巡检日志缺失或不在当前窗口时，总览可能低估自动续费待处理任务。

### 修改

- `cloud/task_center.py` 新增 `CloudAutoRenewRetryTask` 聚合，待重试任务以 `retry_pending` 进入自动续费 section，失败任务以 `retry_failed` 进入最近失败统计。
- 自动续费 section 的 `total`、`active`、`warning`、`failed` 和 `status_counts` 纳入持久化重试任务，并优先展示重试任务的下一次检查时间、失败原因、订单路径和 IP。
- `cloud/tests.py` 新增 `test_task_center_counts_pending_auto_renew_retry_tasks`，覆盖没有巡检日志时待充值重试任务仍出现在任务中心总览。
- 覆盖更新 `docs/auto-optimization-latest.md`。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache uv run python -m py_compile cloud/task_center.py cloud/api_tasks.py cloud/lifecycle.py bot/keyboards.py bot/handlers.py orders/payment_scanner.py
UV_CACHE_DIR=/private/tmp/uv-cache DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_task_center_counts_pending_auto_renew_retry_tasks cloud.tests.CloudServerServicesTestCase.test_auto_renew_retry_task_waits_for_recharge_then_retries cloud.tests.CloudServerServicesTestCase.test_auto_renew_task_detail_includes_due_retry_and_fallback_items cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_tasks_executes_due_retry_and_fallback_queue cloud.tests.CloudServerServicesTestCase.test_tasks_overview_exposes_click_paths_for_entry_and_order_number cloud.tests.CloudOrderStatusDashboardSyncTestCase bot.tests.RetainedIpRenewalUiTestCase orders.tests.ChainPaymentScannerTestCase --verbosity=2
UV_CACHE_DIR=/private/tmp/uv-cache uv run python manage.py makemigrations --check --dry-run
git diff --check
```

结果：`manage.py check`、编译检查、74 个 SQLite 聚焦测试和 `git diff --check` 通过；`makemigrations --check --dry-run` 输出 `No changes detected`，但本地沙箱仍会打印无法连接 `127.0.0.1` MySQL 的迁移历史一致性警告。SQLite 测试仍会打印不支持 `db_comment` 的预期 warning；bot SimpleTestCase 仍会打印既有配置读取容错日志；mocked postcheck 异常日志为既有覆盖输出。

### 工作树说明

- 本轮开始时工作树已存在自动化文档相关未提交改动；运行期间又出现多处我未触碰的路由/文档/测试路径改动，例如 `shop/admin_urls.py`、`shop/urls.py`、`shop/dashboard_urls.py`、`ARCHITECTURE.md`、`DEVELOPMENT.md`、`cloud/tests_task_center.py` 和 `/api/dashboard` 到 `/api/admin` 的测试路径调整。
- 本轮提交只暂存任务中心修复、对应聚焦测试和本轮中文记录，不回退其它未提交改动。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。

## 2026-06-03 机器人返回链复查

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md` 和 `TODO.md`。按 `TODO.md` 第一项，聚焦复查机器人资产详情、订单详情、续费、钱包支付续费、换 IP、重装、修改配置的返回上一层行为，并确认 Telegram `callback_data` 不超过 64 字节。

### 监工结果

- 本轮开始时工作树已有其它未提交路由、文档和测试路径改动，例如 `shop/admin_urls.py`、`shop/auth_urls.py`、`shop/urls.py`、`shop/dashboard_urls.py` 删除，以及多份架构/迁移文档和测试文件改动；本轮未回退、未覆盖这些既有改动。
- 未发现需要修改运行代码的问题；本轮只更新自动化中文记录和 TODO 状态。
- `bot.keyboards` 对订单详情、资产详情、续费支付、换 IP、重装、修改配置、自动续费和二级操作的回调压缩逻辑继续生效。
- `bot.handlers` 仍注册并解析 `cad/csd/d/r/i/ri/u/p/im/ir/ai/ar/ac/au/ao/af/arp/rnp` 等短回调，未发现“键盘生成短码但处理器不识别”的返回链断点。
- 动态枚举极端 18 位资产/订单 ID、长订单筛选来源、资产详情嵌套订单详情、列表分页和 120 字节异常来源，共 306 个回调；最大长度 64 字节，超限 0。
- 红线扫描未发现 `CloudServerOrder.service_expires_at`、订单侧 `actual_expires_at`、旧计划快照、旧退款函数名或废弃 runtime app 回流；命中的 `ip_recycle_at=asset.actual_expires_at` 和 `CloudAsset.actual_expires_at` 写入仍属于固定 IP 回收或资产侧唯一到期事实。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/keyboards.py bot/handlers.py bot/tests.py cloud/services.py cloud/provisioning.py cloud/api_orders.py cloud/api_assets.py cloud/lifecycle.py orders/payment_scanner.py
DJANGO_SETTINGS_MODULE=shop.settings DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python - <<'PY'
# 动态枚举 bot 关键键盘和回调 helper，确认 306 个 callback_data 最大 64 字节、超限 0。
PY
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
```

`RetainedIpRenewalUiTestCase` 共 49 个测试通过。测试中仍会打印 SimpleTestCase 禁止数据库查询配置的既有容错日志和 mocked postcheck 异常日志，最终结果通过。动态脚本首次未指定 SQLite 时触发本地 MySQL 沙箱拒绝日志，但命令最终通过；已使用 `DJANGO_TEST_SQLITE=1` 重跑完成干净验证。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。
- 工作树仍保留其它未提交路由/文档/测试路径改动，本轮不纳入提交。

## 2026-06-03 云资产生命周期复查

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md` 和 `TODO.md`。按 `TODO.md` 第一项未完成任务，聚焦确认 `CloudAsset.actual_expires_at` 仍是唯一资产到期事实，订单表、计划快照、旧退款入口和废弃 runtime app 没有回流。

### 监工结果

- 本轮开始时工作树已有其它未提交路由、文档和测试路径改动，例如 `shop/admin_urls.py`、`shop/auth_urls.py`、`shop/urls.py`、`shop/dashboard_urls.py` 删除，以及多份架构/迁移文档和测试文件改动；本轮未回退、未覆盖这些既有改动。
- 未发现需要修改运行代码的问题；本轮只更新自动化中文记录和 TODO 状态。
- 字段内省确认 `CloudAsset` 的到期字段只有 `actual_expires_at`；`CloudServerOrder` 没有 `actual_expires_at` 或 `service_expires_at`，仅保留 `renew_grace_expires_at` 等流程时间字段；`CloudAssetDashboardSnapshot` 没有派生到期时间字段，仅保留风险布尔字段 `risk_expired`。
- `INSTALLED_APPS` 未安装 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`，仓库根部也未发现这些废弃 runtime app 目录回流。
- 关键字扫描未发现旧计划快照类名、旧退款函数名、退款状态或订单服务到期字段恢复。命中的 `ip_recycle_at=asset.actual_expires_at`、`CloudAsset.actual_expires_at` 写入和 `orders.*.expired_at` 仍分别属于固定 IP 回收、资产侧唯一到期事实和支付订单超时，不是订单服务到期事实恢复。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/models.py cloud/asset_expiry.py cloud/lifecycle.py cloud/lifecycle_schedule.py cloud/lifecycle_tasks.py cloud/services.py cloud/provisioning.py orders/payment_scanner.py orders/models.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.apps import apps; from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; installed={c.label for c in apps.get_app_configs()}; print('retired_installed', sorted(retired & installed)); print('CloudAsset expiry fields', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('CloudServerOrder actual_expires_at', any(f.name=='actual_expires_at' for f in CloudServerOrder._meta.fields)); print('CloudServerOrder service_expires_at', any(f.name=='service_expires_at' for f in CloudServerOrder._meta.fields)); print('CloudServerOrder expiry-like fields', [f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('CloudAssetDashboardSnapshot expiry-like fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name or f.name=='risk_expired'])"
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_lifecycle_audit_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_rejects_removed_service_expiry_field cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_delete_plan_view cloud.tests.CloudServerServicesTestCase.test_update_cloud_asset_expiry_refreshes_order_lifecycle cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_lists_plans_without_creating_order cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_completed_asset_recovery_order_renews_without_reprovisioning cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_preserves_existing_unattached_ip_due_time cloud.tests.CloudServerServicesTestCase.test_sync_aws_retained_ip_preserves_existing_asset_user --noinput --verbosity 1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_orders_lifecycle_audit_20260603.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_public_asset_renewal_expiry_does_not_claim_unowned_asset orders.tests.ChainPaymentScannerTestCase.test_expired_asset_renewal_payment_unbinds_asset_for_retry --noinput --verbosity 1
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at|allow_client_port" bot core orders cloud shop --glob '!**/migrations/**'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
```

`manage.py check`、编译检查、字段内省、8 个生命周期/同步聚焦测试和 2 个支付扫描聚焦测试均通过。SQLite 测试仍会打印不支持 `db_comment` 的预期 warning；`makemigrations --check --dry-run` 仍因本地默认 MySQL 被沙箱拒绝打印一致性检查 warning，但最终输出 `No changes detected`。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。
- 工作树仍保留其它未提交路由/文档/测试路径改动，本轮不纳入提交。

## 2026-06-03 后台任务中心 pending 任务漏报修复

### 范围

本轮继续监工 Shop Django 后端，按 `TODO.md` 后台任务中心和状态统计复查执行，聚焦云资产同步 worker、通知计划、自动续费、生命周期计划的状态统计、失败状态、重试状态和后台总览可观测性。

### 发现与修改

- 生命周期和通知 section 原本只直接聚合 `claimed` 和近期 `failed` 的持久化任务；如果 `CloudLifecycleTask` 或 `CloudNoticeTask` 已经处于到期 `pending` 状态，但计划 bundle 或历史日志暂时缺失，后台任务中心会漏报这些待执行/待通知任务。
- `cloud/task_center.py` 已将到期 `CloudLifecycleTask.STATUS_PENDING` 和 `CloudNoticeTask.STATUS_PENDING` 纳入任务中心聚合，并把 pending/claimed DB 任务计入 `active`、`total`、`status_counts` 和 `items`。
- `cloud/tests_task_center.py` 已补充无计划项/无通知日志时，到期 pending 生命周期任务和通知任务仍出现在后台任务中心的聚焦测试。
- `TODO.md` 勾选后台任务中心和状态统计复查。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/task_center.py cloud/tests_task_center.py cloud/api_tasks.py cloud/lifecycle_tasks.py cloud/sync_jobs.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
git diff --check
```

`manage.py check`、编译检查、14 个任务中心 SQLite 聚焦测试、`makemigrations --check --dry-run` 和 `git diff --check` 均通过。SQLite 测试仍会打印不支持 `db_comment` 的预期 warning；`makemigrations --check --dry-run` 仍因本地默认 MySQL 被沙箱拒绝打印一致性检查 warning，但最终输出 `No changes detected`。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。
- 工作树仍保留其它未提交路由/文档/测试路径改动，本轮不纳入提交。

## 2026-06-03 真机测试计划复查收尾

- 本轮按 `TODO.md` 真机测试计划复查执行；没有获得用户明确授权真实云资源成本，因此未执行真实云资源创建、删除、IP 变更、附加 IP / 固定 IP 变更、续费、真实支付或链上广播。
- 已在 `docs/real-machine-test-report.md` 新增 2026-06-03 计划复查记录，明确后续执行前置条件和脱敏要求。
- 已覆盖更新 `docs/auto-optimization-latest.md`，并将 `TODO.md` 固定任务全部勾选；下一轮如无新增任务，按固定巡检清单做只读巡检。
- 本轮验证通过：`manage.py check`、任务中心/数据库测试文件 Python 编译、字段内省、红线关键字扫描、废弃 app 目录扫描和 `git diff --check`。
- 剩余风险：未跑完整测试套件；未执行真实 Telegram 点击；所有真机验证仍需用户明确授权真实云资源成本后单独记录。

## 2026-06-03 本地数据库差异复查

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md` 和 `TODO.md`。按 `TODO.md` 第一项未完成任务，聚焦确认默认 MySQL/MariaDB 环境和 SQLite 聚焦测试不会隐藏字段、迁移或测试行为差异。

### 发现

- 本轮开始时工作树已有其它未提交路由、文档和测试路径改动，例如 `shop/admin_urls.py`、`shop/auth_urls.py`、`shop/urls.py`、`shop/dashboard_urls.py` 删除，以及后台 API 路径从 `/api/dashboard` 到 `/api/admin` 的测试调整；本轮未回退、未覆盖这些既有改动。
- `manage.py check` 在默认 MySQL 配置下通过。
- 默认 MySQL/MariaDB 的 `migrate --plan` 在当前沙箱会失败：Django MySQL 后端字段检查需要读取服务器版本特性，连接 `127.0.0.1:3306` 时被沙箱拒绝，不能据此判断迁移图异常。
- `DJANGO_TEST_SQLITE=1` 的 `migrate --plan` 可生成完整迁移计划，但 SQLite 会打印不支持 `db_comment` 和 `db_table_comment` 的预期 warning。
- `DJANGO_TEST_SQLITE=1` 字段内省确认：`CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- `core.tests.SiteConfigCacheTestCase.test_set_invalidates_async_config_cache` 在内存 SQLite 下失败，原因是 `TestCase` 外层事务锁住 `core_site_config`，`async_to_sync(get_config)` 通过异步连接读取时触发 SQLite 表锁并返回默认值；这会让 SQLite 聚焦测试误报配置缓存行为差异。

### 修改

- `core/tests.py` 将 `SiteConfigCacheTestCase` 从 `TestCase` 调整为 `TransactionTestCase`，让跨同步/异步连接的缓存失效测试在 SQLite 下使用可见的已提交数据，保留原测试意图。
- 覆盖更新 `docs/auto-optimization-latest.md`。
- `TODO.md` 勾选本地数据库差异复查。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile core/tests.py shop/settings.py core/models.py core/cache.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test core.tests --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.conf import settings; from django.db import connection; from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; print('engine', settings.DATABASES['default']['ENGINE']); print('name', settings.DATABASES['default']['NAME']); print('vendor', connection.vendor); print('asset_expiry_fields', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('order_removed_expiry_fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'actual_expires_at','service_expires_at'}]); print('snapshot_expiry_like_fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name or f.name=='actual_expires_at'])"
```

结果：`manage.py check`、编译检查、SQLite `core.tests` 14 个测试、SQLite/默认 `makemigrations --check --dry-run` 和字段内省均通过；默认 `makemigrations` 仍有本地 MySQL 沙箱连接 warning 但最终显示 `No changes detected`。默认 `migrate --plan` 因当前沙箱禁止连接本地 MySQL 而失败，已记录为环境限制。SQLite `migrate --plan` 会打印大量 `db_comment` warning，但可生成完整迁移计划。

### 剩余风险

- 本轮未在真实 MySQL/MariaDB 上执行 `migrate --plan`，因为当前沙箱禁止连接 `127.0.0.1:3306`。
- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 真机测试仍需在用户明确授权真实云资源成本后，单独按中文报告记录云资源 ID 脱敏结果。
- 工作树仍保留其它未提交路由/文档/测试路径改动，本轮不纳入提交。

## 2026-06-03 固定巡检与后台 API 路由拆分验证

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md` 和 `TODO.md`。由于 `TODO.md` 固定任务已全部勾选，本轮按固定巡检清单执行，重点验证工作树中既有的后台 API 路由拆分、云资产生命周期唯一到期事实、机器人返回链、任务中心状态统计和废弃 app 红线。

### 发现

- 本轮开始时工作树已有未提交改动：`shop/dashboard_urls.py` 删除，新增 `shop/admin_urls.py` 和 `shop/auth_urls.py`，`shop/urls.py` 改为只挂载 `/api/csrf/`、`/api/auth/` 和 `/api/admin/`，同时测试路径和多份架构文档已切到 `/api/admin/`。
- 本轮未回退、未覆盖这些既有改动，也未修改运行代码；只更新自动化中文记录。
- 路由契约验证确认 `/api/auth/login`、`/api/auth/refresh`、`/api/admin/user/info`、`/api/admin/dashboard/overview/` 和 `/api/admin/cloud-assets/sync-jobs/metrics/` 可解析，旧 `/api/dashboard/*`、`/api/admin/auth/login` 和根 `/api/users/` 不再解析。
- 任务中心 14 个聚焦测试继续通过，未发现生命周期、通知或自动续费 section 的 pending/failed 任务漏报回归。
- 机器人返回链 49 个聚焦测试继续通过，续费、钱包支付、换 IP、重装、修改配置和嵌套返回路径仍满足 Telegram `callback_data` 64 字节限制。
- 字段内省确认废弃 app 未安装；`CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 红线关键字扫描未发现旧计划快照、旧退款函数名或废弃 runtime app 回流；命中项仍为资产侧到期事实或固定 IP 回收时间同步。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/tests.py cloud/tests.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.apps import apps; from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; installed={c.label for c in apps.get_app_configs()}; print('retired_installed', sorted(retired & installed)); print('CloudAsset expiry fields', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('CloudServerOrder actual_expires_at', any(f.name=='actual_expires_at' for f in CloudServerOrder._meta.fields)); print('CloudServerOrder service_expires_at', any(f.name=='service_expires_at' for f in CloudServerOrder._meta.fields)); print('CloudServerOrder expiry-like fields', [f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('CloudAssetDashboardSnapshot expiry-like fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name or f.name=='risk_expired'])"
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

结果：`manage.py check`、编译检查、10 个路由/后台鉴权测试、14 个任务中心测试、49 个机器人返回链测试、字段内省和 `git diff --check` 均通过。SQLite 测试仍会打印不支持 `db_comment` / `db_table_comment` 的预期 warning；`RetainedIpRenewalUiTestCase` 仍会打印 SimpleTestCase 数据库访问容错日志和 mocked postcheck 异常日志；默认 `makemigrations --check --dry-run` 仍因当前沙箱无法连接本地 MySQL 打印迁移历史一致性 warning，但最终输出 `No changes detected`。

### 剩余风险

- 工作树仍保留本轮开始前已存在的未提交路由、测试和文档差异，本轮只验证、不替用户整体提交。
- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。

## 2026-06-03 后台 API 路由拆分收尾

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md` 和 `TODO.md`。由于 `TODO.md` 固定任务已全部勾选，本轮按固定巡检清单执行，并收尾此前工作树中已存在的后台 API 路由拆分改动。

### 发现与修改

- 本轮开始时工作树已有未提交路由拆分差异：旧 `shop/dashboard_urls.py` 删除，新增 `shop/admin_urls.py` 和 `shop/auth_urls.py`，`shop/urls.py` 改为只挂载 `/api/csrf/`、`/api/auth/` 和 `/api/admin/`，测试与中文架构文档也已切到 `/api/admin/`。
- 本轮将这组差异作为同一后台 API 收口任务处理，没有回退前序改动。
- `shop/auth_urls.py` 承接登录、登出、刷新和权限码接口，统一暴露在 `/api/auth/`。
- `shop/admin_urls.py` 承接后台业务 API，统一暴露在 `/api/admin/`。
- 为减少前端或脚本滞后造成的回归，在 `/api/admin/` 下保留旧后台业务兼容别名 `task-list/` 和 `plan-settings/`。
- `bot.tests.ApiPrefixContractTestCase` 新增路由契约覆盖：确认 `/api/csrf/`、`/api/auth/login`、`/api/auth/refresh`、`/api/admin/user/info`、`/api/admin/dashboard/overview/`、`/api/admin/cloud-assets/sync-jobs/metrics/`、`/api/admin/task-list/` 和 `/api/admin/plan-settings/` 可解析；确认旧 `/api/dashboard/*`、`/api/admin/auth/login` 和根 `/api/users/` 不再解析。
- 中文文档同步把后台聚合路由从 `shop/dashboard_urls.py` 改为 `shop/admin_urls.py`，并记录后台业务 API 统一使用 `/api/admin/` 前缀。

### 固定巡检结论

- 字段内省确认废弃 app 未安装；`CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 红线关键字扫描未发现订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app 回流。命中的 `ip_recycle_at=asset.actual_expires_at`、`CloudAsset.actual_expires_at` 写入和 `_asset_expires_at` 临时属性仍属于资产侧唯一到期事实或固定 IP 回收同步。
- 机器人返回链 49 个聚焦测试继续通过，资产详情、订单详情、续费、钱包支付、换 IP、重装、修改配置和嵌套返回路径仍满足 Telegram `callback_data` 限制。
- 任务中心 14 个聚焦测试继续通过，未发现生命周期、通知或自动续费 section 的 pending/failed 任务漏报回归。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/tests.py cloud/tests.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.apps import apps; from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; installed={c.label for c in apps.get_app_configs()}; print('retired_installed', sorted(retired & installed)); print('CloudAsset expiry fields', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('CloudServerOrder actual_expires_at', any(f.name=='actual_expires_at' for f in CloudServerOrder._meta.fields)); print('CloudServerOrder service_expires_at', any(f.name=='service_expires_at' for f in CloudServerOrder._meta.fields)); print('CloudServerOrder expiry-like fields', [f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('CloudAssetDashboardSnapshot expiry-like fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name or f.name=='risk_expired'])"
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

结果：`manage.py check`、编译检查、10 个路由/后台鉴权测试、14 个任务中心测试、49 个机器人返回链测试、字段内省和 `git diff --check` 均通过。SQLite 测试仍会打印不支持 `db_comment` / `db_table_comment` 的预期 warning；`RetainedIpRenewalUiTestCase` 仍会打印 SimpleTestCase 数据库访问容错日志和 mocked postcheck 异常日志；默认 `makemigrations --check --dry-run` 仍因当前沙箱无法连接本地 MySQL 打印迁移历史一致性 warning，但最终输出 `No changes detected`。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端若仍调用旧 `/api/dashboard/` 或根 `/api/` 后台业务前缀，需要同步切换到 `/api/auth/` 与 `/api/admin/`。

## 2026-06-03 固定巡检收口记录

### 范围

本轮按自动优化规则继续监工 Shop Django 后端，读取自动化记忆、git 状态、最近提交、控制文档、最新状态、版本记录末尾、`AGENTS.md`、`TODO.md` 和 `django-shop-backend` 技能。`TODO.md` 已全部勾选，因此执行固定巡检并更新中文记录。

### 结果

- 本轮未修改运行代码，只更新自动化记录文档。
- 后端检查、编译检查、路由/后台鉴权测试、任务中心测试、机器人返回链测试、字段内省、SQLite 迁移计划、红线扫描、废弃 app 目录扫描、前端只读旧前缀扫描和 `git diff --check` 均符合预期。
- 云资产生命周期仍只以 `CloudAsset.actual_expires_at` 作为唯一资产到期事实；订单表未恢复 `actual_expires_at` 或 `service_expires_at`；计划快照和旧退款入口未回流；废弃 runtime app 未重新安装。
- 默认 MySQL `migrate --plan` 仍因当前沙箱禁止连接 `127.0.0.1:3306` 失败，属于环境限制。
- 前端源码未检出旧 API 前缀；`/Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 文字说明残留，本轮未跨仓库修改。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/handlers.py bot/api.py bot/tests.py cloud/api_tasks.py cloud/task_center.py cloud/lifecycle_tasks.py cloud/lifecycle_schedule.py cloud/sync_jobs.py cloud/services.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
git diff --check
```

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端文档仍有旧 `/api/dashboard/` 描述残留；源码只读扫描未发现旧调用。

## 2026-06-03 固定巡检与前端文档残留记录

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md`、`TODO.md` 和 `django-shop-backend` 技能。由于 `TODO.md` 固定任务已全部勾选，本轮按固定巡检清单执行，不做真实云资源、真实支付、链上广播、生产发布或其它不可逆操作。

### 发现

- 本轮开始时工作树干净，最近提交为 `510cabb 校正固定巡检验证记录`。
- 发现通知计划 section 的状态统计仍按 `queue_status` 聚合，会把 `notice_status=failed_retry`、`queue_status=due_now` 的失败重试项统计成 `due_now`，后台总览状态分布与失败状态不一致。
- `manage.py check` 通过，后台 API 路由收口、任务中心统计和机器人返回链聚焦测试继续通过。
- 字段内省确认废弃 app 未安装；`CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段，只保留风险标记字段。
- 红线关键字扫描未发现订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app 回流。命中项仍为资产侧唯一到期事实、固定 IP 回收时间同步或 `_asset_expires_at` 临时属性。
- 机器人返回链 49 个聚焦测试继续通过，资产详情、订单详情、续费、钱包支付、换 IP、重装、修改配置和嵌套返回路径仍满足 Telegram `callback_data` 限制。
- 任务中心 14 个聚焦测试继续通过，未发现生命周期、通知或自动续费 section 的 pending/failed/retry 统计漏报回归。
- 前端已知源码路径 `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src` 未检出旧 `/api/dashboard`、`/api/users`、`/api/task-list` 或 `/api/plan-settings` 调用；`/Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 文字说明残留，本轮仅记录风险，未跨仓库修改。

### 修改

- 覆盖更新 `docs/auto-optimization-latest.md`。
- 在 `docs/refactor-version-record.md` 追加本轮中文巡检记录。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/handlers.py bot/api.py bot/tests.py cloud/api_tasks.py cloud/task_center.py cloud/lifecycle_tasks.py cloud/lifecycle_schedule.py cloud/sync_jobs.py cloud/services.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
rg -n "/api/dashboard|/api/users|/api/task-list|/api/plan-settings" /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src /Users/a399/Desktop/data/vue-shop-admin/docs
git diff --check
```

结果：`manage.py check`、编译检查、10 个路由/后台鉴权测试、14 个任务中心测试、49 个机器人返回链测试、字段内省、默认 `makemigrations --check --dry-run`、SQLite `migrate --plan`、红线扫描、废弃 app 目录扫描和前端源码旧前缀扫描均符合预期。SQLite 检查仍会打印不支持 `db_comment` / `db_table_comment` 的预期 warning；机器人返回链测试仍会打印既有配置读取容错、mocked postcheck 异常和 IP 校验日志；默认 MySQL `migrate --plan` 因当前沙箱无法连接本地 `127.0.0.1:3306` 失败，属于环境限制。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端 `DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 文档说明残留；源码只读扫描未发现旧调用。

## 2026-06-03 固定巡检与前端文档残留记录

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md`、`TODO.md` 和 `django-shop-backend` 技能。由于 `TODO.md` 固定任务已全部勾选，本轮按固定巡检清单执行，只做本地只读扫描、聚焦测试、字段内省、迁移差异检查和中文记录更新。

### 发现

- 本轮开始时工作树干净，最近提交为 `510cabb`「校正固定巡检验证记录」。
- 未发现需要修改运行代码的问题。
- `manage.py check`、路由/后台鉴权测试、任务中心测试、机器人返回链测试和编译检查均通过。
- 字段内省确认废弃 app 未安装；`CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 红线关键字扫描未发现订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app 回流。命中项仍为资产侧唯一到期事实、固定 IP 回收同步或 `_asset_expires_at` 临时属性。
- 机器人返回链 49 个聚焦测试继续覆盖资产详情、订单详情、续费、钱包支付续费、换 IP、重装、修改配置、自动续费和极端长 callback 场景，未发现 Telegram `callback_data` 超 64 字节回归。
- 任务中心 14 个聚焦测试继续覆盖生命周期、通知和自动续费 section 的 pending、failed、retry 和历史失败统计，未发现后台总览漏报回归。
- 前端只读扫描显示 `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src` 未检出旧 `/api/dashboard`、`/api/users`、`/api/task-list` 或 `/api/plan-settings` 调用；`/Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 文字说明残留，本轮只记录风险，未跨仓库修改。
- 默认 MySQL/MariaDB 的 `migrate --plan` 仍因当前沙箱禁止连接 `127.0.0.1:3306` 失败；SQLite `migrate --plan` 可生成完整迁移计划，但会打印不支持 `db_comment` / `db_table_comment` 的预期 warning。

### 修改

- 覆盖更新 `docs/auto-optimization-latest.md`。
- 在 `docs/refactor-version-record.md` 追加本轮中文巡检记录。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/handlers.py bot/api.py bot/tests.py cloud/api_tasks.py cloud/task_center.py cloud/lifecycle_tasks.py cloud/lifecycle_schedule.py cloud/sync_jobs.py cloud/services.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
rg -n "/api/dashboard|/api/users|dashboard_urls|dashboard_api|/api/task-list|/api/plan-settings" bot core orders cloud shop docs TODO.md AGENTS.md --glob '!docs/refactor-version-record.md'
git diff --check
```

结果：`manage.py check`、编译检查、10 个路由/后台鉴权测试、14 个任务中心测试、49 个机器人返回链测试、字段内省、默认 `makemigrations --check --dry-run`、SQLite `migrate --plan`、红线扫描、废弃 app 目录扫描和 `git diff --check` 均符合预期。SQLite 检查仍会打印不支持 `db_comment` / `db_table_comment` 的预期 warning；机器人返回链测试仍会打印既有配置读取容错、mocked postcheck 异常和 IP 校验日志；默认 `migrate --plan` 因当前沙箱无法连接本地 MySQL `127.0.0.1:3306` 失败，已记录为环境限制。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端 `DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 描述残留；源码只读扫描未发现旧调用。

## 2026-06-03 固定巡检与后台状态复核

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md`、`TODO.md` 和 `django-shop-backend` 技能。由于 `TODO.md` 固定任务已全部勾选，本轮按固定巡检清单执行，不做真实云资源、真实支付、链上广播、生产发布或其它不可逆操作。

### 发现

- 本轮开始时工作树干净，最近提交为 `ada3c48`「记录迁移到期事实复核」。
- 未发现需要修改运行代码的问题。
- `CloudAsset.actual_expires_at` 继续作为唯一资产到期事实；字段内省确认 `CloudAsset` 到期字段只有 `actual_expires_at`，`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`，`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 废弃 app 未安装，废弃 app 目录扫描无输出。
- 红线关键字扫描未发现运行代码恢复订单到期字段、旧计划快照、旧退款函数名或把迁移截止时间写回资产到期事实。命中项仍为资产侧唯一到期事实、固定 IP 回收时间同步或 `_asset_expires_at` 临时属性。
- 机器人返回链 49 个聚焦测试继续通过，资产详情、订单详情、续费、钱包支付、换 IP、重装、修改配置和嵌套返回路径仍满足 Telegram `callback_data` 限制。
- 已修复通知计划 `status_counts` 聚合字段，任务中心 14 个聚焦测试继续通过，未发现生命周期、通知或自动续费 section 的 pending/failed 任务漏报回归。
- 云资产迁移/重建/AWS 同步 5 个聚焦测试继续通过，确认迁移截止时间不覆盖资产实际到期事实。
- 后端旧 API 前缀扫描未发现旧路由重新挂载；`dashboard_api` 命中来自 `core.dashboard_api` 共享 helper、历史文档和确认旧入口不可解析的测试。
- 前端源码只读扫描未发现旧 `/api/dashboard`、`/api/users`、`/api/task-list` 或 `/api/plan-settings` 调用；`/Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 文案残留，本轮不跨仓库修改。
- 默认 MySQL/MariaDB 的 `migrate --plan` 仍因当前沙箱禁止连接 `127.0.0.1:3306` 失败；SQLite `migrate --plan` 可生成完整迁移计划，但会打印不支持 `db_comment` / `db_table_comment` 的预期 warning。

### 修改

- 更新 `cloud/task_center.py`，通知计划 section 的 `status_counts` 改为按 `notice_status` 聚合，避免失败重试被队列状态覆盖。
- 更新 `cloud/tests_task_center.py`，补充失败重试通知计划的 `status_counts` 断言。
- 覆盖更新 `docs/auto-optimization-latest.md`。
- 在本文件追加本轮中文巡检记录。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/handlers.py bot/api.py bot/tests.py cloud/api_tasks.py cloud/task_center.py cloud/lifecycle_tasks.py cloud/lifecycle_schedule.py cloud/sync_jobs.py cloud/services.py cloud/provisioning.py cloud/management/commands/sync_aws_assets.py cloud/tests.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_rebuild_source_migration_schedule_preserves_asset_expiry cloud.tests.CloudServerServicesTestCase.test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_order_preserves_asset_expiry_when_migration_due_is_earlier cloud.tests.CloudServerServicesTestCase.test_source_migration_schedule_keeps_asset_actual_expiry cloud.tests.CloudServerServicesTestCase.test_aws_sync_deleted_migration_order_keeps_asset_actual_expiry --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
rg -n "callback_data=|InlineKeyboardButton\(|data=['\"](?:asset|order|renew|ip|rebuild|config)|mon:resd|renew:" bot cloud --glob '!**/migrations/**'
rg -n "/api/dashboard|/api/users|dashboard_urls|dashboard_api" bot core orders cloud shop docs TODO.md AGENTS.md --glob '!docs/refactor-version-record.md'
rg -n "/api/dashboard|/api/users|/api/task-list|/api/plan-settings" /Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src /Users/a399/Desktop/data/vue-shop-admin/docs --glob '!**/node_modules/**' --glob '!**/dist/**' --glob '!**/.git/**'
git diff --check
```

结果：`manage.py check`、编译检查、14 个任务中心测试、10 个路由/后台鉴权测试、49 个机器人返回链测试、5 个迁移/同步聚焦测试、字段内省、默认 `makemigrations --check --dry-run`、SQLite `migrate --plan`、红线扫描、废弃 app 目录扫描、前后端旧前缀扫描和 `git diff --check` 均符合预期。默认 MySQL `migrate --plan` 因当前沙箱无法连接本地 MySQL `127.0.0.1:3306` 失败，已记录为环境限制。SQLite 检查仍会打印不支持 `db_comment` / `db_table_comment` 的预期 warning；机器人返回链测试仍会打印既有配置读取容错、mocked postcheck 异常和 IP 校验日志。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端 `DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 描述残留；源码只读扫描未发现旧调用。

## 2026-06-03 迁移到期事实复核

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md`、`TODO.md` 和 `django-shop-backend` 技能。`TODO.md` 已全部勾选，因此按固定巡检清单复核当前 HEAD 中迁移/重建/AWS 同步路径保留资产到期事实的实现。

### 结果

- 当前 HEAD 已包含 `7d79e98`「保留迁移旧机资产到期事实」、`7f332b6`「修正迁移旧机资产到期文案」和 `0e22e5e`「校正迁移到期巡检记录」。
- 复核确认 `_set_source_migration_expiry`、重建源订单待删除标记和 AWS 同步确认删除迁移旧机时，均不再把 `migration_due_at` 写入 `CloudAsset.actual_expires_at` 或旧兼容 `Server.expires_at`。
- 字段内省确认废弃 app 未安装；`CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 红线扫描未发现运行代码继续把 `migration_due_at` 写入资产 `actual_expires_at` 或 `Server.expires_at`；废弃 app 目录扫描无输出。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/provisioning.py cloud/services.py cloud/tests.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_rebuild_source_migration_schedule_preserves_asset_expiry cloud.tests.CloudServerServicesTestCase.test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_order_preserves_asset_expiry_when_migration_due_is_earlier cloud.tests.CloudServerServicesTestCase.test_source_migration_schedule_keeps_asset_actual_expiry cloud.tests.CloudServerServicesTestCase.test_aws_sync_deleted_migration_order_keeps_asset_actual_expiry --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
git diff --check
```

结果：`manage.py check`、编译检查、14 个任务中心测试、59 个机器人返回链/路由测试、5 个迁移/同步聚焦测试、字段内省、默认 `makemigrations --check --dry-run`、SQLite `migrate --plan` 和 `git diff --check` 均符合预期。默认 MySQL `migrate --plan` 因当前沙箱无法连接本地 MySQL `127.0.0.1:3306` 失败，继续记录为环境限制。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端 `DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 描述残留；本轮未跨仓库修改。

## 2026-06-03 固定巡检与前后端路由残留复核

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md` 和 `TODO.md`。由于 `TODO.md` 固定任务已全部勾选，本轮按固定巡检清单执行，只读复核后台 API 路由拆分后的旧前缀残留、云资产生命周期唯一到期事实、机器人返回链、任务中心状态统计、废弃 app 红线和数据库迁移差异。

### 发现

- 本轮开始时工作树干净，最近提交为 `c439448`「记录后台 API 路由拆分收口」。
- 后端 `manage.py check` 通过，未发现新的导入错误或 Django 配置错误。
- 后端旧 `/api/dashboard` 命中主要来自历史 `CHANGELOG.md`、自动化记录和用于确认旧入口不可解析的 `bot.tests.ApiPrefixContractTestCase`，未发现运行路由重新挂载旧入口。
- 前端仓库只读扫描显示 `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src` 未检出旧 `/api/dashboard` 调用，业务页面主要从 `#/api/admin` 导入；前端 `DEVELOPMENT.md` 仍有「接口主要来自 `/api/admin/` 与 `/api/dashboard/`」这类旧说明残留，本轮不在后端仓库修改。
- 字段内省确认废弃 app 未安装；`CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 红线关键字扫描未发现订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app 回流。命中项仍为资产侧到期事实、固定 IP 回收时间同步或 `_asset_expires_at` 临时属性。
- 资源巡检按钮复核确认云端资源详情使用 16 位短 key，支付扫描资源详情按钮也使用 16 位短 key，`mon:resd:<key>` 不会突破 Telegram `callback_data` 64 字节限制。
- 默认 MySQL/MariaDB 的 `migrate --plan` 仍因当前沙箱禁止连接 `127.0.0.1:3306` 失败；SQLite `migrate --plan` 可生成完整迁移计划，但会打印不支持 `db_comment` / `db_table_comment` 的预期 warning。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/tests.py cloud/tests.py cloud/tests_task_center.py cloud/services.py cloud/lifecycle.py cloud/lifecycle_tasks.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
rg -n "/api/dashboard" /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd --glob '!**/node_modules/**' --glob '!**/dist/**' --glob '!**/.git/**'
git diff --check
```

结果：`manage.py check`、编译检查、10 个路由/后台鉴权测试、14 个任务中心测试、49 个机器人返回链测试、字段内省、默认 `makemigrations --check --dry-run`、SQLite `migrate --plan`、红线扫描、废弃 app 目录扫描、前端源码旧前缀扫描和 `git diff --check` 均符合预期。默认 `migrate --plan` 因当前沙箱无法连接本地 MySQL 失败，已记录为环境限制。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端文档仍有旧 `/api/dashboard` 描述残留；源码只读扫描未发现旧调用。

## 2026-06-03 后台 API 路由拆分提交收口

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md` 和 `TODO.md`。`TODO.md` 固定任务已全部勾选，因此本轮按固定巡检清单执行，并将前序已经验证过的后台 API 路由拆分差异整理为一次提交。

### 处理结果

- 保留 `shop/auth_urls.py` 作为 `/api/auth/` 登录、登出、刷新和权限码路由入口。
- 保留 `shop/admin_urls.py` 作为 `/api/admin/` 后台业务 API 聚合入口。
- `shop/urls.py` 只暴露 `/api/csrf/`、`/api/auth/`、`/api/admin/` 和首页，不再挂载旧 `/api/dashboard/` 与根 `/api/` 后台业务入口。
- 删除旧 `shop/dashboard_urls.py`，避免继续把已退出运行时的 `dashboard_api` 命名空间当作后台聚合事实。
- `bot.tests.ApiPrefixContractTestCase` 覆盖新路由、兼容别名和已移除旧入口；`cloud.tests.py` 与 `cloud/tests_task_center.py` 中的后台 API 请求路径同步切到 `/api/admin/`。
- 中文架构和迁移文档同步改写为 `shop/auth_urls.py`、`shop/admin_urls.py` 与 `/api/admin/` 当前事实。

### 固定巡检结论

- `manage.py check`、编译检查、路由/后台鉴权测试、任务中心测试、机器人返回链测试、字段内省、红线扫描、废弃 app 目录扫描和 `git diff --check` 均通过。
- 字段内省确认废弃 app 未安装；`CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 红线关键字扫描命中仍为资产侧唯一到期事实、固定 IP 回收同步或 `_asset_expires_at` 临时属性，不是订单到期字段、旧计划快照、旧退款逻辑或废弃 runtime app 回流。
- 机器人返回链 49 个聚焦测试继续通过，续费、钱包支付、换 IP、重装、修改配置和嵌套返回路径未突破 Telegram `callback_data` 限制。
- 任务中心 14 个聚焦测试继续通过，未发现生命周期、通知或自动续费 section 的 pending/failed 任务漏报回归。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/tests.py cloud/tests.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
git diff --check
```

`makemigrations --check --dry-run` 仍因当前沙箱无法连接默认 MySQL `127.0.0.1:3306` 打印迁移历史一致性 warning，但最终输出 `No changes detected`。SQLite 测试仍会打印不支持 `db_comment` / `db_table_comment` 的预期 warning；机器人返回链测试仍会打印既有配置读取容错和 mocked postcheck 异常日志。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端若仍调用旧 `/api/dashboard/` 或根 `/api/` 后台业务前缀，需要同步切换到 `/api/auth/` 与 `/api/admin/`。

## 2026-06-03 固定巡检与路由拆分回归复核

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md` 和 `TODO.md`。由于 `TODO.md` 固定任务已全部勾选，本轮按固定巡检清单执行，不做真实云资源、真实支付、链上广播、生产发布或其它不可逆操作。

### 发现

- 本轮开始时工作树干净，最近提交为 `c439448 记录后台 API 路由拆分收口`。
- 未发现需要修改运行代码的问题。
- 后台 API 仍收口到 `/api/auth/` 与 `/api/admin/`；旧 `/api/dashboard/` 和根 `/api/` 后台业务入口未恢复。
- 字段内省确认废弃 app 未安装；`CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 红线关键字扫描未发现订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app 回流。命中的 `_asset_expires_at`、`ip_recycle_at=asset.actual_expires_at` 和 `CloudAsset.actual_expires_at` 写入仍属于资产侧唯一到期事实或固定 IP 回收同步。
- 机器人返回链 49 个聚焦测试继续通过，资产详情、订单详情、续费、钱包支付、换 IP、重装、修改配置和嵌套返回路径仍满足 Telegram `callback_data` 限制。
- 任务中心 14 个聚焦测试继续通过，未发现生命周期、通知或自动续费 section 的 pending/failed 任务漏报回归。
- 废弃 app 目录扫描无输出。

### 修改

- 覆盖更新 `docs/auto-optimization-latest.md`。
- 在 `docs/refactor-version-record.md` 追加本轮中文巡检记录。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/handlers.py bot/api.py bot/tests.py cloud/api_tasks.py cloud/task_center.py cloud/lifecycle_tasks.py cloud/lifecycle_schedule.py cloud/sync_jobs.py cloud/services.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.apps import apps; from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; installed={c.label for c in apps.get_app_configs()}; print('retired_installed', sorted(retired & installed)); print('CloudAsset expiry fields', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('CloudServerOrder actual_expires_at', any(f.name=='actual_expires_at' for f in CloudServerOrder._meta.fields)); print('CloudServerOrder service_expires_at', any(f.name=='service_expires_at' for f in CloudServerOrder._meta.fields)); print('CloudServerOrder expiry-like fields', [f.name for f in CloudServerOrder._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('CloudAssetDashboardSnapshot expiry-like fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name or f.name=='actual_expires_at'])"
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
rg -n "/api/dashboard|/api/users|dashboard_urls|dashboard_api" bot core orders cloud shop docs TODO.md AGENTS.md --glob '!docs/refactor-version-record.md'
rg -n "/api/dashboard|/api/users|/api/task-list|/api/plan-settings" /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/docs /Users/a399/Desktop/data/vue-shop-admin/docs
git diff --check
```

结果：`manage.py check`、修正后的编译检查、10 个路由/后台鉴权测试、14 个任务中心测试、49 个机器人返回链测试、字段内省、SQLite `migrate --plan`、`makemigrations --check --dry-run` 和 `git diff --check` 均通过。SQLite 检查仍会打印不支持 `db_comment` / `db_table_comment` 的预期 warning；机器人返回链测试仍会打印既有配置读取容错、mocked postcheck 异常和 IP 校验日志；默认 `makemigrations --check --dry-run` 仍因当前沙箱无法连接本地 MySQL `127.0.0.1:3306` 打印迁移历史一致性 warning，但最终输出 `No changes detected`；默认 MySQL `migrate --plan` 因同一沙箱网络限制失败。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端若仍调用旧 `/api/dashboard/` 或根 `/api/` 后台业务前缀，需要同步切换到 `/api/auth/` 与 `/api/admin/`。

## 2026-06-03 固定巡检最终收口

### 范围

本轮继续监工 Shop Django 后端，按固定入口读取自动化记忆、git 状态、最近提交、控制文档、最新状态、版本记录末尾、`AGENTS.md`、`TODO.md` 和 `django-shop-backend` 技能。由于 `TODO.md` 已全部完成，本轮执行固定巡检并更新中文记录。

### 结果

- 本轮未修改运行代码，只更新 `docs/auto-optimization-latest.md` 与 `docs/refactor-version-record.md`。
- `manage.py check`、编译检查、10 个路由/后台鉴权测试、14 个任务中心测试、49 个机器人返回链测试、字段内省、SQLite `migrate --plan`、`makemigrations --check --dry-run`、红线扫描、废弃 app 目录扫描、前端存在路径旧前缀扫描和 `git diff --check` 均符合预期。
- 字段内省确认废弃 app 未安装；`CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 默认 MySQL `migrate --plan` 仍因当前沙箱禁止连接 `127.0.0.1:3306` 失败，属于环境限制。
- 前端源码无旧 API 前缀命中；`/Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 文字说明残留，本轮未跨仓库修改。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端文档仍有旧 `/api/dashboard/` 描述残留；源码只读扫描未发现旧调用。

## 2026-06-03 迁移计划保留资产到期事实

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md`、`TODO.md` 和 `django-shop-backend` 技能。由于 `TODO.md` 固定任务已全部勾选，本轮按固定巡检清单执行，并优先处理迁移/重建流程中资产到期事实被迁移截止时间覆盖的风险。不做真实云资源、真实支付、链上广播、生产发布或其它不可逆操作。

### 发现

- 本轮开始时工作树干净，最近提交为 `7819121`「补充固定巡检最终收口记录」。
- 发现迁移/重建旧机流程仍会把 `migration_due_at` 同步写入资产 `actual_expires_at` 或旧兼容 `Server.expires_at`，容易把迁移截止时间误当成资产到期事实。
- 已改为只更新订单侧迁移、宽限、删除和固定 IP 回收计划；资产侧 `actual_expires_at` 与旧兼容 `Server.expires_at` 保留原始资产到期事实。
- 字段内省确认废弃 app 未安装；`CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 红线关键字扫描未发现订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app 回流。命中项仍为资产侧唯一到期事实、固定 IP 回收时间同步或 `_asset_expires_at` 临时属性。
- 继承前轮风险：`/Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 文字说明残留，本轮只记录风险，未跨仓库修改。

### 修改

- 更新 `cloud/services.py`，让旧机迁移计划只调整订单侧计划字段，不再通过 `_update_order_primary_records` 覆盖资产实际到期时间，并把日志和用户文案改为迁移截止时间语义。
- 更新 `cloud/provisioning.py`，让重建源订单标记待删除时保留资产实际到期事实，只更新迁移、宽限、删除和回收计划；相关云 IP 日志文案统一改为“资产到期”，避免继续混用服务到期语义。
- 更新 `cloud/management/commands/sync_aws_assets.py`，让 AWS 同步确认删除迁移旧机时不再把 `migration_due_at` 写入资产 `actual_expires_at`。
- 更新 `cloud/tests.py`，覆盖重建、换 IP、旧机迁移计划和 AWS 同步删除场景，确认资产到期事实保持不变。
- 覆盖更新 `docs/auto-optimization-latest.md`，并在本文件追加本轮中文巡检记录。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/handlers.py bot/api.py bot/tests.py cloud/api_tasks.py cloud/task_center.py cloud/lifecycle_tasks.py cloud/lifecycle_schedule.py cloud/sync_jobs.py cloud/services.py cloud/provisioning.py cloud/management/commands/sync_aws_assets.py cloud/tests_task_center.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/provisioning.py cloud/services.py cloud/tests.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_rebuild_source_migration_schedule_preserves_asset_expiry cloud.tests.CloudServerServicesTestCase.test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_order_preserves_asset_expiry_when_migration_due_is_earlier cloud.tests.CloudServerServicesTestCase.test_source_migration_schedule_keeps_asset_actual_expiry cloud.tests.CloudServerServicesTestCase.test_aws_sync_deleted_migration_order_keeps_asset_actual_expiry --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
rg -n "/api/dashboard|/api/users|/api/task-list|/api/plan-settings" /Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src /Users/a399/Desktop/data/vue-shop-admin/docs --glob '!**/node_modules/**' --glob '!**/dist/**' --glob '!**/.git/**'
git diff --check
```

结果：`manage.py check`、编译检查、10 个路由/后台鉴权测试、14 个任务中心测试、49 个机器人返回链测试、5 个迁移/同步聚焦测试、字段内省、默认 `makemigrations --check --dry-run`、SQLite `migrate --plan`、红线扫描、废弃 app 目录扫描、前端存在路径旧前缀扫描和 `git diff --check` 均符合预期。默认 MySQL `migrate --plan` 因当前沙箱无法连接本地 MySQL `127.0.0.1:3306` 失败，已记录为环境限制。SQLite 检查仍会打印不支持 `db_comment` / `db_table_comment` 的预期 warning；机器人返回链测试仍会打印既有配置读取容错、mocked postcheck 异常和 IP 校验日志。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端 `DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 描述残留；源码只读扫描未发现旧调用。

## 2026-06-03 固定巡检复核资产到期事实

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md`、`TODO.md` 和 `django-shop-backend` 技能。由于 `TODO.md` 固定任务已全部勾选，本轮按固定巡检清单执行，不做真实云资源、真实支付、链上广播、生产发布或其它不可逆操作。

### 发现

- 本轮开始时工作树干净，最近提交为 `ada3c48`「记录迁移到期事实复核」。
- 未发现需要修改运行代码的问题，本轮只更新中文巡检记录。
- 字段内省确认废弃 app 未安装；`CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 红线关键字扫描未发现运行代码恢复订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app。命中项仍为资产侧唯一到期事实、固定 IP 回收同步或 `_asset_expires_at` 临时属性。
- 机器人返回链 59 个聚焦测试继续通过，覆盖资产详情、订单详情、续费、钱包支付、换 IP、重装、修改配置和长回调压缩，仍满足 Telegram `callback_data` 64 字节限制。
- 任务中心 14 个聚焦测试继续通过，通知、自动续费和生命周期 section 的 pending/failed/retry_failed 统计未发现漏报回归。
- 迁移/重建/AWS 同步 5 个聚焦测试继续通过，确认 `migration_due_at` 不会覆盖资产 `actual_expires_at`。
- 前端源码只读扫描未发现旧 `/api/dashboard`、`/api/users`、`/api/task-list` 或 `/api/plan-settings` 调用；`/Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 文字说明残留，本轮未跨仓库修改。

### 修改

- 覆盖更新 `docs/auto-optimization-latest.md`。
- 在本文件追加本轮中文巡检记录。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/handlers.py bot/api.py bot/keyboards.py bot/tests.py cloud/api_tasks.py cloud/task_center.py cloud/lifecycle_tasks.py cloud/lifecycle_schedule.py cloud/sync_jobs.py cloud/services.py cloud/provisioning.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_rebuild_source_migration_schedule_preserves_asset_expiry cloud.tests.CloudServerServicesTestCase.test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_order_preserves_asset_expiry_when_migration_due_is_earlier cloud.tests.CloudServerServicesTestCase.test_source_migration_schedule_keeps_asset_actual_expiry cloud.tests.CloudServerServicesTestCase.test_aws_sync_deleted_migration_order_keeps_asset_actual_expiry --settings=shop.settings --verbosity=2
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
rg -n "/api/dashboard|/api/users|/api/task-list|/api/plan-settings" /Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src /Users/a399/Desktop/data/vue-shop-admin/docs --glob '!**/node_modules/**' --glob '!**/dist/**' --glob '!**/.git/**'
git diff --check
```

结果：`manage.py check`、编译检查、字段内省、14 个任务中心测试、59 个 bot 返回链/路由测试、5 个云资产迁移/同步聚焦测试、默认 `makemigrations --check --dry-run`、SQLite `migrate --plan` 和 `git diff --check` 均符合预期。默认 MySQL `migrate --plan` 因当前沙箱无法连接本地 MySQL `127.0.0.1:3306` 失败，已记录为环境限制。SQLite 检查仍会打印不支持 `db_comment` / `db_table_comment` 的预期 warning；bot 返回链测试仍会打印既有配置读取容错、mocked postcheck 异常和 IP 校验日志。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端 `DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 描述残留；源码只读扫描未发现旧调用。

## 2026-06-03 通知状态统计修复后固定巡检

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md`、`TODO.md` 和 `django-shop-backend` 技能。由于 `TODO.md` 固定任务已全部勾选，本轮按固定巡检清单执行，重点复核上一轮通知计划状态统计修复、云资产到期事实红线、机器人返回链和迁移差异。不做真实云资源、真实支付、链上广播、生产发布或其它不可逆操作。

### 发现

- 本轮开始时工作树干净，最近提交为 `beab4a5`「修正通知任务状态统计」。
- 未发现需要修改运行代码的新问题，本轮只更新中文巡检记录。
- `CloudAsset.actual_expires_at` 仍是唯一资产到期事实；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 废弃 runtime app 未安装，目录扫描无 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz` 回流。
- 任务中心 14 个聚焦测试继续通过，通知、自动续费和生命周期 section 的 pending/failed/retry_failed 统计未发现上一轮修复后的回归。
- 机器人返回链 59 个聚焦测试继续通过，资产详情、订单详情、续费、钱包支付、换 IP、重装、修改配置和长回调压缩仍满足 Telegram `callback_data` 64 字节限制。
- 迁移/重建/AWS 同步 5 个聚焦测试继续通过，确认 `migration_due_at` 不会覆盖资产 `actual_expires_at`。
- 红线关键字扫描未发现运行代码恢复订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app。命中项仍为资产侧唯一到期事实、固定 IP 回收同步或 `_asset_expires_at` 临时属性。
- 前端源码和前端 docs 只读扫描未发现旧 `/api/dashboard`、`/api/users`、`/api/task-list` 或 `/api/plan-settings` 调用；`/Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 文字说明残留，本轮未跨仓库修改。

### 修改

- 覆盖更新 `docs/auto-optimization-latest.md`。
- 在本文件追加本轮中文巡检记录。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/handlers.py bot/api.py bot/keyboards.py bot/tests.py cloud/api_tasks.py cloud/task_center.py cloud/lifecycle_tasks.py cloud/lifecycle_schedule.py cloud/sync_jobs.py cloud/services.py cloud/provisioning.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py cloud/tests.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_rebuild_source_migration_schedule_preserves_asset_expiry cloud.tests.CloudServerServicesTestCase.test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_order_preserves_asset_expiry_when_migration_due_is_earlier cloud.tests.CloudServerServicesTestCase.test_source_migration_schedule_keeps_asset_actual_expiry cloud.tests.CloudServerServicesTestCase.test_aws_sync_deleted_migration_order_keeps_asset_actual_expiry --settings=shop.settings --verbosity=2
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
rg -n "/api/dashboard|/api/users|dashboard_urls|dashboard_api" bot core orders cloud shop docs TODO.md AGENTS.md --glob '!docs/refactor-version-record.md'
rg -n "/api/dashboard|/api/users|/api/task-list|/api/plan-settings" /Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src /Users/a399/Desktop/data/vue-shop-admin/docs --glob '!**/node_modules/**' --glob '!**/dist/**' --glob '!**/.git/**'
git diff --check
```

结果：`manage.py check`、编译检查、字段内省、14 个任务中心测试、59 个 bot 返回链/路由测试、5 个云资产迁移/同步聚焦测试、默认 `makemigrations --check --dry-run`、SQLite `migrate --plan` 和 `git diff --check` 均符合预期。默认 MySQL `migrate --plan` 因当前沙箱无法连接本地 MySQL `127.0.0.1:3306` 失败，已记录为环境限制。SQLite 检查仍会打印不支持 `db_comment` / `db_table_comment` 的预期 warning；bot 返回链测试仍会打印既有配置读取容错、mocked postcheck 异常和 IP 校验日志。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端 `DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 描述残留；源码只读扫描未发现旧调用。

## 2026-06-03 固定巡检再复核

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md`、`TODO.md` 和 `django-shop-backend` 技能。由于 `TODO.md` 固定任务已全部勾选，本轮按固定巡检清单再复核云资产到期事实、机器人返回链、任务中心统计、迁移差异和旧 API 前缀。不做真实云资源、真实支付、链上广播、生产发布或其它不可逆操作。

### 发现

- 本轮开始时当前可见最近提交为 `beab4a5`「修正通知任务状态统计」，随后确认上一轮文档记录已提交为 `2b556eb`「记录通知状态统计巡检」。
- 未发现需要修改运行代码的新问题，本轮只更新中文巡检记录。
- `CloudAsset.actual_expires_at` 仍是唯一资产到期事实；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期字段。
- 废弃 runtime app 未安装，目录扫描无 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz` 回流。
- 任务中心 14 个聚焦测试继续通过，通知、自动续费和生命周期 section 的 pending/failed/retry_failed 统计未发现回归。
- 机器人返回链 59 个聚焦测试继续通过，资产详情、订单详情、续费、钱包支付、换 IP、重装、修改配置和长回调压缩仍满足 Telegram `callback_data` 64 字节限制。
- 迁移/重建/AWS 同步 5 个聚焦测试继续通过，确认 `migration_due_at` 不会覆盖资产 `actual_expires_at`。
- 红线关键字扫描未发现运行代码恢复订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app。命中项仍为资产侧唯一到期事实、固定 IP 回收同步或 `_asset_expires_at` 临时属性。
- 前端源码和前端 docs 只读扫描未发现旧 `/api/dashboard`、`/api/users`、`/api/task-list` 或 `/api/plan-settings` 调用；`/Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 文字说明残留，本轮未跨仓库修改。

### 修改

- 覆盖更新 `docs/auto-optimization-latest.md`。
- 在本文件追加本轮中文巡检记录。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/handlers.py bot/api.py bot/keyboards.py bot/tests.py cloud/api_tasks.py cloud/task_center.py cloud/lifecycle_tasks.py cloud/lifecycle_schedule.py cloud/sync_jobs.py cloud/services.py cloud/provisioning.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py cloud/tests.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_rebuild_source_migration_schedule_preserves_asset_expiry cloud.tests.CloudServerServicesTestCase.test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_order_preserves_asset_expiry_when_migration_due_is_earlier cloud.tests.CloudServerServicesTestCase.test_source_migration_schedule_keeps_asset_actual_expiry cloud.tests.CloudServerServicesTestCase.test_aws_sync_deleted_migration_order_keeps_asset_actual_expiry --settings=shop.settings --verbosity=2
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
rg -n "/api/dashboard|/api/users|dashboard_urls|dashboard_api" bot core orders cloud shop docs TODO.md AGENTS.md --glob '!docs/refactor-version-record.md'
rg -n "/api/dashboard|/api/users|/api/task-list|/api/plan-settings" /Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src /Users/a399/Desktop/data/vue-shop-admin/docs --glob '!**/node_modules/**' --glob '!**/dist/**' --glob '!**/.git/**'
git diff --check
```

结果：`manage.py check`、编译检查、字段内省、14 个任务中心测试、59 个 bot 返回链/路由测试、5 个云资产迁移/同步聚焦测试、默认 `makemigrations --check --dry-run`、SQLite `migrate --plan` 和 `git diff --check` 均符合预期。默认 MySQL `migrate --plan` 因当前沙箱无法连接本地 MySQL `127.0.0.1:3306` 失败，已记录为环境限制。SQLite 检查仍会打印不支持 `db_comment` / `db_table_comment` 的预期 warning；bot 返回链测试仍会打印既有配置读取容错、mocked postcheck 异常和 IP 校验日志。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端 `DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 描述残留；源码只读扫描未发现旧调用。

## 2026-06-03 固定巡检三次复核

### 范围

本轮继续监工 Shop Django 后端，先读取自动化记忆、当前 git 状态、最近提交、`docs/auto-optimization-control.md`、`docs/auto-optimization-latest.md`、版本记录末尾、`AGENTS.md`、`TODO.md` 和 `django-shop-backend` 技能。由于 `TODO.md` 固定任务已全部勾选，本轮按固定巡检清单继续复核云资产到期事实、机器人返回链、任务中心统计、迁移差异和旧 API 前缀。不做真实云资源、真实支付、链上广播、生产发布或其它不可逆操作。

### 发现

- 本轮开始时工作区有上一轮文档记录差异；运行中确认该记录已提交为 `c566aa8`「记录固定巡检再复核」，随后本轮在该提交之上继续复核。
- 未发现需要修改运行代码的新问题，本轮只更新中文巡检记录。
- `CloudAsset.actual_expires_at` 仍是唯一资产到期事实；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复实际到期字段，`risk_expired` 仍只是风险状态标记。
- 废弃 runtime app 未安装，目录扫描无 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz` 回流。
- 任务中心 14 个聚焦测试继续通过，通知、自动续费和生命周期 section 的 pending/failed/retry_failed 统计未发现漏报回归。
- 机器人返回链 59 个聚焦测试继续通过，资产详情、订单详情、续费、钱包支付、换 IP、重装、修改配置和长回调压缩仍满足 Telegram `callback_data` 64 字节限制。
- 迁移/重建/AWS 同步 5 个聚焦测试继续通过，确认 `migration_due_at` 不会覆盖资产 `actual_expires_at`。
- 红线关键字扫描未发现运行代码恢复订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app。命中项仍为资产侧唯一到期事实、固定 IP 回收同步或 `_asset_expires_at` 临时属性。
- 前端源码和前端 docs 只读扫描未发现旧 `/api/dashboard`、`/api/users`、`/api/task-list` 或 `/api/plan-settings` 调用；`/Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 文字说明残留，本轮未跨仓库修改。

### 修改

- 覆盖更新 `docs/auto-optimization-latest.md`。
- 在本文件追加本轮中文巡检记录。

### 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/handlers.py bot/api.py bot/keyboards.py bot/tests.py cloud/api_tasks.py cloud/task_center.py cloud/lifecycle_tasks.py cloud/lifecycle_schedule.py cloud/sync_jobs.py cloud/services.py cloud/provisioning.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/management/commands/reconcile_cloud_assets_from_servers.py cloud/tests.py cloud/tests_task_center.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_rebuild_source_migration_schedule_preserves_asset_expiry cloud.tests.CloudServerServicesTestCase.test_mark_cloud_server_ip_change_requested_falls_back_when_plan_missing cloud.tests.CloudServerServicesTestCase.test_sync_aws_missing_order_preserves_asset_expiry_when_migration_due_is_earlier cloud.tests.CloudServerServicesTestCase.test_source_migration_schedule_keeps_asset_actual_expiry cloud.tests.CloudServerServicesTestCase.test_aws_sync_deleted_migration_order_keeps_asset_actual_expiry --settings=shop.settings --verbosity=2
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan --noinput
rg -n "service_expires_at\s*=|actual_expires_at\s*=.*order\.|order\..*actual_expires_at|CloudLifecyclePlan\b|CloudNoticePlan\b|CloudAutoRenewPlan\b|refund_order|process_refund|create_refund|issue_refund|refund_to_balance|refund_balance|STATUS_REFUNDED|status=['\"]refunded['\"]|normalize_service_expiry|service_expired_at" bot core orders cloud shop --glob '!**/migrations/**' --glob '!**/tests.py'
find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print
rg -n "/api/dashboard|/api/users|dashboard_urls|dashboard_api" bot core orders cloud shop docs TODO.md AGENTS.md --glob '!docs/refactor-version-record.md'
rg -n "/api/dashboard|/api/users|/api/task-list|/api/plan-settings" /Users/a399/Desktop/data/vue-shop-admin/DEVELOPMENT.md /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src /Users/a399/Desktop/data/vue-shop-admin/docs --glob '!**/node_modules/**' --glob '!**/dist/**' --glob '!**/.git/**'
git diff --check
```

结果：`manage.py check`、编译检查、字段内省、14 个任务中心测试、59 个 bot 返回链/路由测试、5 个云资产迁移/同步聚焦测试、默认 `makemigrations --check --dry-run`、SQLite `migrate --plan`、前后端旧 API 只读扫描和 `git diff --check` 均符合预期。默认 MySQL `migrate --plan` 因当前沙箱无法连接本地 MySQL `127.0.0.1:3306` 失败，已记录为环境限制。SQLite 检查仍会打印不支持 `db_comment` / `db_table_comment` 的预期 warning；bot 返回链测试仍会打印既有配置读取容错、mocked postcheck 异常和 IP 校验日志。

### 剩余风险

- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。
- 前端 `DEVELOPMENT.md` 仍有旧 `/api/dashboard/` 描述残留；源码只读扫描未发现旧调用。
