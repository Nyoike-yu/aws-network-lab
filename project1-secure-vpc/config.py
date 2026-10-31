#config.py - every setting for Project 1 lives here.
#Why a separate file? If I want a different region or CIDR plan, I change 1 place instead of hunting through four scripts.

import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Region to build in. Africa (Cape Town) 'af-south-1' is closest to Nairobi but
# is an opt-in region, so I default to Ireland. Override with:  export AWS_REGION=af-south-1
REGION = os.environ.get("AWS_REGION", "eu-west-1")

PROJECT_TAG = "aws-network-lab"      # tagged on everything so I can find/cost-track it

# CIDR plan
# /16 gives 65,536 addresses. Each /24 subnet gives 256 (251 usable: AWS reserves 5).
VPC_CIDR = "10.0.0.0/16"
PUBLIC_SUBNET_CIDRS = ["10.0.1.0/24", "10.0.2.0/24"]      # one per AZ
PRIVATE_SUBNET_CIDRS = ["10.0.11.0/24", "10.0.12.0/24"]   # one per AZ


INSTANCE_TYPE = "t3.micro"           # free-tier eligible on most new accounts
SSH_USER = "ubuntu"                  # default login user on Ubuntu AMIs
# AWS publishes the latest official Ubuntu 22.04 AMI id in a public SSM parameter,
# so I never hardcode an AMI id (they differ per region and go stale).
UBUNTU_AMI_PARAM = ("/aws/service/canonical/ubuntu/server/22.04/stable/"
                    "current/amd64/hvm/ebs-gp2/ami-id")

KEY_NAME = "aws-network-lab-key"
KEY_FILE = os.path.join(HERE, f"{KEY_NAME}.pem")   # private key saved locally (git-ignored!)

# Remembers the IDs of what I created so verify.py / teardown.py can find them.
STATE_FILE = os.path.join(HERE, "state.json")
