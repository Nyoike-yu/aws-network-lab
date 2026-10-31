"""
common.py - shared settings + helpers for Project 2.

Project 2 builds ON TOP of Project 1: it reads Project 1's state.json to find
the VPC and the private web server.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
P1_DIR = os.path.join(HERE, "..", "project1-secure-vpc")
P1_STATE_FILE = os.path.join(P1_DIR, "state.json")
P1_KEY_FILE = os.path.join(P1_DIR, "aws-network-lab-key.pem")
STATE_FILE = os.path.join(HERE, "monitoring_state.json")   # IDs of what Project 2 creates

SSH_USER = "ubuntu"

# CloudWatch "namespaces" group related metrics, like folders.
NAMESPACE_SERVER = "LabServer"      # CPU / memory / disk pushed from the server
NAMESPACE_NET = "LabNetwork"        # rejected-connection count from flow logs

ROLE_NAME = "lab-ec2-metrics-role"
PROFILE_NAME = "lab-ec2-metrics-profile"
TOPIC_NAME = "lab-alerts"
ALARM_DISK = "lab-disk-high"
ALARM_REJECTS = "lab-rejected-connections-spike"


def load_p1():
    """Load Project 1's resource IDs (VPC, instances, IPs)."""
    if not os.path.exists(P1_STATE_FILE):
        sys.exit("Project 1 is not deployed. Run project1-secure-vpc/deploy.py first.")
    with open(P1_STATE_FILE) as f:
        return json.load(f)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
