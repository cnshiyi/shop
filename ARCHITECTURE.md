# 架构规划

## 当前目标
后端正在从历史分散 app 收口到更清晰的五层结构，目标是把运行时真实实现稳定压到：`shop / core / bot / orders / cloud`。

## 当前事实结构
- `shop/`：Django 项目配置层
- `core/`：公共配置、公共模型与基础设施
- `bot/`：Telegram 用户模型、后台认证/用户/配置 API、机器人交互
- `orders/`：充值、余额流水、商品、购物车、订单与交易相关服务
- `cloud/`：云套餐、价格模板、云订单、云资产、服务器、监控模型与相关服务/缓存
- `tron/`：链上扫描与资源巡检，运行时已优先依赖 `cloud/`、`orders/`、`bot/`

## 旧层现状
以下目录仍保留，但目标已经降级为“兼容/迁移壳”，不再承载新的真实业务实现：
- `accounts/`
- `finance/`
- `mall/`
- `monitoring/`
- `biz/`（当前仅保留测试命名空间与最小包骨架）

补充说明：后台聚合路由已并回 `shop/dashboard_urls.py`。

其中：
- `bot.models`、`orders.models`、`cloud.models` 已是当前真实模型归属。
- `accounts/models.py`、`finance/models.py`、`mall/models.py`、`monitoring/models.py` 都已删除，旧 app 进一步压成 migration-only 壳。
- `accounts/services.py` 也已删除，旧余额记账兼容入口不再保留在旧 app。
- 旧 `dashboard_api` 已退出运行时并并回 `shop/dashboard_urls.py`。

## 迁移策略
- 保持 `db_table` 稳定，避免为代码收口额外改线上数据结构。
- 保留历史 migration 链，通过 `SeparateDatabaseAndState` 前移 Django state。
- 某段新实现一旦迁移并验证通过，就立即删除旧实现、旧 helper、旧转发层和无用导入。

## 当前剩余重点
### 阶段 A：继续压缩旧入口
- 继续清理 `biz` 仅剩的测试命名空间与历史兼容口径
- 继续清理 `dashboard_api` 已退场后的旧文档口径
- 清理 README / 架构文档中的旧结构叙述

### 阶段 B：评估 `INSTALLED_APPS` 收口
- 评估 `accounts` / `finance` / `mall` / `monitoring` 是否可仅保留 migration 历史
- 评估剩余旧兼容 app 是否还能继续从运行时配置中退出
- 在确认 migration 依赖和 app label 不会破坏测试库初始化后，再做 app 注册裁剪

## 为什么还没直接删旧 app
因为剩余风险点已经不在业务实现，而在 Django 机制本身：
- app label 与 migration 依赖
- 历史 migration 对旧 app 的引用
- fresh test DB 的初始化顺序
- `INSTALLED_APPS` 变化对 admin / URL / migration loader 的连锁影响

所以当前策略已经从“先收口引用”进入“边验证边拆旧壳”的最后阶段。