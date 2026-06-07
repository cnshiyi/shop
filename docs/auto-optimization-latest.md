# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 04:04 CST
- 状态：完成生命周期阶段开关专项巡检，并修复一个测试口径问题。
- 本轮范围：生命周期关机/删机/IP 删除总开关、资产单项开关、未附加 IP 默认 15 天删除计划、计划页真实渲染、基础检查、编译检查、红线扫描。

## 发现与修复

发现：

- 首次运行生命周期专项时，`test_lifecycle_plans_ignore_account_shutdown_disabled_plan_state` 失败。
- 失败原因不是运行时代码，而是测试没有隔离 `cloud_server_delete_enabled`。
- 当前安全默认值是 `cloud_server_delete_enabled=0`，删除阶段被服务器删除总开关挡住是正确行为。
- 该测试要验证的是“云账号关机开关关闭，不应该影响服务器删除阶段”，因此需要显式打开服务器删除总开关。

修复：

- `cloud/tests.py`
  - 在 `test_lifecycle_plans_ignore_account_shutdown_disabled_plan_state` 开头增加 `SiteConfig.set('cloud_server_delete_enabled', '1')`。
  - 保持运行时代码不变。

## 生命周期专项测试

已通过 16 个聚焦测试，覆盖：

- 关机总开关默认开启。
- 关机总开关只阻止计划关机，不阻止删机或 IP 回收。
- 资产 `shutdown_enabled=False` 阻止关机执行。
- 资产 `server_delete_enabled=False` 阻止服务器删除计划执行。
- 资产 `ip_delete_enabled=False` 阻止 IP 删除计划执行。
- 关机计划完成后才进入服务器删除计划。
- 未附加 IP 缺少到期时间时生成默认 15 天后删除计划。
- 未附加 IP 有到期时间时使用 `CloudAsset.actual_expires_at`。
- IP 删除执行器尊重资产单项 IP 删除开关和全局 IP 删除总开关。

命令：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_server_shutdown_enabled_defaults_on cloud.tests.CloudServerServicesTestCase.test_global_shutdown_switch_blocks_scheduled_suspend cloud.tests.CloudServerServicesTestCase.test_due_orders_skip_suspend_when_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_due_orders_global_shutdown_switch_does_not_block_delete_or_recycle cloud.tests.CloudServerServicesTestCase.test_lifecycle_suspend_execution_guard_respects_asset_shutdown_disabled cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ignore_account_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_asset_shutdown_disabled_plan_state cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_split_shutdown_before_server_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_global_stage_switches cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_fill_missing_expiry_with_default_delete_plan cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_use_actual_expiry_as_delete_plan cloud.tests.CloudServerServicesTestCase.test_order_static_ip_release_respects_asset_ip_delete_disabled cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_asset_ip_delete_disabled cloud.tests.CloudServerServicesTestCase.test_aws_sync_release_static_ip_respects_global_ip_delete_switch cloud.tests.CloudServerServicesTestCase.test_unattached_static_ip_due_scan_fills_missing_expiry_as_future_plan --settings=shop.settings --verbosity=1
```

结果：`Ran 16 tests`，全部通过。SQLite `db_comment` 警告仍是测试数据库能力差异。

## 真实页面

本轮创建一次临时后台 session，仅用于真实 Chrome 页面巡检；结束时已删除 session 和临时文件。

真实打开：

- `http://127.0.0.1:5666/admin/tasks/plans`
- 标题：`计划 - Vben Admin Antd`
- 耗时：约 `7.7s`
- 控制台：`0 error / 0 warning`

滚动到底部后确认五个区域均存在：

- 关机计划
- 删除计划
- 服务器删除历史
- IP 删除计划
- IP 删除历史

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/tests.py bot/api.py cloud/lifecycle_execution.py cloud/lifecycle_plan_queries.py
git diff --check
```

红线扫描：

```bash
rg -n "service_expires_at|CloudLifecyclePlanSnapshot|legacy_refund|old_refund|from accounts|from finance|from mall|from monitoring|from dashboard_api|from biz|import accounts|import finance|import mall|import monitoring|import dashboard_api|import biz" shop core bot orders cloud -g '*.py'
```

扫描结果：

- `service_expires_at` 只命中历史 migrations。
- 未命中运行时代码中的旧计划快照、旧退款函数名或废弃 runtime app 导入。

## 清理

- 已删除临时后台 session：`deleted=1`。
- 未发现 `.playwright-cli`、`playwright-report` 或 `test-results` 临时产物。
- 未留下截图、临时脚本或有效后台 session。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。
- 本轮未打印 Telegram token、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。

## 下一步

- 继续循环巡检代理列表高数据量标签翻页、任务中心统计和通知计划口径。
- 下一轮如再触发生命周期测试失败，优先判断是安全默认值、测试隔离还是运行时代码问题。
