# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-06 01:20 CST
- 状态：完成 `TODO.md` 中“50 万数据深分页性能优化”，代理列表 IP 视图第 2 页、深页和最后一页已在本地 50 万压测数据下稳定低于 2 秒，并完成数据库精确对账和浏览器翻页实测。
- 本轮范围：后端 `CloudAssetDashboardSnapshot` 增加后台列表排序缓存和可见性缓存；代理列表默认分页过滤改为使用快照单表索引；未改动前端代码。
- 压测数据：本地数据库 `CloudAssetDashboardSnapshot` 500000 条，其中 `asset_due_sort_at` 非空 499749 条，默认可见 499494 条，用户/分组总数 499492。

## 性能结果

- 代理列表 IP 视图分组分页接口：page=1 为 0.573s，page=2 为 0.403s，page=3 为 0.401s，page=10 为 0.404s，page=100 为 0.695s，page=1000 为 0.712s，page=24975 为 0.982s。
- 精确对账：上述页码均与旧的 `CloudAsset.actual_expires_at` 关联聚合结果一致，分组 key 全部 match。
- 浏览器实测：前端实际请求 page=1、page=2、page=24975 均为 200；第 2 页正常激活并渲染 20 组，最后一页 page=24975 正常激活并渲染末页 12 组；控制台 0 warning / 0 error。
- 旧基准查询：旧的 OR 过滤 + `CloudAsset.actual_expires_at` 关联聚合约 3.3-3.7s，本轮改为快照可见性缓存 + 快照排序缓存后避开关联聚合和 index_merge。

## 最近验证

- 迁移：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate cloud 0052` 通过；`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate cloud 0053` 通过。
- 后端检查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- 迁移一致性：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run` 通过。
- 编译检查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/api_asset_snapshots.py cloud/api_assets.py cloud/models.py cloud/migrations/0052_cloud_asset_dashboard_snapshot_due_sort.py cloud/migrations/0053_cloud_asset_dashboard_snapshot_display_visible.py` 通过。
- 后端聚焦测试：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_compact_returns_ip_view_payload cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_compact_keeps_unbound_group_key cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_paginated_uses_twenty_user_groups_per_page --settings=shop.settings --verbosity=2` 通过；SQLite comment warnings 为预期差异。
- 代码检查：`git diff --check` 通过。

## 剩余风险

- 本轮未执行真实云资源创建、删除、关机、释放 IP、真实支付、链上广播、删除数据或生产发布。
- `asset_due_sort_at` 仅是后台列表排序缓存，真实资产到期事实仍只来自 `CloudAsset.actual_expires_at`；返回 payload 的到期字段仍从 `CloudAsset` 读取。
- 本地 50 万压测数据仍保留；清理属于删除数据操作，需要单独确认。
- 后端仓库仍有本轮无关未跟踪文档 `docs/jisou-bot-functions.md`、`docs/telegram-search-development-plan.md`、`docs/telegram-search-large-scale-architecture.md`，本轮未处理。

## 下一步

- `TODO.md` 中明确的 50 万深分页优化已完成；下一轮若没有新的明确任务，按 `docs/auto-optimization-control.md` 固定巡检清单做一轮只读巡检，并只修复一个明确安全问题。
