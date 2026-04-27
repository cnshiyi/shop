# 本地敏感材料位置

项目默认在仓库工作目录内保存本地敏感材料，但该目录必须保持 Git 忽略，不提交。

## 默认位置

- AWS Lightsail 私钥：`./.shop-secrets/lightsail/`
- 阿里云临时 keypair：`./.shop-secrets/aliyun-keypairs/`
- 通用 SSH 公钥/私钥兜底：`./.shop-secrets/ssh/`

## 环境变量优先级

### AWS Lightsail

1. `AWS_LIGHTSAIL_PRIVATE_KEY_PATH`
2. `AWS_LIGHTSAIL_PRIVATE_KEY_DIR`
3. 默认目录 `./.shop-secrets/lightsail/`
4. 兜底目录 `./.shop-secrets/ssh/`

### AWS 创建实例注入公钥

1. `AWS_LIGHTSAIL_PUBLIC_KEY`
2. `AWS_LIGHTSAIL_PUBLIC_KEY_PATH`
3. 默认目录 `./.shop-secrets/lightsail/*.pub`
4. 兜底目录 `./.shop-secrets/ssh/*.pub`

## 说明

- `.shop-secrets/` 已加入 `.gitignore`，只随本机/项目目录迁移，不进入 Git。
- 如果迁移项目，请把整个 `./.shop-secrets/` 目录一起复制。
- 建议目录权限保持为当前用户私有，私钥文件权限为 `600`。
