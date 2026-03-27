#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

compose_cmd=(docker compose -f docker-compose.yml -f docker-compose.split.yml)
compose_test_cmd=(docker compose --profile test -f docker-compose.yml -f docker-compose.split.yml)
artifacts_dir=".artifacts"
archive_path="$artifacts_dir/poc-demo-http.tar"

cleanup() {
  "${compose_cmd[@]}" down -v >/dev/null 2>&1 || true
  rm -f "$archive_path"
}

cleanup
trap cleanup EXIT

mkdir -p "$artifacts_dir"
docker build -t poc-demo-http:latest ../POC/challenges/demo-http
docker save -o "$archive_path" poc-demo-http:latest

"${compose_cmd[@]}" up -d --build ctfd docker-runtime
"${compose_test_cmd[@]}" run --rm smoke
