from asgiref.sync import sync_to_async
import logging
import os
import re
import secrets
import string
import time
from pathlib import Path

from cloud.bootstrap import _derive_public_keys_from_private_keys
from cloud.ports import get_mtproxy_public_ports
from cloud.schemas import ProvisionResult
from django.apps import apps

from core.cloud_accounts import get_active_cloud_account

logger = logging.getLogger(__name__)


def _instance_name_exists(client, instance_name: str) -> bool:
    try:
        client.get_instance(instanceName=instance_name)
        return True
    except Exception as exc:
        if 'NotFoundException' in exc.__class__.__name__ or 'not found' in str(exc).lower():
            return False
        raise


def _next_available_instance_name(client, base_name: str) -> str:
    candidate = (base_name or '')[:255]
    index = 0
    while _instance_name_exists(client, candidate):
        index += 1
        suffix = f'-{index}'
        candidate = f'{base_name[: max(0, 255 - len(suffix))]}{suffix}'
    return candidate


def _ensure_instance_port_open(client, instance_name: str, port: int) -> None:
    for protocol in ('tcp', 'udp'):
        try:
            client.open_instance_public_ports(
                portInfo={
                    'fromPort': int(port),
                    'toPort': int(port),
                    'protocol': protocol.upper(),
                },
                instanceName=instance_name,
            )
        except Exception:
            pass


def _rand_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits + '@#%_-'
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _bundle_id_from_plan(plan_name: str) -> str:
    text = (plan_name or '').lower()
    if '2c4g' in text or '2h4g' in text or '4gb' in text or '4g' in text:
        return 'medium_3_0'
    if '2c2g' in text or '2gb' in text:
        return 'medium_2_0'
    return 'nano_3_0'


def _looks_like_lightsail_bundle_id(value: str) -> bool:
    text = (value or '').strip()
    if not text:
        return False
    return bool(re.fullmatch(r'(nano|micro|small|medium|large|xlarge|2xlarge)(_[0-9]+){1,2}', text))


def _resolve_bundle_id(order_data: dict) -> tuple[str, str]:
    for field in ('provider_plan_id', 'bundle_code', 'config_id'):
        value = str(order_data.get(field) or '').strip()
        if _looks_like_lightsail_bundle_id(value):
            return value, field
    return _bundle_id_from_plan(order_data.get('plan_name') or ''), 'plan_name_fallback'


def _default_login_user_for_blueprint(blueprint_id: str) -> str:
    text = (blueprint_id or '').lower()
    if 'ubuntu' in text:
        return 'ubuntu'
    if 'debian' in text:
        return 'admin'
    return 'admin'


def _build_user_data(password: str, public_key: str = '') -> str:
    public_key = (public_key or '').strip()
    root_key_block = ''
    admin_key_block = ''
    if public_key:
        root_key_block = f"mkdir -p /root/.ssh\ncat > /root/.ssh/authorized_keys <<'EOF_ROOT_KEY'\n{public_key}\nEOF_ROOT_KEY\nchmod 700 /root/.ssh\nchmod 600 /root/.ssh/authorized_keys\n"
        admin_key_block = f"mkdir -p /home/admin/.ssh\ncat > /home/admin/.ssh/authorized_keys <<'EOF_ADMIN_KEY'\n{public_key}\nEOF_ADMIN_KEY\nchown -R admin:admin /home/admin/.ssh\nchmod 700 /home/admin/.ssh\nchmod 600 /home/admin/.ssh/authorized_keys\n"
    return f'''#!/bin/bash
set -eux

export DEBIAN_FRONTEND=noninteractive

id admin >/dev/null 2>&1 || useradd -m -s /bin/bash -G sudo admin
usermod -aG sudo admin || true

echo 'admin:{password}' | chpasswd
echo 'root:{password}' | chpasswd
passwd -u root || true
passwd -u admin || true

mkdir -p /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/99-openclaw-password.conf <<'EOF_SSHD'
PasswordAuthentication yes
PermitRootLogin yes
PubkeyAuthentication yes
KbdInteractiveAuthentication yes
ChallengeResponseAuthentication no
UsePAM yes
EOF_SSHD

grep -q '^PasswordAuthentication ' /etc/ssh/sshd_config && sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config || echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config
grep -q '^PermitRootLogin ' /etc/ssh/sshd_config && sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config || echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config
grep -q '^KbdInteractiveAuthentication ' /etc/ssh/sshd_config && sed -i 's/^KbdInteractiveAuthentication.*/KbdInteractiveAuthentication yes/' /etc/ssh/sshd_config || echo 'KbdInteractiveAuthentication yes' >> /etc/ssh/sshd_config
grep -q '^UsePAM ' /etc/ssh/sshd_config && sed -i 's/^UsePAM.*/UsePAM yes/' /etc/ssh/sshd_config || echo 'UsePAM yes' >> /etc/ssh/sshd_config

{root_key_block}{admin_key_block}systemctl daemon-reload || true
systemctl enable ssh || systemctl enable sshd || true
systemctl restart ssh || systemctl restart sshd || true
'''


def _load_public_key() -> str:
    env_value = (os.getenv('AWS_LIGHTSAIL_PUBLIC_KEY') or '').strip()
    if env_value:
        return env_value

    public_key_path = (os.getenv('AWS_LIGHTSAIL_PUBLIC_KEY_PATH') or '').strip()
    candidates = [public_key_path] if public_key_path else []
    project_root = Path(__file__).resolve().parent.parent
    project_public_key_dirs = [
        project_root / '.shop-secrets' / 'lightsail',
        project_root / '.shop-secrets' / 'ssh',
    ]
    derived_count = _derive_public_keys_from_private_keys(*project_public_key_dirs)
    for project_public_key_dir in project_public_key_dirs:
        if project_public_key_dir.is_dir():
            candidates.extend(str(path) for path in sorted(project_public_key_dir.glob('*.pub')))
    candidates = [candidate for candidate in candidates if candidate]
    logger.info(
        '开始扫描 AWS 创建实例公钥候选: count=%s derived=%s env_public_key=%s env_public_key_path=%s dirs=%s',
        len(candidates),
        derived_count,
        bool(env_value),
        bool(public_key_path),
        ','.join(str(item) for item in project_public_key_dirs),
    )
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            try:
                content = path.read_text(encoding='utf-8').strip()
                if content:
                    logger.info('已加载 AWS 创建实例公钥: source=%s fingerprint_hint=%s', path, content.split()[1][-12:] if len(content.split()) > 1 else '')
                    return content
            except OSError:
                continue
    logger.warning('未找到 AWS 创建实例公钥: env_public_key=%s env_public_key_path=%s candidate_count=%s', bool(env_value), bool(public_key_path), len(candidates))
    return ''


def _aws_client_from_order_data(order_data: dict):
    region = order_data.get('region_code') or 'ap-southeast-1'
    account_id = order_data.get('cloud_account_id')
    account = None
    if account_id:
        CloudAccountConfig = apps.get_model('core', 'CloudAccountConfig')
        account = CloudAccountConfig.objects.filter(id=account_id, provider='aws', is_active=True).first()
    account = account or get_active_cloud_account('aws', region)
    access_key = ''
    secret_key = ''
    if account:
        ak = (account.access_key_plain or '').strip()
        sk = (account.secret_key_plain or '').strip()
        if ak and sk and len(ak) >= 16 and len(sk) >= 36:
            access_key, secret_key = ak, sk
    if not access_key or not secret_key:
        access_key = os.getenv('AWS_ACCESS_KEY_ID', '')
        secret_key = os.getenv('AWS_SECRET_ACCESS_KEY', '')
    if not access_key or not secret_key:
        return None, '未配置 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY。'
    try:
        import boto3
    except ImportError:
        return None, '未安装 boto3，无法调用 AWS Lightsail。'
    return boto3.client(
        'lightsail',
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    ), ''


def _create_instance_sync(order_data: dict, server_name: str):
    order_no = order_data.get('order_no')
    provider = order_data.get('provider')
    region = order_data.get('region_code') or 'ap-southeast-1'
    plan_name = order_data.get('plan_name')
    mtproxy_port = int(order_data.get('mtproxy_port') or 9528)
    static_ip_name = str(order_data.get('static_ip_name') or '').strip()
    skip_static_ip = bool(order_data.get('skip_static_ip'))

    logger.info('AWS Lightsail 创建开始: order=%s provider=%s region=%s plan=%s server_name=%s skip_static_ip=%s', order_no, provider, region, plan_name, server_name, skip_static_ip)
    client, client_error = _aws_client_from_order_data(order_data)
    if not client:
        logger.warning('AWS Lightsail 创建失败: order=%s error=%s', order_no, client_error)
        return ProvisionResult(ok=False, note=client_error)

    try:
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        logger.warning('AWS Lightsail 创建失败: 未安装 boto3 order=%s', order_no)
        return ProvisionResult(ok=False, note='未安装 boto3，无法调用 AWS Lightsail。')

    password = _rand_password()
    public_key = _load_public_key()
    bundle_id, bundle_source = _resolve_bundle_id(order_data)
    blueprint_id = 'debian_12'
    static_ip_name = static_ip_name or ('' if skip_static_ip else f'{server_name}-ip'[:255])

    try:
        logger.info('AWS 客户端已就绪: order=%s region=%s bundle=%s bundle_source=%s blueprint=%s static_ip_name=%s skip_static_ip=%s', order_no, region, bundle_id, bundle_source, blueprint_id, static_ip_name, skip_static_ip)

        server_name = _next_available_instance_name(client, server_name)
        logger.info('AWS 实例命名完成: order=%s server_name=%s static_ip_name=%s', order_no, server_name, static_ip_name)

        client.create_instances(
            instanceNames=[server_name],
            availabilityZone=f'{region}a',
            blueprintId=blueprint_id,
            bundleId=bundle_id,
            userData=_build_user_data(password, public_key),
        )
        logger.info('AWS 实例创建请求已提交: order=%s server_name=%s az=%s', order_no, server_name, f'{region}a')

        state = None
        for idx in range(60):
            resp = client.get_instance(instanceName=server_name)
            state = ((resp.get('instance') or {}).get('state') or {}).get('name')
            logger.info('等待 AWS 实例运行中: order=%s attempt=%s state=%s', order_no, idx + 1, state)
            if state == 'running':
                break
            time.sleep(5)
        else:
            logger.warning('AWS 实例启动超时: order=%s server_name=%s last_state=%s', order_no, server_name, state)
            return ProvisionResult(ok=False, note=f'AWS 实例已提交创建，但在规定时间内未进入 running: {server_name}')

        try:
            _ensure_instance_port_open(client, server_name, 22)
            public_ports = get_mtproxy_public_ports(mtproxy_port)
            for public_port in public_ports:
                _ensure_instance_port_open(client, server_name, public_port)
            logger.info('AWS 端口放行完成: order=%s server_name=%s ssh=22 mtproxy_ports=%s', order_no, server_name, ','.join(str(item) for item in public_ports))
        except Exception as exc:
            logger.warning('AWS 端口放行失败: order=%s server_name=%s error=%s', order_no, server_name, exc)

        public_ip = ''
        if skip_static_ip:
            for idx in range(30):
                resp = client.get_instance(instanceName=server_name)
                public_ip = ((resp.get('instance') or {}).get('publicIpAddress')) or ''
                logger.info('等待 AWS 临时公网 IP 生效: order=%s attempt=%s public_ip=%s', order_no, idx + 1, public_ip)
                if public_ip:
                    break
                time.sleep(3)
        else:
            try:
                client.allocate_static_ip(staticIpName=static_ip_name)
                logger.info('AWS 固定 IP 已分配: order=%s static_ip_name=%s', order_no, static_ip_name)
            except ClientError as exc:
                if 'already exists' not in str(exc).lower():
                    logger.warning('AWS 固定 IP 分配失败: order=%s static_ip_name=%s error=%s', order_no, static_ip_name, exc)
                    return ProvisionResult(ok=False, note='创建失败，请联系人工客服')
                logger.info('AWS 固定 IP 已存在: order=%s static_ip_name=%s', order_no, static_ip_name)
            client.attach_static_ip(staticIpName=static_ip_name, instanceName=server_name)
            logger.info('AWS 固定 IP 绑定完成: order=%s static_ip_name=%s server_name=%s', order_no, static_ip_name, server_name)
            for idx in range(30):
                ip_resp = client.get_static_ip(staticIpName=static_ip_name)
                public_ip = ((ip_resp.get('staticIp') or {}).get('ipAddress')) or ''
                logger.info('等待 AWS 固定公网 IP 生效: order=%s attempt=%s public_ip=%s', order_no, idx + 1, public_ip)
                if public_ip:
                    break
                time.sleep(3)

        if not public_ip:
            logger.warning('AWS 公网 IP 获取失败: order=%s server_name=%s static_ip_name=%s skip_static_ip=%s', order_no, server_name, static_ip_name, skip_static_ip)
            return ProvisionResult(ok=False, note='创建失败，请联系人工客服')

        login_mode = 'SSH 公钥登录已启用，后续自动设置 root 密码' if public_key else 'root 密码'
        ip_note = '未绑定固定 IP，等待迁移原固定 IP' if skip_static_ip else '已绑定固定公网 IP'
        note = (
            f'AWS Lightsail 创建成功，{ip_note}。实例名: {server_name}，'
            f'套餐: {bundle_id}，镜像: {blueprint_id}，{login_mode}登录已启用。'
        )
        logger.info('AWS Lightsail 创建成功: order=%s server_name=%s public_ip=%s bundle=%s blueprint=%s', order_no, server_name, public_ip, bundle_id, blueprint_id)
        print('[AWS_CREATE_RESULT]', {
            'order_no': order_no,
            'server_name': server_name,
            'region': region,
            'bundle_id': bundle_id,
            'blueprint_id': blueprint_id,
            'public_ip': public_ip,
            'static_ip_name': static_ip_name,
        })
        return ProvisionResult(
            ok=True,
            instance_id=server_name,
            public_ip=public_ip,
            login_user=_default_login_user_for_blueprint(blueprint_id),
            login_password=password,
            note=note,
            static_ip_name=static_ip_name,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.exception('AWS Lightsail 创建失败: client_error order=%s server_name=%s error=%s', order_no, server_name, exc)
        return ProvisionResult(ok=False, note=f'AWS Lightsail 创建失败: {exc}')
    except Exception as exc:
        logger.exception('AWS Lightsail 创建异常: order=%s server_name=%s error=%s', order_no, server_name, exc)
        return ProvisionResult(ok=False, note=f'AWS Lightsail 创建异常: {exc}')


def _move_static_ip_sync(order_data: dict, instance_name: str, static_ip_name: str, temp_static_ip_name: str = '') -> tuple[bool, str, str]:
    order_no = order_data.get('order_no')
    if not instance_name or not static_ip_name:
        return False, '', '缺少实例名或固定 IP 名称，无法迁移固定 IP。'
    client, client_error = _aws_client_from_order_data(order_data)
    if not client:
        return False, '', client_error
    try:
        from botocore.exceptions import ClientError
    except ImportError:
        return False, '', '未安装 boto3/botocore，无法迁移 AWS 固定 IP。'
    try:
        logger.info('AWS 固定 IP 迁移开始: order=%s static_ip_name=%s target_instance=%s temp_static_ip_name=%s', order_no, static_ip_name, instance_name, temp_static_ip_name)
        try:
            client.attach_static_ip(staticIpName=static_ip_name, instanceName=instance_name)
        except ClientError as exc:
            error_text = str(exc)
            error_text_lower = error_text.lower()
            already_on_target = 'already attached to the instance' in error_text_lower and instance_name in error_text
            if already_on_target:
                logger.info('AWS 固定 IP 已在目标实例上: order=%s static_ip_name=%s target_instance=%s', order_no, static_ip_name, instance_name)
            else:
                if 'already attached' not in error_text_lower and 'attached' not in error_text_lower:
                    raise
                logger.warning('AWS 固定 IP 迁移需先解绑: order=%s static_ip_name=%s error=%s', order_no, static_ip_name, exc)
                client.detach_static_ip(staticIpName=static_ip_name)
                time.sleep(5)
                client.attach_static_ip(staticIpName=static_ip_name, instanceName=instance_name)
        public_ip = ''
        attached_to = ''
        for idx in range(30):
            ip_resp = client.get_static_ip(staticIpName=static_ip_name)
            static_ip = ip_resp.get('staticIp') or {}
            public_ip = static_ip.get('ipAddress') or ''
            attached_to = static_ip.get('attachedTo') or ''
            logger.info('等待 AWS 固定 IP 迁移生效: order=%s attempt=%s public_ip=%s attached_to=%s', order_no, idx + 1, public_ip, attached_to)
            if public_ip and attached_to == instance_name:
                break
            time.sleep(3)
        if not public_ip:
            return False, '', '固定 IP 已尝试迁移，但未能读取公网 IP。'
        if attached_to != instance_name:
            return False, '', f'固定 IP 尚未迁移到目标实例，当前附加到: {attached_to or "未附加"}。'
        if temp_static_ip_name and temp_static_ip_name != static_ip_name:
            try:
                client.release_static_ip(staticIpName=temp_static_ip_name)
                logger.info('AWS 临时固定 IP 已释放: order=%s temp_static_ip_name=%s', order_no, temp_static_ip_name)
            except Exception as exc:
                logger.warning('AWS 临时固定 IP 释放失败: order=%s temp_static_ip_name=%s error=%s', order_no, temp_static_ip_name, exc)
        return True, public_ip, f'固定 IP {static_ip_name} 已迁移到新实例 {instance_name}。'
    except Exception as exc:
        logger.exception('AWS 固定 IP 迁移失败: order=%s static_ip_name=%s target_instance=%s error=%s', order_no, static_ip_name, instance_name, exc)
        return False, '', f'AWS 固定 IP 迁移失败: {exc}'


@sync_to_async(thread_sensitive=False)
def create_instance(order_data: dict, server_name: str):
    return _create_instance_sync(order_data, server_name)


def _get_instance_public_ip_sync(order_data: dict, instance_name: str) -> str:
    if not instance_name:
        return ''
    client, client_error = _aws_client_from_order_data(order_data)
    if not client:
        logger.warning('AWS 实例公网 IP 查询失败: instance=%s error=%s', instance_name, client_error)
        return ''
    try:
        response = client.get_instance(instanceName=instance_name)
        return ((response.get('instance') or {}).get('publicIpAddress')) or ''
    except Exception as exc:
        logger.warning('AWS 实例公网 IP 查询异常: instance=%s error=%s', instance_name, exc)
        return ''


@sync_to_async(thread_sensitive=False)
def move_static_ip_to_instance(order_data: dict, instance_name: str, static_ip_name: str, temp_static_ip_name: str = ''):
    return _move_static_ip_sync(order_data, instance_name, static_ip_name, temp_static_ip_name)


@sync_to_async(thread_sensitive=False)
def get_instance_public_ip(order_data: dict, instance_name: str):
    return _get_instance_public_ip_sync(order_data, instance_name)
