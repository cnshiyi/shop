import os
from io import StringIO

import boto3
import paramiko

key = os.getenv('AWS_ACCESS_KEY_ID', '')
secret = os.getenv('AWS_SECRET_ACCESS_KEY', '')
print('has_creds', bool(key), bool(secret))
client = boto3.client(
    'lightsail',
    region_name='ap-southeast-1',
    aws_access_key_id=key,
    aws_secret_access_key=secret,
)
pem = client.download_default_key_pair()['privateKeyBase64']
pkey = paramiko.RSAKey.from_private_key(StringIO(pem))
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(
    '54.169.73.239',
    username='ubuntu',
    pkey=pkey,
    look_for_keys=False,
    allow_agent=False,
    timeout=20,
    auth_timeout=20,
    banner_timeout=20,
)
commands = [
    "sudo -i bash -lc 'whoami; apt-get update -y >/dev/null; apt-get install -y ca-certificates curl wget sudo procps iproute2 >/dev/null; printf \"%s\\n\" net.core.default_qdisc=fq net.ipv4.tcp_congestion_control=bbr > /etc/sysctl.d/99-bbr.conf; sysctl --system >/tmp/bbr.out 2>&1 || true; sysctl net.ipv4.tcp_congestion_control'",
    "sudo -i bash -lc 'if [ -f /home/mtproxy/setup_mtproxy_systemd.sh ]; then bash /home/mtproxy/setup_mtproxy_systemd.sh || true; fi; systemctl is-active mtproxy.service || true; ss -lntup | grep 9528 || true; ps -ef | grep -i mtg | grep -v grep || true'",
]
for index, command in enumerate(commands, start=1):
    stdin, stdout, stderr = ssh.exec_command(command, timeout=300)
    print(f'--- command {index} stdout ---')
    print(stdout.read().decode('utf-8', errors='ignore'))
    print(f'--- command {index} stderr ---')
    print(stderr.read().decode('utf-8', errors='ignore'))
ssh.close()
