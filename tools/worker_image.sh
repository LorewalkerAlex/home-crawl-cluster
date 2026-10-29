#!/usr/bin/env bash
# Independent user-run script. Never source into a login shell.
set -eu
repo_path="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
action="${1:-check}"
if [ "$#" -gt 0 ]; then shift; fi
case "$action" in check|build|start|rollback) ;; *) echo 'Use check, build, start or rollback' >&2; exit 2 ;; esac
base=/srv/apps/crawlab/compose.yaml
lan=/srv/apps/crawlab/compose.override.yaml
test -f "$base"
test -f "$lan"
if [ "$action" = rollback ]; then
  docker compose -f "$base" -f "$lan" --profile worker up -d --no-deps --wait --wait-timeout 180 worker
  exit 0
fi
env_file=/srv/apps/proxypool/compose.env
test -f "$env_file"
docker_args=(compose --env-file "$env_file" -f "$base" -f "$lan" -f "$repo_path/deploy/proxypool/worker.override.yaml" --profile worker)
docker "${docker_args[@]}" config -q
case "$action" in
  check) echo 'Worker merge syntax accepted by the server Compose version' ;;
  build) docker "${docker_args[@]}" build "$@" worker ;;
  start) docker "${docker_args[@]}" up -d --no-deps --no-build --wait --wait-timeout 180 worker ;;
esac
