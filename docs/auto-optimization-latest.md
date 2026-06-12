# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-13 00:55 CST
- 状态：已修复“服务器删除记录显示数量不对”的统计口径。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 目标主分支：`main`

## 本轮背景

- 用户反馈后台生命周期计划页“服务器删除记录”显示数量不对。
- 只读排查确认：真实 AWS 生命周期测试清理后的本地 `CloudAsset` 已标记为 `deleted/is_active=False`，并带有 `测试资源已清理` 或 `真机测试资源已清理` 标记。
- 原服务器删除历史统计会合并：
  - 已删除订单。
  - 无订单、已删除、非未附加 IP 的服务器资产。
- 因此真机测试清理资产会混入用户侧服务器删除记录数量。

## 修改摘要

- `cloud/lifecycle_plan_queries.py`
  - 新增 `real_machine_test_cleanup_asset_q()`，只识别明确带测试清理标记的资产。
  - `server_delete_history_asset_queryset()` 排除这类测试清理资产。
  - 保留正常无订单已删除服务器资产进入服务器删除历史，避免真正的孤儿删机记录丢失。
- `cloud/tests.py`
  - 新增 `test_lifecycle_plans_server_history_excludes_test_cleanup_assets`。
  - 覆盖正常无订单 deleted 服务器仍显示、测试清理 deleted 服务器不计数且不出现在 API 列表。

## 只读对账结果

- 修复后当前库 `server_delete_history_counts()`：
  - `server_history_order_count=1`
  - `server_history_asset_count=2`
  - `server_history_count=3`
- 被排除的测试清理本地记录：6 条。
- 本轮未删除数据库记录，未创建或删除真实云资源。

## 验证命令

通过：

```bash
uv run python -m py_compile cloud/lifecycle_plan_queries.py cloud/tests.py bot/api.py
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_history_excludes_test_cleanup_assets cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_history_includes_orphan_deleted_server_asset cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_history_mixes_orders_and_assets_by_updated_at --keepdb --noinput --verbosity 2
uv run python manage.py check
git diff --check
```

## 风险和下一步

- 本轮只调整服务器删除历史口径，不影响未附加 IP 删除计划、IP 删除历史、真实云同步和执行器。
- 过滤条件只依赖明确清理标记，不按 `codex-` 名称前缀排除，降低误伤业务资产的风险。
- 未执行真实支付、链上广播、生产发布、业务数据删除或真实云资源操作。
