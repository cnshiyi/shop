# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-06 13:46 CST
- 状态：已完成代理列表、通知表、计划表、服务器表的数据数量校验、数据真实性校验、翻页校验和压力测试；修复通知表隐藏列仍构造昂贵 payload、服务器表只暴露前 500 条的问题。
- 本轮范围：后端服务器列表 API、通知计划 API、服务器表前端分页、四表真实浏览器复测和接口压测。

## 修改内容

- 服务器表后端 `/api/admin/servers/` 增加真实服务端分页：
  - `paginated=1` 时返回 `items/page/page_size/total/total_pages`。
  - 默认旧数组响应保持兼容，避免影响已有调用方。
  - 支持第 1 页、第 2 页、深页和最后页按数据库排序精确返回，不再只取前 500 条。
- 服务器表前端改为服务端分页：
  - 翻页、跳页、分页大小、搜索和排序都会重新请求后端。
  - 总数显示来自后端真实 `total`。
- 通知表 API 优化隐藏列加载：
  - 当页面传 `fields=basic` 且关闭文案/渠道等列时，不再构造批量通知文案和账号通知渠道 payload。
  - 保持通知计划数量、当前页数据和历史记录语义不变。
- 新增聚焦回归测试：
  - 服务器表分页结果必须与 `CloudAsset` 数据库排序一致。
  - 通知表关闭文案列时不得调用批量文案构造。

## 验证结论

- 后端 `manage.py check` 通过。
- 前端 `@vben/web-antd` 类型检查通过。
- 聚焦测试通过：服务器分页、通知表隐藏文案列轻量加载。
- Django Client 数据校验通过：
  - 服务器表 DB/API 总数均为 `499993`，第 1/2/1000/10000 页 ID 与数据库精确对账一致。
  - 通知表 `due_count=5401`、`future_count=600`、`active_user_count=6001`，offset 0/10/5391 均正常返回 10 行。
  - 代理列表 `total=499492`，第 2 页 20 组样本资产 ID 均存在。
  - 计划表 `shutdown_plan_count=947`、`server_delete_count=2`、`ip_delete_count=0`、`ip_delete_history_count=7`。
- 真实浏览器复测通过：
  - `/admin/servers` 显示 `共 499993 条`，点击第 2 页后请求 `page=2&page_size=50&paginated=1` 返回 200，页面显示压测服务器数据。
  - `/admin/tasks/notices` 请求 `fields=basic` 返回 200，页面显示 `6001` 组通知、近期 `5401`、未来 `600`。
  - `/admin/cloud-assets` 返回 200，页面显示 `全部 (500000)` 和 20 组代理数据。
  - `/admin/tasks/plans` 返回 200，页面显示关机计划、删除计划、IP 删除历史和压测计划数据。
  - 浏览器 console error 为 0。
- `curl` 压力测试通过：
  - 代理列表：10 请求/3 workers，成功 10，失败 0，avg `1.899s`，p95 `2.333s`。
  - 通知表 basic：5 请求/1 worker，成功 5，失败 0，avg `2.515s`，p95 `2.514s`。
  - 计划表：6 请求/2 workers，成功 6，失败 0，avg `1.958s`，p95 `1.980s`。
  - 服务器表分页：10 请求/2 workers，成功 10，失败 0，avg `2.197s`，p95 `3.913s`。

## 最近验证

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/api_servers.py cloud/api_tasks.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_servers_list_paginated_matches_cloud_asset_order cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_basic_fields_skip_batch_text_payload --settings=shop.settings --verbosity=2
pnpm -C /Users/a399/Desktop/data/vue-shop-admin --filter @vben/web-antd typecheck
```

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除数据或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照或旧退款入口。

## 剩余风险

- 本地仍保留 50 万压测数据；清理属于删除数据操作，需要单独确认。
- 通知表 `fields=basic` 已降至约 2.5 秒；如果打开文案/渠道列，仍会产生更重的 payload 构造，后续可继续做异步预计算或缓存。
- 服务器表深页第 10000 页约 4 秒；当前已保证不丢数据，若目标低于 2 秒，需要进一步做游标分页或专用排序索引方案。
