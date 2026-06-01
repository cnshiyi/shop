import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)
FERNET_TOKEN_PREFIX = 'gAAAA'


def _build_key() -> bytes:
    raw = os.getenv('CONFIG_ENCRYPTION_KEY') or os.getenv('SECRET_KEY', 'dev-secret-key-change-me')
    digest = hashlib.sha256(raw.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)


def get_cipher() -> Fernet:
    return Fernet(_build_key())


def encrypt_text(value: str) -> str:
    if not value:
        return ''
    return get_cipher().encrypt(value.encode('utf-8')).decode('utf-8')


def _looks_like_fernet_token(value: str) -> bool:
    return str(value or '').strip().startswith(FERNET_TOKEN_PREFIX)


def decrypt_text(value: str) -> str:
    if not value:
        return ''
    try:
        return get_cipher().decrypt(value.encode('utf-8')).decode('utf-8')
    except InvalidToken:
        if _looks_like_fernet_token(value):
            logger.warning('CONFIG_DECRYPT_INVALID_TOKEN prefix=%s', str(value)[:8])
            return ''
        return value
