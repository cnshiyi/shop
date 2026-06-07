# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 14:50 CST
- 状态：已完成“再查一轮兼容代码”，运行时代码未发现新的兼容残留；清理当前文档中旧 `Server` 兼容投影/门面误导项。
- 本轮范围：运行时代码强规则扫描、测试代码旧字符串复核、当前说明文档旧入口复核。

## 修改摘要

- `ARCHITECTURE.md`：改为明确旧 `Server` 运行时入口已删除。
- `DEVELOPMENT.md`：把“弱化旧表存在感”改成“清理历史文档残留”，避免误解为旧入口仍存在。
- `docs/project-overview.md`：移除 `Server` 非 Django 兼容门面说明。
- `docs/DB_NAMING_CONVENTIONS.md`：改为明确不恢复 `cloud_server` 或 `Server` 包装层。
- `docs/DATA_FLOW_AND_PERSISTENCE.md`：移除 `cloud.Server` 作为当前业务数据库模型的列表项。
- `docs/installed-apps-cutover-plan.md`：移除 `cloud.Server` 已成为真实模型来源的误写。

## 扫描结论

- 运行时代码范围：`shop/`、`core/`、`bot/`、`orders/`、`cloud/`
- 排除：`migrations/`、`tests.py`、`tests_*.py`
- 旧 callback、旧端口入口、旧 `cloud.api`、旧 `Server` 包装、旧计划快照、旧退款入口、订单旧到期字段、旧账号标签变体：无命中。
- 当前文档中剩余的旧入口命中均为“已删除/不要恢复”的红线说明；`ServerPrice` 是当前价格模板模型名，不属于旧 `Server` 入口。

## 验证

本地已通过：

```bash
uv run python manage.py check
git diff --check
```

结果：Django 系统检查通过；diff 空白检查通过。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款入口、旧 `Server` 兼容壳、旧云 API 聚合入口或旧账号标签解析。

## 剩余风险

- 历史版本记录和复盘文档中仍保留旧兼容关键词，用于追溯历史，不代表运行时代码仍保留兼容入口。
- 默认 MySQL 全量测试仍可能遇到已有测试库 `test_a` 的交互确认；本轮未删除测试库。
