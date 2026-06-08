# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 16:42 CST
- 状态：完成代理列表分组视图百万级分页修复、真实前端跳页验证，并复测机器人多任务高并发。
- Commit：本轮记录随本轮提交一起保存。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`（真实浏览器验证；无前端代码改动）
- 重点：
  - 代理列表云资源视图分组分页
  - 用户分组 / 群组分组真实页面显示
  - 未附加、关机关闭等标签分组翻页
  - 机器人多任务高并发回归

## 本轮问题

- 真实前端打开 `代理列表 -> 云资源视图 -> 分组` 后，分组接口约 10 秒返回空数据：
  - `total=0`
  - `groups=[]`
  - 页面显示 `已展开 0 / 0 组`
- 直接在 Django shell 执行同一查询，确认慢点是非 compact 分组分页回落到 `GROUP BY + MIN + ORDER BY` 聚合：
  - `OperationalError (2013, Lost connection to MySQL server during query timed out)`
- 第一次优化后又暴露出快照 `payload={}` 的压测数据会被分组成 `unbound:`，造成第 1000 页 20 条数据并成 1 组。

## 本轮修复

- `cloud/api_asset_snapshots.py`
  - 非 compact 分组分页也优先使用“按已有排序索引取有序行，再去重出分组 key”的有界分页路径。
  - 末页同样允许使用有界反向分页，避免百万数据末页回落到超重聚合。
  - 快照 payload 缺失 `id` 时，自动从 `CloudAsset` 和快照列补齐最小真实展示字段。
  - payload 存在但缺少分组 key 时，补齐 `group_user_key`、`group_telegram_key`、用户和群组关联字段。
- `cloud/tests.py`
  - 锁定非 compact 用户分组第 2 页必须走有界行分页 helper。
  - 新增空快照 payload 分组分页回归，防止前端把真实多组并成 1 个空组。

## 数据对账与性能

真实 MySQL 后端查询层复核：

- 用户分组 / 全部 / 第 1 页：`20` 组，约 `845ms`。
- 用户分组 / 全部 / 第 2 页：`20` 组，约 `122ms`。
- 用户分组 / 全部 / 第 1000 页：`20` 组，约 `166ms`。
- 用户分组 / 全部 / 末页：`16` 组，约 `247ms`。

接口直连复核：

- `total=2489996`
- `groups_len=20`
- `items_len=20`
- `risk_counts.all=2500003`

## 真实前端验证

实际打开：

```text
http://127.0.0.1:5666/admin/cloud-assets
```

已验证：

- 用户分组 / 全部 / 第 1 页：页面 `20` 组，分页 `共 2489996 个用户/分组`。
- 用户分组 / 全部 / 第 2 页：页面 `20` 组。
- 用户分组 / 全部 / 第 1000 页：页面 `20` 组。
- 用户分组 / 未附加固定 IP / 第 1 页：页面 `20` 组，分页 `共 100001 个用户/分组`。
- 用户分组 / 未附加固定 IP / 第 2 页：页面 `20` 组。
- 群组分组 / 未附加固定 IP / 第 1 页：页面 `20` 组。
- 群组分组 / 未附加固定 IP / 第 2 页：页面 `20` 组。
- 群组分组 / 关机计划关闭 / 第 1000 页：页面 `20` 组，分页 `共 100369 个用户/分组`。

浏览器分组接口结果：

- 分组接口请求：`9`
- 非 200 或空表：`0`
- 业务请求失败：`0`
- Vite 热更新模块请求出现 `ERR_ABORTED`，属于开发服务器模块切换噪音。
- Ant Design Vue 仍有一个既有 Typography ellipsis 用法 warning，本轮未改前端代码。

## 机器人高并发

本轮继续复测：

- 通知复制并发隔离。
- 钱包直付 / 钱包补付同时执行。
- `60` 路批量后台任务隔离。

结果：聚焦测试通过，未发现任务串线、返回链污染或后台任务隔离问题。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_paginated_uses_twenty_user_groups_per_page cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_page_rebuilds_empty_snapshot_payload_group_keys cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_total_counts_distinct_groups_only cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_duplicate_groups_do_not_repeat_across_pages cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_duplicate_groups_reverse_tail_keeps_last_page bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_asset_snapshots.py cloud/tests.py bot/keyboards.py bot/tests.py
git diff --check
```

红线扫描通过。命中项为既有测试桩账号字符串、Telegram 登录账号 API 文件名，以及 `CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实回流。

## 清理

- 已删除前端 `.playwright-cli/` 临时产物。
- 已删除本轮临时后台登录用户 `codex_group_frontend_probe`。
- 已删除 `/private/tmp/shop_group_frontend_probe_token.txt`。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥、云厂商密钥或完整代理链接。

## 下一步

- 继续巡检代理列表其他分组标签的深页与末页表现。
- 继续把机器人返回链、`callback_data <= 64` 字节限制和多任务高并发作为固定回归项。
- 后续可单独处理 Ant Design Vue Typography ellipsis warning。
