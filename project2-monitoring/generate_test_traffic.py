#!/usr/bin/env python3
"""
generate_test_traffic.py - makes REJECTED connections appear in the flow logs so I
can test the analyzer and the alarm.

It tries to connect to ports 1-300 on MY OWN bastion's public IP. The security
group only allows port 22 from my IP, so almost every attempt is dropped and
recorded as REJECT. That looks exactly like a port scan.

!! Only ever run this against infrastructure you own. !!
"""
import socket
from concurrent.futures import ThreadPoolExecutor

from common import load_p1

target = load_p1()["bastion_public_ip"]
PORTS = range(1, 301)


def probe(port):
    try:
        socket.create_connection((target, port), timeout=0.5).close()
        return port, True
    except OSError:
        return port, False


print(f"Probing ports 1-300 on {target} (my own bastion)...")
with ThreadPoolExecutor(max_workers=50) as pool:       # 50 at a time so it finishes quickly
    results = list(pool.map(probe, PORTS))

open_ports = [p for p, ok in results if ok]
print(f"Done. Open: {open_ports}  |  blocked: {len(results) - len(open_ports)}")
print("Flow logs reach S3 after roughly 5-15 minutes. Then run: python analyze_flow_logs.py --publish")
