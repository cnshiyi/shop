import os
from cloud.schemas import ProvisionResult


async def create_instance(order, server_name: str):
    access_key = os.getenv('AWS_ACCESS_KEY_ID', '')
    secret_key = os.getenv('AWS_SECRET_ACCESS_KEY', '')
    if not access_key or not secret_key:
        return ProvisionResult(ok=False, note='未配置 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY，已预留接口。')
    return ProvisionResult(
        ok=False,
        note=(
            f'AWS 光帆创建接口已预留，计划使用实例名: {server_name}。默认镜像为 Debian，登录方式按密码登录设计。'
            '真实创建时需要同步申请并绑定 Lightsail Static IP，且安全组放行 SSH 与 MTProxy 端口。'
            '当前尚未写入真实 Lightsail API 调用参数映射，请补充 AK/SK 后继续完成落地。'
        ),
    )
