# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 02:20 CST
- 状态：完成一轮通知计划/历史通知真实页面巡检，并修复历史通知同批次多行使用同一个表格行键的问题。
- 本轮范围：后端通知计划接口、历史通知删除契约、通知计划真实库分页对账、前端真实页面末页翻页、机器人多任务高并发发送隔离回归。

## 发现与修复

- 发现 1：历史通知接口把行 `id` 写成 `batch_id or log.id`。同一个批次存在多条 `CloudUserNoticeLog` 时，多行会共享同一个 `id`。
  - 影响：前端历史通知表格使用 `record.id` 作为 row key，会导致同批次历史行 key 重复，深分页和渲染稳定性受影响。
  - 修复：`cloud/api_tasks.py` 的历史通知行 `id` 改为日志 ID，继续保留 `batch_id` 和 `log_id` 字段。
  - 回归：`cloud.tests.CloudServerServicesTestCase.test_notice_history_rows_keep_unique_log_ids_for_same_batch` 覆盖同批次两条日志，断言 `id` 和 `log_id` 都是各自日志 ID。

## 真实库对账

- 通知计划：
  - active 总数：`21429`
  - 近期计划：`3428`
  - 未来计划：`18001`
  - 第 1 页、第 2 页、最后页均无重复，最后页 `9` 条。
- 历史通知：
  - history 总数：`14960`
  - 第 1 页、第 2 页、最后页均无重复，最后页 `10` 条。
  - 修复后历史通知最后页返回日志 ID `10-1`，与分页契约一致。

## 真实页面验证

使用 Playwright 打开真实前端：

- `http://127.0.0.1:5666/admin/tasks/notices`

页面结果：

- 顶部统计显示 `21429 组用户通知 / 21429 个 IP 通知项`。
- 近期计划显示 `3428`，未来计划显示 `18001`。
- 点击通知计划最后页 `2143` 后，第一张表显示 `9` 行。
- 点击历史通知最后页 `1496` 后，第二张表显示 `10` 行。
- 最新相关请求：
  - `/api/admin/user/info`：`200`
  - `/api/admin/tasks/notices/?...history_offset=0&offset=0`：`200`
  - `/api/admin/tasks/notices/?...history_offset=0&offset=21420`：`200`
  - `/api/admin/tasks/notices/?...history_offset=14950&offset=21420`：`200`
- 浏览器控制台：`0 error / 0 warning`。

## 机器人并发回归

- 已跑 `bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated`。
- 结果：通过。
- 覆盖点：多用户并发发送隔离，避免并发任务串用户、串消息或共享发送上下文。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_tasks.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_history_rows_keep_unique_log_ids_for_same_batch cloud.tests.CloudServerServicesTestCase.test_delete_notice_history_removes_notice_history_row cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_deep_group_page_has_no_duplicates bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated --settings=shop.settings --verbosity=1
git diff --check
```

SQLite `db_comment` 警告仍是已知数据库能力差异。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。
- 本轮未打印 Telegram token、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。

## 下一步

- 继续真实浏览器专项巡检，优先覆盖代理列表每个标签、计划页、任务中心在高数据下的翻页/跳页和页面显示一致性。
- 继续真机 Telegram 多任务高并发点击验证，重点覆盖购买、续费、换 IP、重装、修改配置和返回链。
