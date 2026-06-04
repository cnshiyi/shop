# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-04 19:30 CST
- 状态：按用户要求专项测试“没有到期日期”的云资产处理流程，覆盖普通服务器资产和未附加固定 IP 资产；未触发真实云删除、释放固定 IP、链上转账或生产发布。
- 最近提交：`851c62c record lifecycle retest status`；本轮仅更新测试记录文档，工作树仍含既存未归属模型/迁移改动，未回退。
- 本轮范围：在真实 MySQL 连接中用事务临时创建 1 条普通服务器资产和 1 条模拟未附加固定 IP 资产，二者 `CloudAsset.actual_expires_at=None`；在事务内执行生命周期计划刷新和通知计划刷新；确认临时数据回滚后真实库无残留。
- 本轮结论：无到期日期资产不会被误加入生命周期任务或通知任务。两条临时资产在刷新后均未生成 `CloudLifecycleTask` 或 `CloudNoticeTask`；事务回滚后临时资产数量为 0。

## 最近验证

- 事务内真实库验证：普通服务器资产 `actual_expires_at=None`，刷新后 `temp_lifecycle_tasks=[]`、`temp_notice_tasks=[]`。
- 事务内真实库验证：模拟未附加固定 IP 资产 `actual_expires_at=None`，刷新后 `temp_lifecycle_tasks=[]`、`temp_notice_tasks=[]`。
- 事务内刷新输出：生命周期计划 `due=1 future=1 history=3 ip_delete=3`；通知计划 `due=2 future=1 history=7`。
- 回滚后真实库复核：临时资产数量 0；`CloudLifecycleTask` 数量 0；`CloudNoticeTask` 数量 1，仍为既有历史记录。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests cloud.tests_task_center --settings=shop.settings --verbosity=1` 通过，383 个生命周期/任务中心相关测试 OK；SQLite 仅打印不支持表/字段 comment 的预期 warning。
- 敏感信息处理：报告和总结不记录完整公网 IP、代理链接、secret、登录密码、Telegram token、session 或云账号密钥。

## 剩余风险

- 本轮没有执行真实云资源破坏性动作，只验证无到期日期资产不会进入计划/通知任务。
- 未执行链上真实充值到账，因为本轮没有外部钱包向收款地址发起真实链上转账。
- 当前没有可实际变更的“修改配置”项，此入口此前真实点击返回“暂无可修改的配置”。
- 工作树仍含既存未归属模型/迁移改动，未纳入本轮测试记录。

## 下一步

- 如继续补充，可在真实云同步拿到未附加固定 IP 且无到期字段的实际样本时，再做一次只读同步后计划刷新复核。
- 链上充值到账仍需真实外部钱包转账来源。
