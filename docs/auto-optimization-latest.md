# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 17:49 CST
- 状态：修复计划页“删除计划”表状态/执行文案误用关机总开关的问题，并完成生命周期总开关、计划页、任务中心、通知页和机器人高并发复测。
- 后端 Commit：本轮后端无业务代码变更，只有巡检记录待提交。
- 前端 Commit：待提交。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 重点：
  - 生命周期计划页三类总开关联动：关机、删除服务器、删除 IP。
  - 关机计划、删除计划、IP 删除计划的单表展示口径。
  - 计划页分页 total/loaded 与服务端查询层一致性。
  - 任务中心、通知计划、机器人多任务高并发固定回归。

## 本轮发现

- 前端计划页把关机计划和删除计划合并为同一个 `serverPlanSections` 渲染。
- “计划状态”和“执行状态”单元格通过 `serverPlanSwitchField(column.key)` 推断总开关字段。
- 对于“删除计划”表，当前列名是 `plan_state_label` 或 `execution_status`，推断结果会落到默认的 `shutdown_enabled`。
- 结果是删除服务器总开关关闭时，删除计划表的状态/执行文案可能仍按关机总开关判断，和后端 `global_server_delete_disabled` 口径不一致。

## 本轮修复

前端文件：

```text
/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src/views/dashboard/tasks/plans.vue
```

修复内容：

- 给 `关机计划` section 显式绑定 `switchField: 'shutdown_enabled'`。
- 给 `删除计划` section 显式绑定 `switchField: 'server_delete_enabled'`。
- “计划状态”和“执行状态”使用 `section.switchField` 计算有效总开关、阻断原因和执行文案。
- 单项开关列仍按列名操作 `shutdown_enabled/server_delete_enabled`，没有改变资产开关保存逻辑。

## 真实前端验证

实际打开并操作：

```text
http://127.0.0.1:5666/admin/tasks/plans
http://127.0.0.1:5666/admin/tasks
http://127.0.0.1:5666/admin/tasks/notices
```

真实操作结果：

- 三个总开关当前原始值均为 `1`。
- 在真实计划页逐个临时关闭并恢复：
  - `关机服务器`：关闭后关机计划显示 `总开关关闭`，恢复成功。
  - `删除服务器`：关闭后删除计划显示 `删机总开关关闭`，恢复成功。
  - `删除IP`：关闭后 IP 删除计划显示 `IP删除总开关关闭`，恢复成功。
- 没有点击执行按钮，没有执行真实关机、删机或释放 IP。
- 计划页、任务中心、通知页 API 均返回 `200`。
- 控制台 error/warning：`0`
- 业务 API 失败：`0`

截图文件：

```text
/private/tmp/shop-lifecycle-switches.png
/private/tmp/shop-lifecycle-pages.png
```

## 数据对账

服务端计划页 `/api/admin/tasks/plans/` 与查询层对账通过：

- 关机计划：第 `1/2/1000` 页，total `1,979,990`。
- 删除计划：第 `1/2` 页，total `2`。
- 服务器删除历史：第 `1/2/401` 页，total `20,010`。
- IP 删除计划：第 `1/2/1000` 页，total `500,000`。
- IP 删除历史：第 `1/2/1000` 页，total `520,010`。

所有表的 `pagination.total` 和查询层 count 一致，`pagination.loaded` 与实际返回行数一致。

## 任务中心与通知页

真实页面显示摘要：

- 任务中心任务总量：`2,517,685`
- 生命周期计划：`2,479,992/2,479,992`
- 通知计划：`21,431/22,437`
- 通知计划活跃用户：`21,429`
- 通知近期：`3,428`
- 通知未来：`18,001`
- 通知历史：`14,960`

未发现本轮开关验证导致任务中心或通知计划统计漂移。

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
pnpm -F @vben/web-antd typecheck
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_list_order_detail_uses_short_back_callback bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_from_extreme_order_detail_stays_under_limit bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_recompacts_nested_asset_detail_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_ip_query_actions_return_to_query_menu bot.tests.RetainedIpRenewalUiTestCase.test_cloud_auto_renew_callbacks_keep_nested_back_under_limit --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/lifecycle_execution.py cloud/lifecycle_plan_queries.py bot/api.py
git diff --check
```

红线扫描通过。命中项为既有测试桩账号字符串、Telegram 登录账号 API 文件名，以及 `CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实回流。

## 清理

- 已删除本轮临时后台登录用户 `codex_patrol_lifecycle_probe`。
- 已删除 `/private/tmp/shop_lifecycle_probe_token.txt`。
- 三个生命周期总开关均已恢复为 `1`。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥、云厂商密钥或完整代理链接。

## 下一步

- 继续巡检代理列表资产详情页的三类单项开关和计划页之间是否同步。
- 继续关注生命周期执行器的关机完成后进入删机、删机完成后进入 IP 删除的任务状态流转。
