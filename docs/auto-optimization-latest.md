# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 17:37 CST
- 状态：完成代理列表 IP 视图 250 万快照、每标签 10 万级数据的真实页面与数据库分页对账；补查生命周期计划、通知计划和机器人多任务高并发。
- 后端 Commit：本轮无业务代码变更，只有巡检记录待提交。
- 前端 Commit：无。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 重点：
  - 代理列表 IP 视图全部标签
  - 代理列表服务端分页、深页和最后页准确性
  - 生命周期计划与通知计划统计稳定性
  - 机器人多任务高并发和返回链

## 数据规模

当前 `CloudAssetDashboardSnapshot` 快照总量：

- 快照总数：`2,500,003`
- 全部标签显示分页：`2,489,998`
- 运行中：`549,988`
- 即将到期：`101,250`
- 已过期：`101,752`
- 未附加固定 IP：`100,001`
- 异常/待确认：`100,000`
- 云账号异常：`1,145,002`
- 关机计划关闭：`100,384`
- 未绑定用户：`100,001`
- 未绑定群组：`100,013`
- 续费关闭：`104,558`

所有标签均满足 10 万级压测基线。

## 数据对账

服务端接口 `/api/admin/cloud-assets/` 与数据库快照排序逐行对账通过：

- 覆盖标签：`all/normal/due_soon/expired/unattached_ip/abnormal/account_disabled/shutdown_disabled/unbound_user/unbound_group/auto_renew_off`
- 覆盖页位：第 `1` 页、第 `2` 页、第 `1000` 页、最后页。
- 共对账 `44` 个页位。
- API `total` 与数据库 `count` 一致。
- API 返回 ID 顺序与数据库 `_dashboard_snapshot_ordering()` 一致。
- 第 `1` 页和第 `2` 页未发现重复 ID。
- 最后页覆盖反向分页优化路径，未发现丢数据或串页。

## 真实前端验证

实际打开：

```text
http://127.0.0.1:5666/admin/cloud-assets
```

真实页面结果：

- IP 视图逐个点击全部标签，分页 total 与接口 total 一致。
- 之前疑似异常的“运行中”标签已复测，页面显示 `共 549988 条代理`，不再沿用全部标签分页。
- 每个标签都真实点击下一页，均返回第 `2` 页 `20` 条数据。
- 额外对 `全部/运行中/未附加固定IP/云账号异常/续费关闭` 使用快速跳页到第 `1000` 页，均返回 `20` 条数据且 total 正确。
- 控制台 error/warning：`0`
- 业务 API 失败：`0`

截图文件：

```text
/private/tmp/shop-cloud-assets-ip-tags.png
/private/tmp/shop-cloud-assets-ip-tags-pagination.png
```

## 生命周期与通知计划

代理列表压测后补查任务中心和计划页：

- 任务中心生命周期：`2,479,992/2,479,992`
- 任务中心通知计划：`21,431/22,437`
- 关机计划：`1,979,990`
- 服务器删除计划：`2`
- 服务器删除历史：`20,010`
- IP 删除计划：`500,000`
- IP 删除历史：`520,010`
- 通知计划活跃用户：`21,429`
- 通知近期：`3,428`
- 通知未来：`18,001`
- 通知历史：`14,960`

未发现代理列表标签压测导致生命周期计划或通知计划统计变化。

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
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_list_order_detail_uses_short_back_callback bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_from_extreme_order_detail_stays_under_limit bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_recompacts_nested_asset_detail_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_ip_query_actions_return_to_query_menu bot.tests.RetainedIpRenewalUiTestCase.test_cloud_auto_renew_callbacks_keep_nested_back_under_limit --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过。命中项为既有测试桩账号字符串、Telegram 登录账号 API 文件名，以及 `CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实回流。

## 清理

- 已删除本轮临时后台登录用户 `codex_patrol_assets_probe`。
- 已删除 `/private/tmp/shop_assets_probe_token.txt`。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥、云厂商密钥或完整代理链接。

## 下一步

- 继续巡检生命周期执行器的总开关、单项关机开关、删机开关、IP 删除开关联动。
- 后续如执行真实云资源创建/删除，必须单独更新 `docs/real-machine-test-report.md`，并脱敏资源 ID。
