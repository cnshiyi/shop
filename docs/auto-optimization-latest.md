# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 12:44 CST
- 状态：已完成“先拆当前补丁”的重构收口。本轮只保留直接从 `CloudAsset` / `CloudIpLog` 服务端分页的最小补丁，暂缓 `CloudLifecycleTask` 计划页投影路线。
- 本轮范围：生命周期计划查询层抽离、计划页四张表分页契约、后端索引、前端服务端分页参数和 Ant Table 翻页。

## 修改内容

- 拆除本轮未提交的任务表投影补丁：
  - 删除暂缓文件 `cloud/lifecycle_plan_projection.py`。
  - 删除暂缓迁移 `cloud/migrations/0058_lifecycle_task_plan_page_index.py`。
  - 删除临时产物 `.playwright-cli/`。
  - 计划页不再读取 `CloudLifecycleTask` 作为数据源；任务表投影路线后续单独补丁处理。
- 后端查询层：
  - 新增 `cloud/lifecycle_plan_queries.py`，集中封装生命周期计划页的 queryset/count/page。
  - 抽出服务器关机/删除计划查询、IP 删除活动计划查询、IP 删除历史来源分页查询。
  - `bot/api.py` 保留鉴权、参数解析和响应拼装，底层数据读取委托给查询层。
- 分页契约：
  - 生命周期计划 API 统一返回 `pagination.{table}.page/page_size/total/loaded`。
  - 关机计划、删除计划、IP 删除计划、IP 删除历史记录均支持独立服务端页码和页大小。
  - 前端计划页信任后端排序，不再对四张计划表做二次排序。
- 索引：
  - `CloudAsset` 增加生命周期计划分页索引。
  - `CloudIpLog` 增加 IP 删除历史按事件和倒序 ID 翻页索引。

## 验证

本地已通过：

```bash
uv run python -m py_compile bot/api.py cloud/lifecycle_plan_queries.py cloud/tests.py cloud/models.py cloud/management/commands/refresh_lifecycle_plans.py cloud/dashboard_snapshots.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_future_server_assets_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_ip_delete_plans_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_ip_delete_history_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_reuses_cached_count_snapshot_after_refresh cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract --settings=shop.settings --verbosity=2
DB_ENGINE=mysql uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py check
DB_ENGINE=mysql uv run python manage.py migrate --plan
git diff --check
/Users/a399/.homebrew/bin/pnpm -C /Users/a399/Desktop/data/vue-shop-admin --filter @vben/web-antd typecheck
git diff --check
```

结果：后端编译通过；6 条生命周期计划聚焦测试通过；MySQL 和默认 `manage.py check` 通过；无待生成迁移；MySQL 迁移计划无待执行迁移；前端 typecheck 通过；两边 diff 空白检查通过。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照或旧退款入口。

## 剩余风险

- `CloudLifecycleTask` 作为计划页最终/可选数据源的路线已暂缓，需后续单独补丁明确刷新策略、执行器边界和页面只读任务表策略。
- 任务中心生命周期、通知、自动续费统计仍需后续抽 domain metrics，避免和计划页口径再次分叉。
- 机器人返回链仍需后续抽 callback source 编解码模块，集中处理 64 字节限制。
- 本地高数据压测数据仍保留，清理需要单独确认。
