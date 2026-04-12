import os
import secrets
import string
import time

from cloud.schemas import ProvisionResult


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


def _region_endpoint(region_code: str) -> str:
    return f'swas.{region_code}.aliyuncs.com'


def _label_index(plan_name: str) -> int:
    labels = ['基础型', '标准型', '增强型', '高配型', '旗舰型', '至尊型']
    try:
        return labels.index(plan_name)
    except ValueError:
        return 0


def _list_linux_plans(client, region_code: str):
    from alibabacloud_swas_open20200601 import models as swas_models

    response = client.list_plans(swas_models.ListPlansRequest(region_id=region_code))
    plans = response.body.to_map().get('Plans', [])
    linux_plans = [item for item in plans if 'Linux' in str(item.get('SupportPlatform', ''))]
    linux_plans.sort(key=lambda item: (float(str(item.get('OriginPrice') or '0').replace('$', '')), item.get('Core') or 0, item.get('Memory') or 0))
    return linux_plans


def _pick_plan_id(client, region_code: str, plan_name: str) -> str:
    linux_plans = _list_linux_plans(client, region_code)
    index = min(_label_index(plan_name), max(len(linux_plans) - 1, 0))
    return (linux_plans[index] or {}).get('PlanId', '') if linux_plans else ''


def _candidate_plan_ids(client, region_code: str, plan_name: str) -> list[dict]:
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


def _pick_image_id(client, region_code: str) -> str:
    from alibabacloud_swas_open20200601 import models as swas_models

    response = client.list_images(swas_models.ListImagesRequest(region_id=region_code, image_type='system'))
    images = response.body.to_map().get('Images', [])
    debian_images = [item for item in images if 'debian' in str(item.get('ImageName', '')).lower() and str(item.get('Platform', '')).lower() == 'linux']
    if debian_images:
        debian_images.sort(key=lambda item: item.get('ImageName', ''))
        return debian_images[-1].get('ImageId', '')
    linux_images = [item for item in images if str(item.get('Platform', '')).lower() == 'linux']
    linux_images.sort(key=lambda item: item.get('ImageName', ''))
    return linux_images[-1].get('ImageId', '') if linux_images else ''


def _find_instance(client, region_code: str, instance_id: str):
    from alibabacloud_swas_open20200601 import models as swas_models

    response = client.list_instances(swas_models.ListInstancesRequest(region_id=region_code, instance_ids=f'["{instance_id}"]'))
    instances = response.body.to_map().get('Instances', [])
    return instances[0] if instances else {}


async def create_instance(order, server_name: str):
    region_code = order.region_code or 'cn-hongkong'
    client = _build_client(_region_endpoint(region_code))
    if not client:
        return ProvisionResult(ok=False, note='未配置 ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET。')

    try:
        from alibabacloud_swas_open20200601 import models as swas_models

        password = _rand_password()
        candidate_plans = _candidate_plan_ids(client, region_code, order.plan_name)
        plan_id = (candidate_plans[0] or {}).get('PlanId', '') if candidate_plans else ''
        image_id = _pick_image_id(client, region_code)
        if not plan_id:
            return ProvisionResult(ok=False, note=f'阿里云轻量云创建失败：未找到可用套餐 plan_id，地区 {region_code}。')
        if not image_id:
            return ProvisionResult(ok=False, note=f'阿里云轻量云创建失败：未找到 Debian/Linux 系统镜像，地区 {region_code}。')

        instance_id = ''
        selected_plan_id = ''
        selected_plan_type = ''
        diagnostics = []
        last_error = ''
        for item in candidate_plans[:6]:
            current_plan_id = item.get('PlanId') or ''
            current_plan_type = str(item.get('PlanType') or '')
            current_plan_price = str(item.get('OriginPrice') or '')
            try:
                create_resp = client.create_instances(
                    swas_models.CreateInstancesRequest(
                        amount=1,
                        charge_type='PrePaid',
                        period=1,
                        plan_id=current_plan_id,
                        image_id=image_id,
                        region_id=region_code,
                        client_token=f'{server_name}-{current_plan_id}'[:64],
                    )
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
                last_error = str(exc)
                diagnostics.append(f'创建失败: {current_plan_id} ({current_plan_type}, {current_plan_price}) -> {last_error}')
                if 'NotEnoughStock' in last_error or 'InternalError' in last_error:
                    continue
                raise

        if not instance_id:
            return ProvisionResult(ok=False, note='阿里云轻量云创建失败:\n' + '\n'.join(diagnostics[-6:]))

        for _ in range(60):
            instance = _find_instance(client, region_code, instance_id)
            status = str(instance.get('Status') or '').lower()
            if status in {'running', 'starting', 'pending', 'stopped'}:
                break
            time.sleep(5)

        client.update_instance_attribute(
            swas_models.UpdateInstanceAttributeRequest(
                region_id=region_code,
                instance_id=instance_id,
                instance_name=server_name,
                password=password,
                client_token=f'{server_name}-attr',
            )
        )

        public_ip = ''
        login_user = 'root'
        for _ in range(90):
            instance = _find_instance(client, region_code, instance_id)
            public_ip = instance.get('PublicIpAddress') or ''
            status = str(instance.get('Status') or '').lower()
            if public_ip and status in {'running', 'starting'}:
                break
            time.sleep(5)

        note = (
            f'阿里云轻量云创建成功。实例名: {server_name}，'
            f'套餐: {selected_plan_id} ({selected_plan_type})，镜像: {image_id}，已按阿里云在线创建流程设置实例名和密码。'
        )
        return ProvisionResult(
            ok=True,
            instance_id=instance_id,
            public_ip=public_ip,
            login_user=login_user,
            login_password=password,
            note=note,
        )
    except Exception as exc:
        return ProvisionResult(ok=False, note=f'阿里云轻量云创建异常: {exc}')
