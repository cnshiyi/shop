# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-04 19:38 CST
- 状态：按用户要求补齐规则：未附加固定 IP 如果没有到期时间，自动写入 15 天后删除计划；未触发真实云删除、释放固定 IP、链上转账或生产发布。
- 最近提交：`04a1e0b record null expiry asset lifecycle test`；本轮修改 `bot/api.py`、`cloud/lifecycle.py`、`cloud/tests.py` 和报告文档，工作树仍含既存未归属模型/迁移改动，未回退。
- 本轮范围：计划列表 `_unattached_ip_delete_items` 与生命周期扫描 `_get_unattached_static_ip_delete_due` 两条入口都补齐 `CloudAsset.actual_expires_at`；新增两个回归测试覆盖计划页补齐和生命周期扫描补齐。
- 本轮结论：未附加固定 IP 缺失 `CloudAsset.actual_expires_at` 时，会按 `cloud_unattached_ip_delete_after_days` 配置补齐，默认 15 天后，在 `cloud_unattached_ip_delete_time` 配置时间执行；补齐后不会被当作本轮立即到期释放。

## 最近验证

- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py cloud/lifecycle.py cloud/tests.py` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_fill_missing_expiry_with_default_delete_plan cloud.tests.CloudServerServicesTestCase.test_unattached_static_ip_due_scan_fills_missing_expiry_as_future_plan --settings=shop.settings --verbosity=2` 通过，2 个新增测试 OK。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests cloud.tests_task_center --settings=shop.settings --verbosity=1` 通过，385 个生命周期/任务中心相关测试 OK；SQLite 仅打印不支持表/字段 comment 的预期 warning。
- 真实库复核：执行前后“未附加固定 IP 且到期为空”的存量记录数量均为 0。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py refresh_lifecycle_plans` 通过，真实库计划结果：`due=1 future=1 history=3 ip_delete=3`。
- 敏感信息处理：报告和总结不记录完整公网 IP、代理链接、secret、登录密码、Telegram token、session 或云账号密钥。

## 剩余风险

- 本轮没有执行真实云资源破坏性动作，只实现并验证补齐计划时间；真实释放仍由已有生命周期执行开关和时间窗口控制。
- 真实库当前没有缺失到期时间的未附加固定 IP 存量样本，因此真实刷新没有发生补写。
- 未执行链上真实充值到账，因为本轮没有外部钱包向收款地址发起真实链上转账。
- 工作树仍含既存未归属模型/迁移改动，未纳入本轮提交。

## 下一步

- 如后续云同步产生未附加固定 IP 且到期为空的真实记录，刷新生命周期计划应自动写入默认 15 天后的删除时间。
- 链上充值到账仍需真实外部钱包转账来源。
