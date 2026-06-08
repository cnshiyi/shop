# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 15:16 CST
- 状态：完成一轮生命周期计划/任务中心专项审计；未发现新的最小安全修复点，本轮无业务代码改动。
- 本轮范围：
  - `cloud/task_center.py` 生命周期计划聚合逻辑。
  - `bot/api.py` 生命周期计划总开关/单项开关状态映射。
  - 机器人后台钱包任务并发隔离回归。

## 审计结论

- 生命周期计划页现有回归覆盖仍能通过：
  - 关机计划与删机计划拆分展示。
  - 资产级 `shutdown_enabled` / `server_delete_enabled` / `ip_delete_enabled` 三个单项开关分别生效。
  - `cloud_server_shutdown_enabled` / `cloud_server_delete_enabled` / `cloud_ip_delete_enabled` 三个总开关状态仍能正确映射到计划项。
- 任务中心生命周期聚合现有回归仍能通过：
  - 失败历史、DB 执行任务、计划项之间的去重逻辑未回退。
  - `failed` / `active` / `warning` 聚合结果与现有测试契约一致。
- 机器人后台并发回归仍通过：
  - 通知复制包装器隔离正常。
  - 钱包直付、钱包补付、续费后巡检的并发任务未出现串线。

## 压测/对账结果

- 本轮完成的真实可验证测试为 SQLite 聚焦回归，覆盖：
  - 生命周期计划/任务中心 `17` 条回归用例。
  - 机器人高并发 `3` 条回归用例。
  - 机器人批量并发样本仍为 `20` 组直付 + `20` 组补付 + `20` 组续费后巡检，总计 `60` 路并发任务。
- 计划中的本地 MySQL 真实库对账与 `10` 万量级以上分页/计数审计在当前沙箱环境被阻断：
  - `uv run python manage.py shell -c "..."` 连接 `127.0.0.1` MySQL 时返回 `PermissionError: [Errno 1] Operation not permitted`。
  - 因此本轮未能在当前环境复用既有 `50` 万/`150` 万真实数据做只读对账。

## 后端验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center.CloudTaskCenterApiTestCase cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_global_stage_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_split_shutdown_before_server_delete --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated --settings=shop.settings --verbosity=1
git diff --check
```

受限：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py shell -c "from cloud.models import CloudAsset, CloudIpLog; from cloud.lifecycle_plan_queries import server_lifecycle_plan_counts; import json; counts=server_lifecycle_plan_counts(); payload={'cloud_asset_total': CloudAsset.objects.count(), 'server_asset_total': CloudAsset.objects.filter(kind='server').count(), 'unattached_ip_history_logs': CloudIpLog.objects.filter(action='release_unattached_ip').count(), 'plan_counts': counts}; print(json.dumps(payload, ensure_ascii=False))"
```

失败原因为当前沙箱禁止访问本机 `127.0.0.1` MySQL。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、支付密钥、云厂商密钥或完整代理链接到仓库文件。
- 本轮未重新打开前端页面；前端仓库 `git status --short` 为空，但浏览器实页与真实 MySQL 大表对账受当前沙箱网络限制影响，未在本轮复跑。

## 下一步

- 下一轮优先在可访问本地 MySQL 的环境中复跑生命周期计划真实库只读对账，补齐 `10` 万量级以上分页/计数验证。
- 如环境仍受限，则继续只做固定巡检清单中的 SQLite 聚焦回归，并优先寻找新的最小安全缺陷而不是重复无效大表命令。
