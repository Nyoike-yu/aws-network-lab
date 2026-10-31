#!/usr/bin/env python3
"""
push_metrics.py - runs ON the Linux server (via cron, every minute).

EC2 reports CPU to CloudWatch by default, but NOT memory or disk usage - the
hypervisor can't see inside the OS. So we measure them ourselves from /proc
and shutil, then push them as CUSTOM metrics.

No access keys anywhere: boto3 automatically picks up temporary credentials
from the instance's IAM role (via the metadata service).
"""
import shutil
import time
import urllib.request

import boto3

NAMESPACE = "LabServer"


def imds(path):
    """Read instance metadata using IMDSv2 (token first, then the request)."""
    req = urllib.request.Request(
        "http://169.254.169.254/latest/api/token", method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
    token = urllib.request.urlopen(req, timeout=3).read().decode()
    req = urllib.request.Request(
        f"http://169.254.169.254/latest/meta-data/{path}",
        headers={"X-aws-ec2-metadata-token": token})
    return urllib.request.urlopen(req, timeout=3).read().decode()


def cpu_percent(interval=1.0):
    """CPU busy %. /proc/stat holds cumulative CPU 'ticks' since boot, so I read it
    twice and compare: busy% = 100 * (1 - idle_delta / total_delta)."""
    def read():
        with open("/proc/stat") as f:
            fields = [float(x) for x in f.readline().split()[1:]]   # first line = all CPUs
        idle = fields[3] + fields[4]                                # idle + iowait
        return idle, sum(fields)

    idle1, total1 = read()
    time.sleep(interval)
    idle2, total2 = read()
    d_total, d_idle = total2 - total1, idle2 - idle1
    return 100.0 * (1 - d_idle / d_total) if d_total else 0.0


def memory_percent():
    """Memory used %. 'MemAvailable' is the kernel's estimate of memory that can be
    handed out (includes reclaimable cache), so it's more honest than 'MemFree'."""
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, val = line.split(":")
            info[key] = int(val.split()[0])                         # values are in kB
    return 100.0 * (1 - info["MemAvailable"] / info["MemTotal"])


def disk_percent(path="/"):
    """Disk used % of the root filesystem, computed like `df` does."""
    u = shutil.disk_usage(path)
    return 100.0 * u.used / (u.used + u.free)


def main():
    instance_id = imds("instance-id")
    region = imds("placement/region")
    dims = [{"Name": "InstanceId", "Value": instance_id}]

    metrics = {
        "CpuUsedPercent": cpu_percent(),
        "MemoryUsedPercent": memory_percent(),
        "DiskUsedPercent": disk_percent("/"),
    }
    cw = boto3.client("cloudwatch", region_name=region)
    cw.put_metric_data(Namespace=NAMESPACE, MetricData=[
        {"MetricName": name, "Dimensions": dims, "Value": value, "Unit": "Percent"}
        for name, value in metrics.items()
    ])
    print(time.strftime("%F %T"), {k: round(v, 1) for k, v in metrics.items()})


if __name__ == "__main__":
    main()
