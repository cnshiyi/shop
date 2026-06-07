# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 06:18 CST
- 状态：完成一轮真实计划页、代理列表加载、数据库口径和分页对账巡检；发现并修复两个安全可提交问题。
- 本轮范围：生命周期计划页计数缓存、计划页第 2 页/末页展示对账、代理列表切标签加载稳定性、后端检查与聚焦测试、前端类型检查。

## 修复内容

- `bot/api.py`
  - 生命周期计划页计数快照增加 60 秒新鲜度判断。
  - 进程缓存和持久化 `SiteConfig` 快照都必须同时满足指纹一致且未过期，才允许复用。
  - 缓存过期后会重算 `shutdown_plan_count`、`server_delete_count`、IP 删除计划和历史计数，避免默认页面继续显示旧总数。
- `cloud/tests.py`
  - 新增回归测试：资产指纹未变但计数快照过期时，计划页必须重新计算数量。
- 前端仓库 `/Users/a399/Desktop/data/vue-shop-admin`
  - `apps/web-antd/src/views/dashboard/cloud-assets/index.vue`
  - 代理列表主数据请求不再被 `sync-status` 请求阻塞。
  - `sync-status` 改为非阻塞更新，失败时不打断标签切换、翻页或主表格渲染。

## 真实页面和数据库对账

- 计划页真实浏览器地址：`http://127.0.0.1:5666/admin/tasks/plans`
- 页面控制台：0 error / 0 warning。
- 页面显示与数据库实时口径一致：
  - 当前计划资产：`2500003`
  - 缺少到期时间：`251`
  - 未附加 IP：`600001`
  - 服务器资产：`1900002`
  - 关机计划：`1979990`
  - 删除计划：`2`
  - 服务器删除历史：`20010`
  - IP 删除计划：`500000`
  - IP 删除历史：`520010`
- 计划页关机计划分页对账：
  - 第 2 页页面显示 `51-100 / 共 1979990 条`，前 8 条 IP/订单号与数据库 `server_lifecycle_plan_page(page=2, page_size=50)` 精确一致。
  - 末页页面显示 `已加载 40 / 总 1979990`，数据库 `page=39600` 返回 40 条，前 8 条 IP 与页面快照一致。
- 代理列表真实浏览器巡检：
  - 标签切换和翻页后控制台 0 error / 0 warning。
  - 修复后 `sync-status` 超时或取消不会再导致主列表加载失败。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_rebuilds_stale_count_snapshot_without_fingerprint_change cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_reuses_cached_count_snapshot_after_refresh cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_reads_persisted_count_snapshot_after_process_cache_clear cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_rebuilds_cached_count_snapshot_when_asset_changes --settings=shop.settings --verbosity=1
pnpm --filter @vben/web-antd typecheck
git diff --check
```

说明：

- SQLite 的 `db_comment` warnings 仍是已知测试噪声，不影响业务测试结果。
- 临时后台 session 已删除，`/private/tmp/shop_pw_state.json` 和 `.playwright-cli/` 临时产物已清理。

## 受限项

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮机器人高并发沿用上一轮整组 `bot.tests` 通过结论；下一轮继续把机器人多任务高并发作为优先专项重跑。

## 下一步

- 提交后端和前端两个仓库的已验证修复。
- 继续执行机器人多任务高并发、生命周期开关执行链和真实页面巡检。
