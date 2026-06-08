# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 13:55 CST
- 状态：完成一轮生命周期计划页关机计划深页数据真实性修复、真实前端跳页复测、真实库对账、机器人高并发回归和红线扫描。
- 本轮范围：`bot/api.py` 的服务器计划页展示逻辑、`cloud/tests.py` 的生命周期分页回归测试。

## 修复结论

- 问题：关机计划深页先按数据库分页，再在响应层把同 IP 的 orphan 服务器折叠为 1 条，导致非末页实际只显示 40 条，但分页总数仍按原始资产行数计算。
- 风险：同 IP 旧服务器资产会被页面隐藏，造成深页少行、数据真实性不一致，也不利于旧服务器逐条管理。
- 修复：服务器关机计划和服务器删除计划不再按同 IP 折叠资产；计划页按 `CloudAsset` 资产行展示，每条服务器资产都可见、可管理。
- 保留：IP 删除计划自己的固定 IP 去重逻辑未改，本轮只处理服务器计划页。

## 真实库结果

真实 MySQL 数据库复测：

- 关机计划总数：`1979990`。
- 第 `1` 页：`loaded=50`，约 `1.32s`。
- 第 `2` 页：`loaded=50`，约 `1.22s`。
- 第 `1000` 页：从修复前 `loaded=40` 恢复为 `loaded=50`，约 `1.25s`。
- 末页 `39600`：`loaded=40`，因为 `1979990 % 50 = 40`，属于正确末页。

## 真实前端结果

打开 `http://127.0.0.1:5666/admin/tasks/plans` 后实际操作：

- 计划页首屏加载成功，关机计划显示 `已加载 50 / 总 1979990`。
- 通过页面分页输入框实际跳到关机计划第 `1000` 页。
- 前端真实请求：`tables=shutdown_plan&shutdown_page=1000&shutdown_page_size=50`，返回 `200`。
- 页面显示第 `1000` 页，关机计划表实际可见 `50` 行。
- 响应体分页：`page=1000`、`page_size=50`、`total=1979990`、`loaded=50`。
- 首行/末行 IP 与页面一致：首行 `198.18.3.115`，末行 `198.18.3.64`。
- 控制台 `0` error / `0` warning，请求 `0` 个 400/500。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_keep_same_ip_orphan_servers_visible_across_pages cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_tables_param_returns_only_requested_items bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated --settings=shop.settings --verbosity=1
git diff --check
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
- 本轮临时后台 session 和浏览器 storage state 已删除，对应临时用户已清理。

## 受限项

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、支付密钥、云厂商密钥或完整代理链接到仓库文件。

## 下一步

- 继续对代理列表每个标签做真实前端翻页和数据库口径对账，重点看未附加、已停用账号、未绑定用户、未绑定分组等标签在百万级数据下是否显示完整。
- 继续做机器人全功能真实账号巡检和多任务高并发覆盖。
- 继续观察生命周期总开关、单项关机开关、删机开关、IP 删除开关在真实页面和执行器中的联动。
