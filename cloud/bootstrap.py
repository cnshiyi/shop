import asyncio
import logging
import socket

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

MTPROXY_DIR = '/home/mtproxy'
MTPROXY_PORT = 9528

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

def _build_mtproxy_script(port: int) -> str:
    return rf'''#!/usr/bin/env bash
set -e
mkdir -p {MTPROXY_DIR}
cd {MTPROXY_DIR}
curl -s -o mtproxy.sh https://raw.githubusercontent.com/ellermister/mtproxy/master/mtproxy.sh
chmod +x mtproxy.sh
printf '%s\n' '{port}' | bash mtproxy.sh
if command -v ufw >/dev/null 2>&1; then
  ufw allow {port}/tcp || true
  ufw allow {port}/udp || true
fi
'''




async def install_bbr(ip: str, username: str, password: str) -> tuple[bool, str]:
    if not ip or not username or not password:
        return False, '缺少 SSH 连接参数，无法执行 BBR 初始化。'
    logger.info('开始执行 BBR 初始化 ip=%s user=%s', ip, username)
    return await _run_ssh_script(ip, username, password, DEBIAN_BBR_SCRIPT)


async def install_mtproxy(ip: str, username: str, password: str, port: int = MTPROXY_PORT) -> tuple[bool, str]:
    if not ip or not username or not password:
        return False, '缺少 SSH 连接参数，无法执行 MTProxy 安装。'
    logger.info('开始执行 MTProxy 安装 ip=%s user=%s port=%s', ip, username, port)
    return await _run_ssh_script(ip, username, password, _build_mtproxy_script(port))


@sync_to_async
def _run_ssh_script(ip: str, username: str, password: str, script: str) -> tuple[bool, str]:
    try:
        import paramiko
    except ImportError:
        return False, '未安装 paramiko，无法通过 SSH 执行 BBR 初始化。'

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=ip,
            port=22,
            username=username,
            password=password,
            timeout=30,
            banner_timeout=30,
            auth_timeout=30,
            look_for_keys=False,
            allow_agent=False,
        )
        stdin, stdout, stderr = client.exec_command(f"bash -s <<'EOF'\n{script}\nEOF", timeout=300)
        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode('utf-8', errors='ignore').strip()
        error = stderr.read().decode('utf-8', errors='ignore').strip()
        if exit_code == 0:
            return True, f'BBR 初始化完成: {output[-500:]}'
        return False, f'BBR 初始化失败(exit={exit_code}): {(error or output)[-500:]}'
    except (socket.timeout, TimeoutError) as exc:
        return False, f'SSH 连接超时，BBR 初始化未完成: {exc}'
    except Exception as exc:
        return False, f'SSH 执行 BBR 初始化失败: {exc}'
    finally:
        client.close()
