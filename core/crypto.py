import base64
from contextlib import contextmanager
import hashlib
import os
import warnings

from django.core.exceptions import ImproperlyConfigured
from cryptography.fernet import Fernet, InvalidToken


class SecretDecryptionError(ValueError):
    """Raised when an encrypted secret cannot be decrypted with the active key."""


_ALLOW_LEGACY_SECRET_KEY_DECRYPTION = False


@contextmanager
def allow_legacy_secret_key_decryption():
    global _ALLOW_LEGACY_SECRET_KEY_DECRYPTION
    previous = _ALLOW_LEGACY_SECRET_KEY_DECRYPTION
    _ALLOW_LEGACY_SECRET_KEY_DECRYPTION = True
    try:
        yield
    finally:
        _ALLOW_LEGACY_SECRET_KEY_DECRYPTION = previous


def _encode_key(raw: str) -> bytes:
    digest = hashlib.sha256(raw.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)


def _encryption_secret() -> str:
    raw = os.getenv('CONFIG_ENCRYPTION_KEY')
    if not raw:
        if os.getenv('DJANGO_TEST_SQLITE', '0') == '1':
            raw = 'test-only-config-encryption-key'
        else:
            raise ImproperlyConfigured('必须配置 CONFIG_ENCRYPTION_KEY，用于加密敏感配置。')
    if raw == 'dev-secret-key-change-me':
        raise ImproperlyConfigured('禁止使用默认弱密钥，请配置 CONFIG_ENCRYPTION_KEY。')
    return raw


def _build_key() -> bytes:
    return _encode_key(_encryption_secret())


def _legacy_decryption_secrets() -> list[str]:
    if (
        not _ALLOW_LEGACY_SECRET_KEY_DECRYPTION
        and (
            os.getenv('DJANGO_TEST_SQLITE', '0') == '1'
            or os.getenv('DEBUG', '1') != '1'
        )
    ):
        return []
    legacy_secret = os.getenv('SECRET_KEY') or 'dev-secret-key-change-me'
    if legacy_secret == os.getenv('CONFIG_ENCRYPTION_KEY'):
        return []
    warnings.warn(
        '正在使用 SECRET_KEY 兼容解密历史敏感配置。请设置 CONFIG_ENCRYPTION_KEY 并重新保存敏感配置完成迁移。',
        RuntimeWarning,
        stacklevel=2,
    )
    return [legacy_secret]


def get_cipher() -> Fernet:
    return Fernet(_build_key())


def encrypt_text(value: str) -> str:
    if not value:
        return ''
    return get_cipher().encrypt(value.encode('utf-8')).decode('utf-8')


def decrypt_text(value: str) -> str:
    if not value:
        return ''
    if not value.startswith('gAAAA'):
        return value
    keys: list[bytes] = []
    try:
        keys.append(_build_key())
    except ImproperlyConfigured:
        keys.extend(_encode_key(secret) for secret in _legacy_decryption_secrets())
    else:
        keys.extend(_encode_key(secret) for secret in _legacy_decryption_secrets())
    if not keys:
        raise SecretDecryptionError('敏感配置解密失败：缺少 CONFIG_ENCRYPTION_KEY。')
    last_error = None
    for key in keys:
        try:
            return Fernet(key).decrypt(value.encode('utf-8')).decode('utf-8')
        except InvalidToken as exc:
            last_error = exc
            continue
    raise SecretDecryptionError('敏感配置解密失败，请确认 CONFIG_ENCRYPTION_KEY 是否正确。') from last_error
