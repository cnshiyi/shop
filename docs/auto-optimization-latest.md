# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 05:24 CST
- 状态：完成一轮代理列表百万级全标签分页压测、生命周期计划页复查、机器人高并发/返回链测试；修复代理列表分组末页在重复分组下回落超重查询导致空页的问题。
- 本轮范围：代理列表 `user`/`telegram_group` 两种分区、全部风险标签首二页/深页/末页、生命周期关机/删机/IP 删除计划分页、机器人多任务高并发和 callback 返回链、后台 Bearer session 回归。

## 修复内容

- `cloud/api_asset_snapshots.py`
  - 放开有界反向尾页分页对重复分组的支持：当重复分组扩容量在安全阈值内时，末页不再回落到超重 `GROUP BY` 深页查询。
  - 修复百万级 `all` 标签最后一页：此前 `api_total=2458992` 但末页应有 `12` 组时返回 `0` 组；修复后末页返回 `12` 组，约 `0.63s`。
- `cloud/tests.py`
  - 新增 `test_cloud_assets_grouped_duplicate_groups_reverse_tail_keeps_last_page`，锁定“重复分组 + 最后一页”必须走有界反向分页并返回最后一组。
- `core/dashboard_api.py` / `bot/tests.py`
  - 继续保留上一阶段 Bearer session 修复和测试：Bearer 请求只刷新 bearer 对应 Session 行，不再创建/修改空 cookie session，避免高频分页时响应保存阶段放大 MySQL 连接异常。

## 实库压测

代理列表 `group_by=telegram_group` 全标签 DB/HTTP 对账通过：

- `all`：`2458992` 组，页 `1/2/100/5001/122950` 均正确，末页 `12` 组。
- `normal`：`549988` 组，末页 `8` 组。
- `due_soon`：`100250` 组，末页 `10` 组。
- `expired`：`100352` 组，末页 `12` 组。
- `unattached_ip`：`100001` 组，末页 `1` 组。
- `abnormal`：`100000` 组，末页 `20` 组。
- `account_disabled`：`1109001` 组，末页 `1` 组。
- `shutdown_disabled`：`100369` 组，末页 `9` 组。
- `unbound_user`：`100001` 组，末页 `1` 组。
- `unbound_group`：`100003` 组，末页 `3` 组。
- `auto_renew_off`：`101002` 组，末页 `2` 组。

代理列表默认 `group_by=user` 全标签 DB/HTTP 对账通过：

- `all`：`2489996` 组，末页 `16` 组。
- `normal`：`549988` 组，末页 `8` 组。
- `due_soon`：`101250` 组，末页 `10` 组。
- `expired`：`101752` 组，末页 `12` 组。
- `unattached_ip`：`100001` 组，末页 `1` 组。
- `abnormal`：`100000` 组，末页 `20` 组。
- `account_disabled`：`1145001` 组，末页 `1` 组。
- `shutdown_disabled`：`100384` 组，末页 `4` 组。
- `unbound_user`：`100001` 组，末页 `1` 组。
- `unbound_group`：`100003` 组，末页 `3` 组。
- `auto_renew_off`：`104548` 组，末页 `8` 组。

## 真实页面复查

已用真实浏览器打开：

- `http://127.0.0.1:5666/admin/cloud-assets`
- `http://127.0.0.1:5666/admin/tasks/plans`

结果：

- 代理列表标题：`代理列表 - Vben Admin Antd`，逐个点击 11 个风险标签，均触发对应 `risk_status` 请求，响应 `200`，页面分页总数与 API total 一致，控制台 `0 error / 0 warning`。
- 代理列表全标签页面结果：运行中 `549988`、即将到期 `101250`、已过期 `101752`、未附加固定IP `100001`、异常/待确认 `100000`、云账号异常 `1145001`、关机计划关闭 `100384`、未绑定用户 `100001`、未绑定群组 `100003`、续费关闭 `104548`、全部 `2489996`。
- 计划页标题：`计划 - Vben Admin Antd`，关机计划、删除计划、IP 删除计划、IP 删除历史、显示列均可见，控制台 `0 error / 0 warning`。
- 计划页 API 分页：关机计划 `1879990`、服务器删除计划 `2`、服务器删除历史 `20010`、IP 删除计划 `500000`、IP 删除历史 `520010`；各表第一页加载数量与分页元数据一致。

## 机器人

已通过机器人聚焦测试：

- `RetainedIpRenewalUiTestCase` 共 `50` 个测试通过。
- 覆盖多任务高并发：钱包直付创建、订单补付创建、续费后巡检通知并发执行，确认 chat/order/port 隔离。
- 覆盖资产详情、订单详情、续费、换 IP、重装、修改配置、返回链和 callback 64 字节限制。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile core/dashboard_api.py cloud/api_asset_snapshots.py bot/tests.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.DashboardSessionExpiryTestCase.test_bearer_dashboard_request_does_not_create_cookie_session cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_duplicate_groups_do_not_repeat_across_pages cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_duplicate_groups_reverse_tail_keeps_last_page cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_total_counts_distinct_groups_only cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_uses_true_database_pages --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描：

- 未发现废弃 runtime app 回流。
- 未发现旧退款入口或旧退款函数名回流。
- `service_expires_at` 仅在历史迁移/文档语境中出现。
- `core.dashboard_api` 命中是当前公共后台 API 工具模块；`dashboard_snapshots` 命中是当前刷新 helper 命名，不是旧计划快照表。

## 受限项

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮使用临时后台 session 做真实页面巡检；结束时必须删除该 session，不打印 token/session。

## 下一步

- 继续优化默认用户分区部分标签末页仍在 `4s-6.5s` 的场景，目标是在不丢数据的前提下降到更稳定的 2 秒内。
- 继续推进生命周期任务表投影路线，降低计划页冷态 count 成本。
