import asyncio
import json
import threading
from copy import deepcopy

from core.models import SiteConfig

BUTTON_CONFIG_KEY = 'bot_button_config'

FIXED_BUTTONS = [
    {'key': 'custom_node', 'label': '🛠 定制节点', 'type': 'business', 'sort_order': 10, 'enabled': True, 'locked': True},
    {'key': 'expiry_query', 'label': '🔎 到期时间查询', 'type': 'business', 'sort_order': 20, 'enabled': True, 'locked': True},
    {'key': 'profile', 'label': '👤 个人中心', 'type': 'business', 'sort_order': 30, 'enabled': True, 'locked': True},
]


def _default_config():
    return {'row_size': 2, 'items': deepcopy(FIXED_BUTTONS)}


def _site_config_get_safe(key: str, default: str = '') -> str:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return SiteConfig.get(key, default)
    result = {'value': default}

    def _read():
        result['value'] = SiteConfig.get(key, default)

    thread = threading.Thread(target=_read)
    thread.start()
    thread.join(timeout=5)
    return result['value'] or default


def load_button_config():
    raw = _site_config_get_safe(BUTTON_CONFIG_KEY, '')
    try:
        config = json.loads(raw) if raw else {}
    except Exception:
        config = {}
    row_size = int(config.get('row_size') or 2)
    row_size = min(max(row_size, 1), 4)
    stored_items = config.get('items') if isinstance(config.get('items'), list) else []
    by_key = {str(item.get('key')): item for item in stored_items if isinstance(item, dict) and item.get('key')}
    items = []
    for fixed in FIXED_BUTTONS:
        stored = by_key.get(fixed['key'], {})
        merged = {**fixed, 'sort_order': int(stored.get('sort_order', fixed['sort_order']) or fixed['sort_order'])}
        items.append(merged)
    for item in stored_items:
        if not isinstance(item, dict) or item.get('type') != 'link':
            continue
        label = str(item.get('label') or '').strip()
        url = str(item.get('url') or '').strip()
        if not label or not url:
            continue
        button_label = str(item.get('button_label') or item.get('button_text') or label).strip()
        message = str(item.get('message') or item.get('reply_message') or '').strip()
        items.append({
            'key': str(item.get('key') or f'link_{len(items) + 1}'),
            'label': label,
            'button_label': button_label,
            'message': message,
            'url': url,
            'type': 'link',
            'sort_order': int(item.get('sort_order') or 100),
            'enabled': bool(item.get('enabled', True)),
            'locked': False,
        })
    return {'row_size': row_size, 'items': sorted(items, key=lambda x: (int(x.get('sort_order') or 0), str(x.get('key') or '')))}


def _normalize_button_url(url: str) -> str:
    value = str(url or '').strip()
    if value.startswith('@') and len(value) > 1:
        username = value[1:].strip()
        if username.replace('_', '').isalnum():
            return f'https://t.me/{username}'
    return value


def save_button_config(config):
    normalized = load_button_config()
    row_size = int(config.get('row_size') or normalized['row_size'] or 2)
    row_size = min(max(row_size, 1), 4)
    incoming = config.get('items') if isinstance(config.get('items'), list) else []
    incoming_by_key = {str(item.get('key')): item for item in incoming if isinstance(item, dict) and item.get('key')}
    items = []
    for fixed in FIXED_BUTTONS:
        item = incoming_by_key.get(fixed['key'], {})
        items.append({**fixed, 'sort_order': int(item.get('sort_order', fixed['sort_order']) or fixed['sort_order'])})
    for item in incoming:
        if not isinstance(item, dict) or item.get('type') != 'link':
            continue
        label = str(item.get('label') or '').strip()
        url = _normalize_button_url(str(item.get('url') or '').strip())
        if not label or not (url.startswith('http://') or url.startswith('https://') or url.startswith('tg://')):
            continue
        key = str(item.get('key') or '').strip() or f'link_{len(items) + 1}'
        if key in {fixed['key'] for fixed in FIXED_BUTTONS}:
            key = f'link_{key}'
        button_label = str(item.get('button_label') or item.get('button_text') or label).strip()
        message = str(item.get('message') or item.get('reply_message') or '').strip()
        items.append({
            'key': key,
            'label': label,
            'button_label': button_label,
            'message': message,
            'url': url,
            'type': 'link',
            'sort_order': int(item.get('sort_order') or 100),
            'enabled': bool(item.get('enabled', True)),
            'locked': False,
        })
    saved = {'row_size': row_size, 'items': sorted(items, key=lambda x: (int(x.get('sort_order') or 0), str(x.get('key') or '')))}
    SiteConfig.set(BUTTON_CONFIG_KEY, json.dumps(saved, ensure_ascii=False), sensitive=False)
    return saved


def init_button_config():
    if not SiteConfig.get(BUTTON_CONFIG_KEY, ''):
        return save_button_config(_default_config())
    return load_button_config()
