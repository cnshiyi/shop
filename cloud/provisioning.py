from asgiref.sync import sync_to_async

from biz.models import CloudServerOrder
from biz.services import build_cloud_server_name
from cloud.aliyun_simple import create_instance as create_aliyun_instance
from cloud.aws_lightsail import create_instance as create_aws_instance
from cloud.bootstrap import install_bbr, install_mtproxy


async def provision_cloud_server(order_id: int):
    order = await _get_order(order_id)
    if not order:
        return None
    server_name = build_cloud_server_name(order.user_id, order.pay_amount)
    if order.provider == 'aws_lightsail':
        result = await create_aws_instance(order, server_name)
        login_user = 'admin'
    else:
        result = await create_aliyun_instance(order, server_name)
        login_user = 'root'
    if result.ok:
        _, bbr_note = await install_bbr(result.public_ip, result.login_user or login_user, result.login_password)
        _, mtproxy_note = await install_mtproxy(result.public_ip, result.login_user or login_user, result.login_password, order.mtproxy_port)
        note = '\n'.join(part for part in [result.note, bbr_note, mtproxy_note] if part)
        return await _mark_success(order_id, result.instance_id, result.public_ip, result.login_user or login_user, result.login_password, note)
    return await _mark_failed(order_id, result.note)


@sync_to_async
def _get_order(order_id: int):
    return CloudServerOrder.objects.filter(id=order_id).first()


@sync_to_async
def _mark_success(order_id: int, instance_id: str, public_ip: str, login_user: str, login_password: str, note: str):
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'completed'
    order.instance_id = instance_id
    order.public_ip = public_ip
    order.login_user = login_user
    order.login_password = login_password
    order.provision_note = note
    from django.utils import timezone
    order.completed_at = timezone.now()
    order.save(update_fields=['status', 'instance_id', 'public_ip', 'login_user', 'login_password', 'provision_note', 'completed_at', 'updated_at'])
    return order


@sync_to_async
def _mark_failed(order_id: int, note: str):
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'failed'
    order.provision_note = note
    order.save(update_fields=['status', 'provision_note', 'updated_at'])
    return order
