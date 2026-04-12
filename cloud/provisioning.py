from asgiref.sync import sync_to_async
import logging
from django.db import close_old_connections

from biz.models import CloudServerOrder
from biz.services import build_cloud_server_name
from cloud.aliyun_simple import create_instance as create_aliyun_instance
from cloud.aws_lightsail import create_instance as create_aws_instance
from cloud.bootstrap import install_bbr, install_mtproxy

logger = logging.getLogger(__name__)


def _extract_mtproxy_fields(note: str) -> tuple[str, str, str]:
    link = ''
    secret = ''
    host = ''
    for line in (note or '').splitlines():
        if line.startswith('TG链接: '):
            link = line.split(': ', 1)[1].strip()
        elif 'secret=' in line and not link:
            link = line.strip()
    if 'server=' in link:
        host = link.split('server=', 1)[1].split('&', 1)[0]
    if 'secret=' in link:
        secret = link.split('secret=', 1)[1].split('&', 1)[0]
    return link, secret, host


async def provision_cloud_server(order_id: int):
    order = await _get_order(order_id)
    if not order:
        return None
    server_name = build_cloud_server_name(order.user_id, order.pay_amount)
    logger.info('云服务器开通开始: order=%s provider=%s region=%s qty=%s port=%s', order.order_no, order.provider, order.region_code, order.quantity, order.mtproxy_port)
    if order.provider == 'aws_lightsail':
        result = await create_aws_instance(order, server_name)
        login_user = 'admin'
    else:
        result = await create_aliyun_instance(order, server_name)
        login_user = 'root'
    if result.ok:
        bbr_ok, bbr_note = await install_bbr(result.public_ip, result.login_user or login_user, result.login_password)
        mtproxy_ok, mtproxy_note = await install_mtproxy(result.public_ip, result.login_user or login_user, result.login_password, order.mtproxy_port)
        note = '\n'.join(part for part in [result.note, bbr_note, mtproxy_note] if part)
        if not bbr_ok or not mtproxy_ok:
            logger.warning('云服务器开通失败: order=%s provider=%s reason=bootstrap_failed', order.order_no, order.provider)
            return await _mark_failed(order_id, note)
        saved = await _mark_success(
            order_id,
            server_name,
            result.instance_id,
            result.public_ip,
            result.login_user or login_user,
            result.login_password,
            note,
        )
        logger.info('云服务器开通完成: order=%s provider=%s region=%s port=%s', saved.order_no, saved.provider, saved.region_code, saved.mtproxy_port)
        return saved
    logger.warning('云服务器开通失败: order=%s provider=%s reason=create_failed', order.order_no, order.provider)
    return await _mark_failed(order_id, result.note)


@sync_to_async
def _get_order(order_id: int):
    close_old_connections()
    return CloudServerOrder.objects.filter(id=order_id).first()


@sync_to_async
def _mark_success(order_id: int, server_name: str, instance_id: str, public_ip: str, login_user: str, login_password: str, note: str):
    close_old_connections()
    order = CloudServerOrder.objects.get(id=order_id)
    mtproxy_link, mtproxy_secret, mtproxy_host = _extract_mtproxy_fields(note)
    order.status = 'completed'
    order.server_name = server_name
    order.instance_id = instance_id
    order.provider_resource_id = instance_id
    order.public_ip = public_ip
    order.mtproxy_host = mtproxy_host or public_ip
    order.mtproxy_link = mtproxy_link
    order.mtproxy_secret = mtproxy_secret
    order.login_user = login_user
    order.login_password = login_password
    order.provision_note = note
    from django.utils import timezone
    order.completed_at = timezone.now()
    order.last_user_id = order.user.tg_user_id
    order.save(update_fields=['status', 'server_name', 'instance_id', 'provider_resource_id', 'public_ip', 'mtproxy_host', 'mtproxy_link', 'mtproxy_secret', 'login_user', 'login_password', 'provision_note', 'completed_at', 'last_user_id', 'updated_at'])
    return order


@sync_to_async
def _mark_failed(order_id: int, note: str):
    close_old_connections()
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'failed'
    order.provision_note = note
    order.save(update_fields=['status', 'provision_note', 'updated_at'])
    return order
