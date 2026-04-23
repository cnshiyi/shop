import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


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


def decrypt_text(value: str) -> str:
    if not value:
        return ''
    try:
        return get_cipher().decrypt(value.encode('utf-8')).decode('utf-8')
    except InvalidToken:
        return value
