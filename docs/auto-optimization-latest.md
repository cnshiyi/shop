# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 20:42 CST
- 状态：完成通知计划页专项深分页和真实前端翻页对账，并修复前端跳页控件缺失与重试说明列控制台警告。
- 后端 Commit：已提交，`docs: record notice plan patrol`。
- 前端 Commit：`24e6a6c fix: support notice plan deep pagination`。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 前端页面：`http://127.0.0.1:5666/admin/tasks/notices`
- 后端接口：`/api/admin/tasks/notices/`
- 后端实现：`cloud/api_tasks.py`
- 前端视图：`apps/web-antd/src/views/dashboard/tasks/notices.vue`

## 修复内容

- 给通知计划表和历史通知表分页补齐 `showQuickJumper: true`，支持真实跳转深页和最后页。
- 将历史通知“重试说明”列的 `TypographyParagraph` 改为 `content` 属性渲染，消除打开重试列后的 Ant Design Vue 控制台 warning。
- 不修改通知计划后端数据口径，不恢复旧计划快照表，不引入兼容分支。

## 后端通知计划口径

真实库后端对账结果：

- `CloudNoticeTask` 总数：`6335`
- `CloudNoticeTask.claimed`：`2`
- `CloudNoticeTask.failed`：`6333`
- 通知活跃分组：`21429`
- 近期分组：`3428`
- 未来分组：`18001`
- 历史通知：`14960`

活跃通知计划 API 对账：

- 第 1 页：`10` 条，total `21429`
- 第 2 页：`10` 条
- 第 1000 页：`10` 条
- 最后页第 `2143` 页：`9` 条
- 页内无重复，返回顺序与后端分组排序一致。

历史通知 API 对账：

- 第 1 页：`10` 条，total `14960`
- 第 2 页：`10` 条
- 第 1000 页：`10` 条
- 最后页第 `1496` 页：`10` 条
- 页内无重复，返回顺序与数据库 `created_at/id` 倒序一致。

字段开关对账：

- 关闭重字段后请求：`fields=basic,actions`
- 开启 IP、文案、渠道、重试后请求：`fields=basic,ips,text,channels,retry,actions`
- 前端列展示与请求字段一致。

## 真实前端验证

真实打开：

```text
http://127.0.0.1:5666/admin/tasks/notices
```

前端结果：

- 页面显示通知计划总数：`21429` 组用户通知。
- 页面显示近期计划：`3428` 种通知。
- 页面显示未来计划：`18001` 种通知。
- 两张表都显示跳页输入框，跳页控件数量：`2`。
- 通知计划表第 1 页：`10` 行。
- 通知计划表第 2 页：`10` 行。
- 通知计划表第 1000 页：`10` 行。
- 通知计划表最后页第 `2143` 页：`9` 行。
- 历史通知表第 1 页：`10` 行。
- 历史通知表第 2 页：`10` 行。
- 历史通知表第 1000 页：`10` 行。
- 历史通知表最后页第 `1496` 页：`10` 行。
- 前端表格行数与每次接口返回 `items.length` 一致。
- 开启列开关后能显示 IP 列、通知文案列、通知渠道列、重试说明列。
- 关闭列开关后上述重字段列被隐藏，接口恢复轻量字段。
- 业务 API 失败：`0`。
- 控制台 error/warning：`0`。
- request failed：`0`。
- 截图：`/private/tmp/shop-notice-plans-front.png`

测试完成后已删除临时后端 session 和临时浏览器 storageState，没有打印有效登录 token。

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_tasks.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_uses_notice_plan_view cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_basic_fields_skip_batch_text_payload cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_basic_actions_fields_keep_order_link_without_hidden_columns cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_allows_deep_offsets_beyond_100k cloud.tests.CloudServerServicesTestCase.test_notice_plan_summary_reuses_group_rows_for_counts cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_counts_all_future_groups_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_deep_group_page_has_no_duplicates cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_hides_shutdown_disabled_lifecycle_notices cloud.tests.CloudServerServicesTestCase.test_notice_write_actions_require_superuser cloud.tests.CloudServerServicesTestCase.test_delete_notice_history_removes_notice_history_row cloud.tests.CloudServerServicesTestCase.test_notice_history_rows_keep_unique_log_ids_for_same_batch --settings=shop.settings --verbosity=1
pnpm -F @vben/web-antd run typecheck
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\.|finance\.|mall\.|monitoring\.|dashboard_api\.|biz\." cloud bot orders core shop -g '!**/migrations/**'
```

命中项为既有允许项：bot 测试桩、Telegram 登录账号模块名、`CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实、旧计划快照或废弃 runtime app 回流。

说明：

- SQLite 聚焦测试仍输出既有 `db_comment` / `db_table_comment` 警告，不属于本轮问题。
- `docs/real-machine-test-report.md` 当前存在既有未提交真实机器测试记录，本轮不覆盖、不提交。

## 结论

- 通知计划页专项深分页、跳页、列开关和真实前端显示已完成。
- 本轮发现并修复 2 个前端问题：缺少跳页控件、重试说明列触发控制台 warning。
- 通知计划页没有发现分页丢数据、串页、最后页行数错误、业务 API 失败或控制台错误。

## 已完成压测

- 生命周期计划页：关机计划、删除计划、服务器删除历史、IP 删除计划、IP 删除历史的深分页和真实前端翻页。
- 代理列表：全部 11 个标签均完成 10 万级以上压测，覆盖第 1 页、第 2 页、第 1000 页和最后页，并完成真实前端点击。
- 任务中心：250 万级统计汇总和真实前端展示已完成。
- 通知计划页：21429 活跃分组和 14960 历史通知的深分页、跳页、列开关和真实前端展示已完成。

## 尚未完成

- 机器人多任务高并发真机点击压测还没有完成。
- 真实云资源创建、到期关机、删机、释放 IP 的生命周期开关矩阵还没有完整闭环。
- 服务器创建后的完整生命周期链路还没有在本轮压测中闭环到真实关机、真实删机和真实 IP 释放。
