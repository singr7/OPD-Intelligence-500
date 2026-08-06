#!/usr/bin/env bash
# EC2 first-boot installs host dependencies and creates stable directories.
# It never receives application secrets or clones an unpinned branch.
set -euxo pipefail

# --- Docker + compose plugin ---
apt-get update
apt-get install -y ca-certificates curl gnupg unzip nginx certbot python3-certbot-nginx awscli
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
# shellcheck disable=SC1091
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker
systemctl enable nginx

# CloudWatch's Ubuntu package is distributed directly by AWS for both target
# architectures rather than through Ubuntu's apt repositories.
case "$(dpkg --print-architecture)" in
  arm64) CW_ARCH=arm64 ;;
  amd64) CW_ARCH=amd64 ;;
  *) echo "unsupported CloudWatch agent architecture" >&2; exit 1 ;;
esac
curl -fsSLo /tmp/amazon-cloudwatch-agent.deb \
  "https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/${CW_ARCH}/latest/amazon-cloudwatch-agent.deb"
dpkg -i /tmp/amazon-cloudwatch-agent.deb
rm -f /tmp/amazon-cloudwatch-agent.deb

# --- Mount the dedicated data volume at /data ---
for _ in $(seq 1 60); do
  DATA_DEV="$(lsblk -dpno NAME,TYPE,MOUNTPOINT | awk '$2 == "disk" && $3 == "" {print $1; exit}')"
  test -n "$DATA_DEV" && break
  sleep 2
done
test -b "$DATA_DEV"
if ! blkid "$DATA_DEV"; then
  mkfs -t ext4 "$DATA_DEV"
fi
mkdir -p /data
DATA_UUID="$(blkid -s UUID -o value "$DATA_DEV")"
grep -q "UUID=$DATA_UUID " /etc/fstab || echo "UUID=$DATA_UUID /data ext4 defaults,nofail 0 2" >> /etc/fstab
mount -a

# --- Stable runtime layout ----------------------------------------------------
install -d -m 0750 -o root -g docker /opt/opd /opt/opd/releases /opt/opd/runtime
# /data/records holds scanned page images (MRD, doc 22 §1). It is bind-mounted
# into every backend container because the api writes pages the worker reads,
# and it is deliberately outside the Postgres backup — see doc 22 §2.
install -d -m 0750 -o root -g docker /data/postgres /data/redis /data/records /data/releases /data/backups
install -d -m 0755 /var/log/opd

cat >/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<'JSON'
{
  "agent": {"metrics_collection_interval": 60, "run_as_user": "root"},
  "metrics": {
    "append_dimensions": {"InstanceId": "${aws:InstanceId}"},
    "metrics_collected": {
      "disk": {
        "measurement": ["used_percent"],
        "metrics_collection_interval": 60,
        "resources": ["/data"]
      },
      "mem": {"measurement": ["mem_used_percent"], "metrics_collection_interval": 60}
    }
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/nginx/access.log",
            "log_group_name": "/opd/OPD_ENVIRONMENT/nginx-access",
            "log_stream_name": "{instance_id}"
          },
          {
            "file_path": "/var/log/nginx/error.log",
            "log_group_name": "/opd/OPD_ENVIRONMENT/nginx-error",
            "log_stream_name": "{instance_id}"
          },
          {
            "file_path": "/var/lib/docker/containers/*/*.log",
            "log_group_name": "/opd/OPD_ENVIRONMENT/application",
            "log_stream_name": "{instance_id}"
          },
          {
            "file_path": "/var/log/opd/backup.log",
            "log_group_name": "/opd/OPD_ENVIRONMENT/backup",
            "log_stream_name": "{instance_id}"
          }
        ]
      }
    }
  }
}
JSON
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json -s

echo "user_data complete: docker/nginx ready; /data mounted; no application secret loaded" \
  >/var/log/opd-bootstrap.log
