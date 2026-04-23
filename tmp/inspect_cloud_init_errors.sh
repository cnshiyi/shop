#!/usr/bin/env bash
set -e

echo '=== cloud-init output log tail ==='
tail -n 200 /var/log/cloud-init-output.log || true

echo '=== cloud-init log errors ==='
grep -nE 'ERROR|Traceback|Failed|FAIL|Unhandled' /var/log/cloud-init.log | tail -n 80 || true

echo '=== cloud-init analyze blame ==='
cloud-init analyze blame || true

echo '=== cloud-init schema status ==='
cloud-init status --long || true
