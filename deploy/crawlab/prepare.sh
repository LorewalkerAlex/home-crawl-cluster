#!/usr/bin/env bash
# User executes this on the server. This script never pulls or starts containers.
set -euo pipefail
umask 077

if [[ ${EUID} -ne 0 ]]; then
  printf '%s\n' 'Run with sudo bash prepare.sh' >&2
  exit 1
fi

bundle_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
app_dir=/srv/apps/crawlab
data_dir=/srv/persistent/data/crawlab
secret_dir=/srv/persistent/secrets/crawlab

for dependency in docker openssl install stat ss grep; do
  command -v "$dependency" >/dev/null || { printf 'Missing command: %s\n' "$dependency" >&2; exit 1; }
done
docker compose version >/dev/null
docker info --format '{{.ServerVersion}}' >/dev/null
if ss -H -ltn 'sport = :13003' | grep -q .; then
  printf '%s\n' 'Port 13003 is already listening; stopped without creating files.' >&2
  exit 1
fi
for required_file in compose.yaml mongo-init.js README.md; do
  [[ -f "$bundle_dir/$required_file" ]] || { printf 'Missing bundle file: %s\n' "$required_file" >&2; exit 1; }
done
for destination in "$app_dir" "$data_dir" "$secret_dir"; do
  if [[ -e "$destination" || -L "$destination" ]]; then
    printf 'Already exists; stopped without overwriting: %s\n' "$destination" >&2
    exit 1
  fi
done
if docker ps -a --format '{{.Names}}' | grep -Eq '^crawlab-(master|worker|mongo)-'; then
  printf '%s\n' 'Existing Crawlab containers found; inspect before preparing.' >&2
  exit 1
fi
if docker network ls --format '{{.Name}}' | grep -qx 'crawlab_default'; then
  printf '%s\n' 'Existing crawlab_default network found; inspect before preparing.' >&2
  exit 1
fi

install -d -m 0755 "$app_dir"
install -d -m 0700 "$data_dir" "$secret_dir"
for relative in \
  master/node master/data master/spiders master/workspace master/repo \
  worker/node worker/data worker/workspace worker/spiders worker/repo mongo/db mongo/configdb; do
  install -d -m 0700 "$data_dir/$relative"
done
install -m 0644 "$bundle_dir/compose.yaml" "$app_dir/compose.yaml"
install -m 0644 "$bundle_dir/mongo-init.js" "$app_dir/mongo-init.js"
install -m 0644 "$bundle_dir/README.md" "$app_dir/README.md"

db_password=$(openssl rand -hex 24)
root_password=$(openssl rand -hex 24)
node_auth=$(openssl rand -hex 32)
filer_auth=$(openssl rand -hex 32)
master_key=$(cat /proc/sys/kernel/random/uuid)
worker_key=$(cat /proc/sys/kernel/random/uuid)

cat >"$secret_dir/app.env" <<EOF
TZ=Asia/Shanghai
CRAWLAB_MONGO_HOST=mongo
CRAWLAB_MONGO_PORT=27017
CRAWLAB_MONGO_DB=crawlab
CRAWLAB_MONGO_USERNAME=crawlab
CRAWLAB_MONGO_PASSWORD=$db_password
CRAWLAB_MONGO_AUTHSOURCE=crawlab
CRAWLAB_GRPC_AUTHKEY=$node_auth
CRAWLAB_FS_FILER_AUTHKEY=$filer_auth
EOF

cat >"$secret_dir/mongo.env" <<EOF
TZ=Asia/Shanghai
MONGO_INITDB_ROOT_USERNAME=root
MONGO_INITDB_ROOT_PASSWORD=$root_password
MONGO_INITDB_DATABASE=crawlab
CRAWLAB_MONGO_USERNAME=crawlab
CRAWLAB_MONGO_PASSWORD=$db_password
EOF

# 0.6.3 persists the node auth key and concurrency in config.json.
# Explicit initialization avoids relying on legacy constructor defaults.
printf '{"key":"%s","name":"home-master","is_master":true,"auth_key":"%s","max_runners":1}\n' \
  "$master_key" "$node_auth" >"$data_dir/master/node/config.json"
printf '{"key":"%s","name":"home-worker-01","is_master":false,"auth_key":"%s","max_runners":1}\n' \
  "$worker_key" "$node_auth" >"$data_dir/worker/node/config.json"
chmod 0600 "$secret_dir/app.env" "$secret_dir/mongo.env" \
  "$data_dir/master/node/config.json" "$data_dir/worker/node/config.json"

cat >"$app_dir/.env" <<'EOF'
CRAWLAB_HTTP_PORT=13003
MASTER_CPUS=1.5
MASTER_MEMORY=2g
WORKER_CPUS=1.0
WORKER_MEMORY=1g
MONGO_CPUS=1.0
MONGO_MEMORY=1g
EOF
chmod 0644 "$app_dir/.env"

cd -- "$app_dir"
docker compose --profile worker config -q
printf '%s\n' 'Prepared and Compose syntax checked. No images pulled or containers started.'
printf '%s\n' 'Secrets generated locally; do not paste env files or node config.json.'
printf '%s\n' 'Next: cd /srv/apps/crawlab && sudo docker compose --profile worker pull'
