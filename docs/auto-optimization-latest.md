# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-06 14:08 CST
- 状态：已修复计划表和通知表的计数口径问题：总数不再使用当前加载行数或构造上限，而是统计全库真实未来计划；列表仍按 `limit` 分批加载，避免 50 万数据一次性塞进前端。
- 本轮范围：生命周期计划 API、通知计划 API、计划页标题显示、聚焦测试和真实前端复测。

## 修改内容

- 生命周期计划：
  - `shutdown_plan_count` 改为全库“未完成关机且有到期计划”的服务器资产数。
  - `server_delete_count` 改为全库“有到期计划”的服务器资产数，远期删除也算计划。
  - 删除计划列表会显示未来计划，但待执行删除仍保留阶段门槛：只有关机阶段完成且状态允许，才会进入待执行删除。
  - 前端计划页标题改用后端总数，不再用当前加载行数。
- 通知计划：
  - `due_count/future_count/due_user_count/future_user_count/active_user_count` 改为全量统计，不再受 `future_limit` 或内部构造上限影响。
  - 当前页列表仍只返回请求的 `limit/future_limit/history_limit`，保持页面加载可控。
- 新增聚焦测试：
  - 生命周期计划总数超过当前加载 limit 时仍返回全量 count。
  - 通知未来计划超过当前加载 limit 时仍返回全量 future_count。

## 验证结论

- 当前 50 万数据库 API 对账：
  - 计划表：`shutdown_plan_count=453489`，当前加载关机行 `50`；`server_delete_count=454747`，当前加载删除行 `50`；`ip_delete_count=1`，`ip_delete_history_count=7`。
  - 通知表：`active_user_count=36033`，`due_count=5401`，`future_count=30632`；当前加载通知行 `10`。
- 真实浏览器复测通过：
  - 计划页显示 `关机计划（453489）`、`删除计划（454747）`、`IP删除计划（1）`，接口 200。
  - 通知页显示 `36033 组用户通知`、近期 `5401`、未来 `30632`，接口 200。
  - 浏览器 console error 为 0。

## 最近验证

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py cloud/api_tasks.py cloud/tests.py
DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_future_server_assets_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_counts_all_future_items_beyond_loaded_limit --settings=shop.settings --verbosity=2
pnpm -C /Users/a399/Desktop/data/vue-shop-admin --filter @vben/web-antd typecheck
```

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除数据或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照或旧退款入口。

## 剩余风险

- 本地 50 万压测数据仍保留，清理需要单独确认。
- 全量通知统计会额外扫描通知候选资产；当前本地 50 万数据下通知接口约 5.3 秒，后续如要求低于 2 秒，需要把通知统计预聚合或缓存化。
- 计划表现在统计全量计划，列表仍分批加载；后续可增加服务端分页/跳页到指定计划页。
