#!/usr/bin/env bash
set -e
sudo -i bash -lc '
if [ -f /home/mtproxy/setup_mtproxy_systemd.sh ]; then
  bash /home/mtproxy/setup_mtproxy_systemd.sh || true
fi
systemctl is-active mtproxy.service || true
ss -lntup | grep 9528 || true
ps -ef | grep -iE "/mtg | mtg run |mtproto-proxy" | grep -v grep || true
'
