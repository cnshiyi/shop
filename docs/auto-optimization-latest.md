# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-13 13:15 CST
- 状态：已严查并补测试确认非管理员旧机保留 IP 查询只显示续费入口，不显示 `修改时间` / `exp:*`；管理员视角仍可修改时间。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 目标主分支：`main`

## 本轮背景

- 用户指出：旧机保留状态可以续费，但非管理员不能出现 `修改时间`。
- 本轮只做权限严查和聚焦回归测试，不执行真实云资源创建、删除、支付或链上广播。

## 修改摘要

- `bot/handlers.py`
  - 严查结果：IP 查询页 `修改时间` 按钮只在 `include_start=True` 时生成；`include_start` 由 `_is_admin_chat()` 决定。
  - 严查结果：翻页回调重新按当前消息是否管理员渲染，不复用用户态状态放大权限。
  - 严查结果：`exp:*` / `cloud:adminexp:*` 回调入口和后续输入消息均二次校验管理员，手工构造 callback 也不能绕过。
- `bot/tests.py`
  - 新增非管理员旧机保留查询测试：显示 `cloud:renew:<order_id>`，不显示 `修改时间`、`exp:*`、`cloud:start:*` 或自动续费按钮。
  - 新增键盘层测试：`include_start=False` 时即使有可续费订单，也不会生成 `修改时间` 或 `exp:*`。
- `docs/real-machine-test-report.md`
  - 修正上一轮真机报告表述，明确“旧机保留显示修改时间”只属于管理员查询视角。

## 验证命令

已通过：

```bash
uv run python manage.py test bot.tests.BotOrderAndBalanceFilterTestCase.test_user_ip_query_old_retained_instance_keeps_renew_but_hides_admin_expiry bot.tests.BotOrderAndBalanceFilterTestCase.test_user_query_keyboard_hides_admin_expiry_action bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_ip_query_old_retained_instance_keeps_renew_but_hides_runtime_actions --keepdb --noinput --verbosity 2
uv run python -m py_compile bot/handlers.py bot/tests.py
uv run python manage.py check
git diff --check
```

## 风险和下一步

- 本轮未执行真实 AWS 操作、真实支付、链上广播、钱包扣款或地址支付。
- `CloudAsset.actual_expires_at` 仍是资产到期事实；订单 `ip_recycle_at` 只用于关联订单是否仍处在保留期的按钮准入判断。
- 本轮只补测试和文档口径；代码严查未发现非管理员可见 `修改时间` 的路径。
