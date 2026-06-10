# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-10 22:05 CST
- 状态：已修复后台人工编辑无订单资产到期时间时，审计订单补资产撞公网 IP 唯一约束的问题。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：未改动
- 目标主分支：`main`

## 本轮背景

- 用户贴出生产日志：后台编辑资产 `asset_id=360` / `asset_id=5` 时，生成手工操作订单后尝试新增同公网 IP 的 `CloudAsset`，触发 `uniq_cloud_asset_public_ip` 唯一约束。
- 根因是人工编辑审计订单本来不应改写资产原订单绑定，却仍调用 `_ensure_order_asset_expiry_record()` 尝试给审计订单创建一条同 IP 资产记录。

## 修改内容

- `cloud/services.py`
  - `_create_manual_asset_operation_order()` 不再为人工编辑审计订单调用 `_ensure_order_asset_expiry_record()`。
  - 保留审计订单和 `CloudIpLog` 记录，但不创建重复公网 IP 资产，也不重绑原资产订单。
- `cloud/tests.py`
  - 新增回归测试：无订单资产带公网 IP 时，人工编辑到期时间会生成审计订单和日志，但 `CloudAsset` 表仍只有一条该公网 IP 资产，且原资产 `order_id` 保持为空。

## 验证

通过：

```bash
git diff --check
uv run python -m py_compile cloud/services.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_manual_expiry_operation_order_does_not_duplicate_unbound_asset_ip --keepdb --noinput --verbosity 1
```

## 风险和下一步

- 本轮只调整人工编辑审计订单补资产路径，不影响正常购买、续费、重装、换 IP、修改配置订单的资产创建逻辑。
- 线上再次编辑同类无订单资产到期时间时，不应再出现 `Duplicate entry ... uniq_cloud_asset_public_ip`。
