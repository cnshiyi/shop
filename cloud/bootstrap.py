import asyncio
import binascii
import logging
import os
import socket
import time
from pathlib import Path

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

MTPROXY_DIR = '/home/mtproxy'
MTPROXY_PORT = 9528
MTPROXY_FAKE_TLS_DOMAIN = 'azure.microsoft.com'
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIGHTSAIL_KEY_DIR = Path.home() / '.shop-secrets' / 'lightsail'


def _load_aws_public_key() -> str:
    env_value = (os.getenv('AWS_LIGHTSAIL_PUBLIC_KEY') or '').strip()
    if env_value:
        return env_value

    public_key_path = (os.getenv('AWS_LIGHTSAIL_PUBLIC_KEY_PATH') or '').strip()
    private_key_dir = (os.getenv('AWS_LIGHTSAIL_PRIVATE_KEY_DIR') or '').strip()
    candidates = [public_key_path] if public_key_path else []
    if private_key_dir:
        key_dir = Path(private_key_dir)
    else:
        key_dir = DEFAULT_LIGHTSAIL_KEY_DIR
    if key_dir.is_dir():
        for pattern in ('*.pub',):
            candidates.extend(str(path) for path in sorted(key_dir.glob(pattern)))
    candidates.extend([
        os.path.expanduser('~/.ssh/id_ed25519.pub'),
        os.path.expanduser('~/.ssh/id_rsa.pub'),
        os.path.expanduser('~/Documents/WindowsPowerShell/id_ed25519.pub'),
        os.path.expanduser('~/Documents/WindowsPowerShell/id_rsa.pub'),
    ])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            with open(candidate, 'r', encoding='utf-8') as handle:
                content = handle.read().strip()
            if content:
                logger.info('已加载 AWS SSH 公钥: source=%s', candidate)
                return content
        except OSError:
            continue
    logger.warning('未找到 AWS SSH 公钥: env_public_key=%s env_public_key_path=%s key_dir=%s', bool(env_value), bool(public_key_path), key_dir)
    return ''


def _iter_private_key_candidates() -> list[Path]:
    private_key_path = (os.getenv('AWS_LIGHTSAIL_PRIVATE_KEY_PATH') or '').strip()
    private_key_dir = (os.getenv('AWS_LIGHTSAIL_PRIVATE_KEY_DIR') or '').strip()
    candidates: list[Path] = []
    if private_key_path:
        candidates.append(Path(private_key_path))

    key_dir = Path(private_key_dir) if private_key_dir else DEFAULT_LIGHTSAIL_KEY_DIR
    if key_dir.is_dir():
        for pattern in ('*.pem', '*.key', 'id_*'):
            for path in sorted(key_dir.glob(pattern)):
                if path.is_file() and not path.name.endswith('.pub'):
                    candidates.append(path)

    for fallback in [
        Path(os.path.expanduser('~/.ssh/id_ed25519')),
        Path(os.path.expanduser('~/.ssh/id_rsa')),
        Path(os.path.expanduser('~/Documents/WindowsPowerShell/id_ed25519')),
        Path(os.path.expanduser('~/Documents/WindowsPowerShell/id_rsa')),
    ]:
        if fallback.is_file():
            candidates.append(fallback)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        normalized = str(path.resolve()) if path.exists() else str(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(path)
    return unique


def _load_aws_private_key():
    try:
        import paramiko
    except ImportError:
        return None, '未安装 paramiko，无法加载 SSH 私钥。'

    last_error = ''
    candidates = _iter_private_key_candidates()
    logger.info('开始扫描 AWS SSH 私钥候选: count=%s key_dir=%s', len(candidates), DEFAULT_LIGHTSAIL_KEY_DIR)
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            try:
                key = paramiko.Ed25519Key.from_private_key_file(str(candidate))
            except Exception:
                key = paramiko.RSAKey.from_private_key_file(str(candidate))
            logger.info('已加载 AWS SSH 私钥: source=%s', candidate)
            return key, str(candidate)
        except Exception as exc:
            last_error = str(exc)
            logger.warning('SSH 私钥加载失败: source=%s error=%s', candidate, last_error)
    return None, last_error


def _build_set_password_script(password: str) -> str:
    escaped = password.replace("'", "'\"'\"'")
    return rf'''#!/usr/bin/env bash
set -euxo pipefail
PASS='{escaped}'
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO='sudo'
  else
    echo '设置密码失败'
    echo '需要 root 权限或 sudo 权限'
    exit 1
  fi
else
  SUDO=''
fi

CHPASSWD_BIN=''
for candidate in /usr/sbin/chpasswd /sbin/chpasswd /usr/bin/chpasswd chpasswd; do
  if command -v "$candidate" >/dev/null 2>&1; then
    CHPASSWD_BIN="$candidate"
    break
  fi
done
if [ -z "$CHPASSWD_BIN" ]; then
  echo '[SET_PASSWORD] chpasswd not found'
  exit 127
fi

echo '[SET_PASSWORD] start'
echo '[SET_PASSWORD] current_user='"$(id -un)"
echo '[SET_PASSWORD] chpasswd_bin='"$CHPASSWD_BIN"
echo '[SET_PASSWORD] set root password'
printf '%s\n' "root:$PASS" | $SUDO "$CHPASSWD_BIN"
if id admin >/dev/null 2>&1; then
  echo '[SET_PASSWORD] set admin password'
  printf '%s\n' "admin:$PASS" | $SUDO "$CHPASSWD_BIN"
fi

echo '[SET_PASSWORD] ensure sshd_config.d'
$SUDO mkdir -p /etc/ssh/sshd_config.d
cat <<'EOF' | $SUDO tee /etc/ssh/sshd_config.d/99-openclaw-password.conf >/dev/null
PasswordAuthentication yes
PermitRootLogin yes
PubkeyAuthentication yes
KbdInteractiveAuthentication yes
ChallengeResponseAuthentication no
UsePAM yes
EOF

echo '[SET_PASSWORD] patch sshd_config'
$SUDO sh -c "grep -q '^PasswordAuthentication ' /etc/ssh/sshd_config && sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config || echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config"
$SUDO sh -c "grep -q '^PermitRootLogin ' /etc/ssh/sshd_config && sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config || echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config"
$SUDO sh -c "grep -q '^KbdInteractiveAuthentication ' /etc/ssh/sshd_config && sed -i 's/^KbdInteractiveAuthentication.*/KbdInteractiveAuthentication yes/' /etc/ssh/sshd_config || echo 'KbdInteractiveAuthentication yes' >> /etc/ssh/sshd_config"
$SUDO sh -c "grep -q '^UsePAM ' /etc/ssh/sshd_config && sed -i 's/^UsePAM.*/UsePAM yes/' /etc/ssh/sshd_config || echo 'UsePAM yes' >> /etc/ssh/sshd_config"

echo '[SET_PASSWORD] unlock root'
$SUDO passwd -u root || true

echo '[SET_PASSWORD] restart ssh service'
if command -v systemctl >/dev/null 2>&1; then
  $SUDO systemctl daemon-reload || true
  if systemctl list-unit-files | grep -q '^ssh\.service'; then
    $SUDO systemctl restart ssh.service
  elif systemctl list-unit-files | grep -q '^sshd\.service'; then
    $SUDO systemctl restart sshd.service
  else
    $SUDO systemctl restart ssh || $SUDO systemctl restart sshd || true
  fi
else
  $SUDO service ssh restart || $SUDO service sshd restart || true
fi

echo 'PASSWORD_SETUP_STATUS=OK'
'''

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

def _build_mtproxy_script(port: int, desired_secret: str = '') -> str:
    desired_secret = (desired_secret or '').strip()
    return rf'''#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo -E bash "$0"
  else
    echo 'MTProxy 安装失败'
    echo '需要 root 权限执行安装脚本'
    exit 1
  fi
fi
SUDO=''
WORKDIR='{MTPROXY_DIR}'
mkdir -p "$WORKDIR"
cd "$WORKDIR"
rm -f mtproxy.sh
curl -fsSL -o mtproxy.sh https://raw.githubusercontent.com/ellermister/mtproxy/master/mtproxy.sh
chmod +x mtproxy.sh
INSTALL_OUTPUT="$(printf '%s\n%s\n%s\n%s\n%s\n' '2' '{port}' '18888' '{MTPROXY_FAKE_TLS_DOMAIN}' '' | bash mtproxy.sh 2>&1 || true)"
printf '%s\n' "$INSTALL_OUTPUT"
STATUS_OUTPUT=''
DESIRED_SECRET='{desired_secret}'
SECRET=$(sed -n 's/^secret="\([^"]*\)"$/\1/p' "$WORKDIR/config" | head -n 1 || true)
if [ -z "$SECRET" ]; then
  SECRET=$(printf '%s\n' "$INSTALL_OUTPUT" | grep -Eo 'secret=[0-9a-fA-F]+' | head -n 1 | cut -d= -f2 || true)
fi
if [ -z "$SECRET" ]; then
  SECRET=$(printf '%s\n' "$INSTALL_OUTPUT" | grep -Eo 'MTProxy Secret:[[:space:]]*[0-9a-fA-F]+' | head -n 1 | grep -Eo '[0-9a-fA-F]+' | tail -n 1 || true)
fi
if [ -z "$SECRET" ] && [ -f "$WORKDIR/run-command.sh" ]; then
  SECRET=$(grep -Eo 'ee[0-9a-fA-F]{{32,}}|[0-9a-fA-F]{{32}}' "$WORKDIR/run-command.sh" | head -n 1 || true)
fi
if [ -z "$SECRET" ]; then
  SECRET=$(ps -ef | grep -iE 'mtproto-proxy|/mtg | mtg run ' | grep -v grep | grep -Eo 'ee[0-9a-fA-F]{{32,}}|[0-9a-fA-F]{{32}}' | head -n 1 || true)
fi
RUN_COMMAND=''
if [ -x "$WORKDIR/bin/mtg" ] && [ -n "$SECRET" ]; then
  RUN_COMMAND="$WORKDIR/bin/mtg run $SECRET -b 0.0.0.0:{port} --multiplex-per-connection 500 --prefer-ip=ipv4 -t 127.0.0.1:18888 -4 $(curl -4 -fsS ifconfig.me || echo 127.0.0.1):{port}"
fi
if [ -z "$RUN_COMMAND" ] && [ -f "$WORKDIR/run-command.sh" ]; then
  RUN_COMMAND=$(grep -v '^#!' "$WORKDIR/run-command.sh" | tail -n 1 || true)
fi
if [ -z "$RUN_COMMAND" ]; then
  RUN_COMMAND=$(ps -ef | grep -iE '/mtg | mtg run |mtproto-proxy' | grep -v grep | head -n 1 || true)
fi
if [ -n "$RUN_COMMAND" ]; then
  RUN_COMMAND=$(printf '%s\n' "$RUN_COMMAND" | sed -E 's/^[^/]*//' | sed -E 's/[[:space:]]+$//')
fi
if [ -n "$DESIRED_SECRET" ]; then
  SECRET="$DESIRED_SECRET"
  if [ -f "$WORKDIR/config" ]; then
    if grep -q '^secret=' "$WORKDIR/config"; then
      sed -i.bak -E "s/^secret=.*/secret=\"$SECRET\"/" "$WORKDIR/config"
    else
      printf '\nsecret="%s"\n' "$SECRET" >> "$WORKDIR/config"
    fi
  fi
fi
if [ -z "$RUN_COMMAND" ]; then
  echo 'MTProxy 安装失败'
  echo '未能解析启动命令'
  echo 'MTPROXY_STATUS=FAILED'
  echo 'MTPROXY_ERROR=未能解析启动命令'
  exit 1
fi
printf '%s\n' '#!/usr/bin/env bash' 'set -e' "$RUN_COMMAND" | tee "$WORKDIR/run-command.sh" >/dev/null
chmod +x "$WORKDIR/run-command.sh"
printf '%s\n' '* soft nofile 655350' '* hard nofile 655350' | tee /etc/security/limits.d/99-mtproxy.conf >/dev/null
if grep -q '^fs.file-max' /etc/sysctl.conf 2>/dev/null; then
  sed -i 's/^fs\.file-max.*/fs.file-max = 655350/' /etc/sysctl.conf
else
  printf '%s\n' 'fs.file-max = 655350' | tee -a /etc/sysctl.conf >/dev/null
fi
/usr/sbin/sysctl -p || sysctl -p || true
SERVICE_FILE=/etc/systemd/system/mtproxy.service
printf '%s\n' \
  '[Unit]' \
  'Description=MTProxy Service' \
  'After=network-online.target' \
  'Wants=network-online.target' \
  '' \
  '[Service]' \
  'Type=simple' \
  'User=root' \
  "WorkingDirectory=$WORKDIR" \
  "ExecStart=/bin/bash $WORKDIR/run-command.sh" \
  'Restart=always' \
  'RestartSec=3' \
  'LimitNOFILE=655350' \
  '' \
  '[Install]' \
  'WantedBy=multi-user.target' | tee "$SERVICE_FILE" >/dev/null
chmod 644 "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable mtproxy.service >/dev/null 2>&1
systemctl restart mtproxy.service
sleep 3
if command -v ufw >/dev/null 2>&1; then
  ufw allow {port}/tcp || true
  ufw allow {port}/udp || true
fi
PROC_OK=0
if ps -ef | grep -i 'mtproto-proxy' | grep -v grep >/dev/null 2>&1; then
  PROC_OK=1
fi
if [ "$PROC_OK" != "1" ]; then
  if ps -ef | grep -iE '/mtg | mtg run ' | grep -v grep >/dev/null 2>&1; then
    PROC_OK=1
  fi
fi
PORT_OK=0
if command -v ss >/dev/null 2>&1; then
  if ss -lntup 2>/dev/null | grep -E '[:.]({port})\b' >/dev/null 2>&1; then
    PORT_OK=1
  fi
elif command -v netstat >/dev/null 2>&1; then
  if netstat -lntup 2>/dev/null | grep -E '[:.]({port})\b' >/dev/null 2>&1; then
    PORT_OK=1
  fi
fi
if ! systemctl is-active --quiet mtproxy.service; then
  systemctl status mtproxy.service --no-pager || true
  journalctl -u mtproxy.service -n 80 --no-pager || true
  echo 'MTProxy 安装失败'
  echo 'systemd 服务未启动'
  echo 'MTPROXY_STATUS=FAILED'
  echo 'MTPROXY_ERROR=systemd 服务未启动'
  exit 1
fi
if [ "$PROC_OK" != "1" ]; then
  echo 'MTProxy 安装失败'
  echo '代理进程未运行'
  echo 'MTPROXY_STATUS=FAILED'
  echo 'MTPROXY_ERROR=代理进程未运行'
  exit 1
fi
echo 'MTPROXY_DAEMON=SYSTEMD'
if [ "$PORT_OK" != "1" ]; then
  echo 'MTProxy 安装失败'
  echo '端口 {port} 未监听'
  echo 'MTPROXY_STATUS=FAILED'
  echo 'MTPROXY_ERROR=端口 {port} 未监听'
  exit 1
fi
if [ -z "$SECRET" ]; then
  echo 'MTProxy 安装失败'
  echo '未能解析 MTProxy Secret'
  echo 'MTPROXY_STATUS=FAILED'
  echo 'MTPROXY_ERROR=未能解析 MTProxy Secret'
  exit 1
fi
echo "MTPROXY_STATUS=OK"
echo "MTPROXY_SECRET=${{SECRET}}"
echo "MTPROXY_PORT={port}"
'''




def _log_multiline_output(prefix: str, text: str):
    for line in (text or '').splitlines():
        logger.info('%s %s', prefix, line)


def _build_bootstrap_full_log(label: str, ip: str, username: str, ok: bool, output: str) -> str:
    status = 'OK' if ok else 'FAILED'
    lines = [
        f'[{label}] ip={ip} user={username} status={status}',
        '----- BEGIN OUTPUT -----',
        (output or '').rstrip(),
        '----- END OUTPUT -----',
    ]
    return '\n'.join(lines)


def _sanitize_mtproxy_output(text: str) -> str:
    lines = []
    for raw_line in (text or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith('MTPROXY_'):
            continue
        if line.startswith('TMProxy+TLS代理: 已停止'):
            continue
        if line.startswith('WARNING: apt does not have a stable CLI interface.'):
            continue
        if line.startswith('debconf:'):
            continue
        if line.startswith('dpkg-preconfigure: unable to re-open stdin:'):
            continue
        if line.startswith('usage: mtg '):
            continue
        if line == 'Simple MTPROTO proxy.':
            continue
        if line == 'Flags:':
            continue
        if line == 'Commands:':
            continue
        if line.startswith('-h, --help Show context-sensitive help'):
            continue
        if line == '--help-man).':
            continue
        if line == '--version Show application version.':
            continue
        if line == 'help [<command>...]':
            continue
        if line == 'Show help.':
            continue
        if line == 'generate-secret [<flags>] <type>':
            continue
        if line == 'Generate new secret':
            continue
        if line == 'run [<flags>] <secret> [<adtag>]':
            continue
        if line == 'Run new proxy instance':
            continue
        lines.append(raw_line)
    return '\n'.join(lines).strip()


@sync_to_async
def _run_ssh_script_with_key(ip: str, usernames: str | list[str], script: str, label: str = 'SSH_KEY') -> tuple[bool, str]:
    try:
        import paramiko
    except ImportError:
        return False, f'未安装 paramiko，无法通过 SSH 公钥执行 {label}。'

    pkey, key_source = _load_aws_private_key()
    if not pkey:
        env_private_key_path = (os.getenv('AWS_LIGHTSAIL_PRIVATE_KEY_PATH') or '').strip()
        return False, f'未找到可用私钥，无法通过 key 登录执行初始化。env_private_key_path={bool(env_private_key_path)} detail={key_source}'

    candidates = [usernames] if isinstance(usernames, str) else list(usernames or [])
    last_error = ''
    remote_script_path = f'/tmp/openclaw_{label.lower()}_key.sh'
    for username in candidates:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            logger.info('开始建立 SSH 公钥连接: stage=%s ip=%s user=%s key_source=%s', label, ip, username, key_source)
            client.connect(
                hostname=ip,
                port=22,
                username=username,
                pkey=pkey,
                timeout=30,
                banner_timeout=30,
                auth_timeout=30,
                look_for_keys=False,
                allow_agent=False,
            )
            logger.info('SSH 公钥连接成功: stage=%s ip=%s user=%s key_source=%s', label, ip, username, key_source)
            logger.info('开始执行公钥阶段远端脚本: stage=%s ip=%s user=%s', label, ip, username)
            sftp = client.open_sftp()
            try:
                with sftp.file(remote_script_path, 'w') as remote_file:
                    remote_file.write(script)
                sftp.chmod(remote_script_path, 0o700)
            finally:
                sftp.close()
            stdin, stdout, stderr = client.exec_command(f'bash {remote_script_path}', timeout=300)
            exit_code = stdout.channel.recv_exit_status()
            output = stdout.read().decode('utf-8', errors='ignore').strip()
            error = stderr.read().decode('utf-8', errors='ignore').strip()
            merged = '\n'.join(part for part in [output, error] if part)
            logger.info('公钥阶段远端脚本执行结束: stage=%s exit_code=%s stdout_len=%s stderr_len=%s', label, exit_code, len(output), len(error))
            client.exec_command(f'rm -f {remote_script_path}', timeout=30)
            if exit_code == 0:
                logger.info('公钥阶段远端脚本执行成功: stage=%s ip=%s user=%s', label, ip, username)
                return True, f'{label} 用户={username} 执行完成\n{merged}'.strip()
            logger.warning('公钥阶段远端脚本执行失败: stage=%s ip=%s user=%s exit_code=%s', label, ip, username, exit_code)
            last_error = f'user={username} exit={exit_code} output={merged}'
        except Exception as exc:
            last_error = f'user={username} error={exc}'
            logger.warning('SSH 公钥连接失败，尝试下一个用户: stage=%s ip=%s user=%s key_source=%s error=%s', label, ip, username, key_source, exc)
        finally:
            client.close()
    return False, f'SSH 公钥执行 {label} 失败: {last_error}'


async def install_bbr(ip: str, username: str, password: str) -> tuple[bool, str]:
    if not ip or not password:
        return False, '缺少 SSH 连接参数，无法执行 BBR 初始化。'
    bootstrap_username = (username or 'root').strip() or 'root'
    logger.info('开始执行 BBR 初始化 ip=%s user=%s', ip, bootstrap_username)

    key_login_users = ['admin', 'debian', 'ubuntu', 'root']
    logger.info('开始执行公钥阶段密码设置: ip=%s key_users=%s target_user=%s', ip, ','.join(key_login_users), bootstrap_username)
    key_ok, key_output = await _run_ssh_script_with_key(ip, key_login_users, _build_set_password_script(password), label='SET_PASSWORD')
    _log_multiline_output('[BOOTSTRAP][SET_PASSWORD]', key_output)
    if not key_ok:
        return False, f'公钥登录设置密码失败\n{key_output}'.strip()

    logger.info('公钥阶段密码设置完成，开始等待密码复登: ip=%s user=%s', ip, bootstrap_username)
    ready, message = await _wait_ssh_password_ready(ip, bootstrap_username, password)
    if not ready:
        return False, message
    ok, output = await _run_ssh_script(ip, bootstrap_username, password, DEBIAN_BBR_SCRIPT, label='BBR')
    _log_multiline_output('[BOOTSTRAP][BBR]', output)
    logger.info('%s', _build_bootstrap_full_log('BBR', ip, bootstrap_username, ok, output))
    return ok, output


def build_mtproxy_links(ip: str, port: int | str, secret: str) -> tuple[str, str]:
    normalized_secret = (secret or '').strip()
    if normalized_secret.startswith('ee') and len(normalized_secret) > 34:
        proxy_secret = normalized_secret
    else:
        domain_hex = binascii.hexlify(MTPROXY_FAKE_TLS_DOMAIN.encode('utf-8')).decode('ascii')
        proxy_secret = f"ee{normalized_secret[:32]}{domain_hex}"
    tg_link = f'tg://proxy?server={ip}&port={port}&secret={proxy_secret}'
    tme_link = f'https://t.me/proxy?server={ip}&port={port}&secret={proxy_secret}'
    return tg_link, tme_link


@sync_to_async
def _probe_mtproxy_state(ip: str, username: str, password: str, port: int) -> tuple[bool, dict[str, str]]:
    try:
        import paramiko
    except ImportError:
        return False, {}

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
        probe_script = rf"""bash -s <<'EOF'
set -e
PORT='{port}'
WORKDIR='{MTPROXY_DIR}'
CONFIG_FILE="$WORKDIR/config"
SECRET=''
if [ -f "$CONFIG_FILE" ]; then
  SECRET=$(sed -n 's/^secret="\([^"]*\)"$/\1/p' "$CONFIG_FILE" | head -n 1)
fi
PROC_OK=0
if ps -ef | grep -iE '/mtg | mtg run |mtproto-proxy' | grep -v grep >/dev/null 2>&1; then
  PROC_OK=1
fi
PORT_OK=0
if command -v ss >/dev/null 2>&1; then
  if ss -lntup 2>/dev/null | grep -E '[:.]('"$PORT"')\b' >/dev/null 2>&1; then
    PORT_OK=1
  fi
elif command -v netstat >/dev/null 2>&1; then
  if netstat -lntup 2>/dev/null | grep -E '[:.]('"$PORT"')\b' >/dev/null 2>&1; then
    PORT_OK=1
  fi
fi
DAEMON=''
if systemctl is-active --quiet mtproxy.service 2>/dev/null; then
  DAEMON='SYSTEMD'
elif [ "$PROC_OK" = '1' ]; then
  DAEMON='SCRIPT'
fi
echo "MTPROXY_PROBE_PROC_OK=$PROC_OK"
echo "MTPROXY_PROBE_PORT_OK=$PORT_OK"
echo "MTPROXY_PROBE_SECRET=$SECRET"
echo "MTPROXY_PROBE_DAEMON=$DAEMON"
EOF"""
        stdin, stdout, stderr = client.exec_command(probe_script, timeout=120)
        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode('utf-8', errors='ignore')
        if exit_code != 0:
            return False, {}
        data: dict[str, str] = {}
        for line in output.splitlines():
            if '=' in line and line.startswith('MTPROXY_PROBE_'):
                key, value = line.split('=', 1)
                data[key] = value.strip()
        ok = data.get('MTPROXY_PROBE_PROC_OK') == '1' and data.get('MTPROXY_PROBE_PORT_OK') == '1' and bool(data.get('MTPROXY_PROBE_SECRET'))
        return ok, data
    except Exception:
        return False, {}
    finally:
        client.close()


async def install_mtproxy(ip: str, username: str, password: str, port: int = MTPROXY_PORT, desired_secret: str = '') -> tuple[bool, str]:
    if not ip or not password:
        return False, '缺少 SSH 连接参数，无法执行 MTProxy 安装。'
    bootstrap_username = (username or 'root').strip() or 'root'
    logger.info('开始执行 MTProxy 安装 ip=%s user=%s port=%s', ip, bootstrap_username, port)
    ready, message = await _wait_ssh_password_ready(ip, bootstrap_username, password)
    if not ready:
        return False, message.replace('BBR 初始化', 'MTProxy 安装')
    ok, output = await _run_ssh_script(ip, bootstrap_username, password, _build_mtproxy_script(port, desired_secret), label='MTPROXY')
    secret = ''
    actual_port = str(port)
    mtproxy_status = ''
    mtproxy_error = ''
    mtproxy_daemon = ''
    sanitized_output = _sanitize_mtproxy_output(output)
    for line in output.splitlines():
        if line.startswith('MTPROXY_SECRET='):
            secret = line.split('=', 1)[1].strip()
        elif line.startswith('MTPROXY_PORT='):
            actual_port = line.split('=', 1)[1].strip()
        elif line.startswith('MTPROXY_STATUS='):
            mtproxy_status = line.split('=', 1)[1].strip()
        elif line.startswith('MTPROXY_ERROR='):
            mtproxy_error = line.split('=', 1)[1].strip()
        elif line.startswith('MTPROXY_DAEMON='):
            mtproxy_daemon = line.split('=', 1)[1].strip()
    if sanitized_output:
        _log_multiline_output('[BOOTSTRAP][MTPROXY]', sanitized_output)
    logger.info('%s', _build_bootstrap_full_log('MTPROXY', ip, bootstrap_username, ok, sanitized_output))
    probe_ok, probe = await _probe_mtproxy_state(ip, bootstrap_username, password, port)
    if not secret and probe.get('MTPROXY_PROBE_SECRET'):
        secret = probe.get('MTPROXY_PROBE_SECRET', '')
    if not mtproxy_daemon and probe.get('MTPROXY_PROBE_DAEMON'):
        mtproxy_daemon = probe.get('MTPROXY_PROBE_DAEMON', '')
    if desired_secret:
        secret = desired_secret
    if secret and (mtproxy_status == 'OK' or probe_ok):
        tg_link, tme_link = build_mtproxy_links(ip, actual_port, secret)
        verified_output = (
            'MTProxy 安装完成\n'
            f'状态: 运行正常\n'
            f'端口: {actual_port}\n'
            f'进程守护: {mtproxy_daemon or "已启用"}\n'
            f'TG链接: {tg_link}\n'
            f'分享链接: {tme_link}'
        )
        _log_multiline_output('[BOOTSTRAP][MTPROXY_LINK]', verified_output)
        return True, verified_output
    if mtproxy_error:
        return False, f'MTProxy 安装失败\n{mtproxy_error}\n{output}'.strip()
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
    logger.info('开始等待 SSH 密码登录就绪: ip=%s user=%s timeout=%ss interval=%ss', ip, username, timeout, interval)
    port_ready, message = _wait_ssh_port.__wrapped__(ip, 600, 5)
    if not port_ready:
        logger.warning('SSH 22 端口未就绪: ip=%s user=%s note=%s', ip, username, message)
        return False, message
    logger.info('SSH 22 端口已就绪，开始探测密码登录: ip=%s user=%s', ip, username)
    try:
        import paramiko
    except ImportError:
        return False, '未安装 paramiko，无法探测 SSH 密码登录是否就绪。'

    end_time = time.time() + timeout
    last_error = ''
    attempt = 0
    while time.time() < end_time:
        attempt += 1
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            logger.info('探测 SSH 密码登录: ip=%s user=%s attempt=%s', ip, username, attempt)
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
            logger.info('SSH 密码登录已就绪: ip=%s user=%s attempt=%s', ip, username, attempt)
            return True, 'SSH 密码登录已就绪。'
        except Exception as exc:
            last_error = str(exc)
            logger.info('SSH 密码登录尚未就绪: ip=%s user=%s attempt=%s error=%s', ip, username, attempt, last_error)
            time.sleep(interval)
        finally:
            try:
                client.close()
            except Exception:
                pass
    logger.warning('等待 SSH 密码登录超时: ip=%s user=%s last_error=%s', ip, username, last_error)
    return False, f'SSH 密码登录长时间未就绪，系统可能仍在重装或密码尚未生效: {last_error}'


@sync_to_async
def _run_ssh_script(ip: str, username: str, password: str, script: str, label: str = 'SSH') -> tuple[bool, str]:
    try:
        import paramiko
    except ImportError:
        return False, f'未安装 paramiko，无法通过 SSH 执行 {label}。'

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    remote_script_path = f'/tmp/openclaw_{label.lower()}.sh'
    try:
        logger.info('开始建立 SSH 连接: stage=%s ip=%s user=%s', label, ip, username)
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
        logger.info('SSH 连接成功: stage=%s ip=%s user=%s', label, ip, username)
        logger.info('开始执行远端脚本: stage=%s ip=%s user=%s', label, ip, username)
        sftp = client.open_sftp()
        try:
            with sftp.file(remote_script_path, 'w') as remote_file:
                remote_file.write(script)
            sftp.chmod(remote_script_path, 0o700)
        finally:
            sftp.close()
        stdin, stdout, stderr = client.exec_command(f'bash {remote_script_path}', timeout=300)
        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode('utf-8', errors='ignore').strip()
        error = stderr.read().decode('utf-8', errors='ignore').strip()
        merged = '\n'.join(part for part in [output, error] if part)
        logger.info('远端脚本执行结束: stage=%s exit_code=%s stdout_len=%s stderr_len=%s', label, exit_code, len(output), len(error))
        client.exec_command(f'rm -f {remote_script_path}', timeout=30)
        if exit_code == 0:
            logger.info('远端脚本执行成功: stage=%s ip=%s user=%s', label, ip, username)
            return True, f'{label} 执行完成\n{merged}'.strip()
        logger.warning('远端脚本执行失败: stage=%s ip=%s user=%s exit_code=%s', label, ip, username, exit_code)
        return False, f'{label} 执行失败(exit={exit_code})\n{merged}'.strip()
    except (socket.timeout, TimeoutError) as exc:
        logger.exception('SSH 连接超时: stage=%s ip=%s user=%s error=%s', label, ip, username, exc)
        return False, f'SSH 连接超时，{label} 未完成: {exc}'
    except Exception as exc:
        logger.exception('SSH 执行失败: stage=%s ip=%s user=%s error=%s', label, ip, username, exc)
        return False, f'SSH 执行 {label} 失败: {exc}'
    finally:
        client.close()
