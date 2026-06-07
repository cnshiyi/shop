# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 15:28 CST
- 状态：已完成“再次压测”专项；代理列表 50 万快照深分页已降到 0.5 秒级并完成数据库对账；生命周期计划完成结构化来源字段重构和分页查询优化。
- 本轮范围：本地 MySQL 大数据压测、代理列表分页、生命周期计划分页、IP 删除计划/历史、通知计划只读压测、前端计划页结构化 key 配套修复。

## 数据规模

- `CloudAsset`：1,500,000
- `CloudAssetDashboardSnapshot`：500,000
- 可显示快照：499,494
- `CloudIpLog`：515,739
- 服务器资产：1,500,000

## 修改摘要

- `cloud_asset_dashboard_snapshot` 增加 `asset_due_sort_null_rank` 和组合索引 `cad_vis_list_page_idx`，让代理列表默认排序命中索引。
- 代理列表分页接近尾页时使用反向窗口取数再反转，保持页码契约不变，避免深 offset 扫描。
- 生命周期计划查询层抽出通用反向分页，服务器计划排序统一为 `actual_expires_at,id`，避免 `user_id` 导致 filesort。
- IP 删除历史分页复用已计算的日志/资产/完成保留统计，减少尾页重复 count。
- 生命周期计划项不再使用 `order-xxx`、`asset-xxx`、`log-xxx`、`trace-xxx` 混合字符串主键，统一返回 `source_kind`、`source_id`、`plan_item_key`。
- 前端计划页和工作台改为使用 `plan_item_key`/结构化来源作为行 key，资产开关和备注保存只使用明确的 `asset_id` 或 `order_id`。
- 聚焦测试新增结构化计划项身份字段断言，防止混合字符串主键回流。

## 压测结果

代理列表 IP 视图，50 万快照，`page_size=20`：

- 第 1 页：0.561 秒，数据库对账一致。
- 第 2 页：0.541 秒，数据库对账一致。
- 第 1000 页：0.606 秒，数据库对账一致。
- 倒数第 2 页：0.546 秒，数据库对账一致。
- 最后一页：0.515 秒，数据库对账一致。

生命周期计划，统计缓存预热后，`page_size=50`：

- 关机计划：996,990 条；第 1 页 0.629 秒，最后页 0.544 秒。
- 服务器删除计划：2,752 条；第 1 页 0.579 秒，最后页 2.578 秒。
- IP 删除计划：500,000 条；第 1 页 0.579 秒，最后页 3.616 秒。
- IP 删除历史：500,007 条；第 1 页 0.586 秒，最后页 0.918 秒。
- 所有计划页返回项 `plan_item_key` 无重复，`source_kind/source_id` 完整，`id` 不再是字符串前缀。

通知计划只读压测：

- 组通知统计：due 5,400；future 30,031；history 1,000。
- `offset=0/100/1000/5000/100000` 均能返回正确切片，耗时约 4.9-5.5 秒。
- 通知计划仍是剩余优化点：当前每次请求仍会构建较大的通知计划集合后切片。

## 验证

本地已通过：

```bash
uv run python -m py_compile bot/api.py cloud/api_asset_snapshots.py cloud/lifecycle_plan_queries.py cloud/models.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_uses_true_database_pages cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract --settings=shop.settings --verbosity=1
git diff --check
pnpm --dir /Users/a399/Desktop/data/vue-shop-admin/apps/web-antd typecheck
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

结果：后端编译、Django 系统检查、聚焦测试、前端类型检查、空白检查均通过。SQLite 测试输出的 `db_comment` 警告为已知数据库能力差异。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款入口、旧 `Server` 兼容壳或旧云 API 聚合入口。

## 剩余风险

- 生命周期计划首次统计仍约 14.4 秒，后续翻页依赖进程内统计缓存；上线前建议下一轮把生命周期统计投影到任务表或统计缓存表。
- IP 删除计划最后页仍约 3.6 秒，主要瓶颈是未附加 IP 查询和完成保留 IP 排除条件；需要下一轮继续做任务表投影或更细的物化统计。
- 通知计划接口仍约 5 秒，建议下一轮把通知计划也改为服务端分页查询或任务表投影，不再每次构建全量集合。
