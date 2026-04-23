echo 'ubuntu:TmpPass#20260418' | chpasswd
sed -i "s/^#\?PasswordAuthentication.*/PasswordAuthentication yes/" /etc/ssh/sshd_config
sed -i "s/^#\?KbdInteractiveAuthentication.*/KbdInteractiveAuthentication yes/" /etc/ssh/sshd_config
sed -i "s/^#\?ChallengeResponseAuthentication.*/ChallengeResponseAuthentication yes/" /etc/ssh/sshd_config
if ! grep -q '^UsePAM yes' /etc/ssh/sshd_config; then echo 'UsePAM yes' >> /etc/ssh/sshd_config; fi
systemctl restart ssh || systemctl restart sshd
echo CONFIG_OK
