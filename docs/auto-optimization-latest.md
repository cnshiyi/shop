# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-10 13:39 CST
- 状态：已完成代理列表、服务器表、生命周期计划、通知计划、用户表排序巡检和修复。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户要求检查排序问题：
  - 代理列表、计划、通知计划、服务器表：快到期的排在上面。
  - 用户表：服务器数量多的排在上面。
- 本轮沿用独立压测库 `.stress/cloud_assets_100k.sqlite3` 做真实页面验证，避免污染默认本地业务库。

## 修复内容

- `cloud/api_asset_snapshots.py`
  - 代理列表默认排序改为优先按资产到期时间升序。
  - `auto_renew_off`、`shutdown_disabled` 标签也改为到期时间优先。
- `cloud/api_tasks.py`
  - 通知计划分组排序改为优先按下次通知时间升序。
- `bot/user_stats.py`、`bot/api_users.py`
  - 用户列表分页前先按有效服务器数量倒序排序。
  - 服务器数量相同再按用户 ID 倒序，0 服务器用户排在后面。
- `bot/tests.py`、`cloud/tests.py`
  - 增加用户表服务器数量排序、通知计划时间排序测试。
  - 更新代理列表默认排序断言。

## 验证

通过：

```bash
uv run python -m py_compile bot/api_users.py bot/user_stats.py bot/tests.py cloud/api_asset_snapshots.py cloud/api_tasks.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_risk_ordering_uses_existing_page_indexes cloud.tests.CloudServerServicesTestCase.test_notice_plan_summary_sorts_by_next_notice_time_before_user cloud.tests.CloudServerServicesTestCase.test_notice_plan_summary_reuses_group_rows_for_counts bot.tests.DashboardCloudAccountVerifyTestCase.test_users_list_orders_by_proxy_count_before_pagination bot.tests.DashboardCloudAccountVerifyTestCase.test_users_list_uses_server_pagination_total_and_distinct_pages bot.tests.DashboardCloudAccountVerifyTestCase.test_users_list_searches_numeric_and_text_keywords_with_pagination --settings=shop.settings --verbosity=1
```

真实浏览器验证使用前端 `127.0.0.1:5666`、后端独立压测库 `.stress/cloud_assets_100k.sqlite3`：

- 代理列表：20 条，`actual_expires_at` 升序，接口 4.37s。
- 服务器表：50 条，`expires_at` 升序，接口 2.27s。
- 计划页关机计划：50 条，`suspend_at/next_run_at` 升序，接口 2.08s。
- 通知计划：10 条，`next_notice_at` 升序，接口 1.93s。
- 用户表：10 条，`proxy_count` 倒序，接口 1.83s。
- 浏览器控制台无报错。

结果文件：

- `output/playwright/sort-check-result.json`
- `output/playwright/sort-check-cloud-assets.png`
- `output/playwright/sort-check-servers.png`
- `output/playwright/sort-check-plans.png`
- `output/playwright/sort-check-notices.png`
- `output/playwright/sort-check-users.png`

## 结论

- 代理列表、通知计划、用户表已修复排序口径。
- 服务器表、关机计划、删机计划、未附加 IP 删除计划的活跃计划查询层已确认按到期/执行时间升序。
- 当前压测库中服务器删除计划和 IP 删除计划活跃数据为 0，本轮只能确认代码排序口径，页面实测无活跃行可排序。
