# 05 — Deployment (simple, cheap, pilot-right)

## 1. Topology decision

**One EC2 instance, docker compose, Terraform-provisioned.** No Kubernetes, no ECS, no RDS for the pilot — Postgres runs in a container on a dedicated EBS volume with WAL archiving + nightly dumps to S3. Upgrade path to RDS is a connection-string change.

## 2. Exact AWS configuration

| Item | Spec | Why |
|---|---|---|
| Region | **ap-south-1 (Mumbai)** | Latency to Exotel/users, data residency |
| Instance | **t4g.xlarge (Graviton, 4 vCPU / 16 GB)** | Headroom for concurrent audio WS + STT streaming + Celery; ARM is ~20% cheaper. Fallback if any dependency lacks ARM wheels: t3.xlarge |
| Root vol | gp3 30 GB | OS + images |
| Data vol | gp3 100 GB, 3000 IOPS (separate EBS, `/data`) | Postgres + Redis AOF + Loki |
| Elastic IP | 1 | Stable DNS + Exotel/Meta webhook allowlisting |
| S3 | `opd-media` (audio, Rx PDFs; lifecycle: audio → delete 90d), `opd-backups` (WAL + nightly dumps, 35d) | |
| Networking | VPC, public subnet, SG: 80/443 world, 22 via SSM only (no open SSH) | |
| DNS/TLS | Route53 + Caddy auto-HTTPS | Zero cert ops |
| Monitoring | CloudWatch agent (CPU/mem/disk alarms → SNS→email/WhatsApp), Uptime Kuma container, Sentry SaaS | |
| Backup | EBS data-vol snapshot daily (DLM, 14d), pg_dump nightly → S3, WAL continuous | RPO ≤5 min |
| Secondary | AMI + `terraform apply` recreates box in ~10 min; docs runbook | DR is rebuild, not HA — acceptable because Downtime Protocol covers gaps |

## 3. docker-compose services

`caddy` (edge/TLS) · `api` (FastAPI/uvicorn, 4 workers) · `voice-gw` (audio WS, separate container so a telephony crash never takes down HTTP) · `web` (Next.js) · `worker` (Celery) · `beat` · `postgres:16` · `redis:7` · `loki+grafana` · `uptime-kuma`. All with healthchecks, `restart: unless-stopped`, resource limits, and a `make deploy` that does: git pull → build → `docker compose up -d --wait` → smoke tests.

## 4. Terraform layout

```
infra/
  main.tf         # vpc, sg, ec2 (t4g.xlarge), eip, ebs, iam role (s3+ssm+cloudwatch)
  s3.tf  route53.tf  dlm.tf  sns.tf
  user_data.sh    # docker install, mount /data, compose up
  variables.tf  outputs.tf   # single env=pilot; staging optional via workspace
```

Deploys via SSM (no SSH keys). GitHub Actions: on tag → build images → push to ECR → SSM run `make deploy`.

## 5. Cost model (monthly, ₹ approx @ ₹84/$)

| Item | USD | INR |
|---|---|---|
| t4g.xlarge (1-yr no-upfront reserved) | ~$77 | ₹6,500 |
| EBS 130 GB gp3 + snapshots | ~$16 | ₹1,350 |
| S3 + egress + Route53 + CloudWatch | ~$10 | ₹850 |
| **AWS total** | **~$103** | **~₹8,700** |
| Gemini Live (V1: ~40% of ~150 voice sessions/day × 4 min, audio in+out) | $90–180 | ₹8–15k |
| LLM pipeline (V2 turns + all summaries/routing/dictation/check-ins on Gemini Flash, OpenAI fallback; heavy context caching) | $60–140 | ₹5–12k |
| Sarvam/Google STT+TTS (V2 share of voice sessions) | $60–120 | ₹5–10k |
| Exotel (number + ~400 call-min/day) | — | ₹8–15k |
| WhatsApp Cloud API (service convos mostly free-window; ~3k template msgs/mo) | — | ₹2–4k |
| SMS (MSG91) | — | ₹1–2k |
| **All-in pilot run rate** | | **≈ ₹40–70k/month** |

Cost levers designed in: tier mix is a dial (V1 premium voice ↔ V2 cost-optimal pipeline ↔ V3 zero-AI), enforced live by the cost-guard; Flash + context caching for all non-realtime work; batch where latency allows; audio deleted at 90 days. The §11 dashboard makes cost-per-intake visible daily so the tier mix can be tuned on real numbers, not estimates.

## 6a. V-OSS local voice deployment → doc 09

The fully open-source local voice tier (doc 08) is **not** deployed on the AWS box
above — it runs on an on-prem GPU server. The self-sufficient runbook is
**[doc 09](09-VOSS-GPU-DEPLOYMENT.md)** (own-box / single-server, with an approved
same-LAN two-box GPU-node variant and an approval-gated app-in-AWS hybrid). Note
for S19: if the hybrid is ever approved, `voice-gw` becomes an on-prem *edge*
service co-located with the GPU box, not an EC2 container — the real-time loop must
stay off the WAN (doc 09 §2/§15).

## 6. Kiosk hardware note

Any 10–11" Android tab (₹15–20k) in a floor stand + USB thermal printer; Chrome kiosk mode pointed at the PWA. Queue board = TV + ₹3k Android stick. Budget ~₹60k per OPD floor.
