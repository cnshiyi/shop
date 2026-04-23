from io import StringIO
import json
import os
import sys
from pathlib import Path

import boto3
import paramiko

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')

import django

django.setup()

from mall.models import CloudServerOrder
from cloud.bootstrap import _build_mtproxy_script

FINAL_PASSWORD = 'RootPass_20260419!'
PORT = 9528
ORDER_ID = 118

order = CloudServerOrder.objects.get(id=ORDER_ID)
ip = order.public_ip or order.previous_public_ip
login_user = order.login_user or 'admin'
assert ip, 'order has no ip'

client = boto3.client(
    'lightsail',
    region_name='ap-southeast-1',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID', ''),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY', ''),
)
pem = client.download_default_key_pair()['privateKeyBase64']
pkey = paramiko.RSAKey.from_private_key(StringIO(pem))

print(json.dumps({'step': 'target', 'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'login_user': login_user}, ensure_ascii=False), flush=True)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(ip, username=login_user, pkey=pkey, look_for_keys=False, allow_agent=False, timeout=30, auth_timeout=30, banner_timeout=30)
stdin, stdout, stderr = ssh.exec_command('whoami && hostname', timeout=60)
print('STEP1_KEY_LOGIN_STDOUT')
print(stdout.read().decode('utf-8', errors='ignore'))
print('STEP1_KEY_LOGIN_STDERR')
print(stderr.read().decode('utf-8', errors='ignore'))
setup_cmd = (
    "sudo -i bash -lc \""
    "echo root:%s | chpasswd; "
    "echo %s:%s | chpasswd; "
    "grep -q '^PasswordAuthentication ' /etc/ssh/sshd_config && sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config || echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config; "
    "grep -q '^KbdInteractiveAuthentication ' /etc/ssh/sshd_config && sed -i 's/^KbdInteractiveAuthentication.*/KbdInteractiveAuthentication yes/' /etc/ssh/sshd_config || echo 'KbdInteractiveAuthentication yes' >> /etc/ssh/sshd_config; "
    "grep -q '^PermitRootLogin ' /etc/ssh/sshd_config && sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config || echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config; "
    "grep -q '^UsePAM ' /etc/ssh/sshd_config && sed -i 's/^UsePAM.*/UsePAM yes/' /etc/ssh/sshd_config || echo 'UsePAM yes' >> /etc/ssh/sshd_config; "
    "passwd -u root || true; "
    "systemctl restart ssh || systemctl restart sshd || true; "
    "/usr/sbin/sshd -T | grep -E 'passwordauthentication|kbdinteractiveauthentication|permitrootlogin|usepam|authenticationmethods'"
    "\""
) % (FINAL_PASSWORD, login_user, FINAL_PASSWORD)
stdin, stdout, stderr = ssh.exec_command(setup_cmd, timeout=300)
print('STEP2_SET_PASSWORD_STDOUT')
print(stdout.read().decode('utf-8', errors='ignore'))
print('STEP2_SET_PASSWORD_STDERR')
print(stderr.read().decode('utf-8', errors='ignore'))
ssh.close()

def interactive_handler(title, instructions, prompts):
    return [FINAL_PASSWORD for _ in prompts]

transport = paramiko.Transport((ip, 22))
transport.start_client(timeout=30)
transport.auth_interactive('root', interactive_handler)
print('STEP3_ROOT_AUTH', transport.is_authenticated())

channel = transport.open_session()
channel.exec_command("bash -lc 'whoami; id'")
out = b''
err = b''
while True:
    if channel.recv_ready():
        out += channel.recv(4096)
    if channel.recv_stderr_ready():
        err += channel.recv_stderr(4096)
    if channel.exit_status_ready():
        break
print('STEP3_ROOT_COMMAND_STDOUT')
print(out.decode('utf-8', errors='ignore'))
print('STEP3_ROOT_COMMAND_STDERR')
print(err.decode('utf-8', errors='ignore'))
print('STEP3_EXIT', channel.recv_exit_status())

script = _build_mtproxy_script(PORT)
sftp = paramiko.SFTPClient.from_transport(transport)
with sftp.file('/root/install_mtproxy.sh', 'w') as f:
    f.write(script)
sftp.chmod('/root/install_mtproxy.sh', 0o755)
sftp.close()

channel2 = transport.open_session()
channel2.exec_command("bash -lc '/root/install_mtproxy.sh'")
out2 = b''
err2 = b''
while True:
    if channel2.recv_ready():
        out2 += channel2.recv(4096)
    if channel2.recv_stderr_ready():
        err2 += channel2.recv_stderr(4096)
    if channel2.exit_status_ready():
        break
print('STEP4_INSTALL_STDOUT')
print(out2.decode('utf-8', errors='ignore'))
print('STEP4_INSTALL_STDERR')
print(err2.decode('utf-8', errors='ignore'))
print('STEP4_EXIT', channel2.recv_exit_status())

channel3 = transport.open_session()
channel3.exec_command("bash -lc 'systemctl is-active mtproxy.service || true; ss -lntup | grep 9528 || true; ps -ef | grep -iE \"/mtg | mtg run |mtproto-proxy\" | grep -v grep || true'")
out3 = b''
err3 = b''
while True:
    if channel3.recv_ready():
        out3 += channel3.recv(4096)
    if channel3.recv_stderr_ready():
        err3 += channel3.recv_stderr(4096)
    if channel3.exit_status_ready():
        break
print('STEP5_VERIFY_STDOUT')
print(out3.decode('utf-8', errors='ignore'))
print('STEP5_VERIFY_STDERR')
print(err3.decode('utf-8', errors='ignore'))
print('STEP5_EXIT', channel3.recv_exit_status())
transport.close()
