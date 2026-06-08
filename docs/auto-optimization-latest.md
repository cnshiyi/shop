# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 13:39 CST
- 状态：完成一轮生命周期计划页 IP 删除计划末页专项优化、真实前端复测、真实库精确对账、机器人高并发回归和红线扫描。
- 本轮范围：`cloud/lifecycle_plan_queries.py` 的 IP 删除计划尾页查询路径、`cloud/tests.py` 聚焦测试、真实计划页浏览器翻页。

## 巡检结论

- IP 删除计划末页此前单表约 `6.6s` 到 `7.3s`，根因是 MySQL 对 `instance_id IS NULL OR instance_id=''` 加多路 `LIKE '%…%'` 的未附加 IP 过滤做反向排序时无法有效利用索引。
- 已新增尾页候选扫描路径：
  - 将候选拆为 `instance_id=''` 和 `instance_id IS NULL` 两路。
  - 两路各自按 `actual_expires_at DESC, id DESC` 取候选。
  - 在 Python 中按相同排序做有序归并。
  - 最终仍用原 `unattached_ip_delete_active_queryset()` 精确条件过滤，保证数据口径不变。
- 真实库精确对账通过：IP 删除计划第 `1/2/1000/10000` 页优化结果与原始精确分页逐项一致。

## 真实前端结果

打开 `http://127.0.0.1:5666/admin/tasks/plans` 后实际点击 IP 删除计划末页：

- IP 删除计划末页 `10000`：约 `2.30s`。
- 只返回 `ip_delete_plan_items`。
- 页面显示 `已加载 50 / 总 500000`，实际行数 `50`。
- 首行：`10.6.140.63 / CODEX-IPDEL-MILLION-429119`。
- 其他表未被清空，控制台 `0` error，请求 `0` 个 400/500。

后端热路径复测：

- IP 删除计划第 `1` 页：API 约 `0.70s`。
- IP 删除计划第 `1000` 页：API 约 `0.77s`。
- IP 删除计划末页 `10000`：API 约 `1.34s`。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_plan_tail_page_keeps_exact_order cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_tables_param_returns_only_requested_items cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated --settings=shop.settings --verbosity=1
git diff --check
```

真实库/API 对账：

```text
IP 删除计划分页点：第 1 页、第 2 页、第 1000 页、末页 10000
结果：4/4 与原始精确分页一致
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

- 继续处理关机计划深页“分页后同 IP 去重导致非末页不足 page_size”的结构问题，优先把去重前移到查询层或生命周期任务投影层。
- 继续做机器人全功能真实账号巡检和多任务高并发覆盖。
- 继续对代理列表各风险标签做真实前端翻页和数据库口径对账。
