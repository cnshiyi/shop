# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 19:12 CST
- 状态：完成通知计划 10 万级压测，并修复通知计划深页 `offset` 超过 `100000` 后被静默截断的问题。
- 后端 Commit：待提交。
- 前端 Commit：本轮无前端变更。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 页面：`http://127.0.0.1:5666/admin/tasks/notices`
- 重点：
  - 通知计划 10 万级用户分组压测。
  - 通知计划第 1 页、第 2 页、第 10000 页、最后页数据库对账。
  - 真实前端打开通知计划页，并切换第 2 页、第 10000 页、最后页。
  - 清理临时压测数据后复核业务统计恢复。

## 发现并修复的问题

- 问题：通知计划接口 `notice_task_detail` 把 `offset` 和 `history_offset` 最大值限制为 `100000`。
- 影响：当通知计划超过 10 万分组时，前端跳最后页会被后端静默截断到 `offset=100000`，显示错页数据。
- 修复：新增 `NOTICE_PLAN_MAX_OFFSET = 10_000_000`，通知计划和通知历史 offset 使用该上限。
- 回归测试：新增 `test_notice_task_detail_allows_deep_offsets_beyond_100k`，确认 `offset=120000` 和 `history_offset=130000` 不再被截断。

## 压测数据

本轮临时注入：

- `100000` 个临时 Telegram 用户。
- `100000` 个临时云订单。
- `100000` 个临时 `CloudAsset`。
- 每个临时用户 1 个 completed 订单和 1 个 active server asset，形成 `renew_notice/future` 用户通知分组。
- 临时数据使用专用前缀：
  - 用户 `first_name`：`codex_notice_10w_20260608_`
  - 订单号：`CN10W20260608`
  - 资产名：`codex_notice_10w_20260608_`

注入后通知计划统计：

- 近期通知：`3428`
- 未来通知：`118001`
- 活跃用户通知分组：`121429`

清理后通知计划统计恢复：

- 近期通知：`3428`
- 未来通知：`18001`
- 活跃用户通知分组：`21429`
- 临时资产、订单、用户残留：`0`

## 后端对账

分页口径：

- `limit=10`
- 第 1 页：`offset=0`
- 第 2 页：`offset=10`
- 第 10000 页：`offset=99990`
- 最后一页：修复前请求被截断；修复后 `offset=121419` 与数据库排序一致。

修复后接口结果：

- 第 1 页：`loaded=10`，数据库顺序一致。
- 第 2 页：`loaded=10`，数据库顺序一致。
- 第 10000 页：`loaded=10`，数据库顺序一致。
- 最后一页：`loaded=10`，数据库顺序一致。
- 接口耗时约 `4.1s/页`，后续仍可优化。

## 真实前端验证

真实打开页面：

```text
http://127.0.0.1:5666/admin/tasks/notices
```

页面内实际触发请求：

- `offset=0`，`total=121429`，`loaded=10`
- `offset=10`，`total=121429`，`loaded=10`
- `offset=99990`，`total=121429`，`loaded=10`
- `offset=121420`，`total=121429`，`loaded=9`

前端结果：

- 页面显示 `121429` 组用户通知。
- 业务 API 失败：`0`
- 控制台 error/warning：`0`
- request failed：`0`
- 截图：

```text
/private/tmp/shop-notice-10w-front-current.png
```

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_allows_deep_offsets_beyond_100k cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_counts_all_future_groups_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_deep_group_page_has_no_duplicates cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_basic_fields_skip_batch_text_payload --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_tasks.py cloud/tests.py
git diff --check
```

红线扫描通过。命中项为既有测试桩、Telegram 登录账号 API 文件名，以及 `CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实回流。

## 清理

- 已删除本轮 `100000` 条临时资产。
- 已删除本轮 `100000` 条临时订单。
- 已删除本轮 `100000` 条临时 Telegram 用户。
- 已删除临时后台用户：
  - `codex_notice_10w_api_probe`
  - `codex_notice_10w_front_probe`
- 已删除 `/private/tmp/shop_notice_10w_front_token.txt`。

## 尚未压测完

- 机器人还没有完成多任务高并发真机点击压测。
- 真实云资源创建、关机、删机、释放 IP 的生命周期开关矩阵还没有完整闭环。
- 服务器创建后的完整生命周期链路还没有在本轮压测中闭环到真实关机、真实删机和真实 IP 释放。
- 通知计划当前 10 万级可正确翻页，但接口约 `4.1s/页`，后续可继续优化查询层。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播或生产发布。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接或登录 token。
- `docs/real-machine-test-report.md` 当前有未提交的真实机器测试记录，本轮不覆盖、不提交该脏文件。

## 下一步

- 继续按 10 万级推进，不再做百万级压测。
- 优先补机器人多任务高并发真机点击压测。
- 继续补真实生命周期关机、删除、IP 删除开关组合闭环测试，并对真实资源 ID 脱敏记录。
- 继续优化通知计划 `4.1s/页` 的 10 万级查询耗时。
