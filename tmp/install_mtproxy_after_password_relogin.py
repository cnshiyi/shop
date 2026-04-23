from io import StringIO
import os
import boto3
import paramiko
from cloud.bootstrap import _build_mtproxy_script

IP = '3.0.231.151'
PASSWORD = 'RootPass_20260418!'
PORT = 9528

client = boto3.client(
    'lightsail',
    region_name='ap-southeast-1',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID', ''),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY', ''),
)
pem = client.download_default_key_pair()['privateKeyBase64']
pkey = paramiko.RSAKey.from_private_key(StringIO(pem))
script = _build_mtproxy_script(PORT)

def interactive_handler(title, instructions, prompts):
    return [PASSWORD for _ in prompts]

transport = paramiko.Transport((IP, 22))
transport.start_client(timeout=30)
transport.auth_interactive('root', interactive_handler)
print('PASSWORD_RELOGIN_OK', transport.is_authenticated())
sftp = paramiko.SFTPClient.from_transport(transport)
with sftp.file('/root/install_mtproxy.sh', 'w') as f:
    f.write(script)
sftp.chmod('/root/install_mtproxy.sh', 0o755)
sftp.close()
channel = transport.open_session()
channel.exec_command("bash -lc '/root/install_mtproxy.sh'")
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
print('INSTALL_EXIT', channel.recv_exit_status())
transport.close()
