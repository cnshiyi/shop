#!/usr/bin/env bash
set -e

echo '=== user-data.txt ==='
cat /var/lib/cloud/instance/user-data.txt || true

echo '=== scripts/part-001 ==='
cat /var/lib/cloud/instance/scripts/part-001 || true

echo '=== cloud-init status ==='
cloud-init status --long || true

echo '=== cloud-init output tail ==='
tail -n 160 /var/log/cloud-init-output.log || true

echo '=== cloud-init log tail ==='
tail -n 160 /var/log/cloud-init.log || true

echo '=== sshd dropin ls ==='
ls -la /etc/ssh/sshd_config.d || true

echo '=== sshd dropin cat ==='
cat /etc/ssh/sshd_config.d/99-openclaw-password.conf || true
