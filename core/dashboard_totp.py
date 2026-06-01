"""TOTP helpers for dashboard authentication."""

import base64
import binascii
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from core.runtime_config import get_runtime_config


def normalize_totp_secret(secret: str) -> str:
    return ''.join(ch for ch in str(secret or '').upper() if ch.isalnum()).rstrip('=')


def dashboard_totp_secret():
    return normalize_totp_secret(get_runtime_config('dashboard_totp_secret', ''))


def generate_totp_secret():
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
    return ''.join(secrets.choice(alphabet) for _ in range(32))


def totp_otpauth_url(secret: str, username: str = 'admin'):
    issuer = 'Shop Admin'
    account = username or 'admin'
    label = f'{quote(issuer, safe="")}:{quote(account, safe="")}'
    normalized_secret = normalize_totp_secret(secret)
    return (
        'otpauth://totp/'
        f'{label}?secret={quote(normalized_secret, safe="")}&issuer={quote(issuer, safe="")}&algorithm=SHA1&digits=6&period=30'
    )


def _totp_code(secret: str, counter: int) -> str:
    secret = normalize_totp_secret(secret)
    padding = '=' * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode((secret + padding).upper(), casefold=True)
    digest = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f'{value % 1_000_000:06d}'


def verify_totp_token(token: str, secret: str) -> bool:
    token = ''.join(ch for ch in str(token or '') if ch.isdigit())
    secret = normalize_totp_secret(secret)
    if len(token) != 6 or not secret:
        return False
    try:
        current_counter = int(time.time()) // 30
        for drift in (-1, 0, 1):
            if hmac.compare_digest(_totp_code(secret, current_counter + drift), token):
                return True
    except (binascii.Error, ValueError):
        return False
    return False
