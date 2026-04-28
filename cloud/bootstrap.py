import asyncio
import binascii
import logging
import re
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from asgiref.sync import sync_to_async

from cloud.ports import get_mtproxy_port_plan

logger = logging.getLogger(__name__)


def _set_paramiko_quiet(enabled: bool) -> dict[str, int]:
    previous_levels = {}
    for logger_name in ('paramiko', 'paramiko.transport'):
        target_logger = logging.getLogger(logger_name)
        previous_levels[logger_name] = target_logger.level
        if enabled:
            target_logger.setLevel(logging.CRITICAL)
    return previous_levels


def _restore_paramiko_levels(levels: dict[str, int]) -> None:
    for logger_name, level in levels.items():
        logging.getLogger(logger_name).setLevel(level)


MTPROXY_DIR = '/home/mtproxy'
MTPROXY_PORT = 9528
MTPROXY_FAKE_TLS_DOMAIN = 'azure.microsoft.com'
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_SECRETS_DIR = PROJECT_ROOT / '.shop-secrets'
DEFAULT_LIGHTSAIL_KEY_DIR = PROJECT_SECRETS_DIR / 'lightsail'
DEFAULT_SSH_KEY_DIR = PROJECT_SECRETS_DIR / 'ssh'


def _derive_public_keys_from_private_keys(*directories: Path) -> int:
    created = 0
    for directory in directories:
        if not directory.is_dir():
            continue
        for pattern in ('*.pem', '*.key', 'id_*'):
            for private_key in sorted(directory.glob(pattern)):
                if not private_key.is_file() or private_key.name.endswith('.pub'):
                    continue
                public_key = private_key.with_name(f'{private_key.name}.pub')
                if public_key.exists():
                    continue
                try:
                    result = subprocess.run(
                        ['ssh-keygen', '-y', '-f', str(private_key)],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=10,
                    )
                except Exception as exc:
                    logger.warning('AWS SSH 私钥推导公钥失败: source=%s error=%s', private_key, exc)
                    continue
                content = (result.stdout or '').strip()
                if result.returncode != 0 or not content:
                    logger.warning('AWS SSH 私钥推导公钥失败: source=%s stderr=%s', private_key, (result.stderr or '').strip())
                    continue
                public_key.write_text(f'{content}\n', encoding='utf-8')
                try:
                    public_key.chmod(0o600)
                except OSError:
                    pass
                created += 1
                logger.info('已从 AWS SSH 私钥推导公钥: private=%s public=%s fingerprint_hint=%s', private_key, public_key, content.split()[1][-12:] if len(content.split()) > 1 else '')
    return created


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
    derived_count = _derive_public_keys_from_private_keys(key_dir, DEFAULT_SSH_KEY_DIR)
    if key_dir.is_dir():
        for pattern in ('*.pub',):
            candidates.extend(str(path) for path in sorted(key_dir.glob(pattern)))
    if DEFAULT_SSH_KEY_DIR.is_dir():
        candidates.extend(str(path) for path in sorted(DEFAULT_SSH_KEY_DIR.glob('*.pub')))
    candidates = [candidate for candidate in candidates if candidate]
    logger.info(
        '开始扫描 AWS SSH 公钥候选: count=%s derived=%s env_public_key=%s env_public_key_path=%s key_dir=%s ssh_key_dir=%s',
        len(candidates),
        derived_count,
        bool(env_value),
        bool(public_key_path),
        key_dir,
        DEFAULT_SSH_KEY_DIR,
    )
    for candidate in candidates:
        if not candidate:
            continue
        try:
            with open(candidate, 'r', encoding='utf-8') as handle:
                content = handle.read().strip()
            if content:
                logger.info('已加载 AWS SSH 公钥: source=%s fingerprint_hint=%s', candidate, content.split()[1][-12:] if len(content.split()) > 1 else '')
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

    if DEFAULT_SSH_KEY_DIR.is_dir():
        for pattern in ('*.pem', '*.key', 'id_*'):
            for path in sorted(DEFAULT_SSH_KEY_DIR.glob(pattern)):
                if path.is_file() and not path.name.endswith('.pub'):
                    candidates.append(path)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        normalized = str(path.resolve()) if path.exists() else str(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(path)
    return unique


def _load_private_key_path(path: Path):
    try:
        import paramiko
    except ImportError:
        return None, '未安装 paramiko，无法加载 SSH 私钥。'

    if not path.is_file():
        return None, f'私钥文件不存在: {path}'
    try:
        try:
            key = paramiko.Ed25519Key.from_private_key_file(str(path))
        except Exception:
            key = paramiko.RSAKey.from_private_key_file(str(path))
        return key, str(path)
    except Exception as exc:
        return None, str(exc)


def _load_private_key_file(private_key_path: str):
    path = Path(str(private_key_path or '')).expanduser()
    key, source = _load_private_key_path(path)
    if key:
        logger.info('已加载指定 SSH 私钥: source=%s', source)
    return key, source


def _load_aws_private_keys() -> tuple[list[tuple[object, str]], str]:
    last_error = ''
    loaded: list[tuple[object, str]] = []
    candidates = _iter_private_key_candidates()
    logger.info('开始扫描 AWS SSH 私钥候选: count=%s key_dir=%s', len(candidates), DEFAULT_LIGHTSAIL_KEY_DIR)
    for candidate in candidates:
        key, source = _load_private_key_path(candidate)
        if key:
            logger.info('已加载 AWS SSH 私钥: source=%s', source)
            loaded.append((key, source))
            continue
        last_error = source
        logger.warning('SSH 私钥加载失败: source=%s error=%s', candidate, last_error)
    return loaded, last_error


def _load_aws_private_key():
    loaded, last_error = _load_aws_private_keys()
    if loaded:
        return loaded[0]
    return None, last_error


def _build_set_password_script(password: str) -> str:
    escaped = password.replace("'", "'\"'\"'")
    return rf'''#!/usr/bin/env bash
set -euo pipefail
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


def _normalize_mtproxy_core_secret(secret: str) -> str:
    value = re.sub(r'\s+', '', str(secret or '').strip().strip('"\'')).lower()
    if value.startswith(('ee', 'dd')):
        value = value[2:]
    match = re.match(r'^[0-9a-f]{32}', value)
    return match.group(0) if match else ''


def _build_mtproxy_script(port: int, desired_secret: str = '', desired_backup_secret: str = '') -> str:
    desired_secret = _normalize_mtproxy_core_secret(desired_secret)
    desired_backup_secret = _normalize_mtproxy_core_secret(desired_backup_secret) or desired_secret
    port_plan = get_mtproxy_port_plan(port)
    fake_tls_domain_hex = binascii.hexlify(MTPROXY_FAKE_TLS_DOMAIN.encode('utf-8')).decode('ascii')
    backup_port = port_plan['backup']
    telemt_all_port = port_plan['telemt_all']
    telemt_classic_port = port_plan['telemt_classic']
    telemt_secure_port = port_plan['telemt_secure']
    telemt_tls_port = port_plan['telemt_tls']
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
DESIRED_BACKUP_SECRET='{desired_backup_secret}'
normalize_secret() {{
  local raw
  raw="$(printf '%s' "$1" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
  raw="${{raw#ee}}"
  raw="${{raw#dd}}"
  printf '%s' "$raw" | cut -c1-32
}}
SECRET=$(sed -n 's/^secret="\([^"]*\)"$/\1/p' "$WORKDIR/config" | head -n 1 || true)
SECRET=$(normalize_secret "$SECRET")
if [ -z "$SECRET" ]; then
  SECRET=$(printf '%s\n' "$INSTALL_OUTPUT" | grep -Eo 'secret=[0-9a-fA-F]+' | head -n 1 | cut -d= -f2 || true)
  SECRET=$(normalize_secret "$SECRET")
fi
if [ -z "$SECRET" ]; then
  SECRET=$(printf '%s\n' "$INSTALL_OUTPUT" | grep -Eo 'MTProxy Secret:[[:space:]]*[0-9a-fA-F]+' | head -n 1 | grep -Eo '[0-9a-fA-F]+' | tail -n 1 || true)
  SECRET=$(normalize_secret "$SECRET")
fi
if [ -z "$SECRET" ] && [ -f "$WORKDIR/run-command.sh" ]; then
  SECRET=$(grep -Eo 'ee[0-9a-fA-F]{{32,}}|[0-9a-fA-F]{{32}}' "$WORKDIR/run-command.sh" | head -n 1 || true)
  SECRET=$(normalize_secret "$SECRET")
fi
if [ -z "$SECRET" ]; then
  SECRET=$(ps -ef | grep -iE 'mtproto-proxy|/mtg | mtg run ' | grep -v grep | grep -Eo 'ee[0-9a-fA-F]{{32,}}|[0-9a-fA-F]{{32}}' | head -n 1 || true)
  SECRET=$(normalize_secret "$SECRET")
fi
RUN_COMMAND=''
if [ -n "$DESIRED_SECRET" ]; then
  SECRET="$DESIRED_SECRET"
  if [ -f "$WORKDIR/config" ]; then
    if grep -q '^secret=' "$WORKDIR/config"; then
      sed -i.bak -E "s/^secret=.*/secret=\"$SECRET\"/" "$WORKDIR/config"
    else
      printf '
secret="%s"
' "$SECRET" >> "$WORKDIR/config"
    fi
  fi
fi
if [ -x "$WORKDIR/bin/mtg" ] && [ -n "$SECRET" ]; then
  RUN_SECRET=$(normalize_secret "$SECRET")
  RUN_SECRET="ee${{RUN_SECRET}}{fake_tls_domain_hex}"
  RUN_COMMAND="$WORKDIR/bin/mtg run $RUN_SECRET -b 0.0.0.0:{port} --multiplex-per-connection 500 --prefer-ip=ipv4 -t 127.0.0.1:18888 -4 $(curl -4 -fsS ifconfig.me || echo 127.0.0.1):{port}"
fi
if [ -z "$RUN_COMMAND" ] && [ -f "$WORKDIR/run-command.sh" ]; then
  RUN_COMMAND=$(grep -v '^#!' "$WORKDIR/run-command.sh" | tail -n 1 || true)
fi
if [ -z "$RUN_COMMAND" ]; then
  RUN_COMMAND=$(ps -ef | grep -iE '/mtg | mtg run |mtproto-proxy' | grep -v grep | head -n 1 || true)
fi
if [ -n "$RUN_COMMAND" ]; then
  RUN_COMMAND=$(printf '%s
' "$RUN_COMMAND" | sed -E 's/^[^/]*//' | sed -E 's/[[:space:]]+$//')
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
systemctl stop mtproxy.service >/dev/null 2>&1 || true
if command -v fuser >/dev/null 2>&1; then
  fuser -k {port}/tcp >/dev/null 2>&1 || true
  fuser -k 18888/tcp >/dev/null 2>&1 || true
fi
pkill -f "$WORKDIR/bin/mtg run " >/dev/null 2>&1 || true
sleep 1
systemctl restart mtproxy.service
sleep 3
BACKUP_PORT={backup_port}
BACKUP_MANAGE_PORT=18889
BACKUP_WORKDIR='/home/mtproxy-python'
mkdir -p "$BACKUP_WORKDIR"
cd "$BACKUP_WORKDIR"
rm -f mtproxy.sh
BACKUP_OUTPUT="$(curl -fsSL -o mtproxy.sh https://raw.githubusercontent.com/ellermister/mtproxy/master/mtproxy.sh && chmod +x mtproxy.sh && printf '%s\n%s\n%s\n%s\n%s\n' '3' "$BACKUP_PORT" "$BACKUP_MANAGE_PORT" '{MTPROXY_FAKE_TLS_DOMAIN}' '' | bash mtproxy.sh 2>&1 || true)"
printf '%s\n' "$BACKUP_OUTPUT"
BACKUP_SECRET=$(sed -n 's/^secret="\([^"]*\)"$/\1/p' "$BACKUP_WORKDIR/config" | head -n 1 || true)
if [ -z "$BACKUP_SECRET" ]; then
  BACKUP_SECRET=$(printf '%s\n' "$BACKUP_OUTPUT" | grep -Eo 'MTProxy Secret:[[:space:]]*ee[0-9a-fA-F]+' | head -n 1 | grep -Eo 'ee[0-9a-fA-F]+' | tail -n 1 || true)
fi
BACKUP_SECRET=$(normalize_secret "$BACKUP_SECRET")
if [ -n "$DESIRED_BACKUP_SECRET" ]; then
  BACKUP_SECRET="$DESIRED_BACKUP_SECRET"
elif [ -n "$DESIRED_SECRET" ]; then
  BACKUP_SECRET="$DESIRED_SECRET"
fi
if [ ! -f "$BACKUP_WORKDIR/bin/mtprotoproxy.py" ] || [ ! -f "$BACKUP_WORKDIR/bin/config.py" ]; then
  echo '[WARNING] Python 备用 MTProxy 脚本安装不完整，启用手动兜底安装...'
  rm -rf "$BACKUP_WORKDIR/src" "$BACKUP_WORKDIR/bin"
  mkdir -p "$BACKUP_WORKDIR/src" "$BACKUP_WORKDIR/bin"
  curl -fsSL -o "$BACKUP_WORKDIR/src/mtprotoproxy-master.zip" https://github.com/alexbers/mtprotoproxy/archive/refs/heads/master.zip
  unzip -qo "$BACKUP_WORKDIR/src/mtprotoproxy-master.zip" -d "$BACKUP_WORKDIR/src"
  MTPROTO_SRC=$(find "$BACKUP_WORKDIR/src" -maxdepth 2 -type f -name mtprotoproxy.py -print -quit | xargs dirname)
  if [ -z "$MTPROTO_SRC" ] || [ ! -f "$MTPROTO_SRC/mtprotoproxy.py" ]; then
    echo 'MTProxy 安装失败'
    echo 'Python 备用 MTProxy 手动兜底下载失败'
    echo 'MTPROXY_STATUS=FAILED'
    echo 'MTPROXY_ERROR=Python 备用 MTProxy 手动兜底下载失败'
    exit 1
  fi
  cp "$MTPROTO_SRC/mtprotoproxy.py" "$BACKUP_WORKDIR/bin/mtprotoproxy.py"
  cp -r "$MTPROTO_SRC/pyaes" "$BACKUP_WORKDIR/bin/pyaes"
  if [ -z "$BACKUP_SECRET" ]; then
    BACKUP_SECRET=$(openssl rand -hex 16 2>/dev/null || python3 - <<'PY'
import secrets
print(secrets.token_hex(16))
PY
)
  fi
  cat > "$BACKUP_WORKDIR/bin/config.py" <<EOF
PORT = $BACKUP_PORT
USERS = {{
    "tg": "$BACKUP_SECRET",
}}
MODES = {{
    "classic": False,
    "secure": False,
    "tls": True
}}
TLS_DOMAIN = "{MTPROXY_FAKE_TLS_DOMAIN}"
AD_TAG = ""
EOF
fi
if [ -n "$BACKUP_SECRET" ] && [ -f "$BACKUP_WORKDIR/bin/config.py" ]; then
  python3 - "$BACKUP_WORKDIR/bin/config.py" "$BACKUP_SECRET" <<'PY'
import pathlib
import re
import sys
path = pathlib.Path(sys.argv[1])
secret = sys.argv[2]
text = path.read_text()
text = re.sub(r'"tg"\s*:\s*"[^"]*"', f'"tg": "{{secret}}"', text)
path.write_text(text)
PY
fi
if [ -n "$DESIRED_BACKUP_SECRET" ] && [ "$(normalize_secret "$BACKUP_SECRET")" != "$(normalize_secret "$DESIRED_BACKUP_SECRET")" ]; then
  echo 'MTProxy 安装失败'
  echo 'Python 备用 MTProxy 未能锁定指定密钥'
  echo 'MTPROXY_STATUS=FAILED'
  echo 'MTPROXY_ERROR=Python 备用 MTProxy 未能锁定指定密钥'
  exit 1
fi
printf '%s\n' '#!/usr/bin/env bash' 'set -e' "cd $BACKUP_WORKDIR" "/usr/bin/python3 $BACKUP_WORKDIR/bin/mtprotoproxy.py $BACKUP_WORKDIR/bin/config.py" | tee "$BACKUP_WORKDIR/run-command.sh" >/dev/null
chmod +x "$BACKUP_WORKDIR/run-command.sh"
BACKUP_SERVICE_FILE=/etc/systemd/system/mtproxy-python.service
printf '%s\n' \
  '[Unit]' \
  'Description=MTProxy Python Backup Service' \
  'After=network-online.target' \
  'Wants=network-online.target' \
  '' \
  '[Service]' \
  'Type=simple' \
  'User=root' \
  "WorkingDirectory=$BACKUP_WORKDIR" \
  "ExecStart=/bin/bash $BACKUP_WORKDIR/run-command.sh" \
  'Restart=always' \
  'RestartSec=3' \
  'LimitNOFILE=655350' \
  '' \
  '[Install]' \
  'WantedBy=multi-user.target' | tee "$BACKUP_SERVICE_FILE" >/dev/null
chmod 644 "$BACKUP_SERVICE_FILE"
systemctl daemon-reload
systemctl enable mtproxy-python.service >/dev/null 2>&1
systemctl stop mtproxy-python.service >/dev/null 2>&1 || true
if [ -f "$BACKUP_WORKDIR/pid/pid_mtproxy" ]; then
  kill "$(cat "$BACKUP_WORKDIR/pid/pid_mtproxy")" >/dev/null 2>&1 || true
fi
if command -v fuser >/dev/null 2>&1; then
  fuser -k "$BACKUP_PORT"/tcp >/dev/null 2>&1 || true
fi
pkill -f "$BACKUP_WORKDIR/bin/mtprotoproxy.py" >/dev/null 2>&1 || true
sleep 1
systemctl restart mtproxy-python.service
sleep 3
echo '[INFO] 主/备用 MTProxy 安装完成，开始二次重启服务...'
systemctl daemon-reload
systemctl restart mtproxy.service
systemctl restart mtproxy-python.service
sleep 3
systemctl is-active mtproxy.service >/dev/null 2>&1 && echo '[INFO] mtproxy.service 二次重启完成'
systemctl is-active mtproxy-python.service >/dev/null 2>&1 && echo '[INFO] mtproxy-python.service 二次重启完成'
if command -v ufw >/dev/null 2>&1; then
  ufw allow {port}/tcp || true
  ufw allow {port}/udp || true
  ufw allow "$BACKUP_PORT"/tcp || true
  ufw allow "$BACKUP_PORT"/udp || true
fi
BACKUP_PORT_OK=0
if command -v ss >/dev/null 2>&1; then
  if ss -lntup 2>/dev/null | grep -E "[:.]($BACKUP_PORT)\b" >/dev/null 2>&1; then
    BACKUP_PORT_OK=1
  fi
elif command -v netstat >/dev/null 2>&1; then
  if netstat -lntup 2>/dev/null | grep -E "[:.]($BACKUP_PORT)\b" >/dev/null 2>&1; then
    BACKUP_PORT_OK=1
  fi
fi
if ! systemctl is-active --quiet mtproxy-python.service || [ "$BACKUP_PORT_OK" != "1" ]; then
  systemctl status mtproxy-python.service --no-pager || true
  journalctl -u mtproxy-python.service -n 80 --no-pager || true
  echo 'MTProxy 安装失败'
  echo "Python 备用 MTProxy 端口 $BACKUP_PORT 未运行"
  echo 'MTPROXY_STATUS=FAILED'
  echo "MTPROXY_ERROR=Python 备用 MTProxy 端口 $BACKUP_PORT 未运行"
  exit 1
fi
POST_RESTART_BACKUP_SECRET=$(python3 - "$BACKUP_WORKDIR/bin/config.py" <<'PY'
import pathlib
import re
import sys
path = pathlib.Path(sys.argv[1])
text = path.read_text() if path.exists() else ''
match = re.search(r'"tg"\s*:\s*"([^"]+)"', text)
print(match.group(1).strip() if match else '')
PY
)
POST_RESTART_BACKUP_SECRET=$(normalize_secret "$POST_RESTART_BACKUP_SECRET")
if [ -n "$DESIRED_BACKUP_SECRET" ] && [ "$POST_RESTART_BACKUP_SECRET" != "$(normalize_secret "$DESIRED_BACKUP_SECRET")" ]; then
  systemctl status mtproxy-python.service --no-pager || true
  journalctl -u mtproxy-python.service -n 80 --no-pager || true
  echo 'MTProxy 安装失败'
  echo 'Python 备用 MTProxy 重启后密钥与指定密钥不一致'
  echo 'MTPROXY_STATUS=FAILED'
  echo 'MTPROXY_ERROR=Python 备用 MTProxy 重启后密钥与指定密钥不一致'
  exit 1
fi
if [ -n "$POST_RESTART_BACKUP_SECRET" ]; then
  BACKUP_SECRET="$POST_RESTART_BACKUP_SECRET"
fi
BACKUP_PUBLIC_IP=$(curl -4 -fsS ifconfig.me || hostname -I | awk '{{print $1}}' || echo 127.0.0.1)
BACKUP_FAKE_TLS_SECRET="ee${{BACKUP_SECRET}}{fake_tls_domain_hex}"
BACKUP_RESTART_LINK="tg://proxy?server=$BACKUP_PUBLIC_IP&port=$BACKUP_PORT&secret=$BACKUP_FAKE_TLS_SECRET"
echo "MTPROXY_BACKUP_RESTART_SECRET=${{BACKUP_SECRET}}"
echo "MTPROXY_BACKUP_RESTART_LINK=${{BACKUP_RESTART_LINK}}"
TELEMT_BIN=/usr/local/bin/telemt
TELEMT_ALL_PORT={telemt_all_port}
TELEMT_CLASSIC_PORT={telemt_classic_port}
TELEMT_SECURE_PORT={telemt_secure_port}
TELEMT_TLS_PORT={telemt_tls_port}
TELEMT_API_ALL=19091
TELEMT_API_CLASSIC=19092
TELEMT_API_SECURE=19093
TELEMT_API_TLS=19094
PUBLIC_IP=$(curl -4 -fsS ifconfig.me || hostname -I | awk '{{print $1}}' || echo 127.0.0.1)
TELEMT_BASE_SECRET=$(printf '%s' "$SECRET" | sed -E 's/^ee//' | cut -c1-32)
if ! printf '%s' "$TELEMT_BASE_SECRET" | grep -Eq '^[0-9a-fA-F]{{32}}$'; then
  TELEMT_BASE_SECRET=$(openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
fi
TELEMT_CLASSIC_SECRET=$(printf '1%s' "$TELEMT_BASE_SECRET" | cut -c1-32)
TELEMT_SECURE_SECRET=$(printf '2%s' "$TELEMT_BASE_SECRET" | cut -c1-32)
TELEMT_TLS_SECRET=$(printf '3%s' "$TELEMT_BASE_SECRET" | cut -c1-32)
ARCH=$(uname -m)
LIBC=$(ldd --version 2>&1 | grep -iq musl && echo musl || echo gnu)
TELEMT_URL="https://github.com/telemt/telemt/releases/latest/download/telemt-$ARCH-linux-$LIBC.tar.gz"
TELEMT_TMP=$(mktemp -d)
(curl -fsSL "$TELEMT_URL" -o "$TELEMT_TMP/telemt.tar.gz" || curl -k -fsSL "$TELEMT_URL" -o "$TELEMT_TMP/telemt.tar.gz")
tar -xzf "$TELEMT_TMP/telemt.tar.gz" -C "$TELEMT_TMP"
install -m 0755 "$TELEMT_TMP/telemt" "$TELEMT_BIN"
rm -rf "$TELEMT_TMP"
mkdir -p /opt/telemt-classic /opt/telemt-secure /opt/telemt-tls /etc/telemt-classic /etc/telemt-secure /etc/telemt-tls
cat > /etc/telemt-classic/telemt.toml <<EOF
[general]
use_middle_proxy = true
log_level = "normal"
[general.modes]
classic = true
secure = false
tls = false
[general.links]
show = "*"
public_host = "$PUBLIC_IP"
public_port = $TELEMT_CLASSIC_PORT
[server]
port = $TELEMT_CLASSIC_PORT
[server.api]
enabled = true
listen = "127.0.0.1:$TELEMT_API_CLASSIC"
whitelist = ["127.0.0.1/32", "::1/128"]
[[server.listeners]]
ip = "0.0.0.0"
[censorship]
tls_domain = "{MTPROXY_FAKE_TLS_DOMAIN}"
mask = true
tls_emulation = true
tls_front_dir = "tlsfront"
[access.users]
hello = "$TELEMT_CLASSIC_SECRET"
EOF
cat > /etc/telemt-secure/telemt.toml <<EOF
[general]
use_middle_proxy = true
log_level = "normal"
[general.modes]
classic = false
secure = true
tls = false
[general.links]
show = "*"
public_host = "$PUBLIC_IP"
public_port = $TELEMT_SECURE_PORT
[server]
port = $TELEMT_SECURE_PORT
[server.api]
enabled = true
listen = "127.0.0.1:$TELEMT_API_SECURE"
whitelist = ["127.0.0.1/32", "::1/128"]
[[server.listeners]]
ip = "0.0.0.0"
[censorship]
tls_domain = "{MTPROXY_FAKE_TLS_DOMAIN}"
mask = true
tls_emulation = true
tls_front_dir = "tlsfront"
[access.users]
hello = "$TELEMT_SECURE_SECRET"
EOF
cat > /etc/telemt-tls/telemt.toml <<EOF
[general]
use_middle_proxy = true
log_level = "normal"
[general.modes]
classic = false
secure = false
tls = true
[general.links]
show = "*"
public_host = "$PUBLIC_IP"
public_port = $TELEMT_TLS_PORT
[server]
port = $TELEMT_TLS_PORT
[server.api]
enabled = true
listen = "127.0.0.1:$TELEMT_API_TLS"
whitelist = ["127.0.0.1/32", "::1/128"]
[[server.listeners]]
ip = "0.0.0.0"
[censorship]
tls_domain = "{MTPROXY_FAKE_TLS_DOMAIN}"
mask = true
tls_emulation = true
tls_front_dir = "tlsfront"
[access.users]
hello = "$TELEMT_TLS_SECRET"
EOF
for name in classic secure tls; do
  port_var=TELEMT_$(printf '%s' "$name" | tr '[:lower:]' '[:upper:]')_PORT
  cat > /etc/systemd/system/telemt-$name.service <<EOF
[Unit]
Description=Telemt $name Service
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=root
WorkingDirectory=/opt/telemt-$name
ExecStart=$TELEMT_BIN /etc/telemt-$name/telemt.toml
Restart=on-failure
LimitNOFILE=65536
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
EOF
done
systemctl daemon-reload
for name in classic secure tls; do
  systemctl enable telemt-$name.service >/dev/null 2>&1
  systemctl restart telemt-$name.service
done
sleep 6
for name in classic secure tls; do
  if ! systemctl is-active --quiet telemt-$name.service; then
    systemctl status telemt-$name.service --no-pager || true
    journalctl -u telemt-$name.service -n 80 --no-pager || true
    echo 'MTProxy 安装失败'
    echo "Telemt systemd $name 未运行"
    echo 'MTPROXY_STATUS=FAILED'
    echo "MTPROXY_ERROR=Telemt systemd $name 未运行"
    exit 1
  fi
done
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y docker.io
  systemctl enable docker >/dev/null 2>&1 || true
  systemctl start docker || true
fi
if ! command -v docker >/dev/null 2>&1; then
  echo 'MTProxy 安装失败'
  echo 'Telemt Docker 依赖安装失败'
  echo 'MTPROXY_STATUS=FAILED'
  echo 'MTPROXY_ERROR=Telemt Docker 依赖安装失败'
  exit 1
fi
mkdir -p /opt/telemt-docker-all
cat > /opt/telemt-docker-all/telemt.toml <<EOF
[general]
use_middle_proxy = true
log_level = "normal"
[general.modes]
classic = true
secure = true
tls = true
[general.links]
show = "*"
public_host = "$PUBLIC_IP"
public_port = $TELEMT_ALL_PORT
[server]
port = 443
[server.api]
enabled = true
listen = "0.0.0.0:9091"
whitelist = ["0.0.0.0/0", "::/0"]
[[server.listeners]]
ip = "0.0.0.0"
[censorship]
tls_domain = "{MTPROXY_FAKE_TLS_DOMAIN}"
mask = true
tls_emulation = true
tls_front_dir = "tlsfront"
[access.users]
hello = "$TELEMT_BASE_SECRET"
EOF
docker rm -f telemt-all >/dev/null 2>&1 || true
docker pull ghcr.io/telemt/telemt:latest
docker run -d \
  --name telemt-all \
  --restart unless-stopped \
  -p "$TELEMT_ALL_PORT:443" \
  -p "127.0.0.1:$TELEMT_API_ALL:9091" \
  -w /etc/telemt \
  -v /opt/telemt-docker-all/telemt.toml:/etc/telemt/config.toml:ro \
  --tmpfs /etc/telemt:rw,mode=1777,size=4m \
  -e RUST_LOG=info \
  --cap-drop ALL \
  --cap-add NET_BIND_SERVICE \
  --read-only \
  --security-opt no-new-privileges:true \
  --ulimit nofile=65536:262144 \
  ghcr.io/telemt/telemt:latest
sleep 8
if ! docker ps --filter name=telemt-all --filter status=running --format '{{{{.Names}}}}' | grep -q '^telemt-all$'; then
  docker logs telemt-all || true
  echo 'MTProxy 安装失败'
  echo 'Telemt Docker A 未运行'
  echo 'MTPROXY_STATUS=FAILED'
  echo 'MTPROXY_ERROR=Telemt Docker A 未运行'
  exit 1
fi
if command -v ufw >/dev/null 2>&1; then
  for telemt_port in "$TELEMT_ALL_PORT" "$TELEMT_CLASSIC_PORT" "$TELEMT_SECURE_PORT" "$TELEMT_TLS_PORT"; do
    ufw allow "$telemt_port"/tcp || true
    ufw allow "$telemt_port"/udp || true
  done
fi
TELEMT_ALL_LINKS=$(curl -fsS "http://127.0.0.1:$TELEMT_API_ALL/v1/users" || true)
TELEMT_CLASSIC_LINKS=$(curl -fsS "http://127.0.0.1:$TELEMT_API_CLASSIC/v1/users" || true)
TELEMT_SECURE_LINKS=$(curl -fsS "http://127.0.0.1:$TELEMT_API_SECURE/v1/users" || true)
TELEMT_TLS_LINKS=$(curl -fsS "http://127.0.0.1:$TELEMT_API_TLS/v1/users" || true)
echo "MTPROXY_TELEMT_A_STATUS=OK"
echo "MTPROXY_TELEMT_A_PORT=$TELEMT_ALL_PORT"
echo "MTPROXY_TELEMT_B_STATUS=OK"
echo "MTPROXY_TELEMT_B_PORTS=$TELEMT_CLASSIC_PORT,$TELEMT_SECURE_PORT,$TELEMT_TLS_PORT"
echo "MTPROXY_TELEMT_A_LINKS=$TELEMT_ALL_LINKS"
echo "MTPROXY_TELEMT_B_CLASSIC_LINKS=$TELEMT_CLASSIC_LINKS"
echo "MTPROXY_TELEMT_B_SECURE_LINKS=$TELEMT_SECURE_LINKS"
echo "MTPROXY_TELEMT_B_TLS_LINKS=$TELEMT_TLS_LINKS"
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
echo "MTPROXY_BACKUP_STATUS=OK"
echo "MTPROXY_BACKUP_PROVIDER=mtprotoproxy"
echo "MTPROXY_BACKUP_PORT=${{BACKUP_PORT}}"
echo "MTPROXY_BACKUP_SECRET=${{BACKUP_SECRET}}"
'''




def _sanitize_bootstrap_output(text: str) -> str:
    sanitized_lines = []
    secret_pattern = re.compile(r'(secret(?:=|:|\s+)[\s\"\']*)(ee|dd)?[0-9a-fA-F]{32,}(\b|$)', re.IGNORECASE)
    link_secret_pattern = re.compile(r'(secret=)(ee|dd)?[0-9a-fA-F]{32,}', re.IGNORECASE)
    pass_assignment_pattern = re.compile(r'(PASS=)[^\s]+')
    chpasswd_pattern = re.compile(r'((?:root|admin):)[^\s]+')
    for raw_line in (text or '').splitlines():
        line = raw_line
        line = pass_assignment_pattern.sub(r'\1***', line)
        line = chpasswd_pattern.sub(r'\1***', line)
        line = link_secret_pattern.sub(r'\1***', line)
        line = secret_pattern.sub(r'\1***', line)
        sanitized_lines.append(line)
    return '\n'.join(sanitized_lines)


def _log_multiline_output(prefix: str, text: str):
    for line in _sanitize_bootstrap_output(text).splitlines():
        logger.info('%s %s', prefix, line)


def _build_bootstrap_full_log(label: str, ip: str, username: str, ok: bool, output: str) -> str:
    status = 'OK' if ok else 'FAILED'
    lines = [
        f'[{label}] ip={ip} user={username} status={status}',
        '----- BEGIN OUTPUT -----',
        _sanitize_bootstrap_output(output).rstrip(),
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
def _run_ssh_script_with_key(ip: str, usernames: str | list[str], script: str, label: str = 'SSH_KEY', private_key_path: str = '') -> tuple[bool, str]:
    try:
        import paramiko
    except ImportError:
        return False, f'未安装 paramiko，无法通过 SSH 公钥执行 {label}。'

    if private_key_path:
        pkey, key_source = _load_private_key_file(private_key_path)
        key_candidates = [(pkey, key_source)] if pkey else []
        load_error = key_source
    else:
        key_candidates, load_error = _load_aws_private_keys()
    if not key_candidates:
        env_private_key_path = (os.getenv('AWS_LIGHTSAIL_PRIVATE_KEY_PATH') or '').strip()
        return False, f'未找到可用私钥，无法通过 key 登录执行初始化。private_key_path={bool(private_key_path)} env_private_key_path={bool(env_private_key_path)} detail={load_error}'

    user_candidates = [usernames] if isinstance(usernames, str) else list(usernames or [])
    logger.info('开始 SSH 公钥双层轮询: stage=%s ip=%s key_count=%s user_count=%s users=%s', label, ip, len(key_candidates), len(user_candidates), ','.join(user_candidates))
    last_error = ''
    remote_script_path = f'/tmp/openclaw_{label.lower()}_key.sh'
    for pkey, key_source in key_candidates:
        for username in user_candidates:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                logger.info('开始建立 SSH 公钥连接: stage=%s ip=%s user=%s key_source=%s', label, ip, username, key_source)
                previous_levels = _set_paramiko_quiet(True)
                try:
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
                finally:
                    time.sleep(0.5)
                    _restore_paramiko_levels(previous_levels)
                logger.info('SSH 公钥连接成功: stage=%s ip=%s user=%s key_source=%s', label, ip, username, key_source)
                logger.info('开始执行公钥阶段远端脚本: stage=%s ip=%s user=%s key_source=%s', label, ip, username, key_source)
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
                logger.info('公钥阶段远端脚本执行结束: stage=%s key_source=%s exit_code=%s stdout_len=%s stderr_len=%s', label, key_source, exit_code, len(output), len(error))
                client.exec_command(f'rm -f {remote_script_path}', timeout=30)
                if exit_code == 0:
                    logger.info('公钥阶段远端脚本执行成功: stage=%s ip=%s user=%s key_source=%s', label, ip, username, key_source)
                    return True, f'{label} 私钥={key_source} 用户={username} 执行完成\n{merged}'.strip()
                logger.warning('公钥阶段远端脚本执行失败: stage=%s ip=%s user=%s key_source=%s exit_code=%s', label, ip, username, key_source, exit_code)
                last_error = f'key={key_source} user={username} exit={exit_code} output={merged}'
            except Exception as exc:
                last_error = f'key={key_source} user={username} error={exc}'
                logger.warning('SSH 公钥连接失败，尝试下一组 key/user: stage=%s ip=%s user=%s key_source=%s error=%s', label, ip, username, key_source, exc)
            finally:
                client.close()
    return False, f'SSH 公钥执行 {label} 失败: {last_error}'


async def install_bbr(ip: str, username: str, password: str, private_key_path: str = '', use_key_setup: bool = True) -> tuple[bool, str]:
    if not ip or not password:
        return False, '缺少 SSH 连接参数，无法执行 BBR 初始化。'
    bootstrap_username = (username or 'root').strip() or 'root'
    logger.info('开始执行 BBR 初始化 ip=%s user=%s', ip, bootstrap_username)

    key_login_users = ['root', 'admin', 'debian', 'ubuntu'] if private_key_path else ['admin', 'debian', 'ubuntu', 'root']
    key_ok = False
    key_output = ''
    if not use_key_setup:
        logger.info('跳过公钥阶段，直接等待 SSH 密码登录: ip=%s user=%s', ip, bootstrap_username)
    elif private_key_path:
        logger.info('开始执行公钥阶段密码设置: ip=%s key_users=%s target_user=%s', ip, ','.join(key_login_users), bootstrap_username)
        deadline = time.time() + 900
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            logger.info('探测 SSH 公钥登录并设置密码: ip=%s key=%s attempt=%s', ip, private_key_path, attempt)
            key_ok, key_output = await _run_ssh_script_with_key(ip, key_login_users, _build_set_password_script(password), label='SET_PASSWORD', private_key_path=private_key_path)
            _log_multiline_output('[BOOTSTRAP][SET_PASSWORD]', key_output)
            if key_ok:
                break
            await asyncio.sleep(10)
    else:
        logger.info('开始执行公钥阶段密码设置: ip=%s key_users=%s target_user=%s', ip, ','.join(key_login_users), bootstrap_username)
        key_ok, key_output = await _run_ssh_script_with_key(ip, key_login_users, _build_set_password_script(password), label='SET_PASSWORD')
        _log_multiline_output('[BOOTSTRAP][SET_PASSWORD]', key_output)
    if key_ok:
        logger.info('公钥阶段密码设置完成，开始等待密码复登: ip=%s user=%s', ip, bootstrap_username)
    elif private_key_path and use_key_setup:
        return False, f'公钥登录设置密码失败，未切换密码 fallback（实例已绑定 keypair）\n{key_output}'.strip()
    elif use_key_setup:
        logger.warning('公钥阶段密码设置失败，将直接尝试云厂商已下发的密码登录: ip=%s user=%s note=%s', ip, bootstrap_username, key_output)

    ready, message = await _wait_ssh_password_ready(ip, bootstrap_username, password)
    if not ready:
        return False, message
    ok, output = await _run_ssh_script(ip, bootstrap_username, password, DEBIAN_BBR_SCRIPT, label='BBR')
    _log_multiline_output('[BOOTSTRAP][BBR]', output)
    logger.info('%s', _build_bootstrap_full_log('BBR', ip, bootstrap_username, ok, output))
    return ok, output



def _extract_tg_links(text: str, exclude_port: int | str | None = None) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    excluded_port = str(exclude_port or '').strip()
    for match in re.findall(r'tg://proxy\?[^"\'\s<>]+', text or ''):
        link = match.rstrip(',.，。')
        link_port = link.split('port=', 1)[1].split('&', 1)[0].strip() if 'port=' in link else ''
        if excluded_port and link_port == excluded_port:
            continue
        if link and link not in seen:
            links.append(link)
            seen.add(link)
    return links


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
  SECRET=$(sed -n -E 's/^secret="?([^"[:space:]]+)"?$/\1/p' "$CONFIG_FILE" | head -n 1)
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


async def install_mtproxy(ip: str, username: str, password: str, port: int = MTPROXY_PORT, desired_secret: str = '', desired_backup_secret: str = '') -> tuple[bool, str]:
    if not ip or not password:
        return False, '缺少 SSH 连接参数，无法执行 MTProxy 安装。'
    bootstrap_username = (username or 'root').strip() or 'root'
    logger.info('开始执行 MTProxy 安装 ip=%s user=%s port=%s desired_backup=%s', ip, bootstrap_username, port, bool(desired_backup_secret))
    ready, message = await _wait_ssh_password_ready(ip, bootstrap_username, password)
    if not ready:
        return False, message.replace('BBR 初始化', 'MTProxy 安装')
    ok, output = await _run_ssh_script(ip, bootstrap_username, password, _build_mtproxy_script(port, desired_secret, desired_backup_secret), label='MTPROXY')
    secret = ''
    actual_port = str(port)
    mtproxy_status = ''
    mtproxy_error = ''
    mtproxy_daemon = ''
    mtproxy_backup_status = ''
    mtproxy_backup_provider = ''
    mtproxy_backup_port = ''
    mtproxy_backup_secret = ''
    mtproxy_backup_restart_link = ''
    telemt_a_port = ''
    telemt_b_ports = ''
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
        elif line.startswith('MTPROXY_BACKUP_STATUS='):
            mtproxy_backup_status = line.split('=', 1)[1].strip()
        elif line.startswith('MTPROXY_BACKUP_PROVIDER='):
            mtproxy_backup_provider = line.split('=', 1)[1].strip()
        elif line.startswith('MTPROXY_BACKUP_PORT='):
            mtproxy_backup_port = line.split('=', 1)[1].strip()
        elif line.startswith('MTPROXY_BACKUP_SECRET='):
            mtproxy_backup_secret = line.split('=', 1)[1].strip()
        elif line.startswith('MTPROXY_BACKUP_RESTART_SECRET='):
            mtproxy_backup_secret = line.split('=', 1)[1].strip()
        elif line.startswith('MTPROXY_BACKUP_RESTART_LINK='):
            mtproxy_backup_restart_link = line.split('=', 1)[1].strip()
        elif line.startswith('MTPROXY_TELEMT_A_PORT='):
            telemt_a_port = line.split('=', 1)[1].strip()
        elif line.startswith('MTPROXY_TELEMT_B_PORTS='):
            telemt_b_ports = line.split('=', 1)[1].strip()
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
    if mtproxy_status == 'FAILED' and mtproxy_error:
        return False, f'MTProxy 安装失败\n{mtproxy_error}\n{sanitized_output}'.strip()
    if secret and (mtproxy_status == 'OK' or probe_ok):
        tg_link, tme_link = build_mtproxy_links(ip, actual_port, secret)
        actual_port_plan = get_mtproxy_port_plan(actual_port)
        telemt_b_ports_default = ','.join(str(actual_port_plan[key]) for key in ('telemt_classic', 'telemt_secure', 'telemt_tls'))
        actual_port_plan = get_mtproxy_port_plan(actual_port)
        backup_port_value = str(mtproxy_backup_port or actual_port_plan['backup'])
        def _link_port(link: str) -> str:
            try:
                return parse_qs(urlparse(link).query).get('port', [''])[0]
            except Exception:
                return ''
        extra_links = [
            link for link in _extract_tg_links(output, exclude_port=actual_port)
            if link != tg_link and _link_port(link) != backup_port_value
        ]
        seen_extra_links = set(extra_links)
        def add_extra_link(link: str):
            if link and link != tg_link and link not in seen_extra_links:
                extra_links.append(link)
                seen_extra_links.add(link)
        domain_hex = binascii.hexlify(MTPROXY_FAKE_TLS_DOMAIN.encode('utf-8')).decode('ascii')
        core_secret = _normalize_mtproxy_core_secret(secret)
        backup_core_secret = core_secret
        if mtproxy_backup_restart_link:
            add_extra_link(mtproxy_backup_restart_link)
        elif backup_core_secret:
            add_extra_link(build_mtproxy_links(ip, backup_port_value, backup_core_secret)[0])
        if core_secret:
            telemt_all_port_value = str(telemt_a_port or actual_port_plan['telemt_all'])
            telemt_classic_secret = core_secret
            telemt_secure_secret = f'dd{core_secret}'
            telemt_tls_secret = f'ee{core_secret}{domain_hex}'
            add_extra_link(f'tg://proxy?server={ip}&port={telemt_all_port_value}&secret={telemt_classic_secret}')
            add_extra_link(f'tg://proxy?server={ip}&port={telemt_all_port_value}&secret={telemt_secure_secret}')
            add_extra_link(f'tg://proxy?server={ip}&port={telemt_all_port_value}&secret={telemt_tls_secret}')
            telemt_b_port_values = str(telemt_b_ports or telemt_b_ports_default).split(',')
            if len(telemt_b_port_values) >= 3:
                telemt_b_classic_secret = core_secret
                telemt_b_secure_secret = f'dd{core_secret}'
                telemt_b_tls_secret = f'ee{core_secret}{domain_hex}'
                add_extra_link(f'tg://proxy?server={ip}&port={telemt_b_port_values[0].strip()}&secret={telemt_b_classic_secret}')
                add_extra_link(f'tg://proxy?server={ip}&port={telemt_b_port_values[1].strip()}&secret={telemt_b_secure_secret}')
                add_extra_link(f'tg://proxy?server={ip}&port={telemt_b_port_values[2].strip()}&secret={telemt_b_tls_secret}')
        link_lines = '\n'.join(f'扩展链接: {link}' for link in extra_links)
        verified_output = (
            'MTProxy 安装完成\n'
            f'状态: 运行正常\n'
            f'端口: {actual_port}\n'
            f'进程守护: {mtproxy_daemon or "已启用"}\n'
            f'备用代理: {mtproxy_backup_provider or "mtprotoproxy"} {mtproxy_backup_status or "OK"} 端口 {mtproxy_backup_port or actual_port_plan["backup"]}\n'
            f'Telemt A(Docker三模式): 端口 {telemt_a_port or actual_port_plan["telemt_all"]}\n'
            f'Telemt B(systemd单模式): 端口 {telemt_b_ports or telemt_b_ports_default}\n'
            f'TG链接: {tg_link}\n'
            f'分享链接: {tme_link}'
            + (f'\n{link_lines}' if link_lines else '')
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
    initial_delay = 30
    logger.info('SSH 22 端口已就绪，等待系统初始化后探测密码登录: ip=%s user=%s delay=%ss', ip, username, initial_delay)
    time.sleep(initial_delay)
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
            previous_levels = _set_paramiko_quiet(True)
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
            finally:
                time.sleep(0.5)
                _restore_paramiko_levels(previous_levels)
            logger.info('SSH 密码登录已就绪: ip=%s user=%s attempt=%s', ip, username, attempt)
            return True, 'SSH 密码登录已就绪。'
        except Exception as exc:
            last_error = str(exc)
            logger.info('SSH 密码登录尚未就绪: ip=%s user=%s attempt=%s error=%s', ip, username, attempt, last_error)
            lowered_error = last_error.lower()
            if 'bad authentication type' in lowered_error and 'publickey' in lowered_error:
                logger.warning('SSH 服务端禁用密码登录，停止等待: ip=%s user=%s error=%s', ip, username, last_error)
                return False, '服务器 SSH 已禁用密码登录，仅允许公钥登录；无法继续自动重装，请先在后台补正确私钥/登录用户，或开启密码登录。'
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
