#!/usr/bin/env bash
set -e
mkdir -p /home/mtproxy
cat > /home/mtproxy/run-command.txt <<'EOF'
/home/mtproxy/bin/mtg run ee8e12eab4c37158a1e49e28d0671313aa617a7572652e6d6963726f736f66742e636f6d -b 0.0.0.0:9528 --multiplex-per-connection 500 --prefer-ip=ipv4 -t 127.0.0.1:8888 -4 54.169.73.239:9528
EOF
chmod +x /home/mtproxy/run-command.txt
cat > /etc/systemd/system/mtproxy.service <<'EOF'
[Unit]
Description=MTProxy Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/mtproxy
ExecStart=/bin/bash /home/mtproxy/run-command.txt
Restart=always
RestartSec=3
LimitNOFILE=655350

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable mtproxy.service
systemctl restart mtproxy.service
sleep 3
systemctl is-active mtproxy.service
ss -lntup | grep 9528
