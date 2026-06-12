# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-12 22:25 CST
- 状态：已完成 AWS 固定 IP 生命周期真机测试，并修复同步纠正删除记录的缺口。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 目标主分支：`main`

## 本轮背景

- 用户明确授权真实云资源成本，允许使用 AWS Lightsail 最小规格创建、绑定、解绑、释放固定 IP 和删除测试服务器。
- 用户要求实测计划表、未附加 IP 删除记录、附加/未附加/已删除状态变化、附加 IP 不会直接删除、未附加 IP 不会混入删除记录，以及同步链路能否纠正已经混入删除记录的 IP 和服务器。
- 用户追加指出：服务器误标 `deleted` 后只按 IP 同步也应该找回；并补充“IP 是附加状态，但混入了删除记录”的场景。

## 修改摘要

- `cloud/management/commands/sync_aws_assets.py`
  - AWS 同步按公网 IP 查找资产时，如果当前 `public_ip` 找不到，会继续用 `previous_public_ip` 匹配同账号同地区已误标 `deleted/terminated` 的资产。
  - 固定 IP 从未附加重新绑定实例后，会清理旧的“未附加/已删除/已释放”备注，避免计划页继续把它识别为未附加 IP。
- `cloud/lifecycle_plan_queries.py`
  - IP 删除历史日志排除已经被同步纠回为活跃服务器的资产，防止 attached IP 的旧回收日志继续混入删除历史。
- `cloud/tests.py`
  - 新增三条回归测试覆盖：
    - 服务器误标 deleted 且 `public_ip` 清空后，仅按公网 IP 同步可通过 `previous_public_ip` 恢复。
    - 未附加固定 IP 重新 attached 后移出未附加 IP 删除计划。
    - 云端固定 IP 仍 attached 但本地混入删除记录后，同步恢复为服务器，并从 IP 删除计划/历史排除。
- `docs/real-machine-test-report.md`
  - 追加中文真机测试报告，资源名/IP 已脱敏。

## 真机测试结果

- 使用 AWS Lightsail `ap-southeast-1`、`nano_3_0`、`debian_12` 创建独立测试实例和固定 IP。
- 已验证：
  - 附加 IP 不能被未附加 IP 删除执行器直接删除。
  - 服务器误标 deleted 后，仅按公网 IP 同步能恢复为服务器。
  - 解绑后同步为未附加 IP，并重算 `CloudAsset.actual_expires_at`。
  - 云端仍存在的未附加 IP，即使有回收日志，也不会混入 IP 删除历史。
  - 未附加 IP 误标 deleted 后，云端仍存在时同步能恢复到未附加 IP 删除计划。
  - 未附加 IP 真实释放后进入删除历史，再同步不会错误恢复。
- 清理复核：
  - 测试实例不存在。
  - 测试固定 IP 不存在。
  - 未打印或记录完整云资源 ID、完整公网 IP、密钥、密码或代理 secret。

## 验证命令

通过：

```bash
uv run python -m py_compile cloud/management/commands/sync_aws_assets.py cloud/lifecycle_plan_queries.py cloud/tests.py
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_public_ip_restores_deleted_server_from_previous_ip cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_reattached_static_ip_leaves_unattached_delete_plan cloud.tests.CloudServerServicesTestCase.test_sync_aws_assets_attached_static_ip_mixed_delete_record_restores_server --keepdb --noinput --verbosity 2
uv run python manage.py check
```

## 风险和下一步

- 本轮已执行真实 AWS 资源操作，操作前已获得用户明确授权；测试完成后云端测试实例和固定 IP 已清理。
- 本轮没有执行真实支付、链上广播、生产发布或业务数据删除。
- 未附加 IP 重绑为服务器但没有订单/人工到期时间时，会按既有设计显示为“待人工添加时间”，不会进入服务器生命周期计划；需要人工补真实到期时间后才进入服务器计划。
