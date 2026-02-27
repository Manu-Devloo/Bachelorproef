# CTFd Container Orchestrator PoC

This PoC implements a self-hosted control plane for per-user CTF challenge containers.
It is designed for your bachelorproef scope: dynamic lifecycle management, baseline container hardening, resource limits, and timeout-based cleanup.

## What is included

- Flask API for challenge registration and instance lifecycle.
- Registry-first flow: add container templates, then pick one to start for a team.
- Event logs persisted in SQLite and exposed through `/api/logs`.
- Docker backend with per-instance network creation.
- Automatic timeout reaper thread.
- Baseline hardening at container launch:
  - `read_only` root filesystem
  - `cap_drop=['ALL']`
  - `security_opt=['no-new-privileges:true']`
  - `pids_limit`, memory, and CPU limits
- Browser dashboard (`/`) for operators.
- Demo challenge image (`poc-demo-http:latest`).
- Unit/API tests and a smoke test script.
- Research and experiment notes in Markdown.

## Quick start

Prerequisites:

- Docker Desktop / Docker Engine running
- Docker Compose v2+
- Python 3.12+ (for local tests)

Run end-to-end smoke test (build + start + register + start + validate + stop):

```bash
cd POC
./scripts/smoke_test.sh
```

Open dashboard:

- [http://127.0.0.1:8000](http://127.0.0.1:8000)

Stop stack:

```bash
docker compose -f docker-compose.yml down -v
```

Force cleanup all managed runtime artifacts (including leftover challenge containers):

```bash
./scripts/cleanup_runtime.sh
```

## API summary

- `GET /healthz`
- `GET /api/challenges`
- `POST /api/challenges`
- `GET /api/registry` (alias of challenge registry)
- `POST /api/registry` (alias for registry insert/update)
- `GET /api/instances`
- `POST /api/instances/start`
- `POST /api/registry/<registry_id>/start`
- `POST /api/instances/<instance_id>/stop`
- `POST /api/instances/stop-all`
- `POST /api/reaper/run`
- `GET /api/logs`
- `POST /api/logs`
- `DELETE /api/logs`

## Test commands

```bash
cd POC
app/.venv/bin/pytest -q tests
```

```bash
cd POC
app/.venv/bin/locust -f tests/locustfile.py --host=http://127.0.0.1:8000 --headless -u 10 -r 5 -t 10s
```

## Folder layout

- `app/`: API, backend logic, dashboard UI, Dockerfile
- `challenges/demo-http/`: demo challenge container image
- `scripts/`: helper scripts (build image, smoke test, local mock run)
- `tests/`: pytest and Locust files
- `docs/`: research notes, experiments, and validation logs
- `docs/06_bp-alignment.md`: alignment check against BP PoC chapter expectations

## Known limitations

- No direct CTFd plugin integration yet; this PoC exposes a service API that can be called from a CTFd plugin.
- Uses Docker socket mount in the PoC stack; acceptable for prototype, but production should isolate this with stricter host controls.
- Load tests currently hit `max_instances` quickly unless that value is increased per challenge.
