import itertools
import re

from core.cache import get_config

_key_counter = itertools.count()


def parse_trongrid_api_keys(raw: str) -> list[str]:
    parts = re.split(r'[\s,;，；]+', str(raw or '').strip())
    return list(dict.fromkeys(part.strip() for part in parts if part.strip()))


async def get_trongrid_api_key() -> str:
    keys = parse_trongrid_api_keys(await get_config('trongrid_api_key', ''))
    if not keys:
        return ''
    return keys[next(_key_counter) % len(keys)]


async def build_trongrid_headers() -> dict[str, str]:
    headers = {'accept': 'application/json', 'content-type': 'application/json'}
    api_key = await get_trongrid_api_key()
    if api_key:
        headers['TRON-PRO-API-KEY'] = api_key
    return headers
