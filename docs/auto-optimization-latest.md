# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-10 01:40 CST
- 状态：已重构自动续费开启规则，支持无订单资产按资产事实开启自动续费。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户要求自动续费不再看是否有订单，只要资产有价格、有到期时间就能开启。
- 当前资产事实仍以 `CloudAsset.actual_expires_at` 为准，不恢复订单侧到期字段。
- 无订单人工资产此前无法在后台和机器人自动续费入口完整开启；机器人自动续费列表还会把资产 ID 当订单 ID 使用。

## 修复内容

- `cloud/services.py`
  - 新增资产级自动续费判定：必须绑定用户、有 `actual_expires_at`、有公网 IP、有价格，且不能是未附加固定 IP、删除或失联资产。
  - 新增资产级设置入口：无订单资产开启自动续费时自动创建操作订单，再开启订单钱包自动续费；关闭无订单资产不会反向创建订单。
  - 一键开启/关闭和群组批量开关改为从 `CloudAsset` 扫描，支持无订单但满足条件的资产。
  - 自动续费列表只展示满足开启条件的资产，或已经开启过、需要允许关闭的资产。
- `cloud/api_assets.py`
  - 代理列表 `can_auto_renew` 改为复用资产级规则。
- `cloud/api_asset_edit.py`
  - 后台代理列表开关自动续费改为调用资产级设置入口，不再要求先有订单。
- `bot/keyboards.py`
  - IP 查询结果新增无订单资产自动续费按钮。
  - 自动续费列表 callback 增加来源类型：`o` 表示订单，`a` 表示资产，避免把资产 ID 当订单 ID。
  - 键盘日志上下文改为 `item_ids`，不再误写 `order_ids`。
- `bot/handlers.py`
  - IP 查询页按“价格 + 到期时间”显示自动续费状态。
  - 新增资产级自动续费 callback，群聊仍校验资产归属。
  - 自动续费列表单项开关同时支持订单和资产来源。
- `cloud/tests.py`
  - 覆盖无订单有价格资产可开启自动续费，并断言创建操作订单且不制造同 IP 重复资产。
  - 覆盖缺价格资产不能开启自动续费。
  - 覆盖自动续费列表包含无订单有价格资产、排除缺价格资产，并生成资产级 callback。

## 验证

通过：

```bash
uv run python -m py_compile cloud/services.py cloud/api_assets.py cloud/api_asset_edit.py bot/keyboards.py bot/handlers.py cloud/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_toggle_auto_renew_creates_operation_order_for_priced_asset_without_order cloud.tests.CloudServerServicesTestCase.test_toggle_auto_renew_rejects_asset_without_price cloud.tests.CloudServerServicesTestCase.test_auto_renew_list_includes_priced_asset_without_order cloud.tests.CloudServerServicesTestCase.test_group_auto_renew_bulk_toggle_is_scoped_to_current_group cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_skips_when_asset_expiry_moved_out_of_due_window cloud.tests.CloudServerServicesTestCase.test_auto_renew_retry_task_waits_for_recharge_then_retries --settings=shop.settings --verbosity=1
uv run python manage.py check
git diff --check
```

结果：

- 编译通过。
- 自动续费聚焦测试 6 条通过。
- Django 系统检查通过。
- diff 空白检查通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 告警，不属于本轮问题。

## 结论

- 自动续费开启口径已改为资产事实：有用户、有价格、有到期时间即可开启，不再要求资产预先存在订单。
- 无订单资产开启自动续费时会生成操作订单；后续自动续费仍沿用现有订单扣款和执行链路。
- 未附加固定 IP 不进入自动续费开启规则，仍走恢复续费链路。
