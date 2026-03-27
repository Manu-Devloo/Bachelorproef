#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

compose_cmd=(docker compose -f docker-compose.yml -f docker-compose.local.yml)

cleanup() {
  "${compose_cmd[@]}" down -v >/dev/null 2>&1 || true
  containers="$(docker ps -aq --filter "label=ctfd.plugin=ctfd_container_challenges")"
  if [ -n "$containers" ]; then
    docker rm -f $containers >/dev/null 2>&1 || true
  fi
  networks="$(docker network ls -q --filter "label=ctfd.plugin=ctfd_container_challenges")"
  if [ -n "$networks" ]; then
    docker network rm $networks >/dev/null 2>&1 || true
  fi
}

cleanup
trap cleanup EXIT

docker build -t poc-demo-http:latest ../POC/challenges/demo-http
archive_base="$(mktemp /tmp/poc-demo-http.XXXXXX)"
archive_path="${archive_base}.tar"
docker save -o "$archive_path" poc-demo-http:latest
"${compose_cmd[@]}" up -d --build
IMAGE_ARCHIVE_PATH="$archive_path" ./scripts/smoke_test.py
rm -f "$archive_path"
rm -f "$archive_base"
