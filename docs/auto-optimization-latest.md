# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 22:28 CST
- 状态：完成此前授权创建的 AWS Lightsail 测试服务器清理；云端确认不存在，本地新测试库资产和订单已收敛为已删除。
- 后端提交：本轮记录待提交。
- 前端提交：本轮无前端变更。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 新测试库：`shop_manual_20260608_5676`
- 测试入口：后端 `127.0.0.1:8010`，前端 `127.0.0.1:5676`
- 真实云资源：此前用户授权创建的 1 台 AWS Lightsail 测试服务器
- 本地资产：`CloudAsset #4`
- 关联订单：`CloudServerOrder #1`

## 本轮动作

- 按用户要求删除此前由 Codex 创建的测试服务器。
- 使用 AWS Lightsail `delete_instance` 删除目标实例。
- 轮询 `get_instance`，确认云端已返回 `not_found`。
- 运行 AWS 同步检查；同步器因缺失删除保护阈值为 5 次、确认间隔 60 分钟，短时间内只进入“待确认”状态。
- 在云端已确认不存在后，对新测试库调用同步器内部同一“云端缺失后标记删除”收敛逻辑。
- 将 `CloudAsset #4` 和 `CloudServerOrder #1` 收敛为 `deleted`。
- 追加更新 `docs/real-machine-test-report.md`，资源 ID 和公网 IP 保持脱敏。

## 最终状态

- AWS 云端：目标测试实例 `not_found`。
- 资产：`CloudAsset #4` 为 `deleted/is_active=False`。
- 订单：`CloudServerOrder #1` 为 `deleted`。
- 当前公网 IP：资产和订单均已清空，仅保留历史 IP。
- 无订单服务器待删队列：不包含目标资产。
- 生命周期 due 队列：目标订单不在 `expire`、`suspend`、`delete`、`recycle` 任一执行队列中。

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop MYSQL_HOST=127.0.0.1 MYSQL_PORT=3307 MYSQL_DATABASE=shop_manual_20260608_5676 uv run python manage.py check
git diff --check
```

补充复核：

- AWS Lightsail `get_instance` 确认目标实例不存在。
- `_get_orphan_asset_delete_due()` 不包含 `CloudAsset #4`。
- `_get_due_orders()` 的 `expire/suspend/delete/recycle` 均不包含 `CloudServerOrder #1`。

## 安全边界

- 本轮真实云操作仅限用户要求删除的测试实例。
- 未执行真实支付、链上广播、生产发布或业务数据删除。
- 未打印完整实例名、完整公网 IP、登录密码、代理链接、代理 secret、Telegram token、session 或云账号密钥。

## 剩余风险

- 该测试实例已删除，未发现云端实例残留。
- 本轮没有释放固定 IP，因为该测试实例创建时使用 `skip_static_ip=True`，没有为它申请固定 IP。
- 继续按当前会话的 24 小时连续巡检要求，下一轮进入安全巡检或最小修复任务。
