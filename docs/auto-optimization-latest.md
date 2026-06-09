# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-09 15:19 CST
- 状态：已修复 AWS 云账号轮询创建前不查真实配额的问题。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户反馈多个 AWS 云账号轮询仍有问题，要求创建服务器前检查配额，配额不足直接换下一个账号。
- 进一步确认后，配额检查必须调用 AWS 真实配额，不使用后台配置模拟配额。
- 本轮只处理 AWS Lightsail；阿里云不增加创建前配额检查，保持原创建逻辑。

## 修复内容

- `cloud/aws_lightsail.py`
  - 新增 AWS Service Quotas 客户端构造。
  - 创建前读取 Lightsail 真实配额：
    - `Instances`：按 AWS Lightsail 口径作为区域实例 vCPU 配额。
    - `Static IP addresses`：固定 IP 数量配额。
  - 结合当前 Lightsail 实例硬件 `cpuCount`、待创建 bundle 的 `cpuCount`、当前固定 IP 数，判断本次创建是否会超配额。
  - 如果实例 vCPU 或固定 IP 会超限，返回明确原因，不进入真实创建请求。
  - 如果无法读取真实配额，也按检查失败处理，避免盲目创建。
- `cloud/provisioning.py`
  - AWS 候选账号循环中新增 `provider_capacity_check` 日志阶段。
  - 每个 AWS 账号在调用 `create_aws_instance()` 前先执行真实配额检查。
  - 当前账号配额不足时记录失败原因并直接轮询下一个账号。
  - 阿里云返回“无需创建前配额检查”，不改变原创建链路。
- `cloud/tests.py`
  - 覆盖 AWS 配额函数会读取 Service Quotas，并按实例 vCPU 和固定 IP 判断。
  - 覆盖第一个 AWS 账号配额不足时不会调用创建接口，只用第二个账号创建。
  - 修正现有开通测试，避免测试环境误触真实 AWS 配额查询。

## 验证

通过：

```bash
uv run python -m py_compile cloud/aws_lightsail.py cloud/aliyun_simple.py cloud/provisioning.py cloud/tests.py core/runtime_config.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_aws_create_capacity_uses_real_service_quotas cloud.tests.CloudServerServicesTestCase.test_provision_rotates_before_create_when_aws_quota_is_full cloud.tests.CloudServerServicesTestCase.test_provision_rotates_to_next_cloud_account_after_create_failure cloud.tests.CloudServerServicesTestCase.test_provision_expected_ip_failure_schedules_cleanup --settings=shop.settings --verbosity=1
uv run python manage.py check
git diff --check
```

结果：

- 编译通过。
- 聚焦测试 4 条通过。
- Django 系统检查通过。
- diff 空白检查通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 告警，不属于本轮问题。

## 结论

- AWS 购买创建现在会在真实创建实例前先查 AWS 真实配额。
- 当前 AWS 账号配额不足时，不会发起实例创建，直接切换到下一个候选云账号。
- 阿里云不做本轮配额前置检查，避免引入无关逻辑。
