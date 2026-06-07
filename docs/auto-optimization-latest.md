# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 03:28 CST
- 状态：完成一轮生命周期计划页真实前端、数据库分页对账、机器人并发和红线巡检；本轮未改业务代码。
- 本轮范围：`/admin/tasks/plans` 计划页、生命周期计划查询层、IP 删除历史深分页、机器人监听推送并发隔离、Django check、红线关键字扫描。

## 真实页面

使用 Playwright 真实打开：

- `http://127.0.0.1:5666/admin/tasks/plans`

页面确认：

- 页面标题为 `计划 - Vben Admin Antd`。
- 关机服务器、删除服务器、删除 IP 三个总开关均显示。
- 显示列开关包含服务器 IP、IP、固定 IP/资产、用户、备注、订单号、服务到期、关机时间、删除/释放时间、真实状态、计划状态、关机开关、删机开关、IP 删除开关、执行状态、云厂商、云上状态、删除来源、队列状态、记录时间、执行时间、结果、执行错误、失败原因、操作。
- 首屏五张表均真实渲染。

## 数据口径

当前计划页口径：

- 关机计划：`1879990`
- 删除计划：`2`
- 服务器删除历史：`20010`
- IP 删除计划：`500000`
- IP 删除历史：`520010`

数据库分页真实性对账已覆盖第 1 页、第 2 页、第 10 页、深页和末页：

- 关机计划：第 1、2、10、1000、18800 页通过；末页 `90` 条。
- 服务器删除计划：第 1 页通过；共 `2` 条。
- IP 删除计划：第 1、2、10、1000、5000 页通过。
- 服务器删除历史：第 1、2、10、201 页通过；末页 `10` 条。
- IP 删除历史：第 1、2、10、1000、5201 页通过；末页 `10` 条。

所有分页检查均满足：

- `loaded` 与预期条数一致。
- 单页内无重复。
- 被抽查页之间无重叠。
- 末页条数正确。

## 前端翻页

实际点击 IP 删除历史分页：

- 第 2 页：页面显示 `101-200 / 共 520010 条`，耗时约 `4.8s`。
- 末页 `5201`：页面显示 `IP 删除历史记录（已加载 10 / 总 520010）` 和 `520001-520010 / 共 520010 条`，耗时约 `4.6s`。

结论：本轮未发现 IP 删除历史计划/记录混淆，也未发现翻页丢数据。

## 机器人并发

已将用户要求的“机器人多任务高并发”纳入本轮验证。聚焦测试通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated --settings=shop.settings --verbosity=1
```

该用例验证多用户并发发送时，Telegram 监听推送的复制包装上下文不会串用户。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_ip_delete_history_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_history_includes_orphan_deleted_server_asset cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_history_mixes_orders_and_assets_by_updated_at cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract cloud.tests.CloudServerServicesTestCase.test_ip_delete_history_page_sources_reverse_tail_keeps_order cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_mixes_logs_and_assets_by_time cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_separate_ip_delete_plan_and_history_items --settings=shop.settings --verbosity=1
git diff --check
```

说明：

- SQLite `db_comment` 警告仍是已知数据库能力差异。
- 一次聚焦测试命令曾包含不存在的测试名，已纠正后重跑通过；该失败不是业务断言失败。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。
- 红线扫描命中的 `core.dashboard_api` 是当前公共后台 API 工具模块导入；`service_expires_at` 命中仅在历史迁移文件中。
- 本轮未打印 Telegram token、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。

## 下一步

- 继续下一轮巡检时，优先覆盖代理列表各标签在百万级数据下的前端跳页和数据库对账。
- 继续补强机器人多任务高并发覆盖，扩大到资产详情、续费、换 IP、重装迁移/重建、修改配置等 callback 返回链。
- 继续关注 IP 删除历史深页耗时，当前真实前端末页约 `4.6s`，仍可作为下一轮优化候选。
