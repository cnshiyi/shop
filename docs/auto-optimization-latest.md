# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 07:14 CST
- 状态：完成一轮机器人后台钱包并发隔离巡检和 12 万分组代理列表深页只读对账；未发现需要修改代码的问题。
- 本轮范围：`bot/tests.py` 新增并发用例核验、代理列表快照分组深页 helper 精确分页对账、生命周期/旧字段/废弃 app 红线扫描、前端工作区状态检查。

## 巡检结论

- 前端仓库 `/Users/a399/Desktop/data/vue-shop-admin` 当前 `git status --short` 为空，本轮未改动前端。
- 后端仓库当前存在未提交的 [`/Users/a399/Desktop/data/shop/bot/tests.py`](/Users/a399/Desktop/data/shop/bot/tests.py) 改动；本轮未覆盖或改写该文件。
- 新增的后台钱包并发隔离用例通过，`20` 组直付 + `20` 组补付 + `20` 组续费后检查并发下没有串 `chat_id`、数量或任务上下文。
- 复用临时 SQLite 审计库构造 `120005` 条 `CloudAsset` 和 `CloudAssetDashboardSnapshot`，验证代理列表分组深页 `start=120000/page_size=3` 的核心 helper 返回：
  - `expected=['user:120000', 'user:120001', 'user:120002']`
  - `actual=['user:120000', 'user:120001', 'user:120002']`
  - `match=True`
- 红线扫描未发现订单到期字段回流、旧计划快照、旧退款命名或废弃 runtime app 回流。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-automation-audit.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py migrate --settings=shop.settings --noinput
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop-automation-audit.sqlite3 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py shell --settings=shop.settings -c \"...12万分组快照构造与 _dashboard_snapshot_group_keys_from_ordered_rows 精确对账...\"
rg -n \"service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\.\" cloud bot orders core shop -g '!**/migrations/**'
git diff --check
```

说明：

- `manage.py check` 使用默认 MySQL 配置可直接通过。
- 当前沙箱禁止访问 `127.0.0.1:3306`，因此本轮无法连接本地 MySQL 做真实库只读对账，改为在独立临时 SQLite 库上做可重复的 `120005` 规模分页验证。
- 临时 SQLite 审计文件保留在 `/private/tmp/shop-automation-audit.sqlite3`。

## 受限项

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、支付密钥、云厂商密钥或完整代理链接。
- 本轮未运行浏览器前端翻页，因为沙箱环境下无法同时访问受限本地 MySQL 和后端页面数据源。

## 下一步

- 下一轮优先在可访问真实数据源的环境继续做代理列表深页/末页浏览器实测，补上 HTTP 接口与前端页面的第 2 页、深页、末页一致性验证。
- 继续关注 [`/Users/a399/Desktop/data/shop/bot/tests.py`](/Users/a399/Desktop/data/shop/bot/tests.py) 这批未提交并发测试是否还需要补充对日志脱敏或任务创建数量的断言。
