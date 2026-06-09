# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-09 12:59 CST
- 状态：已修复代理列表未绑定用户分组分裂问题。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户要求“未绑定用户在同一分组”。
- 代理列表后端快照分组曾把未绑定资产按资产 ID 生成 `unbound:{asset_id}`，导致每条未绑定资产在按用户分组或无群组兜底分组时拆成单独分组。

## 修复内容

- `cloud/api_asset_snapshots.py`
  - 未绑定用户资产统一使用固定分组键 `user:unbound`。
  - `group_by=user` 时，无 `user_id/tg_user_id` 的资产会合并到同一个“未绑定用户”分组。
  - 默认 `group_by=telegram_group` 且资产没有群组、没有用户时，也兜底合并到同一个“未绑定用户”分组。
- `cloud/migrations/0065_merge_unbound_dashboard_snapshot_user_group.py`
  - 迁移旧快照数据，把无用户资产的 `group_user_key` 统一更新为 `user:unbound`。
  - 对无用户且无群组资产，把 `group_telegram_key` 也统一更新为 `user:unbound`。
- `cloud/tests.py`
  - 新增聚焦测试，验证两条未绑定资产在“按用户分组”和默认“按群组分组但无群组”两种入口下都进入同一个 `user:unbound` 分组。

## 验证

本轮需要通过：

```bash
uv run python -m py_compile cloud/api_asset_snapshots.py cloud/tests.py cloud/migrations/0065_merge_unbound_dashboard_snapshot_user_group.py
uv run python manage.py check
uv run python manage.py migrate --plan
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_compact_merges_unbound_user_group --settings=shop.settings --verbosity=1
git diff --check
```

结果：

- 后端相关文件编译通过。
- Django 系统检查通过。
- 迁移计划包含 `cloud.0065_merge_unbound_dashboard_snapshot_user_group`。
- 未绑定用户合并分组聚焦测试通过。
- diff 空白检查通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。

## 结论

- 代理列表中的未绑定用户资产不会再按资产拆成多个用户分组。
- 历史快照会在迁移后统一到同一个“未绑定用户”分组。
