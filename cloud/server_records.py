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

    def create(self, **kwargs):
        kwargs = _payload_kwargs(kwargs)
        kwargs.setdefault('kind', CloudAsset.KIND_SERVER)
        return CloudAsset.objects.create(**kwargs)

    def update_or_create(self, defaults=None, **kwargs):
        kwargs = _payload_kwargs(kwargs)
        kwargs.setdefault('kind', CloudAsset.KIND_SERVER)
        defaults = _payload_kwargs(defaults or {})
        defaults.setdefault('kind', CloudAsset.KIND_SERVER)
        return CloudAsset.objects.update_or_create(defaults=defaults, **kwargs)

    def get(self, *args, **kwargs):
        return self.filter(*args, **kwargs).get()

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
