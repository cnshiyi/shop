# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 14:16 CST
- 状态：已完成“移除兼容残留”。
- 本轮范围：旧云资产兼容入口、旧 Server 兼容壳、旧账号标签兼容、旧计划兼容路由、旧提醒兼容函数、相关测试口径和红线扫描。

## 修改摘要

- 删除旧兼容入口文件：`cloud/api.py`、`cloud/server_records.py`、`cloud/management/commands/reconcile_cloud_assets_from_servers.py`。
- 删除后台旧兼容路由：`task-list-compat`、`plan-settings-compat`。
- 删除机器人旧 `cloud:mute` 回退分支，以及 `mute_cloud_reminders` / `unmute_cloud_reminders`。
- `CloudAsset` 移除旧别名属性 `server_name` / `expires_at`，资产名称和到期事实只使用 `asset_name` / `actual_expires_at`。
- 云订单状态同步只更新 `CloudAsset`，删除 `server_updates`、`_order_primary_server`、旧字段映射辅助逻辑。
- 云账号标签只保留当前标准 `provider+external_account_id+name`，不再解析冒号标签或 provider-only 标签。
- AWS / Aliyun 同步、资产编辑、重建迁移、删除服务器接口移除 `compat_server_record` 分支。
- 测试口径改为直接创建和断言 `CloudAsset`，删除旧 Server 兼容入口测试，账号标签测试改为当前标签口径。

## 验证

本地已通过：

```bash
uv run python -m py_compile bot/tests.py cloud/tests.py core/tests.py bot/handlers.py cloud/models.py cloud/services.py cloud/api_orders.py cloud/api_asset_edit.py cloud/api_servers.py cloud/provisioning.py cloud/lifecycle_state.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py shop/admin_urls.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase cloud.tests.CloudServerServicesTestCase.test_cloud_account_label_variants_return_current_label_only cloud.tests.CloudServerServicesTestCase.test_account_load_does_not_count_provider_only_label_for_every_account cloud.tests.CloudServerServicesTestCase.test_aws_sync_server_resolution_accepts_current_account_label cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_log_without_known_note_shows_history cloud.tests.CloudServerServicesTestCase.test_dashboard_asset_update_matches_current_account_label cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_separate_ip_delete_plan_and_history_items core.tests.CryptoDecryptTestCase --settings=shop.settings --verbosity=1
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudOrderStatusDashboardSyncTestCase --settings=shop.settings --verbosity=1
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_dedupe_cloud_assets_merges_same_cloud_account_label_variants cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_dedupes_same_cloud_account_label_variants cloud.tests.CloudServerServicesTestCase.test_aws_sync_resolution_does_not_match_cross_region_same_instance_without_ip cloud.tests.CloudServerServicesTestCase.test_dashboard_asset_update_matches_current_account_label --settings=shop.settings --verbosity=1
DB_ENGINE=mysql uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
git diff --check
```

结果：编译通过；默认和 MySQL `manage.py check` 通过；迁移检查无待生成迁移；聚焦测试共 21 条通过；diff 空白检查通过。SQLite 测试中的字段/表注释警告为已知数据库能力差异。

## 红线扫描

```bash
rg -n "legacy|compat|兼容|server_records|reconcile_cloud_assets_from_servers|_order_primary_server|server_updates|task-list-compat|plan-settings-compat|mute_cloud_reminders|unmute_cloud_reminders|Server\\.objects|cloud\\.server_records|compat_server_record|sync_state__compat_server_record" shop core bot orders cloud -g '!*/migrations/*'
rg -n "\\bfrom cloud import api\\b|\\bimport cloud\\.api\\b|\\bfrom cloud\\.api import\\b|\\bfrom cloud\\.api$" shop core bot orders cloud -g '!*/migrations/*'
rg -n "service_expires_at|old_refund|legacy_refund|refund_cloud_order|refund_order|apply_refund|process_refund|create_refund|lifecycle_plan_projection|0058_lifecycle_task_plan_page_index|plan_projection|page_lifecycle_plan_tasks|sync_lifecycle_plan_projection" shop core bot orders cloud -g '!*/migrations/*'
```

结果：无命中。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款入口或旧 Server 兼容壳。

## 剩余风险

- 旧兼容入口删除后，仍持有旧冒号账号标签或 provider-only 标签的数据不会再被账号标签辅助函数归属到云账号；这是本轮按“不需要兼容”执行的预期结果。
- 默认 MySQL 全量测试仍可能遇到已有测试库 `test_a` 的交互确认；本轮未删除测试库，继续使用 SQLite 隔离库跑聚焦测试。
