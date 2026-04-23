set -x
whoami
command -v sudo || true
command -v chpasswd || true
command -v systemctl || true
command -v service || true
ls -l /bin/sh || true
systemctl list-unit-files | grep -E '^(ssh|sshd)\.service' || true
grep -nE '^(PasswordAuthentication|PermitRootLogin|KbdInteractiveAuthentication|UsePAM)' /etc/ssh/sshd_config || true
