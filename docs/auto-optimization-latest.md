# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 13:17 CST
- 状态：完成一轮生命周期计划页专项巡检，覆盖关机/删机/IP 删除三阶段开关联动、任务中心聚焦测试和 10 万级真实接口分页对账；未发现需要改代码的问题。
- 本轮范围：生命周期计划页、阶段总开关、资产单项开关、任务中心生命周期统计、红线扫描。

## 巡检结论

- Django 基础检查通过：`uv run python manage.py check` 无报错。
- 生命周期与任务中心聚焦测试通过：`18` 个测试全部通过，覆盖关机总开关、阶段单项开关、关机完成后进入删机计划、任务中心生命周期失败/待执行统计。
- 10 万级专项压测通过：在独立临时 SQLite 审计库构造 `101003` 条 `CloudAsset`，其中：
  - 关机计划资产：`50001` 条。
  - 服务器删除计划资产：`50001` 条。
  - 未附加固定 IP 删除计划资产：`1001` 条。
- 真实接口 `lifecycle_plans` 与查询层分页逐页对账一致：
  - 关机计划：第 `1/2/1000/2501` 页全部一致。
  - 服务器删除计划：第 `1/2/1000/2501` 页全部一致。
  - IP 删除计划：第 `1/2/51` 页全部一致。
- 资产单项开关状态正确落到接口：
  - 关机计划关闭资产返回 `shutdown_disabled`。
  - 服务器删除计划关闭资产返回 `server_delete_disabled`。
  - IP 删除计划关闭资产返回 `ip_delete_disabled`。
- 生命周期总开关状态正确落到接口：
  - 关机总开关关闭返回 `global_shutdown_disabled`。
  - 服务器删除总开关关闭返回 `global_server_delete_disabled`。
  - IP 删除总开关关闭返回 `global_ip_delete_disabled`。
- 本轮未发现旧到期字段、旧计划快照、旧退款逻辑或废弃 runtime app 回流。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_global_stage_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_split_shutdown_before_server_delete cloud.tests.CloudServerServicesTestCase.test_due_orders_global_shutdown_switch_does_not_block_delete_or_recycle cloud.tests_task_center.CloudTaskCenterApiTestCase --settings=shop.settings --verbosity=1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-lifecycle-audit.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py migrate --settings=shop.settings --noinput
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-lifecycle-audit.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py shell --settings=shop.settings <<'PY'
# 构造 101003 条 CloudAsset，调用 lifecycle_plans 真接口，
# 对账关机/删机/IP 删除分页与 cloud.lifecycle_plan_queries 直接查询结果。
PY
```

压测结果摘要：

```text
关机计划：50001 条，第 1/2/1000/2501 页全部一致；首屏约 320.67ms
服务器删除：50001 条，第 1/2/1000/2501 页全部一致；首屏约 24.37ms
IP 删除计划：1001 条，第 1/2/51 页全部一致；首屏约 9.69ms
总开关关闭后的三阶段接口返回均正确进入 global_*_disabled 状态
```

红线扫描建议沿用：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\." cloud bot orders core shop -g '!**/migrations/**'
```

## 受限项

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、支付密钥、云厂商密钥或完整代理链接。
- 前端仓库 `/Users/a399/Desktop/data/vue-shop-admin` 仍有本地未提交改动，本轮未做浏览器写操作或前端代码改动。

## 下一步

- 下一轮优先继续做真实前端链路巡检，覆盖任务中心计划页分页/跳页和生命周期总开关切换后的界面状态。
- 继续盯紧生命周期计划页首屏性能差异，尤其是关机计划页在冷缓存下是否存在比删机/IP 删除更高的首屏耗时。
- 继续避开当前后端 `bot/api.py`、`cloud/lifecycle_plan_queries.py`、`cloud/tests.py` 和前端脏改动区域，只做最小安全任务。
