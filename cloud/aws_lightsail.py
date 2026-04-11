import os
from cloud.schemas import ProvisionResult


async def create_instance(order):
    access_key = os.getenv('AWS_ACCESS_KEY_ID', '')
    secret_key = os.getenv('AWS_SECRET_ACCESS_KEY', '')
    if not access_key or not secret_key:
        return ProvisionResult(ok=False, note='未配置 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY，已预留接口。')
    return ProvisionResult(
        ok=False,
        note=(
            'AWS 光帆创建接口已预留，默认镜像为 Debian，登录方式按密码登录设计。'
            '当前尚未写入真实 Lightsail API 调用参数映射，请补充 AK/SK 后继续完成落地。'
        ),
    )
