# Shop 自动优化 TODO

本文件用于 `codex exec "continue to next task"` 工人会话领取下一项任务。每项任务必须有可验证输出；完成后勾选并在 `docs/refactor-version-record.md` 追加中文记录。

## 待办

- [x] 机器人返回链复查：覆盖资产详情、订单详情、续费、钱包支付续费、换 IP、重装、修改配置的返回上一层行为，并确认所有 Telegram `callback_data` 不超过 64 字节。
  - 输出：修复代码或记录无问题；必要时补 `bot.tests` 聚焦测试。
  - 验证：`uv run python manage.py check` 和相关 bot 聚焦测试。

- [x] 云资产生命周期复查：确认 `CloudAsset.actual_expires_at` 仍是唯一资产到期事实，订单表、计划快照和旧退款入口没有回流。
  - 输出：修复代码或记录无问题；必要时补 `cloud` / `orders` 聚焦测试。
  - 验证：`uv run python manage.py check`、相关生命周期测试、字段/关键字扫描。

- [ ] 后台任务中心和状态统计复查：检查云资产同步 worker、通知计划、自动续费、生命周期计划的状态统计、失败状态、重试状态和后台总览可观测性。
  - 输出：修复漏报、状态不一致或异常重试问题；必要时补 `cloud.tests_task_center`。
  - 验证：`uv run python manage.py check` 和任务中心聚焦测试。

- [ ] 本地数据库差异复查：确认默认 MySQL/MariaDB 环境和 SQLite 聚焦测试不会隐藏字段、迁移或测试行为差异。
  - 输出：记录差异或修复配置/测试问题。
  - 验证：`uv run python manage.py check`、`uv run python manage.py migrate --plan`，必要时运行相关测试。

- [ ] 真机测试计划复查：在未获得用户明确授权真实云资源成本前，只维护计划，不执行真实云资源创建、删除、IP 变更、支付或链上广播。
  - 输出：如获授权，单独更新 `docs/real-machine-test-report.md`，云资源 ID 脱敏，不打印密钥。
  - 验证：只读计划检查；禁止未授权真实操作。

## 已完成

- [x] 建立固定复查入口：`docs/auto-optimization-control.md` 和 `docs/auto-optimization-latest.md`。
- [x] 更新 `shop` 自动化提示词：每轮先读取固定入口和版本记录末尾，再执行巡检、修复、验证、记录和提交。
