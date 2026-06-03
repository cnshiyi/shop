# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-03 15:02 CST
- 状态：已完成真机测试计划复查；未获得真实云资源成本授权，因此未执行任何真实云操作。
- 最近提交：本轮提交后以 `git log -1` 为准。
- 本轮改动：`docs/real-machine-test-report.md` 新增未授权计划复查记录；`TODO.md` 勾选真机测试计划复查；覆盖更新本文件并追加版本记录。
- 本轮结论：现有真机报告继续保留 2026-06-02 的脱敏真实测试记录，灰色地带续费和人工创建无订单资产仍为待授权执行项；后续必须先获得用户明确授权真实云资源成本，才可执行创建、删除、IP 变更、附加 IP / 固定 IP 变更、续费、生命周期、通知计划或删除计划真机验证。

## 最近验证

- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile cloud/task_center.py cloud/tests_task_center.py core/tests.py` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py shell -c "from django.apps import apps; from cloud.models import CloudAsset, CloudServerOrder, CloudAssetDashboardSnapshot; retired={'accounts','finance','mall','monitoring','dashboard_api','biz'}; installed={c.label for c in apps.get_app_configs()}; print('retired_installed', sorted(retired & installed)); print('CloudAsset expiry fields', [f.name for f in CloudAsset._meta.fields if 'expires' in f.name or 'expiry' in f.name]); print('CloudServerOrder removed expiry fields', [f.name for f in CloudServerOrder._meta.fields if f.name in {'actual_expires_at','service_expires_at'}]); print('CloudAssetDashboardSnapshot expiry fields', [f.name for f in CloudAssetDashboardSnapshot._meta.fields if 'expires' in f.name or 'expiry' in f.name or f.name=='actual_expires_at'])"` 确认废弃 app 未安装、订单到期字段未恢复、资产到期事实仍为 `CloudAsset.actual_expires_at`。
- 红线关键字扫描未发现订单服务到期字段、旧计划快照、旧退款函数名或废弃 runtime app 回流。
- `find . -maxdepth 2 -type d \( -name accounts -o -name finance -o -name mall -o -name monitoring -o -name dashboard_api -o -name biz \) -print` 未发现废弃 runtime app 目录回流。
- `git diff --check` 通过。

## 剩余风险

- 工作树仍存在其它未提交路由、文档和测试路径改动，本轮未回退；提交时只暂存真机计划复查相关文档和必要自动化记录。
- 本轮未执行真机测试，因为没有用户明确授权真实云资源成本。
- 本轮未跑完整测试套件。
- 本轮未执行真实 Telegram 点击、真实云资源、真实支付、链上广播、生产发布或不可逆操作。

## 下一步

- `TODO.md` 当前固定任务已全部勾选；下一轮如无新增明确任务，按 `docs/auto-optimization-control.md` 固定巡检清单做只读巡检，重点继续看机器人返回链、云资产生命周期唯一到期事实、后台任务中心可观测性和红线回流。
