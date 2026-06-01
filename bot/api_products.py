"""Dashboard API views for product management."""

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.dashboard_api import (
    _apply_keyword_filter,
    _decimal_to_str,
    _error,
    _get_keyword,
    _iso,
    _ok,
    _parse_decimal,
    _read_payload,
    dashboard_login_required,
    dashboard_superuser_required,
)
from orders.models import Product


def _product_payload(item):
    return {
        'id': item.id,
        'name': item.name,
        'description': item.description,
        'price': _decimal_to_str(item.price),
        'content_type': item.content_type,
        'content_text': item.content_text,
        'content_image': item.content_image,
        'content_video': item.content_video,
        'stock': item.stock,
        'is_active': item.is_active,
        'sort_order': item.sort_order,
        'created_at': _iso(item.created_at),
        'updated_at': _iso(item.updated_at),
    }


@dashboard_login_required
@require_GET
def products_list(request):
    keyword = _get_keyword(request)
    queryset = Product.objects.order_by('-sort_order', '-id')
    queryset = _apply_keyword_filter(queryset, keyword, ['name', 'description', 'content_text'])
    return _ok([_product_payload(item) for item in queryset[:200]])


@csrf_exempt
@dashboard_superuser_required
@require_POST
def create_product(request):
    payload = _read_payload(request)
    name = (payload.get('name') or '').strip()
    if not name:
        return _error('商品名称不能为空', status=400)
    try:
        price = _parse_decimal(payload.get('price'), '商品价格')
        stock = int(payload.get('stock', -1))
        sort_order = int(payload.get('sort_order', 0))
    except (ValueError, TypeError):
        return _error('商品价格或库存格式不正确', status=400)
    content_type = (payload.get('content_type') or Product.CONTENT_TEXT).strip()
    if content_type not in {choice[0] for choice in Product.CONTENT_CHOICES}:
        return _error('商品内容类型不正确', status=400)
    item = Product.objects.create(
        name=name,
        description=(payload.get('description') or '').strip() or None,
        price=price,
        content_type=content_type,
        content_text=payload.get('content_text') or None,
        content_image=payload.get('content_image') or None,
        content_video=payload.get('content_video') or None,
        stock=stock,
        is_active=str(payload.get('is_active', 'true')).lower() in {'1', 'true', 'yes', 'on'},
        sort_order=sort_order,
    )
    return _ok({'id': item.id})


@csrf_exempt
@dashboard_superuser_required
@require_POST
def update_product(request, product_id: int):
    item = Product.objects.filter(id=product_id).first()
    if not item:
        return _error('商品不存在', status=404)
    payload = _read_payload(request)
    if 'name' in payload:
        name = (payload.get('name') or '').strip()
        if not name:
            return _error('商品名称不能为空', status=400)
        item.name = name
    if 'description' in payload:
        item.description = (payload.get('description') or '').strip() or None
    if 'price' in payload:
        try:
            item.price = _parse_decimal(payload.get('price'), '商品价格')
        except ValueError as exc:
            return _error(str(exc), status=400)
    if 'content_type' in payload:
        content_type = (payload.get('content_type') or '').strip()
        if content_type not in {choice[0] for choice in Product.CONTENT_CHOICES}:
            return _error('商品内容类型不正确', status=400)
        item.content_type = content_type
    for field in ('content_text', 'content_image', 'content_video'):
        if field in payload:
            value = payload.get(field)
            setattr(item, field, value or None)
    if 'stock' in payload:
        try:
            item.stock = int(payload.get('stock'))
        except (ValueError, TypeError):
            return _error('库存格式不正确', status=400)
    if 'sort_order' in payload:
        try:
            item.sort_order = int(payload.get('sort_order'))
        except (ValueError, TypeError):
            return _error('排序值格式不正确', status=400)
    if 'is_active' in payload:
        item.is_active = str(payload.get('is_active')).lower() in {'1', 'true', 'yes', 'on'}
    item.save()
    return _ok({'id': item.id})
