# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-12 12:36 CST
- 状态：已完成 AWS 固定 IP 从绑定实例变为未附加 IP 的真机测试，验证代理列表快照和到期时间会更新。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 目标主分支：`main`

## 本轮背景

- 用户授权真实云资源成本，并要求“更激进：真实把固定 IP 从实例解绑，再跑同步验证页面和到期时间；你自己创建”。
- 本轮真实创建 AWS Lightsail 测试实例和固定 IP，完成绑定、同步、解绑、再次同步、验证和清理。

## 真机测试摘要

- 云厂商：AWS Lightsail
- 云账号：后台 AWS 云账号 `#55`
- 地区：`ap-southeast-1`
- 套餐：`nano_3_0`
- 镜像：`debian_12`
- 测试资源：实例名、固定 IP 名、公网 IP 均已脱敏，完整值未写入文档。
- 本地测试资产：`CloudAsset #39`

## 验证结果

- 绑定阶段：
  - 创建实例成功。
  - 分配固定 IP 成功。
  - 固定 IP 绑定实例成功。
  - 首次 `sync_aws_assets` 后，本地资产和代理列表快照显示为“服务器”。
- 解绑阶段：
  - 真实调用 AWS `detach_static_ip`，固定 IP 变为未附加。
  - 定向运行 `sync_aws_assets --public-ip ...` 后，同一资产清空 `instance_id`，资源标识变为 StaticIp。
  - `CloudAsset.actual_expires_at` 从服务器测试到期时间重算为未附加 IP 删除计划时间。
  - `CloudAssetDashboardSnapshot.payload.resource_kind=unattached_ip`。
  - `CloudAssetDashboardSnapshot.payload.resource_kind_label=未附加IP`。
  - 快照排序时间 `asset_due_sort_at` 与资产 `actual_expires_at` 一致。

## 清理结果

- 已提交删除测试实例。
- 已释放测试固定 IP。
- 清理后只读复核：
  - 测试实例不存在。
  - 测试固定 IP 不存在。
- 本地测试资产已标记为 `deleted/is_active=False`，并刷新代理列表快照。

## 验证命令

通过：

```bash
uv run python manage.py shell
```

脚本动作：创建真实实例和固定 IP、绑定、同步、写入测试到期、刷新快照、解绑固定 IP、定向同步、断言快照和到期、删除实例、释放固定 IP、复核残留。

## 风险和下一步

- 本轮执行了真实云资源创建、固定 IP 绑定/解绑、实例删除和固定 IP 释放；用户已明确授权。
- 未执行真实支付、链上广播或生产发布。
- 云资源已清理完成，未发现测试实例或固定 IP 残留。
