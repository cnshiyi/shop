"""Dashboard API views for cloud plans and provider pricing."""

from decimal import Decimal, InvalidOperation

from asgiref.sync import async_to_sync
from django.db import IntegrityError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from cloud.models import CloudServerOrder, CloudServerPlan, ServerPrice
from cloud.dashboard_api_helpers import _generate_cloud_plan_config_id
from cloud.services import refresh_custom_plan_cache
from core.dashboard_api import _apply_keyword_filter, _decimal_to_str, _error, _get_keyword, _iso, _ok, _parse_decimal, _provider_label, _read_payload, _region_label, dashboard_login_required, dashboard_superuser_required


def _resolve_cloud_plan_config_id(provider: str, region_code: str, provider_plan_id: str, config_id: str = '') -> str:
    explicit = str(config_id or '').strip()
    if explicit:
        return explicit
    bundle_code = str(provider_plan_id or '').strip()
    if bundle_code:
        matched_price = ServerPrice.objects.filter(
            provider=provider,
            region_code=region_code,
            bundle_code=bundle_code,
            is_active=True,
        ).only('config_id').first()
        if matched_price and str(matched_price.config_id or '').strip():
            return matched_price.config_id.strip()
    return _generate_cloud_plan_config_id()


def _cloud_plan_payload(plan):
    return {
        'id': plan.id,
        'provider': plan.provider,
        'provider_label': _provider_label(plan.provider),
        'region_code': plan.region_code,
        'region_name': plan.region_name,
        'region_label': _region_label(plan.region_code, plan.region_name),
        'config_id': plan.config_id,
        'provider_plan_id': plan.provider_plan_id,
        'plan_name': plan.plan_name,
        'plan_description': plan.plan_description,
        'display_plan_name': plan.display_plan_name,
        'display_cpu': plan.display_cpu,
        'display_memory': plan.display_memory,
        'display_storage': plan.display_storage,
        'display_bandwidth': plan.display_bandwidth,
        'display_description': plan.display_description,
        'cpu': plan.cpu,
        'memory': plan.memory,
        'storage': plan.storage,
        'bandwidth': plan.bandwidth,
        'cost_price': _decimal_to_str(getattr(plan, 'cost_price', 0)),
        'price': _decimal_to_str(plan.price),
        'currency': plan.currency,
        'sort_order': plan.sort_order,
        'is_active': plan.is_active,
        'updated_at': _iso(plan.updated_at),
    }


def _server_price_payload(price):
    return {
        'id': price.id,
        'provider': price.provider,
        'region_code': price.region_code,
        'region_name': price.region_name,
        'config_id': price.config_id,
        'bundle_code': price.bundle_code,
        'plan_name': price.server_name,
        'server_name': price.server_name,
        'plan_description': price.server_description or '',
        'server_description': price.server_description or '',
        'cpu': price.cpu,
        'memory': price.memory,
        'storage': price.storage,
        'bandwidth': price.bandwidth,
        'cost_price': _decimal_to_str(getattr(price, 'cost_price', 0)),
        'price': _decimal_to_str(price.price),
        'currency': price.currency,
        'sort_order': price.sort_order,
        'is_active': price.is_active,
        'updated_at': _iso(price.updated_at),
    }


@dashboard_login_required
@require_GET
def cloud_pricing_list(request):
    keyword = _get_keyword(request)
    queryset = ServerPrice.objects.filter(is_active=True).order_by('provider', 'region_code', '-sort_order', 'id')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['provider', 'region_code', 'region_name', 'bundle_code', 'server_name', 'server_description', 'cpu', 'memory', 'storage', 'bandwidth'],
    )
    provider = (request.GET.get('provider') or '').strip()
    region_code = (request.GET.get('region_code') or '').strip()
    if provider:
        queryset = queryset.filter(provider=provider)
    if region_code:
        queryset = queryset.filter(region_code=region_code)
    return _ok([_server_price_payload(item) for item in queryset])


@dashboard_login_required
@require_GET
def cloud_plans_list(request):
    keyword = _get_keyword(request)
    queryset = CloudServerPlan.objects.filter(is_active=True).order_by('provider', 'region_code', '-sort_order', 'id')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['provider', 'region_code', 'region_name', 'plan_name', 'plan_description', 'cpu', 'memory', 'storage', 'bandwidth'],
    )
    provider = (request.GET.get('provider') or '').strip()
    region_code = (request.GET.get('region_code') or '').strip()
    if provider:
        queryset = queryset.filter(provider=provider)
    if region_code:
        queryset = queryset.filter(region_code=region_code)
    return _ok([_cloud_plan_payload(item) for item in queryset])


@csrf_exempt
@dashboard_superuser_required
@require_http_methods(['POST'])
def create_cloud_plan(request):
    data = _read_payload(request)
    provider = (data.get('provider') or '').strip()
    region_code = (data.get('region_code') or '').strip()
    region_name = (data.get('region_name') or '').strip()
    plan_name = (data.get('plan_name') or '').strip()
    if not provider or not region_code or not region_name or not plan_name:
        return _error('云厂商、地区代码、地区名称、套餐名不能为空')
    provider_plan_id = (data.get('provider_plan_id') or '').strip()
    resolved_config_id = _resolve_cloud_plan_config_id(
        provider=provider,
        region_code=region_code,
        provider_plan_id=provider_plan_id,
        config_id=(data.get('config_id') or '').strip(),
    )
    try:
        payload_fields = {
            'provider': provider,
            'region_code': region_code,
            'region_name': region_name,
            'config_id': resolved_config_id,
            'provider_plan_id': provider_plan_id,
            'plan_name': plan_name,
            'plan_description': ((data.get('plan_description') or data.get('display_description') or '').strip()),
            'display_plan_name': (data.get('display_plan_name') or '').strip(),
            'display_cpu': (data.get('display_cpu') or '').strip(),
            'display_memory': (data.get('display_memory') or '').strip(),
            'display_storage': (data.get('display_storage') or '').strip(),
            'display_bandwidth': (data.get('display_bandwidth') or '').strip(),
            'display_description': (data.get('display_description') or '').strip(),
            'cpu': (data.get('cpu') or '').strip(),
            'memory': (data.get('memory') or '').strip(),
            'storage': (data.get('storage') or '').strip(),
            'bandwidth': (data.get('bandwidth') or '').strip(),
            'cost_price': _parse_decimal(data.get('cost_price') or 0, '进货价').quantize(Decimal('0.01')),
            'price': _parse_decimal(data.get('price') or 0, '出售价').quantize(Decimal('0.01')),
            'currency': (data.get('currency') or 'USDT').strip() or 'USDT',
            'sort_order': int(data.get('sort_order') or 0),
            'is_active': str(data.get('is_active', True)).lower() in {'1', 'true', 'yes', 'on'},
        }
        existed = CloudServerPlan.objects.filter(
            provider=provider,
            region_code=region_code,
            config_id=resolved_config_id,
        ).order_by('-id').first()
        if existed:
            for field, value in payload_fields.items():
                setattr(existed, field, value)
            existed.is_active = True
            existed.save()
            plan = existed
        else:
            plan = CloudServerPlan.objects.create(**payload_fields)
    except IntegrityError:
        return _error('同地区下已存在同厂商配置ID', status=400)
    except (InvalidOperation, TypeError, ValueError):
        return _error('提交的套餐数据格式不正确', status=400)
    async_to_sync(refresh_custom_plan_cache)()
    return _ok(_cloud_plan_payload(plan))


@csrf_exempt
@dashboard_superuser_required
@require_http_methods(['POST'])
def delete_cloud_plan(request, plan_id: int):
    plan = CloudServerPlan.objects.filter(id=plan_id).first()
    if not plan:
        return _error('套餐不存在', status=404)
    if CloudServerOrder.objects.filter(plan_id=plan_id).exists():
        return _error('该套餐已有订单引用，无法删除，请改为停用', status=400)
    plan.delete()
    async_to_sync(refresh_custom_plan_cache)()
    return _ok({'id': plan_id, 'deleted': True})


@csrf_exempt
@dashboard_superuser_required
@require_http_methods(['POST'])
def update_cloud_plan(request, plan_id: int):
    plan = CloudServerPlan.objects.filter(id=plan_id).first()
    if not plan:
        return _error('套餐不存在', status=404)
    data = _read_payload(request)
    plan_name = (data.get('plan_name') or '').strip()
    display_description = (data.get('display_description') or '').strip()
    plan_description = (data.get('plan_description') or display_description).strip()
    price = data.get('price')
    cost_price = data.get('cost_price')
    sort_order = data.get('sort_order')
    is_active = data.get('is_active')
    try:
        config_id = (data.get('config_id') or '').strip()
        provider_plan_id = (data.get('provider_plan_id') or '').strip()
        next_provider = (data.get('provider') or '').strip() or plan.provider
        next_region_code = (data.get('region_code') or '').strip() or plan.region_code
        next_provider_plan_id = provider_plan_id if 'provider_plan_id' in data else plan.provider_plan_id
        resolved_config_id = _resolve_cloud_plan_config_id(
            provider=next_provider,
            region_code=next_region_code,
            provider_plan_id=next_provider_plan_id,
            config_id=config_id if 'config_id' in data else plan.config_id,
        )
        plan.config_id = resolved_config_id
        if 'provider_plan_id' in data:
            plan.provider_plan_id = provider_plan_id
        if plan_name:
            plan.plan_name = plan_name
        if 'provider' in data:
            plan.provider = (data.get('provider') or '').strip() or plan.provider
        if 'region_code' in data:
            plan.region_code = (data.get('region_code') or '').strip() or plan.region_code
        if 'region_name' in data:
            plan.region_name = (data.get('region_name') or '').strip() or plan.region_name
        if 'display_plan_name' in data:
            plan.display_plan_name = (data.get('display_plan_name') or '').strip()
        if 'display_cpu' in data:
            plan.display_cpu = (data.get('display_cpu') or '').strip()
        if 'display_memory' in data:
            plan.display_memory = (data.get('display_memory') or '').strip()
        if 'display_storage' in data:
            plan.display_storage = (data.get('display_storage') or '').strip()
        if 'display_bandwidth' in data:
            plan.display_bandwidth = (data.get('display_bandwidth') or '').strip()
        if 'display_description' in data:
            plan.display_description = display_description
        if 'cpu' in data:
            plan.cpu = (data.get('cpu') or '').strip()
        if 'memory' in data:
            plan.memory = (data.get('memory') or '').strip()
        if 'storage' in data:
            plan.storage = (data.get('storage') or '').strip()
        if 'bandwidth' in data:
            plan.bandwidth = (data.get('bandwidth') or '').strip()
        if 'currency' in data:
            plan.currency = (data.get('currency') or 'USDT').strip() or 'USDT'
        plan.plan_description = plan_description
        if price not in (None, ''):
            plan.price = Decimal(str(price))
        if cost_price not in (None, ''):
            plan.cost_price = Decimal(str(cost_price))
        if sort_order not in (None, ''):
            plan.sort_order = int(sort_order)
        if is_active not in (None, ''):
            plan.is_active = str(is_active).lower() in {'1', 'true', 'yes', 'on'}
        plan.save()
    except IntegrityError:
        return _error('同地区下已存在同厂商配置ID', status=400)
    except (InvalidOperation, ValueError):
        return _error('提交的套餐数据格式不正确')
    async_to_sync(refresh_custom_plan_cache)()
    return _ok(_cloud_plan_payload(plan))
