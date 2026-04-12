import asyncio
import logging
import socket
import time

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
    ready, message = await _wait_ssh_password_ready(ip, username, password)
    if not ready:
        return False, message
    return await _run_ssh_script(ip, username, password, DEBIAN_BBR_SCRIPT)


def build_mtproxy_links(ip: str, port: int | str, secret: str) -> tuple[str, str]:
    tg_link = f'tg://proxy?server={ip}&port={port}&secret={secret}'
    tme_link = f'https://t.me/proxy?server={ip}&port={port}&secret={secret}'
    return tg_link, tme_link


async def install_mtproxy(ip: str, username: str, password: str, port: int = MTPROXY_PORT) -> tuple[bool, str]:
    if not ip or not username or not password:
        return False, '缺少 SSH 连接参数，无法执行 MTProxy 安装。'
    logger.info('开始执行 MTProxy 安装 ip=%s user=%s port=%s', ip, username, port)
    ready, message = await _wait_ssh_password_ready(ip, username, password)
    if not ready:
        return False, message.replace('BBR 初始化', 'MTProxy 安装')
    ok, output = await _run_ssh_script(ip, username, password, _build_mtproxy_script(port))
    secret = ''
    actual_port = str(port)
    for line in output.splitlines():
        if line.startswith('MTPROXY_SECRET='):
            secret = line.split('=', 1)[1].strip()
        elif line.startswith('MTPROXY_PORT='):
            actual_port = line.split('=', 1)[1].strip()
    if secret:
        tg_link, tme_link = build_mtproxy_links(ip, actual_port, secret)
        return ok, f'MTProxy 安装完成\n端口: {actual_port}\nTG链接: {tg_link}\n分享链接: {tme_link}'
    return ok, output.replace('BBR 初始化', 'MTProxy 安装')


@sync_to_async
def _wait_ssh_port(ip: str, timeout: int = 600, interval: int = 5) -> tuple[bool, str]:
    end_time = time.time() + timeout
    last_error = ''
    while time.time() < end_time:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect((ip, 22))
            return True, 'SSH 22 端口已就绪。'
        except Exception as exc:
            last_error = str(exc)
            time.sleep(interval)
        finally:
            try:
                sock.close()
            except Exception:
                pass
    return False, f'SSH 22 端口长时间未就绪，可能实例防火墙未放通或系统仍在初始化: {last_error}'


@sync_to_async
def _wait_ssh_password_ready(ip: str, username: str, password: str, timeout: int = 900, interval: int = 10) -> tuple[bool, str]:
    port_ready, message = _wait_ssh_port.__wrapped__(ip, 600, 5)
    if not port_ready:
        return False, message
    try:
        import paramiko
    except ImportError:
        return False, '未安装 paramiko，无法探测 SSH 密码登录是否就绪。'

    end_time = time.time() + timeout
    last_error = ''
    while time.time() < end_time:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=ip,
                port=22,
                username=username,
                password=password,
                timeout=20,
                banner_timeout=20,
                auth_timeout=20,
                look_for_keys=False,
                allow_agent=False,
            )
            return True, 'SSH 密码登录已就绪。'
        except Exception as exc:
            last_error = str(exc)
            time.sleep(interval)
        finally:
            try:
                client.close()
            except Exception:
                pass
    return False, f'SSH 密码登录长时间未就绪，系统可能仍在重装或密码尚未生效: {last_error}'


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
