# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 03:48 CST
- 状态：完成一轮代理列表全标签真实前端巡检、数据库分组口径对账和聚焦测试修复。
- 本轮范围：`/admin/cloud-assets` 代理列表、11 个风险标签、未附加固定 IP 第 2 页和末页、快照查询层分组总数、代理列表风险筛选测试。

## 真实页面

使用真实浏览器打开：

- `http://127.0.0.1:5666/admin/cloud-assets`

页面确认：

- 页面标题为 `代理列表 - Vben Admin Antd`。
- 首屏真实渲染代理列表，显示 `全部 (2500003)`。
- 当前为 IP 视图，显示列为用户、分组、IP/价格、到期/剩余、编辑。
- 页面控制台 `0 error / 0 warning`。

全标签真实点击结果：

| 标签 | 分组总数 | 首屏 | 耗时 |
| --- | ---: | --- | ---: |
| 全部 | 2489996 | 20 组 / 20 个编辑按钮 | 已加载页面 |
| 运行中 | 549988 | 20 组 / 20 个编辑按钮 | 约 6.6s |
| 即将到期 | 101250 | 20 组 / 20 个编辑按钮 | 约 6.5s |
| 已过期 | 101752 | 20 组 / 20 个编辑按钮 | 约 6.5s |
| 未附加固定IP | 100001 | 20 组 / 20 个编辑按钮 | 约 6.5s |
| 异常/待确认 | 100000 | 20 组 / 20 个编辑按钮 | 约 6.6s |
| 云账号异常 | 1145001 | 20 组 / 20 个编辑按钮 | 约 6.4s |
| 关机计划关闭 | 100384 | 20 组 / 20 个编辑按钮 | 约 6.6s |
| 未绑定用户 | 100001 | 20 组 / 20 个编辑按钮 | 约 6.9s |
| 未绑定群组 | 100003 | 20 组 / 30 个编辑按钮 | 约 6.7s |
| 续费关闭 | 104548 | 20 组 / 30 个编辑按钮 | 约 6.5s |

未附加固定 IP 翻页：

- 第 2 页：显示 `共 100001 个用户/分组`、`已展开 20 / 20 组`，耗时约 `6.4s`。
- 第 5001 页：显示 `共 100001 个用户/分组`、`已展开 1 / 1 组`，耗时约 `7.3s`。

说明：

- 一次过早点击标签的脚本巡检得到 `0` 分组；复查代码确认标签切换会重置页码，等待首屏非 0 后重跑未复现。该轮作废，不作为业务失败。

## 数据对账

使用后端同一套 `/api/admin/cloud-assets/` 入口和 `cloud.api_asset_snapshots` 查询 helper 对账。

风险资产数：

- `all=2500003`
- `normal=549988`
- `due_soon=101250`
- `expired=101752`
- `unattached_ip=100001`
- `abnormal=100000`
- `account_disabled=1145002`
- `shutdown_disabled=100384`
- `unbound_user=100001`
- `unbound_group=100013`
- `auto_renew_off=104558`

11 个标签第 1 页 API `total` 均与数据库分组总数一致。

未附加固定 IP 分页对账：

- 第 1 页：`20` 组，唯一 key，无跨页重叠。
- 第 2 页：`20` 组，唯一 key，无跨页重叠。
- 第 5001 页：`1` 组，唯一 key，无跨页重叠。

## 修复

修复测试数据口径：

- `cloud/tests.py`
  - 给 3 个代理列表风险筛选测试补齐有效 AWS 云账号和 `account_label`。
  - 目的：测试 `due_soon`、`expired`、`unattached_ip` 标签本身，而不是被当前规则归入“云账号异常”。
  - 未修改运行时代码。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_paginated_uses_twenty_user_groups_per_page cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_total_counts_distinct_groups_only cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_paginated_orders_null_due_groups_last cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_filters_by_risk_and_searches_asset_identifiers cloud.tests.CloudServerServicesTestCase.test_cloud_asset_expired_filter_excludes_unattached_ip_assets cloud.tests.CloudServerServicesTestCase.test_cloud_asset_unattached_filter_uses_raw_provider_status cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_uses_true_database_pages --settings=shop.settings --verbosity=1
git diff --check
```

说明：

- SQLite `db_comment` 警告仍是已知数据库能力差异。
- 一次聚焦测试命令包含不存在的测试名，纠正后重跑通过；该失败不是业务断言失败。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。
- 红线扫描命中仅在历史迁移文件中出现 `service_expires_at`。
- 本轮创建过临时后台 session 用于真实页面巡检；一次 CLI 回显后已立即作废旧 session，重新创建的临时 session 也已在结束时删除。

## 下一步

- 继续巡检机器人 callback 返回链，重点覆盖资产详情、续费、换 IP、重装迁移/重建、修改配置的多任务高并发。
- 继续关注代理列表全标签耗时，目前多数标签约 `6.4s-6.9s`，仍可作为下一轮性能优化候选。
