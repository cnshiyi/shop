# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 14:22 CST
- 状态：已完成“再查一轮兼容代码”，并移除新发现的机器人旧 callback 兼容入口。
- 本轮范围：机器人回调、云资产兼容壳、旧云 API 聚合入口、旧 Server 入口、旧计划字段、旧退款入口、废弃 runtime app 回流扫描。

## 修改摘要

- 删除机器人旧资产详情 callback 入口：
  - 不再注册 `cloud:assetdetail:`
  - 不再在资产详情处理器中解析 `cloud:assetdetail:<id>` 或 `cloud:assetdetail:<kind>:<id>`
  - `compact_callback_path()` 不再把旧 `cloud:assetdetail:` 压缩为 `cad:` / `csd:`
- 删除机器人旧续费钱包支付 callback 入口：
  - 不再注册 `cloud:renewpay:`
  - 当前续费钱包支付只保留 `cloud:rp:` 和超短 `p:`
- 保留当前有效入口：
  - 资产详情：`cloud:ad:`、`cad:`、`csd:`
  - 续费钱包支付：`cloud:rp:`、`p:`
  - 订单级关闭提醒：`cloud:mute:` 仍是当前按钮使用的订单提醒开关，不属于旧用户级静默兼容分支。
- 更新机器人回调测试，改为断言旧入口不存在，并继续覆盖当前短回调和返回链。

## 验证

本地已通过：

```bash
uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=1
git diff --check
```

结果：编译通过；Django 系统检查通过；机器人返回链与回调聚焦测试 49 条通过；diff 空白检查通过。

## 红线扫描

运行时代码扫描已通过：

```bash
rg -n "cloud:assetdetail|cloud:renewpay|custom:port|cloud:ipport|waiting_port|set_cloud_server_port|bot_custom_port|bot_set_port" bot cloud core orders shop -g '!*/migrations/*' -g '!*/tests.py' -g '!*/tests_*.py'
rg -n "legacy|compat|兼容|server_records|reconcile_cloud_assets_from_servers|_order_primary_server|server_updates|task-list-compat|plan-settings-compat|mute_cloud_reminders|unmute_cloud_reminders|Server\\.objects|cloud\\.server_records|compat_server_record|sync_state__compat_server_record" shop core bot orders cloud -g '!*/migrations/*' -g '!*/tests.py' -g '!*/tests_*.py'
rg -n "\\bfrom cloud import api\\b|\\bimport cloud\\.api\\b|\\bfrom cloud\\.api import\\b|\\bfrom cloud\\.api$|cloud/api\\.py|cloud\\.api\\b" shop core bot orders cloud -g '!*/migrations/*' -g '!*/tests.py' -g '!*/tests_*.py'
rg -n "service_expires_at|old_refund|legacy_refund|refund_cloud_order|refund_order|apply_refund|process_refund|create_refund|CloudLifecyclePlanSnapshot|CloudNoticePlanSnapshot|PlanSnapshot|lifecycle_plan_projection" shop core bot orders cloud -g '!*/migrations/*' -g '!*/tests.py' -g '!*/tests_*.py'
```

结果：无命中。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款入口、旧 `Server` 兼容壳或旧云 API 聚合入口。

## 剩余风险

- 历史文档和版本记录中仍保留旧兼容关键词，用于追溯历史，不代表运行时代码仍保留兼容入口。
- 默认 MySQL 全量测试仍可能遇到已有测试库 `test_a` 的交互确认；本轮未删除测试库，继续使用 SQLite 隔离库跑聚焦测试。
