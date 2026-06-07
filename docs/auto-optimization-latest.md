# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 03:58 CST
- 状态：完成一轮机器人多任务高并发、callback 返回链、后台真实页面和红线巡检。
- 本轮范围：`bot.tests` 机器人并发与返回链聚焦测试、`/admin/tasks/plans`、`/admin/cloud-assets`、`/admin/logs/operations`、`/admin/telegram-accounts/accounts`、基础检查、编译检查、红线扫描。

## 机器人专项

已覆盖的机器人功能链路：

- 多用户并发监听推送隔离。
- 资产详情、订单详情、代理详情短回调。
- 续费、钱包余额续费、TRX/USDT 续费支付按钮。
- 换 IP、地区提交、修改配置。
- 重装迁移/重建确认、取消、提交后返回链。
- 管理员查询入口、修改到期入口、余额明细分页。
- Telegram `callback_data` 64 字节限制和极端嵌套返回链压缩。

聚焦测试结果：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_detail_callbacks_keep_nested_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_detail_actions_keep_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_detail_actions_from_long_asset_detail_stay_under_callback_limit bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_detail_back_button_from_extreme_nested_detail_stays_under_limit bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_detail_back_button_falls_back_to_cloud_list_when_source_is_too_long bot.tests.RetainedIpRenewalUiTestCase.test_detail_back_buttons_fall_back_when_source_is_too_long bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_direct_action_buttons_compact_back_callback bot.tests.RetainedIpRenewalUiTestCase.test_reinstall_cancel_buttons_keep_back_path bot.tests.RetainedIpRenewalUiTestCase.test_reinstall_submitted_buttons_keep_back_path bot.tests.RetainedIpRenewalUiTestCase.test_reinstall_confirm_handlers_reuse_saved_back_path_after_submit bot.tests.RetainedIpRenewalUiTestCase.test_asset_renewal_plan_keyboard_keeps_back_path bot.tests.RetainedIpRenewalUiTestCase.test_retained_ip_renewal_plan_keyboard_keeps_back_path bot.tests.RetainedIpRenewalUiTestCase.test_second_level_cloud_actions_with_large_ids_stay_under_callback_limit bot.tests.RetainedIpRenewalUiTestCase.test_extreme_nested_cloud_callbacks_stay_under_telegram_limit bot.tests.RetainedIpRenewalUiTestCase.test_cloud_renew_payment_keyboard_keeps_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_renew_payment_from_asset_detail_returns_to_asset_detail bot.tests.RetainedIpRenewalUiTestCase.test_cloud_renew_payment_from_long_asset_detail_stays_under_callback_limit bot.tests.RetainedIpRenewalUiTestCase.test_cloud_renewal_result_branches_keep_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_change_ip_keyboards_keep_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_change_ip_from_asset_detail_returns_to_asset_detail bot.tests.RetainedIpRenewalUiTestCase.test_asset_change_ip_action_keeps_back_path_when_rendering_regions bot.tests.RetainedIpRenewalUiTestCase.test_cloud_change_ip_region_submission_keeps_back_path bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_handler_keeps_current_callback_parsing bot.tests.RetainedIpRenewalUiTestCase.test_cloud_ip_query_actions_return_to_query_menu bot.tests.RetainedIpRenewalUiTestCase.test_cloud_auto_renew_callbacks_keep_nested_back_under_limit bot.tests.RetainedIpRenewalUiTestCase.test_cloud_upgrade_payment_keeps_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_action_handlers_compact_nested_back_callback_before_reuse bot.tests.BotOrderAndBalanceFilterTestCase.test_balance_detail_filters_and_pagination_callbacks_keep_filter bot.tests.BotOrderAndBalanceFilterTestCase.test_paid_cloud_order_prepare_submits_default_port_directly bot.tests.BotOrderAndBalanceFilterTestCase.test_balance_pay_existing_cloud_order_auto_submits_default_port bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_query_keyboard_includes_reinstall_and_expiry_actions bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_start_handler_keeps_query_menu_back_path --settings=shop.settings --verbosity=1
```

结果：`Ran 33 tests`，全部通过。SQLite `db_comment` 警告仍是测试数据库能力差异，不是业务失败。

## 真实页面

本轮创建一次临时后台 session，仅用于浏览器真实页面巡检；结束时已删除该 session 和临时文件。

真实 Chrome 打开结果：

| 页面 | URL | 标题 | 结果 | 控制台 |
| --- | --- | --- | --- | --- |
| 生命周期计划页 | `http://127.0.0.1:5666/admin/tasks/plans` | `计划 - Vben Admin Antd` | 已渲染 | `0 error / 0 warning` |
| 代理列表页 | `http://127.0.0.1:5666/admin/cloud-assets` | `代理列表 - Vben Admin Antd` | 已渲染 | `0 error / 0 warning` |
| 机器人操作日志 | `http://127.0.0.1:5666/admin/logs/operations` | `操作日志 - Vben Admin Antd` | 已渲染 | `0 error / 0 warning` |
| Telegram 账号 | `http://127.0.0.1:5666/admin/telegram-accounts/accounts` | `账号列表 - Vben Admin Antd` | 已渲染 | `0 error / 0 warning` |

计划页滚动到底部后确认五个区域均存在：

- 关机计划
- 删除计划
- 服务器删除历史
- IP 删除计划
- IP 删除历史

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/tests.py bot/handlers.py bot/keyboards.py bot/telegram_listener.py
```

红线扫描：

```bash
rg -n "service_expires_at|CloudLifecyclePlanSnapshot|legacy_refund|old_refund|from accounts|from finance|from mall|from monitoring|from dashboard_api|from biz|import accounts|import finance|import mall|import monitoring|import dashboard_api|import biz" shop core bot orders cloud -g '*.py'
```

扫描结果：

- `service_expires_at` 只命中历史 migrations。
- 未命中运行时代码中的旧计划快照、旧退款函数名或废弃 runtime app 导入。

## 清理

- 已删除临时后台 session：`deleted=1`。
- 已删除本轮 `.playwright-cli` 临时产物。
- 本轮未留下新的浏览器截图、临时脚本或有效后台 session。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。
- 本轮未打印 Telegram token、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。

## 下一步

- 继续循环巡检生命周期创建/关机/删除开关联动，保持不做不可逆真实操作，除非单独形成真机测试报告并脱敏记录资源 ID。
- 下一轮优先继续看代理列表标签高数据量性能和机器人实际按钮链路的后台可观测性。
