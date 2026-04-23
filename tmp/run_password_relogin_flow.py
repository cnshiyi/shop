from io import StringIO
import os
import boto3
import paramiko

IP = '3.0.231.151'
FIRST_PASSWORD = 'vQW@fFrrk%gD%nzsai'
FINAL_PASSWORD = 'RootPass_20260418!'

client = boto3.client(
    'lightsail',
    region_name='ap-southeast-1',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID', ''),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY', ''),
)
pem = client.download_default_key_pair()['privateKeyBase64']
pkey = paramiko.RSAKey.from_private_key(StringIO(pem))

print('STEP1_KEY_LOGIN_START')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(IP, username='ubuntu', pkey=pkey, look_for_keys=False, allow_agent=False, timeout=30, auth_timeout=30, banner_timeout=30)
stdin, stdout, stderr = ssh.exec_command("whoami && hostname", timeout=60)
print(stdout.read().decode('utf-8', errors='ignore'))
print(stderr.read().decode('utf-8', errors='ignore'))

setup_cmd = (
    "sudo -i bash -lc \""
    "echo root:%s | chpasswd; "
    "echo ubuntu:%s | chpasswd; "
    "grep -q '^PasswordAuthentication ' /etc/ssh/sshd_config && sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config || echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config; "
    "grep -q '^KbdInteractiveAuthentication ' /etc/ssh/sshd_config && sed -i 's/^KbdInteractiveAuthentication.*/KbdInteractiveAuthentication yes/' /etc/ssh/sshd_config || echo 'KbdInteractiveAuthentication yes' >> /etc/ssh/sshd_config; "
    "grep -q '^PermitRootLogin ' /etc/ssh/sshd_config && sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config || echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config; "
    "grep -q '^UsePAM ' /etc/ssh/sshd_config && sed -i 's/^UsePAM.*/UsePAM yes/' /etc/ssh/sshd_config || echo 'UsePAM yes' >> /etc/ssh/sshd_config; "
    "passwd -u root || true; "
    "systemctl restart ssh || systemctl restart sshd || true; "
    "/usr/sbin/sshd -T | grep -E 'passwordauthentication|kbdinteractiveauthentication|permitrootlogin|usepam|authenticationmethods'"
    "\""
) % (FINAL_PASSWORD, FINAL_PASSWORD)
stdin, stdout, stderr = ssh.exec_command(setup_cmd, timeout=300)
print('STEP2_SET_PASSWORD_START')
print(stdout.read().decode('utf-8', errors='ignore'))
print(stderr.read().decode('utf-8', errors='ignore'))
ssh.close()

print('STEP3_PASSWORD_RELOGIN_START')
def interactive_handler(title, instructions, prompts):
    return [FINAL_PASSWORD for _ in prompts]

transport = paramiko.Transport((IP, 22))
transport.start_client(timeout=30)
transport.auth_interactive('root', interactive_handler)
print('root_interactive_authenticated', transport.is_authenticated())
channel = transport.open_session()
channel.exec_command("bash -lc 'whoami; id; apt-get update -y >/dev/null; apt-get install -y ca-certificates curl wget sudo procps iproute2 git make gcc libssl-dev zlib1g-dev >/dev/null 2>&1 || true; printf \"%s\\n\" net.core.default_qdisc=fq net.ipv4.tcp_congestion_control=bbr > /etc/sysctl.d/99-bbr.conf; sysctl --system >/tmp/bbr.out 2>&1 || true; sysctl net.ipv4.tcp_congestion_control; sysctl net.core.default_qdisc'",)
out = b''
err = b''
while True:
    if channel.recv_ready():
        out += channel.recv(4096)
    if channel.recv_stderr_ready():
        err += channel.recv_stderr(4096)
    if channel.exit_status_ready():
        break
print(out.decode('utf-8', errors='ignore'))
print(err.decode('utf-8', errors='ignore'))
print('STEP3_EXIT', channel.recv_exit_status())
transport.close()
