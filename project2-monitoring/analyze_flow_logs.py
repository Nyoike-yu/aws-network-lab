#!/usr/bin/env python3
"""
analyze_flow_logs.py - reads VPC Flow Logs from S3 and reports:
   * top talkers (who moves the most data)
   * rejected connections (blocked by security groups)
   * possible port scans (one IP rejected on MANY different ports)
   * traffic by protocol and by destination port

With --publish it also sends the rejected-flow count to CloudWatch as the custom
metric LabNetwork/RejectedConnections - which is what my alarm watches.
(Why not alarm straight from the logs? Flow logs in S3 can't have CloudWatch metric
filters; that needs CloudWatch Logs. Publishing from Python keeps S3 storage cheap.)

A flow log line has 14 space-separated fields (the default format):
  version account-id interface-id srcaddr dstaddr srcport dstport protocol
  packets bytes start end action log-status

Run:  python analyze_flow_logs.py --hours 1 --publish
"""
import argparse
import gzip
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import boto3

from common import NAMESPACE_NET, load_state

FIELDS = ["version", "account", "interface", "src", "dst", "srcport", "dstport",
          "protocol", "packets", "bytes", "start", "end", "action", "status"]
PROTOCOLS = {1: "ICMP", 6: "TCP", 17: "UDP"}   # IANA protocol numbers


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def parse_line(line):
    """Turn one log line into a dict (or None for headers / empty intervals)."""
    parts = line.split()
    if len(parts) != 14 or parts[0] == "version":
        return None
    rec = dict(zip(FIELDS, parts))
    if rec["status"] != "OK":                  # NODATA / SKIPDATA = nothing happened
        return None
    try:
        for k in ("srcport", "dstport", "protocol", "packets", "bytes", "start"):
            rec[k] = int(rec[k])
    except ValueError:
        return None
    return rec


def list_log_keys(s3, bucket, account, region, since):
    """Flow logs are stored as AWSLogs/<acct>/vpcflowlogs/<region>/YYYY/MM/DD/<file>.log.gz"""
    keys, day, today = [], since.date(), datetime.now(timezone.utc).date()
    while day <= today:
        prefix = f"AWSLogs/{account}/vpcflowlogs/{region}/{day:%Y/%m/%d}/"
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            keys += [o["Key"] for o in page.get("Contents", []) if o["LastModified"] >= since]
        day += timedelta(days=1)
    return keys


def main():
    ap = argparse.ArgumentParser(description="Analyze VPC Flow Logs")
    ap.add_argument("--hours", type=float, default=1.0, help="how far back to look")
    ap.add_argument("--top", type=int, default=5, help="rows per ranking")
    ap.add_argument("--scan-threshold", type=int, default=15,
                    help="distinct rejected ports from one IP that counts as a scan")
    ap.add_argument("--publish", action="store_true", help="send RejectedConnections to CloudWatch")
    args = ap.parse_args()

    m = load_state()
    if not m.get("bucket"):
        raise SystemExit("Run setup_monitoring.py first.")
    s3 = boto3.client("s3", region_name=m["region"])

    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    keys = list_log_keys(s3, m["bucket"], m["account"], m["region"], since)
    if not keys:
        print("No log files yet. Flow logs take ~5-15 min to appear - try again shortly.")
        return

    # ---- counters -----------------------------------------------------------
    bytes_by_src = Counter()
    rejects_by_src = Counter()
    rejected_ports = defaultdict(set)      # src IP -> set of ports it tried and was blocked on
    by_proto, by_port, actions = Counter(), Counter(), Counter()
    total = 0

    for key in keys:
        body = s3.get_object(Bucket=m["bucket"], Key=key)["Body"].read()
        for line in gzip.decompress(body).decode().splitlines():
            rec = parse_line(line)
            if not rec or rec["start"] < since.timestamp():
                continue
            total += 1
            proto = PROTOCOLS.get(rec["protocol"], str(rec["protocol"]))
            actions[rec["action"]] += 1
            by_proto[proto] += rec["bytes"]
            by_port[(proto, rec["dstport"])] += 1
            bytes_by_src[rec["src"]] += rec["bytes"]
            if rec["action"] == "REJECT":
                rejects_by_src[rec["src"]] += 1
                rejected_ports[rec["src"]].add(rec["dstport"])

    # ---- report -------------------------------------------------------------
    rejected_total = actions["REJECT"]
    print(f"\n=== Flow log report: last {args.hours}h | {len(keys)} files | {total} flow records ===")
    print("(a record = one flow per 5-tuple per minute, not one packet)\n")
    print(f"ACCEPT: {actions['ACCEPT']}    REJECT: {rejected_total}\n")

    print(f"Top {args.top} talkers by bytes sent:")
    for ip, b in bytes_by_src.most_common(args.top):
        print(f"  {ip:<16} {human(b)}")

    print(f"\nTop {args.top} sources of REJECTED traffic:")
    for ip, n in rejects_by_src.most_common(args.top):
        print(f"  {ip:<16} {n} rejected flows, {len(rejected_ports[ip])} distinct ports")

    print(f"\nPossible PORT SCANS (>= {args.scan_threshold} distinct ports rejected from one IP):")
    scanners = [ip for ip, ports in rejected_ports.items() if len(ports) >= args.scan_threshold]
    if scanners:
        for ip in scanners:
            print(f"  !! {ip} tried {len(rejected_ports[ip])} different ports")
    else:
        print("  none detected")

    print("\nBytes by protocol:")
    for proto, b in by_proto.most_common():
        print(f"  {proto:<6} {human(b)}")

    print(f"\nTop {args.top} destination ports (by flow count):")
    for (proto, port), n in by_port.most_common(args.top):
        print(f"  {proto}/{port:<6} {n}")

    if args.publish:
        boto3.client("cloudwatch", region_name=m["region"]).put_metric_data(
            Namespace=NAMESPACE_NET,
            MetricData=[{"MetricName": "RejectedConnections",
                         "Value": rejected_total, "Unit": "Count"}])
        print(f"\nPublished RejectedConnections={rejected_total} to CloudWatch ({NAMESPACE_NET}).")


if __name__ == "__main__":
    main()
