# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-13 01:52 CST
- 状态：已完成生命周期计划页五张表 10 万级分页压测，并修复压测发现的尾页问题。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 目标主分支：`main`

## 本轮背景

- 用户要求压测计划页几个表，目标 10 万，并验证每页数据是否正确。
- 根据红线，本轮使用全新独立 SQLite 压测库，不复用当前业务库、手工真机测试库或含真实用户数据的库。
- 未执行真实云资源创建、绑定、解绑、释放、删除、真实支付或链上广播。

## 修改摘要

- `cloud/management/commands/stress_lifecycle_plans.py`
  - 新增生命周期计划页专项压测命令。
  - 支持在隔离压测库中为五张表分别造 10 万数据：
    - 关机计划 `shutdown_plan`
    - 服务器删机计划 `server_delete`
    - 服务器删除记录 `server_history`
    - 未附加 IP 删除计划 `ip_delete`
    - IP 删除历史 `ip_delete_history`
  - 校验每张表计数、排序基准、关键页分页结果，以及计划页 API 首/中/末页返回。
- `cloud/lifecycle_plan_queries.py`
  - 修复未附加 IP 删除计划尾页优化中的三列解包错误。
  - 服务器删除记录无搜索分页增加 UNION 页查询路径，提升跨订单/资产来源的深页展示能力。
- `cloud/tests_load_test_db.py`
  - 补充 10 万级压测 IP 生成边界测试，避免压测造数撞 `CloudAsset.public_ip` 唯一索引。

## 压测结果

- 独立数据库：`.shop-load-tests/shop-loadtest-lifecycle-plans-100k.sqlite3`
- 报告文件：`.shop-load-tests/lifecycle-plans-loadtest-100k-report.json`
- 规模：五张表各 100,000 条，合计约 500,000 条计划页相关记录。
- 计数全部通过：
  - `shutdown_plan=100000`
  - `server_delete=100000`
  - `server_history=100000`
  - `ip_delete=100000`
  - `ip_delete_history=100000`
- 每张表按 `page_size=1000` 校验关键页：`1, 2, 49, 50, 51, 99, 100`。
- 计划页 API 按前端页大小 `20` 校验第 `1`、`2500`、`5000` 页，每次均返回 20 条且分页总数正确。
- API 抽查最大耗时：
  - `shutdown_plan` 末页约 `0.552s`
  - `server_delete` 中页约 `0.588s`
  - `server_history` 末页约 `1.142s`
  - `ip_delete` 末页约 `1.083s`
  - `ip_delete_history` 末页约 `0.303s`

## 验证命令

通过：

```bash
uv run python -m py_compile cloud/lifecycle_plan_queries.py cloud/management/commands/stress_lifecycle_plans.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_load_test_db.PrepareLoadTestDbCommandTestCase --settings=shop.settings --verbosity=1
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_plan_tail_page_keeps_exact_order cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_delete_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_history_mixes_orders_and_assets_by_updated_at --keepdb --noinput --verbosity 2
uv run python manage.py check
git diff --check
```

10 万压测命令：

```bash
uv run python manage.py prepare_load_test_db --sqlite-name .shop-load-tests/shop-loadtest-lifecycle-plans-100k.sqlite3 --migrate --confirm-isolated
DB_ENGINE=sqlite SQLITE_NAME=.shop-load-tests/shop-loadtest-lifecycle-plans-100k.sqlite3 SHOP_LOAD_TEST_DB=1 uv run python manage.py stress_lifecycle_plans --seed --validate --target 100000 --page-size 1000 --frontend-page-size 20 --report-json .shop-load-tests/lifecycle-plans-loadtest-100k-report.json --confirm-isolated
```

## 风险和下一步

- 本轮压测库和报告保留在 `.shop-load-tests/`，不提交数据库文件；清理策略为删除本轮 `shop-loadtest-lifecycle-plans-100k.sqlite3` 和对应 report。
- 本轮只修复计划页查询/压测发现的问题，不改生命周期执行器、真实云同步和支付链路。
- 如需继续扩到 50 万，应沿用本轮 `stress_lifecycle_plans` 命令并使用新的独立压测库文件。
