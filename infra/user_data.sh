#!/usr/bin/env bash
# EC2 first-boot: install docker, mount the /data EBS volume, bring the stack up.
# Full deploy pipeline (ECR pull, make deploy over SSM) lands in S19.
set -euxo pipefail

# --- Docker + compose plugin ---
apt-get update
apt-get install -y ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker

# --- Mount the dedicated data volume at /data ---
DATA_DEV=/dev/xvdf
if ! blkid "$DATA_DEV"; then
  mkfs -t ext4 "$DATA_DEV"
fi
mkdir -p /data
grep -q "$DATA_DEV" /etc/fstab || echo "$DATA_DEV /data ext4 defaults,nofail 0 2" >> /etc/fstab
mount -a

# --- App bootstrap (repo + compose) is completed by the S19 deploy pipeline ---
echo "user_data complete: docker up, /data mounted" > /var/log/opd-bootstrap.log
