import base64
import hashlib
import os

from django.core.exceptions import ImproperlyConfigured
from cryptography.fernet import Fernet, InvalidToken


def _build_key() -> bytes:
    raw = os.getenv('CONFIG_ENCRYPTION_KEY') or os.getenv('SECRET_KEY')
    if not raw:
        if os.getenv('DEBUG', '1') == '1':
            raw = 'dev-secret-key-change-me'
        else:
            raise ImproperlyConfigured('生产环境必须配置 CONFIG_ENCRYPTION_KEY 或 SECRET_KEY。')
    if os.getenv('DEBUG', '1') != '1' and raw == 'dev-secret-key-change-me':
        raise ImproperlyConfigured('生产环境禁止使用默认弱密钥，请配置 CONFIG_ENCRYPTION_KEY。')
    digest = hashlib.sha256(raw.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)


def get_cipher() -> Fernet:
    return Fernet(_build_key())


def encrypt_text(value: str) -> str:
    if not value:
        return ''
    return get_cipher().encrypt(value.encode('utf-8')).decode('utf-8')


def decrypt_text(value: str) -> str:
    if not value:
        return ''
    try:
        return get_cipher().decrypt(value.encode('utf-8')).decode('utf-8')
    except InvalidToken:
        return value
