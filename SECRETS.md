# 本地敏感材料位置

项目不再在仓库内保存云主机私钥或临时密钥文件。

## 默认位置
- AWS Lightsail 私钥：`~/.shop-secrets/lightsail/`
- 阿里云临时 keypair：`~/.shop-secrets/aliyun-keypairs/`

## 环境变量优先级
### AWS Lightsail
1. `AWS_LIGHTSAIL_PRIVATE_KEY_PATH`
2. `AWS_LIGHTSAIL_PRIVATE_KEY_DIR`
3. 默认目录 `~/.shop-secrets/lightsail/`

## 说明
- 如果需要迁移旧环境中的项目内私钥，请把 `secrets/lightsail/*.pem` 挪到 `~/.shop-secrets/lightsail/`
- 临时生成的阿里云 keypair 也会默认写入 `~/.shop-secrets/aliyun-keypairs/`
- 建议目录权限保持为当前用户私有，私钥文件权限为 `600`
