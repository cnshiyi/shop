# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 19:15 CST
- 状态：完成生命周期计划 / 任务中心分页契约专项审计，未发现需要立即修复的回归；真实页面与真实 MySQL 对账被当前沙箱限制阻塞。
- 本轮范围：生命周期计划分页契约、IP 删除历史分页契约、任务中心生命周期状态统计聚合。

## 审计摘要

- 复查 `bot/api.py` 的 `lifecycle_plans` 分页入口，确认关机计划、删机计划、IP 删除计划和 IP 删除历史继续使用独立分页参数，不存在把多个表混成同一分页状态的回流。
- 复查 `cloud/lifecycle_plan_queries.py` 的 `paged_queryset()`、`server_lifecycle_plan_page()`、`ip_delete_history_page_sources()`，确认深页仍走当前反向截取策略，分页契约继续以精确总数和稳定排序返回。
- 复查 `cloud/task_center.py` 的生命周期聚合，确认最近失败历史、数据库任务和计划项的去重优先级维持现状，没有发现状态统计漏报或重复计数回归。
- 尝试执行真实浏览器页与真实 MySQL 对账时，当前会话对 `127.0.0.1:5666`、`127.0.0.1:8000` 以及本地 MySQL socket 连接均返回 `EPERM/Operation not permitted`，因此本轮只能完成 SQLite 聚焦验证，不能完成要求中的真实页面点击和真实库对账。

## 数据与样本

- 生命周期分页样本：覆盖删机计划第 1/2 页分页契约、IP 删除历史第 1/2 页分页契约。
- 状态聚合样本：覆盖“最近失败历史计入 failed”“仅 DB 失败任务也应计入 failed”“DB 任务优先于重复计划项”三类任务中心生命周期场景。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_ip_delete_history_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_split_shutdown_before_server_delete --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center.CloudTaskCenterApiTestCase.test_lifecycle_section_counts_recent_failed_history_as_failed cloud.tests_task_center.CloudTaskCenterApiTestCase.test_lifecycle_section_counts_failed_db_task_without_history_log cloud.tests_task_center.CloudTaskCenterApiTestCase.test_lifecycle_section_prefers_db_task_over_duplicate_plan_item --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py cloud/lifecycle_plan_queries.py cloud/task_center.py
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

结果：4 个生命周期分页聚焦测试、3 个任务中心生命周期聚焦测试、Django 系统检查、编译检查和前后端空白检查均通过。SQLite 的 `db_comment` 警告仍为已知数据库能力差异。

## 阻塞与边界

- 当前沙箱禁止访问本地 `127.0.0.1` 端口，无法打开 `http://127.0.0.1:5666` 前端页面或直连 `http://127.0.0.1:8000` 后端接口做真实浏览器验证。
- 当前沙箱禁止连接本地 MySQL（`127.0.0.1` 返回 `Operation not permitted`），无法对真实 50 万到 100 万级数据做数据库对账和深分页耗时采样。
- 因此本轮没有伪造“真实页面点击”或“真实数据库对账”结论，只记录了可执行的只读契约审计结果。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款入口或旧兼容壳。

## 剩余风险

- 本轮未能完成用户要求中的真实浏览器翻页/跳页和真实 MySQL 数据库对账，50 万到 100 万级耗时结论仍缺失。
- 当前后端工作区存在未提交业务改动：`cloud/api_asset_snapshots.py`、`cloud/management/commands/refresh_cloud_asset_dashboard_snapshots.py`、`cloud/tests.py`，本轮未介入这些补丁。
- 下一轮应在具备本地端口和 MySQL 访问能力的环境中，继续对任务中心、生命周期计划和通知计划执行真实页面点击、深页跳页和数据库精确对账。
