**AWS Network & Observability Lab: Automated VPC Infrastructure and Telemetry with Python (boto3)**


Overview & Motivation
Cloud tutorials often teach AWS services in isolation—clicking through the console to spin up an EC2 instance one day, or reading about CIDR blocks the next. I built this repository to bridge the gap between theoretical networking, Linux systems administration, and cloud automation by engineering one cohesive, end-to-end infrastructure lab.   
README.md
+ 1

Instead of relying on manual console clicks ("ClickOps") or jumping straight into high-level abstractions like Terraform before understanding the underlying API calls, I chose to automate the entire lifecycle using Python and the AWS SDK (boto3). Writing imperative provisioning and teardown scripts forced me to confront how AWS resources actually depend on one another, how state is tracked, and how network packets traverse security boundaries in real time.   
README.md

**System Architecture**
This lab is split into two interconnected phases where the observability layer (Project 2) sits directly on top of the hardened network foundation (Project 1):   
README.md

Plaintext
                        [ Internet ]
                             |
                   [ Internet Gateway ]
                             |
        +--------------------+--------------------+
        |              VPC (Custom CIDR)          |
        |                                         |
        |  +-----------------------------------+  |
        |  |           Public Subnet           |  |
        |  |                                   |  |
        |  |  [ Bastion Host ]   [ NAT GW ]    |  |
        |  |  (SSH Hardened /       |          |  |
        |  |   UFW Enabled)         |          |  |
        |  +------------------------|----------+  |
        |                           |             |
        |  +------------------------v----------+  |
        |  |          Private Subnet           |  |
        |  |                                   |  |
        |  |     [ Private Linux Server ]      |  |
        |  |     (Custom /proc Telemetry)      |  |
        |  +-----------------------------------+  |
        +--------------------|--------------------+
                             |
         +-------------------+-------------------+
         |                                       |
         v                                       v
[ VPC Flow Logs ]                    [ Custom CloudWatch Agent ]
(Parsed via Python)                  (Cron + /proc Metrics)
         |                                       |
         +-------------------+-------------------+
                             |
                             v
                 [ Amazon CloudWatch ]
                  (Metrics & Alarms)
                             |
                             v
                    [ Amazon SNS ]
                 (Email Alerting Action)

**Repository Breakdown**
Module	Core Focus	AWS & Linux Concepts Practiced
project1-secure-vpc	Network Foundation & Compute Hardening	
Custom VPC design, CIDR subnetting, public/private route tables, Internet & NAT Gateways, chained Security Groups, Bastion jump-host architecture, OS-level SSH hardening, ufw host firewall rules, and automated infrastructure verification. 
README.md

project2-monitoring	Telemetry, Log Analysis & Alerting	
VPC Flow Logs, Python log parsing, Linux /proc filesystem telemetry, cron scheduling, IAM Instance Profiles (least privilege), custom CloudWatch metric ingestion, CloudWatch Alarms, and SNS topic notifications. 
README.md

**Engineering & Learning Journey: What This Lab Entails**
1. Programmatic Infrastructure & State Management (boto3)
Automating AWS via Python revealed everything the AWS Management Console hides behind a single "Launch" button:

Resource Dependency Ordering: Provisioning a network programmatically requires strict sequencing. You cannot attach an Internet Gateway before the VPC exists, nor can you create a Private Route Table route pointing to a NAT Gateway before that NAT Gateway transitions from pending to available. I had to implement waiter patterns and polling logic in Python to handle asynchronous AWS resource creation.

Lightweight State Tracking: Because boto3 is an imperative SDK rather than a declarative IaC tool, re-running or tearing down resources requires knowing exact resource IDs (vpc-xxx, subnet-xxx, igw-xxx, alloc-xxx). I designed the deployment scripts to serialize created resource identifiers into local state.json and monitoring_state.json artifacts.   
README.md

Dependency-Safe Teardown: Tearing down cloud infrastructure is often harder than building it. Deleting a VPC fails with a DependencyViolation if an Elastic IP is still associated with a NAT Gateway, or if network interfaces (ENIs) haven't fully detached after instance termination. Writing teardown.py taught me how to reverse the dependency graph cleanly to guarantee zero orphaned billable resources.   
README.md

2. Defense-in-Depth Networking (project1-secure-vpc)
Rather than putting instances in a default VPC with public IPs, I built a segmented network from scratch:   
README.md

Routing & Asymmetric Boundaries: Configured explicit route tables so the public subnet routes 0.0.0.0/0 to an Internet Gateway (igw), while the private subnet routes outbound traffic through a public NAT Gateway—allowing the private server to pull OS updates and talk to AWS APIs without ever being directly reachable from the public internet.   
README.md

Layered Access Control: Applied security at both the AWS perimeter and the Linux OS level. At the AWS layer, the private instance's Security Group only permits inbound SSH from the Bastion Host's Security Group ID (security group chaining). At the OS layer, I configured ufw (Uncomplicated Firewall) and hardened sshd_config to enforce key-based authentication and restrict root login.   
README.md
+ 1

Self-Testing Infrastructure (verify.py): Instead of assuming my security rules worked, I wrote an automated verification script (verify.py) that actively tests the topology—confirming that the Bastion is reachable, the private host can reach the internet via NAT, and unauthorized ports/direct public access are strictly rejected.   
README.md

3. Linux Internals, Least-Privilege IAM & Observability (project2-monitoring)
In Project 2, I wanted to understand how monitoring agents actually work under the hood rather than just installing a pre-packaged black-box agent:   
README.md

Scraping the Linux /proc Pseudo-Filesystem: I wrote a custom Python monitoring agent deployed to the private Linux server that reads raw kernel and system statistics directly from /proc (such as /proc/meminfo, /proc/loadavg, and /proc/stat). Scheduled via cron, the script parses these system files and pushes custom metric dimensions directly to the CloudWatch API (put_metric_data).   
README.md
+ 1

IAM Roles & Temporary Credentials: Hardcoding AWS access keys onto an EC2 instance is a major security anti-pattern. I configured an IAM Role and Instance Profile scoped with least-privilege permissions (cloudwatch:PutMetricData and log publishing). This taught me how boto3 automatically queries the EC2 Instance Metadata Service (IMDS) to fetch temporary, rotating STS credentials seamlessly.   
README.md

VPC Flow Log Analysis & Automated Alerting: By generating synthetic allowed and rejected traffic (generate_test_traffic.py), I captured network-level telemetry via VPC Flow Logs. My analyze_flow_logs.py script ingests and parses raw flow log records—identifying rejected packet spikes and port scans—and publishes aggregated findings to CloudWatch, triggering CloudWatch Alarms wired to an Amazon SNS topic for real-time email alerts.   
README.md
+ 1

Troubleshooting & Key Takeaways
WSL File Permissions & SSH Keys: When running this lab from Windows Subsystem for Linux (WSL Ubuntu), cloning the repository into the mounted Windows filesystem (/mnt/c/...) causes OpenSSH to reject the .pem private key because Windows NTFS metadata leaves permissions too open (0777), and chmod 400 silently fails without custom mount flags. Fix: Always clone and execute the lab inside the native Linux ext4 filesystem (~/) where POSIX permissions apply properly.   
README.md
+ 1

VPC Flow Log Propagation Delays: Initially, running analyze_flow_logs.py immediately after generating test traffic returned empty datasets. I learned that VPC Flow Logs capture traffic in aggregation intervals and take roughly 5 to 10 minutes to flush and index into CloudWatch Logs.   
README.md
+ 1

FinOps & Cost Discipline: While VPCs, subnets, and security groups are free, NAT Gateways charge an hourly base rate (~$0.045/hour or ~$1.08/day) plus per-GB data processing fees immediately upon provisioning—even inside a Free Tier account. Building deterministic teardown scripts (teardown_monitoring.py and teardown.py) and configuring an AWS Budgets billing alarm before writing a single line of code kept my lab costs near zero.   
README.md
+ 1

**Prerequisites & Environment Setup**
AWS Account & Security:

Create an AWS Budget billing alarm first (Billing and Cost Management > Budgets, set to $5.00).   
README.md

Provision a dedicated IAM User (never use the root account) with programmatic access, and configure local credentials via aws configure.   
README.md

Local Environment:

OS/Shell: Linux, macOS, or WSL2 (Ubuntu) on Windows. (Note: On WSL, ensure the repo is cloned inside your Linux home directory ~, not /mnt/c/..., so chmod 400 works on your .pem keys).   
README.md

Runtime: Python 3.9+ with boto3 installed.   
README.md

**Execution Guide (Order of Operations)**
Bash
# 1. Deploy and verify the hardened VPC & EC2 foundation
cd project1-secure-vpc
pip install -r requirements.txt
python deploy.py                 # Provisions VPC, subnets, NAT GW, Bastion & Private EC2 (~5 min)
python verify.py                 # Runs automated connectivity & security boundary assertions

# 2. Layer on VPC Flow Logs, custom /proc metrics, and SNS alerting
cd ../project2-monitoring
python setup_monitoring.py --email you@example.com   # Creates IAM roles, Flow Logs, Alarms & SNS
python install_agent.py                              # Deploys /proc telemetry script & cron job
python generate_test_traffic.py                      # Simulates accepted & rejected network traffic

# Wait ~10 minutes for VPC Flow Log aggregation intervals to flush to CloudWatch
python analyze_flow_logs.py --publish                # Parses Flow Logs & publishes custom metrics

# 3. Teardown (CRITICAL: Always run in reverse order to avoid dependency locks & NAT charges!)
python teardown_monitoring.py
cd ../project1-secure-vpc && python teardown.py
Security & Git Hygiene
The following runtime artifacts contain sensitive keys or environment-specific resource IDs and are strictly excluded via .gitignore:   
README.md

*.pem — Generated EC2 private SSH keys.   
README.md

state.json — Tracked VPC/EC2 resource IDs for Project 1.   
README.md

monitoring_state.json — Tracked IAM/CloudWatch/SNS ARNs for Project 2.   
README.md

Whenever you're ready, paste or upload your resume (and let me know if you're targeting the Cloud Support Associate Intern, Solutions Architect Intern, or SDE Intern track at AWS), and we will tailor every section to match what AWS recruiters and hiring managers look for!