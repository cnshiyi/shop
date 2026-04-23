from io import StringIO
import os
import boto3
import paramiko

PASSWORD = 'RootPass_20260418!'
IP = '54.169.73.239'

def interactive_handler(title, instructions, prompts):
    return [PASSWORD for _ in prompts]

key = os.getenv('AWS_ACCESS_KEY_ID', '')
secret = os.getenv('AWS_SECRET_ACCESS_KEY', '')
print('has_creds', bool(key), bool(secret))
client = boto3.client('lightsail', region_name='ap-southeast-1', aws_access_key_id=key, aws_secret_access_key=secret)
pem = client.download_default_key_pair()['privateKeyBase64']
pkey = paramiko.RSAKey.from_private_key(StringIO(pem))

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(IP, username='ubuntu', pkey=pkey, look_for_keys=False, allow_agent=False, timeout=20, auth_timeout=20, banner_timeout=20)
for command in [
    "sudo -i bash -lc 'whoami; apt-get update -y >/dev/null; apt-get install -y ca-certificates curl wget sudo procps iproute2 >/dev/null; printf \"%s\\n\" net.core.default_qdisc=fq net.ipv4.tcp_congestion_control=bbr > /etc/sysctl.d/99-bbr.conf; sysctl --system >/tmp/bbr.out 2>&1 || true; sysctl net.ipv4.tcp_congestion_control; sysctl net.core.default_qdisc'",
    "sudo -i bash -lc 'if [ -x /usr/sbin/sshd ]; then /usr/sbin/sshd -T | grep -E \"passwordauthentication|kbdinteractiveauthentication|permitrootlogin|usepam|authenticationmethods\"; fi'",
]:
    stdin, stdout, stderr = ssh.exec_command(command, timeout=300)
    print(stdout.read().decode('utf-8', errors='ignore'))
    print(stderr.read().decode('utf-8', errors='ignore'))
ssh.close()

transport = paramiko.Transport((IP, 22))
transport.start_client(timeout=20)
transport.auth_interactive('root', interactive_handler)
print('root_interactive_authenticated', transport.is_authenticated())
channel = transport.open_session()
channel.exec_command("bash -lc 'whoami; systemctl is-active mtproxy.service || true; ss -lntup | grep 9528 || true; ps -ef | grep -i mtg | grep -v grep || true; if [ -f /home/mtproxy/run-command.txt ]; then echo RUN_COMMAND; cat /home/mtproxy/run-command.txt; fi'")
out = b''
err = b''
while True:
    if channel.recv_ready():
        out += channel.recv(4096)
    if channel.recv_stderr_ready():
        err += channel.recv_stderr(4096)
    if channel.exit_status_ready():
        break
exit_code = channel.recv_exit_status()
print(out.decode('utf-8', errors='ignore'))
print(err.decode('utf-8', errors='ignore'))
print('root_check_exit', exit_code)
transport.close()
