# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 05:43 CST
- 状态：完成一轮代理列表用户分区末页性能专项、真实 HTTP 对账、真实前端页面复查和聚焦测试；修复 10 万量级风险标签末页慢路径。
- 本轮范围：代理列表默认 `group_by=user` 末页、风险标签组合索引、分页策略、真实浏览器标签点击、迁移状态、红线扫描。

## 修复内容

- `cloud/models.py` / `cloud/migrations/0062_dashboard_snapshot_user_risk_due_indexes.py`
  - 为用户分区补齐 6 个风险标签到期排序组合索引：
    - 即将到期、已过期、未附加固定IP、异常/待确认、未绑定用户、未绑定群组。
  - 迁移使用 `SeparateDatabaseAndState + RunPython` 幂等建索引，能处理本地建索引中途断线后“索引已存在但迁移未记录”的半完成状态。
  - 本地已执行 `MYSQL_READ_TIMEOUT=600 MYSQL_WRITE_TIMEOUT=600 uv run python manage.py migrate cloud 0062`。
- `cloud/api_asset_snapshots.py`
  - 增加 `_dashboard_snapshot_can_use_forward_row_paging()`。
  - 对无重复分组且 `start <= 150000` 的中等尾页允许正向有界扫描，避免没有专用索引的标签走慢反向排序。
  - 有重复分组仍不放宽，避免 `unbound_group` 这类重复分组走正向大窗口后变慢或串页。
- `cloud/tests.py`
  - 新增 `test_cloud_assets_forward_row_paging_allows_medium_unique_tail_pages`，锁定分页策略边界。

## 实库压测

直接 helper 压测，真实 MySQL：

- `shutdown_disabled`：从约 `2.0s` 降到 `0.344s`。
- `unbound_group`：从约 `4.2s` 降到 `0.162s`。
- `due_soon`：`0.198s`。
- `all`：`0.271s`。

真实 HTTP 用户分区末页对账通过：

- `due_soon`：`101250` 组，末页 `10` 组，`1.570s`。
- `expired`：`101752` 组，末页 `12` 组，`1.261s`。
- `unattached_ip`：`100001` 组，末页 `1` 组，`0.957s`。
- `abnormal`：`100000` 组，末页 `20` 组，`0.926s`。
- `shutdown_disabled`：`100384` 组，末页 `4` 组，`1.421s`。
- `unbound_user`：`100001` 组，末页 `1` 组，`0.903s`。
- `unbound_group`：`100003` 组，末页 `3` 组，`0.906s`。
- `auto_renew_off`：`104548` 组，末页 `8` 组，`1.221s`。
- `all`：`2489996` 组，末页 `16` 组，`1.253s`。
- `account_disabled`：`1145001` 组，末页 `1` 组，`0.704s`。

DB 状态：

- 目标索引实际存在：`cad_due_user_due_ord_idx`、`cad_exp_user_due_ord_idx`、`cad_unatt_user_due_ord_idx`、`cad_abn_user_due_ord_idx`、`cad_nouser_user_due_idx`、`cad_nogroup_user_due_idx`。
- `cloud.0062_dashboard_snapshot_user_risk_due_indexes` 已记录。

## 真实页面复查

已用真实浏览器打开：

- `http://127.0.0.1:5666/admin/cloud-assets`

结果：

- 页面标题：`代理列表 - Vben Admin Antd`。
- 控制台 `0 error / 0 warning`。
- 真实点击并等待页面分页 total 变更：
  - 关机计划关闭：API/page total `100384`。
  - 未绑定群组：API/page total `100003`。
  - 未附加固定IP：API/page total `100001`。
  - 全部：API/page total `2489996`。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_asset_snapshots.py cloud/models.py cloud/tests.py cloud/migrations/0062_dashboard_snapshot_user_risk_due_indexes.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_forward_row_paging_allows_medium_unique_tail_pages cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_duplicate_groups_reverse_tail_keeps_last_page cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_duplicate_groups_do_not_repeat_across_pages cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_total_counts_distinct_groups_only cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_uses_true_database_pages --settings=shop.settings --verbosity=1
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

- 继续巡检生命周期计划页冷态 count 和任务表投影路线。
- 继续抽样代理列表跳页/翻页与 DB 对账，重点防止新增索引后查询计划变化导致其他标签回退。
