#!/usr/bin/env python3
"""
teardown.py - deletes EVERYTHING deploy.py created, in the right order.

AWS refuses to delete a resource while something else still depends on it
(e.g. you can't delete a VPC that still has subnets). So we delete from the
inside out:  instances -> NAT -> elastic IP -> key -> SGs -> routes -> IGW -> subnets -> VPC

It discovers resources by asking AWS "what's inside this VPC?", so it still works
if deploy.py crashed half-way.

Run:  python teardown.py        (ALWAYS do this when finished - the NAT gateway costs money!)
"""
import os
import time

import boto3
from botocore.exceptions import ClientError

from common import load_state
from config import KEY_FILE, KEY_NAME, REGION, STATE_FILE


def attempt(label, fn, tries=1, wait=10):
    """Run fn(). On 'dependency' errors, wait and retry (AWS cleans up network
    interfaces in the background, so deletes can fail briefly)."""
    for n in range(1, tries + 1):
        try:
            fn()
            print(f"  [ok]   {label}")
            return True
        except ClientError as e:
            code = e.response["Error"]["Code"]
            retryable = code in ("DependencyViolation", "InvalidGroup.InUse",
                                 "IncorrectState", "InvalidParameterValue")
            if retryable and n < tries:
                time.sleep(wait)
                continue
            print(f"  [skip] {label}: {code}")
            return False


def main():
    state = load_state()
    if not state:
        print("No state.json - nothing to tear down.")
        return
    ec2 = boto3.client("ec2", region_name=state.get("region", REGION))
    vpc_id = state.get("vpc_id")
    vpc_filter = [{"Name": "vpc-id", "Values": [vpc_id]}] if vpc_id else None

    if vpc_id:
        # 1. Instances
        print("Terminating instances...")
        resp = ec2.describe_instances(Filters=vpc_filter + [{
            "Name": "instance-state-name",
            "Values": ["pending", "running", "stopping", "stopped"]}])
        ids = [i["InstanceId"] for r in resp["Reservations"] for i in r["Instances"]]
        if ids:
            ec2.terminate_instances(InstanceIds=ids)
            ec2.get_waiter("instance_terminated").wait(InstanceIds=ids)
            print(f"  [ok]   terminated {ids}")

        # 2. NAT gateway (must be fully gone before its Elastic IP can be released)
        print("Deleting NAT gateway...")
        nats = ec2.describe_nat_gateways(Filter=vpc_filter + [{
            "Name": "state", "Values": ["pending", "available", "failed"]}])["NatGateways"]
        for nat in nats:
            ec2.delete_nat_gateway(NatGatewayId=nat["NatGatewayId"])
        for nat in nats:
            for _ in range(40):                      # poll up to ~6 min
                st = ec2.describe_nat_gateways(
                    NatGatewayIds=[nat["NatGatewayId"]])["NatGateways"][0]["State"]
                if st == "deleted":
                    break
                time.sleep(10)
            print(f"  [ok]   NAT {nat['NatGatewayId']} deleted")

    # 3. Elastic IP
    if state.get("eip_allocation_id"):
        print("Releasing Elastic IP...")
        attempt("release EIP", lambda: ec2.release_address(
            AllocationId=state["eip_allocation_id"]), tries=3)

    # 4. Key pair (AWS copy + local .pem file)
    print("Deleting key pair...")
    attempt("delete key pair", lambda: ec2.delete_key_pair(KeyName=KEY_NAME))
    if os.path.exists(KEY_FILE):
        os.remove(KEY_FILE)
        print("  [ok]   removed local .pem")

    if vpc_id:
        # 5. Security groups (the web SG references the bastion SG, so the order
        #    matters - retrying a few passes sorts it out)
        print("Deleting security groups...")
        sgs = ec2.describe_security_groups(Filters=vpc_filter)["SecurityGroups"]
        for sg in [s for s in sgs if s["GroupName"] != "default"]:
            # Remove rules first so SGs referencing each other can be deleted
            if sg["IpPermissions"]:
                attempt(f"revoke rules {sg['GroupName']}", lambda sg=sg: ec2.revoke_security_group_ingress(
                    GroupId=sg["GroupId"], IpPermissions=sg["IpPermissions"]))
        for sg in [s for s in sgs if s["GroupName"] != "default"]:
            attempt(f"delete SG {sg['GroupName']}",
                    lambda sg=sg: ec2.delete_security_group(GroupId=sg["GroupId"]), tries=6)

        # 6. Route tables (un-associate from subnets first; never touch the main one)
        print("Deleting route tables...")
        for rt in ec2.describe_route_tables(Filters=vpc_filter)["RouteTables"]:
            if any(a.get("Main") for a in rt["Associations"]):
                continue
            for a in rt["Associations"]:
                attempt("disassociate route table", lambda a=a: ec2.disassociate_route_table(
                    AssociationId=a["RouteTableAssociationId"]))
            attempt(f"delete route table {rt['RouteTableId']}",
                    lambda rt=rt: ec2.delete_route_table(RouteTableId=rt["RouteTableId"]))

        # 7. Internet gateway (detach, then delete)
        print("Deleting internet gateway...")
        for igw in ec2.describe_internet_gateways(Filters=[
                {"Name": "attachment.vpc-id", "Values": [vpc_id]}])["InternetGateways"]:
            iid = igw["InternetGatewayId"]
            attempt("detach IGW", lambda iid=iid: ec2.detach_internet_gateway(
                InternetGatewayId=iid, VpcId=vpc_id), tries=6)
            attempt("delete IGW", lambda iid=iid: ec2.delete_internet_gateway(InternetGatewayId=iid))

        # 8. Subnets, then the VPC itself
        print("Deleting subnets and VPC...")
        for sn in ec2.describe_subnets(Filters=vpc_filter)["Subnets"]:
            attempt(f"delete subnet {sn['SubnetId']}",
                    lambda sn=sn: ec2.delete_subnet(SubnetId=sn["SubnetId"]), tries=6)
        attempt("delete VPC", lambda: ec2.delete_vpc(VpcId=vpc_id), tries=6)

    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    print("\nTeardown complete. Double-check the VPC, EC2 and NAT consoles to be sure.")


if __name__ == "__main__":
    main()
