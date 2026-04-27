import os

from dotenv import load_dotenv

from core.runtime_config import get_runtime_config
from core.cache import build_redis_url

load_dotenv()

BOT_TOKEN = get_runtime_config('bot_token', os.getenv('BOT_TOKEN', ''))
RECEIVE_ADDRESS = get_runtime_config('receive_address', os.getenv('RECEIVE_ADDRESS', ''))
TRONGRID_API_KEY = get_runtime_config('trongrid_api_key', os.getenv('TRONGRID_API_KEY', ''))
REDIS_URL = build_redis_url()
FSM_STATE_TTL = int(get_runtime_config('fsm_state_ttl', os.getenv('FSM_STATE_TTL', '3600')))
FSM_DATA_TTL = int(get_runtime_config('fsm_data_ttl', os.getenv('FSM_DATA_TTL', '3600')))
