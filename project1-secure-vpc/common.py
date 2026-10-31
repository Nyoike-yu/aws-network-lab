#common.py - small helpers shared by deploy.py, verify.py and teardown.py.

import json
import os
import urllib.request

from config import PROJECT_TAG, STATE_FILE


def load_state():
# Reads the saved resource IDs (returns {} if nothing deployed yet)
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
#Write resource IDs to disk. Called after EVERY created resource so that if the script crashes half-way, teardown.py still knows what exists
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def tag_spec(resource_type, name):
    """Build the TagSpecifications argument boto3 wants. Gives each resource a
    friendly Name plus a Project tag."""
    return [{
        "ResourceType": resource_type,
        "Tags": [
            {"Key": "Name", "Value": name},
            {"Key": "Project", "Value": PROJECT_TAG},
        ],
    }]


def my_public_ip():
# Asks AWS 'what IP am I coming from?'. Used to lock SSH to ONLY my IP
    with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10) as r:
        return r.read().decode().strip()
