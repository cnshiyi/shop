# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 02:06 CST
- 状态：完成计划页高数据口径修复、IP 删除历史深分页优化、真实前端页面翻页验证和机器人并发聚焦回归。
- 本轮范围：250 万资产快照压力背景下的生命周期计划页、关机计划、服务器删除计划、服务器删除历史、IP 删除计划、IP 删除历史、计划页缓存、真实浏览器分页显示。

## 发现与修复

- 发现 1：生命周期计划页 `pagination.shutdown_plan.total` 复用了旧的持久计数缓存。
  - 现象：查询层实时计数 `shutdown_plan_count=1879990`，但页面 API 曾返回旧值 `979990`。
  - 修复：计划计数缓存增加数据指纹，覆盖服务器资产总数、服务器资产最新更新时间、IP 日志总数、IP 日志最新记录时间、删除订单总数和最新删除订单 ID。指纹变化时自动重建计数快照。
  - 结果：真实库计划页关机计划 total 已恢复为 `1879990`，与查询层一致。
- 发现 2：`IP 删除历史` 深页合并查询从第 1 条遍历到末页，在 `520010` 条历史规模下卡住。
  - 修复：`ip_delete_history_page_sources()` 在后半段分页改为从尾部反向合并，再反转成原始时间轴顺序。
  - 结果：真实库 `IP 删除历史`最后一页 `10401` 返回 `520001-520010 / 共 520010 条`，耗时约 `1.87s`。
- 发现 3：反向分页末页未先把 `end` 截到 `total`，导致最后一页元数据为 `10` 条但实际返回 `50` 条。
  - 修复：合并前先 `end = min(end, total)`。
  - 结果：真实库最后一页实际返回 `10` 条，和 `pagination.loaded=10` 一致。
- 发现 4：前端计划页实测时 Vite 代理曾返回 `/api/admin/user/info` 502。
  - 原因：本地 8000 后端没有监听，残留 runserver 进程不服务请求。
  - 处理：清理旧 runserver 并以前台 `--noreload` 临时启动后端完成页面实测。

## 真实库对账

- 查询层计数：
  - 关机计划：`1879990`
  - 服务器删除计划：`2`
  - 服务器删除历史：`20010`
  - IP 删除计划：`500000`
  - IP 删除历史：`520010`
- API 分页对账：
  - 关机计划第 1 页、第 2 页、末页：`meta.total`、`meta.loaded`、实际行数一致。
  - 服务器删除计划第 1 页：一致。
  - 服务器删除历史第 1 页、第 2 页、末页：一致。
  - IP 删除计划第 1 页、第 2 页、末页：一致。
  - IP 删除历史第 1 页、第 2 页、末页：修复后末页一致。

## 真实前端页面

- 使用 Playwright 真实打开：`http://127.0.0.1:5666/admin/tasks/plans`。
- 页面成功显示：
  - 当前计划资产：`2500003`
  - 未附加IP：`600001`
  - 服务器资产：`1900002`
  - 服务器删除历史：`20010`
  - IP删除历史：`520010`
  - 关机计划表：`已加载 50 / 总 1879990`
- 点击 `IP 删除历史`最后一页 `10401` 后，页面显示：`520001-520010 / 共 520010 条`。
- 最新相关前端请求：
  - `/api/admin/user/info`：`200`
  - `/api/admin/tasks/plans/` 首页：`200`
  - `/api/admin/tasks/plans/` IP 删除历史末页：`200`

## 机器人回归

- 已跑机器人并发发送隔离聚焦测试：
  - `bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated`
- 结果：通过。
- 真机 Telegram 多任务高并发点击仍需继续单独执行，重点覆盖购买、续费、换 IP、重装、修改配置和返回链。本轮未打印 Telegram token、session 或账号敏感信息。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py cloud/lifecycle_plan_queries.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_reuses_cached_count_snapshot_after_refresh cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_reads_persisted_count_snapshot_after_process_cache_clear cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_rebuilds_cached_count_snapshot_when_asset_changes cloud.tests.CloudServerServicesTestCase.test_ip_delete_history_page_sources_reverse_tail_keeps_order cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_mixes_logs_and_assets_by_time --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated --settings=shop.settings --verbosity=1
git diff --check
```

SQLite `db_comment` 警告仍是已知数据库能力差异。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。
- 本轮曾暴露一个临时本地后台 session token，已立即删除旧 session 并重新生成未回显的新浏览器状态继续测试；未打印 Telegram token、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接或代理 secret。

## 下一步

- 继续真机 Telegram 多任务高并发点击测试。
- 继续计划页前端渲染性能优化，当前后端末页已约 `1.87s`，但整页 DOM 很大。
- 继续通知计划和服务器删除历史高数据深分页巡检。
