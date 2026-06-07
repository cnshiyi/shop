# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 01:42 CST
- 状态：完成代理列表全标签 100 万级注入压测、真实页面翻页对账、分页查询优化和机器人并发/返回链聚焦回归。
- 本轮范围：代理列表 11 个标签、按用户分组分页第 1 页/第 2 页/末页、2,500,003 条快照规模下的接口性能、Telegram Bot 并发发送隔离和云资产操作返回链。

## 覆盖结果

- 注入数据：
  - 新增标签压测资产 `1,000,000` 条。
  - 新增标签压测快照 `1,000,000` 条。
  - 当前 `CloudAssetDashboardSnapshot=2,500,003`，可见快照 `2,489,998`。
  - “全部”标签自然包含新增可见资产；其他 10 个风险标签各新增 `100,000` 条。
- 注入后接口计数：
  - 全部：`2,489,996` 组，`124,500` 页。
  - 运行中：`549,988` 组，`27,500` 页。
  - 即将到期：`101,250` 组，`5,063` 页。
  - 已过期：`101,752` 组，`5,088` 页。
  - 未附加固定IP：`100,001` 组，`5,001` 页。
  - 异常/待确认：`100,000` 组，`5,000` 页。
  - 云账号异常：`1,145,001` 组，`57,251` 页。
  - 关机计划关闭：`100,384` 组，`5,020` 页。
  - 未绑定用户：`100,001` 组，`5,001` 页。
  - 未绑定群组：`100,003` 组，`5,001` 页。
  - 续费关闭：`104,548` 组，`5,228` 页。
- 真实页面标签压测：
  - 注入前连续 3 轮切换 11 个标签，共 33 次，0 失败，0 控制台错误。
  - 注入后逐标签真实页面验证第 1 页、第 2 页、末页，共 33 项，0 失败，0 控制台错误。
  - `未附加固定IP` 和 `异常/待确认` 两个原本小数据标签已分别验证到 10 万级分页和末页。
- 机器人回归：
  - 已跑 `TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated`。
  - 已跑完整 `RetainedIpRenewalUiTestCase`，覆盖续费、换 IP、重装、修改配置、资产详情返回链、钱包异步任务、callback 64 字节限制。
  - 共 50 个 bot 聚焦测试通过。

## 发现与修复

- 发现 1：直接写入 100 万压测快照后，`asset_updated_at` 与资产 `updated_at` 存在轻微差异，列表接口把压测快照判成 stale，反复记录 `CLOUD_ASSET_DASHBOARD_SNAPSHOT_STALE_LARGE_DEFERRED`。
  - 处理：用按 `asset_id` 范围分批更新对齐 `asset_updated_at`，避免一次性大 JOIN 超时。
  - 结果：100 万行分批更新完成，stale 误报消失。
- 发现 2：风险计数原实现使用单次条件聚合，在 250 万快照下需要全表扫描，冷计数约 `5.8s`。
  - 修复：`_dashboard_snapshot_risk_counts()` 改为逐项索引计数，复用原缓存键和返回格式。
  - 结果：同口径计数从约 `5.8s` 降到约 `1.2s`。
- 发现 3：`运行中` 和 `云账号异常` 分组分页缺少复合索引，执行计划出现 `filesort`。
  - 修复：新增 `0060_dashboard_snapshot_risk_group_indexes`，补 `normal/account_disabled` 的分组计数索引和到期排序索引。
  - 结果：`运行中` 首屏接口从约 `4.3s` 降到约 `0.68s`；`云账号异常` 首屏接口从约 `5.1s` 降到约 `0.61s`；第 2 页和末页约 `0.32-0.38s`。
- 发现 4：一次性 `UPDATE ... JOIN` 100 万行在本地 MySQL 超时。
  - 处理：确认大数据维护应使用范围分批更新，已在本轮报告中记录。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_asset_snapshots.py cloud/models.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_paginated_orders_null_due_groups_last cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_total_counts_distinct_groups_only cloud.tests.CloudServerServicesTestCase.test_cloud_assets_grouped_paginated_uses_twenty_user_groups_per_page --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py migrate --plan
git diff --check
```

SQLite `db_comment` 警告仍是已知数据库能力差异。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮注入的是本地标签压测资产和快照，统一使用 `TAGSTRESS20260608` / `tagstress20260608:` 前缀，未打印密钥、私钥、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。

## 剩余风险与下一轮

- 真实页面在后端接口已降到亚秒级后，等待 DOM 稳定仍约 `5.7s`；后续应单独优化前端加载态、同步状态请求和渲染等待。
- 机器人已完成代码级并发/返回链回归；下一轮继续做真机 Telegram 多任务高并发点击，重点覆盖并发购买、续费、换 IP、重装、修改配置和返回链。
- 继续关注 250 万快照规模下通知计划、删除计划、IP 删除历史和计划页的深分页表现。
