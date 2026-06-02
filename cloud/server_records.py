"""Command-side compatibility wrapper for server-like cloud assets."""

from copy import copy

from django.db.models import Q

from cloud.models import CloudAsset


def _lookup_key(key: str) -> str:
    if key == 'server_name' or key.startswith('server_name__'):
        return 'asset_name' + key[len('server_name'):]
    if key == 'expires_at' or key.startswith('expires_at__'):
        return 'actual_expires_at' + key[len('expires_at'):]
    return key


def _payload_kwargs(kwargs: dict | None) -> dict:
    return {_lookup_key(key): value for key, value in dict(kwargs or {}).items()}


def _payload_ordering(fields):
    result = []
    for field in fields:
        prefix = '-' if str(field).startswith('-') else ''
        name = str(field)[1:] if prefix else str(field)
        result.append(prefix + _lookup_key(name))
    return result


def _payload_values(fields):
    return [_lookup_key(field) for field in fields]


def _payload_q(node):
    mapped = copy(node)
    children = []
    for child in node.children:
        if isinstance(child, Q):
            children.append(_payload_q(child))
        elif isinstance(child, tuple) and len(child) == 2:
            children.append((_lookup_key(child[0]), child[1]))
        else:
            children.append(child)
    mapped.children = children
    return mapped


def _payload_args(args):
    return [_payload_q(arg) if isinstance(arg, Q) else arg for arg in args]


def _preserve_existing_manual_fields(payload: dict, asset: CloudAsset) -> dict:
    payload = dict(payload)
    if asset.user_id:
        if 'user' in payload:
            payload['user'] = asset.user
        if 'user_id' in payload:
            payload['user_id'] = asset.user_id
    if asset.actual_expires_at and 'actual_expires_at' in payload:
        payload['actual_expires_at'] = asset.actual_expires_at
    if asset.note and 'note' in payload:
        payload['note'] = asset.note
    return payload


def _mark_server_record_payload(payload: dict) -> dict:
    payload = dict(payload)
    sync_state = dict(payload.get('sync_state') or {})
    sync_state['compat_server_record'] = True
    payload['sync_state'] = sync_state
    return payload


class _ServerObjects:
    def _qs(self):
        return CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER)

    def filter(self, *args, **kwargs):
        return self._qs().filter(*_payload_args(args), **_payload_kwargs(kwargs))

    def exclude(self, *args, **kwargs):
        return self._qs().exclude(*_payload_args(args), **_payload_kwargs(kwargs))

    def order_by(self, *fields):
        return self._qs().order_by(*_payload_ordering(fields))

    def select_related(self, *fields):
        return self._qs().select_related(*fields)

    def annotate(self, *args, **kwargs):
        return self._qs().annotate(*args, **kwargs)

    def values(self, *fields, **expressions):
        return self._qs().values(*_payload_values(fields), **expressions)

    def values_list(self, *fields, **kwargs):
        return self._qs().values_list(*_payload_values(fields), **kwargs)

    def count(self):
        return self._qs().count()

    def _identity_scope(self, payload):
        queryset = self._qs()
        if payload.get('provider'):
            queryset = queryset.filter(provider=payload.get('provider'))
        if payload.get('cloud_account_id'):
            queryset = queryset.filter(cloud_account_id=payload.get('cloud_account_id'))
        elif payload.get('cloud_account'):
            queryset = queryset.filter(cloud_account=payload.get('cloud_account'))
        if payload.get('account_label'):
            queryset = queryset.filter(account_label=payload.get('account_label'))
        if payload.get('region_code'):
            queryset = queryset.filter(region_code=payload.get('region_code'))
        return queryset

    def _find_existing_for_create(self, payload):
        order_id = payload.get('order_id') or getattr(payload.get('order'), 'pk', None)
        public_ip = str(payload.get('public_ip') or '').strip()
        previous_public_ip = str(payload.get('previous_public_ip') or '').strip()
        instance_id = str(payload.get('instance_id') or '').strip()
        provider_resource_id = str(payload.get('provider_resource_id') or '').strip()
        asset_name = str(payload.get('asset_name') or '').strip()
        ip_identity = Q()
        if public_ip:
            ip_identity |= Q(public_ip=public_ip) | Q(previous_public_ip=public_ip)
        if previous_public_ip:
            ip_identity |= Q(public_ip=previous_public_ip) | Q(previous_public_ip=previous_public_ip)
        identity = Q()
        if instance_id:
            identity |= Q(instance_id=instance_id) | Q(asset_name=instance_id)
        if provider_resource_id:
            identity |= Q(provider_resource_id=provider_resource_id)
        if asset_name:
            identity |= Q(asset_name=asset_name) | Q(instance_id=asset_name)
        if order_id:
            ordered = self._qs().filter(order_id=order_id, sync_state__compat_server_record=True)
            if ip_identity or identity:
                existing = ordered.filter(ip_identity | identity).order_by('-updated_at', '-id').first()
                if existing:
                    return existing
            existing = ordered.order_by('-updated_at', '-id').first()
            if existing:
                return existing
            if str(payload.get('note') or '').strip():
                return None
        if ip_identity:
            return self._identity_scope(payload).filter(ip_identity).order_by('-updated_at', '-id').first()
        if identity:
            return self._identity_scope(payload).filter(identity).order_by('-updated_at', '-id').first()
        return None

    def create(self, **kwargs):
        kwargs = _payload_kwargs(kwargs)
        kwargs.setdefault('kind', CloudAsset.KIND_SERVER)
        kwargs = _mark_server_record_payload(kwargs)
        existing = self._find_existing_for_create(kwargs)
        if existing:
            kwargs = _preserve_existing_manual_fields(kwargs, existing)
            for key, value in kwargs.items():
                setattr(existing, key, value)
            existing.save(update_fields=list(kwargs.keys()))
            return existing
        return CloudAsset.objects.create(**kwargs)

    def update_or_create(self, defaults=None, **kwargs):
        kwargs = _payload_kwargs(kwargs)
        kwargs.setdefault('kind', CloudAsset.KIND_SERVER)
        defaults = _payload_kwargs(defaults or {})
        defaults.setdefault('kind', CloudAsset.KIND_SERVER)
        defaults = _mark_server_record_payload(defaults)
        return CloudAsset.objects.update_or_create(defaults=defaults, **kwargs)

    def get(self, *args, **kwargs):
        queryset = self.filter(*args, **kwargs)
        try:
            return queryset.get()
        except CloudAsset.MultipleObjectsReturned:
            candidates = list(queryset.order_by('-updated_at', '-id')[:50])
            compat = [item for item in candidates if (item.sync_state or {}).get('compat_server_record')]
            if len(compat) == 1:
                return compat[0]
            return compat[0] if compat else candidates[0]

    def __getattr__(self, name):
        return getattr(self._qs(), name)


class Server:
    STATUS_RUNNING = CloudAsset.STATUS_RUNNING
    STATUS_PENDING = CloudAsset.STATUS_PENDING
    STATUS_STARTING = CloudAsset.STATUS_STARTING
    STATUS_STOPPING = CloudAsset.STATUS_STOPPING
    STATUS_STOPPED = CloudAsset.STATUS_STOPPED
    STATUS_SUSPENDED = CloudAsset.STATUS_SUSPENDED
    STATUS_TERMINATING = CloudAsset.STATUS_TERMINATING
    STATUS_TERMINATED = CloudAsset.STATUS_TERMINATED
    STATUS_DELETING = CloudAsset.STATUS_DELETING
    STATUS_DELETED = CloudAsset.STATUS_DELETED
    STATUS_EXPIRED = CloudAsset.STATUS_EXPIRED
    STATUS_EXPIRED_GRACE = CloudAsset.STATUS_EXPIRED_GRACE
    STATUS_UNKNOWN = CloudAsset.STATUS_UNKNOWN
    STATUS_CHOICES = CloudAsset.STATUS_CHOICES
    ACTIVE_STATUSES = CloudAsset.ACTIVE_STATUSES
    SOURCE_ALIYUN = CloudAsset.SOURCE_ALIYUN
    SOURCE_AWS_MANUAL = CloudAsset.SOURCE_AWS_MANUAL
    SOURCE_AWS_SYNC = CloudAsset.SOURCE_AWS_SYNC
    SOURCE_ORDER = CloudAsset.SOURCE_ORDER
    objects = _ServerObjects()
