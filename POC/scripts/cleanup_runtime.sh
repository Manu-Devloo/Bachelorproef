#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Stop orchestrator stack if present.
docker compose -f "$ROOT_DIR/docker-compose.yml" down -v >/dev/null 2>&1 || true

# Remove any managed challenge containers/networks that may outlive compose.
while IFS= read -r container_id; do
  [[ -n "$container_id" ]] && docker rm -f "$container_id" >/dev/null 2>&1 || true
done < <(docker ps -aq --filter "label=poc.managed=true")

while IFS= read -r network_id; do
  [[ -n "$network_id" ]] && docker network rm "$network_id" >/dev/null 2>&1 || true
done < <(docker network ls -q --filter "label=poc.managed=true")

echo "PoC runtime cleanup complete."
