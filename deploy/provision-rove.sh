#!/usr/bin/env bash
# Provision the hosted runner host (rove). Idempotent; run as root:
#   ssh rove 'bash -s' < deploy/provision-rove.sh
set -euo pipefail

echo "== TiVM host provisioning =="
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg rsync ufw unattended-upgrades >/dev/null

if ! command -v docker >/dev/null 2>&1; then
  echo "-- installing docker (explicit repo packages; the convenience script wants packages focal lacks)"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    >/etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin >/dev/null
fi
systemctl enable --now docker >/dev/null 2>&1 || true

if ! docker compose version >/dev/null 2>&1; then
  echo "-- installing compose plugin"
  apt-get install -y -qq docker-compose-plugin >/dev/null
fi

if ! swapon --show 2>/dev/null | grep -q .; then
  echo "-- adding 4G swapfile"
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
sysctl -w vm.swappiness=10 >/dev/null
printf 'vm.swappiness=10\n' >/etc/sysctl.d/60-tivm.conf

mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ]; then
  echo "-- capping docker logs at 10m x3"
  printf '{\n  "log-driver": "json-file",\n  "log-opts": { "max-size": "10m", "max-file": "3" }\n}\n' \
    >/etc/docker/daemon.json
  systemctl restart docker
fi

echo "-- firewall: ssh only"
ufw allow OpenSSH >/dev/null
ufw allow 22/tcp >/dev/null
ufw --force enable >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null

echo "-- unattended security upgrades"
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
  >/etc/apt/apt.conf.d/20auto-upgrades
systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true

echo
echo "== summary =="
docker --version
docker compose version
swapon --show || true
ufw status | head -5
nproc
free -h | head -2
df -h / | tail -1
