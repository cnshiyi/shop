# 修复完成复扫报告 2026-05-18

完成时间：2026-05-18 07:27:18 CST
范围：后端、前端后台、Telegram 机器人、TRON 扫链、云资源生命周期。
全量索引文件数：1249 个源码/配置/脚本文件。
旧审计报告：[FULL_LOGIC_AUDIT_2026-05-18.md](./FULL_LOGIC_AUDIT_2026-05-18.md)
全量文件索引：[FILE_INVENTORY_2026-05-18.md](./FILE_INVENTORY_2026-05-18.md)

## 修复任务状态

| 任务 | 状态 | 说明 |
| --- | --- | --- |
| 后台 API 鉴权收口 | 已完成 | 后台业务 GET 接口统一改为 `dashboard_login_required`，兼容 `Bearer session-*`。 |
| 危险云操作确认 | 已完成 | 删除服务器、释放未附加 IP、本地删除代理记录均增加更明确的确认信息。 |
| 机器人重启丢消息风险 | 已完成 | `drop_pending_updates` 改为配置项，默认不丢弃待处理更新。 |
| TRON 扫链断点补扫 | 已完成 | 使用 `SiteConfig.tron_scanner_last_block_number` 持久化最后区块号，单轮限制补扫数量。 |
| 支付文案收紧 | 已完成 | 订单前不再提示“已开始监控 USDT/TRX”；订单后只展示真实 `pay_amount + currency`。 |
| 云动作结构化结果 | 已完成 | 生命周期云动作返回 `CloudActionResult`，不再靠字符串判断失败。 |
| 生命周期时间重算保护 | 已完成 | `CloudServerOrder.recalculate_lifecycle_dates()` 显式化，普通保存不再覆盖人工时间。 |
| 全文件复扫与报告 | 已完成 | 已执行静态复扫、后端检查、编译、前端 typecheck。 |

## 关键代码变更

### 后端鉴权

- `dashboard_api/views.py`
  - `me`
  - `overview`
  - `users_list`
  - `user_balance_details`
  - `products_list`
  - `orders_list`
  - `cloud_orders_list`
  - `cloud_assets_list`
  - `servers_statistics`
  - `servers_list`
  - `recharges_list`
  - `monitors_list`

这些接口由 Django 原生 `login_required` 改为项目自己的 `dashboard_login_required`，避免前后端分离场景下 Bearer token 被误判。

### 机器人与支付

- `bot/runner.py`
  - 新增 `telegram_drop_pending_updates_on_start` 配置。
  - 默认值为 `0`，即机器人启动不丢 Telegram pending updates。
- `core/runtime_config.py`
  - 新增配置帮助和环境变量映射：`TELEGRAM_DROP_PENDING_UPDATES_ON_START`。
- `bot/handlers.py`
  - 创建订单前的支付页改为提示“选择支付方式后生成唯一金额”。
  - 订单创建后展示真实币种和唯一金额。
- `biz/services/custom.py`
  - TRX 云服务器地址支付金额先换算 TRX，再生成唯一尾数。

### TRON 扫链

- `tron/scanner.py`
  - 新增 `tron_scanner_last_block_number` 持久化。
  - 新增 `getblockbynum` 区块补扫。
  - 单轮补扫默认最多 20 个块，可用 `TRON_SCANNER_MAX_BACKFILL_BLOCKS` 调整。
  - 保持 `tx_hash` 幂等和币种 + 金额匹配。

### 云生命周期

- `cloud/lifecycle.py`
  - 新增 `CloudActionResult`。
  - 关机、删机、释放 IP、删除迁移旧机统一返回 `{ok, action, provider, target, note}`。
  - `lifecycle_tick` 和后台手动执行都按 `ok` 更新 DB。
- `dashboard_api/views.py`
  - 手动释放未附加 IP、手动删机改用结构化结果。

### 生命周期时间保护

- `mall/models.py`
  - 新增 `CloudServerOrder.recalculate_lifecycle_dates()`。
  - `save()` 只在 `completed_at/service_started_at/service_expires_at/lifecycle_days/renew_extension_days` 变化时重算生命周期字段。
  - 普通保存备注、用户、支付信息、自动续费等不会覆盖手工编辑的 `ip_recycle_at`。

### 前端危险确认

- `apps/web-antd/src/views/dashboard/tasks/plans.vue`
  - 精准删除服务器：确认框显示云厂商、地区、实例/资产、订单号、公网 IP、后果。
  - 释放未附加固定 IP：确认框明确“释放后通常不可恢复”。
- `apps/web-antd/src/views/dashboard/cloud-assets/index.vue`
  - 删除代理记录确认框明确“只清除本地数据库状态，不删除云端服务器或释放云端 IP”。

## 复扫结果

静态复扫命令检查了以下高风险模式：

- `@login_required`
- `drop_pending_updates=True`
- `系统已开始自动监控 USDT 和 TRX`
- 字符串式失败判断：`'失败' not in ...`
- `_cloud_action_failed`
- 危险确认关键文案
- `CloudActionResult`
- `tron_scanner_last_block_number`
- `recalculate_lifecycle_dates`

结果：

- 业务代码中未发现残留 `@login_required`。
- 业务代码中未发现 `drop_pending_updates=True`。
- 业务代码中未发现旧支付误导文案。
- 业务代码中未发现旧的字符串式云动作失败判断。
- 危险确认、结构化云动作、TRON last block、生命周期显式重算均已命中。
- 旧审计报告中仍保留历史风险描述，作为问题来源记录，不代表当前代码仍有该问题。

## 验证记录

已执行：

```bash
cd /Users/a399/Desktop/shop/shop
uv run python manage.py check
uv run python -m compileall tron biz bot cloud dashboard_api mall core accounts monitoring finance shop -q

cd /Users/a399/Desktop/shop/vue-shop-admin
pnpm turbo run typecheck --filter=@vben/web-antd
```

结果：

- Django system check：通过。
- Python compileall：通过。
- 前端 `vue-tsc` typecheck：通过。

未执行：

- 未执行真实 AWS 删除实例。
- 未释放真实 Static IP。
- 未进行 TRON 主网转账测试。

## 剩余建议

这些不是本轮阻塞项，但建议后续继续做：

1. 给 `SiteConfig` JSON 配置加 schema 校验和迁移函数。
2. 给 `bot/keyboards.py` / `bot/handlers.py` 的 callback_data 做集中编码/解析。
3. 为 TRON 补扫增加后台可视化状态：last block、当前 block、落后块数、最近错误。
4. 增加生命周期动作的单元测试，覆盖 `ok=False` 时不改 DB 成功状态。
5. 用 Playwright 做后台页面全按钮回归，危险按钮只验证确认框，不执行真实云动作。
