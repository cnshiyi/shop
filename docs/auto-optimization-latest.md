# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-09 06:13 CST
- 状态：已修复后台用户列表分页数量不对，并统一 AWS 同步、生命周期计划、通知计划/历史的日志输出格式。
- 后端提交：本轮完成后提交，具体哈希以 `git log -1` 为准。
- 前端提交：本轮完成后提交，具体哈希以 `/Users/a399/Desktop/data/vue-shop-admin` 的 `git log -1` 为准。

## 本轮背景

- 用户反馈后台用户列表翻页数量不对，日志太乱，要求日志能看懂哪个账号触发了哪个任务、地区、实例名、IP、状态，并且不要摘要，要完整列表。
- 用户补充要求通知日志和计划日志也统一格式化。
- 本轮不执行真实支付、链上广播、真实云资源创建/删除、生产发布或删除数据。

## 修复内容

- `bot/api_users.py`
  - 用户列表改为服务端分页响应，返回 `items/page/page_size/total/total_pages/loaded`。
  - 搜索、分页和代理数排序后的分页结果不再固定只取前 50 条。
- 前端 `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd`
  - 用户列表改为读取后端分页结构。
  - 表格分页总数使用后端 `total`，搜索/重置回到第一页，删除当前页最后一条时自动回退页码。
- `cloud/management/commands/sync_aws_assets.py`
  - AWS 同步日志改成分区逐条输出，不再只显示前 20 条摘要。
  - 每条日志统一包含账号、任务、地区、类型、实例/资源名、IP、状态、结果、资产 ID、订单。
- `cloud/management/commands/refresh_lifecycle_plans.py`
  - 生命周期计划输出关机计划、删机计划、服务器删除历史、IP 删除计划、IP 删除历史的完整逐条列表。
  - 每条计划日志统一包含任务、账号、地区、实例/资源名、IP、计划时间、状态、结果、资产 ID、订单。
  - 命令日志使用原始备注，不再使用前端折叠后的 `...（备注过长，已折叠预览）`。
- `cloud/api_tasks.py`、`cloud/management/commands/refresh_notice_plans.py`
  - 通知计划命令内部启用 `full` 明细模式，按订单/IP 逐条输出。
  - 通知计划和通知历史统一包含任务、账号、地区、用户、用户名、实例/资源名、IP、计划时间、状态、结果、订单 ID。

## 验证

通过：

```bash
uv run python -m py_compile bot/api.py bot/api_users.py bot/tests.py cloud/api_tasks.py cloud/management/commands/sync_aws_assets.py cloud/management/commands/refresh_lifecycle_plans.py cloud/management/commands/refresh_notice_plans.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.DashboardCloudAccountVerifyTestCase.test_user_proxy_count_follows_cloud_account_active_state bot.tests.DashboardCloudAccountVerifyTestCase.test_users_list_uses_server_pagination_total_and_distinct_pages --settings=shop.settings --verbosity=1
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_refresh_notice_plans_command_builds_notice_plan_view cloud.tests.CloudServerServicesTestCase.test_notice_plan_summary_reuses_group_rows_for_counts cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_deep_group_page_has_no_duplicates --settings=shop.settings --verbosity=1
uv run python manage.py refresh_lifecycle_plans --limit 2 --page-size 2
uv run python manage.py refresh_notice_plans --limit 2 --history-limit 2
pnpm --filter @vben/web-antd typecheck
git diff --check
```

结果：

- Django 系统检查通过。
- 后端相关文件编译通过。
- 用户列表分页聚焦测试通过。
- 通知计划命令、通知统计复用和通知深分页聚焦测试通过。
- 生命周期计划命令和通知计划命令实跑通过。
- 前端 `vue-tsc` 类型检查通过。
- 后端和前端 `git diff --check` 通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。

## 页面验证

- 已用 Playwright 打开 `http://127.0.0.1:5676/admin/users`。
- 当前浏览器登录态已失效，页面跳转到 `/auth/login?redirect=%252Fadmin%252Fusers`，需要 Google 动态验证码，因此未绕过登录做用户列表页面点击验证。
- 登录页控制台只有 Vite HMR WebSocket fallback 和密码框不在 form 内的浏览器提示，未见本轮用户列表代码导致的业务错误。

## 剩余风险

- 本轮没有实际登录后台点击用户列表分页；分页正确性由后端聚焦测试和前端类型检查覆盖。
- 用户列表后端仍会为了保持“代理数优先排序”先计算当前搜索结果的全部用户代理数；如果用户表继续放大，下一轮应把代理数排序下沉到数据库聚合分页。
