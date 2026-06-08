# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 22:00 CST
- 状态：完成生命周期计划页过期状态修复、真实库对账、真实前端打开验证和后端聚焦测试。
- 后端提交：已提交，提交信息 `fix: mark overdue lifecycle plans as due`。
- 前端提交：本轮无前端代码变更。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 页面：`http://127.0.0.1:5666/admin/tasks/plans`
- 后端服务：`http://127.0.0.1:8000`
- 重点链路：
  - 生命周期计划页服务端分页
  - 关机计划、服务器删除计划的 `queue_status`
  - 真实前端计划页加载、接口请求、控制台错误检查

## 修复内容

- `bot/api.py`
  - `_server_lifecycle_plan_page_items()` 不再把服务端分页出来的所有关机/删机计划默认标为 `scheduled_future`。
  - 根据计划时间和当前时间计算：
    - 已到执行时间：`due_now / 待执行`
    - 7 天窗口内：`within_window / 计划中`
    - 更远未来：`scheduled_future / 计划中`
- `cloud/tests.py`
  - 新增已过期删除计划的回归测试，固定过期删除计划必须显示为 `due_now`。
  - 补充未来计划测试的删除总开关显式配置，避免依赖默认值。

## 真实库对账

真实库复核目标：

- `CloudAsset #20418 / CloudServerOrder #20170`
  - 关机计划返回 `queue_status=due_now`
  - 执行状态为“已到关机时间，待执行关机服务器”
- `CloudAsset #326 / CloudServerOrder #94`
  - 删除计划返回 `queue_status=due_now`
  - 执行状态为“已到删除时间，待执行删除服务器”

结论：

- 已过期未执行的关机计划和删除计划不再被误标为未来计划。
- 本轮只做状态口径修复，没有执行真实关机、删机或 IP 释放。

## 真实前端验证

前端首次检查时发现 `127.0.0.1:8000` 后端不在，前端代理 `/api/admin/user/info` 返回 `502`，页面无法进入计划接口。

处理：

- 重新启动后端：`uv run python manage.py runserver 127.0.0.1:8000`
- 重新生成临时后台 session 登录态，只写入 `/private/tmp/shop-plans-storage-state.json`，未打印 session 内容。
- 使用系统 Chrome 打开真实前端页面。

结果：

- 页面成功进入：`/admin/tasks/plans`
- 不是登录页。
- 页面出现“关机计划”“删除计划”和“待执行”。
- 生命周期计划接口请求成功 `1` 次。
- 接口状态码：`200`
- 业务 code：`0`
- 控制台 error/warning：`0`
- request failed：`0`
- 4xx/5xx 响应：`0`
- 截图：`/private/tmp/shop-lifecycle-plan-status-front.png`
- 前端巡检 JSON：`/private/tmp/shop-lifecycle-plan-status-front.json`

接口返回样本确认：

- `server_delete` 第 1 页包含 `asset_id=326 / order_id=94`
- 该行 `queue_status=due_now`
- 该行 `execution_status=已到删除时间，待执行删除服务器`
- 分页元数据：
  - 关机计划 `total=1979933 / loaded=50`
  - 服务器删除计划 `total=59 / loaded=50`
  - 服务器删除历史 `total=20010 / loaded=50`
  - IP 删除计划 `total=500000 / loaded=50`
  - IP 删除历史 `total=520010 / loaded=50`

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_page_marks_overdue_delete_as_due_now cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_future_server_plan_item cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_split_shutdown_before_server_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\.|finance\.|mall\.|monitoring\.|dashboard_api\.|biz\." cloud bot orders core shop -g '!**/migrations/**'
```

命中项仍是允许项：bot 测试桩、Telegram 登录账号模块名、`CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实、旧计划快照或废弃 runtime app 回流。

说明：

- SQLite 聚焦测试仍输出既有 `db_comment` / `db_table_comment` 警告，不属于本轮问题。
- `docs/real-machine-test-report.md` 当前存在既有未提交真实机器测试记录，本轮不覆盖、不提交。

## 结论

- 生命周期计划页服务端分页状态口径已修复。
- 真实前端计划页已重新打开并验证通过。
- 本轮没有执行真实支付、链上广播、真实云资源创建/删除、生产发布或删除业务数据。

## 剩余风险

- 机器人多任务高并发真机点击压测仍受 Telegram 网络/session 状态影响，尚未完成。
- 真实云资源创建后的完整关机、删机、IP 释放闭环仍需继续在授权范围内逐项验证。
- 当前仍有既有真机报告脏文件 `docs/real-machine-test-report.md`，需要单独处理，不应混入本轮状态修复提交。
