#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_BASE="${API_BASE:-http://127.0.0.1:8000}"

cleanup() {
  # Best-effort cleanup to avoid orphaned challenge containers when the smoke
  # test exits early.
  if curl -sf "$API_BASE/healthz" >/dev/null 2>&1; then
    curl -sS -X POST "$API_BASE/api/instances/stop-all" \
      -H 'Content-Type: application/json' \
      -d '{"reason":"smoke-cleanup"}' >/dev/null 2>&1 || true
  fi

  while IFS= read -r container_id; do
    [[ -n "$container_id" ]] && docker rm -f "$container_id" >/dev/null 2>&1 || true
  done < <(docker ps -aq --filter "label=poc.managed=true")

  while IFS= read -r network_id; do
    [[ -n "$network_id" ]] && docker network rm "$network_id" >/dev/null 2>&1 || true
  done < <(docker network ls -q --filter "label=poc.managed=true")
}

trap cleanup EXIT

"$ROOT_DIR/scripts/build_demo_image.sh"

docker compose -f "$ROOT_DIR/docker-compose.yml" up -d --build

for _ in {1..30}; do
  if curl -sf "$API_BASE/healthz" >/dev/null; then
    break
  fi
  sleep 1
done

echo "Registering challenge..."
curl -sS -X POST "$API_BASE/api/challenges" \
  -H 'Content-Type: application/json' \
  -d '{
    "challenge_id":"demo-http",
    "name":"Demo HTTP",
    "image":"poc-demo-http:latest",
    "container_port":8080,
    "cpu_limit":0.5,
    "memory_limit_mb":256,
    "timeout_seconds":120,
    "max_instances":20
  }' >/dev/null

echo "Starting instance for team-a..."
START_A="$(curl -sS -X POST "$API_BASE/api/instances/start" \
  -H 'Content-Type: application/json' \
  -d '{"challenge_id":"demo-http","user_id":"team-a"}')"
if ! printf '%s' "$START_A" | python3 -c 'import json,sys; j=json.load(sys.stdin); assert "instance" in j' >/dev/null 2>&1; then
  echo "team-a start failed: $START_A"
  exit 1
fi

INSTANCE_A="$(printf '%s' "$START_A" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance"]["instance_id"])')"
URL_A="$(printf '%s' "$START_A" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance"]["access_url"])')"

echo "Starting instance for team-b..."
START_B="$(curl -sS -X POST "$API_BASE/api/instances/start" \
  -H 'Content-Type: application/json' \
  -d '{"challenge_id":"demo-http","user_id":"team-b"}')"
if ! printf '%s' "$START_B" | python3 -c 'import json,sys; j=json.load(sys.stdin); assert "instance" in j' >/dev/null 2>&1; then
  echo "team-b start failed: $START_B"
  exit 1
fi
INSTANCE_B="$(printf '%s' "$START_B" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance"]["instance_id"])')"
URL_B="$(printf '%s' "$START_B" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance"]["access_url"])')"

echo "Checking challenge endpoints..."
for url in "$URL_A" "$URL_B"; do
  ok=0
  for _ in {1..10}; do
    if curl -fs "$url" >/dev/null 2>/dev/null; then
      ok=1
      break
    fi
    sleep 1
  done
  if [[ "$ok" -ne 1 ]]; then
    echo "challenge endpoint did not become reachable: $url"
    exit 1
  fi
done

echo "Stopping team-a instance..."
curl -sS -X POST "$API_BASE/api/instances/$INSTANCE_A/stop" \
  -H 'Content-Type: application/json' \
  -d '{"reason":"manual"}' >/dev/null

echo "Stopping team-b instance..."
curl -sS -X POST "$API_BASE/api/instances/$INSTANCE_B/stop" \
  -H 'Content-Type: application/json' \
  -d '{"reason":"manual"}' >/dev/null

echo "Listing instances..."
curl -sS "$API_BASE/api/instances" | python3 -m json.tool

echo "Smoke test completed successfully."
