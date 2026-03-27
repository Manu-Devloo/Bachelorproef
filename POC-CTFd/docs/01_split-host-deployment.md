# Split-Host Deployment Guide

This document describes the deployment mode where the CTFd application runs on one VM and the Docker daemon that launches challenge containers runs on another.

## Why this change was needed

The original PoC assumed a local Docker socket mounted directly into the CTFd container. That worked for a single-host prototype, but it broke the intended deployment model where:

- VM A runs CTFd and the plugin.
- VM B runs Docker and the challenge containers.
- Players connect to the published challenge ports on VM B.

The implementation now separates:

- the Docker control endpoint used by the plugin;
- the player-facing host used in generated `access_url` values.

## Runtime variables

The plugin now supports these deployment variables:

| Variable | Purpose |
| --- | --- |
| `CTFD_CONTAINER_DOCKER_HOST` | Docker API endpoint used by the plugin, for example `tcp://docker-vm.example.org:2376` or `unix:///var/run/docker.sock` |
| `CTFD_CONTAINER_DOCKER_TLS_VERIFY` | Enables TLS verification for remote Docker connections |
| `CTFD_CONTAINER_DOCKER_CERT_PATH` | Directory containing `ca.pem`, `cert.pem`, and `key.pem` for Docker TLS |
| `CTFD_CONTAINER_DOCKER_TIMEOUT` | Docker client timeout in seconds |
| `CTFD_CONTAINER_PUBLIC_HOST` | Hostname or IP that players should use to reach the published challenge ports |
| `CTFD_CONTAINER_PUBLIC_SCHEME` | URL scheme used in generated access URLs |
| `CTFD_CONTAINER_PUBLISHED_PORT_MIN` | Optional lower bound for published host ports |
| `CTFD_CONTAINER_PUBLISHED_PORT_MAX` | Optional upper bound for published host ports |

## Compose layout

The PoC now uses a shared base Compose file plus mode-specific overrides:

- `docker-compose.yml`: common CTFd service and runtime configuration
- `docker-compose.local.yml`: same-host mode with `/var/run/docker.sock`
- `docker-compose.split.yml`: split-host simulation with a dedicated remote Docker daemon (`docker:dind`) and a smoke-runner container

## Local single-host mode

Use this when CTFd and Docker run on the same machine:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

In this mode:

- the plugin talks to `unix:///var/run/docker.sock`;
- generated runtime URLs use `127.0.0.1`;
- the host Docker daemon publishes the challenge ports directly.

## Split-host simulation

Use this when validating the control-plane split locally:

```bash
docker compose -f docker-compose.yml -f docker-compose.split.yml up -d --build ctfd docker-runtime
docker compose --profile test -f docker-compose.yml -f docker-compose.split.yml run --rm smoke
```

In this mode:

- `ctfd` talks to `tcp://docker-runtime:2375`;
- generated runtime URLs point to `docker-runtime`;
- the smoke test runs inside the same Compose network so it can reach the published ports on the simulated Docker VM.

## Real two-VM deployment

For a real deployment, keep the same separation:

1. Run CTFd and the plugin on VM A.
2. Run Docker Engine on VM B and expose a protected API endpoint.
3. Set `CTFD_CONTAINER_DOCKER_HOST` on VM A to the Docker API on VM B.
4. Set `CTFD_CONTAINER_PUBLIC_HOST` to the address players can use for VM B.
5. If your firewall requires a fixed range, set `CTFD_CONTAINER_PUBLISHED_PORT_MIN` and `CTFD_CONTAINER_PUBLISHED_PORT_MAX` and open that range on VM B.

Example:

```env
CTFD_CONTAINER_DOCKER_HOST=tcp://docker-vm.example.org:2376
CTFD_CONTAINER_DOCKER_TLS_VERIFY=true
CTFD_CONTAINER_DOCKER_CERT_PATH=/run/secrets/docker-client
CTFD_CONTAINER_PUBLIC_HOST=docker-vm.example.org
CTFD_CONTAINER_PUBLIC_SCHEME=https
CTFD_CONTAINER_PUBLISHED_PORT_MIN=20000
CTFD_CONTAINER_PUBLISHED_PORT_MAX=20999
```

## Security notes

- The split-host test topology uses plain TCP on port `2375` because it is a disposable local simulation.
- For a real two-VM setup, use TLS and limit network access to the Docker API.
- A fixed published-port range is useful for firewall policy and auditability, but it should still be scoped tightly.
