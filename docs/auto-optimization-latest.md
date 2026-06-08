# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 19:36 CST
- 状态：完成通知计划字段开关加载优化，并重新完成 10 万级通知分组后端分页、数据库对账和真实前端复测。
- 后端变更：`cloud/api_tasks.py`、`cloud/tests.py`
- 前端变更：`/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src/views/dashboard/tasks/notices.vue`

## 本轮修复

- 通知计划后端新增 `actions` 字段开关。
- 当前端只请求 `fields=basic` 时，后端不再查询当前分组的订单/IP/文案明细。
- 当前端请求 `fields=basic,actions` 时，后端只取当前分组第一条订单用于“订单详情”链接，不再加载隐藏的 IP 列表和通知文案。
- 当前端打开 IP 或文案列时，后端仍按原逻辑加载完整分组明细。
- 前端通知计划页会把“操作”列开关同步传给后端，关闭操作列后请求参数变为 `fields=basic`。

## 10 万级压测

临时注入：

- `100000` 个临时 Telegram 用户。
- `100000` 个临时云订单。
- `100000` 个临时 `CloudAsset`。
- 临时前缀：
  - 用户 `first_name`：`codex_notice_perf_20260608_`
  - 订单号：`CNPERF20260608`
  - 资产名：`codex_notice_perf_20260608_`

注入后：

- 通知分组总数：`121429`
- 近期通知：`3428`
- 未来通知：`118001`

后端 HTTP 复测：

- `fields=basic`：第 1 页、第 2 页、第 10000 页、最后页约 `2.0s - 2.15s/页`。
- `fields=basic,actions`：第 1 页、第 2 页、第 10000 页、最后页约 `2.0s - 2.08s/页`。
- `fields=basic,actions,ips`：第 1 页、第 2 页、第 10000 页、最后页约 `2.0s - 2.11s/页`。
- 相比上一轮约 `4.1s/页`，深分页耗时已明显下降。

数据库对账：

- 对账页位：`offset=0`、`offset=10`、`offset=99990`、`offset=121420`。
- 4 个页位 API 返回 ID 顺序均与数据库排序结果一致。
- 4 个页位互相无重复。
- 最后一页 `loaded=9`，符合 `121429` 总数。

真实前端验证：

- 页面：`http://127.0.0.1:5666/admin/tasks/notices`
- 实际触发请求：
  - `offset=0`，`fields=basic,actions`，`total=121429`，`loaded=10`
  - `offset=10`，`fields=basic,actions`，`total=121429`，`loaded=10`
  - `offset=99990`，`fields=basic,actions`，`total=121429`，`loaded=10`
  - `offset=121420`，`fields=basic,actions`，`total=121429`，`loaded=9`
  - 关闭操作列后 `fields=basic`
  - 打开 IP 列后 `fields=basic,ips,actions`
- 业务 API 失败：`0`
- 控制台 error/warning：`0`
- request failed：`0`
- 截图：`/private/tmp/shop-notice-perf-10w-front.png`

## 清理

- 已删除本轮 `100000` 条临时资产。
- 已删除本轮 `100000` 条临时订单。
- 已删除本轮 `100000` 个临时 Telegram 用户。
- 临时资产、订单、用户残留：`0`
- 清理后统计恢复：
  - 通知分组总数：`21429`
  - 近期通知：`3428`
  - 未来通知：`18001`

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_basic_fields_skip_batch_text_payload cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_basic_actions_fields_keep_order_link_without_hidden_columns cloud.tests.CloudServerServicesTestCase.test_notice_plan_summary_reuses_group_rows_for_counts cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_deep_group_page_has_no_duplicates --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_tasks.py cloud/tests.py
pnpm -F @vben/web-antd run typecheck
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

红线扫描通过。命中项仍为既有测试桩、Telegram 登录账号 API 文件名，以及 `CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实回流。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播或生产发布。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥、云厂商密钥、登录 token 或完整代理链接。
- `docs/real-machine-test-report.md` 当前存在未提交真实机器测试记录，本轮不覆盖、不提交。

## 尚未完成

- 机器人多任务高并发真机点击压测还没有完成。
- 真实云资源创建、到期关机、删机、释放 IP 的生命周期开关矩阵还没有完整闭环。
- 服务器创建后的完整生命周期链路还没有在本轮压测中闭环到真实关机、真实删机和真实 IP 释放。
