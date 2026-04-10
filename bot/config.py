import os

from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=True)

BOT_TOKEN = os.getenv('BOT_TOKEN', '')
REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
FSM_STATE_TTL = int(os.getenv('FSM_STATE_TTL', '1800'))
FSM_DATA_TTL = int(os.getenv('FSM_DATA_TTL', '1800'))
