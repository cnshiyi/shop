#!/usr/bin/env bash
set -e

echo '=== cloud-init status ==='
cloud-init status --wait || true

echo '=== cloud-init output log tail ==='
tail -n 120 /var/log/cloud-init-output.log || true

echo '=== cloud-init log tail ==='
tail -n 120 /var/log/cloud-init.log || true

echo '=== sshd conf dropin ==='
cat /etc/ssh/sshd_config.d/99-openclaw-password.conf || true

echo '=== sshd config grep ==='
grep -nE 'PasswordAuthentication|PermitRootLogin|KbdInteractiveAuthentication|UsePAM|ChallengeResponseAuthentication|PubkeyAuthentication' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/* 2>/dev/null || true

echo '=== sshd -T ==='
/usr/sbin/sshd -T | grep -E 'passwordauthentication|permitrootlogin|kbdinteractiveauthentication|usepam|authenticationmethods|pubkeyauthentication' || true

echo '=== passwd status ==='
passwd -S root || true
passwd -S admin || true
