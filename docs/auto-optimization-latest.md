# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 15:31 CST
- 状态：完成代理列表 `sync-status` 大表性能修复、真实前端标签复测、机器人多任务高并发回归。
- Commit：`20d106d perf: speed up cloud asset sync status`。

## 本轮修复

- `cloud/sync_jobs.py`
  - `cloud_assets_sync_status()` 不再从 `CloudAsset` 大表直接统计 AWS、阿里云和未附加 IP 数量。
  - 改为复用 `CloudAssetDashboardSnapshot`，并用无 join 的 `cloud_account_id` / `account_label` 活跃账号口径过滤。
  - 三个计数合并到一次 `aggregate()`，避免页面每次加载触发多次大表扫描。
  - `last_synced_at` 的资产更新时间优先从快照表 `asset_updated_at` 最大值读取，避免对 `CloudAsset` 大表排序。
- `cloud/tests.py`
  - 新增 `test_cloud_assets_sync_status_counts_use_dashboard_snapshots`，覆盖活跃账号、停用账号、AWS、阿里云和未附加 IP 计数口径。

## 真实前端结果

- 前端：`http://127.0.0.1:5666/admin/cloud-assets`
- 后端：`http://127.0.0.1:8000`
- 浏览器：本机 Google Chrome + Playwright。
- 实测标签：
  - 未附加固定IP：`838ms`，DOM 行数 `20`。
  - 未绑定用户：`3696ms`，DOM 行数 `20`。
  - 未绑定群组：`3273ms`，DOM 行数 `20`。
  - 续费关闭：`603ms`，DOM 行数 `20`。
- `sync-status` 浏览器内实测：
  - 修复前同环境约 `6532ms`。
  - 修复后 `133ms`。
  - HTTP `200`，返回 `code=0`，三个数量字段均为数字。
- 页面请求无失败，控制台 `0 error / 0 warning`。

## 真实 MySQL 计时

- `_latest_synced_cloud_asset_updated_at()`：`11ms`。
- `_cloud_assets_sync_status_counts()`：`159ms`。
- 返回计数：
  - AWS：`10008`。
  - 阿里云：`0`。
  - 未附加 IP：`1726`。

## 后端验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_sync_status_counts_use_dashboard_snapshots --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/sync_jobs.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\." cloud bot orders core shop -g '!**/migrations/**'
```

命中项仍为 Telegram 登录账号、云账号测试桩和 `CloudServerOrder.ip_recycle_at` 同步语句，不是旧到期事实回流。

## 机器人并发

- 本轮继续复跑机器人多任务高并发聚焦测试，`3` 条通过。
- 覆盖通知复制包装器并发隔离。
- 覆盖钱包直付、钱包补付、续费后巡检同时执行。
- 批量样本为 `20` 组直付 + `20` 组补付 + `20` 组续费后巡检，总计 `60` 路并发任务。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、支付密钥、云厂商密钥或完整代理链接。
- 本轮创建过一次性后台验证用户，提交前会清理。

## 下一步

- 下一轮继续优化代理列表慢标签，重点是 `未绑定用户` 和 `未绑定群组` 仍约 `3` 秒。
- 继续做真实前端翻页和数据库口径对账，不能只看计数。
- 继续把机器人多任务高并发作为固定回归项。
