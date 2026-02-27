#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker build -t poc-demo-http:latest "$ROOT_DIR/challenges/demo-http"
echo "Built image: poc-demo-http:latest"
