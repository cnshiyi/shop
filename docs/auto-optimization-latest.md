# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 14:38 CST
- 状态：已完成“逐文件查兼容代码”，并移除新发现的旧云账号标签解析兼容残留。
- 本轮范围：`shop/`、`core/`、`bot/`、`orders/`、`cloud/` 运行时代码逐文件扫描；测试代码旧入口断言复核；当前说明文档旧入口清理。

## 修改摘要

- `core/cloud_accounts.py`：
  - `get_cloud_account_from_label()` 不再把 `aws_lightsail+外部账号+名称` 或其他 provider 变体标签解析成当前云账号。
  - 当前只接受 `cloud_account_label()` 生成的标准账号标签完整匹配。
- `core/tests.py`：
  - 删除保护旧 `aws_lightsail+...` 标签变体的测试口径。
  - 新增/调整测试，断言旧 provider 标签不再解析为当前账号，负载统计只统计当前标准标签。
- `cloud/tests.py`：
  - 去重和代理列表测试改为验证旧账号标签残留不会再与当前标准标签合并。
- 当前说明文档：
  - 移除仍把已删除 `cloud/api.py` 聚合入口、`reconcile_cloud_assets_from_servers` 管理命令、旧 `Server` 入口写成当前结构的内容。
  - 当前路由和测试替换目标统一指向 `cloud/api_*` 域模块。

## 逐文件扫描

- 运行时代码扫描范围：`shop/`、`core/`、`bot/`、`orders/`、`cloud/`
- 排除：`migrations/`、`__pycache__/`、`tests.py`、`tests_*.py`
- 文件数：113 个
- 强规则结果：0 个文件、0 条命中
- 宽规则结果：32 个文件、263 条命中，已逐文件复核；命中主要为 Redis/TronGrid 容灾 fallback、迁移旧机业务、历史记录展示、重装迁移和未附加固定 IP 续费，不是旧兼容入口。

测试代码中保留旧字符串只用于负向断言，例如旧 callback 和旧账号标签不应存在/不应解析；这类测试用于防回流。

## 验证

本地已通过：

```bash
uv run python -m py_compile core/cloud_accounts.py core/tests.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test core.tests.CloudAccountSelectionTestCase cloud.tests.CloudServerServicesTestCase.test_dedupe_cloud_assets_does_not_merge_old_account_label_variants cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_keeps_old_account_label_variants_separate cloud.tests.CloudServerServicesTestCase.test_cloud_account_label_variants_return_current_label_only cloud.tests.CloudServerServicesTestCase.test_account_load_does_not_count_provider_only_label_for_every_account --settings=shop.settings --verbosity=1
git diff --check
```

结果：编译通过；Django 系统检查通过；账号标签和代理列表/去重聚焦测试 7 条通过；diff 空白检查通过。SQLite 测试输出的 `db_comment` 警告是已知数据库能力差异。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款入口、旧 `Server` 兼容壳、旧云 API 聚合入口或旧账号标签解析。

## 剩余风险

- 历史版本记录和复盘文档中仍保留旧兼容关键词，用于追溯历史，不代表运行时代码仍保留兼容入口。
- 默认 MySQL 全量测试仍可能遇到已有测试库 `test_a` 的交互确认；本轮未删除测试库，继续使用 SQLite 隔离库跑聚焦测试。
