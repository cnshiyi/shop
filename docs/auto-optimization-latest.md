# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 15:57 CST
- 状态：完成生命周期计划、通知计划、真实前端分页跳页、机器人多任务高并发专项巡检。
- Commit：本轮记录随本轮提交一起保存。

## 本轮范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 真实前端：
  - `http://127.0.0.1:5666/admin/tasks/plans`
  - `http://127.0.0.1:5666/admin/tasks/notices`
- 本轮未改业务代码。

## 数据库与 API 对账

真实 MySQL，后端 API 走 `/api/admin/tasks/plans/` 和 `/api/admin/tasks/notices/`，每页按页面默认或页面契约校验。

生命周期计划计数：

- 关机计划：`1979990`
- 删除计划：`2`
- 服务器删除历史：`20010`
- IP 删除计划：`500000`
- IP 删除历史：`520010`

生命周期计划分页对账：

- 关机计划：第 `1` 页、第 `2` 页、第 `1000` 页、第 `39600` 页，API 资产 ID 与查询层同排序切片一致。
- 删除计划：第 `1` 页，API 资产 ID 与查询层同排序切片一致。
- 服务器删除历史：第 `1` 页、第 `2` 页、第 `401` 页，API 来源类型/来源 ID 与查询层合并来源一致。
- IP 删除计划：第 `1` 页、第 `2` 页、第 `1000` 页、第 `10000` 页，API 资产 ID 与查询层同排序切片一致。
- IP 删除历史：第 `1` 页、第 `2` 页、第 `1000` 页、第 `10401` 页，API 来源类型/来源 ID 与查询层合并来源一致。
- 抽样页内和跨抽样页均未发现重复 ID。

通知计划计数：

- 到期通知：`3428`
- 未来通知：`18001`
- 到期用户通知：`3428`
- 未来用户通知：`18001`
- 活动用户通知：`21429`
- 通知历史：`14960`

通知计划分页对账：

- 活动通知：第 `1` 页、第 `2` 页、第 `1000` 页、第 `2143` 页，API ID 与同口径构建结果一致。
- 通知历史：第 `1` 页、第 `2` 页、第 `1000` 页、第 `1496` 页，API ID 与同口径构建结果一致。
- 抽样页内和跨抽样页均未发现重复 ID。

## 真实前端结果

计划页真实打开并操作：

- 关机计划首屏：`50` 行，分页显示 `1-50 / 共 1979990 条`。
- 删除计划首屏：`2` 行，分页显示 `1-2 / 共 2 条`。
- 服务器删除历史首屏：`50` 行，分页显示 `1-50 / 共 20010 条`。
- IP 删除计划首屏：`50` 行，分页显示 `1-50 / 共 500000 条`。
- IP 删除历史首屏：`50` 行，分页显示 `1-50 / 共 520010 条`。
- 关机计划实际点击第 `2` 页：`50` 行，分页显示 `51-100 / 共 1979990 条`。
- IP 删除计划实际快速跳页到第 `1000` 页：`50` 行，分页显示 `49951-50000 / 共 500000 条`。
- IP 删除历史实际快速跳页到第 `10401` 页：`10` 行，分页显示 `520001-520010 / 共 520010 条`。

通知页真实打开并操作：

- 通知计划首屏：`10` 行，总页码显示到 `2143`。
- 历史通知首屏：`10` 行，总页码显示到 `1496`。
- 通知计划实际点击第 `2` 页：`10` 行。
- 历史通知实际点击第 `2` 页：`10` 行。

浏览器结果：

- 任务 API 请求数：`7`
- 失败 API：`0`
- 控制台 error/warning：`0`
- requestfailed：`0`

## 机器人高并发复测

通过固定并发回归：

- `bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated`
- `bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated`
- `bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated`

覆盖：

- 通知复制包装器并发隔离。
- 钱包直付和钱包补付同时执行。
- `20` 组批量钱包直付、`20` 组钱包补付、`20` 组续费后巡检，总计 `60` 路并发任务。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_mixes_logs_and_assets_by_time cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_deep_group_page_has_no_duplicates cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_global_stage_switches bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\." cloud bot orders core shop -g '!**/migrations/**'
```

命中项仍为 Telegram 登录账号测试桩和 `CloudServerOrder.ip_recycle_at` 同步语句，不是旧到期事实回流。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、支付密钥、云厂商密钥或完整代理链接。
- 本轮创建过一次性后台验证用户和 session，验证后已清理。

## 下一步

- 继续巡检代理列表所有标签，特别是未附加、未绑定用户、未绑定群组、续费关闭等大数据标签。
- 继续压测生命周期计划和通知计划深页，观察是否出现超过 `2s` 的慢页。
- 继续把机器人多任务高并发作为每轮固定回归项。
