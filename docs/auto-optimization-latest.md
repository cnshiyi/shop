# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 06:24 CST
- 状态：完成一轮无代码变更巡检；后端机器人整组测试、机器人高并发专项、生命周期开关专项、真实前端页面标签/分页巡检均通过。
- 本轮范围：机器人多任务高并发、机器人全量业务测试、生命周期关机/删机/IP 删除开关联动、IP 删除执行时间窗、代理列表重点标签加载、计划页末页展示和控制台错误检查。

## 巡检结论

- 机器人测试：
  - 整组 `bot.tests` 共 `106` 个测试通过。
  - 多用户通知复制并发隔离通过。
  - 钱包直付、订单补付、续费后巡检通知三类后台任务高并发隔离通过。
  - 资产详情、订单详情、续费、换 IP、重装、修改配置、管理员修改时间、返回链和 `callback_data` 64 字节限制仍由整组测试覆盖。
- 生命周期测试：
  - 资产单项 `shutdown_enabled`、`server_delete_enabled`、`ip_delete_enabled` 会正确投影到对应计划状态。
  - 全局生命周期总开关会覆盖计划页展示状态。
  - 订单固定 IP 回收和未附加固定 IP 释放都会再次校验后台配置的 IP 删除执行时间窗口。
- 真实前端页面：
  - 代理列表重点标签均能加载：未附加固定 IP、未绑定群组、关机计划关闭、续费关闭、云账号异常、全部。
  - 计划页首页关键总数显示正常：关机计划 `1979990`、删除计划 `2`、IP 删除计划 `500000`、IP 删除历史存在。
  - 计划页关机计划末页真实显示 `关机计划（已加载 40 / 总 1979990）`，分页范围 `1979951-1979990 / 共 1979990 条`。
  - 末页前 8 条可见 IP 为 `10.6.207.191`、`10.6.208.25`、`10.6.208.115`、`10.6.208.205`、`10.6.209.39`、`10.6.209.129`、`10.6.209.219`、`10.6.210.53`，与上一轮数据库末页对账一致。
  - 页面控制台错误数：`0`。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_asset_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_global_stage_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_recycle_respects_ip_delete_time_window cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_unattached_ip_uses_ip_delete_time_window --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests --settings=shop.settings --verbosity=1
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\." cloud bot orders core shop -g '!**/migrations/**'
```

真实页面巡检使用系统 Chrome 打开：

```text
http://127.0.0.1:5666/admin/cloud-assets
http://127.0.0.1:5666/admin/tasks/plans
```

说明：

- SQLite 的 `db_comment` warnings 仍是已知测试噪声。
- 红线扫描只命中当前云账号测试、Telegram 登录账号查询和 `CloudServerOrder.ip_recycle_at` 同步语句；未发现订单侧服务器到期字段、旧退款入口或废弃 runtime app 回流。
- 临时后台 session 已删除，未保留 `/private/tmp/shop_admin_session.json`、`/private/tmp/shop_pw_state.json` 或 `.playwright-cli/` 产物。

## 受限项

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮没有新增代码问题，因此只更新巡检文档并提交记录。

## 下一步

- 继续不停轮巡检，下一轮优先做任务中心/通知计划/自动续费统计口径和真实页面展示对账。
- 继续把机器人多任务高并发与生命周期开关执行链作为每轮高优先级检查项。
