# Shop 自动优化执行规则

本仓库的自动优化任务以中文记录为准。任何 Codex 会话或 `codex exec "continue to next task"` 启动后，必须先读取：

1. `docs/auto-optimization-control.md`
2. `docs/auto-optimization-latest.md`
3. `docs/refactor-version-record.md` 末尾
4. `TODO.md`

## continue to next task

当收到 `continue to next task` 时：

1. 先运行 `git status --short` 和 `git log -1 --oneline --decorate --stat`，确认当前用户改动和最近提交。
2. 读取上述四个文件，选择 `TODO.md` 中第一个未完成且不违反红线的任务。
3. 只做该任务需要的最小安全修复；如果没有明确可执行任务，按 `docs/auto-optimization-control.md` 的固定巡检清单做一轮只读巡检。
4. 运行 `uv run python manage.py check`，并运行与改动相关的聚焦测试或编译检查。
5. 覆盖更新 `docs/auto-optimization-latest.md`，在 `docs/refactor-version-record.md` 追加中文记录。
6. 如果存在实际仓库变更，提交一个清晰的 git commit。
7. 遇到生产发布、合并、删除数据、密钥暴露、真实支付、链上广播或其他不可逆操作，立即停止并报告。

## 工作边界

- 不恢复废弃 runtime app：`accounts`、`finance`、`mall`、`monitoring`、`dashboard_api`、`biz`。
- 云资产到期事实只使用 `CloudAsset.actual_expires_at`。
- 不恢复订单到期字段、旧计划快照、旧退款逻辑或旧退款函数名。
- 不打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 真机云资源测试必须先获得用户明确授权真实成本，并单独写中文报告，资源 ID 脱敏。
