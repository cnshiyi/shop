from asgiref.sync import sync_to_async
import logging
from django.utils import timezone

from django.db.models import Q

from cloud.models import CloudAsset, Server
from cloud.services import build_cloud_server_name, ensure_unique_cloud_server_name, record_cloud_ip_log
from cloud.aliyun_simple import create_instance as create_aliyun_instance
from cloud.aws_lightsail import create_instance as create_aws_instance
from cloud.bootstrap import install_bbr, install_mtproxy
from cloud.models import CloudServerOrder

logger = logging.getLogger(__name__)


def _extract_mtproxy_fields(note: str) -> tuple[str, str, str]:
    link = ''
    secret = ''
    host = ''
    for raw_line in (note or '').splitlines():
        line = raw_line.strip()
        if line.startswith('TG链接: '):
            link = line.split(': ', 1)[1].strip()
            break
    if 'server=' in link:
        host = link.split('server=', 1)[1].split('&', 1)[0].strip()
    if 'secret=' in link:
        secret = link.split('secret=', 1)[1].split('&', 1)[0].strip()
    return link, secret, host


def _upsert_server_record(order: CloudServerOrder, note: str):
    try:
        order_user = order.user
    except Exception:
        order_user = None
    lookup = Q()
    if order.instance_id:
        lookup |= Q(instance_id=order.instance_id)
    if order.provider_resource_id:
        lookup |= Q(provider_resource_id=order.provider_resource_id)
    if order.public_ip:
        lookup |= Q(public_ip=order.public_ip)
    if order.id:
        lookup |= Q(order=order)
    if lookup:
        server_record = Server.objects.filter(lookup).order_by('-updated_at', '-id').first()
    else:
        server_record = None
    defaults = {
            'source': Server.SOURCE_ORDER,
            'provider': order.provider,
            'account_label': order.provider,
            'region_code': order.region_code,
            'region_name': order.region_name,
            'server_name': order.server_name,
            'instance_id': order.instance_id,
            'provider_resource_id': order.provider_resource_id or order.instance_id,
            'public_ip': order.public_ip,
            'previous_public_ip': order.previous_public_ip,
            'login_user': order.login_user,
            'login_password': order.login_password,
            'expires_at': order.service_expires_at,
            'order': order,
            'user': order_user,
            'note': note,
            'status': Server.STATUS_RUNNING if order.status in {'completed', 'expiring', 'renew_pending', 'suspended'} else Server.STATUS_PENDING,
            'is_active': order.status in {'provisioning', 'completed', 'expiring', 'renew_pending', 'suspended'},
        }
    if server_record:
        for key, value in defaults.items():
            setattr(server_record, key, value)
        server_record.save()
        return server_record
    return Server.objects.create(**defaults)


@sync_to_async
def _build_unique_server_name(tg_user_id: int | None, pay_amount):
    return ensure_unique_cloud_server_name(build_cloud_server_name(tg_user_id, pay_amount))


@sync_to_async
def _get_order_tg_user_id(order: CloudServerOrder):
    try:
        return getattr(order.user, 'tg_user_id', None)
    except Exception:
        return None


@sync_to_async
def _get_aws_order_payload(order: CloudServerOrder):
    return {
        'order_no': order.order_no,
        'provider': order.provider,
        'region_code': order.region_code,
        'plan_name': order.plan_name,
        'mtproxy_port': order.mtproxy_port,
        'static_ip_name': order.static_ip_name,
    }


@sync_to_async
def _mark_provisioning_start(order_id: int, server_name: str):
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'provisioning'
    order.server_name = server_name
    order.provision_note = '\n'.join(filter(None, [order.provision_note, f'开始创建服务器：{server_name}']))
    order.save(update_fields=['status', 'server_name', 'provision_note', 'updated_at'])
    try:
        order_user = order.user
    except Exception:
        order_user = None
    server_asset, _ = CloudAsset.objects.update_or_create(
        order=order,
        kind=CloudAsset.KIND_SERVER,
        defaults={
            'source': CloudAsset.SOURCE_ORDER,
            'provider': order.provider,
            'region_code': order.region_code,
            'region_name': order.region_name,
            'asset_name': server_name,
            'instance_id': order.instance_id,
            'provider_resource_id': order.provider_resource_id,
            'public_ip': order.public_ip,
            'mtproxy_port': order.mtproxy_port,
            'mtproxy_link': order.mtproxy_link,
            'mtproxy_secret': order.mtproxy_secret,
            'mtproxy_host': order.mtproxy_host,
            'actual_expires_at': order.service_expires_at,
            'price': order.total_amount,
            'currency': order.currency,
            'order': order,
            'user': order_user,
            'note': order.provision_note,
            'status': CloudAsset.STATUS_PENDING,
            'is_active': True,
        },
    )
    server_record = _upsert_server_record(order, order.provision_note)
    record_cloud_ip_log(event_type='created', order=order, asset=server_asset, server=server_record, public_ip=order.public_ip, note=f'服务器开始创建：{server_name}')
    return order


async def provision_cloud_server(order_id: int):
    started_at = timezone.now()
    logger.info('云服务器开通开始: order_id=%s', order_id)
    try:
        order = await _get_order(order_id)
        if not order:
            logger.warning('云服务器开通失败: 订单不存在 order_id=%s', order_id)
            return None

        order_tg_user_id = await _get_order_tg_user_id(order)

        server_name = await _build_unique_server_name(order_tg_user_id, order.pay_amount)
        logger.info(
            '云服务器开通准备完成: order_id=%s order_no=%s status=%s provider=%s region=%s plan=%s qty=%s currency=%s pay_amount=%s mtproxy_port=%s server_name=%s user_id=%s tg_user_id=%s',
            order.id,
            order.order_no,
            order.status,
            order.provider,
            order.region_code,
            order.plan_name,
            order.quantity,
            order.currency,
            order.pay_amount,
            order.mtproxy_port,
            server_name,
            order.user_id,
            order_tg_user_id,
        )

        await _mark_provisioning_start(order.id, server_name)

        if order.provider == 'aws_lightsail':
            logger.info('云服务器创建开始: order=%s provider=AWS Lightsail server_name=%s', order.order_no, server_name)
            result = await create_aws_instance(await _get_aws_order_payload(order), server_name)
            login_user = 'admin'
        else:
            logger.info('云服务器创建开始: order=%s provider=%s server_name=%s', order.order_no, order.provider, server_name)
            result = await create_aliyun_instance(order, server_name)
            login_user = 'root'

        logger.info(
            '云服务器创建结果: order=%s ok=%s instance_id=%s public_ip=%s login_user=%s note=%s',
            order.order_no,
            result.ok,
            result.instance_id,
            result.public_ip,
            result.login_user or login_user,
            (result.note or '')[:1000],
        )

        if result.ok:
            bootstrap_user = result.login_user or login_user
            logger.info('开始执行 BBR 初始化: order=%s public_ip=%s user=%s requested_user=%s', order.order_no, result.public_ip, bootstrap_user, bootstrap_user)
            bbr_ok, bbr_note = await install_bbr(result.public_ip, bootstrap_user, result.login_password)
            logger.info('BBR 初始化结果: order=%s ok=%s note=%s', order.order_no, bbr_ok, (bbr_note or '')[:1000])

            logger.info('开始执行 MTProxy 安装: order=%s public_ip=%s user=%s port=%s requested_user=%s', order.order_no, result.public_ip, bootstrap_user, order.mtproxy_port, bootstrap_user)
            mtproxy_ok, mtproxy_note = await install_mtproxy(result.public_ip, bootstrap_user, result.login_password, order.mtproxy_port, order.mtproxy_secret or '')
            logger.info('MTProxy 安装结果: order=%s ok=%s note=%s', order.order_no, mtproxy_ok, (mtproxy_note or '')[:1000])

            note = '\n'.join(part for part in [result.note, bbr_note, mtproxy_note] if part)
            if not bbr_ok or not mtproxy_ok:
                logger.warning(
                    '云服务器开通失败: order=%s reason=bootstrap_failed bbr_ok=%s mtproxy_ok=%s elapsed_seconds=%s',
                    order.order_no,
                    bbr_ok,
                    mtproxy_ok,
                    (timezone.now() - started_at).total_seconds(),
                )
                saved = await _mark_failed(order_id, note)
                logger.warning('云服务器开通结束: order=%s status=%s note=%s', saved.order_no, saved.status, (saved.provision_note or '')[:1500])
                print('[PROVISION_RESULT]', {'order_id': saved.id, 'order_no': saved.order_no, 'status': saved.status, 'error': saved.provision_note})
                return saved

            saved = await _mark_success(
                order_id,
                server_name,
                result.instance_id,
                result.public_ip,
                result.login_user or login_user,
                result.login_password,
                note,
                getattr(result, 'static_ip_name', '') or '',
            )
            logger.info(
                '云服务器开通成功: order=%s status=%s provider=%s region=%s server_name=%s instance_id=%s public_ip=%s mtproxy_host=%s mtproxy_port=%s mtproxy_link=%s expires_at=%s elapsed_seconds=%s',
                saved.order_no,
                saved.status,
                saved.provider,
                saved.region_code,
                saved.server_name,
                saved.instance_id,
                saved.public_ip,
                saved.mtproxy_host,
                saved.mtproxy_port,
                saved.mtproxy_link,
                saved.service_expires_at,
                (timezone.now() - started_at).total_seconds(),
            )
            print(
                '[PROVISION_RESULT]',
                {
                    'order_id': saved.id,
                    'order_no': saved.order_no,
                    'status': saved.status,
                    'provider': saved.provider,
                    'region': saved.region_code,
                    'server_name': saved.server_name,
                    'instance_id': saved.instance_id,
                    'public_ip': saved.public_ip,
                    'mtproxy_port': saved.mtproxy_port,
                    'mtproxy_link': saved.mtproxy_link,
                    'service_expires_at': saved.service_expires_at.isoformat() if saved.service_expires_at else None,
                },
            )
            return saved

        logger.warning(
            '云服务器开通失败: order=%s reason=create_failed note=%s elapsed_seconds=%s',
            order.order_no,
            (result.note or '')[:1500],
            (timezone.now() - started_at).total_seconds(),
        )
        saved = await _mark_failed(order_id, result.note)
        logger.warning('云服务器开通结束: order=%s status=%s note=%s', saved.order_no, saved.status, (saved.provision_note or '')[:1500])
        print('[PROVISION_RESULT]', {'order_id': saved.id, 'order_no': saved.order_no, 'status': saved.status, 'error': saved.provision_note})
        return saved
    except Exception as exc:
        logger.exception('云服务器开通异常: order_id=%s error=%s', order_id, exc)
        try:
            saved = await _mark_failed(order_id, f'云服务器开通异常: {exc}')
            logger.warning('云服务器开通异常结束: order=%s status=%s note=%s', saved.order_no, saved.status, (saved.provision_note or '')[:1500])
            print('[PROVISION_RESULT]', {'order_id': saved.id, 'order_no': saved.order_no, 'status': saved.status, 'error': saved.provision_note})
            return saved
        except Exception:
            logger.exception('云服务器开通异常后回写失败: order_id=%s', order_id)
            raise


async def reprovision_cloud_server_bootstrap(order_id: int):
    order = await _get_order(order_id)
    if not order:
        return None
    if not order.public_ip or not order.login_password:
        return await _mark_failed(order_id, '重试初始化失败：缺少公网 IP 或登录密码。')
    bootstrap_user = order.login_user or 'root'
    logger.info('[PROVISION][RETRY] start order=%s public_ip=%s user=%s port=%s', order.order_no, order.public_ip, bootstrap_user, order.mtproxy_port)
    bbr_ok, bbr_note = await install_bbr(order.public_ip, bootstrap_user, order.login_password)
    logger.info('[PROVISION][RETRY] bbr_result order=%s ok=%s note=%s', order.order_no, bbr_ok, (bbr_note or '')[:1000])
    mtproxy_ok, mtproxy_note = await install_mtproxy(order.public_ip, bootstrap_user, order.login_password, order.mtproxy_port)
    logger.info('[PROVISION][RETRY] mtproxy_result order=%s ok=%s note=%s', order.order_no, mtproxy_ok, (mtproxy_note or '')[:1000])
    note = '\n'.join(part for part in [order.provision_note, '已执行重试初始化。', bbr_note, mtproxy_note] if part)
    if not bbr_ok or not mtproxy_ok:
        return await _mark_failed(order_id, note)
    return await _mark_success(order_id, order.server_name or order.instance_id or '', order.instance_id or order.provider_resource_id or '', order.public_ip, order.login_user or 'root', order.login_password, note, order.static_ip_name or '')


@sync_to_async
def _get_order(order_id: int):
    return CloudServerOrder.objects.filter(id=order_id).first()


@sync_to_async
def _mark_success(order_id: int, server_name: str, instance_id: str, public_ip: str, login_user: str, login_password: str, note: str, static_ip_name: str = ''):
    logger.info('[PROVISION] mark_success_start order_id=%s server_name=%s instance_id=%s public_ip=%s', order_id, server_name, instance_id, public_ip)
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
    order.static_ip_name = static_ip_name or order.static_ip_name
    order.completed_at = timezone.now()
    if not order.service_started_at:
        order.service_started_at = order.completed_at
    if not order.service_expires_at:
        order.service_expires_at = order.completed_at + timezone.timedelta(days=order.lifecycle_days or 31)
    try:
        order.last_user_id = order.user.tg_user_id
    except Exception:
        order.last_user_id = order.user_id or 0
    order.save(update_fields=['status', 'server_name', 'instance_id', 'provider_resource_id', 'public_ip', 'mtproxy_host', 'mtproxy_link', 'mtproxy_secret', 'static_ip_name', 'login_user', 'login_password', 'provision_note', 'completed_at', 'service_started_at', 'service_expires_at', 'last_user_id', 'updated_at'])
    logger.info('[PROVISION] order_saved order=%s status=%s service_started_at=%s service_expires_at=%s mtproxy_host=%s mtproxy_link=%s', order.order_no, order.status, order.service_started_at, order.service_expires_at, order.mtproxy_host, order.mtproxy_link)

    try:
        try:
            order_user = order.user
        except Exception:
            order_user = None
        server_asset, _ = CloudAsset.objects.update_or_create(
            order=order,
            kind=CloudAsset.KIND_SERVER,
            defaults={
                'source': CloudAsset.SOURCE_ORDER,
                'provider': order.provider,
                'region_code': order.region_code,
                'region_name': order.region_name,
                'asset_name': server_name,
                'instance_id': instance_id,
                'provider_resource_id': instance_id,
                'public_ip': public_ip,
                'login_user': login_user,
                'login_password': login_password,
                'mtproxy_port': order.mtproxy_port,
                'mtproxy_link': mtproxy_link,
                'mtproxy_secret': mtproxy_secret,
                'mtproxy_host': mtproxy_host or public_ip,
                'actual_expires_at': order.service_expires_at,
                'price': order.total_amount,
                'currency': order.currency,
                'order': order,
                'user': order_user,
                'note': note,
                'status': CloudAsset.STATUS_RUNNING,
                'is_active': True,
            },
        )
        server_record = _upsert_server_record(order, note)
        record_cloud_ip_log(
            event_type='created',
            order=order,
            asset=server_asset,
            server=server_record,
            public_ip=public_ip,
            note=f'服务器创建并分配IP：{public_ip or "未分配"}',
        )
        logger.info('[PROVISION] server_asset_saved order=%s asset_id=%s server_record_id=%s expires_at=%s host=%s port=%s link=%s', order.order_no, server_asset.id, getattr(server_record, 'id', None), order.service_expires_at, mtproxy_host or public_ip, order.mtproxy_port, mtproxy_link)
    except Exception as exc:
        logger.exception('[PROVISION] asset_sync_failed order=%s error=%s', order.order_no, exc)
    return order


@sync_to_async
def _mark_failed(order_id: int, note: str):
    logger.info('[PROVISION] mark_failed_start order_id=%s note=%s', order_id, (note or '')[:1500])
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'failed'
    order.provision_note = note
    order.save(update_fields=['status', 'provision_note', 'updated_at'])
    server_record = _upsert_server_record(order, note)
    CloudAsset.objects.filter(order=order).update(note=note, status=CloudAsset.STATUS_UNKNOWN, is_active=False, updated_at=timezone.now())
    Server.objects.filter(order=order).update(note=note, status=Server.STATUS_UNKNOWN, is_active=False, updated_at=timezone.now())
    logger.info('[PROVISION] failed_server_record_synced order=%s server_record_id=%s', order.order_no, getattr(server_record, 'id', None))
    logger.info('[PROVISION] mark_failed_done order=%s status=%s', order.order_no, order.status)
    return order
