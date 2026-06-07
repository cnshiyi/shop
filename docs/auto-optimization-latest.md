# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 06:59 CST
- 状态：完成一轮生命周期计划、开关联动、执行窗口和真实页面巡检；未发现需要修改代码的问题。
- 本轮范围：关机计划、删除计划、服务器删除历史、IP 删除计划、IP 删除历史、生命周期全局/单项开关、真实 HTTP、真实前端页面。

## 巡检结论

- 后端生命周期聚焦测试全部通过：
  - 关机总开关默认开启。
  - 关机总开关关闭时阻止计划关机。
  - 关机总开关不阻止删除或 IP 回收阶段。
  - 生命周期计划页按关机计划、删除计划、IP 删除计划拆分。
  - 资产级 `shutdown_enabled`、`server_delete_enabled`、`ip_delete_enabled` 分阶段生效。
  - 全局关机、删机、IP 删除开关在计划页展示对应状态。
  - IP 删除执行窗口、未附加 IP 删除窗口、执行前重算删除时间均生效。
- 真实 HTTP：
  - 关机计划 `1979990`，分页 `loaded=20/total=1979990`。
  - 删除计划 `2`，分页 `loaded=2/total=2`。
  - 服务器删除历史 `20010`，分页 `loaded=20/total=20010`。
  - IP 删除计划 `500000`，分页 `loaded=20/total=500000`。
  - IP 删除历史 `520010`，分页 `loaded=20/total=520010`。
  - 缺少到期时间 `251`，未附加 IP `600001`。
- 真实前端页面：
  - `/admin/tasks/plans` 加载成功，接口返回 page size 50 的五张表分页。
  - 页面可见关机计划、删除计划、服务器删除历史、IP 删除计划、IP 删除历史。
  - 页面控制台错误数：`0`。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_server_shutdown_enabled_defaults_on cloud.tests.CloudServerServicesTestCase.test_global_shutdown_switch_blocks_scheduled_suspend cloud.tests.CloudServerServicesTestCase.test_due_orders_global_shutdown_switch_does_not_block_delete_or_recycle cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_split_shutdown_before_server_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_global_stage_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_recycle_respects_ip_delete_time_window cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_unattached_ip_uses_ip_delete_time_window cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_orphan_asset_delete_time_before_cloud_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_rechecks_unattached_ip_delete_time_before_release --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
git diff --check
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\." cloud bot orders core shop -g '!**/migrations/**'
```

说明：

- SQLite 的 `db_comment` warnings 仍是已知测试噪声。
- 临时后台 session 已删除。
- Playwright 临时截图目录已删除。

## 受限项

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮没有打印密钥、Telegram session、支付密钥或完整代理链接。
- 本轮没有业务代码改动，仅记录巡检结果。

## 下一步

- 继续不停轮巡检，下一轮优先覆盖机器人全功能 callback 返回链、资产详情/订单详情/续费/换 IP/重装/修改配置路径。
- 继续关注生命周期真实云资源测试计划；如进入真机云资源操作，需要单独更新 `docs/real-machine-test-report.md` 并脱敏资源 ID。
