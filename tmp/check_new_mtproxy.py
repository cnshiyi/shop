from io import StringIO
import os
import boto3
import paramiko

IP = '13.213.154.113'
PASSWORD = 'RootPass_20260418!'

client = boto3.client(
    'lightsail',
    region_name='ap-southeast-1',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID', ''),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY', ''),
)
pem = client.download_default_key_pair()['privateKeyBase64']
pkey = paramiko.RSAKey.from_private_key(StringIO(pem))

def interactive_handler(title, instructions, prompts):
    return [PASSWORD for _ in prompts]

transport = paramiko.Transport((IP, 22))
transport.start_client(timeout=30)
transport.auth_interactive('root', interactive_handler)
channel = transport.open_session()
channel.exec_command("bash -lc 'systemctl status mtproxy.service --no-pager -l || true; echo SEP; journalctl -u mtproxy.service -n 80 --no-pager || true; echo SEP; ss -lntup | grep 9528 || true; echo SEP; ps -ef | grep -i mtproto-proxy | grep -v grep || true; echo SEP; cat /home/mtproxy/secret.txt || true'")
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
print('exit', channel.recv_exit_status())
transport.close()
