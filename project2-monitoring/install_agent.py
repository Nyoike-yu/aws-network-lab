#!/usr/bin/env python3
"""
install_agent.py - copies push_metrics.py onto the PRIVATE web server and
schedules it with cron (every minute).

The private server has no public IP, so every ssh/scp goes THROUGH the bastion
using ProxyCommand - same trick as in Project 1.

Run:  python install_agent.py
"""
import os
import subprocess
import sys

from common import HERE, P1_KEY_FILE, SSH_USER, load_p1

p1 = load_p1()
bastion, web = p1["bastion_public_ip"], p1["web_private_ip"]

OPTS = ["-i", P1_KEY_FILE, "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]
PROXY = (f"ProxyCommand=ssh {' '.join(OPTS)} -W %h:%p {SSH_USER}@{bastion}")

script = os.path.join(HERE, "server_scripts", "push_metrics.py")

print("Copying push_metrics.py to the private server (via bastion)...")
subprocess.run(["scp", *OPTS, "-o", PROXY, script, f"{SSH_USER}@{web}:/tmp/push_metrics.py"],
               check=True)

# One long remote command, step by step:
remote = " && ".join([
    "sudo apt-get update -y",
    "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-boto3",   # AWS SDK for the server
    "sudo mkdir -p /opt/lab",
    "sudo mv /tmp/push_metrics.py /opt/lab/push_metrics.py",
    # /etc/cron.d files need a USER column (root) - unlike a personal crontab
    "echo '* * * * * root /usr/bin/python3 /opt/lab/push_metrics.py >> /var/log/lab-metrics.log 2>&1'"
    " | sudo tee /etc/cron.d/lab-metrics",
    "sudo chmod 644 /etc/cron.d/lab-metrics",
    "sudo python3 /opt/lab/push_metrics.py",        # run once now = instant proof the IAM role works
])
print("Installing dependencies + cron job...")
r = subprocess.run(["ssh", *OPTS, "-o", PROXY, f"{SSH_USER}@{web}", remote])
if r.returncode != 0:
    sys.exit("Install failed - see the error above. (Common causes: IAM profile not attached yet, "
             "or the server is still booting.)")
print("\nAgent installed. Metrics appear in CloudWatch > Metrics > LabServer within ~2 minutes.")
print(f"Watch the log:  ssh ... 'tail -f /var/log/lab-metrics.log'")
