# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-03 14:51 CST
- 状态：已修复后台任务中心对到期持久化 pending 任务的漏报。
- 最近提交：本轮提交后以 `git log -1` 为准。
- 本轮改动：`cloud/task_center.py` 将到期 `CloudLifecycleTask.STATUS_PENDING` 和 `CloudNoticeTask.STATUS_PENDING` 纳入生命周期/通知 section 的 active、total、status_counts 和 items；`cloud/tests_task_center.py` 补充无计划项/无通知日志时 pending DB 任务仍出现在总览的聚焦测试；`TODO.md` 勾选后台任务中心和状态统计复查。
- 本轮结论：云资产同步 worker、自动续费重试、生命周期计划和通知计划的任务中心聚合继续可见；本轮补齐了计划 bundle 或历史日志缺失时，持久化到期待执行/待通知任务被后台总览漏报的问题。

## 最近验证

- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/task_center.py cloud/tests_task_center.py cloud/api_tasks.py cloud/lifecycle_tasks.py cloud/sync_jobs.py` 通过。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=2` 通过，共 14 个测试。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py makemigrations --check --dry-run` 输出 `No changes detected`，仍有本地 MySQL 沙箱连接 warning。
- `git diff --check` 通过。
- 红线扫描未发现订单服务到期字段、旧计划快照、旧退款函数名或废弃 runtime app 回流；命中项仍为固定 IP 回收、资产侧 `actual_expires_at` 派生使用和测试数据。

## 剩余风险

- 工作树仍存在其它未提交路由/文档/测试路径改动，本轮未回退；提交时只暂存本轮任务中心修复、聚焦测试和中文记录。
- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源、真实支付、链上广播、生产发布或不可逆操作。
- SQLite 聚焦测试仍会打印不支持 `db_comment` 的预期 warning；`makemigrations --check --dry-run` 在默认 MySQL 连接被沙箱拒绝时仍会打印一致性检查 warning，但最终显示无迁移变化。

## 下一步

- 下一轮领取 `TODO.md` 中本地数据库差异复查，继续确认默认 MySQL/MariaDB 环境和 SQLite 聚焦测试不会隐藏字段、迁移或行为差异。
