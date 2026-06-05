# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-05 23:45 CST
- 状态：完成 50 万云资产本地压测、代理列表/删除计划/通知计划性能优化和浏览器实测。
- 本轮范围：代理列表 IP 视图分组分页优化；删除计划全量统计与展示行拆分；通知计划移除全量订单扫描；删除计划列开关文案修正。
- 压测数据：本地数据库 `CloudAsset` 服务器资产 500000 条，`CloudAssetDashboardSnapshot` 500000 条，`TelegramUser` 500316 条；本轮补充数据前后都未调用云厂商 API、未执行真实删机/关机/释放 IP、未执行真实支付或链上广播。

## 性能结果

- 代理列表 IP 视图：5 万数据从约 1.32s 优化到 0.50s；50 万数据首屏为 2.29s，返回 `全部 (500000)`，用户/分组总数 499492。
- 删除计划：5 万数据从 10.51s / 3.9MB 降到首刷 2.06s / 260KB，缓存命中 0.43s；50 万数据首刷 4.80s，缓存命中 1.67s。
- 通知计划：50 万数据接口约 2.87s；浏览器显示 6000 组用户通知、近期 5400、未来 600。
- 删除计划 50 万浏览器实测：当前删除计划 454999 条，服务器资产 454248 条，未附加 IP 751 条；表格只返回展示 50 条，计数字段不截断。

## 最近验证

- 后端：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- 后端聚焦测试：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_fields_basic_omits_notes_and_execution_payload cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_uses_notice_plan_view --settings=shop.settings --verbosity=2` 通过；SQLite comment warnings 为预期差异。
- 前端：`pnpm -C /Users/a399/Desktop/data/vue-shop-admin --filter @vben/web-antd typecheck` 通过。
- 检查：后端和前端仓库 `git diff --check` 均通过。
- 浏览器实测：代理列表、删除计划、通知计划在 50 万数据下均 0 error / 0 warning。

## 剩余风险

- 本轮只做本地数据库压测；未执行真实云资源创建、删除、关机、释放 IP、真实支付、链上广播或生产发布。
- 代理列表 IP 视图第一页已优化；第 2 页仍走精确分组聚合，50 万下约 5.12s。如后续需要深分页也保持 2s 内，需要增加快照表到期时间冗余字段和索引，或改为游标分页。
- 本地压测数据保留在项目数据库中，用于继续压测；清理这批数据属于删除数据操作，需要单独确认。
- 前端仓库存在大量本轮无关脏文件；后端仓库存在未跟踪文档 `docs/jisou-bot-functions.md`、`docs/telegram-search-large-scale-architecture.md`，本轮均未处理。

## 下一步

- 如果继续压测深分页，优先优化代理列表分组排序索引或改游标分页。
- 如果要恢复较小本地数据库，需要单独确认清理 `LOADTEST20260605Z` 等压测数据。
