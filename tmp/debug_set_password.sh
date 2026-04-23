#!/usr/bin/env bash
set -euxo pipefail
PASS='Test1234@OpenClaw'
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
echo "CHPASSWD_BIN=$CHPASSWD_BIN"
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
