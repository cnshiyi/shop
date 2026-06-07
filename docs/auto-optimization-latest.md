# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 20:05 CST
- 状态：修复后台云订单编辑到期时间时未同步同订单全部服务器资产，避免 `CloudAsset.actual_expires_at` 事实分裂。
- 本轮范围：生命周期事实专项巡检、运行时旧入口扫描、后台订单到期时间同步修复、聚焦回归测试。

## 修复摘要

- `TODO.md` 已无未完成条目，本轮按固定巡检清单执行“生命周期事实与旧兼容回流”专项审计。
- 扫描确认运行时 `INSTALLED_APPS` 仍只包含 `core`、`bot`、`orders`、`cloud`，未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- 运行时代码未发现 `service_expires_at`、旧退款入口或旧计划快照表回流；`dashboard_plan_snapshots` 命中为当前后台计划投影刷新逻辑，不是旧架构残留。
- 审计过程触发真实回归：后台 `cloud_order_detail` 编辑 `actual_expires_at` 时只通过 `_update_order_primary_records()` 更新主记录，导致同订单其他服务器资产仍保留旧到期时间，`order_asset_expiry(order)` 可继续读到旧值。
- 已改为在订单后台编辑到期时间时调用 `set_order_asset_expiry(order, asset_expires_at, update_lifecycle=False)`，统一同步同订单全部服务器资产，再保留原有主记录字段同步逻辑处理 IP、名称、状态等非到期字段。

## 数据与结论

- 本轮不涉及真实云资源、真实支付、链上广播、数据库删除或生产发布。
- 本轮属于后端生命周期事实修复，不涉及前端代码改动；前端仓库空白检查保持通过。
- 压测数据规模：本轮未进行 10 万级以上压测，属于单点生命周期回归修复与聚焦测试。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_dashboard_order_expiry_update_syncs_asset_expiry_and_lifecycle_plan cloud.tests.CloudServerServicesTestCase.test_cloud_asset_detail_does_not_fallback_to_order_asset_expiry cloud.tests.CloudServerServicesTestCase.test_aws_notice_schedule_does_not_override_manual_order_expiry --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_orders.py cloud/asset_expiry.py cloud/api_asset_edit.py cloud/models.py
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

结果：3 个生命周期到期事实聚焦测试、Django 系统检查、编译检查和前后端空白检查均通过。SQLite 测试中的 `db_comment` 警告仍为已知数据库能力差异。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。

## 剩余风险

- 当前后端工作区仍有未提交业务改动：`cloud/api_tasks.py`、`cloud/task_center.py`、`cloud/tests_task_center.py`；本轮未介入。
- 任务中心、生命周期计划、通知计划的 50 万到 100 万级真实翻页和数据库精确对账仍待下一轮继续。
- 当前没有 `logged_in` 状态的 Telegram 登录账号，机器人真机菜单/回调点击仍无法做真实账号验证。
