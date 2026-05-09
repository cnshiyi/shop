import asyncio
import inspect
import ipaddress
import logging

logger = logging.getLogger(__name__)


def normalize_public_ip(value) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return ''
    if ip.version != 4:
        return ''
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return ''
    return str(ip)


def validate_server_connection_ip(target_ip, expected_ips, *, context: str = 'server_connection') -> tuple[bool, str]:
    target = normalize_public_ip(target_ip)
    expected = [normalize_public_ip(item) for item in (expected_ips or [])]
    expected = [item for item in expected if item]
    if not target:
        note = f'服务器连接前 IP 校验失败：目标 IP 无效，已停止连接。目标={target_ip or "-"}。'
        logger.warning('SERVER_CONNECTION_IP_GUARD_FAIL context=%s reason=invalid_target target=%s expected=%s', context, target_ip, expected)
        return False, note
    if not expected:
        note = f'服务器连接前 IP 校验失败：缺少预期 IP，已停止连接。目标={target}。'
        logger.warning('SERVER_CONNECTION_IP_GUARD_FAIL context=%s reason=missing_expected target=%s expected=%s', context, target, expected)
        return False, note
    if target not in expected:
        note = f'服务器连接前 IP 校验失败：目标 IP {target} 与预期 IP {" / ".join(expected)} 不一致，已停止连接。'
        logger.warning('SERVER_CONNECTION_IP_GUARD_FAIL context=%s reason=mismatch target=%s expected=%s', context, target, expected)
        return False, note
    logger.info('SERVER_CONNECTION_IP_GUARD_OK context=%s target=%s expected=%s', context, target, expected)
    return True, '服务器连接前 IP 校验通过'


def _is_retryable_mismatch(target_ip, expected_ips) -> bool:
    target = normalize_public_ip(target_ip)
    expected = [normalize_public_ip(item) for item in (expected_ips or [])]
    expected = [item for item in expected if item]
    return bool(target and expected and target not in expected)


async def validate_server_connection_ip_with_retry(
    target_ip,
    expected_ips,
    *,
    context: str = 'server_connection',
    attempts: int = 3,
    delay_seconds: float = 5,
    refresh_target=None,
    sleep=asyncio.sleep,
) -> tuple[bool, str, str]:
    attempts = max(1, int(attempts or 1))
    current_target = target_ip
    last_note = ''
    for attempt in range(1, attempts + 1):
        ok, note = validate_server_connection_ip(current_target, expected_ips, context=f'{context}:attempt:{attempt}')
        if ok:
            if attempt > 1:
                return True, f'{note}（第 {attempt} 次校验通过）', normalize_public_ip(current_target)
            return True, note, normalize_public_ip(current_target)
        last_note = note
        if attempt >= attempts or not _is_retryable_mismatch(current_target, expected_ips):
            break
        logger.warning(
            'SERVER_CONNECTION_IP_GUARD_RETRY context=%s attempt=%s/%s target=%s expected=%s',
            context,
            attempt,
            attempts,
            current_target,
            [normalize_public_ip(item) for item in (expected_ips or []) if normalize_public_ip(item)],
        )
        if delay_seconds > 0:
            await sleep(delay_seconds)
        if refresh_target:
            refreshed = refresh_target()
            if inspect.isawaitable(refreshed):
                refreshed = await refreshed
            if refreshed:
                current_target = refreshed
    retry_note = f'{last_note} 已重试 {attempts} 次，仍未匹配预期 IP。' if attempts > 1 and _is_retryable_mismatch(current_target, expected_ips) else last_note
    return False, retry_note, normalize_public_ip(current_target)
