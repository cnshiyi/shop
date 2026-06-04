# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-04 19:22 CST
- 状态：按用户要求继续做生命周期专项复核；未触发新的真实删机、释放固定 IP、链上转账或生产发布；确认正式 bot polling 只剩一组，结束了 PyCharm debug 重复 polling 进程。
- 最近提交：`61c8c4e record lifecycle test pass`；本轮仅更新测试记录文档，工作树仍含既存未归属模型/迁移改动，未回退。
- 本轮范围：复核真实库订单/资产/余额/地址监控状态；重跑生命周期与任务中心聚焦测试；刷新真实库生命周期计划和通知计划；确认 `run.py bot` 正式进程仍在运行。
- 本轮结论：生命周期专项测试通过。旧订单 `#79` / 旧资产 `#325` 已删除；新订单 `#80` 为 `completed`，新资产 `#326` 为 `running`；资产到期事实仍在 `CloudAsset.actual_expires_at`。当前未发现待执行 pending lifecycle task。

## 最近验证

- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests cloud.tests_task_center --settings=shop.settings --verbosity=1` 通过，383 个生命周期/任务中心相关测试 OK；SQLite 仅打印不支持表/字段 comment 的预期 warning。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py refresh_lifecycle_plans` 通过，真实库计划结果：`due=1 future=1 history=3 ip_delete=3`。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py refresh_notice_plans` 通过，真实库通知计划结果：`due=2 future=1 history=7`。
- 真实库复核：测试用户余额为 USDT `990.000000`、TRX `984.747000`；余额流水 3 条；地址监控 0 条。
- 真实库复核：订单 `#79` 状态 `deleted`，资产 `#325` 状态 `deleted` 且不可见；订单 `#80` 状态 `completed` 且替换订单 `#79`，资产 `#326` 状态 `running` 且可见。
- 进程复核：已结束 PyCharm debug 派生的重复 `bot.runner`；当前只保留 `uv run python run.py bot` -> `run.py bot` -> `python -m bot.runner` 这一组正式 polling。
- 敏感信息处理：报告和总结不记录完整公网 IP、代理链接、secret、登录密码、Telegram token、session 或云账号密钥。

## 剩余风险

- 本轮没有执行新的真实云资源破坏性动作，只复核上一轮已完成的旧机删除、旧固定 IP 释放和新资产运行状态。
- 未执行链上真实充值到账，因为本轮没有外部钱包向收款地址发起真实链上转账；此前已覆盖充值入口、地址展示、充值记录和余额钱包支付/扣款流水。
- 当前没有可实际变更的“修改配置”项，此入口此前真实点击返回“暂无可修改的配置”。
- 工作树仍含既存未归属模型/迁移改动，未纳入本轮测试记录。

## 下一步

- 如继续补充，唯一未真实链上完成的是外部钱包向充值地址转账后的到账扫描；需要真实链上转账来源。
- 可在存在可升级/可变更套餐时再补一次“修改配置”实际变更场景。
