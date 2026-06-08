# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 22:10 CST
- 状态：完成生命周期计划页与机器人回调专项审计；本轮不改运行代码，只更新中文记录。
- 后端 Commit：待提交。
- 前端 Commit：本轮无前端变更。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 重点链路：
  - 生命周期计划页 `lifecycle_plans`
  - 生命周期查询层 `cloud.lifecycle_plan_queries`
  - 机器人高风险回调链 `RetainedIpRenewalUiTestCase`
- 文档更新：
  - `docs/auto-optimization-latest.md`
  - `docs/refactor-version-record.md`

## 本轮审计结论

- `TODO.md` 已无未完成条目，本轮按固定巡检清单执行只读专项审计。
- 真实库规模满足本轮 10 万级以上压测要求：
  - `CloudAsset=2500003`
  - `CloudAssetDashboardSnapshot=2500003`
  - `CloudNoticeTask=6335`
  - `CloudServerOrder=50015`
- 生命周期计划真实库计数：
  - 关机计划快照总数：`1979933`
  - 服务器删除计划原始计数：`59`
  - IP 删除计划：`500000`
  - IP 删除历史：`520010`
- 机器人回调聚焦回归继续通过，未发现 `callback_data > 64 bytes` 回归。

## 10 万级以上真实库对账

只读调用真实后台接口 `lifecycle_plans`，并和查询层直接分页结果逐页比对。

关机计划：

- 第 `1` 页：`50` 条，耗时约 `395.98ms`，接口与查询层一致。
- 第 `2` 页：`50` 条，耗时约 `271.84ms`，接口与查询层一致。
- 第 `1000` 页：`50` 条，耗时约 `324.15ms`，接口与查询层一致。

IP 删除计划：

- 第 `1` 页：`50` 条，耗时约 `700.14ms`，接口与查询层一致。
- 第 `2` 页：`50` 条，耗时约 `686.94ms`，接口与查询层一致。
- 第 `1000` 页：`50` 条，耗时约 `786.97ms`，接口与查询层一致。
- 第 `10000` 页：`50` 条，耗时约 `1315.24ms`，接口与查询层一致。

生命周期总开关联动：

- 关闭 `cloud_shutdown_enabled` 后，关机计划首行 `queue_status=shutdown_disabled`。
- 关闭 `cloud_server_delete_enabled` 后，服务器删除计划首行 `queue_status=global_server_delete_disabled`。
- 关闭 `cloud_ip_delete_enabled` 后，IP 删除计划首行 `queue_status=global_ip_delete_disabled`。

## 暴露的差异与判断

- 服务器删除计划真实接口分页元数据当前是 `total=2`，但底层 `server_lifecycle_plan_counts()` 原始计数为 `59`。
- 关机计划只读缓存分页第 `39599` 页返回 `total=1979990 / loaded=50`，而直接查询层按原始计数 `1979933` 计算最后页只剩 `33` 条。
- 结合接口实现复查，当前差异更像“缓存快照/页面投影口径”和“底层原始 helper 计数”不一致，不是第 `1/2/1000/10000` 页分页切片错误。
- 带 `refresh=1` 的整套生命周期快照重算在真实 250 万级资产库上超过 `30s`，本轮未等到完整刷新结果，因此最后页口径差异暂列剩余风险，不在本轮直接改代码。

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_page_marks_overdue_delete_as_due_now cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_future_server_plan_item --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudAssetDashboardSnapshot, CloudLifecycleTask, CloudNoticeTask, CloudServerOrder; print('CloudAsset', CloudAsset.objects.count()); print('CloudAssetDashboardSnapshot', CloudAssetDashboardSnapshot.objects.count()); print('CloudLifecycleTask', CloudLifecycleTask.objects.count()); print('CloudNoticeTask', CloudNoticeTask.objects.count()); print('CloudServerOrder', CloudServerOrder.objects.count())"
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py shell -c "from cloud.lifecycle_plan_queries import server_lifecycle_plan_counts, ip_delete_plan_counts; print('server_lifecycle_plan_counts', server_lifecycle_plan_counts()); print('ip_delete_plan_counts', ip_delete_plan_counts())"
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\.|finance\.|mall\.|monitoring\.|dashboard_api\.|biz\." cloud bot orders core shop -g '!**/migrations/**'
```

命中项仍是允许项：测试桩、Telegram 登录账号模块名、`CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实、旧计划快照或废弃 runtime app 回流。

## 结论

- 本轮未发现机器人高风险回调长度回归，`RetainedIpRenewalUiTestCase` 51 条继续通过。
- 生命周期计划页在真实大样本下，关机计划 `1/2/1000` 页与 IP 删除计划 `1/2/1000/10000` 页继续保持接口和查询层一致。
- 生命周期总开关与资产单项计划状态联动没有回退。
- 本轮没有执行真实支付、链上广播、真实云资源创建/删除、生产发布或删除业务数据。

## 剩余风险

- 生命周期计划页缓存快照与底层原始 helper 计数在关机最后页、服务器删除计划上存在口径差异；需要下一轮单独用 `refresh=1` 全量重算或直接读生命周期计划快照来源定位。
- 机器人多任务高并发真机点击压测仍被 Telegram 网络不可达阻塞。
- 真实云资源创建后的完整关机/删机/IP 释放闭环仍未完成授权内的安全验证。
