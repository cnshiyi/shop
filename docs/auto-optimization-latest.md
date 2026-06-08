# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 18:45 CST
- 状态：修复生命周期计划页把“实例已删除但固定 IP 保留中”的 IP 删除计划行误标为完成态的问题，并按用户最新要求把压测范围收敛到 10 万级。
- 后端 Commit：待提交。
- 前端 Commit：本轮无前端变更。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 重点：
  - 生命周期执行器关机、删机、IP 删除三阶段串行推进。
  - 手动删机后保留固定 IP 的资产继续进入 IP 删除计划，不混入 IP 删除历史。
  - 计划页 10 万级边界分页对账。
  - 通知计划现有全量分页对账。
  - 真实前端计划页打开、显示和翻页。
  - 机器人高并发和返回链固定回归。

## 本轮发现

- 计划页装饰层会先识别“实例已删除 + 固定 IP 保留”，并直接标记 `plan_state=completed`。
- 对服务器删除计划行，这个状态是合理的：服务器已删，只剩固定 IP 回收。
- 但对 IP 删除计划行，这会把仍需释放的固定 IP 误显示为完成态，容易造成“IP 删除计划”和“IP 删除记录”混淆。
- 通知计划当前数据未达到 10 万分组：活跃通知分组 `21429`，通知历史 `14960`。本轮只做现有全量范围内的首尾页对账，没有伪造 10 万级通知压测结论。

## 本轮修复

后端文件：

```text
/Users/a399/Desktop/data/shop/bot/api.py
/Users/a399/Desktop/data/shop/cloud/lifecycle_plan_queries.py
/Users/a399/Desktop/data/shop/cloud/tests.py
```

修复内容：

- `decorate_plan_item` 对“实例已删除 + 固定 IP 保留”的完成态判断排除 IP 删除计划行。
- 固定 IP 保留中的资产继续留在 `ip_delete_plan_items`，计划页显示为待回收计划。
- `retained_unattached_ip_q()` 统一识别固定 IP 保留中的未附加 IP。
- IP 删除历史查询排除仍处于固定 IP 保留中的资产，避免计划和历史混淆。
- 新增聚焦测试：
  - 手动删机且固定 IP 保留后，计划页不再显示服务器删除计划行，而显示 IP 删除计划行。
  - 生命周期执行器同一轮只推进一个破坏性阶段：第一轮关机，第二轮删机，不在同一轮释放 IP。

## 10 万级对账

计划页对账通过，每页 `50` 条：

- 关机计划：第 `1/2/2000` 页，total `1979990`，每页 loaded `50`。
- IP 删除计划：第 `1/2/2000` 页，total `500000`，每页 loaded `50`。
- IP 删除历史：第 `1/2/2000` 页，total `520010`，每页 loaded `50`。

结论：

- 接口 `pagination.{table}.total/loaded/page/page_size` 与数据库查询层一致。
- 接口返回的资产 ID 或历史来源 ID 顺序与数据库分页结果一致。
- 没有发现第 1 页、第 2 页和 10 万边界页丢数据、重复数据或串页。

通知计划现有全量对账通过：

- 活跃通知分组：total `21429`，offset `0/50/21400` 对账通过。
- 通知历史：total `14960`，offset `0/50/14950` 对账通过。
- 近期通知 `3428`，未来通知 `18001`。

## 真实前端验证

实际打开：

```text
http://127.0.0.1:5666/admin/tasks/plans
```

真实页面结果：

- 页面停留在计划页，没有被登录页或错误页重定向。
- 页面显示 `关机计划`、`IP 删除计划`、`IP 删除历史`。
- 首次计划接口返回：
  - 关机计划 total `1979990`，page `1`，loaded `50`
  - IP 删除计划 total `500000`，loaded `50`
  - IP 删除历史 total `520010`，loaded `50`
- 点击分页下一页后，关机计划返回 page `2`，loaded `50`。
- 控制台 error/warning：`0`
- 业务 API 失败：`0`
- 浏览器层 `requestfailed` 为 Vite 开发环境脚本 `net::ERR_ABORTED`，非业务 API。

截图：

```text
/private/tmp/shop-lifecycle-plans-10w-front.png
```

## 机器人高并发

机器人 8 条聚焦测试通过，覆盖：

- 通知复制并发隔离。
- 钱包直付 / 钱包补付并发。
- `60` 路批量后台任务隔离。
- 订单详情、资产详情、IP 查询、自动续费返回链。
- `callback_data <= 64` 字节限制。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py cloud/lifecycle_plan_queries.py cloud/lifecycle.py cloud/lifecycle_execution.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_manual_order_delete_with_retained_ip_moves_into_ip_delete_plan cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_serializes_shutdown_delete_and_ip_release_stages cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_separate_ip_delete_plan_and_history_items cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_ip_delete_history_item --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_list_order_detail_uses_short_back_callback bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_from_extreme_order_detail_stays_under_limit bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_recompacts_nested_asset_detail_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_ip_query_actions_return_to_query_menu bot.tests.RetainedIpRenewalUiTestCase.test_cloud_auto_renew_callbacks_keep_nested_back_under_limit --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过。命中项为既有测试桩、Telegram 登录账号 API 文件名，以及 `CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实回流。

## 清理

- 已删除临时后台登录用户：
  - `codex_patrol_frontend_plan_probe`
  - `codex_patrol_lifecycle_10w_probe`
  - `codex_patrol_notice_probe`
- 已删除 `/private/tmp/shop_frontend_plan_probe_token.txt`。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥、云厂商密钥或完整代理链接。

## 下一步

- 继续优化计划页 10 万边界页加载耗时，尤其是 IP 删除历史合并日志和资产来源的深页查询。
- 如需通知计划达到 10 万级，需要单独设计可清理的通知分组压测数据，不把当前 `21429/14960` 的现有量误写成 10 万级。
