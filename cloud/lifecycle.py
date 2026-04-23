import logging
import os

from aiogram.types import InlineKeyboardMarkup
from asgiref.sync import sync_to_async
from django.core.management import call_command
from django.utils import timezone

from core.models import CloudAccountConfig

from bot.models import TelegramUser
from cloud.models import CloudAsset, CloudServerOrder, Server
from bot.keyboards import cloud_expiry_actions

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
    renew_notice_at = now + timezone.timedelta(days=5)
    delete_notice_at = now + timezone.timedelta(days=1)
    recycle_notice_at = now + timezone.timedelta(days=1)
    return {
        'renew_notice': list(CloudServerOrder.objects.filter(status__in=['completed', 'expiring', 'renew_pending'], service_expires_at__lte=renew_notice_at, service_expires_at__gt=now, renew_notice_sent_at__isnull=True)),
        'delete_notice': list(CloudServerOrder.objects.filter(status__in=['suspended', 'deleting'], delete_at__lte=delete_notice_at, delete_at__gt=now, delete_notice_sent_at__isnull=True)),
        'recycle_notice': list(CloudServerOrder.objects.filter(status='deleted', ip_recycle_at__lte=recycle_notice_at, ip_recycle_at__gt=now, recycle_notice_sent_at__isnull=True)),
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
        CloudAsset.objects.filter(order=order).update(updated_at=timezone.now())
        Server.objects.filter(order=order).update(updated_at=timezone.now())
    return order


@sync_to_async
def _mark_suspended(order_id: int, note: str):
    now = timezone.now()
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'suspended'
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    order.save(update_fields=['status', 'provision_note', 'updated_at'])
    CloudAsset.objects.filter(order=order).update(is_active=False, note=order.provision_note, updated_at=now)
    Server.objects.filter(order=order).update(is_active=False, note=order.provision_note, updated_at=now)
    return order


@sync_to_async
def _mark_deleted(order_id: int, note: str):
    now = timezone.now()
    order = CloudServerOrder.objects.get(id=order_id)
    previous_public_ip = order.public_ip
    order.status = 'deleted'
    order.previous_public_ip = previous_public_ip
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    order.instance_id = ''
    order.provider_resource_id = ''
    order.save(update_fields=['status', 'previous_public_ip', 'provision_note', 'instance_id', 'provider_resource_id', 'updated_at'])
    CloudAsset.objects.filter(order=order).update(
        previous_public_ip=previous_public_ip,
        instance_id=None,
        provider_resource_id=None,
        is_active=False,
        note=order.provision_note,
        updated_at=now,
    )
    Server.objects.filter(order=order).update(
        previous_public_ip=previous_public_ip,
        instance_id=None,
        provider_resource_id=None,
        is_active=False,
        note=order.provision_note,
        updated_at=now,
    )
    return order


@sync_to_async
def _mark_recycled(order_id: int, note: str):
    now = timezone.now()
    order = CloudServerOrder.objects.get(id=order_id)
    previous_public_ip = order.public_ip or order.previous_public_ip
    order.previous_public_ip = previous_public_ip
    order.public_ip = ''
    order.static_ip_name = ''
    order.mtproxy_host = ''
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    order.save(update_fields=['previous_public_ip', 'public_ip', 'static_ip_name', 'mtproxy_host', 'provision_note', 'updated_at'])
    CloudAsset.objects.filter(order=order).update(
        previous_public_ip=previous_public_ip,
        public_ip=None,
        mtproxy_host=None,
        note=order.provision_note,
        updated_at=now,
    )
    Server.objects.filter(order=order).update(
        previous_public_ip=previous_public_ip,
        public_ip=None,
        note=order.provision_note,
        updated_at=now,
    )
    return order




@sync_to_async
def _mark_notice_sent(order_id: int, field_name: str):
    CloudServerOrder.objects.filter(id=order_id).update(**{field_name: timezone.now(), 'updated_at': timezone.now()})


@sync_to_async
def _user_can_receive_cloud_notice(user_id: int) -> bool:
    user = TelegramUser.objects.filter(id=user_id).first()
    if not user:
        return False
    muted_until = getattr(user, 'cloud_reminder_muted_until', None)
    return not muted_until or muted_until <= timezone.now()


@sync_to_async
def _get_migration_due_orders():
    now = timezone.now()
    return list(
        CloudServerOrder.objects.filter(
            replacement_orders__isnull=False,
            migration_due_at__lte=now,
        ).exclude(status__in=['deleted'])
    )


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


async def _delete_replaced_server(order: CloudServerOrder) -> str:
    if order.provider != 'aws_lightsail' or not order.server_name:
        return '迁移期结束，旧服务器已标记删除。'
    try:
        client = _aws_client(order.region_code)
        client.delete_instance(instanceName=order.server_name)
        return '迁移期结束，旧 AWS 实例已删除。'
    except Exception as exc:
        return f'迁移期结束，旧实例删除失败: {exc}'


@sync_to_async
def _mark_replaced_order_deleted(order_id: int, note: str):
    now = timezone.now()
    order = CloudServerOrder.objects.get(id=order_id)
    previous_public_ip = order.public_ip or order.previous_public_ip
    order.status = 'deleted'
    order.previous_public_ip = previous_public_ip
    order.instance_id = ''
    order.provider_resource_id = ''
    order.public_ip = ''
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    order.save(update_fields=['status', 'previous_public_ip', 'instance_id', 'provider_resource_id', 'public_ip', 'provision_note', 'updated_at'])
    CloudAsset.objects.filter(order=order).update(is_active=False, public_ip=None, previous_public_ip=previous_public_ip, note=order.provision_note, updated_at=now)
    Server.objects.filter(order=order).update(is_active=False, public_ip=None, previous_public_ip=previous_public_ip, note=order.provision_note, updated_at=now)
    return order


@sync_to_async
def check_cloud_accounts_status(queryset=None):
    items = list(queryset if queryset is not None else CloudAccountConfig.objects.order_by('provider', 'name', 'id'))
    results = []
    for item in items:
        status = CloudAccountConfig.STATUS_OK
        note = '验证成功'
        try:
            if item.provider == CloudAccountConfig.PROVIDER_AWS:
                import boto3
                client = boto3.client(
                    'lightsail',
                    region_name=item.region_hint or 'ap-southeast-1',
                    aws_access_key_id=item.access_key_plain,
                    aws_secret_access_key=item.secret_key_plain,
                )
                response = client.get_instances()
                count = len(response.get('instances') or [])
                note = f'验证成功，实例数 {count}，地区 {item.region_hint or "ap-southeast-1"}'
            elif item.provider == CloudAccountConfig.PROVIDER_ALIYUN:
                from alibabacloud_swas_open20200601 import models as swas_models
                from cloud.aliyun_simple import _region_endpoint, _runtime_options
                from cloud.aliyun_simple import _build_client as _default_build_client
                old_key = os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_ID')
                old_secret = os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_SECRET')
                try:
                    os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'] = item.access_key_plain
                    os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'] = item.secret_key_plain
                    client = _default_build_client(_region_endpoint(item.region_hint or 'cn-hongkong'))
                    if not client:
                        raise ValueError('无法创建阿里云客户端')
                    response = client.list_instances_with_options(
                        swas_models.ListInstancesRequest(region_id=item.region_hint or 'cn-hongkong', page_size=1),
                        _runtime_options(),
                    )
                    count = len(response.body.to_map().get('Instances', []) or [])
                    note = f'验证成功，实例数 {count}，地区 {item.region_hint or "cn-hongkong"}'
                finally:
                    if old_key is None:
                        os.environ.pop('ALIBABA_CLOUD_ACCESS_KEY_ID', None)
                    else:
                        os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'] = old_key
                    if old_secret is None:
                        os.environ.pop('ALIBABA_CLOUD_ACCESS_KEY_SECRET', None)
                    else:
                        os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'] = old_secret
            else:
                status = CloudAccountConfig.STATUS_UNSUPPORTED
                note = '暂不支持该平台巡检'
        except Exception as exc:
            status = CloudAccountConfig.STATUS_ERROR
            note = str(exc)
        item.mark_status(status, note)
        results.append({'id': item.id, 'provider': item.provider, 'name': item.name, 'status': status, 'note': note})
    return results


async def sync_server_status_tick():
    regions = [
        ('aliyun_simple', os.getenv('ALIYUN_REGION', 'cn-hongkong') or 'cn-hongkong'),
        ('aws_lightsail', os.getenv('AWS_REGION', 'ap-southeast-1') or 'ap-southeast-1'),
    ]
    for provider, region in regions:
        try:
            if provider == 'aliyun_simple':
                await sync_to_async(call_command)('sync_aliyun_assets', region=region)
            else:
                await sync_to_async(call_command)('sync_aws_assets', region=region)
            logger.info('云服务器状态同步完成: provider=%s region=%s', provider, region)
        except Exception as exc:
            logger.warning('云服务器状态同步失败: provider=%s region=%s error=%s', provider, region, exc)


async def sync_cloud_accounts_tick():
    try:
        results = await check_cloud_accounts_status()
        logger.info('云账号状态巡检完成: total=%s ok=%s error=%s', len(results), len([x for x in results if x['status'] == 'ok']), len([x for x in results if x['status'] == 'error']))
    except Exception as exc:
        logger.warning('云账号状态巡检失败: %s', exc)


async def lifecycle_tick(notify=None):
    due = await _get_due_orders()
    migration_due_orders = await _get_migration_due_orders()

    for order in due['renew_notice']:
        if notify and await _user_can_receive_cloud_notice(order.user_id):
            await notify(
                order.user_id,
                f'⏰ 云服务器到期提醒\n订单号: {order.order_no}\n到期时间: {order.service_expires_at}\n将在到期后进入宽限与删机流程，请及时处理。',
                cloud_expiry_actions(order.id),
            )
        await _mark_notice_sent(order.id, 'renew_notice_sent_at')

    for order in due['delete_notice']:
        if notify and await _user_can_receive_cloud_notice(order.user_id):
            await notify(order.user_id, f'⚠️ 云服务器删机提醒\n订单号: {order.order_no}\n计划删机时间: {order.delete_at}\n如需保留，请尽快处理。')
        await _mark_notice_sent(order.id, 'delete_notice_sent_at')

    for order in due['recycle_notice']:
        if notify and await _user_can_receive_cloud_notice(order.user_id):
            await notify(order.user_id, f'📦 固定IP删除提醒\n订单号: {order.order_no}\n计划删除IP时间: {order.ip_recycle_at}\n如需保留，请尽快处理。')
        await _mark_notice_sent(order.id, 'recycle_notice_sent_at')

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

    for order in migration_due_orders:
        note = await _delete_replaced_server(order)
        updated = await _mark_replaced_order_deleted(order.id, note)
        if notify:
            await notify(updated.user_id, f'🧹 迁移期已结束，旧服务器已删除\n订单号: {updated.order_no}')
