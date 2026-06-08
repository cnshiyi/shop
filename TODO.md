# Shop 自动优化 TODO

本文件用于 `codex exec "continue to next task"` 工人会话领取下一项任务。每项任务必须有可验证输出；完成后勾选并在 `docs/refactor-version-record.md` 追加中文记录。

## 待办

- [ ] 压测数据库隔离改造：后续性能压测、批量造数、深分页压测必须先创建一个全新的独立测试数据库，完成迁移和造数后只在该测试库执行压测；禁止在当前业务库、手工真机测试库或含真实用户数据的库上直接压测。
  - 输出：压测入口或脚本支持指定/创建独立测试库；中文记录数据库名、端口、造数规模、压测命令、结果和清理策略；不打印任何密钥。
  - 验证：`uv run python manage.py check`、压测脚本 dry-run 或小规模测试库实跑、`git diff --check`。

- [x] 全自动优化项目巡检：按 `docs/auto-optimization-control.md` 固定入口自动领取下一项，不做真实支付、链上广播、真实云资源创建/删除、生产发布或删除数据；每轮只做一个最小安全修复，并完成验证、中文记录和 git commit。
  - 输出：修复代码或记录只读巡检结论；更新 `docs/auto-optimization-latest.md`，在 `docs/refactor-version-record.md` 追加中文记录。
  - 验证：`uv run python manage.py check`、相关聚焦测试或编译检查、`git diff --check`。

- [x] 50 万数据深分页性能优化：在不牺牲分页准确性的前提下，把代理列表 IP 视图第 2 页、深页和最后一页从约 5 秒继续降到 2 秒内；必须继续用数据库精确分页对账，并做浏览器实际翻页/跳页测试。
  - 输出：索引/快照字段/游标分页等最小安全优化方案和实现；不得用不精确候选分页导致丢组、串组或排序不一致。
  - 验证：接口 page=1/2/3/10/100/1000/最后一页与数据库精确结果一致；浏览器实际点击第 2 页和最后一页；控制台 0 error / 0 warning。

- [x] 机器人返回链复查：覆盖资产详情、订单详情、续费、钱包支付续费、换 IP、重装、修改配置的返回上一层行为，并确认所有 Telegram `callback_data` 不超过 64 字节。
  - 输出：修复代码或记录无问题；必要时补 `bot.tests` 聚焦测试。
  - 验证：`uv run python manage.py check` 和相关 bot 聚焦测试。

- [x] 云资产生命周期复查：确认 `CloudAsset.actual_expires_at` 仍是唯一资产到期事实，订单表、计划快照和旧退款入口没有回流。
  - 输出：修复代码或记录无问题；必要时补 `cloud` / `orders` 聚焦测试。
  - 验证：`uv run python manage.py check`、相关生命周期测试、字段/关键字扫描。

- [x] 后台任务中心和状态统计复查：检查云资产同步 worker、通知计划、自动续费、生命周期计划的状态统计、失败状态、重试状态和后台总览可观测性。
  - 输出：修复漏报、状态不一致或异常重试问题；必要时补 `cloud.tests_task_center`。
  - 验证：`uv run python manage.py check` 和任务中心聚焦测试。

- [x] 本地数据库差异复查：确认默认 MySQL/MariaDB 环境和 SQLite 聚焦测试不会隐藏字段、迁移或测试行为差异。
  - 输出：记录差异或修复配置/测试问题。
  - 验证：`uv run python manage.py check`、`uv run python manage.py migrate --plan`，必要时运行相关测试。

- [x] 真机测试计划复查：在未获得用户明确授权真实云资源成本前，只维护计划，不执行真实云资源创建、删除、IP 变更、支付或链上广播。
  - 输出：如获授权，单独更新 `docs/real-machine-test-report.md`，云资源 ID 脱敏，不打印密钥。
  - 验证：只读计划检查；禁止未授权真实操作。

## 已完成

- [x] 建立固定复查入口：`docs/auto-optimization-control.md` 和 `docs/auto-optimization-latest.md`。
- [x] 更新 `shop` 自动化提示词：每轮先读取固定入口和版本记录末尾，再执行巡检、修复、验证、记录和提交。
