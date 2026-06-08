# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 19:44 CST
- 状态：完成生命周期计划页只读巡检，覆盖后端分页、数据库对账、真实前端翻页和列开关。
- 后端 Commit：待提交。
- 前端 Commit：本轮无前端变更，`/Users/a399/Desktop/data/vue-shop-admin` 工作区干净。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端页面：`http://127.0.0.1:5666/admin/tasks/plans`
- 后端接口：`/api/admin/tasks/plans/`
- 查询层：`cloud/lifecycle_plan_queries.py`
- 响应拼装：`bot/api.py` 的 `lifecycle_plans`

## 当前数据规模

真实库当前计划页统计：

- 关机计划：`1979990`
- 删除计划：`2`
- 服务器删除历史：`20010`
- IP 删除计划：`500000`
- IP 删除历史：`520010`

## 数据库对账

本轮按每页 `50` 条对账，直接比对 API 返回 `plan_item_key` 与查询层/数据库排序结果：

- 关机计划：
  - 第 1 页、第 2 页、第 1000 页、最后页第 `39600` 页。
  - 最后一页 `40` 条。
  - API 顺序与数据库顺序一致，无重复。
- 删除计划：
  - 第 1 页。
  - 总数 `2`，加载 `2`。
  - API 顺序与数据库顺序一致。
- 服务器删除历史：
  - 第 1 页、第 2 页、最后页第 `401` 页。
  - 最后一页 `10` 条。
  - API 顺序与数据库顺序一致，无重复。
- IP 删除计划：
  - 第 1 页、第 2 页、第 1000 页、最后页第 `10000` 页。
  - 最后一页 `50` 条。
  - API 顺序与数据库顺序一致，无重复。
- IP 删除历史：
  - 第 1 页、第 2 页、第 1000 页、最后页第 `10401` 页。
  - 最后一页 `10` 条。
  - API 顺序与数据库顺序一致，无重复。

## 真实前端验证

真实打开：

```text
http://127.0.0.1:5666/admin/tasks/plans
```

前端实际触发：

- 初始全表加载：关机计划 `50`、删除计划 `2`、服务器历史 `50`、IP 删除计划 `50`、IP 删除历史 `50`。
- 关机计划：第 `2` 页、第 `1000` 页、最后页第 `39600` 页。
- 服务器删除历史：第 `2` 页、最后页第 `401` 页。
- IP 删除计划：第 `2` 页、第 `1000` 页、最后页第 `10000` 页。
- IP 删除历史：第 `2` 页、第 `1000` 页、最后页第 `10401` 页。
- 列开关：关闭执行相关列后请求降为 `fields=basic`。

结果：

- 页面可见计划总数：关机计划 `1979990`、IP 删除计划 `500000`、IP 删除历史 `520010`。
- 业务 API 失败：`0`
- 控制台 error/warning：`0`
- request failed：`0`
- 截图：`/private/tmp/shop-lifecycle-plans-front-current.png`

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py cloud/lifecycle_plan_queries.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract cloud.tests.CloudServerServicesTestCase.test_ip_delete_history_page_sources_reverse_tail_keeps_order cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_split_shutdown_before_server_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_global_stage_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_separate_ip_delete_plan_and_history_items --settings=shop.settings --verbosity=1
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

红线扫描通过。命中项仍为既有测试桩、Telegram 登录账号 API 文件名，以及 `CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实回流。

## 结论

- 本轮没有发现生命周期计划页分页丢数据、串页、最后页错误或前端加载错误。
- 关机计划、删除计划、服务器删除历史、IP 删除计划、IP 删除历史当前均能按服务端分页契约返回 `pagination.{table}.page/page_size/total/loaded`。
- 本轮没有代码修复，仅更新巡检记录。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播或生产发布。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥、云厂商密钥、登录 token 或完整代理链接。
- `docs/real-machine-test-report.md` 当前存在未提交真实机器测试记录，本轮不覆盖、不提交。

## 尚未完成

- 机器人多任务高并发真机点击压测还没有完成。
- 真实云资源创建、到期关机、删机、释放 IP 的生命周期开关矩阵还没有完整闭环。
- 服务器创建后的完整生命周期链路还没有在本轮压测中闭环到真实关机、真实删机和真实 IP 释放。
