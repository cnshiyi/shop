# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-09 13:51 CST
- 状态：已把同 IP 去重升级为数据库硬规则，并补齐数据库唯一约束。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户明确指出：数据库不应该有同 IP 两条资产，这是硬规则。
- 上一轮只清理代理列表快照和显示口径，不符合该规则。
- 本轮修正为：同一个有效 `CloudAsset.public_ip` 只能保留一条资产本体，后续写入也由数据库唯一约束阻断。

## 修复内容

- `cloud/asset_dedupe.py`
  - 新增统一资产合并 helper。
  - 同 IP 资产按服务器、未附加、删除中、活跃、有订单、有用户、更新时间、ID 的顺序选择保留资产。
  - 合并时迁移 `CloudIpLog`、`CloudLifecyclePlanNote`、`CloudLifecycleTask`、`CloudNoticeTask`。
  - 删除重复资产对应旧快照，再删除重复 `CloudAsset` 本体。
- `cloud/models.py`
  - `CloudAsset.public_ip` 保存前会去掉首尾空格，空字符串归一为 `NULL`。
  - 新增 `uniq_cloud_asset_public_ip` 唯一约束，允许多条无 IP 资产，但阻断有效 IP 重复。
- `cloud/management/commands/dedupe_cloud_assets.py`
  - 管理命令改为按公网 IP 硬去重，不再按云账号/地区隔离同 IP。
- `cloud/api_asset_snapshots.py`
  - 快照刷新前先执行资产本体同 IP 合并。
  - 增量刷新会同时纳入目标资产当前 IP 和旧快照 IP，清理快照表中同 IP 残留。
- `cloud/migrations/0067_dedupe_cloud_assets_by_public_ip.py`
  - 部署迁移时先把空公网 IP 归一为 `NULL`，并去掉历史公网 IP 首尾空格。
  - 清理现有 `CloudAsset` 同 IP 重复资产。
  - 迁移关联记录后删除重复资产本体。
  - 最后创建 `uniq_cloud_asset_public_ip` 数据库唯一约束。
- `cloud/tests.py`
  - 覆盖跨账号同 IP 写入被数据库阻断。
  - 覆盖空 IP 可多条存在、带空格 IP 会先归一。
  - 覆盖日志、生命周期任务、通知任务外键迁移。
  - 覆盖旧快照 IP 残留清理、计划页唯一 IP 分页和未附加 IP 删除计划。

## 验证

通过：

```bash
uv run python -m py_compile cloud/asset_dedupe.py cloud/asset_queries.py cloud/api_asset_snapshots.py cloud/models.py cloud/management/commands/dedupe_cloud_assets.py cloud/migrations/0067_dedupe_cloud_assets_by_public_ip.py cloud/tests.py
uv run python manage.py check
uv run python manage.py migrate --plan
uv run python manage.py makemigrations --check --dry-run
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_asset_public_ip_is_unique_across_accounts cloud.tests.CloudServerServicesTestCase.test_cloud_asset_blank_public_ip_is_normalized_and_not_unique_blocked cloud.tests.CloudServerServicesTestCase.test_cloud_asset_public_ip_is_stripped_before_unique_check cloud.tests.CloudServerServicesTestCase.test_dedupe_cloud_asset_group_relinks_related_rows cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_keeps_unique_public_ip_asset_visible cloud.tests.CloudServerServicesTestCase.test_cloud_asset_snapshot_incremental_refresh_prunes_stale_same_ip_snapshot cloud.tests.CloudServerServicesTestCase.test_dedupe_cloud_assets_keeps_same_instance_with_different_ips cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_handles_unique_ip_server_assets cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_handles_unique_ip_asset --settings=shop.settings --verbosity=1
git diff --check
```

实际本地数据库测试：

- 测试库：`shop_manual_20260608_5676`
- 注入同 IP `198.51.100.88` 的两条 `CloudAsset`。
- 执行 `merge_duplicate_cloud_assets_by_ip(public_ips=[ip])`。
- 结果：资产本体从 2 条变 1 条，保留 `codex-hard-dedupe-live-new`。
- `CloudIpLog`、`CloudLifecycleTask`、`CloudNoticeTask` 均迁移到保留资产。
- 测试数据已清理，剩余测试资产 0 条。

## 结论

- 同 IP 现在是数据库硬规则，不只是前端或快照隐藏。
- 迁移会清理已有重复资产并创建唯一约束；后续有效 IP 重复写入会被数据库阻断。
