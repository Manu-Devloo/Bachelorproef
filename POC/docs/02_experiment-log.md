# Experiment Log

Date: 2026-02-27

This file logs concrete spikes and what changed because of them.

## Experiment A: Direct Docker CLI spike

Command:

```bash
docker run -d --rm --name exp-cli-demo -p 0:8080 poc-demo-http:latest
docker port exp-cli-demo 8080/tcp
docker rm -f exp-cli-demo
```

Observed:

- Container starts quickly and host port is allocated dynamically.
- Output parsing is fragile and shell-centric.

Decision:

- Use Docker SDK for production PoC logic.
- Keep CLI only for ad-hoc diagnostics.

## Experiment B: Docker SDK startup with local images

Initial behavior:

- Backend always called `images.pull(image)`.
- Locally built images (`poc-demo-http:latest`) failed with pull-denied errors.

Fix applied:

- Try `images.get(image)` first.
- Pull only if `ImageNotFound`.

Result:

- Local challenge images now start correctly.

## Experiment C: Endpoint readiness race in smoke test

Issue:

- Immediately curling a newly started challenge occasionally produced connection reset.

Fix applied:

- Added retry loop (10x, 1s) before declaring endpoint failure.

Result:

- Smoke test became stable across repeated runs.

## Experiment D: Network isolation check

Setup:

- Two containers on separate user-defined bridge networks.
- DNS lookup from container A to container B by name.

Observed output:

```text
iso-b:FAILED:gaierror
iso-a:RESOLVED:172.21.0.2
```

Result:

- Per-instance network isolation behaves as expected for container-name discovery.

## Experiment E: Hardening flags verification

Method:

- Started an instance through API.
- Inspected container HostConfig.

Observed:

```text
ReadonlyRootfs True
CapDrop ['ALL']
SecurityOpt ['no-new-privileges:true']
Memory 268435456
NanoCpus 500000000
PidsLimit 128
```

Result:

- Runtime security/resource flags are correctly applied.

## Experiment F: Locust load behavior

Command:

```bash
app/.venv/bin/locust -f tests/locustfile.py --host=http://127.0.0.1:8000 --headless -u 10 -r 5 -t 10s
```

Observed:

- Total requests: 128
- Failures: 84 (all `409 CONFLICT` from `start_instance`)
- Cause: challenge `max_instances` set to 20 in smoke setup, reached quickly.

Decision:

- Keep this as a useful pressure signal.
- Next iteration should include stop/reuse behavior in the load scenario or larger capacity settings.

## Experiment G: Orphan cleanup after failed test flow

Issue:

- Earlier failing smoke runs could leave managed challenge containers/networks running.
- This happened when the script exited before explicit stop calls.

Fix applied:

- Added `trap cleanup EXIT` to `scripts/smoke_test.sh`.
- Cleanup now:
  - calls `/api/instances/stop-all` when API is reachable
  - force-removes any Docker resources labeled `poc.managed=true`
- Added `scripts/cleanup_runtime.sh` for manual full cleanup.

Result:

- No lingering PoC containers or `pocnet-*` networks after cleanup.
