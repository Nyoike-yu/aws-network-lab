#!/usr/bin/env python3
"""
setup_monitoring.py - creates the monitoring plumbing for the Project 1 VPC.

  1. S3 bucket (private) to store VPC Flow Logs
  2. VPC Flow Logs  -> records every accepted/rejected network flow in the VPC
  3. IAM role + instance profile so the server can push metrics WITHOUT any
     hardcoded access keys (least privilege: it may only call PutMetricData,
     and only into the 'LabServer' namespace)
  4. SNS topic + email subscription (where alerts get sent)
  5. CloudWatch alarms: disk > 80%  and  rejected-connections spike

Run:  python setup_monitoring.py --email you@example.com
"""
import argparse
import json
import time

import boto3
from botocore.exceptions import ClientError

from common import (ALARM_DISK, ALARM_REJECTS, NAMESPACE_NET, NAMESPACE_SERVER,
                    PROFILE_NAME, ROLE_NAME, TOPIC_NAME, load_p1, save_state)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", required=True, help="where alert emails are sent")
    ap.add_argument("--disk-threshold", type=float, default=80.0, help="disk %% to alarm at")
    ap.add_argument("--reject-threshold", type=int, default=50,
                    help="rejected flows per 5 min to alarm at")
    args = ap.parse_args()

    p1 = load_p1()
    region, vpc_id, web_id = p1["region"], p1["vpc_id"], p1["web_id"]

    ec2 = boto3.client("ec2", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    iam = boto3.client("iam")                       # IAM is global, no region
    sns = boto3.client("sns", region_name=region)
    cw = boto3.client("cloudwatch", region_name=region)
    account = boto3.client("sts").get_caller_identity()["Account"]

    state = {"region": region, "account": account}
    save_state(state)

    def remember(k, v):
        state[k] = v
        save_state(state)
        print(f"    {k} = {v}")

    # ------------------------------------------------------- 1. S3 bucket
    bucket = f"lab-flowlogs-{account}-{region}"    # bucket names are globally unique
    print(f"[1/6] Creating private S3 bucket {bucket}")
    kwargs = {"Bucket": bucket}
    if region != "us-east-1":                       # us-east-1 is the odd one out in the S3 API
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        s3.create_bucket(**kwargs)
    except ClientError as e:
        if e.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            raise
    # Flow logs can reveal your network layout - make sure the bucket can never go public.
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True,
        "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    remember("bucket", bucket)

    # ------------------------------------------------------ 2. Flow logs
    print("[2/6] Enabling VPC Flow Logs -> S3 (all traffic, 1-minute aggregation)")
    resp = ec2.create_flow_logs(
        ResourceIds=[vpc_id], ResourceType="VPC",
        TrafficType="ALL",                          # ACCEPT and REJECT
        LogDestinationType="s3", LogDestination=f"arn:aws:s3:::{bucket}",
        MaxAggregationInterval=60,
    )
    if resp.get("Unsuccessful"):
        raise RuntimeError(resp["Unsuccessful"])
    remember("flow_log_id", resp["FlowLogIds"][0])

    # --------------------------------------------- 3. IAM role for the server
    print("[3/6] Creating IAM role + instance profile (least privilege)")
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
        "Action": "sts:AssumeRole"}]}
    policy = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow",
        "Action": "cloudwatch:PutMetricData",       # the ONLY thing the server may do
        "Resource": "*",
        "Condition": {"StringEquals": {"cloudwatch:namespace": NAMESPACE_SERVER}},
    }]}
    try:
        iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust))
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="put-lab-metrics",
                        PolicyDocument=json.dumps(policy))
    try:
        iam.create_instance_profile(InstanceProfileName=PROFILE_NAME)
        iam.add_role_to_instance_profile(InstanceProfileName=PROFILE_NAME, RoleName=ROLE_NAME)
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
    remember("role_name", ROLE_NAME)
    remember("profile_name", PROFILE_NAME)

    # IAM is "eventually consistent": a brand-new profile may not be visible to EC2
    # for a few seconds, so retry the attach.
    print("    attaching instance profile to the web server...")
    for attempt in range(8):
        try:
            ec2.associate_iam_instance_profile(
                IamInstanceProfile={"Name": PROFILE_NAME}, InstanceId=web_id)
            break
        except ClientError as e:
            if attempt == 7:
                raise
            print(f"    not ready yet ({e.response['Error']['Code']}), retrying...")
            time.sleep(8)

    # --------------------------------------------------------- 4. SNS topic
    print(f"[4/6] Creating SNS topic and subscribing {args.email}")
    topic_arn = sns.create_topic(Name=TOPIC_NAME)["TopicArn"]
    sns.subscribe(TopicArn=topic_arn, Protocol="email", Endpoint=args.email)
    remember("topic_arn", topic_arn)
    print("    >>> CHECK YOUR INBOX and click 'Confirm subscription' or no alerts will arrive!")

    # ----------------------------------------------------- 5. CloudWatch alarms
    print("[5/6] Creating CloudWatch alarms")
    cw.put_metric_alarm(
        AlarmName=ALARM_DISK,
        AlarmDescription=f"Disk usage on the web server is above {args.disk_threshold}%",
        Namespace=NAMESPACE_SERVER, MetricName="DiskUsedPercent",
        Dimensions=[{"Name": "InstanceId", "Value": web_id}],
        Statistic="Maximum", Period=300, EvaluationPeriods=1,
        Threshold=args.disk_threshold, ComparisonOperator="GreaterThanThreshold",
        TreatMissingData="notBreaching",            # no data = don't page me
        AlarmActions=[topic_arn],
    )
    cw.put_metric_alarm(
        AlarmName=ALARM_REJECTS,
        AlarmDescription=f"More than {args.reject_threshold} rejected flows in 5 min (possible port scan)",
        Namespace=NAMESPACE_NET, MetricName="RejectedConnections",
        Statistic="Sum", Period=300, EvaluationPeriods=1,
        Threshold=args.reject_threshold, ComparisonOperator="GreaterThanThreshold",
        TreatMissingData="notBreaching",
        AlarmActions=[topic_arn],
    )

    print("[6/6] Done.")
    print("\nNext steps:")
    print("  1. Confirm the SNS email subscription")
    print("  2. python install_agent.py           (puts the metrics script + cron on the server)")
    print("  3. python generate_test_traffic.py   (creates REJECT entries safely, against MY bastion)")
    print("  4. wait ~10 min, then: python analyze_flow_logs.py --publish")


if __name__ == "__main__":
    main()
