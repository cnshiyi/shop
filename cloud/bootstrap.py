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
if command -v sudo >/dev/null 2>&1; then
  SUDO='sudo'
else
  SUDO=''
fi
$SUDO apt-get update -y
$SUDO apt-get install -y ca-certificates curl wget sudo procps
printf '%s\n' 'net.core.default_qdisc=fq' 'net.ipv4.tcp_congestion_control=bbr' | $SUDO tee /etc/sysctl.d/99-bbr.conf >/dev/null
$SUDO /usr/sbin/sysctl --system || $SUDO sysctl --system
$SUDO /usr/sbin/sysctl net.ipv4.tcp_congestion_control || $SUDO sysctl net.ipv4.tcp_congestion_control
'''

def _build_mtproxy_script(port: int) -> str:
    return rf'''#!/usr/bin/env bash
set -e
if command -v sudo >/dev/null 2>&1; then
  SUDO='sudo'
else
  SUDO=''
fi
mkdir -p /tmp/mtproxy-work
cd /tmp/mtproxy-work
curl -s -o mtproxy.sh https://raw.githubusercontent.com/ellermister/mtproxy/master/mtproxy.sh
chmod +x mtproxy.sh
printf '%s\n' '{port}' | bash mtproxy.sh
$SUDO mkdir -p {MTPROXY_DIR}
$SUDO cp -f mtproxy.sh {MTPROXY_DIR}/mtproxy.sh || true
if command -v ufw >/dev/null 2>&1; then
  $SUDO ufw allow {port}/tcp || true
  $SUDO ufw allow {port}/udp || true
fi
SECRET=""
for file in $(find {MTPROXY_DIR} /etc /usr/local -maxdepth 4 -type f 2>/dev/null); do
  secret=$(grep -Eo '([0-9a-fA-F]{{32}}|ee[0-9a-fA-F]{{32}})' "$file" 2>/dev/null | head -n 1 || true)
  if [ -n "$secret" ]; then
    SECRET="$secret"
    break
  fi
done
if [ -z "$SECRET" ]; then
  SECRET=$(ps -ef | grep -i mtproto-proxy | grep -v grep | grep -Eo '([0-9a-fA-F]{{32}}|ee[0-9a-fA-F]{{32}})' | head -n 1 || true)
fi
if [ -n "$SECRET" ]; then
  echo "MTPROXY_SECRET=${{SECRET}}"
  echo "MTPROXY_PORT={port}"
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
    ok, output = await _run_ssh_script(ip, username, password, _build_mtproxy_script(port))
    secret = ''
    actual_port = str(port)
    for line in output.splitlines():
        if line.startswith('MTPROXY_SECRET='):
            secret = line.split('=', 1)[1].strip()
        elif line.startswith('MTPROXY_PORT='):
            actual_port = line.split('=', 1)[1].strip()
    if secret:
        tg_link = f'tg://proxy?server={ip}&port={actual_port}&secret={secret}'
        tme_link = f'https://t.me/proxy?server={ip}&port={actual_port}&secret={secret}'
        return ok, f'MTProxy 安装完成\n端口: {actual_port}\nTG链接: {tg_link}\n分享链接: {tme_link}'
    return ok, output.replace('BBR 初始化', 'MTProxy 安装')


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
