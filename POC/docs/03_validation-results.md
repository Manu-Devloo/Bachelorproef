# Validation Results

Date: 2026-02-27

## 1) Automated tests (mock backend)

Command:

```bash
cd POC
app/.venv/bin/pytest -q tests
```

Result:

- `4 passed in 0.60s`

Coverage of these tests:

- challenge registration
- idempotent start behavior per user/challenge
- `max_instances` enforcement
- timeout expiry flow through the service layer
- main API happy-path (register, start, stop)

## 2) Docker end-to-end smoke test

Command:

```bash
cd POC
docker compose -f docker-compose.yml down -v
./scripts/smoke_test.sh
```

Result:

- Success
- Built demo image and orchestrator image
- Started stack
- Registered challenge
- Started two instances (`team-a`, `team-b`)
- Validated challenge HTTP endpoints
- Stopped both instances and listed final states as `stopped`

## 3) Timeout auto-cleanup validation

Scenario:

- Registered `quick-expire` challenge with `timeout_seconds=30`
- Started instance for `team-timeout`
- Waited 35 seconds

Result:

- Instance transitioned to:
  - `status: expired`
  - `stop_reason: timeout`
  - `stopped_at` timestamp after deadline

## 4) UI availability

Check:

```bash
curl -sS http://127.0.0.1:8000/ | head
```

Result:

- Dashboard HTML served correctly.

## 5) Load behavior snapshot

Command:

```bash
cd POC
app/.venv/bin/locust -f tests/locustfile.py --host=http://127.0.0.1:8000 --headless -u 10 -r 5 -t 10s
```

Observed summary:

- 128 total requests
- 84 failures (`POST /api/instances/start` => `409`)
- Primary reason: capacity guard (`max_instances`) hit under aggressive create-only load

Interpretation:

- Capacity limits are enforced correctly.
- For realistic sustained load tests, include stop/reuse patterns or increase challenge limits.
