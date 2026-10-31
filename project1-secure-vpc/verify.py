#!/usr/bin/env python3
"""
verify.py - PROVES the design works (and that the private server is truly private).

Building infrastructure is half the job; testing that it behaves as designed is
the other half. Each check prints PASS/FAIL.

Run:  python verify.py
"""
import socket
import subprocess
import sys
import time

import boto3

from common import load_state
from config import KEY_FILE, SSH_USER

state = load_state()
if not state.get("web_private_ip"):
    sys.exit("Nothing deployed (or deploy not finished). Run deploy.py first.")

ec2 = boto3.client("ec2", region_name=state["region"])
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def tcp_open(host, port, timeout=5):
    """Can I open a TCP connection to host:port from my laptop? (OSI layer 4 test)"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ssh(command, via_bastion=False):
    """Run a command on the bastion, or on the private server by hopping through the bastion."""
    bastion = state["bastion_public_ip"]
    base = ["ssh", "-i", KEY_FILE, "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
    if via_bastion:
        # ProxyCommand = "to reach the target, first SSH into the bastion and forward the connection"
        proxy = (f"ProxyCommand=ssh -i {KEY_FILE} -o StrictHostKeyChecking=no "
                 f"-o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "
                 f"-W %h:%p {SSH_USER}@{bastion}")
        base += ["-o", proxy]
        target = state["web_private_ip"]
    else:
        target = bastion
    cmd = base + [f"{SSH_USER}@{target}", command]
    result = None
    for _ in range(12):                       # servers may still be booting: retry
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 255:          # 255 = ssh itself could not connect
            return result
        time.sleep(15)
    return result


print("Waiting for both servers to finish first-boot configuration...\n")
ssh("cloud-init status --wait")
ssh("cloud-init status --wait", via_bastion=True)

# ---------- Network exposure (tested from the public internet side) ----------
check("Bastion SSH (22) reachable from my IP", tcp_open(state["bastion_public_ip"], 22))
check("Bastion HTTP (80) is NOT exposed", not tcp_open(state["bastion_public_ip"], 80, 4))

web = ec2.describe_instances(InstanceIds=[state["web_id"]])["Reservations"][0]["Instances"][0]
check("Web server has NO public IP", "PublicIpAddress" not in web)
check("Web server private IP not reachable directly from my laptop",
      not tcp_open(state["web_private_ip"], 22, 4),
      "if this fails, your home LAN may also use 10.0.x.x")

# ---------- Security group audit ----------
sg = ec2.describe_security_groups(GroupIds=[state["web_sg_id"]])["SecurityGroups"][0]
open_to_world = [r for p in sg["IpPermissions"] for r in p.get("IpRanges", [])]
check("Web SG allows traffic ONLY from the bastion SG (no CIDR rules)", not open_to_world)

# ---------- Server hardening (checked on the live servers) ----------
for label, via in (("bastion", False), ("web server", True)):
    r = ssh("sudo sshd -T | grep -Ei '^(passwordauthentication|permitrootlogin)'", via)
    out = r.stdout.lower()
    check(f"{label}: password login disabled", "passwordauthentication no" in out)
    check(f"{label}: root login disabled", "permitrootlogin no" in out)
    r = ssh("sudo ufw status", via)
    check(f"{label}: ufw firewall active", "status: active" in r.stdout.lower())

# ---------- Connectivity INSIDE the VPC ----------
r = ssh(f"curl -s -m 10 http://{state['web_private_ip']}")
check("Bastion -> private web server over HTTP works",
      "private web server" in r.stdout.lower(), r.stdout.strip()[:60])

r = ssh("curl -s -m 15 https://checkip.amazonaws.com", via_bastion=True)
check("Private server reaches the internet OUTBOUND via NAT (its public IP == NAT EIP)",
      r.stdout.strip() == state["nat_public_ip"], r.stdout.strip())

r = ssh("getent hosts amazon.com", via_bastion=True)
check("DNS resolution works on the private server", bool(r.stdout.strip()), r.stdout.strip()[:50])

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
