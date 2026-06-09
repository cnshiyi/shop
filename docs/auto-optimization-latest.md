# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-09 14:23 CST
- 状态：已复查并修复云资产同步链路的同 IP 写入风险。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户要求检查同步链路是否还会往数据库塞同 IP 多条资产。
- 现有 `CloudAsset.public_ip` 已有数据库唯一约束，成功落库两条有效同 IP 资产会被数据库阻断。
- 但 AWS/阿里云同步 resolver 原来主要按云账号、地区、实例名和 IP 范围查询；跨账号或跨地区存在旧同 IP 资产时，resolver 可能返回空，然后同步分支尝试 `CloudAsset.objects.create()`，最终撞唯一约束导致同步任务失败。

## 修复内容

- `cloud/management/commands/sync_aws_assets.py`
  - 新增 `_resolve_global_public_ip_asset()`。
  - `_resolve_asset()` 在账号/地区范围内未命中当前公网 IP 后，按 `CloudAsset.public_ip` 全局兜底复用已有资产。
  - `_resolve_asset_for_static_ip()` 同样增加当前公网 IP 全局兜底，覆盖未附加固定 IP 同步入口。
- `cloud/management/commands/sync_aliyun_assets.py`
  - 新增 `_resolve_global_public_ip_asset()`。
  - `_resolve_asset()` 在账号/地区范围内未命中当前公网 IP 后，按当前公网 IP 全局兜底复用已有资产。
- `cloud/tests.py`
  - 新增跨账号/跨地区同 IP resolver 测试，确保 AWS 实例同步、AWS 未附加固定 IP 同步、阿里云同步都会复用已有同 IP 资产，不走新增分支。
  - 清理旧 resolver 测试中违反当前数据库硬规则的同 IP 双资产造数。

## 验证

通过：

```bash
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/management/commands/sync_aliyun_assets.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_aws_sync_resolution_does_not_match_cross_region_same_instance_without_ip cloud.tests.CloudServerServicesTestCase.test_aliyun_sync_resolution_does_not_match_cross_region_same_instance_without_ip cloud.tests.CloudServerServicesTestCase.test_cloud_sync_resolvers_reuse_global_current_public_ip cloud.tests.CloudServerServicesTestCase.test_cloud_sync_resolvers_keep_ip_primary_when_instance_changes cloud.tests.CloudServerServicesTestCase.test_cloud_sync_resolvers_prefer_current_ip_over_stale_previous_ip --settings=shop.settings --verbosity=1
git diff --check
```

## 结论

- 数据库层已经不会允许同一个有效公网 IP 落两条 `CloudAsset`。
- 同步链路现在也会优先复用全局同 IP 资产，避免先尝试新增再被数据库唯一约束打爆。
- `previous_public_ip` 仍只作为查找辅助，不参与唯一约束；硬规则对象是当前 `public_ip`。
