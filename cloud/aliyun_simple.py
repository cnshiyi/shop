import os
import secrets
import string
import time
import uuid

from cloud.schemas import ProvisionResult


TIMEOUTS = {
    'connect_ms': 10000,
    'read_ms': 30000,
    'instance_visible_seconds': 600,
    'instance_running_seconds': 900,
}


def _rand_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits + '@#%_-'
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _build_client(endpoint: str = 'swas.cn-hangzhou.aliyuncs.com'):
    access_key = os.getenv('ALIBABA_CLOUD_ACCESS_KEY_ID', '')
    secret_key = os.getenv('ALIBABA_CLOUD_ACCESS_KEY_SECRET', '')
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


async def create_instance(order, server_name: str):
    region_code = order.region_code or 'cn-hongkong'
    client = _build_client(_region_endpoint(region_code))
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

        running_instance = _wait_running(client, region_code, instance_id) or _list_instance(client, region_code, instance_id)
        public_ip = (running_instance or {}).get('PublicIpAddress') or ''

        note = (
            f'阿里云轻量云创建成功。实例名: {server_name}，'
            f'套餐: {selected_plan_id} ({selected_plan_type})，镜像: {image.get("ImageName", image_id)}。'
            f'创建链路已对齐 mtproxy-py 的阿里云建机方式；密码后续由 SSH/重装流程处理。'
        )
        return ProvisionResult(
            ok=True,
            instance_id=instance_id,
            public_ip=public_ip,
            login_user='root',
            login_password=password,
            note=note,
        )
    except Exception as exc:
        return ProvisionResult(ok=False, note=f'阿里云轻量云创建异常: {exc}')
