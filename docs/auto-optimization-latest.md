# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 18:20 CST
- 状态：修复未附加 IP 在资产详情页切换 IP 删除计划开关时误刷新已有到期时间的问题，并完成真实浏览器详情页/计划页同步验证、机器人多任务高并发回归。
- 后端 Commit：待提交。
- 前端 Commit：本轮无前端变更。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 重点：
  - 资产详情页三类单项开关：关机计划、删除计划、IP 删除计划。
  - 计划页三类单项开关同步：关机计划、删除计划、IP 删除计划。
  - 未附加 IP 已有到期时间时，切换 IP 删除开关不得刷新 `CloudAsset.actual_expires_at`。
  - 机器人多任务高并发和返回链固定回归。

## 本轮发现

- 真实浏览器测试中，服务器资产的关机/删机开关关闭后，计划页能继续显示对应行并标记关闭。
- 未附加 IP 资产 `500332` 关闭 IP 删除开关后，计划页一度找不到该资产。
- 继续追踪后确认根因不在前端：后台资产更新接口对未附加 IP 的任意保存都会触发 15 天删除到期时间刷新。
- 这违反既定规则：只有未附加 IP 没有到期时间时，才自动补 15 天删除到期时间；已有 `CloudAsset.actual_expires_at` 不应被开关切换改写。

## 本轮修复

后端文件：

```text
/Users/a399/Desktop/data/shop/cloud/api_asset_edit.py
/Users/a399/Desktop/data/shop/cloud/tests.py
```

修复内容：

- `update_cloud_asset` 中未附加 IP 自动补到期时间增加 `asset.actual_expires_at is None` 条件。
- 切换 `ip_delete_enabled`、`shutdown_enabled`、`server_delete_enabled` 等非到期字段时，不再刷新已有未附加 IP 到期事实。
- 新增聚焦测试：
  - 已有到期时间的未附加 IP 关闭 IP 删除开关后，到期时间保持不变。
  - 生命周期计划页仍返回该 IP 删除计划行。
  - 行状态为 `ip_delete_disabled`，`ip_delete_enabled=false`。

## 真实前端验证

实际打开并操作：

```text
http://127.0.0.1:5666/admin/cloud-assets/20418
http://127.0.0.1:5666/admin/cloud-assets/326
http://127.0.0.1:5666/admin/cloud-assets/500332
http://127.0.0.1:5666/admin/tasks/plans
```

真实操作结果：

- 资产 `20418`：详情页关闭关机计划后，计划页响应和真实表格行均显示 `shutdown_disabled`，行内开关关闭；随后恢复开启。
- 资产 `326`：详情页关闭删除计划后，计划页响应和真实表格行均显示 `server_delete_disabled`，行内开关关闭；随后恢复开启。
- 资产 `500332`：详情页关闭 IP 删除计划后，计划页响应和真实表格行均显示 `ip_delete_disabled`，行内开关关闭；随后恢复开启。
- IP 样本到期时间保持为 `2026-06-21T14:17:03.527000+08:00`，没有再被刷新到 15 天后。
- 控制台 error/warning：`0`
- 业务 API 失败：`0`
- 请求失败：`0`

截图：

```text
/private/tmp/shop-asset-detail-plan-sync.png
```

## 数据清理

- 三个实测样本开关已恢复开启。
- 本轮曾被误刷新的 IP 样本到期时间已恢复为实测前值。
- 已删除临时后台登录用户 `codex_patrol_asset_detail_probe`。
- 已删除 `/private/tmp/shop_asset_detail_probe_token.txt`。

## 机器人高并发

机器人 8 条聚焦测试通过，覆盖：

- 通知复制并发隔离。
- 钱包直付 / 钱包补付同时执行。
- `60` 路批量后台任务隔离。
- 订单详情、资产详情、IP 查询、自动续费返回链。
- `callback_data <= 64` 字节限制。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_asset_edit.py cloud/lifecycle_plan_queries.py bot/api.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_unattached_ip_switch_preserves_existing_expiry_and_plan_row cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_list_order_detail_uses_short_back_callback bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_from_extreme_order_detail_stays_under_limit bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_recompacts_nested_asset_detail_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_ip_query_actions_return_to_query_menu bot.tests.RetainedIpRenewalUiTestCase.test_cloud_auto_renew_callbacks_keep_nested_back_under_limit --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过。命中项为既有测试桩账号字符串、Telegram 登录账号 API 文件名，以及 `CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实回流。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥、云厂商密钥或完整代理链接。

## 下一步

- 继续巡检生命周期执行器：关机完成后进入删除计划、删除完成后进入 IP 删除计划的状态流转。
- 继续关注计划页百万级数据加载耗时，本轮完整计划页重载仍明显偏慢。
