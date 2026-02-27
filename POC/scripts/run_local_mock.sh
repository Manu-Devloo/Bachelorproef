#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/app"

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export POC_BACKEND=mock
export POC_DB_PATH="$ROOT_DIR/mock.db"
export POC_BIND_HOST=127.0.0.1
export POC_BIND_PORT=8000
export POC_PUBLIC_HOST=127.0.0.1

python -m poc_orchestrator.web
