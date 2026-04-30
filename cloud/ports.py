MTPROXY_DEFAULT_PORT = 9528
MTPROXY_SPECIAL_LOW_PORTS = {443}
MTPROXY_FIXED_BACKUP_PORTS = (9529, 9530, 9531, 9532, 9533)
MTPROXY_RESERVED_SLOTS = 6


def is_valid_mtproxy_main_port(port: int) -> bool:
    if port in MTPROXY_SPECIAL_LOW_PORTS:
        return True
    if port in MTPROXY_FIXED_BACKUP_PORTS:
        return False
    return 1025 <= port <= 65535 - MTPROXY_RESERVED_SLOTS + 1


def mtproxy_port_validation_hint() -> str:
    return '端口格式不正确，请输入 443 或 1025-65530 之间的数字；9529-9533 为备用链路固定端口，不能作为主端口。'


def get_mtproxy_port_plan(main_port: int | str | None) -> dict[str, int]:
    main = int(main_port or MTPROXY_DEFAULT_PORT)
    backup, telemt_all, telemt_classic, telemt_secure, telemt_tls = MTPROXY_FIXED_BACKUP_PORTS
    return {
        'main': main,
        'backup': backup,
        'telemt_all': telemt_all,
        'telemt_classic': telemt_classic,
        'telemt_secure': telemt_secure,
        'telemt_tls': telemt_tls,
    }


def get_mtproxy_public_ports(main_port: int | str | None) -> list[int]:
    plan = get_mtproxy_port_plan(main_port)
    return [
        plan['main'],
        plan['backup'],
        plan['telemt_all'],
        plan['telemt_classic'],
        plan['telemt_secure'],
        plan['telemt_tls'],
    ]


def get_mtproxy_port_label(main_port: int | str | None, port: int | str | None) -> str:
    try:
        port_value = int(port or 0)
    except (TypeError, ValueError):
        return 'MTProxy'
    plan = get_mtproxy_port_plan(main_port)
    labels = {
        plan['main']: '主代理 mtg',
        plan['backup']: '备用 mtprotoproxy',
        plan['telemt_all']: 'Telemt A 三模式',
        plan['telemt_classic']: 'Telemt B classic',
        plan['telemt_secure']: 'Telemt B dd secure',
        plan['telemt_tls']: 'Telemt B ee tls',
    }
    return labels.get(port_value, f'端口 {port_value}')
