"""cloud 后台订单/关联资产展示 helper。"""

from django.db.models import Q

from bot.api import _decimal_to_str, _iso, _provider_label, _region_label, _status_label, _user_payload
from cloud.models import CloudAsset, CloudIpLog, CloudServerOrder
from core.runtime_config import get_runtime_config


def _cloud_execution_status(note: str | None):
    text = str(note or '').strip()
    if not text:
        return '', ''
    if '阿里云真实续费失败' in text:
        return 'aliyun_renew_failed', '阿里云续费失败，待重试'
    if '关机失败' in text:
        return 'suspend_failed', '关机失败，待重试'
    if '删除失败' in text:
        return 'delete_failed', '删机失败，待重试'
    if '旧实例删除失败' in text or '旧服务器删除失败' in text:
        return 'migration_delete_failed', '迁移旧机删除失败，待重试'
    return '', ''


def _mask_secret(value, keep=4):
    text = str(value or '')
    if not text:
        return ''
    if len(text) <= keep * 2:
        return '*' * len(text)
    return f'{text[:keep]}***{text[-keep:]}'


def _cloud_order_source_tags(order):
    note = str(getattr(order, 'provision_note', '') or '')
    order_no = str(getattr(order, 'order_no', '') or '')
    tags: list[tuple[str, str]] = []
    seen = set()

    def add(tag_key: str, tag_label: str):
        if tag_key in seen:
            return
        seen.add(tag_key)
        tags.append((tag_key, tag_label))

    if '人工编辑' in note or order_no.startswith('SRVMANUAL') or order_no.startswith('SRVADMIN'):
        if '所属人' in note or '用户' in note:
            add('manual_owner_change', '人工改用户')
        if '到期时间' in note:
            add('manual_expiry_change', '人工改时间')
        if '价格' in note:
            add('manual_price_change', '人工改价格')
        if ('所属人' in note or '用户' in note) and '时间' in note and not tags:
            add('manual_owner_expiry_change', '人工改用户+时间')
    if not tags:
        if getattr(order, 'replacement_for_id', None):
            add('renewal_rebuild', '续费恢复')
        elif getattr(order, 'last_renewed_at', None) or getattr(order, 'status', '') == 'renew_pending' or '续费' in note:
            add('renewal', '续费')
        else:
            add('new', '新购')
    return tags


def _cloud_order_source_label(order):
    tags = _cloud_order_source_tags(order)
    first_tag = tags[0] if tags else ('new', '新购')
    return first_tag[0], first_tag[1]


def _cloud_order_summary_payload(order):
    if not order:
        return None
    order_source, order_source_label = _cloud_order_source_label(order)
    order_source_tags = _cloud_order_source_tags(order)
    return {
        'id': order.id,
        'order_id': order.id,
        'order_no': order.order_no,
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'order_source': order_source,
        'order_source_label': order_source_label,
        'order_source_tags': [item[0] for item in order_source_tags],
        'order_source_tag_labels': [item[1] for item in order_source_tags],
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'public_ip': order.public_ip,
        'previous_public_ip': order.previous_public_ip,
        'service_started_at': _iso(order.service_started_at),
        'service_expires_at': _iso(order.service_expires_at),
        'created_at': _iso(order.created_at),
        'updated_at': _iso(order.updated_at),
        'replacement_for_id': order.replacement_for_id,
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
    }


def _order_lineage_ids(order):
    if not order:
        return set()
    seen = set()
    queue = [order.id]
    while queue:
        current_id = queue.pop(0)
        if not current_id or current_id in seen:
            continue
        seen.add(current_id)
        parent_id = CloudServerOrder.objects.filter(id=current_id).values_list('replacement_for_id', flat=True).first()
        if parent_id and parent_id not in seen:
            queue.append(parent_id)
        child_ids = list(CloudServerOrder.objects.filter(replacement_for_id=current_id).values_list('id', flat=True))
        for child_id in child_ids:
            if child_id not in seen:
                queue.append(child_id)
    return seen


def _cloud_asset_detail_log_queryset(asset, order):
    order_ids = _order_lineage_ids(order)
    asset_names = {str(asset.asset_name or '').strip(), str(asset.instance_id or '').strip()}
    ip_values = {str(asset.public_ip or '').strip(), str(asset.previous_public_ip or '').strip()}
    order_nos = set()
    if order_ids:
        for item in CloudServerOrder.objects.filter(id__in=order_ids).only('order_no', 'server_name', 'instance_id', 'public_ip', 'previous_public_ip'):
            order_nos.add(str(item.order_no or '').strip())
            asset_names.add(str(item.server_name or '').strip())
            asset_names.add(str(item.instance_id or '').strip())
            ip_values.add(str(item.public_ip or '').strip())
            ip_values.add(str(item.previous_public_ip or '').strip())
    asset_names.discard('')
    ip_values.discard('')
    order_nos.discard('')
    related_asset_ids = {asset.id}
    asset_lookup = Q()
    if order_ids:
        asset_lookup |= Q(order_id__in=order_ids)
    if asset_names:
        asset_lookup |= Q(asset_name__in=asset_names) | Q(instance_id__in=asset_names)
    if ip_values:
        asset_lookup |= Q(public_ip__in=ip_values) | Q(previous_public_ip__in=ip_values)
    if asset_lookup:
        related_asset_ids.update(CloudAsset.objects.filter(asset_lookup).values_list('id', flat=True)[:200])
    log_lookup = Q(asset_id__in=related_asset_ids)
    if order_ids:
        log_lookup |= Q(order_id__in=order_ids)
    if asset_names:
        log_lookup |= Q(asset_name__in=asset_names) | Q(instance_id__in=asset_names)
    if ip_values:
        log_lookup |= Q(public_ip__in=ip_values) | Q(previous_public_ip__in=ip_values)
    for order_no in order_nos:
        log_lookup |= Q(order_no=order_no) | Q(note__icontains=order_no)
    return CloudIpLog.objects.filter(log_lookup).distinct().order_by('-created_at', '-id')


def _related_order_history_payload(order):
    if not order:
        return []
    root = order
    seen = set()
    while root.replacement_for_id and root.replacement_for_id not in seen:
        seen.add(root.id)
        parent = CloudServerOrder.objects.select_related('user', 'plan').filter(id=root.replacement_for_id).first()
        if not parent:
            break
        root = parent
    chain = list(
        CloudServerOrder.objects.select_related('user', 'plan')
        .filter(Q(id=root.id) | Q(replacement_for_id=root.id) | Q(replacement_for__replacement_for_id=root.id) | Q(replacement_for__replacement_for__replacement_for_id=root.id))
        .order_by('-created_at', '-id')[:20]
    )
    if order.id not in {item.id for item in chain}:
        chain.insert(0, order)
    deduped = []
    seen_ids = set()
    for item in chain:
        if item.id in seen_ids:
            continue
        seen_ids.add(item.id)
        deduped.append(item)
    deduped.sort(key=lambda item: (0 if item.id == order.id else 1, -(item.created_at.timestamp() if item.created_at else 0), -item.id))
    return [_cloud_order_summary_payload(item) for item in deduped]


def _cloud_order_detail_payload(order):
    user = order.user
    usernames = user.usernames if user else []
    user_payload = _user_payload({
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'username': user.username,
        'first_name': user.first_name,
        'usernames': usernames,
        'primary_username': usernames[0] if usernames else '',
    }) if user else None
    order_source, order_source_label = _cloud_order_source_label(order)
    payload = {
        'id': order.id,
        'order_no': order.order_no,
        'provider': order.provider,
        'cloud_account_id': order.cloud_account_id,
        'account_label': order.account_label,
        'region_code': order.region_code,
        'region_label': _region_label(order.region_code, order.region_name),
        'region_name': order.region_name,
        'plan_name': order.plan_name,
        'quantity': order.quantity,
        'currency': order.currency,
        'total_amount': _decimal_to_str(order.total_amount),
        'pay_amount': _decimal_to_str(order.pay_amount) if order.pay_amount is not None else None,
        'pay_method': order.pay_method,
        'order_source': order_source,
        'order_source_label': order_source_label,
        'order_source_tags': [item[0] for item in _cloud_order_source_tags(order)],
        'order_source_tag_labels': [item[1] for item in _cloud_order_source_tags(order)],
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'tx_hash': order.tx_hash,
        'payer_address': order.payer_address,
        'receive_address': order.receive_address,
        'tronscan_url': f'https://tronscan.org/#/transaction/{order.tx_hash}' if order.tx_hash else '',
        'image_name': order.image_name,
        'server_name': order.server_name,
        'lifecycle_days': order.lifecycle_days,
        'service_started_at': _iso(order.service_started_at),
        'service_expires_at': _iso(order.service_expires_at),
        'renew_grace_expires_at': _iso(order.renew_grace_expires_at),
        'suspend_at': _iso(order.suspend_at),
        'delete_at': _iso(order.delete_at),
        'ip_recycle_at': _iso(order.ip_recycle_at),
        'suspend_time_config': str(get_runtime_config('cloud_suspend_time', '15:00') or '15:00').strip() or '15:00',
        'delete_time_config': str(get_runtime_config('cloud_delete_time', '15:00') or '15:00').strip() or '15:00',
        'last_renewed_at': _iso(order.last_renewed_at),
        'auto_renew_enabled': order.auto_renew_enabled,
        'last_user_id': order.last_user_id,
        'mtproxy_port': order.mtproxy_port,
        'mtproxy_link': order.mtproxy_link,
        'proxy_links': order.proxy_links or [],
        'mtproxy_secret': _mask_secret(order.mtproxy_secret),
        'has_mtproxy_secret': bool(order.mtproxy_secret),
        'mtproxy_host': order.mtproxy_host,
        'instance_id': order.instance_id,
        'provider_resource_id': order.provider_resource_id,
        'static_ip_name': order.static_ip_name,
        'public_ip': order.public_ip,
        'previous_public_ip': order.previous_public_ip,
        'login_user': order.login_user,
        'login_password': _mask_secret(order.login_password),
        'has_login_password': bool(order.login_password),
        'provision_note': order.provision_note,
        'created_at': _iso(order.created_at),
        'paid_at': _iso(order.paid_at),
        'expired_at': _iso(order.expired_at),
        'completed_at': _iso(order.completed_at),
        'updated_at': _iso(order.updated_at),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'plan_id': order.plan_id,
        'execution_status': _cloud_execution_status(order.provision_note)[0],
        'execution_status_label': _cloud_execution_status(order.provision_note)[1],
    }
    payload.update({
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
        'replacement_for_detail_path': f'/admin/cloud-orders/{order.replacement_for_id}' if order.replacement_for_id else '',
        'history_orders': _related_order_history_payload(order),
    })
    return payload
