# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 13:24 CST
- 状态：完成一轮生命周期计划页真实性、翻页、压力数据和机器人高并发巡检；发现计划页深页翻页会重算所有表、计数快照每 60 秒全量重算、历史分页深页慢的问题，已修复并完成真实前端复测。
- 本轮范围：生命周期计划页后端分页查询层、计划页前端局部表加载、真实库/API 对账、真实浏览器翻页、Telegram 机器人多任务高并发回归、红线扫描。

## 巡检结论

- 计划页支持 `tables=<表名>` 局部加载：翻某一个表时只返回该表 items，前端合并到现有页面状态，其他表不清空、不重算。
- 生命周期计数缓存改为“数据指纹不变就复用”，不再因为 60 秒 TTL 到期强制重算 50 万/百万级统计。
- 服务器删除历史和 IP 删除历史分页查询层增加单来源直接分页、日志为主且少量资产历史时的稀疏插入路径。
- `pagination.*.loaded` 现在使用实际返回行数，修复关机计划分页后去重导致“返回 40 行但 loaded 仍写 50”的契约错误。
- 真实库/API 对账通过：`16/16` 个分页点一致或契约正确，覆盖关机计划、服务器删除历史、IP 删除计划、IP 删除历史的第 1 页、第 2 页、深页和末页。
- 真实前端复测通过：页面控制台 `0` error，请求 `0` 个 400/500。

## 真实前端结果

打开 `http://127.0.0.1:5666/admin/tasks/plans` 后实际点击分页：

- IP 删除历史：第 `2` 页约 `0.86s`，第 `1000` 页约 `1.35s`，末页 `10401` 约 `0.83s`；每次只返回 `ip_delete_history_items`。
- 关机计划：末页 `39600` 约 `2.25s`；只返回 `shutdown_plan_items`，实际加载 `40 / 1979990`。
- 服务器删除历史：末页 `401` 约 `1.19s`；只返回 `server_history_items`，实际加载 `10 / 20010`。
- IP 删除计划：末页 `10000` 仍约 `7.3s`；只返回 `ip_delete_plan_items`，数据正确但仍是下一轮优化重点。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_tables_param_returns_only_requested_items cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated --settings=shop.settings --verbosity=1
pnpm -C /Users/a399/Desktop/data/vue-shop-admin -F @vben/web-antd run typecheck
git diff --check
```

真实库/API 对账：

```text
计划页分页点：shutdown_plan 1/2/1000/39600，server_history 1/2/400/401，ip_delete 1/2/1000/10000，ip_delete_history 1/2/1000/10401
结果：16/16 通过
```

机器人高并发：

```text
60 个并发后台任务通过：20 组钱包直付、20 组钱包补付、20 组续费后检查；聊天窗口、订单、数量和派生任务未串上下文。
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\." cloud bot orders core shop -g '!**/migrations/**'
```

说明：

- 红线扫描命中项仍是 Telegram 登录账号代码、云账号测试桩和 `CloudServerOrder.ip_recycle_at` 同步语句，不是旧到期事实回流。
- SQLite 的 `db_comment` warnings 仍是已知测试噪声。

## 受限项

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、支付密钥、云厂商密钥或完整代理链接。

## 下一步

- 继续优化 IP 删除计划 50 万末页单表约 `7.3s` 的查询路径，优先考虑把未附加 IP 判定固化为可索引字段或投影任务表。
- 关机计划深页存在分页后去重导致非末页不足 `page_size` 的现象；本轮已修正 `loaded` 契约，下一轮应把去重前移到查询层或任务投影层。
- 继续做机器人全功能真实账号巡检和多任务高并发覆盖。
