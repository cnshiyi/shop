# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 06:13 CST
- 状态：完成一轮只读生命周期/机器人高风险路径巡检；未发现需要立即提交的代码缺陷，本轮仅更新记录。
- 本轮范围：生命周期总开关与资产单项开关联动、IP 删除执行时间窗、机器人详情回退链与 Telegram `callback_data` 长度约束。

## 巡检结论

- 生命周期计划相关聚焦测试全部通过，确认以下契约仍成立：
  - 资产单项 `shutdown_enabled`、`server_delete_enabled`、`ip_delete_enabled` 会分别投影到对应计划状态。
  - 全局生命周期总开关会覆盖计划页展示状态，不会被资产局部状态绕过。
  - 订单固定 IP 回收与未附加固定 IP 释放都会再次校验后台配置的 IP 删除执行时间窗口。
- 机器人高风险回退链聚焦测试全部通过，确认：
  - 资产详情从超长订单详情返回时会压缩为短回调路径。
  - 嵌套资产详情回退链会重新压缩，不会把历史长路径继续向下传。
  - 极端大 ID 场景下，续费/换 IP/重装/修改配置等按钮的 `callback_data` 仍不超过 Telegram 64 字节限制。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_asset_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_global_stage_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_recycle_respects_ip_delete_time_window cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_unattached_ip_uses_ip_delete_time_window --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_from_extreme_order_detail_stays_under_limit bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_recompacts_nested_asset_detail_back_path bot.tests.RetainedIpRenewalUiTestCase.test_extreme_nested_cloud_callbacks_stay_under_telegram_limit --settings=shop.settings --verbosity=1
git diff --check
```

只读扫描已执行：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|plan snapshot|snapshot table|old refund|refund_legacy|dashboard_api|accounts|finance|mall|monitoring|biz" cloud bot orders core shop -g '!**/migrations/**'
```

说明：

- SQLite 的 `db_comment` warnings 仍是已知测试噪声，不影响本轮聚焦结论。
- `core.dashboard_api` 与 `core.cloud_accounts` 是当前运行时代码；本轮未发现废弃 runtime app 回流，也未发现订单侧到期字段和旧退款入口回流。

## 受限项

- 受当前会话沙箱限制，无法连接本地 MySQL：对 `127.0.0.1` 的数据库连接返回 `Operation not permitted`，因此本轮没法做实库生命周期对账。
- 本地前端页面 `http://127.0.0.1:5666/admin/cloud-assets` 当前未启动，`curl` 连接失败；因此本轮未执行真实浏览器翻页/点击和控制台检查。
- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。

## 下一步

- 在具备本地 MySQL 和前端页面可达条件的下一轮，优先补生命周期计划页的真实页面巡检和实库对账。
- 继续维持“每轮一个最小动作”的节奏，优先盯住生命周期总开关/单项开关和机器人支付/续费返回链。
