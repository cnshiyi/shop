import asyncio
import logging

logger = logging.getLogger(__name__)

DEBIAN_BBR_SCRIPT = r'''#!/usr/bin/env bash
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl wget sudo
cat >/etc/sysctl.d/99-bbr.conf <<'EOF'
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
EOF
sysctl --system
sysctl net.ipv4.tcp_congestion_control
'''


async def install_bbr(ip: str, username: str, password: str) -> tuple[bool, str]:
    if not ip or not username or not password:
        return False, '缺少 SSH 连接参数，无法执行 BBR 初始化。'
    logger.info('预留 BBR 初始化流程 ip=%s user=%s', ip, username)
    await asyncio.sleep(0)
    return False, 'BBR 初始化脚本已写入，待接入真实 SSH 执行链路。'
