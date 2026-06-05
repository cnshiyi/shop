# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-06 00:27 CST
- 状态：按用户要求激活 `shop` 上线前全自动监工审计，让自动化每轮审计、领取安全任务、验证、记录、提交，并在下一次触发后继续下一轮优化。
- 本轮范围：更新 Codex App 中现有 `shop` 自动化；更新自动优化控制台，明确后端和前端双工作区监工审计规则。
- 压测数据：本地数据库 `CloudAsset` 服务器资产 500000 条，`CloudAssetDashboardSnapshot` 500000 条，`TelegramUser` 500316 条；本轮补充数据前后都未调用云厂商 API、未执行真实删机/关机/释放 IP、未执行真实支付或链上广播。

## 性能结果

- 代理列表 IP 视图：5 万数据从约 1.32s 优化到 0.50s；50 万数据首屏为 2.29s，返回 `全部 (500000)`，用户/分组总数 499492。
- 代理列表分页对账：第 1/2/3/10/100/1000/24975 页接口分组 key 均与数据库精确分页一致；实际浏览器点击第 2 页和第 24975 页均正常显示。
- 删除计划：5 万数据从 10.51s / 3.9MB 降到首刷 2.06s / 260KB，缓存命中 0.43s；50 万数据首刷 4.80s，缓存命中 1.67s。
- 通知计划：50 万数据接口约 2.87s；浏览器显示 6000 组用户通知、近期 5400、未来 600。
- 删除计划 50 万浏览器实测：当前删除计划 454999 条，服务器资产 454248 条，未附加 IP 751 条；表格只返回展示 50 条，计数字段不截断。

## 最近验证

- 本轮自动监工配置变更：已将现有 `shop` 自动化从暂停改为启用，模型为 `gpt-5-codex`，执行环境为本地，工作区包含后端 `/Users/a399/Desktop/data/shop` 和前端 `/Users/a399/Desktop/data/vue-shop-admin`。
- 本轮文档/任务清单变更：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过；`git diff --check` 通过。
- 后端：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- 后端聚焦测试：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_fields_basic_omits_notes_and_execution_payload cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_uses_notice_plan_view --settings=shop.settings --verbosity=2` 通过；SQLite comment warnings 为预期差异。
- 前端：`pnpm -C /Users/a399/Desktop/data/vue-shop-admin --filter @vben/web-antd typecheck` 通过。
- 检查：后端和前端仓库 `git diff --check` 均通过。
- 浏览器实测：代理列表、删除计划、通知计划在 50 万数据下均 0 error / 0 warning。
- 浏览器翻页实测：代理列表实际请求并显示 page=1、page=2、page=24975，控制台 0 error / 0 warning；最后一页显示 12 组。

## 剩余风险

- 本轮只配置自动化和仓库控制文档，未执行真实云资源创建、删除、关机、释放 IP、真实支付、链上广播或生产发布。
- 代理列表 IP 视图第一页约 2.2s；第 2 页、深页和最后一页为保证精确分页仍走数据库聚合，50 万下约 4.7-5.5s。如后续需要深分页也保持 2s 内，需要增加快照表到期时间冗余字段和索引，或改为游标分页。
- 本地压测数据保留在项目数据库中，用于继续压测；清理这批数据属于删除数据操作，需要单独确认。
- 前端仓库存在大量本轮无关脏文件；后端仓库存在未跟踪文档 `docs/jisou-bot-functions.md`、`docs/telegram-search-development-plan.md`、`docs/telegram-search-large-scale-architecture.md`，本轮均未处理。

## 下一步

- 自动监工下一轮领取 `TODO.md` 第一项“全自动优化项目巡检”，按固定入口每轮只做一个最小安全修复或一轮完整审计。
- 如果继续压测深分页，优先优化代理列表分组排序索引或改游标分页。
- 如果要恢复较小本地数据库，需要单独确认清理 `LOADTEST20260605Z` 等压测数据。
