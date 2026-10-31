#!/usr/bin/env python3
"""
teardown_monitoring.py - removes everything setup_monitoring.py created.

Run this BEFORE project1-secure-vpc/teardown.py.
"""
import os

import boto3
from botocore.exceptions import ClientError

from common import (ALARM_DISK, ALARM_REJECTS, PROFILE_NAME, ROLE_NAME, STATE_FILE,
                    load_state)


def attempt(label, fn):
    try:
        fn()
        print(f"  [ok]   {label}")
    except ClientError as e:
        print(f"  [skip] {label}: {e.response['Error']['Code']}")


def main():
    st = load_state()
    if not st:
        print("No monitoring_state.json - nothing to remove.")
        return
    region = st["region"]
    ec2 = boto3.client("ec2", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    iam = boto3.client("iam")
    sns = boto3.client("sns", region_name=region)
    cw = boto3.client("cloudwatch", region_name=region)

    print("Alarms...")
    attempt("delete alarms", lambda: cw.delete_alarms(AlarmNames=[ALARM_DISK, ALARM_REJECTS]))

    print("Flow logs + bucket...")
    if st.get("flow_log_id"):
        attempt("delete flow log", lambda: ec2.delete_flow_logs(FlowLogIds=[st["flow_log_id"]]))
    if st.get("bucket"):
        def empty_and_delete():
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=st["bucket"]):
                objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if objs:
                    s3.delete_objects(Bucket=st["bucket"], Delete={"Objects": objs})
            s3.delete_bucket(Bucket=st["bucket"])      # a bucket must be empty to delete
        attempt("empty + delete bucket", empty_and_delete)

    print("IAM...")
    # Detach the profile from any instance using it (if Project 1 is already gone, nothing to do)
    try:
        assocs = ec2.describe_iam_instance_profile_associations(Filters=[
            {"Name": "state", "Values": ["associated"]}])["IamInstanceProfileAssociations"]
        for a in assocs:
            if a["IamInstanceProfile"]["Arn"].endswith(f"/{PROFILE_NAME}"):
                attempt("disassociate profile", lambda a=a: ec2.disassociate_iam_instance_profile(
                    AssociationId=a["AssociationId"]))
    except ClientError:
        pass
    attempt("remove role from profile", lambda: iam.remove_role_from_instance_profile(
        InstanceProfileName=PROFILE_NAME, RoleName=ROLE_NAME))
    attempt("delete instance profile", lambda: iam.delete_instance_profile(
        InstanceProfileName=PROFILE_NAME))
    attempt("delete role policy", lambda: iam.delete_role_policy(
        RoleName=ROLE_NAME, PolicyName="put-lab-metrics"))
    attempt("delete role", lambda: iam.delete_role(RoleName=ROLE_NAME))

    print("SNS...")
    if st.get("topic_arn"):
        attempt("delete topic", lambda: sns.delete_topic(TopicArn=st["topic_arn"]))

    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    print("\nMonitoring removed. Now run project1-secure-vpc/teardown.py")


if __name__ == "__main__":
    main()
