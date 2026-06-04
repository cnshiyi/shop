# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-04 20:00 CST
- 状态：按用户要求专项测试“重装后的旧服务器处理”。现有回归测试通过；真实库事务验证覆盖旧机进入保留期、未到期不删除、到期后迁移旧机删除本地状态转换。
- 本轮提交：`record rebuild old server lifecycle test`；本轮仅更新报告文档，工作树仍含既存未归属模型/迁移改动，未回退。
- 本轮范围：重建/重装新单绑定 `replacement_for` 后，旧订单标记 `deleting`，旧资产标记 `deleting/is_active=False`，迁移清理时间默认 3 天后，删机时间为迁移时间后 3 天，IP 回收为删机后 15 天。
- 本轮结论：旧资产的 `CloudAsset.actual_expires_at` 在进入保留期和迁移旧机删除后都保持不变；未到 `migration_due_at` 时不会进入迁移旧机待删；到期后指定旧单执行器可完成 `migration_delete/done` 并标记旧单、旧资产为 `deleted`。

## 最近验证

- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_create_cloud_server_rebuild_order_reuses_original_static_ip_without_temp cloud.tests.CloudServerServicesTestCase.test_reinit_request_reinstalls_current_server_without_rebuild_order cloud.tests.CloudServerServicesTestCase.test_rebuild_source_migration_schedule_preserves_asset_expiry cloud.tests.CloudServerServicesTestCase.test_rebuild_job_keeps_old_instance_until_migration_due cloud.tests.CloudServerServicesTestCase.test_get_migration_due_orders_is_distinct cloud.tests.CloudServerServicesTestCase.test_get_migration_due_orders_skips_non_deleting_orders cloud.tests.CloudServerServicesTestCase.test_replaced_order_delete_respects_asset_shutdown_switch cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_deletes_migration_due_order_with_deleting_asset cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_migration_delete_uses_migration_due_without_notice_payload cloud.tests.CloudServerServicesTestCase.test_source_migration_schedule_keeps_asset_actual_expiry cloud.tests.CloudServerServicesTestCase.test_aws_sync_deleted_migration_order_keeps_asset_actual_expiry --settings=shop.settings --verbosity=2` 通过，11 个旧机/迁移生命周期相关测试 OK；SQLite 仅打印不支持 table/field comment 的预期 warning。
- 真实 MySQL 事务验证 1：临时旧单进入重装保留期后，旧订单为 `deleting`，旧资产为 `deleting/is_active=False`，资产到期事实保持不变；`migration_due_at` 约 3 天后，`delete_at` 比迁移时间晚 3 天，`ip_recycle_at` 比删机时间晚 15 天；未到期执行迁移旧机删除返回“清理时间未到”；事务回滚后临时订单和资产数量为 0。
- 真实 MySQL 事务验证 2：将临时旧单 `migration_due_at` 调到过去后，只调用指定订单的迁移旧机删除执行器，并用本地替身阻断云 API；结果 `ok=True`，旧订单和旧资产均标记 `deleted`，旧资产公网 IP 清空，资产到期事实保持不变，生成 `migration_delete/done`；事务回滚后临时订单和资产数量为 0。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- 敏感信息处理：报告不记录完整公网 IP、代理链接、secret、登录密码、Telegram token、session 或云账号密钥。

## 真实库注意事项

- 本轮曾尝试用全局 `lifecycle_tick` 覆盖到期链路，真实库中一个既有普通删机候选被扫描并执行为 `deleted`，关联资产也为 `deleted/is_active=False`，生命周期任务为 `delete/done`。该资源属于既有替换链订单，不是临时事务数据；公网 IP、实例名和凭据不在报告中记录。
- 发现全局 `lifecycle_tick` 会同时处理真实库所有到期候选；后续专项验证已改用指定订单执行器，避免误处理无关候选。

## 剩余风险

- 到期后成功删除链路的真实库事务验证使用本地替身阻断云 API，只验证本地生命周期状态转换；除上述既有候选被全局扫描处理外，没有再触发其它真实云删除、固定 IP 释放、链上转账或生产发布。
- 未执行链上真实充值到账，因为本轮没有外部钱包向收款地址发起真实链上转账。
- 工作树仍含既存未归属模型/迁移改动，未纳入本轮报告提交。

## 下一步

- 如继续做生命周期专项，优先使用指定订单/资产执行器或只读计划刷新，不再直接跑全局 `lifecycle_tick`，除非明确要处理真实库全部到期候选。
