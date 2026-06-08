# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 16:13 CST
- 状态：完成代理列表全部风险标签压测、真实前端逐标签点击/翻页，并修复多个风险标签深页慢查询。
- Commit：本轮记录随本轮提交一起保存。

## 本轮修复

- `cloud/api_asset_snapshots.py`
  - 风险标签默认排序改为复用快照表已有组合索引。
  - `normal`、`due_soon`、`expired`、`unattached_ip`、`abnormal`、`unbound_user`、`unbound_group` 走：
    - `asset_due_sort_null_rank`
    - `asset_due_sort_at`
    - `group_user_label`
    - `group_user_key`
    - `-asset_id`
  - `shutdown_disabled`、`auto_renew_off` 走：
    - `group_telegram_key`
    - `group_telegram_label`
    - `-asset_id`
  - 显式到期排序仍保持原有契约。
- `cloud/tests.py`
  - 将原未绑定标签排序测试扩展为全风险标签排序测试，锁住“复用已有索引”的策略。

## 重要发现

- 真实 MySQL 的 `cloud_asset_dashboard_snapshot` 已达到单表 `64` 个索引上限。
- 本轮曾尝试新增风险标签列表分页索引，`migrate cloud 0064` 被 MySQL 拒绝：
  - `Too many keys specified; max 64 keys allowed`
- 已确认没有半截新索引残留。
- 最终没有继续堆索引，而是改查询排序，复用现有索引。

## 数据对账与性能

真实 MySQL，代理列表 IP 视图、非分组、`page_size=20`。所有标签均校验第 `1` 页、第 `2` 页、深页和末页候选页，API ID 与数据库同排序切片一致，抽样无重复。

修复前慢页：

- 即将到期末页：约 `5731ms`
- 已过期末页：约 `3242ms`
- 未附加固定 IP 末页：约 `3117ms`
- 异常/待确认末页：约 `3142ms`
- 关机计划关闭第 `1000` 页：约 `2940ms`
- 续费关闭第 `1000` 页：约 `3437ms`

修复后：

- 全部：第 `1` 页 `1237ms`，第 `2` 页 `432ms`，第 `1000` 页 `523ms`，末页 `424ms`。
- 运行中：第 `1` 页 `1818ms`，第 `2` 页 `1424ms`，第 `1000` 页 `1502ms`，末页 `1453ms`。
- 即将到期：第 `1` 页 `557ms`，第 `2` 页 `524ms`，第 `1000` 页 `524ms`，末页 `494ms`。
- 已过期：第 `1` 页 `527ms`，第 `2` 页 `503ms`，第 `1000` 页 `537ms`，末页 `501ms`。
- 未附加固定 IP：第 `1` 页 `410ms`，第 `2` 页 `414ms`，第 `1000` 页 `434ms`，末页 `402ms`。
- 异常/待确认：第 `1` 页 `409ms`，第 `2` 页 `411ms`，第 `1000` 页 `488ms`，末页 `416ms`。
- 云账号异常：第 `1` 页 `376ms`，第 `2` 页 `394ms`，第 `1000` 页 `466ms`，末页 `374ms`。
- 关机计划关闭：第 `1` 页 `602ms`，第 `2` 页 `565ms`，第 `1000` 页 `601ms`，末页 `570ms`。
- 未绑定用户：第 `1` 页 `472ms`，第 `2` 页 `409ms`，第 `1000` 页 `533ms`，末页 `410ms`。
- 未绑定群组：第 `1` 页 `421ms`，第 `2` 页 `421ms`，第 `1000` 页 `538ms`，末页 `429ms`。
- 续费关闭：第 `1` 页 `665ms`，第 `2` 页 `623ms`，第 `1000` 页 `652ms`，末页 `629ms`。

## 真实前端结果

实际打开：

```text
http://127.0.0.1:5666/admin/cloud-assets
```

逐标签点击结果：

- 全部：首屏 `20` 行，第 `2` 页 `20` 行，总数 `2489998`。
- 运行中：首屏 `20` 行，第 `2` 页 `20` 行，总数 `549988`。
- 即将到期：首屏 `20` 行，第 `2` 页 `20` 行，跳第 `5063` 页 `10` 行，总数 `101250`。
- 已过期：首屏 `20` 行，第 `2` 页 `20` 行，跳第 `5088` 页 `12` 行，总数 `101752`。
- 未附加固定 IP：首屏 `20` 行，第 `2` 页 `20` 行，跳第 `5001` 页 `1` 行，总数 `100001`。
- 异常/待确认：首屏 `20` 行，第 `2` 页 `20` 行，跳第 `5000` 页 `20` 行，总数 `100000`。
- 云账号异常：首屏 `20` 行，第 `2` 页 `20` 行，总数 `1145002`。
- 关机计划关闭：首屏 `20` 行，第 `2` 页 `20` 行，跳第 `1000` 页 `20` 行，总数 `100384`。
- 未绑定用户：首屏 `20` 行，第 `2` 页 `20` 行，总数 `100001`。
- 未绑定群组：首屏 `20` 行，第 `2` 页 `20` 行，总数 `100013`。
- 续费关闭：首屏 `20` 行，第 `2` 页 `20` 行，跳第 `1000` 页 `20` 行，总数 `104558`。

浏览器结果：

- 代理列表相关 API 请求数：`56`
- 失败 API：`0`
- 控制台 error/warning：`0`
- requestfailed：`0`

## 机器人高并发复测

继续通过固定并发回归：

- `bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated`
- `bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated`
- `bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated`

覆盖通知复制并发隔离、钱包直付/补付同时执行，以及 `60` 路批量后台任务。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_risk_ordering_uses_existing_page_indexes cloud.tests.CloudServerServicesTestCase.test_cloud_assets_paginated_uses_true_database_pages cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_compact_returns_ip_view_payload bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\." cloud bot orders core shop -g '!**/migrations/**'
```

命中项仍为 Telegram 登录账号测试桩和 `CloudServerOrder.ip_recycle_at` 同步语句，不是旧到期事实回流。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、支付密钥、云厂商密钥或完整代理链接。
- 本轮创建过一次性后台验证用户和 session，验证后已清理。

## 下一步

- 继续巡检分组视图下的用户分组/群组分组翻页，避免只覆盖非分组 IP 视图。
- 继续检查机器人返回链和 Telegram `callback_data` 64 字节限制。
- 继续把机器人多任务高并发作为每轮固定回归项。
