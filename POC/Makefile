SHELL := /bin/bash
ROOT := $(shell pwd)

.PHONY: build-demo up down clean-runtime smoke test test-mock load

build-demo:
	./scripts/build_demo_image.sh

up:
	docker compose -f docker-compose.yml up -d --build

down:
	docker compose -f docker-compose.yml down

clean-runtime:
	./scripts/cleanup_runtime.sh

smoke:
	./scripts/smoke_test.sh

test:
	cd app && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt && cd .. && PYTHONPATH=app app/.venv/bin/pytest -q tests

load:
	cd app && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt && cd .. && app/.venv/bin/locust -f tests/locustfile.py --host=http://127.0.0.1:8000 --headless -u 20 -r 5 -t 30s
