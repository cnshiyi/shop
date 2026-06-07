# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 07:21 CST
- 状态：完成一轮代理列表真实库分页对账、真实前端标签翻页/末页测试、Telegram 机器人多任务高并发回归；发现测试覆盖不足，已补充高并发用例。
- 本轮范围：代理列表 IP 视图风险标签、前端真实页面分页、机器人后台钱包直付/补付/续费并发、callback 回归、红线扫描。

## 巡检结论

- 真实数据库/API 对账通过：`11` 个风险标签共 `44` 个分页点全部一致，覆盖第 `1` 页、第 `2` 页、第 `1000` 页和末页。
- 真实前端页面通过：打开 `http://127.0.0.1:5666/admin/cloud-assets`，逐个点击重点标签并实际翻到第 `2` 页和末页，控制台 `0` error。
- 重点标签前端实测结果：
  - 全部：`共 2489998 条代理`，第 `2` 页 `20` 行，末页 `124500` 加载 `18` 行。
  - 未附加固定 IP：`共 100001 条代理`，第 `2` 页 `20` 行，末页 `5001` 加载 `1` 行。
  - 云账号异常：`共 1145002 条代理`，第 `2` 页 `20` 行，末页 `57251` 加载 `2` 行。
  - 关机计划关闭：`共 100384 条代理`，第 `2` 页 `20` 行，末页 `5020` 加载 `4` 行。
  - 未绑定群组：`共 100013 条代理`，第 `2` 页 `20` 行，末页 `5001` 加载 `13` 行。
  - 续费关闭：`共 104558 条代理`，第 `2` 页 `20` 行，末页 `5228` 加载 `18` 行。
- 机器人高并发测试已加强：新增 `60` 个并发后台任务用例，覆盖 `20` 组钱包直付、`20` 组钱包补付、`20` 组续费后检查，校验 `chat_id`、订单、数量和派生创建任务不串上下文。
- `bot.tests` 整组 `107` 个测试全部通过。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
git diff --check
```

真实库/API 对账：

```text
代理列表风险标签：all、unattached_ip、account_disabled、shutdown_disabled、unbound_group、auto_renew_off、normal、due_soon、expired、abnormal、unbound_user
分页点：第 1 页、第 2 页、第 1000 页、末页
结果：44/44 一致
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\." cloud bot orders core shop -g '!**/migrations/**'
```

说明：

- 红线扫描命中项仍是 Telegram 登录账号代码、云账号测试桩和 `CloudServerOrder.ip_recycle_at` 同步语句，不是旧到期事实回流。
- SQLite 的 `db_comment` warnings 仍是已知测试噪声。
- 临时后台 session、临时后台用户、Playwright 截图目录和上一轮临时 SQLite 审计库均已清理。

## 受限项

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、支付密钥、云厂商密钥或完整代理链接。

## 下一步

- 继续不停轮巡检，下一轮优先回到生命周期创建服务器、关机计划、删除计划、IP 删除计划的开关联动和执行顺序。
- 继续关注代理列表云账号异常标签首屏约 `2.4s` 的加载耗时，后续可继续优化冷缓存计数和筛选。
