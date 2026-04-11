import os
import secrets
import string
import time

from cloud.schemas import ProvisionResult


def _rand_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits + '@#%_-'
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _bundle_id_from_plan(plan_name: str) -> str:
    text = (plan_name or '').lower()
    if '2c2g' in text or '2gb' in text:
        return 'medium_2_0'
    return 'nano_3_0'


def _build_user_data(password: str) -> str:
    return f'''#!/usr/bin/env bash
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y sudo openssh-server
id -u admin >/dev/null 2>&1 || useradd -m -s /bin/bash admin
printf 'admin:{password}' | chpasswd
usermod -aG sudo admin || true
sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
systemctl restart ssh || systemctl restart sshd || true
'''


async def create_instance(order, server_name: str):
    access_key = os.getenv('AWS_ACCESS_KEY_ID', '')
    secret_key = os.getenv('AWS_SECRET_ACCESS_KEY', '')
    if not access_key or not secret_key:
        return ProvisionResult(ok=False, note='未配置 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY。')

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        return ProvisionResult(ok=False, note='未安装 boto3，无法调用 AWS Lightsail。')

    region = order.region_code or 'ap-southeast-1'
    password = _rand_password()
    bundle_id = _bundle_id_from_plan(order.plan_name)
    blueprint_id = 'debian_12'
    static_ip_name = f'{server_name}-ip'[:255]

    try:
        client = boto3.client(
            'lightsail',
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

        client.create_instances(
            instanceNames=[server_name],
            availabilityZone=f'{region}a',
            blueprintId=blueprint_id,
            bundleId=bundle_id,
            userData=_build_user_data(password),
        )

        for _ in range(60):
            resp = client.get_instance(instanceName=server_name)
            state = ((resp.get('instance') or {}).get('state') or {}).get('name')
            if state == 'running':
                break
            time.sleep(5)
        else:
            return ProvisionResult(ok=False, note=f'AWS 实例已提交创建，但在规定时间内未进入 running: {server_name}')

        try:
            client.allocate_static_ip(staticIpName=static_ip_name)
        except ClientError as exc:
            if 'already exists' not in str(exc).lower():
                raise
        client.attach_static_ip(staticIpName=static_ip_name, instanceName=server_name)

        public_ip = ''
        for _ in range(30):
            ip_resp = client.get_static_ip(staticIpName=static_ip_name)
            public_ip = ((ip_resp.get('staticIp') or {}).get('ipAddress')) or ''
            if public_ip:
                break
            time.sleep(3)

        if not public_ip:
            resp = client.get_instance(instanceName=server_name)
            public_ip = ((resp.get('instance') or {}).get('publicIpAddress')) or ''

        note = (
            f'AWS Lightsail 创建成功，已绑定固定公网 IP。实例名: {server_name}，'
            f'套餐: {bundle_id}，镜像: {blueprint_id}。'
        )
        return ProvisionResult(
            ok=True,
            instance_id=server_name,
            public_ip=public_ip,
            login_user='admin',
            login_password=password,
            note=note,
        )
    except (BotoCoreError, ClientError) as exc:
        return ProvisionResult(ok=False, note=f'AWS Lightsail 创建失败: {exc}')
    except Exception as exc:
        return ProvisionResult(ok=False, note=f'AWS Lightsail 创建异常: {exc}')
