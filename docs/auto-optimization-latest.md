# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 20:20 CST
- 状态：完成任务中心 250 万级统计巡检，覆盖后端口径对账、真实前端打开、卡片展示、明细分页、搜索和详情跳转。
- 后端 Commit：待提交。
- 前端 Commit：本轮无前端变更。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 前端页面：`http://127.0.0.1:5666/admin/tasks`
- 后端接口：`/api/admin/tasks/center/`
- 查询入口：`cloud/task_center.py`
- 前端视图：`apps/web-antd/src/views/dashboard/tasks/index.vue`

## 后端任务中心口径

后端 `task_center_overview` 真实库返回：

- HTTP 状态：`200`
- 接口耗时：约 `1.128s`
- 板块数：`5`
- 总任务：`2516679`
- 活动任务：`2512110`
- 失败：`172`
- 告警：`178`

板块明细：

- 云资产同步：`0/0`，告警 `0`，失败 `0`
- 云服务器任务：`10516/10516`，状态计数 `deleting=2`、`expiring=10343`、`renew_pending=171`
- 生命周期计划：`2479992/2479992`，状态计数 `shutdown_disabled=1`、`scheduled_future=7`
- 通知计划：`21431/21431`，告警 `7`，失败 `1`
- 自动续费：`171/4740`，告警 `171`，失败 `171`

说明：

- 任务中心页不是深分页列表页，前端当前从 5 个板块各取最多 `8` 条明细后本地分页。
- 本轮任务中心压测重点是大统计口径、汇总卡片、明细渲染、搜索和详情入口。
- 深分页压力已在生命周期计划页和代理列表全部标签页单独完成。

## 真实前端验证

真实打开：

```text
http://127.0.0.1:5666/admin/tasks
```

前端结果：

- 任务总量卡片显示：`2516679`
- 汇总卡片数：`6`，包含总量卡片和 5 个任务板块。
- 页面包含并显示：云资产同步、云服务器任务、生命周期计划、通知计划、自动续费。
- 明细表第 1 页：`12` 行。
- 明细表第 2 页：`12` 行。
- 第 2 页首行与第 1 页首行不同，前端分页切换生效。
- 搜索 `自动续费`：返回 `8` 行，表格包含自动续费文本。
- 点击首个 `详情`：从 `/admin/tasks` 跳转到 `/admin/cloud-orders/20395`。
- 业务 API 失败：`0`。
- 控制台 error/warning：`0`。
- request failed：`0`。
- 截图：`/private/tmp/shop-task-center-front.png`

测试完成后已删除临时后端 session 和临时浏览器 storageState，没有打印有效登录 token。

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/task_center.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=1
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\.|finance\.|mall\.|monitoring\.|dashboard_api\.|biz\." cloud bot orders core shop -g '!**/migrations/**'
```

命中项为既有允许项：`CloudServerOrder.ip_recycle_at` 同步记录、bot 测试桩、Telegram 登录账号模块名，不是旧订单到期事实或废弃 runtime app 回流。

说明：

- SQLite 聚焦测试仍输出既有 `db_comment` / `db_table_comment` 警告，不属于本轮问题。
- 本轮没有代码修复。
- `docs/real-machine-test-report.md` 当前存在既有未提交真实机器测试记录，本轮不覆盖、不提交。

## 结论

- 任务中心 250 万级统计口径和真实前端展示一致。
- 汇总卡片、明细分页、本地搜索和详情跳转均正常。
- 本轮未发现任务中心统计漏报、前端渲染错误、业务 API 失败或控制台错误。

## 已完成压测

- 生命周期计划页：关机计划、删除计划、服务器删除历史、IP 删除计划、IP 删除历史的深分页和真实前端翻页。
- 代理列表：全部 11 个标签均完成 10 万级以上压测，覆盖第 1 页、第 2 页、第 1000 页和最后页，并完成真实前端点击。
- 任务中心：250 万级统计汇总和真实前端展示已完成。

## 尚未完成

- 通知计划页专项深分页和前端翻页对账还没有作为独立页面压测收尾。
- 机器人多任务高并发真机点击压测还没有完成。
- 真实云资源创建、到期关机、删机、释放 IP 的生命周期开关矩阵还没有完整闭环。
- 服务器创建后的完整生命周期链路还没有在本轮压测中闭环到真实关机、真实删机和真实 IP 释放。
