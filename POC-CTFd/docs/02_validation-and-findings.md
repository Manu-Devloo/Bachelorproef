# Validation And Findings

Validation date: `2026-03-24`

## End-to-end commands executed

Local same-host regression:

```bash
cd POC-CTFd
./scripts/smoke_test.sh
```

Split-host end-to-end validation:

```bash
cd POC-CTFd
./scripts/smoke_test_split.sh
```

Real two-VM validation:

- executed on Lima with two separate VMs on `2026-03-24`
- full command log and findings are documented in `docs/03_real-vm-validation.md`

Sanity check after code changes:

```bash
python3 -m compileall POC-CTFd/plugins/ctfd_container_challenges POC-CTFd/scripts/smoke_test.py
```

## What the smoke tests now verify

Both smoke flows execute a live HTTP-level scenario against CTFd and the plugin:

- initial CTFd setup and admin login
- image archive upload
- challenge creation and flag creation
- archive re-import by deleting the uploaded image before the first runtime start
- three different player accounts
- instance creation for multiple players
- idempotent restart for the same player
- per-challenge capacity enforcement
- slot reuse after a solved challenge is cleaned up
- manual stop behavior
- timeout cleanup behavior
- admin history/log visibility

## Findings during implementation

### 1. The original Compose file hardcoded the local Docker socket

This made split-host deployment impossible without editing the base stack. The fix was to:

- move common settings into `docker-compose.yml`;
- add `docker-compose.local.yml` for the local socket case;
- add `docker-compose.split.yml` for the split-host case.

### 2. The plugin needed an explicit Docker control endpoint

`docker.from_env()` was too implicit for the deployment goal. The plugin now reads dedicated runtime variables for:

- remote Docker host
- TLS verification
- certificate path
- timeout

### 3. The player-facing host is not the same as the Docker API host

The plugin now clearly separates:

- `CTFD_CONTAINER_DOCKER_HOST` for control-plane calls
- `CTFD_CONTAINER_PUBLIC_HOST` for generated access URLs

That separation is required when CTFd and Docker run on different machines.

### 4. Split-host testing on Docker Desktop required an in-network smoke runner

When the remote Docker VM is simulated with `docker:dind`, the spawned challenge services are reachable from peers on the same Compose network, not directly from the macOS host in a useful generic way. The fix was to run the split smoke test inside a dedicated `smoke` container on that network.

### 5. A published port range is operationally useful

The plugin now supports an optional published port range. This helps with:

- firewall rules on the Docker VM
- predictable exposure of runtime services
- local split-host simulation

### 6. The challenge model already enforces a minimum timeout of 30 seconds

The timeout smoke scenario initially failed because the new test used a shorter value. The validation flow was updated to respect the existing lower bound instead of changing the product behavior.

### 7. The smoke runner could not use the CTFd image entrypoint as-is

The base CTFd image always starts Gunicorn through `/opt/CTFd/docker-entrypoint.sh`. The split smoke service therefore needed an explicit `entrypoint` override to run the smoke script directly.

### 8. Real VM validation on Lima needed a named shared `user-v2` network

Default Lima networking put each VM behind a separate NAT, which produced duplicate guest IPs and was not suitable for real cross-VM testing. A custom named `user-v2` network provided direct VM-to-VM connectivity without requiring privileged macOS networking helpers.

### 9. Real VM validation on Lima did not expose the guest subnet directly to the host

The macOS host could not reach `192.168.150.x` directly in the validated Lima configuration. The real smoke run therefore executed from inside the CTFd VM while still targeting the Docker VM over the real cross-VM network.

## Outcome

The PoC now works in both validated modes:

- same-host mode using the local Docker socket
- split-host mode where CTFd controls a separate Docker daemon and players receive runtime URLs for that remote host
- real two-VM mode validated on Lima, with CTFd in one VM and challenge containers on another

Known limitation:

- the included split-host Compose stack is a local simulation and intentionally uses insecure Docker TCP (`2375`); a real deployment should switch to TLS-protected Docker API access.
