# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 23:02 CST
- 状态：完成服务器删除历史口径补强，把无订单已删除服务器资产纳入计划页服务器删除历史查询层。
- 本轮范围：生命周期计划查询层、服务器删除历史分页、孤儿删除服务器资产回归测试、真实计划页数量对账。

## 本轮修复

- `cloud/lifecycle_plan_queries.py`
  - 新增服务器删除历史查询层：
    - `server_delete_history_order_queryset()`
    - `server_delete_history_asset_queryset()`
    - `server_delete_history_counts()`
    - `server_delete_history_page_sources()`
  - 服务器删除历史总数现在等于已删除云订单数量 + 无订单已删除服务器资产数量。
  - 无订单已删除服务器资产会排除未附加固定 IP 口径，避免和 IP 删除历史混表。
- `bot/api.py`
  - 计划页服务器删除历史改为复用查询层来源。
  - 新增无订单已删除服务器资产的历史行 payload，详情入口指向资产详情页。
- `cloud/tests.py`
  - 新增 `test_lifecycle_plans_server_history_includes_orphan_deleted_server_asset`。
  - 覆盖无订单已删除服务器必须出现在 `server_history_items`，且不能混入 `ip_delete_history_items`。

## 数据库与页面对账

- 当前真实库只读统计：
  - 已删除云订单：20009。
  - 无订单已删除服务器资产：0。
  - 预期服务器删除历史总数：20009。
- 实际打开 `/admin/tasks/plans`：
  - 页面标题为“计划”。
  - 页面包含“服务器删除历史记录”。
  - 页面显示 `服务器删除历史记录（已加载 50 / 总 20009）`。
  - 页面总数与数据库预期总数一致。
  - 页面无加载失败 / 请求失败 / 异常文案。
  - 控制台 error 为 0，warning 为 0。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_history_includes_orphan_deleted_server_asset cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_returns_server_delete_history_table --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py cloud/lifecycle_plan_queries.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
git diff --check
```

SQLite `db_comment` 警告为已知数据库能力差异，不影响本轮结果。

## 清理

- 本轮使用临时后台账号 `codex_ui_tester` 做页面验证，已删除。
- 本轮启动本地 Django `runserver` 做页面验证，已停止。
- 本轮 Playwright 浏览器、`.playwright-cli/` 和临时登录态文件已清理。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、真实支付、链上广播、生产发布、删除业务数据或删除测试库。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。

## 剩余风险与下一轮

- 继续执行不少于 4 小时的自动巡检目标。
- 下一轮继续做代理列表和计划页深分页真实性对账，重点验证翻页 / 跳页不丢数据、不重复数据。
- 继续关注 150 万资产数据下首屏冷加载性能，优化时必须保持数据库精确对账一致。
