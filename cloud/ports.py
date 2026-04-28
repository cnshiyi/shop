MTPROXY_DEFAULT_PORT = 9528
MTPROXY_SPECIAL_LOW_PORTS = {443}
MTPROXY_LOW_PORT_BACKUP_BASE = 20000
MTPROXY_RESERVED_SLOTS = 6


def is_valid_mtproxy_main_port(port: int) -> bool:
    if port in MTPROXY_SPECIAL_LOW_PORTS:
        return True
    return 1025 <= port <= 65535 - MTPROXY_RESERVED_SLOTS + 1


def mtproxy_port_validation_hint() -> str:
    return '端口格式不正确，请输入 443 或 1025-65530 之间的数字。'


def get_mtproxy_port_plan(main_port: int | str | None) -> dict[str, int]:
    main = int(main_port or MTPROXY_DEFAULT_PORT)
    if main < 1025:
        backup_start = MTPROXY_LOW_PORT_BACKUP_BASE + main + 1
    else:
        backup_start = main + 1
    return {
        'main': main,
        'backup': backup_start,
        'telemt_all': backup_start + 1,
        'telemt_classic': backup_start + 2,
        'telemt_secure': backup_start + 3,
        'telemt_tls': backup_start + 4,
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
