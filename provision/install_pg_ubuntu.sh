#!/usr/bin/env bash
# Install PostgreSQL 16 + hypopg on Ubuntu 24.04 and tune it for measurement.
# Usage:  DB_NAME=idxlab DB_USER=idxlab DB_PASS=idxlab bash provision/install_pg_ubuntu.sh
set -euo pipefail

DB_NAME=${DB_NAME:-idxlab}
DB_USER=${DB_USER:-idxlab}
DB_PASS=${DB_PASS:-idxlab}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

echo "==> apt packages"
sudo apt update
sudo apt install -y postgresql-16 postgresql-16-hypopg postgresql-client-16 \
    python3-venv python3-pip wget unzip cpufrequtils || true

echo "==> measurement-stability tweaks"
echo never | sudo tee /sys/kernel/mm/transparent_hugepage/enabled >/dev/null 2>&1 || \
    echo "WARN: cannot disable THP (host-managed); record this in the report"
sudo sysctl -w vm.swappiness=1 >/dev/null || true
sudo sh -c 'echo performance | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor' >/dev/null 2>&1 || \
    echo "WARN: cannot pin CPU governor (host-managed); record this in the report"

echo "==> server configuration"
CONF_DIR=/etc/postgresql/16/main/conf.d
sudo mkdir -p "$CONF_DIR"
sudo cp "$HERE/postgresql.tuned.conf" "$CONF_DIR/10-idxlab.conf"
# keep all config in one place: listen on localhost only, lab DB not exposed
sudo sed -i "s/^#\?listen_addresses.*/listen_addresses = 'localhost'/" /etc/postgresql/16/main/postgresql.conf
sudo systemctl restart postgresql@16-main 2>/dev/null || sudo systemctl restart postgresql
sleep 2

echo "==> role and database"
sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
    "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}') THEN
        CREATE ROLE ${DB_USER} LOGIN SUPERUSER PASSWORD '${DB_PASS}'; END IF; END \$\$;"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 || \
    sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
sudo -u postgres psql -d "${DB_NAME}" -c "CREATE EXTENSION IF NOT EXISTS hypopg;"

echo "==> verify"
sudo -u postgres psql -d "${DB_NAME}" -c "SELECT extname, extversion FROM pg_extension WHERE extname='hypopg';"
sudo -u postgres psql -d "${DB_NAME}" -c "SHOW shared_buffers;" -c "SHOW max_parallel_workers_per_gather;" -c "SHOW jit;"
echo "done. connect with: psql -h 127.0.0.1 -p 5432 -U ${DB_USER} -d ${DB_NAME}"
