# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 14:54 CST
- 状态：完成一轮执行器/快照延迟刷新巡检，修复线程启动失败时延迟刷新锁未立即释放的问题。
- 本轮范围：
  - `cloud/dashboard_snapshots.py`：仪表盘、计划表、通知表延迟刷新协调器。
  - `cloud/tests.py`：延迟刷新线程启动失败防回退测试。
  - 机器人并发聚焦测试。

## 修复结论

- 巡检用户此前提到的“新进程创建失败/执行器失败”方向时，当前代码没有发现生产路径还在创建独立进程。
- 现有定时管理命令使用 `asyncio.to_thread`，仪表盘刷新使用 `threading.Thread` 做后台延迟刷新。
- 发现一个真实缺口：`_refresh_dashboard_plan_snapshots_deferred()` 在 `Thread.start()` 抛 `RuntimeError("can't start new thread")` 时，已经写入的去重锁不会立即释放，需要等待 `60` 秒 TTL，可能导致后续刷新被误跳过。
- 已把 `can't start new thread` / `cannot start new thread` / `can't create new thread` / `cannot create new thread` 纳入可识别的 shutdown/thread 资源错误。
- 已在 `Thread.start()` 外层增加兜底：
  - 启动失败时立即 `cache.delete(lock_key)`。
  - shutdown/thread 资源类错误记为跳过，不打成异常堆栈。
  - 其他 RuntimeError 仍按异常记录。

## 机器人高并发测试

本轮按用户要求继续覆盖机器人多任务高并发，聚焦执行：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated --settings=shop.settings --verbosity=1
```

结果：

- `3` 条通过。
- 覆盖通知复制包装器并发隔离。
- 覆盖钱包直付、钱包补付、续费后巡检同时执行。
- 覆盖 `20` 组批量钱包直付、`20` 组钱包补付、`20` 组续费后巡检，总计 `60` 路并发任务，验证消息、创建准备调用和后台创建任务不串线不丢失。

## 后端验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_dashboard_snapshot_deferred_releases_lock_when_thread_start_fails --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/dashboard_snapshots.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\." cloud bot orders core shop -g '!**/migrations/**'
```

命中项仍为 Telegram 登录账号代码、云账号测试桩和 `CloudServerOrder.ip_recycle_at` 同步语句，不是旧到期事实回流。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、支付密钥、云厂商密钥或完整代理链接到仓库文件。
- 本轮为执行器/后台刷新聚焦修复，未重新打开前端页面；上一轮已真实打开计划页和通知页完成对账。

## 下一步

- 下一轮继续执行固定巡检清单，优先真实打开代理列表其他标签页面做加载、翻页和数据库对账。
- 继续把机器人多任务高并发作为固定覆盖项。
