"""Timestamp-based order number helpers."""

from __future__ import annotations

from collections.abc import Callable

from django.utils import timezone


def _clean_part(value: str | None) -> str:
    return ''.join(ch for ch in str(value or '').upper() if ch.isalnum())


def timestamp_order_no(prefix: str, *, tag: str | None = None, max_length: int = 191) -> str:
    stamp = timezone.now().strftime('%Y%m%d%H%M%S%f')
    clean_prefix = _clean_part(prefix)
    clean_tag = _clean_part(tag)
    value = f'{clean_prefix}{stamp}'
    if clean_tag:
        value = f'{value}{clean_tag}'
    return value[:max_length]


def unique_timestamp_order_no(
    prefix: str,
    exists: Callable[[str], bool],
    *,
    tag: str | None = None,
    max_length: int = 191,
) -> str:
    base = timestamp_order_no(prefix, tag=tag, max_length=max_length)
    candidate = base
    counter = 1
    while exists(candidate):
        suffix = f'{counter:02d}'
        candidate = f'{base[: max(0, max_length - len(suffix))]}{suffix}'
        counter += 1
    return candidate
