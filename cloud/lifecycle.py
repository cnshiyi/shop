import logging
import os

from asgiref.sync import sync_to_async
from django.utils import timezone

from biz.models import CloudServerOrder

logger = logging.getLogger(__name__)


def _aws_client(region: str):
    import boto3
    return boto3.client(
        'lightsail',
        region_name=region,
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID', ''),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY', ''),
    )


@sync_to_async
def _get_due_orders():
    now = timezone.now()
    return {
        'expire': list(CloudServerOrder.objects.filter(status='completed', service_expires_at__lte=now)),
        'suspend': list(CloudServerOrder.objects.filter(status__in=['completed', 'expiring', 'renew_pending'], suspend_at__lte=now)),
        'delete': list(CloudServerOrder.objects.filter(status__in=['suspended', 'deleting'], delete_at__lte=now)),
        'recycle': list(CloudServerOrder.objects.filter(status='deleted', ip_recycle_at__lte=now)),
    }


@sync_to_async
def _mark_expiring(order_id: int):
    order = CloudServerOrder.objects.get(id=order_id)
    if order.status == 'completed':
        order.status = 'expiring'
        order.save(update_fields=['status', 'updated_at'])
    return order


@sync_to_async
def _mark_suspended(order_id: int, note: str):
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'suspended'
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    order.save(update_fields=['status', 'provision_note', 'updated_at'])
    return order


@sync_to_async
def _mark_deleted(order_id: int, note: str):
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'deleted'
    order.previous_public_ip = order.public_ip
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    order.instance_id = ''
    order.provider_resource_id = ''
    order.save(update_fields=['status', 'previous_public_ip', 'provision_note', 'instance_id', 'provider_resource_id', 'updated_at'])
    return order


@sync_to_async
def _mark_recycled(order_id: int, note: str):
    order = CloudServerOrder.objects.get(id=order_id)
    order.previous_public_ip = order.public_ip or order.previous_public_ip
    order.public_ip = ''
    order.static_ip_name = ''
    order.mtproxy_host = ''
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    order.save(update_fields=['previous_public_ip', 'public_ip', 'static_ip_name', 'mtproxy_host', 'provision_note', 'updated_at'])
    return order


async def _stop_instance(order: CloudServerOrder) -> str:
    if order.provider != 'aws_lightsail' or not order.server_name:
        return '非 AWS 资源，暂未执行真实关机。'
    try:
        client = _aws_client(order.region_code)
        client.stop_instance(instanceName=order.server_name, force=True)
        return 'AWS 实例已执行关机。'
    except Exception as exc:
        return f'AWS 实例关机失败: {exc}'


async def _delete_instance(order: CloudServerOrder) -> str:
    if order.provider != 'aws_lightsail' or not order.server_name:
        return '非 AWS 资源，暂未执行真实删机。'
    try:
        client = _aws_client(order.region_code)
        client.delete_instance(instanceName=order.server_name)
        return 'AWS 实例已执行删除，固定 IP 继续保留。'
    except Exception as exc:
        return f'AWS 实例删除失败: {exc}'


async def lifecycle_tick(notify=None):
    due = await _get_due_orders()

    for order in due['expire']:
        updated = await _mark_expiring(order.id)
        if notify:
            await notify(updated.user_id, f'⏰ 云服务器即将到期\n订单号: {updated.order_no}\n请尽快续费，未续费将按规则关机/删机。')

    for order in due['suspend']:
        note = await _stop_instance(order)
        updated = await _mark_suspended(order.id, note)
        if notify:
            await notify(updated.user_id, f'⚠️ 云服务器已关机\n订单号: {updated.order_no}\n如需继续使用，请尽快续费。')

    for order in due['delete']:
        note = await _delete_instance(order)
        updated = await _mark_deleted(order.id, note)
        if notify:
            await notify(updated.user_id, f'🗑 云服务器实例已删除\n订单号: {updated.order_no}\n固定 IP 仍保留，可在保留期内续费恢复。')

    for order in due['recycle']:
        updated = await _mark_recycled(order.id, '固定 IP 保留期结束，已释放数据库占位。')
        if notify:
            await notify(updated.user_id, f'📦 云服务器固定 IP 保留期已结束\n订单号: {updated.order_no}')
