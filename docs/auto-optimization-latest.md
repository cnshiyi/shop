# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 14:49 CST
- 状态：完成一轮生命周期计划页真实 MySQL 慢查询修复、计划页/通知页真实前端对账、机器人多任务高并发测试。
- 本轮范围：
  - `cloud/lifecycle_plan_queries.py`：服务器生命周期计划查询与计数。
  - `cloud/tests.py`：生命周期计划查询防回退测试。
  - 真实前端页面：`/admin/tasks/plans`、`/admin/tasks/notices`。
  - 机器人完整测试：`bot.tests`。

## 修复结论

- 真实 MySQL 中，服务器生命周期计划计数原先通过 `exclude(id__in=未附加IP子查询)` 排除未附加 IP。
- 在当前百万级资产数据下，该子查询会触发 MySQL materialize 和大表扫描，实际出现 `Lost connection to MySQL server during query (timed out)`。
- 已把服务器计划基准查询改为直接条件排除：`exclude(blank_instance_q & unattached_ip_asset_q())`。
- 服务器计划计数改为分步精确计数：
  - 先计算服务器生命周期基准总量。
  - 单独计算未附加 IP 数量。
  - 单独计算服务器删除计划数量。
  - 关机计划数量按总量减去未附加 IP 和删除计划得出。
- 口径保持不变：真实库新旧筛选结果一致，只移除导致 MySQL 超时的 `id__in` 子查询形态。

## 真实库验证

- 新计数：约 `9.641s`，`shutdown_plan_count=1979990`，`server_delete_count=2`。
- 原始口径对账：约 `10.399s`，`shutdown_plan_count=1979990`，`server_delete_count=2`。
- 生命周期计划接口分页对账通过：
  - `shutdown_plan` 第 `1`、`2`、`1000`、`39600` 页无重复无丢失。
  - `server_delete` 第 `1`、`2` 页无重复无丢失。
  - `server_history` 第 `1`、`2`、`401` 页无重复无丢失。
  - `ip_delete` 第 `1`、`2`、`1000`、`10000` 页无重复无丢失。
  - `ip_delete_history` 第 `1`、`2`、`1000`、`10401` 页无重复无丢失。
- 通知计划接口对账通过：
  - 活跃通知用户总数 `21429`。
  - 历史通知记录 `14960`。
  - 第 `1`、`2`、`100`、最后页对账通过，无重复。

## 真实前端复测

使用系统 Chrome 打开真实前端：

```text
http://127.0.0.1:5666/admin/tasks/plans
http://127.0.0.1:5666/admin/tasks/notices
```

计划页：

- 首屏 `shutdownRows=50`，后端 `shutdownLoaded=50`。
- 首屏 `ipRows=50`，后端 `ipLoaded=50`，`ipTotal=500000`。
- IP 删除计划跳第 `1000` 页：DOM 行数 `50`，后端 `loaded=50`，`total=500000`。
- 首屏接口约 `1407ms`，IP 第 `1000` 页约 `866ms`。
- 控制台 `0` error / `0` warning，请求 `0` 个 400/500。

通知页：

- 首屏通知 `10` 行、历史 `10` 行，均与接口一致。
- 通知第 `2` 页、历史第 `2` 页均为 `10` 行，和接口一致。
- 接口耗时约 `1030ms`、`996ms`、`980ms`。
- 控制台 `0` error / `0` warning，请求 `0` 个 400/500。

## 机器人高并发测试

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests --settings=shop.settings --verbosity=1
```

结果：

- `107` 条机器人测试通过。
- 覆盖通知复制包装器并发隔离。
- 覆盖钱包直付、钱包补付、续费后巡检 `asyncio.gather` 同时执行。
- 覆盖 `20` 组批量钱包直付、`20` 组钱包补付、`20` 组续费后巡检，总计 `60` 路并发任务。
- 验证 `60` 个 chat_id 消息互不串线，`40` 次创建准备调用不丢，后台创建任务数量和端口均正确。
- 覆盖云资产/订单返回链、续费、换 IP、重装迁移/重建、修改配置、callback 长度压缩等回归。

## 后端验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plan_server_queryset_avoids_unattached_ip_subquery cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_keep_same_ip_orphan_servers_visible_across_pages cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\." cloud bot orders core shop -g '!**/migrations/**'
```

命中项仍为 Telegram 登录账号代码、云账号测试桩和 `CloudServerOrder.ip_recycle_at` 同步语句，不是旧到期事实回流。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、支付密钥、云厂商密钥或完整代理链接到仓库文件。
- 本轮使用本地真实 MySQL 大表和真实前端页面，不是只看 API 计数。

## 下一步

- 后续每轮继续把机器人多任务高并发作为固定覆盖项，重点看钱包支付、补付、续费、创建任务、重装迁移/重建和回调返回链。
- 继续巡检生命周期计划页、通知计划页和代理列表其他高基数标签。
- 后续新增索引前必须先检查 MySQL 单表 64 索引上限，优先替换低价值旧索引。
