# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-10 17:15 CST
- 状态：已完成未附加 IP 续费真库实测、残留旧实例 ID 的计划归类修复、IP 删除计划/历史去重修复、AWS 真机创建阻塞确认。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：未改动
- 目标主分支：`main`

## 本轮背景

- 用户要求“跑真机测试，不要猜”。
- 重点问题：未附加 IP 续费后，IP 删除计划和 IP 删除历史不能混淆；没有删除的未附加 IP 不能被当成已删除记录，也不能继续进入错误计划。

## 修复内容

- `cloud/lifecycle_plan_queries.py`
  - IP 删除计划计数缓存升到 `v2`，避免旧计数缓存污染页面。
  - 未附加 IP 已绑定待支付/已支付/开通中/待续费的“未绑定代理资产续费”订单后，不再进入 IP 删除计划。
  - 服务器生命周期计划不再只排除“空实例 ID + 未附加 IP”，而是只要识别为未附加/保留固定 IP，就禁止进入关机计划和删机计划。
  - 未附加 IP 删除计划不再要求 `instance_id` 为空；残留旧实例 ID 的未附加固定 IP 仍进入 IP 删除计划和 due 队列。
  - IP 删除历史日志排除仍处于活跃 IP 删除计划的资产，避免旧 `CloudIpLog(deleted)` 让待释放 IP 同时出现在计划和历史。
- `cloud/services.py`
  - 保留中的未附加固定 IP 不再被 `deleted` 状态误判为不可续费。
  - 未附加 IP 输入旧代理链接生成待支付续费订单后，刷新计划页快照。
- `cloud/tests.py`
  - 增加未附加 IP 保留状态可发起续费恢复测试。
  - 增加已有续费恢复订单时排除 IP 删除计划测试。
  - 增加“未附加 IP 残留旧实例 ID”不能进入关机/删机/IP 删除计划测试。
  - 增加“未附加 IP 残留旧实例 ID 且有旧删除日志”仍只进入 IP 删除计划、不进入 IP 删除历史测试。

## 真库实测

- 默认本地库：`shop_manual_20260608_5676`
- 前端：`http://127.0.0.1:5666`
- 后端：`http://127.0.0.1:8000`
- 测试资产：`CloudAsset #556`，公网 IP `18.138.xxx.xxx`，固定 IP 名 `StaticIp-707`。
- 续费前：后端查询确认该 IP 在 IP 删除计划内、IP 删除历史外。
- 续费下单：生成待支付订单 `SRVASSET...RENEW556`。
- 修复前页面实测：该 IP 从 IP 删除计划查询层排除，但因为资产残留旧 `instance_id`，仍错误出现在“关机计划”表。
- 清理测试订单后再次页面实测：计划页和浏览器内 API 确认目标资产不在关机/删机计划，重新进入 IP 删除计划，不进入 IP 删除历史。
- 截图：`output/playwright/real-unattached-renewal-plan-page-fixed.png`
- 补充截图：`output/playwright/real-unattached-ip-stale-instance-fixed.png`
- 清理：已删除本轮测试订单、测试本地资产和测试日志，资产 #556 恢复为无订单、无用户、无测试 secret。

## AWS 真机创建阻塞

- 使用真实开通入口创建测试订单 `REALTEST...`，系统按轮询尝试 4 个启用 AWS 账号。
- 4 个账号在创建前真实配额检查和 `GetStaticIp` 只读校验中均返回 `UnrecognizedClientException`。
- 结论：当前后台 AWS 凭据无效/过期，本轮无法完成真实云端创建、删除、查询闭环；没有产生云资源成本。

## 验证

通过：

```bash
uv run python -m py_compile cloud/lifecycle_plan_queries.py cloud/services.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_exclude_unattached_ip_with_stale_instance_after_recovery_order cloud.tests.CloudServerServicesTestCase.test_retained_unattached_deleted_status_asset_can_start_recovery_renewal cloud.tests.CloudServerServicesTestCase.test_unattached_ip_active_recovery_order_is_excluded_from_delete_plan cloud.tests.CloudServerServicesTestCase.test_unattached_ip_renewal_lists_recovery_plans_without_creating_order cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_retained_ip_after_server_delete_stays_in_ip_delete_plan --settings=shop.settings --verbosity=1
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_stale_instance_unattached_ip_stays_in_ip_delete_plan cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_exclude_unattached_ip_with_stale_instance_after_recovery_order cloud.tests.CloudServerServicesTestCase.test_unattached_ip_active_recovery_order_is_excluded_from_delete_plan cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_retained_ip_after_server_delete_stays_in_ip_delete_plan cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_skip_assets_attached_to_instance --settings=shop.settings --verbosity=1
```

## 风险和下一步

- 当前 AWS 云账号凭据无效，真实创建/删除服务器无法继续，需要在后台更新有效 AWS Access Key/Secret 后再跑云端闭环。
- 真实库仍存在同公网 IP 多行历史资产，后续应单独做去重治理和同步链路收敛，避免前端和计划表再次出现重复资产口径分叉。
