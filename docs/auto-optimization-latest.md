# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-03 15:14 CST
- 状态：已完成固定巡检和既有后台 API 路由拆分验证。
- 最近提交：本轮提交后以 `git log -1` 为准。
- 本轮改动：未修改运行代码；只覆盖更新本文件并在 `docs/refactor-version-record.md` 追加本轮中文记录。
- 本轮结论：`TODO.md` 固定任务已全部勾选，本轮按固定巡检清单执行。当前工作树已有未提交的 `/api/admin/` 与 `/api/auth/` 路由拆分、旧 `shop/dashboard_urls.py` 删除、测试路径和架构文档调整；本轮验证这些既有改动的 Django 检查、路由契约、任务中心、机器人返回链和字段红线均通过。

## 最近验证

- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile shop/urls.py shop/admin_urls.py shop/auth_urls.py bot/tests.py cloud/tests.py cloud/tests_task_center.py` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.ApiPrefixContractTestCase bot.tests.DashboardAuthSurfaceTestCase --settings=shop.settings --verbosity=2` 通过，共 10 个测试。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2` 通过，共 14 个测试。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase --settings=shop.settings --verbosity=2` 通过，共 49 个测试。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "...字段内省..."` 确认废弃 app 未安装，`CloudAsset` 到期字段只有 `actual_expires_at`，`CloudServerOrder` 未恢复 `actual_expires_at` 或 `service_expires_at`，`CloudAssetDashboardSnapshot` 未恢复到期字段。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run` 输出 `No changes detected`，但当前沙箱仍无法连接默认 MySQL `127.0.0.1:3306`，因此会打印迁移历史一致性检查 warning。
- 红线关键字扫描未发现订单到期字段、旧计划快照、旧退款函数名或废弃 runtime app 回流；命中项仍为资产侧唯一到期事实或固定 IP 回收时间同步。
- 废弃 app 目录扫描无输出。
- `git diff --check` 通过。

## 剩余风险

- 工作树仍保留本轮开始前已存在的未提交路由、测试和文档差异，本轮未回退、未覆盖，也不替用户判断是否应整体提交。
- 本轮未跑完整测试套件。
- 本轮未在真实 MySQL/MariaDB 上执行迁移计划，因为当前沙箱禁止连接本地 `127.0.0.1:3306`。
- 本轮未执行真实 Telegram 点击、真实云资源创建/删除/IP 变更、真实支付、链上广播、生产发布或不可逆操作。

## 下一步

- 若确认当前 `/api/admin/` 与 `/api/auth/` 路由拆分是预期方向，下一轮可优先清理或提交这批既有未提交改动；否则继续按固定巡检清单监控机器人返回链、云资产生命周期唯一到期事实和任务中心可观测性。
