# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-06 13:04 CST
- 状态：已修复代理列表快照大数据阻塞、计划页 IP 删除计划/历史混用，以及 50 万压测数据下计划页接口超时；未执行真实云资源、真实支付、链上广播、删除数据或生产发布。
- 本轮范围：后台代理列表、生命周期计划接口、计划页前端字段消费、CloudAsset 查询索引。

## 修改内容

- 代理列表快照刷新增加大数据保护：
  - 快照为空且资产量超过同步阈值时转为后台延迟刷新。
  - 快照过期但候选资产超过阈值时不在列表请求内同步全量刷新，避免请求超时。
  - 新增 `CloudAsset(kind, updated_at)` 索引用于快照过期候选检查。
- 生命周期计划接口明确拆分 IP 删除数据：
  - 新增 `ip_delete_plan_items`：只返回活动 IP 删除计划。
  - 新增 `ip_delete_history_items`：只返回 IP 删除历史记录。
  - 保留 `ip_delete_items` 作为兼容字段，但前端计划页不再依赖它做主数据源。
  - `ip_delete_count` 改为活动 IP 删除计划总数，`ip_delete_due_count` / `pending_ip_delete_count` 继续表示近期待执行，`ip_delete_history_count` 表示历史记录总数。
- 计划页前端改为直接读取 `ip_delete_plan_items` 和 `ip_delete_history_items`，避免计划与历史记录在页面层混用。
- 50 万压测数据下计划页接口优化：
  - 服务器生命周期计划查询不再用备注/状态文本模糊匹配排除未附加 IP。
  - 服务器计划只走实例 ID 明确存在的资产路径，避开大表文本扫描。
  - 新增 `CloudAsset(kind, -sort_order, actual_expires_at, -updated_at)` 排序索引支撑计划页按到期时间取前批数据。

## 验证结论

- 后端编译通过。
- `DB_ENGINE=mysql uv run python manage.py check` 通过。
- `DB_ENGINE=mysql uv run python manage.py migrate --plan` 通过，当前无待执行迁移。
- MySQL 本地迁移已应用 `cloud.0056_cloudasset_lifecycle_plan_sort_index`。
- 聚焦测试通过，覆盖：
  - 代理列表大数据快照过期时不阻塞请求；
  - IP 删除计划和 IP 删除历史字段严格分离；
  - compact/limit 场景下 IP 删除历史记录不被活动计划挤掉；
  - 关机、服务器删除、IP 删除三个阶段和单项开关语义不回退。
- 前端 `@vben/web-antd` 类型检查通过。
- 浏览器实测计划页：
  - `/admin/tasks/plans` 可正常打开；
  - 最新计划接口请求 200；
  - 页面显示 `IP删除计划（0）`、`IP删除历史记录（7）`；
  - 顶部统计显示 `IP删除历史 7 条`；
  - 当前浏览器 console error 为 0。
- 计划接口本地 50 万压测数据请求结果：
  - `plans_http=200`
  - 响应约 150 KB
  - `time_total=3.036615`
  - `ip_delete_plan_items=0`
  - `ip_delete_history_items=7`
  - `plan_has_history_rows=false`
  - `history_has_active_rows=false`

## 最近验证

```bash
DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py cloud/api_asset_snapshots.py cloud/models.py cloud/tests.py cloud/migrations/0055_cloudasset_kind_updated_index.py cloud/migrations/0056_cloudasset_lifecycle_plan_sort_index.py
DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan
DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_defers_large_stale_snapshot_refresh cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_paginated_uses_twenty_user_groups_per_page cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_compact_returns_ip_view_payload --settings=shop.settings --verbosity=2
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_separate_ip_delete_plan_and_history_items cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_compact_request_keeps_ip_delete_history_item cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_ip_delete_history_item cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_real_released_retained_ip_history_without_active_row cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_move_deleted_unattached_ip_active_row_to_history cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_split_shutdown_before_server_delete --settings=shop.settings --verbosity=2
pnpm -C /Users/a399/Desktop/data/vue-shop-admin --filter @vben/web-antd typecheck
```

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除数据或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照或旧退款入口。

## 剩余风险

- 本地 50 万压测数据仍保留，清理属于删除数据操作，需要单独确认。
- 计划页接口已从超时恢复到约 3 秒；如果目标是稳定低于 2 秒，还需要继续做计划表缓存分页或预聚合。
- 后端仓库仍有本轮无关未跟踪文档 `docs/jisou-bot-functions.md`、`docs/telegram-search-development-plan.md`、`docs/telegram-search-large-scale-architecture.md`，本轮未处理。
- 前端仓库仍有大量本轮无关脏文件和既有 `apps/web-antd/src/views/dashboard/cloud-assets/index.vue` 脏改动，本轮只提交计划页相关两个文件。
