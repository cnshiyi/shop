import os
from cloud.schemas import ProvisionResult


async def create_instance(order, server_name: str):
    access_key = os.getenv('ALIBABA_CLOUD_ACCESS_KEY_ID', '')
    secret_key = os.getenv('ALIBABA_CLOUD_ACCESS_KEY_SECRET', '')
    if not access_key or not secret_key:
        return ProvisionResult(ok=False, note='未配置 ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET，已预留接口。')
    return ProvisionResult(
        ok=False,
        note=f'阿里云轻量云创建接口已预留，计划使用实例名: {server_name}。默认镜像为 Debian；补充 AK/SK 后可继续接入真实 API。',
    )
