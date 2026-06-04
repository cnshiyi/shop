# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-04 20:29 CST
- 状态：按用户确认继续收紧“所有重装都走重装迁移/重建”的语义，bot 资产重装确认和订单重装确认不再保留“当前机重新安装”兜底路径。
- 本轮范围：`bot/handlers.py` 将资产重装确认限制为必须拿到 `replacement_for_id` 的重建迁移订单，否则直接提示重新进入详情或联系人工；订单确认只允许两类后续动作：正常服务订单的“重建迁移”和未完成订单的“继续初始化”。
- 本轮结论：用户点击“重新安装/重装确认”时不会再走当前服务器原地重跑安装；“继续初始化”只作为 `paid/provisioning/failed` 未完成订单恢复流程存在，按钮文案也明确显示为“确认继续初始化”。

## 最近验证

- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py cloud/services.py cloud/tests.py bot/tests.py` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_create_cloud_server_rebuild_order_reuses_original_static_ip_without_temp cloud.tests.CloudServerServicesTestCase.test_reinit_request_creates_rebuild_order_for_active_server cloud.tests.CloudServerServicesTestCase.test_reinit_request_keeps_unfinished_order_as_resume_init cloud.tests.CloudServerServicesTestCase.test_rebuild_source_migration_schedule_preserves_asset_expiry cloud.tests.CloudServerServicesTestCase.test_rebuild_job_keeps_old_instance_until_migration_due --settings=shop.settings --verbosity=2` 通过，5 个重装/重建迁移测试 OK。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_reinstall_cancel_buttons_keep_back_path bot.tests.RetainedIpRenewalUiTestCase.test_reinstall_confirm_handlers_reuse_saved_back_path_after_submit bot.tests.RetainedIpRenewalUiTestCase.test_cloud_action_handlers_compact_nested_back_callback_before_reuse bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_query_keyboard_includes_reinstall_and_expiry_actions --settings=shop.settings --verbosity=2` 通过，4 个 bot 按钮/返回链/源码约束测试 OK。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `git diff --check` 通过。
- `rg -n "确认重新安装|重新安装大约|准备重新安装|普通重装|不创建新实例|不迁移固定 IP|继续初始化当前服务器|else '重新安装'|已确认重新安装|retry_only=True" bot cloud -g '*.py'` 仅命中测试里的反向断言。

## 剩余风险

- 本轮只改本地代码和测试，没有执行真实云创建、删除、固定 IP 释放、链上转账、真实支付或生产发布。
- SQLite 测试环境继续打印不支持 `db_comment` / `db_table_comment` 的预期 warning。

## 下一步

- 真机 bot 点击测试时，正常服务中的“重新安装”应创建 `SRVREBUILD` 新订单；如果创建失败，应提示无法创建重建迁移订单，不应继续当前机初始化。
