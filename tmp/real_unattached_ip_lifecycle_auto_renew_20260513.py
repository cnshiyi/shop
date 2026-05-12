import asyncio
import json
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

import django
django.setup()

from asgiref.sync import sync_to_async
from django.utils import timezone

from bot.models import TelegramUser
from cloud.lifecycle import _delete_instance, _get_due_orders, _mark_deleted
from cloud.management.commands.sync_aws_assets import _lightsail_client
from cloud.models import CloudAsset, CloudIpLog, CloudServerOrder, CloudServerPlan
from cloud.provisioning import provision_cloud_server
from cloud.services import list_all_auto_renew_cloud_servers, list_retained_ip_renewal_plans_by_asset
from core.cloud_accounts import cloud_account_label, list_active_cloud_accounts
from core.runtime_config import get_runtime_config

STAMP = timezone.now().strftime('%Y%m%d%H%M%S')
ORDER_NO = f'SRVREALUNATTACHED{STAMP}'
REGION = 'ap-southeast-1'
RECORD_PATH = Path('tmp') / f'real-unattached-ip-lifecycle-auto-renew-{STAMP}.json'


def choose_account():
    accounts = list(list_active_cloud_accounts('aws_lightsail'))
    return next((a for a in accounts if getattr(a, 'id', None) == 3), accounts[0] if accounts else None)


def choose_plan():
    return (
        CloudServerPlan.objects.filter(provider='aws_lightsail', region_code=REGION, provider_plan_id='nano_3_0', is_active=True).first()
        or CloudServerPlan.objects.filter(provider='aws_lightsail', region_code=REGION, is_active=True).order_by('price', 'id').first()
    )


def choose_user():
    return TelegramUser.objects.filter(tg_user_id=21989077).first() or TelegramUser.objects.order_by('id').first()


def create_order(user, plan, account):
    now = timezone.now()
    return CloudServerOrder.objects.create(
        order_no=ORDER_NO,
        user=user,
        plan=plan,
        provider=plan.provider,
        cloud_account=account,
        account_label=cloud_account_label(account),
        region_code=plan.region_code or REGION,
        region_name=plan.region_name or REGION,
        plan_name=plan.plan_name,
        provider_resource_id=plan.provider_plan_id or None,
        quantity=1,
        currency='USDT',
        total_amount=plan.price,
        pay_amount=plan.price,
        pay_method='balance',
        status='paid',
        paid_at=now,
        mtproxy_port=9791,
        auto_renew_enabled=True,
        provision_note=f'真机测试：删除实例后验证固定 IP 转未附加生命周期刷新且不自动续费；stamp={STAMP}',
    )


def sync_aws(label, account, record):
    from django.core.management import call_command
    print('SYNC_START', label, flush=True)
    call_command('sync_aws_assets', region=REGION, account_id=str(account.id))
    record.setdefault('syncs', []).append({'label': label, 'at': timezone.now().isoformat()})


def collect_db(label, record):
    order = CloudServerOrder.objects.filter(order_no=ORDER_NO).first()
    order_ids = [order.id] if order else []
    orders = list(CloudServerOrder.objects.filter(id__in=order_ids).values(
        'id', 'order_no', 'status', 'server_name', 'public_ip', 'previous_public_ip', 'instance_id', 'provider_resource_id', 'static_ip_name', 'service_expires_at', 'delete_at', 'ip_recycle_at', 'auto_renew_enabled', 'provision_note'
    ))
    assets = list(CloudAsset.objects.filter(order_id__in=order_ids).values(
        'id', 'order_id', 'asset_name', 'public_ip', 'previous_public_ip', 'instance_id', 'provider_resource_id', 'status', 'provider_status', 'actual_expires_at', 'is_active', 'note'
    ).order_by('id'))
    logs = list(CloudIpLog.objects.filter(order_id__in=order_ids).values(
        'id', 'order_id', 'event_type', 'order_no', 'asset_name', 'public_ip', 'previous_public_ip', 'note', 'created_at'
    ).order_by('id'))
    snapshot = {'label': label, 'at': timezone.now().isoformat(), 'orders': orders, 'assets': assets, 'logs': logs}
    record.setdefault('snapshots', []).append(snapshot)
    print('SNAPSHOT', label, flush=True)
    for item in orders:
        print('ORDER', item['id'], item['status'], item['public_ip'], item['previous_public_ip'], item['instance_id'], item['static_ip_name'], item['ip_recycle_at'], flush=True)
    for item in assets:
        print('ASSET', item['id'], item['asset_name'], item['status'], item['provider_status'], item['public_ip'], item['instance_id'], item['actual_expires_at'], flush=True)
    return snapshot


def collect_aws(account, server_name, static_ip_name):
    client = _lightsail_client(REGION, account)
    result = {'instances': [], 'static_ips': []}
    try:
        for item in client.get_instances().get('instances') or []:
            if item.get('name') == server_name:
                result['instances'].append({
                    'name': item.get('name'),
                    'public_ip': item.get('publicIpAddress') or '',
                    'state': (item.get('state') or {}).get('name') or '',
                })
    except Exception as exc:
        result['instances_error'] = str(exc)
    try:
        for item in client.get_static_ips().get('staticIps') or []:
            if item.get('name') == static_ip_name:
                result['static_ips'].append({
                    'name': item.get('name'),
                    'ip': item.get('ipAddress') or '',
                    'attached_to': item.get('attachedTo') or '',
                    'arn': item.get('arn') or '',
                })
    except Exception as exc:
        result['static_ips_error'] = str(exc)
    return result


def release_static_ip(account, static_ip_name, record):
    if not static_ip_name:
        return {'ok': False, 'error': 'missing static_ip_name'}
    client = _lightsail_client(REGION, account)
    try:
        client.release_static_ip(staticIpName=static_ip_name)
        result = {'ok': True, 'static_ip_name': static_ip_name, 'at': timezone.now().isoformat()}
        print('CLEANUP_RELEASE_STATIC_IP_OK', static_ip_name, flush=True)
    except Exception as exc:
        result = {'ok': False, 'static_ip_name': static_ip_name, 'error': str(exc), 'at': timezone.now().isoformat()}
        print('CLEANUP_RELEASE_STATIC_IP_FAIL', static_ip_name, exc, flush=True)
    record['cleanup_release_static_ip'] = result
    return result


def write_record(record):
    RECORD_PATH.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print('RECORD_PATH', RECORD_PATH, flush=True)


async def collect_auto_renew_and_manual(order_id, user_id, record):
    due = await _get_due_orders()
    auto_renew_ids = [item.id for item in due.get('auto_renew', [])]
    auto_renew_notice_ids = [item.id for item in due.get('auto_renew_notice', [])]
    auto_renew_items = await list_all_auto_renew_cloud_servers()
    asset = await sync_to_async(lambda: CloudAsset.objects.filter(order_id=order_id).order_by('-id').first())()
    retained_order = None
    manual_plans = []
    manual_err = None
    if asset:
        retained_order, manual_plans, manual_err = await list_retained_ip_renewal_plans_by_asset(asset.id, user_id)
    payload = {
        'auto_renew_ids': auto_renew_ids,
        'auto_renew_notice_ids': auto_renew_notice_ids,
        'order_in_auto_renew': order_id in auto_renew_ids,
        'order_in_auto_renew_notice': order_id in auto_renew_notice_ids,
        'auto_renew_list_asset_ids': [getattr(item, 'asset_id', None) for item in auto_renew_items],
        'asset_in_auto_renew_list': bool(asset and any(getattr(item, 'asset_id', None) == asset.id for item in auto_renew_items)),
        'asset_id': asset.id if asset else None,
        'manual_renewal_order_id': getattr(retained_order, 'id', None),
        'manual_renewal_plan_count': len(manual_plans or []),
        'manual_renewal_error': manual_err,
        'manual_renewal_plans': [getattr(item, '__dict__', {}) for item in (manual_plans or [])[:3]],
    }
    record['auto_renew_and_manual_check'] = payload
    print('AUTO_RENEW_CHECK', payload, flush=True)


async def main():
    record = {'stamp': STAMP, 'order_no': ORDER_NO, 'region': REGION, 'started_at': timezone.now().isoformat()}
    account = await sync_to_async(choose_account)()
    plan = await sync_to_async(choose_plan)()
    user = await sync_to_async(choose_user)()
    if not account or not plan or not user:
        raise RuntimeError(f'missing prerequisites account={account} plan={plan} user={user}')
    record.update({
        'account': {'id': account.id, 'label': cloud_account_label(account)},
        'plan': {'id': plan.id, 'provider_plan_id': plan.provider_plan_id, 'price': str(plan.price)},
        'config': {
            'cloud_unattached_ip_delete_after_days': get_runtime_config('cloud_unattached_ip_delete_after_days', '15'),
            'cloud_unattached_ip_delete_time': get_runtime_config('cloud_unattached_ip_delete_time', '15:00'),
        },
    })
    static_ip_name = ''
    server_name = ''
    try:
        order = await sync_to_async(create_order)(user, plan, account)
        print('PROVISION_START', order.id, order.order_no, flush=True)
        saved = await provision_cloud_server(order.id)
        await sync_to_async(saved.refresh_from_db)()
        print('PROVISION_DONE', saved.id, saved.status, saved.server_name, saved.public_ip, saved.static_ip_name, flush=True)
        if saved.status != 'completed':
            raise RuntimeError(f'provision failed: {saved.status} {saved.provision_note}')
        static_ip_name = saved.static_ip_name
        server_name = saved.server_name
        record['created_order'] = {'id': saved.id, 'server_name': server_name, 'public_ip': saved.public_ip, 'static_ip_name': static_ip_name, 'service_expires_at': saved.service_expires_at}
        await sync_to_async(sync_aws)('创建后同步', account, record)
        await sync_to_async(collect_db)('创建后同步', record)
        record['aws_after_create_sync'] = await sync_to_async(collect_aws)(account, server_name, static_ip_name)

        print('DELETE_INSTANCE_START', saved.id, server_name, static_ip_name, flush=True)
        ok, note = await _delete_instance(saved)
        print('DELETE_INSTANCE_RESULT', ok, note, flush=True)
        record['delete_instance'] = {'ok': ok, 'note': note, 'at': timezone.now().isoformat()}
        if not ok:
            raise RuntimeError(f'delete failed: {note}')
        marked = await _mark_deleted(saved.id, note)
        await sync_to_async(marked.refresh_from_db)()
        record['mark_deleted'] = {'status': marked.status, 'public_ip': marked.public_ip, 'previous_public_ip': marked.previous_public_ip, 'instance_id': marked.instance_id, 'static_ip_name': marked.static_ip_name, 'ip_recycle_at': marked.ip_recycle_at}
        print('MARK_DELETED', record['mark_deleted'], flush=True)
        await sync_to_async(time.sleep)(20)
        record['aws_after_delete_before_sync'] = await sync_to_async(collect_aws)(account, server_name, static_ip_name)
        await sync_to_async(collect_db)('删机后同步前', record)
        await sync_to_async(sync_aws)('删机后同步', account, record)
        await sync_to_async(collect_db)('删机后同步', record)
        record['aws_after_delete_sync'] = await sync_to_async(collect_aws)(account, server_name, static_ip_name)
        await collect_auto_renew_and_manual(saved.id, user.id, record)
    finally:
        if static_ip_name:
            await sync_to_async(release_static_ip)(account, static_ip_name, record)
            await sync_to_async(time.sleep)(5)
            record['aws_after_cleanup'] = await sync_to_async(collect_aws)(account, server_name, static_ip_name)
        record['finished_at'] = timezone.now().isoformat()
        await sync_to_async(write_record)(record)

asyncio.run(main())
