# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 04:39 CST
- 状态：完成一轮机器人高并发、生命周期计划缓存、代理列表/计划页真实页面巡检；修复生命周期计划 count 短缓存和页面刷新缓存不联动的问题。
- 本轮范围：机器人云服务器后台多任务高并发、生命周期关机/删机/IP 删除开关与分页契约、代理快照 Telegram 分组风险索引、计划页与代理列表真实浏览器渲染、红线扫描。

## 修复内容

- `bot/tests.py`
  - 新增 `test_cloud_background_tasks_keep_high_concurrency_isolated`。
  - 同时覆盖钱包直付创建、订单补付创建、续费后巡检 3 类后台任务，确认 chat/order/port 不串线。
- `cloud/tests.py`
  - 修正通知计划详情测试 patch 入口，从已迁移的旧入口改为当前 `cloud.lifecycle._get_due_orders`。
- `cloud/models.py` / `cloud/migrations/0061_dashboard_snapshot_tg_risk_group_indexes.py`
  - 给 `CloudAssetDashboardSnapshot` 补齐风险标签 + 云账号异常 + Telegram 分组索引，降低海量分组下代理列表按群组聚合的 count 压力。
  - 本机已执行 `uv run python manage.py migrate cloud 0061`。
- `cloud/lifecycle_plan_queries.py` / `bot/api.py`
  - 给服务器生命周期计划 count 增加短 TTL 缓存。
  - 修复刷新/指纹失效重建时没有清理该短缓存的问题，避免页面使用旧 total 导致计划项漏页或空页。
  - 保持未附加固定 IP 从服务器关机/删机计划中拆出，继续进入 IP 删除计划。

## 真实页面复查

已用真实浏览器打开：

- `http://127.0.0.1:5666/admin/tasks/plans`
- `http://127.0.0.1:5666/admin/cloud-assets`

结果：

- 计划页标题：`计划 - Vben Admin Antd`，约 `9.15s` 渲染完成，控制台 `0 error / 0 warning`。
- 计划页可见：关机计划、删除计划、IP 删除计划、IP 删除历史、列开关和计划统计。
- 计划页 API 计数：关机计划 `1879990`，服务器删除计划 `2`，IP 删除计划 `500000`，IP 删除历史 `520010`。
- 代理列表标题：`代理列表 - Vben Admin Antd`，约 `9.41s` 渲染完成，控制台 `0 error / 0 warning`。
- 代理列表可见：IP 视图、全部、未附加固定IP、编辑按钮和全标签计数。
- 本轮创建的临时后台 session 已删除，未打印 token/session。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py bot/handlers.py bot/tests.py cloud/lifecycle_plan_queries.py cloud/models.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ignore_account_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_asset_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_split_shutdown_before_server_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_global_stage_switches cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_fill_missing_expiry_with_default_delete_plan cloud.tests.CloudServerServicesTestCase.test_unattached_static_ip_due_scan_fills_missing_expiry_as_future_plan --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_reuses_cached_count_snapshot_after_refresh cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_reads_persisted_count_snapshot_after_process_cache_clear cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_rebuilds_cached_count_snapshot_when_asset_changes cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_returns_server_delete_history_table cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_history_mixes_orders_and_assets_by_updated_at cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_mixes_logs_and_assets_by_time --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描：

- 未发现运行时代码回流旧计划快照、旧退款函数或废弃 runtime app。
- `service_expires_at` 仅命中历史 migrations。

## 受限项

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未使用真实 Telegram 客户端点击机器人；已补机器人高并发聚焦测试并跑完整机器人回调测试集。
- 计划页冷态全量 count 仍可能较慢；本轮已加短缓存和索引，后续应继续把冷态 count 路径拆到投影/任务表。

## 下一步

- 继续长跑巡检代理列表全标签深分页和计划页深分页，确认新索引在更多标签下不会丢组或串页。
- 继续推进生命周期任务表投影路线，减少计划页冷态全库 count。
- 若要做真实 Telegram 客户端点击，需要使用本地安全登录会话，不能在日志或文档中打印 session/token/TOTP。
