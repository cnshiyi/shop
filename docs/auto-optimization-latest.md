# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 23:03 CST
- 状态：完成压测数据库隔离改造，新增独立压测库准备命令和安全测试。
- 后端提交：本轮完成后提交，具体哈希以 `git log -1` 为准。
- 前端提交：无前端代码变更。

## 本轮背景

- 本轮按 `TODO.md` 首个未完成任务执行“压测数据库隔离改造”。
- 目标是让后续性能压测、批量造数和深分页压测先创建全新的独立测试数据库，避免复用当前业务库、手工真机测试库或含真实用户数据的库。
- 本轮不执行真实支付、链上广播、真实云资源操作、生产发布或删除数据。

## 本轮调整

- 新增 `cloud/management/commands/prepare_load_test_db.py`
  - 默认 dry-run，只输出隔离压测库环境，不创建库、不写数据。
  - 支持 `--sqlite-name` 指定 `.shop-load-tests/` 下且文件名包含 `loadtest` 的 SQLite 压测库。
  - 实际迁移或造数必须显式传入 `--confirm-isolated`。
  - `--migrate` 会以 `DB_ENGINE=sqlite`、`SQLITE_NAME=<压测库>`、`SHOP_LOAD_TEST_DB=1` 运行 `migrate --noinput`。
  - `--seed-assets N` 只在当前连接已切到目标隔离 SQLite 库且带 `SHOP_LOAD_TEST_DB=1` 时写入 CloudAsset 测试资产，并回填代理列表快照。
  - 输出清理策略：删除 `.shop-load-tests/` 下本轮生成的 loadtest SQLite 文件。
- 新增 `cloud/tests_load_test_db.py`
  - 覆盖 dry-run 输出、禁止仓库默认库路径、禁止未确认写入和测试 IP 生成。
- 更新 `.gitignore`
  - 忽略 `.shop-load-tests/`，避免提交压测数据库文件。
- 更新 `TODO.md`
  - 勾选“压测数据库隔离改造”。

## 本轮压测库记录

- 数据库名：`.shop-load-tests/shop-loadtest-smoke.sqlite3`
- 端口：无，SQLite 文件库。
- 造数规模：3 条 `CloudAsset` 测试资产，回填 3 条 `CloudAssetDashboardSnapshot` 快照。
- 压测/准备命令：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py prepare_load_test_db --sqlite-name .shop-load-tests/shop-loadtest-smoke.sqlite3 --migrate --seed-assets 3 --confirm-isolated
```

- 结果：迁移通过，造数和快照回填通过。
- 清理策略：删除 `.shop-load-tests/shop-loadtest-smoke.sqlite3`；整个 `.shop-load-tests/` 目录已加入 `.gitignore`。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py prepare_load_test_db --sqlite-name .shop-load-tests/shop-loadtest-dryrun.sqlite3
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/management/commands/prepare_load_test_db.py cloud/tests_load_test_db.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests_load_test_db --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py prepare_load_test_db --sqlite-name .shop-load-tests/shop-loadtest-smoke.sqlite3 --migrate --seed-assets 3 --confirm-isolated
```

## 结论

- 后续压测已有独立 SQLite 压测库准备入口，不再需要复用当前业务库。
- 命令默认只读 dry-run；写库路径有目录、文件名、环境标记和显式确认四层约束。

## 剩余风险

- 本轮只做 3 条资产的小规模 smoke 验证，未执行 10 万级深分页压测。
- SQLite 迁移仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。
