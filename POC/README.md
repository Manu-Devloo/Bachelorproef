# CTFd Container Orchestrator PoC

This PoC implements a self-hosted control plane for per-user CTF challenge containers.
It is designed for your bachelorproef scope: dynamic lifecycle management, baseline container hardening, resource limits, and timeout-based cleanup.

## What is included

- Flask API for challenge registration and instance lifecycle.
- Registry-first flow: add container templates, then pick one to start for a team.
- Optional startup bootstrap for demo/showcase registry entries via `POC_BOOTSTRAP_CHALLENGES`.
- Event logs persisted in SQLite and exposed through `/api/logs`.
- Docker backend with per-instance network creation.
- Automatic timeout reaper thread.
- Serialized start handling so duplicate concurrent starts for the same team/challenge
  reuse the same running instance and in-process `max_instances` checks stay consistent.
- Baseline hardening at container launch:
  - `read_only` root filesystem
  - `cap_drop=['ALL']`
  - `security_opt=['no-new-privileges:true']`
  - `pids_limit`, memory, and CPU limits
- Browser dashboard (`/`) for operators.
- Demo challenge image (`poc-demo-http:latest`).
- Unit/API tests and a smoke test script.
- Research and experiment notes in Markdown.

## Core components at a glance

| Component | Responsibility | Why it matters in this PoC |
| --- | --- | --- |
| `web.py` dashboard + API | Exposes the operator UI and lifecycle endpoints | Keeps challenge registration, start, stop, and status inspection in one entrypoint |
| `service.py` | Applies lifecycle rules, idempotency, quotas, and timeout logic | Central place where the PoC proves policy enforcement works |
| `backend.py` | Talks to Docker through the Python SDK | Validates that challenge runtime management can stay in Python instead of shelling out |
| `storage.py` | Persists challenges, instances, and event history in SQLite | Makes cleanup, auditing, and UI state reproducible across restarts |
| `reaper.py` | Expires timed-out instances in the background | Demonstrates that stale containers can be removed automatically |
| `challenges/demo-http/` | Demo workload used by the smoke test | Provides a repeatable target for end-to-end validation |

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

Showcase flow:

- `docker compose -f docker-compose.yml up -d --build`
- Open the dashboard and use the pre-registered `demo-http` container from the registry dropdown.
- Start it for a team such as `team-01`.

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

Notable response behavior:

- Duplicate start for the same `challenge_id + user_id` returns `200` with `created: false`.
- Capacity exhaustion returns `409`.
- Backend startup failures return `503` instead of being reported as capacity conflicts.
- Automatic timeout expiry writes an event log entry (`instance expired by timeout`).

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
- `docs/07_ctfd-plugin-comparison.md`: comparison with a CTFd plugin approach and rationale for a standalone orchestrator

## Known limitations

- No direct CTFd plugin integration yet; this PoC exposes a service API that can be called from a CTFd plugin.
- Uses Docker socket mount in the PoC stack; acceptable for prototype, but production should isolate this with stricter host controls.
- Load tests currently hit `max_instances` quickly unless that value is increased per challenge.

## Startup bootstrap

You can preload one or more registry entries at process start with `POC_BOOTSTRAP_CHALLENGES`.
The value must be valid JSON containing either one object or an array of objects using the same schema as `POST /api/challenges`.

Example:

```bash
export POC_BOOTSTRAP_CHALLENGES='[
  {
    "challenge_id":"demo-http",
    "name":"Demo HTTP Challenge",
    "image":"poc-demo-http:latest",
    "container_port":8080,
    "cpu_limit":0.5,
    "memory_limit_mb":256,
    "timeout_seconds":900,
    "max_instances":30
  }
]'
```

`docker-compose.yml` and `scripts/run_local_mock.sh` already set this for the default showcase container.
