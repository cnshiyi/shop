# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 20:44 CST
- 状态：修复任务中心和生命周期计划在百万级资产下的冷启动慢加载，并完成真实前端计划页翻页对账。
- 本轮范围：任务中心轻量预览、生命周期计划计数持久快照、生命周期测试隔离、真实页面分页验证。

## 修复摘要

- `cloud/task_center.py` 的生命周期、通知计划和自动续费总览改为轻量预览，不再为任务中心同步构建完整百万级计划。
- 自动续费置顶任务改为直接读取 `CloudAutoRenewRetryTask` 和 `CloudAutoRenewPatrolLog`，避免旧接口为了统计扫描完整自动续费计划。
- `bot/api.py` 新增生命周期计划计数持久快照 `cloud_lifecycle_plan_count_snapshot`，显式刷新时重算并写入 `SiteConfig`，普通 GET 优先复用进程缓存或持久快照。
- `cloud/tests.py` 增加生命周期计划缓存隔离，避免 `SiteConfig` 缓存和计划进程缓存跨测试污染；旧“首次刷新”测试改为显式 `refresh=1` 后再验证普通请求走缓存。
- `cloud/tests_task_center.py` 改用真实 DB 任务和历史记录验证自动续费统计，不再 mock 完整自动续费计划构建。

## 真实页面和数据库对账

- 后端已用最新代码重启：`127.0.0.1:8000`。
- 前端实际打开：`127.0.0.1:5666/admin/tasks/plans`。
- 页面显示：
  - 当前计划资产：1500000。
  - 缺少到期时间：251。
  - 未附加 IP：500001。
  - 服务器资产：999999。
  - 关机计划：已加载 50 / 总 979990。
  - IP 删除计划：共 500000。
  - IP 删除历史：520007。
- 浏览器实际点击 IP 删除历史第 2 页，页面显示 `51-100 / 共 520007 条`，首行 `LOADTEST20260605X-asset-018990`。
- 浏览器实际点击 IP 删除历史最后页 `10401`，页面显示 `520001-520007 / 共 520007 条`，首行 `20260605-7886424151-5-o92`，末行 `20260602-990000000001-5-o78-ip`。
- 数据库同一查询层对账：
  - 第 2 页 50 条，前三条为 `LOADTEST20260605X-asset-018990`、`LOADTEST20260605X-asset-018970`、`LOADTEST20260605X-asset-018950`，后三条为 `LOADTEST20260605X-asset-018050`、`LOADTEST20260605X-asset-018030`、`LOADTEST20260605X-asset-018010`。
  - 最后一页 7 条，前三条为 `20260605-7886424151-5-o92`、`20260604-7886424151-5-o91`、`20260604-7886424151-5-o90`，后三条为 `20260602-990000000001-5-o76`、`20260602-990000000001-5-o75`、`20260602-990000000001-5-o78-ip`。
- 浏览器控制台：0 error / 0 warning。
- 临时后台账号 `codex_ui_tester` 已删除，`.playwright-cli/` 临时产物清理中。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_reuses_cached_count_snapshot_after_refresh cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_reads_persisted_count_snapshot_after_process_cache_clear cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_manual_order_delete_enters_lifecycle_success_history cloud.tests_task_center.CloudTaskCenterApiTestCase --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
```

结果：20 个任务中心 / 生命周期聚焦测试和 Django 系统检查均通过。SQLite 输出的 `db_comment` 警告仍为已知数据库能力差异。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。

## 剩余风险

- 当前没有 `logged_in` 状态的 Telegram 登录账号，机器人真机菜单/回调点击仍无法完成。
- 下一轮继续覆盖任务中心、通知计划页面和代理列表页面的真实翻页、跳页和数据库对账，并继续检查生命周期全局开关 / 单项开关联动。
