将 AWS/Lightsail 私钥放到项目内目录：`secrets/lightsail/`

建议：
- 私钥文件：`secrets/lightsail/*.pem`
- 可选同名公钥：`secrets/lightsail/*.pub`

自动识别顺序：
1. `AWS_LIGHTSAIL_PRIVATE_KEY_PATH`
2. `AWS_LIGHTSAIL_PRIVATE_KEY_DIR`
3. 项目内默认目录 `secrets/lightsail/`
4. 用户目录回退路径（兼容旧环境）
