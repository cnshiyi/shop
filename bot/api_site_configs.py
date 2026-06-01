"""Dashboard API views for site and text configuration."""

from asgiref.sync import async_to_sync
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from bot.api import (
    _error,
    _ok,
    _parse_runtime_time_point,
    _read_payload,
    dashboard_login_required,
    dashboard_superuser_required,
)
from core.button_config import init_button_config, load_button_config, save_button_config
from core.crypto import decrypt_text
from core.models import SiteConfig
from core.runtime_config import CONFIG_HELP, SENSITIVE_CONFIG_KEYS, get_runtime_config
from core.texts import TEXT_GROUPS, init_texts, text_default, text_description
from core.trongrid import parse_trongrid_api_keys


def _masked_sensitive_preview(value):
    plain = str(value or '')
    if not plain:
        return ''
    if len(plain) <= 6:
        return '*' * len(plain)
    return f'{plain[:3]}***{plain[-3:]}'


def _site_config_payload(item):
    is_sensitive = item.key in SENSITIVE_CONFIG_KEYS
    value = SiteConfig.get(item.key, '')
    if item.key == 'trongrid_api_key':
        value = decrypt_text(item.value or '')
    value_preview = _masked_sensitive_preview(value) if is_sensitive else (item.value or '')
    if item.key == 'trongrid_api_key':
        value_preview = value
    return {
        'id': item.id,
        'key': item.key,
        'value': '' if is_sensitive else value,
        'value_preview': value_preview,
        'is_sensitive': is_sensitive,
        'description': CONFIG_HELP.get(item.key, '') or text_description(item.key, ''),
        'sort_order': item.sort_order,
    }


def _site_config_group_map():
    return {
        'database': ['mysql_host', 'mysql_port', 'mysql_database', 'mysql_user', 'mysql_password', 'redis_host', 'redis_port', 'redis_password', 'redis_db'],
        'system': [
            'bot_token',
            'telegram_api_id',
            'telegram_api_hash',
            'dashboard_totp_secret',
        ],
        'payment': [
            'receive_address',
            'trongrid_api_key',
        ],
        'cloud_actions': [
            'cloud_server_delete_enabled',
            'cloud_ip_delete_enabled',
        ],
        'logs': [
            'scanner_block_log_enabled',
            'scanner_verbose',
        ],
        'notifications': [
            'telegram_listener_push_enabled',
            'telegram_listener_push_bark_url',
            'telegram_listener_push_private_enabled',
            'telegram_listener_push_bark_encryption_key',
            'telegram_listener_push_bark_encryption_iv',
            'bot_admin_chat_id',
            'bot_notice_copy_chat_ids',
            'cloud_auto_renew_execution_notify_enabled',
            'cloud_auto_renew_execution_notify_chat_ids',
            'cloud_auto_renew_execution_notify_events',
            'cloud_daily_expiry_summary_enabled',
            'cloud_daily_expiry_summary_chat_ids',
        ],
        'lifecycle': [
            'cloud_suspend_after_days',
            'cloud_suspend_time',
            'cloud_delete_after_days',
            'cloud_delete_time',
            'cloud_asset_sync_interval_seconds',
            'cloud_sync_missing_delete_confirmations',
        ],
        **TEXT_GROUPS,
    }


@dashboard_login_required
@require_GET
def site_configs_list(request):
    group_key = str(request.GET.get('group') or '').strip()
    queryset = SiteConfig.objects.order_by('sort_order', 'key')
    if group_key:
        group_keys = _site_config_group_map().get(group_key, [])
        queryset = queryset.filter(key__in=group_keys) if group_keys else SiteConfig.objects.none()
    return _ok([_site_config_payload(item) for item in queryset])


@dashboard_login_required
@require_GET
def button_config_detail(request):
    return _ok(load_button_config())


@csrf_exempt
@dashboard_superuser_required
@require_POST
def update_button_config(request):
    payload = _read_payload(request)
    return _ok(save_button_config(payload))


@csrf_exempt
@dashboard_superuser_required
@require_POST
def init_button_config_view(request):
    return _ok(init_button_config())


@dashboard_login_required
@require_GET
def site_config_groups(request):
    groups = _site_config_group_map()
    group_key = str(request.GET.get('group') or '').strip()
    if group_key:
        groups = {group_key: groups.get(group_key, [])}
    existing = {item.key: item for item in SiteConfig.objects.filter(key__in=[key for keys in groups.values() for key in keys])}
    payload = []
    for group_key, keys in groups.items():
        items = []
        ordered_keys = sorted(
            keys,
            key=lambda candidate: (
                existing[candidate].sort_order if candidate in existing and existing[candidate].sort_order else keys.index(candidate) + 1,
                keys.index(candidate),
            ),
        )
        for key in ordered_keys:
            obj = existing.get(key)
            is_sensitive = key in SENSITIVE_CONFIG_KEYS
            stored_value = SiteConfig.get(key, '') if obj else ''
            if key == 'trongrid_api_key' and obj:
                stored_value = decrypt_text(obj.value or '')
            effective_value = stored_value or get_runtime_config(key, '')
            value_preview = effective_value
            if is_sensitive:
                value_preview = _masked_sensitive_preview(effective_value)
            items.append({
                'key': key,
                'id': obj.id if obj else None,
                'value': '' if is_sensitive else effective_value,
                'value_preview': value_preview,
                'default_value': text_default(key, ''),
                'is_sensitive': is_sensitive,
                'description': CONFIG_HELP.get(key, '') or text_description(key, ''),
                'sort_order': obj.sort_order if obj else keys.index(key) + 1,
            })
        payload.append({'group': group_key, 'items': items})
    return _ok(payload)


@csrf_exempt
@dashboard_superuser_required
@require_POST
def init_site_configs(request):
    payload = _read_payload(request)
    scope = (payload.get('scope') or 'all').strip() or 'all'
    created = 0
    for key in CONFIG_HELP.keys():
        item, was_created = SiteConfig.objects.get_or_create(
            key=key,
            defaults={'value': get_runtime_config(key, ''), 'is_sensitive': key in SENSITIVE_CONFIG_KEYS, 'sort_order': list(CONFIG_HELP.keys()).index(key) + 1},
        )
        if not was_created and not SiteConfig.get(key, ''):
            runtime_value = get_runtime_config(key, '')
            if runtime_value:
                SiteConfig.set(key, runtime_value, sensitive=key in SENSITIVE_CONFIG_KEYS)
        created += int(was_created)
    if scope == 'all':
        text_result = init_texts(get_runtime_config('text_init_mode', 'missing_only'))
        return _ok({'created': created + text_result['created'], 'updated': text_result['updated'], 'scope': scope})
    return _ok({'created': created, 'updated': 0, 'scope': scope})


@csrf_exempt
@dashboard_superuser_required
@require_POST
def init_text_site_configs(request):
    enabled = str(get_runtime_config('text_init_enabled', '1')).lower() not in {'0', 'false', 'no', 'off'}
    if not enabled:
        return _error('文案初始化当前已禁用', status=400)
    payload = _read_payload(request)
    mode = (payload.get('mode') or get_runtime_config('text_init_mode', 'missing_only')).strip() or 'missing_only'
    if mode not in {'missing_only', 'reset_defaults'}:
        return _error('初始化模式不正确', status=400)
    result = init_texts(mode)
    return _ok({'mode': mode, **result})


@csrf_exempt
@dashboard_superuser_required
@require_POST
def test_daily_expiry_summary_notification(request):
    token = SiteConfig.get('bot_token', '') or get_runtime_config('bot_token', '')
    if not str(token or '').strip():
        return _error('测试通知发送失败：未配置 Telegram 机器人 Token', status=400)

    async def _send():
        from aiogram import Bot

        from cloud.lifecycle import daily_expiry_summary_tick

        bot = Bot(str(token).strip())

        async def _notify_target(chat_id, text: str, reply_markup=None):
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode='HTML')
            return True

        try:
            return await daily_expiry_summary_tick(notify_target=_notify_target, force=True, sync_cloud=False)
        finally:
            await bot.session.close()

    try:
        result = async_to_sync(_send)()
    except Exception as exc:
        return _error(f'测试通知发送失败：{exc}', status=400)
    if result.get('skipped') == 'disabled':
        return _error('每日到期汇总通知未开启或未配置通知目标', status=400)
    if result.get('skipped') == 'missing_notify_target':
        return _error('通知发送器不可用', status=400)
    if not result.get('sent'):
        return _error('测试通知未送达，请检查 Chat ID / 群组 / 频道权限', status=400)
    return _ok(result)


@csrf_exempt
@dashboard_superuser_required
@require_POST
def update_site_config(request, config_id: int):
    item = SiteConfig.objects.filter(id=config_id).first()
    if not item:
        return _error('配置不存在', status=404)
    data = _read_payload(request)
    is_sensitive = item.key in SENSITIVE_CONFIG_KEYS
    preserve_existing = str(data.get('preserve_existing', '')).lower() in {'1', 'true', 'yes', 'on'}
    value = data.get('value')
    if preserve_existing:
        plain_value = SiteConfig.get(item.key, '')
    else:
        plain_value = '' if value is None else str(value).strip()
    if item.key == 'trongrid_api_key' and plain_value:
        plain_value = '\n'.join(parse_trongrid_api_keys(plain_value))
        if not plain_value:
            return _error('TRON API Key 至少要有一个有效值', status=400)
    if item.key == 'cloud_asset_sync_interval_seconds':
        try:
            interval_seconds = int(plain_value)
        except (TypeError, ValueError):
            return _error('代理同步间隔必须是秒数整数', status=400)
        if interval_seconds < 60:
            return _error('代理同步间隔不能小于60秒', status=400)
        plain_value = str(interval_seconds)
    if item.key in {'bot_admin_chat_id', 'bot_notice_copy_chat_ids'}:
        normalized = (
            plain_value
            .replace('，', ',')
            .replace('；', ',')
            .replace(';', ',')
            .replace('\n', ',')
        )
        parsed_ids = []
        for part in normalized.split(','):
            candidate = part.strip()
            if not candidate:
                continue
            try:
                parsed_ids.append(str(int(candidate)))
            except Exception:
                label = '抄送 Chat ID' if item.key == 'bot_notice_copy_chat_ids' else '管理员转发 Chat ID'
                return _error(f'{label} 格式不正确：{candidate}', status=400)
        if item.key == 'bot_admin_chat_id' and not parsed_ids:
            return _error('管理员转发 Chat ID 至少要有一个有效值', status=400)
        plain_value = ','.join(dict.fromkeys(parsed_ids))
    if item.key in {'cloud_suspend_time', 'cloud_delete_time', 'cloud_unattached_ip_delete_time'} and plain_value:
        raw_time = plain_value.replace('～', '-').replace('—', '-').replace('–', '-').strip()
        try:
            if '-' in raw_time:
                start_raw, end_raw = raw_time.split('-', 1)
                start_hour, start_minute = _parse_runtime_time_point(start_raw, '15:00')
                end_hour, end_minute = _parse_runtime_time_point(end_raw, '15:00')
                plain_value = f'{start_hour:02d}:{start_minute:02d}-{end_hour:02d}:{end_minute:02d}'
            else:
                hour, minute = _parse_runtime_time_point(raw_time, '15:00')
                plain_value = f'{hour:02d}:{minute:02d}'
        except Exception:
            return _error('生命周期执行时间格式不正确，请使用 HH:mm 或 HH:mm-HH:mm', status=400)
    sort_order_raw = data.get('sort_order')
    if sort_order_raw is not None:
        try:
            item.sort_order = int(sort_order_raw)
        except (TypeError, ValueError):
            return _error('排序必须是整数', status=400)
        item.save(update_fields=['sort_order'])
    SiteConfig.set(item.key, plain_value, sensitive=is_sensitive)
    try:
        from core.cache import cache_config_value
        cache_config_value(item.key, plain_value)
    except Exception:
        pass
    item = SiteConfig.objects.get(id=item.id)
    return _ok(_site_config_payload(item))
