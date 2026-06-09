# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-09 12:43 CST
- 状态：已修复多云账号购买创建轮询，并补齐机器人购买到创建服务器全链路日志。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户反馈线上配置多个云账号后，机器人购买服务器无法创建。
- 用户要求先修好云账号轮询，并给机器人购买全流程到创建服务器增加详细日志。
- 用户随后指出代理列表“按用户分组”入口不见了，需要恢复前端可见入口。

## 修复内容

- `core/cloud_accounts.py`
  - 云账号 `region_hint` 支持多个地区，兼容逗号、中文逗号、分号、竖线和空白分隔。
  - 账号负载排序除了已存在服务器资产，也纳入 `paid/provisioning` 待创建订单，避免连续购买总落到同一账号。
- `cloud/services.py`
  - 单台创建前重新按当前负载选择云账号。
  - 批量拆单按候选账号轮询分配子订单，不再让整批订单继承同一个账号。
  - 钱包下单、补付、拆单阶段输出 `BOT_CLOUD_PURCHASE_FLOW` 日志，包含用户、订单、地区、套餐、数量、账号和金额。
- `cloud/provisioning.py`
  - 开通前记录候选账号列表。
  - 每次账号尝试、创建结果、失败切换、无可执行账号都输出统一字段日志。
  - 订单已绑定但当前账号不在启用候选列表时，不再强行把旧账号放进轮询。
  - `_set_order_cloud_account` 遇到停用或不存在账号会跳过，不再把订单退回 provider 默认标签继续创建。
- `orders/payment_scanner.py`
  - 链上支付确认进入创建流程时写入统一购买链路日志。
- `bot/handlers.py`
  - 机器人地址支付建单、钱包直付、钱包补付、任务提交均补统一链路日志。
- `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src/views/dashboard/cloud-assets/index.vue`
  - 代理列表顶部恢复显式“显示方式”下拉：`按用户分组`、`按群组分组`、`不分组`。
  - 移除原来没有文字说明的分组 Switch，避免“按用户分组”入口被隐藏。

## 验证

通过：

```bash
uv run python -m py_compile core/cloud_accounts.py cloud/services.py cloud/provisioning.py orders/payment_scanner.py bot/handlers.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_account_region_hint_accepts_multiple_regions cloud.tests.CloudServerServicesTestCase.test_prepare_cloud_server_order_instances_rotates_cloud_accounts cloud.tests.CloudServerServicesTestCase.test_provision_rotates_to_next_cloud_account_after_create_failure --settings=shop.settings --verbosity=1
pnpm --filter @vben/web-antd typecheck
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

结果：

- Django 系统检查通过。
- 后端相关文件编译通过。
- 新增三条聚焦测试通过：
  - 多地区 `region_hint` 不会错误过滤账号。
  - 批量拆单按账号轮询分配。
  - 第一个账号创建失败后会切换到第二个账号并完成创建。
- 前端类型检查通过。
- diff 空白检查通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。

## 浏览器检查

- 已打开 `http://127.0.0.1:5666/admin/cloud-assets`。
- Playwright 自动浏览器会话被重定向到登录页：`/auth/login?redirect=%252Fadmin%252Fcloud-assets`。
- 因没有该自动浏览器会话的后台登录态，未伪造页面内分组加载实测；代码层面已恢复显式入口，前端 `vue-tsc` 已通过。

## 结论

- 多云账号购买创建现在会按可用账号候选列表轮询，失败会切换下一个账号。
- 线上排查可按 `BOT_CLOUD_PURCHASE_FLOW` grep 一条订单的购买、扣款、拆单、候选账号、账号尝试、创建结果和切换过程。
- 代理列表“按用户分组”入口已恢复为顶部显式下拉。
