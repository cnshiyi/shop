# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-05 22:29 CST
- 状态：按用户要求补齐代理列表、通知计划、删除计划的列显示开关，并完成浏览器实测和后端聚焦验证。
- 本轮范围：代理列表 IP 视图、列开关、紧凑接口、孤儿资产默认可见；通知计划和删除计划按列开关裁剪重字段；删除计划总开关补齐关机/删机/删 IP；服务器单项显示关机计划，IP 单项显示 IP 删除计划。
- 本轮结论：大量压测数据下，代理列表 IP 视图请求 `compact=1`，首屏仅保留用户、分组、IP/价格、到期/剩余、编辑列；通知计划和删除计划切换重列时请求按 `fields` 增量加载；浏览器控制台已清理到 0 error / 0 warning。

## 最近验证

- 前端：`pnpm --filter @vben/web-antd exec vue-tsc --noEmit --skipLibCheck` 通过。
- 后端：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- 后端：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py bot/api_site_configs.py cloud/api_asset_snapshots.py cloud/api_assets.py cloud/api_tasks.py cloud/tests.py` 通过。
- 后端聚焦测试：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_compact_returns_ip_view_payload cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_all_includes_disabled_or_missing_cloud_account_assets cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_fields_basic_omits_notes_and_execution_payload --settings=shop.settings --verbosity=2` 通过；SQLite comment warnings 为预期差异。
- 检查：`git diff --check` 在后端仓库和前端仓库均通过。
- 浏览器实测：通知计划默认请求 `fields=basic`，打开 IP 列后请求 `fields=basic,ips`，表格实际显示 IP 列且控制台无错误。
- 浏览器实测：删除计划页显示 `关机服务器`、`删除服务器`、`删除IP` 三个总开关；服务器表单项列为 `关机计划`，IP 表单项列为 `IP删除计划`；打开备注列后请求 `fields=basic,notes,execution`，控制台无错误。
- 浏览器实测：代理列表切到 `IP视图` 后请求 `compact=1`，显示列为用户、分组、IP/价格、到期/剩余、编辑；关闭 IP/价格列后表头同步消失；价格显示为 `5 USDT`，未出现长小数。

## 剩余风险

- 本轮未执行真实云资源创建、删除、关机、释放 IP、真实支付、链上广播或生产发布。
- `CloudAsset.shutdown_enabled` 仍是服务器关机计划和未附加 IP 删除计划共用的资产级单项布尔字段；本轮已修正后台文案和列标题，但若未来要完全拆分语义，需要新增独立字段和迁移。
- 前端仓库存在大量本轮无关脏文件，未处理、未回滚。
- 后端仓库存在未跟踪文件 `docs/jisou-bot-functions.md`，本轮未处理。

## 下一步

- 如继续优化大列表性能，可进一步把代理列表普通视图也接入后端按字段裁剪，减少非 IP 视图重字段传输。
