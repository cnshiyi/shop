import os

from dotenv import load_dotenv
from pathlib import Path

from core.runtime_config import get_runtime_config

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=True)

BOT_TOKEN = get_runtime_config('bot_token', os.getenv('BOT_TOKEN', ''))
REDIS_URL = get_runtime_config('redis_url', os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/0'))
FSM_STATE_TTL = int(os.getenv('FSM_STATE_TTL', '1800'))
FSM_DATA_TTL = int(os.getenv('FSM_DATA_TTL', '1800'))
