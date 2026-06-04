# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-04 20:22 CST
- 状态：按用户确认修正重装语义：普通“当前服务器重装”逻辑已弃用；正常服务中的重装现在走 AWS Lightsail 重建迁移，未完成订单仍保留“继续初始化”。
- 本轮范围：`cloud.services.mark_cloud_server_reinit_requested` 不再对正常订单返回原订单重跑安装，而是创建 `SRVREBUILD` 替换订单；bot 详情和续费后详情只对 AWS 正常订单展示重装入口；bot 确认文案改为“重建迁移”；资产重装确认按返回订单判断是否重建迁移，不再固定 `retry_only=True`。
- 本轮结论：三条高风险操作语义已收口：重装=重建迁移，换 IP=同配置新机新 IP，修改配置=目标规格新机并迁移固定 IP；只有 `paid/provisioning/failed` 未完成订单继续使用当前订单初始化恢复。

## 最近验证

- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/handlers.py cloud/services.py cloud/tests.py bot/tests.py` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_create_cloud_server_rebuild_order_reuses_original_static_ip_without_temp cloud.tests.CloudServerServicesTestCase.test_reinit_request_creates_rebuild_order_for_active_server cloud.tests.CloudServerServicesTestCase.test_reinit_request_keeps_unfinished_order_as_resume_init cloud.tests.CloudServerServicesTestCase.test_rebuild_source_migration_schedule_preserves_asset_expiry cloud.tests.CloudServerServicesTestCase.test_rebuild_job_keeps_old_instance_until_migration_due --settings=shop.settings --verbosity=2` 通过，5 个重装/重建迁移测试 OK。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_reinstall_confirm_handlers_reuse_saved_back_path_after_submit bot.tests.RetainedIpRenewalUiTestCase.test_cloud_action_handlers_compact_nested_back_callback_before_reuse bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_query_keyboard_includes_reinstall_and_expiry_actions --settings=shop.settings --verbosity=2` 通过，3 个 bot 返回链/按钮测试 OK。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `git diff --check` 通过。

## 剩余风险

- 本轮只改本地代码和测试，没有执行真实云创建、删除、固定 IP 释放、链上转账或生产发布。
- SQLite 测试环境继续打印不支持 `db_comment` / `db_table_comment` 的预期 warning。

## 下一步

- 如果继续真机点击测试，重装按钮应表现为“重建迁移”：创建 `SRVREBUILD` 新订单，新机成功后迁移固定 IP，旧机保留 3 天后进入删除流程。
