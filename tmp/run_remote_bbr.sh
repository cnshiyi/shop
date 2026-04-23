#!/usr/bin/env bash
set -e
sudo -i bash -lc '
whoami
apt-get update -y >/dev/null
apt-get install -y ca-certificates curl wget sudo procps >/dev/null
printf "%s\n" net.core.default_qdisc=fq net.ipv4.tcp_congestion_control=bbr > /etc/sysctl.d/99-bbr.conf
sysctl --system >/tmp/bbr.out 2>&1 || true
sysctl net.ipv4.tcp_congestion_control
'
