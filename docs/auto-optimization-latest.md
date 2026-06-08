# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-09 00:50 CST
- 状态：已收掉生命周期执行器底层人工绕过计划能力。
- 后端提交：本轮完成后提交，具体哈希以 `git log -1` 为准。
- 前端提交：无前端代码变更。

## 本轮背景

- 用户要求解释并收掉代码里仍存在的底层人工绕过能力。
- 本轮没有执行真实云资源创建、真实关机、真实删机、真实支付、链上广播、生产发布或删除数据。

## 修复内容

- `cloud/lifecycle_execution.py`
  - 删除关机、删机、迁移旧机删除、订单固定 IP 回收、未附加 IP 删除等破坏性执行入口的计划绕过形参。
  - 总开关、单资产开关、计划时间、执行窗口、任务认领全部改为无条件校验。
  - 删除成功来源统一按计划执行记录，不再根据手动队列或绕过参数写入“人工手动删除/释放/清理”。
- `bot/api.py`
  - 后台计划页的关机、删机、未附加 IP 删除包装函数不再接收或透传计划绕过参数。
- `cloud/lifecycle.py`
  - 定时生命周期 tick 不再传计划绕过参数，所有破坏性动作只调用严格计划执行器。
- `cloud/tests.py`
  - 旧的“手动绕过计划限制”测试改成“计划时间/窗口/开关必须阻断真实动作”。
  - 新增签名级断言，确认 7 个生命周期破坏性执行入口不再暴露旧绕过形参。
  - 修正生命周期 tick 测试，匹配当前资产计划执行链。

## 结论

- 现在不是“传 False 会失败”，而是执行器签名和调用链都不再存在计划绕过参数。
- 关机、删机、未附加 IP 删除都必须经过计划时间、执行窗口、总开关、单资产开关和任务认领。
- 服务器删机仍要求服务器已进入关机完成/暂停/删除中阶段；未关机服务器不会直接删机。
- IP 删除仍只受 IP 删除总开关和资产 `ip_delete_enabled` 控制，不受关机开关影响。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/lifecycle_execution.py cloud/lifecycle.py bot/api.py cloud/tests.py
rg -n "enforce_schedule|_run_shutdown_order_sync" cloud bot core orders shop --glob '!*/migrations/*'
git diff --check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_order_delete_respects_schedule_window ... --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_tick_serializes_shutdown_delete_and_ip_release_stages ... cloud.tests_task_center --settings=shop.settings --verbosity=1
```

结果：

- Django 系统检查通过。
- 相关文件编译通过。
- 旧参数和旧包装函数扫描无命中。
- 本轮 16 个生命周期执行器聚焦测试通过。
- 生命周期 tick/计划页/任务中心 26 个回归测试通过。
- `git diff --check` 通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。

## 剩余风险

- 本轮没有执行真实云资源操作。
- 仍需在上线前用脱敏报告单独记录已授权真机云资源生命周期测试结果。
