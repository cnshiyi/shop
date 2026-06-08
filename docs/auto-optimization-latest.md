# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 19:05 CST
- 状态：完成一轮只读巡检，复查计划页 10 万级边界页耗时、任务中心统计口径、真实前端任务页面和通知统计差异来源；本轮未发现需要修改业务代码的问题。
- 后端 Commit：待提交。
- 前端 Commit：本轮无前端变更。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 重点：
  - 生命周期计划页各表第 1 页、第 2 页、10 万边界页、最后页接口耗时。
  - 任务中心、生命周期计划页、通知计划页统计口径复查。
  - 真实前端打开任务中心、计划页、通知页。
  - 任务中心通知统计聚焦测试。
  - 红线扫描。

## 本轮结论

- 生命周期计划页 10 万级边界页接口耗时正常，没有复现上一轮人工感知的长时间卡顿。
- 任务中心通知 `active=21431` 与通知页 `active_user_count=21429` 的差异来源明确：
  - 通知页展示计划分组数 `21429`。
  - 任务中心额外纳入数据库通知任务队列：`2` 条 `claimed` 和 `998` 条 `failed_retry`。
  - 因此任务中心 active 多 `2`，total 多 `1008`；这是任务中心“计划分组 + DB 任务队列”的宽口径，不是通知页丢数。
- 真实前端三页均可打开，业务 API 失败为 `0`。
- 本轮没有发现安全可修的业务代码问题。

## 计划页耗时

每页 `50` 条，接口层计时结果：

- 关机计划：
  - 第 `1` 页：`412.7 ms`
  - 第 `2` 页：`323.8 ms`
  - 第 `2000` 页：`504.6 ms`
  - 最后一页 `39600`：`267.3 ms`，loaded `40`
- 服务器删除计划：
  - 第 `1` 页：`297.4 ms`
  - 第 `2` 页：`263.0 ms`，loaded `0`
- 服务器删除历史：
  - 第 `1` 页：`393.3 ms`
  - 第 `2` 页：`327.4 ms`
  - 最后一页 `401`：`339.9 ms`，loaded `10`
- IP 删除计划：
  - 第 `1` 页：`849.2 ms`
  - 第 `2` 页：`818.2 ms`
  - 第 `2000` 页：`1041.7 ms`
  - 最后一页 `10000`：`1530.2 ms`
- IP 删除历史：
  - 第 `1` 页：`795.0 ms`
  - 第 `2` 页：`704.0 ms`
  - 第 `2000` 页：`1345.3 ms`
  - 最后一页 `10401`：`684.4 ms`

结论：

- 10 万边界页均低于 `1.4` 秒。
- IP 删除计划最后页约 `1.5` 秒，仍是后续优化优先点，但本轮未达到必须修复的程度。

## 真实前端验证

实际打开：

```text
http://127.0.0.1:5666/admin/tasks
http://127.0.0.1:5666/admin/tasks/plans
http://127.0.0.1:5666/admin/tasks/notices
```

真实页面和接口结果：

- 任务中心：
  - 生命周期 total/active：`2479992/2479992`
  - 通知 total/active/failed/warning：`22437/21431/1007/7`
  - 自动续费 total/active/failed/warning：`4740/171/171/171`
- 计划页：
  - 关机计划 total `1979990`，loaded `50`
  - 服务器删除计划 total `2`，loaded `2`
  - 服务器删除历史 total `20010`，loaded `50`
  - IP 删除计划 total `500000`，loaded `50`
  - IP 删除历史 total `520010`，loaded `50`
- 通知页：
  - 近期通知 `3428`
  - 未来通知 `18001`
  - 活跃通知分组 `21429`
  - 通知历史 `14960`
- 控制台 error/warning：`0`
- 业务 API 失败：`0`
- 浏览器层 `requestfailed` 为 Vite 开发环境脚本 `net::ERR_ABORTED`，非业务 API。

截图：

```text
/private/tmp/shop-tasks-patrol.png
/private/tmp/shop-plans-patrol.png
/private/tmp/shop-notices-patrol.png
```

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center.CloudTaskCenterApiTestCase.test_notice_section_total_uses_full_plan_counts_not_preview_items cloud.tests_task_center.CloudTaskCenterApiTestCase.test_notice_section_counts_pending_db_task_without_notice_plan cloud.tests_task_center.CloudTaskCenterApiTestCase.test_notice_section_counts_failed_db_task_without_notice_log --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过。命中项为既有测试桩、Telegram 登录账号 API 文件名，以及 `CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实回流。

## 清理

- 已删除临时后台登录用户：
  - `codex_patrol_frontend_tasks_probe`
  - `codex_patrol_timing_probe`
- 已删除 `/private/tmp/shop_frontend_tasks_probe_token.txt`。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥、云厂商密钥或完整代理链接。

## 下一步

- 继续巡检代理列表各标签和任务页面的 10 万级翻页真实性。
- 继续关注 IP 删除计划最后页约 `1.5` 秒的加载耗时。
- 如需通知计划也达到 10 万级，需要单独设计可清理的通知分组压测数据。
