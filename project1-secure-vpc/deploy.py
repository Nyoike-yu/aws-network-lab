#!/usr/bin/env python3
"""
deploy.py - builds the entire network and both servers using boto3 (the AWS SDK).

What gets built:

    Internet
    
 Internet Gateway
    
  PUBLIC subnets (route 0.0.0.0/0 -> IGW)      <- bastion host + NAT gateway live here
     
  PRIVATE subnets (route 0.0.0.0/0 -> NAT)     <- web server lives here (no public IP)

Run:  python deploy.py
"""
import os
import sys

import boto3

from common import load_state, my_public_ip, save_state, tag_spec
from config import (INSTANCE_TYPE, KEY_FILE, KEY_NAME, PRIVATE_SUBNET_CIDRS,
                    PUBLIC_SUBNET_CIDRS, REGION, SSH_USER, UBUNTU_AMI_PARAM,
                    VPC_CIDR)
from userdata import BASTION_USER_DATA, WEB_USER_DATA


def main():
    if load_state():
        sys.exit("state.json already exists - run `python teardown.py` first.")

    ec2 = boto3.client("ec2", region_name=REGION)
    ssm = boto3.client("ssm", region_name=REGION)

    state = {"region": REGION}
    save_state(state)

    def remember(key, value):
        """Store an ID in state.json immediately (so teardown can always find it)."""
        state[key] = value
        save_state(state)
        print(f"    {key} = {value}")

    # 1. VPC
    print(f"[1/9] Creating VPC {VPC_CIDR} in {REGION}")
    vpc_id = ec2.create_vpc(
        CidrBlock=VPC_CIDR, TagSpecifications=tag_spec("vpc", "lab-vpc")
    )["Vpc"]["VpcId"]
    remember("vpc_id", vpc_id)
    ec2.get_waiter("vpc_available").wait(VpcIds=[vpc_id])
    # DNS hostnames let instances get names like ip-10-0-11-5.eu-west-1.compute.internal
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})

    # 2. Internet gateway
    print("[2/9] Creating + attaching the Internet Gateway")
    igw_id = ec2.create_internet_gateway(
        TagSpecifications=tag_spec("internet-gateway", "lab-igw")
    )["InternetGateway"]["InternetGatewayId"]
    remember("igw_id", igw_id)
    ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)

    # 3. Subnets
    print("[3/9] Creating subnets across 2 Availability Zones")
    # An AZ = a separate data centre in the region. Spreading subnets over two
    # AZs is the standard pattern for high availability.
    azs = [az["ZoneName"] for az in ec2.describe_availability_zones(
        Filters=[{"Name": "state", "Values": ["available"]}])["AvailabilityZones"]][:2]

    public_subnets, private_subnets = [], []
    for i, (az, cidr) in enumerate(zip(azs, PUBLIC_SUBNET_CIDRS), start=1):
        sid = ec2.create_subnet(
            VpcId=vpc_id, CidrBlock=cidr, AvailabilityZone=az,
            TagSpecifications=tag_spec("subnet", f"lab-public-{i}"),
        )["Subnet"]["SubnetId"]
        # Anything launched here automatically gets a public IP.
        ec2.modify_subnet_attribute(SubnetId=sid, MapPublicIpOnLaunch={"Value": True})
        public_subnets.append(sid)
    for i, (az, cidr) in enumerate(zip(azs, PRIVATE_SUBNET_CIDRS), start=1):
        sid = ec2.create_subnet(
            VpcId=vpc_id, CidrBlock=cidr, AvailabilityZone=az,
            TagSpecifications=tag_spec("subnet", f"lab-private-{i}"),
        )["Subnet"]["SubnetId"]
        private_subnets.append(sid)
    remember("public_subnets", public_subnets)
    remember("private_subnets", private_subnets)

    # 4. NAT gateway + Elastic IP
    print("[4/9] Creating NAT gateway (takes ~1-2 min, costs ~$0.045/hr!)")
    # The NAT gateway sits in a PUBLIC subnet and needs a fixed public address (Elastic IP).
    eip = ec2.allocate_address(
        Domain="vpc", TagSpecifications=tag_spec("elastic-ip", "lab-nat-eip"))
    remember("eip_allocation_id", eip["AllocationId"])
    remember("nat_public_ip", eip["PublicIp"])
    nat_id = ec2.create_nat_gateway(
        SubnetId=public_subnets[0], AllocationId=eip["AllocationId"],
        TagSpecifications=tag_spec("natgateway", "lab-nat"),
    )["NatGateway"]["NatGatewayId"]
    remember("nat_id", nat_id)
    ec2.get_waiter("nat_gateway_available").wait(NatGatewayIds=[nat_id])

    # 5. Route tables
    print("[5/9] Creating route tables")
    # What makes a subnet "public"? NOT its name. It is a route to the Internet Gateway.
    public_rt = ec2.create_route_table(
        VpcId=vpc_id, TagSpecifications=tag_spec("route-table", "lab-public-rt")
    )["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=public_rt, DestinationCidrBlock="0.0.0.0/0",
                     GatewayId=igw_id)
    for sid in public_subnets:
        ec2.associate_route_table(RouteTableId=public_rt, SubnetId=sid)

    # Private subnets send internet-bound traffic to the NAT gateway instead.
    # The NAT lets them go OUT, but nothing on the internet can start a connection IN.
    private_rt = ec2.create_route_table(
        VpcId=vpc_id, TagSpecifications=tag_spec("route-table", "lab-private-rt")
    )["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=private_rt, DestinationCidrBlock="0.0.0.0/0",
                     NatGatewayId=nat_id)
    for sid in private_subnets:
        ec2.associate_route_table(RouteTableId=private_rt, SubnetId=sid)
    remember("public_rt_id", public_rt)
    remember("private_rt_id", private_rt)

    # 6. Security groups
    print("[6/9] Creating security groups")
    # Security groups are STATEFUL firewalls: if inbound is allowed, the reply is
    # automatically allowed back out. Everything inbound is denied unless listed.
    my_ip = my_public_ip()
    print(f"    (locking SSH to my current IP: {my_ip})")

    bastion_sg = ec2.create_security_group(
        GroupName="lab-bastion-sg", Description="Bastion: SSH from my IP only",
        VpcId=vpc_id, TagSpecifications=tag_spec("security-group", "lab-bastion-sg"),
    )["GroupId"]
    remember("bastion_sg_id", bastion_sg)
    ec2.authorize_security_group_ingress(GroupId=bastion_sg, IpPermissions=[{
        "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
        "IpRanges": [{"CidrIp": f"{my_ip}/32", "Description": "SSH from my IP only"}],
    }])

    web_sg = ec2.create_security_group(
        GroupName="lab-web-sg", Description="Private web: only reachable from bastion",
        VpcId=vpc_id, TagSpecifications=tag_spec("security-group", "lab-web-sg"),
    )["GroupId"]
    remember("web_sg_id", web_sg)
    # Source = another security group (not an IP range). So ONLY instances wearing
    # the bastion SG can reach the web server. Nothing from the internet can.
    ec2.authorize_security_group_ingress(GroupId=web_sg, IpPermissions=[
        {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
         "UserIdGroupPairs": [{"GroupId": bastion_sg, "Description": "SSH from bastion"}]},
        {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80,
         "UserIdGroupPairs": [{"GroupId": bastion_sg, "Description": "HTTP from bastion"}]},
    ])

    # 7. Key pair
    print(f"[7/9] Creating SSH key pair -> {KEY_FILE}")
    key = ec2.create_key_pair(KeyName=KEY_NAME, KeyType="ed25519",
                              TagSpecifications=tag_spec("key-pair", KEY_NAME))
    with open(KEY_FILE, "w") as f:
        f.write(key["KeyMaterial"])       # AWS only shows this ONCE
    os.chmod(KEY_FILE, 0o400)             # ssh refuses keys that others can read
    remember("key_name", KEY_NAME)

    # 8. Launch servers
    print("[8/9] Launching servers")
    ami = ssm.get_parameter(Name=UBUNTU_AMI_PARAM)["Parameter"]["Value"]
    print(f"    Ubuntu 22.04 AMI: {ami}")

    common = dict(
        ImageId=ami, InstanceType=INSTANCE_TYPE, KeyName=KEY_NAME,
        MinCount=1, MaxCount=1,
        # IMDSv2 only: protects the instance metadata service (and the IAM
        # credentials behind it) from SSRF-style attacks.
        MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
    )
    bastion = ec2.run_instances(
        **common, SubnetId=public_subnets[0], SecurityGroupIds=[bastion_sg],
        UserData=BASTION_USER_DATA,
        TagSpecifications=tag_spec("instance", "lab-bastion"),
    )["Instances"][0]["InstanceId"]
    web = ec2.run_instances(
        **common, SubnetId=private_subnets[0], SecurityGroupIds=[web_sg],
        UserData=WEB_USER_DATA,
        TagSpecifications=tag_spec("instance", "lab-web"),
    )["Instances"][0]["InstanceId"]
    remember("bastion_id", bastion)
    remember("web_id", web)
    ec2.get_waiter("instance_running").wait(InstanceIds=[bastion, web])

    # 9. Summary
    print("[9/9] Reading back addresses")
    for inst_id, label, key_name in [(bastion, "bastion", "bastion_public_ip"),
                                     (web, "web", "web_private_ip")]:
        inst = ec2.describe_instances(InstanceIds=[inst_id])["Reservations"][0]["Instances"][0]
        value = inst.get("PublicIpAddress") if label == "bastion" else inst["PrivateIpAddress"]
        remember(key_name, value)

    bip, wip = state["bastion_public_ip"], state["web_private_ip"]
    print("\nDONE. Servers need ~1-2 more minutes to finish configuring (user data).")
    print(f"\nSSH to bastion:\n  ssh -i {KEY_FILE} {SSH_USER}@{bip}")
    print("\nSSH to the private web server THROUGH the bastion:")
    print(f'  ssh -i {KEY_FILE} -o ProxyCommand="ssh -i {KEY_FILE} -W %h:%p {SSH_USER}@{bip}" {SSH_USER}@{wip}')
    print("\nNext: python verify.py      When finished: python teardown.py")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nERROR: {exc}\nSomething failed part-way. Run `python teardown.py` to clean up.")
        raise
