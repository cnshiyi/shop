# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 16:13 CST
- 状态：已完成生命周期资产开关隔离专项。
- 本轮范围：未附加固定 IP 删除、订单固定 IP 回收、AWS 同步释放未附加固定 IP、生命周期计划关机开关展示。

## 修改摘要

- AWS 同步释放未附加固定 IP 时，不再读取资产关机开关 `shutdown_enabled`，改为读取资产 IP 删除开关 `ip_delete_enabled`。
- 未附加固定 IP 的资产关机开关关闭时，IP 删除仍可进入执行队列；只有资产 IP 删除开关关闭时才阻止释放。
- 订单删除后的固定 IP 回收同样只受 IP 删除开关影响，不再被资产关机开关误挡。
- 生命周期计划页继续明确展示关机计划受关机开关控制，文案从泛化“资产开关关闭”收敛为“关机开关关闭 / 关机计划开关关闭”。
- 补齐聚焦测试，防止“关机开关误挡 IP 删除”回流。

## 验证

本地已通过：

```bash
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_ignores_shutdown_disabled_asset cloud.tests.CloudServerServicesTestCase.test_due_orders_recycle_ignores_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_respects_asset_ip_delete_disabled cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_asset_ip_delete_disabled cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_ignores_shutdown_disabled_account cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_global_ip_delete_switch cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ignore_account_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_asset_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_due_orders_global_shutdown_switch_does_not_block_delete_or_recycle --settings=shop.settings --verbosity=1
uv run python manage.py check
uv run python -m py_compile bot/api.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/lifecycle_plan_queries.py cloud/management/commands/sync_aws_assets.py cloud/tests.py
git diff --check
```

结果：10 个生命周期聚焦测试、Django 系统检查、编译检查和空白检查均通过。SQLite 测试输出的 `db_comment` 警告为已知数据库能力差异。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款入口、旧 `Server` 兼容壳或旧云 API 聚合入口。

## 剩余风险

- 本轮只修复生命周期单项开关隔离，不包含真实云资源执行。
- 下一轮继续做计划页、通知页、代理列表的大数据真实性、翻页、跳页和性能压测。
