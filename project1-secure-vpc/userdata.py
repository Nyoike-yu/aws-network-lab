"""
userdata.py - bash scripts that run ONCE, as root, the first time each server boots
(AWS calls this "user data", it is executed by cloud-init).

Output is logged to /var/log/cloud-init-output.log on the server - the first place
to look if something did not configure properly.
"""

# Shared by both servers: harden SSH, then set up the ufw firewall rules.
_COMMON = r"""#!/bin/bash
set -euxo pipefail                 # stop on any error, print each command (for the log)
export DEBIAN_FRONTEND=noninteractive

# ---------- 1. SSH hardening ----------
# sshd uses the FIRST value it finds for each setting, and config files in
# sshd_config.d/ are read in alphabetical order. Naming mine "01-" makes sure
# my rules win over cloud-init's own file.
cat > /etc/ssh/sshd_config.d/01-hardening.conf <<'SSHCONF'
PasswordAuthentication no     # keys only: passwords can be brute-forced
PermitRootLogin no            # nobody logs in directly as root
MaxAuthTries 3                # fewer guesses per connection
X11Forwarding no              # we don't need GUI forwarding = smaller attack surface
SSHCONF
systemctl restart ssh

# ---------- 2. Host firewall (ufw) ----------
# A second layer of defence INSIDE the server, on top of the AWS security group.
apt-get update -y
apt-get install -y ufw
ufw default deny incoming          # block everything inbound by default...
ufw default allow outgoing         # ...but let the server make outbound requests
ufw allow 22/tcp                   # ...except SSH (we must not lock ourselves out!)
"""

# Bastion = just the hardening + firewall. Turn the firewall on at the end.
BASTION_USER_DATA = _COMMON + "ufw --force enable\n"

# Private web server = hardening + firewall + nginx.
# apt-get here only works because the private subnet routes outbound traffic
# through the NAT gateway (created in deploy.py before this server launches).
WEB_USER_DATA = _COMMON + r"""
apt-get install -y nginx
echo "<h1>Hello from the PRIVATE web server: $(hostname)</h1>" > /var/www/html/index.html
systemctl enable --now nginx
ufw allow 80/tcp                   # HTTP, so nginx is reachable
ufw --force enable
"""
