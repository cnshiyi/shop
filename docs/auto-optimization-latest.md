# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-06 08:19 CST
- 状态：本轮按 `TODO.md` 无未完成明确任务后的固定巡检执行，优先复查上一轮 MySQL `migrate --plan` 未得到成功完成态的问题；未发现新的安全业务代码修复点，仅更新自动优化记录。
- 本轮范围：MySQL/OrbStack 本机连接诊断、废弃 runtime app 和到期字段回流巡检、机器人返回链与 `callback_data`、任务中心状态统计、支付详情缓存隔离验证。

## 修改内容

- 未修改业务代码。
- 覆盖更新本文件，并在 `docs/refactor-version-record.md` 追加本轮中文巡检记录。

## 巡检结论

- MySQL 目标仍是本机 `127.0.0.1:3306`，TCP 连接成功，监听进程为 OrbStack。
- 直接读取 MySQL 协议 greeting 在 3 秒内超时，说明连接已建立但服务端/端口代理未返回 MySQL 握手包。
- `DB_ENGINE=mysql uv run python manage.py migrate --plan` 约 10 秒后失败为 `OperationalError: Lost connection to MySQL server during query (timed out)`，与上一轮 timeout 修复后的预期一致：不再无限挂起，但本机 MySQL/OrbStack 状态仍未恢复到可完成迁移计划。
- 运行时 `INSTALLED_APPS` 未恢复 `accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- 字段内省确认 `CloudAsset` 到期字段仍只有 `actual_expires_at`；`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`；`CloudAssetDashboardSnapshot` 未恢复到期事实字段，仅保留 `asset_due_sort_at` 排序缓存和风险标记。
- 过滤运行时代码扫描未命中 `service_expires_at`、旧计划快照或旧退款入口；命中的 `actual_expires_at` 均为 `CloudAsset` 单一到期事实或 API 输出/编辑路径，废弃 app 名称命中主要是云账号、Telegram 账号、权限代号和当前 `core.dashboard_api` 模块。
- 机器人返回链、`callback_data` 长度、后台任务中心状态统计、支付扫描器和资源监控详情按钮缓存相关聚焦测试通过。

## 最近验证

- 后端检查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- MySQL TCP 诊断：`uv run python - <<'PY' ... socket.connect ...` 输出 `tcp_connect ok`；`lsof -nP -iTCP:3306 -sTCP:LISTEN` 显示 OrbStack 监听本机 3306。
- MySQL greeting 诊断：`uv run python - <<'PY' ... socket.recv(16) ...` 输出 `mysql_greeting TimeoutError timed out`。
- 迁移计划复查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan` 约 10 秒内失败为 MySQL 握手读取超时。
- 字段内省：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段/废弃 app 内省..."` 通过，输出 `retired_installed []`、`CloudAsset expiry fields ['actual_expires_at']`、`CloudServerOrder expiry fields {'actual_expires_at': False, 'service_expires_at': False}`、`CloudAssetDashboardSnapshot expiry-like fields ['asset_due_sort_at', 'risk_due_soon', 'risk_expired']`。
- 编译检查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py bot/keyboards.py cloud/task_center.py cloud/api_tasks.py cloud/lifecycle.py orders/payment_scanner.py cloud/api_assets.py cloud/api_orders.py cloud/api_asset_snapshots.py cloud/models.py cloud/sync_jobs.py shop/settings.py core/runtime_config.py` 通过。
- 聚焦测试：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase cloud.tests_task_center.CloudTaskCenterApiTestCase cloud.tests.CloudServerServicesTestCase.test_due_orders_use_asset_expiry_for_lightsail_lifecycle cloud.tests.CloudServerServicesTestCase.test_cloud_asset_detail_does_not_fallback_to_order_asset_expiry orders.tests.TronMonitorStatsTestCase.test_tx_detail_cache_is_scoped_per_user_for_same_hash orders.tests.TronMonitorStatsTestCase.test_resource_detail_cache_is_scoped_per_user_for_same_address_time --settings=shop.settings --verbosity=2` 通过，67 个测试 OK。
- 只读扫描：本机缺少 `rg`，本轮使用 `git ls-files '*.py' | grep ... | xargs grep -nE ...` 扫描旧到期字段、旧计划快照、旧退款入口和废弃 app 回流。
- SQLite 测试库输出不支持 `db_comment` / `db_table_comment` 的 warnings，属于当前测试环境预期差异；测试中故意触发的异常/警告路径已由断言覆盖。

## 剩余风险

- 当前 MySQL 服务或 OrbStack 端口代理仍在握手阶段无响应；仓库代码已避免无限等待，但本机仍未得到 `migrate --plan` 成功完成态。
- 本轮启动的 `docker ps` 只读探测也卡住，已清理该诊断进程；这进一步指向本机 OrbStack/Docker 控制面异常。
- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除数据或生产发布。
- 本地 50 万压测数据仍保留；清理属于删除数据操作，需要单独确认。
- 后端仓库仍有本轮无关未跟踪文档 `docs/jisou-bot-functions.md`、`docs/telegram-search-development-plan.md`、`docs/telegram-search-large-scale-architecture.md`，本轮未处理。

## 下一步

- 下一轮继续按固定巡检清单执行；如需恢复 MySQL 迁移计划成功态，应先在本机层面检查或重启 OrbStack/MySQL 容器/端口代理，再复跑 `DB_ENGINE=mysql uv run python manage.py migrate --plan`。
