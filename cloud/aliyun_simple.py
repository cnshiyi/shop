import asyncio
import os
import secrets
import string
import time
import uuid
from pathlib import Path

from cloud.schemas import ProvisionResult
from core.cloud_accounts import get_active_cloud_account


TIMEOUTS = {
    'connect_ms': 10000,
    'read_ms': 60000,
    'instance_visible_seconds': 600,
    'instance_running_seconds': 900,
}
PROJECT_ROOT = Path(__file__).resolve().parent.parent
KEYPAIR_DIR = PROJECT_ROOT / '.shop-secrets' / 'aliyun-keypairs'


def _rand_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits + '@#%_-'
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _build_client(endpoint: str = 'swas.cn-hangzhou.aliyuncs.com', account=None):
    account = account or get_active_cloud_account('aliyun')
    access_key = account.access_key_plain if account else os.getenv('ALIBABA_CLOUD_ACCESS_KEY_ID', '')
    secret_key = account.secret_key_plain if account else os.getenv('ALIBABA_CLOUD_ACCESS_KEY_SECRET', '')
    if not access_key or not secret_key:
        return None
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_swas_open20200601.client import Client

    config = open_api_models.Config(
        access_key_id=access_key,
        access_key_secret=secret_key,
        endpoint=endpoint,
    )
    return Client(config)


def _runtime_options():
    from alibabacloud_tea_util import models as util_models

    return util_models.RuntimeOptions(
        connect_timeout=TIMEOUTS['connect_ms'],
        read_timeout=TIMEOUTS['read_ms'],
    )


def _region_endpoint(region_code: str) -> str:
    return f'swas.{region_code}.aliyuncs.com'


def _label_index(plan_name: str) -> int:
    labels = ['基础型', '标准型', '增强型', '高配型', '旗舰型', '至尊型']
    try:
        return labels.index(plan_name)
    except ValueError:
        return 0


def _wait_until(predicate, timeout: int, interval: int = 5):
    started = time.time()
    while time.time() - started < timeout:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def _list_linux_plans(client, region_code: str):
    from alibabacloud_swas_open20200601 import models as swas_models

    response = client.list_plans_with_options(
        swas_models.ListPlansRequest(region_id=region_code),
        _runtime_options(),
    )
    plans = response.body.to_map().get('Plans', [])
    linux_plans = [item for item in plans if 'Linux' in str(item.get('SupportPlatform', ''))]
    linux_plans.sort(key=lambda item: (float(str(item.get('OriginPrice') or '0').replace('$', '')), item.get('Core') or 0, item.get('Memory') or 0))
    return linux_plans


def _candidate_plans(client, region_code: str, plan_name: str) -> list[dict]:
    linux_plans = _list_linux_plans(client, region_code)
    if not linux_plans:
        return []
    start = min(_label_index(plan_name), len(linux_plans) - 1)
    preferred_type_order = {'NORMAL': 0, 'INTERNATIONAL': 1, 'MULTI_IP': 2, 'PREVIOUS': 3}
    ordered = linux_plans[start:] + linux_plans[:start]
    ordered.sort(key=lambda item: (preferred_type_order.get(str(item.get('PlanType') or ''), 99), float(str(item.get('OriginPrice') or '0').replace('$', ''))))
    result = []
    seen = set()
    for item in ordered:
        plan_id = item.get('PlanId') or ''
        if plan_id and plan_id not in seen:
            result.append(item)
            seen.add(plan_id)
    return result


def _pick_image(client, region_code: str) -> dict:
    from alibabacloud_swas_open20200601 import models as swas_models

    response = client.list_images_with_options(
        swas_models.ListImagesRequest(region_id=region_code),
        _runtime_options(),
    )
    images = response.body.to_map().get('Images', [])
    for item in images:
        if item.get('Platform') == 'Linux' and str(item.get('ImageName', '')).startswith('Debian-12'):
            return item
    debian_images = [item for item in images if 'debian' in str(item.get('ImageName', '')).lower() and str(item.get('Platform', '')).lower() == 'linux']
    if debian_images:
        debian_images.sort(key=lambda item: item.get('ImageName', ''))
        return debian_images[-1]
    linux_images = [item for item in images if str(item.get('Platform', '')).lower() == 'linux']
    linux_images.sort(key=lambda item: item.get('ImageName', ''))
    return linux_images[-1] if linux_images else {}


def _list_instance(client, region_code: str, instance_id: str):
    from alibabacloud_swas_open20200601 import models as swas_models

    response = client.list_instances_with_options(
        swas_models.ListInstancesRequest(region_id=region_code, page_size=100),
        _runtime_options(),
    )
    instances = response.body.to_map().get('Instances', [])
    return next((item for item in instances if item.get('InstanceId') == instance_id), {})


def _wait_instance_visible(client, region_code: str, instance_id: str):
    return _wait_until(
        lambda: _list_instance(client, region_code, instance_id),
        timeout=TIMEOUTS['instance_visible_seconds'],
        interval=10,
    )


def _wait_running(client, region_code: str, instance_id: str):
    def _check():
        instance = _list_instance(client, region_code, instance_id)
        if instance and str(instance.get('Status') or '') == 'Running':
            return instance
        return None

    return _wait_until(_check, timeout=TIMEOUTS['instance_running_seconds'], interval=10)


def _maybe_secure_private_key(path: Path) -> None:
    if os.name == 'nt':
        return
    path.chmod(0o600)


def _list_mtproxy_keypairs(client, region_code: str) -> list[dict]:
    from alibabacloud_swas_open20200601 import models as swas_models

    response = client.list_key_pairs_with_options(
        swas_models.ListKeyPairsRequest(region_id=region_code, page_size=100),
        _runtime_options(),
    )
    key_pairs = response.body.to_map().get('KeyPairs', [])
    return [item for item in key_pairs if str(item.get('KeyPairName', '')).startswith('openclaw-mtproxy-')]


def _find_reusable_keypair() -> tuple[str, Path] | None:
    if not KEYPAIR_DIR.exists():
        return None
    candidates = []
    for key_path in KEYPAIR_DIR.glob('*.pem'):
        key_name = key_path.stem
        try:
            created_ts = int(key_path.stat().st_mtime)
        except Exception:
            created_ts = 0
        candidates.append((created_ts, key_name, key_path))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, key_name, key_path = candidates[0]
    return key_name, key_path


def _find_private_key_value(value) -> str:
    if isinstance(value, str) and 'PRIVATE KEY' in value:
        return value
    if isinstance(value, dict):
        preferred_keys = ('PrivateKey', 'PrivateKeyBody', 'private_key', 'privateKey', 'KeyPairFingerPrint')
        for key in preferred_keys:
            private_key = _find_private_key_value(value.get(key))
            if private_key:
                return private_key
        for item in value.values():
            private_key = _find_private_key_value(item)
            if private_key:
                return private_key
    if isinstance(value, list):
        for item in value:
            private_key = _find_private_key_value(item)
            if private_key:
                return private_key
    return ''


def _ensure_keypair(client, region_code: str) -> tuple[str, str]:
    from alibabacloud_swas_open20200601 import models as swas_models

    reusable = _find_reusable_keypair()
    if reusable:
        return reusable[0], str(reusable[1])

    KEYPAIR_DIR.mkdir(parents=True, exist_ok=True)
    key_name = f'openclaw-mtproxy-{uuid.uuid4().hex[:8]}'
    key_path = KEYPAIR_DIR / f'{key_name}.pem'
    response = client.create_key_pair_with_options(
        swas_models.CreateKeyPairRequest(region_id=region_code, key_pair_name=key_name),
        _runtime_options(),
    )
    body_map = response.body.to_map() if hasattr(response.body, 'to_map') else {}
    private_key = (
        getattr(response.body, 'private_key_body', '')
        or getattr(response.body, 'private_key', '')
        or _find_private_key_value(body_map)
        or _find_private_key_value(response.body)
    )
    if not private_key:
        return '', ''
    key_path.write_text(private_key, encoding='utf-8')
    _maybe_secure_private_key(key_path)
    return key_name, str(key_path)


def _reset_system_with_password(client, region_code: str, instance_id: str, image_id: str, root_password: str, key_pair_name: str = '') -> None:
    from alibabacloud_swas_open20200601 import models as swas_models

    client.reset_system_with_options(
        swas_models.ResetSystemRequest(
            instance_id=instance_id,
            region_id=region_code,
            image_id=image_id,
            client_token='reset-' + uuid.uuid4().hex,
            login_credentials=swas_models.ResetSystemRequestLoginCredentials(
                key_pair_name=key_pair_name or None,
                password=root_password,
            ),
        ),
        _runtime_options(),
    )


def _open_instance_port(client, region_code: str, instance_id: str, port: int) -> None:
    from alibabacloud_swas_open20200601 import models as swas_models

    for protocol in ('TCP', 'UDP'):
        try:
            client.create_firewall_rule_with_options(
                swas_models.CreateFirewallRuleRequest(
                    region_id=region_code,
                    instance_id=instance_id,
                    firewall_rules=[
                        swas_models.CreateFirewallRuleRequestFirewallRules(
                            port=str(port),
                            protocol=protocol,
                            cidr_ip='0.0.0.0/0',
                            remark=f'openclaw mtproxy {port}',
                        ),
                    ],
                ),
                _runtime_options(),
            )
        except Exception:
            pass


def _create_instance_sync(order, server_name: str):
    region_code = order.region_code or 'cn-hongkong'
    account = getattr(order, 'cloud_account', None) or get_active_cloud_account('aliyun', region_code)
    client = _build_client(_region_endpoint(region_code), account=account)
    if not client:
        return ProvisionResult(ok=False, note='未配置 ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET。')

    try:
        from alibabacloud_swas_open20200601 import models as swas_models

        password = _rand_password()
        candidate_plans = _candidate_plans(client, region_code, order.plan_name)
        image = _pick_image(client, region_code)
        image_id = image.get('ImageId', '')
        if not candidate_plans:
            return ProvisionResult(ok=False, note=f'阿里云轻量云创建失败：未找到可用 Linux 套餐，地区 {region_code}。')
        if not image_id:
            return ProvisionResult(ok=False, note=f'阿里云轻量云创建失败：未找到 Debian/Linux 系统镜像，地区 {region_code}。')

        instance_id = ''
        selected_plan_id = ''
        selected_plan_type = ''
        diagnostics = []
        desired_plan_id = str(getattr(order, 'provider_resource_id', '') or '').strip()
        if desired_plan_id:
            candidate_plans = [item for item in candidate_plans if str(item.get('PlanId') or '').strip() == desired_plan_id] or candidate_plans
            diagnostics.append(f'指定真实 PlanId: {desired_plan_id}')

        for item in candidate_plans[:6]:
            current_plan_id = item.get('PlanId') or ''
            current_plan_type = str(item.get('PlanType') or '')
            current_plan_price = str(item.get('OriginPrice') or '')
            try:
                create_resp = client.create_instances_with_options(
                    swas_models.CreateInstancesRequest(
                        amount=1,
                        charge_type='PrePaid',
                        period=1,
                        plan_id=current_plan_id,
                        image_id=image_id,
                        region_id=region_code,
                        client_token='openclaw-' + uuid.uuid4().hex,
                    ),
                    _runtime_options(),
                )
                instance_ids = create_resp.body.to_map().get('InstanceIds', [])
                if instance_ids:
                    instance_id = instance_ids[0]
                    selected_plan_id = current_plan_id
                    selected_plan_type = current_plan_type
                    diagnostics.append(f'创建成功: {current_plan_id} ({current_plan_type}, {current_plan_price})')
                    break
                diagnostics.append(f'创建无实例ID: {current_plan_id} ({current_plan_type}, {current_plan_price})')
            except Exception as exc:
                message = str(exc)
                diagnostics.append(f'创建失败: {current_plan_id} ({current_plan_type}, {current_plan_price}) -> {message}')
                if 'NotEnoughStock' in message or 'InternalError' in message:
                    continue
                raise

        if not instance_id:
            return ProvisionResult(ok=False, note='阿里云轻量云创建失败:\n' + '\n'.join(diagnostics[-6:]))

        visible_instance = _wait_instance_visible(client, region_code, instance_id)
        if not visible_instance:
            return ProvisionResult(ok=False, note=f'阿里云轻量云创建失败：实例创建后长时间未出现在列表中，instance_id={instance_id}')

        key_name = ''
        key_path = ''
        diagnostics.append('密钥准备跳过：统一使用密码初始化')

        try:
            client.update_instance_attribute_with_options(
                swas_models.UpdateInstanceAttributeRequest(
                    instance_id=instance_id,
                    region_id=region_code,
                    instance_name=server_name,
                ),
                _runtime_options(),
            )
        except Exception as exc:
            diagnostics.append(f'实例命名失败（忽略）: {exc}')

        running_instance = _wait_running(client, region_code, instance_id)
        if not running_instance:
            return ProvisionResult(ok=False, note=f'阿里云轻量云创建失败：实例未进入 Running，instance_id={instance_id}')

        mtproxy_port = int(getattr(order, 'mtproxy_port', 9528) or 9528)
        _open_instance_port(client, region_code, instance_id, 22)
        for offset in range(0, 6):
            _open_instance_port(client, region_code, instance_id, mtproxy_port + offset)

        _reset_system_with_password(client, region_code, instance_id, image_id, password, key_name)
        diagnostics.append('已触发 ResetSystem 下发 root 密码' + (f' / 密钥 {key_name}' if key_name else ''))

        visible_instance = _wait_instance_visible(client, region_code, instance_id)
        if not visible_instance:
            return ProvisionResult(ok=False, note=f'阿里云轻量云重装失败：ResetSystem 后实例未重新出现，instance_id={instance_id}')

        running_instance = _wait_running(client, region_code, instance_id) or _list_instance(client, region_code, instance_id)
        public_ip = (running_instance or {}).get('PublicIpAddress') or ''

        note = (
            f'阿里云轻量云创建成功。实例名: {server_name}，'
            f'套餐: {selected_plan_id} ({selected_plan_type})，镜像: {image.get("ImageName", image_id)}。'
            f'创建链路已对齐 mtproxy-py 的 SWAS 建机 + ResetSystem 方式。'
        )
        return ProvisionResult(
            ok=True,
            instance_id=instance_id,
            public_ip=public_ip,
            login_user='root',
            login_password=password,
            note=note,
            private_key_path='',
        )
    except Exception as exc:
        return ProvisionResult(ok=False, note=f'阿里云轻量云创建异常: {exc}')


async def create_instance(order, server_name: str):
    return await asyncio.to_thread(_create_instance_sync, order, server_name)
