# Real VM Validation

Validation date: `2026-03-24`

## Goal

Validate the PoC with:

- CTFd and the plugin running inside one VM
- challenge containers running on a different VM
- the existing smoke flow exercising the full user journey against that cross-VM setup

## Test environment

Host:

- macOS on Apple Silicon
- Lima `user-v2` networking

VM topology:

- `bp-ctfd-vm`: CTFd, plugin, local Docker Engine for the CTFd stack
- `bp-docker-vm`: remote Docker Engine for challenge containers

Shared Lima network:

```bash
limactl network create bp-shared-net --gateway 192.168.150.1/24
```

Observed VM addresses and routes:

```bash
limactl shell bp-ctfd-vm -- ip -4 addr show dev eth0
limactl shell bp-ctfd-vm -- ip route
limactl shell bp-docker-vm -- ip -4 addr show dev eth0
limactl shell bp-docker-vm -- ip route
```

Observed values during validation:

- `bp-ctfd-vm`: `192.168.150.1`
- `bp-docker-vm`: `192.168.150.3`
- default gateway inside both VMs: `192.168.150.2`

Cross-VM reachability check:

```bash
limactl shell bp-ctfd-vm -- ping -c 2 192.168.150.3
limactl shell bp-docker-vm -- ping -c 2 192.168.150.1
```

Both ping tests succeeded.

## VM provisioning

CTFd VM:

```bash
limactl start \
  --name=bp-ctfd-vm \
  --network=lima:bp-shared-net \
  --cpus=2 \
  --memory=4 \
  --disk=20 \
  --mount=/Users/manu/Documents/GitHub/Bachelorproef:w \
  template:docker-rootful \
  -y
```

Docker VM:

```bash
limactl start \
  --name=bp-docker-vm \
  --network=lima:bp-shared-net \
  --cpus=2 \
  --memory=4 \
  --disk=20 \
  template:docker-rootful \
  -y
```

## Remote Docker API setup

For validation, the Docker VM was configured to listen on insecure TCP `2375` in addition to the Unix socket:

```bash
limactl shell bp-docker-vm -- bash -lc '
set -euo pipefail
sudo mkdir -p /etc/systemd/system/docker.service.d
cat <<'"'"'EOF'"'"' | sudo tee /etc/systemd/system/docker.service.d/override.conf >/dev/null
[Service]
ExecStart=
ExecStart=/usr/bin/dockerd --host=unix:///var/run/docker.sock --host=tcp://0.0.0.0:2375 --containerd=/run/containerd/containerd.sock
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker
sudo systemctl is-active docker
'
```

Cross-VM remote Docker check from the CTFd VM:

```bash
limactl shell bp-ctfd-vm -- curl -fsS http://192.168.150.3:2375/_ping
limactl shell bp-ctfd-vm -- docker -H tcp://192.168.150.3:2375 version
```

Both checks succeeded.

## CTFd startup on the CTFd VM

The CTFd stack was started from the mounted repository with the real Docker VM IP as both:

- `CTFD_CONTAINER_DOCKER_HOST`
- `CTFD_CONTAINER_PUBLIC_HOST`

Command used:

```bash
limactl shell bp-ctfd-vm -- bash -lc '
set -euo pipefail
cd /Users/manu/Documents/GitHub/Bachelorproef/POC-CTFd
export CTFD_CONTAINER_DOCKER_HOST=tcp://192.168.150.3:2375
export CTFD_CONTAINER_PUBLIC_HOST=192.168.150.3
export CTFD_CONTAINER_PUBLISHED_PORT_MIN=20000
export CTFD_CONTAINER_PUBLISHED_PORT_MAX=20099
docker compose -f docker-compose.yml up -d --build
'
```

Readiness checks:

```bash
limactl shell bp-ctfd-vm -- bash -lc 'cd /Users/manu/Documents/GitHub/Bachelorproef/POC-CTFd && docker compose -f docker-compose.yml ps'
limactl shell bp-ctfd-vm -- curl -I http://127.0.0.1:8001
```

Observed result:

- `ctfd-plugin-poc` started successfully
- CTFd returned `302 /setup`, confirming the service was reachable

## End-to-end smoke run

Because Lima `user-v2` networking did not expose the guest subnet directly to the macOS host, the smoke runner was executed from inside `bp-ctfd-vm`.

Command used:

```bash
limactl shell bp-ctfd-vm -- bash -lc '
set -euo pipefail
cd /Users/manu/Documents/GitHub/Bachelorproef/POC-CTFd
docker build -t poc-demo-http:latest ../POC/challenges/demo-http
archive_base=$(mktemp /tmp/poc-demo-http.XXXXXX)
archive_path="${archive_base}.tar"
docker save -o "$archive_path" poc-demo-http:latest
BASE_URL=http://127.0.0.1:8001 \
IMAGE_ARCHIVE_PATH="$archive_path" \
SMOKE_TEST_DOCKER_HOST=tcp://192.168.150.3:2375 \
SMOKE_TEST_EXPECT_ACCESS_HOST=192.168.150.3 \
SMOKE_TEST_EXPECT_ACCESS_PORT_MIN=20000 \
SMOKE_TEST_EXPECT_ACCESS_PORT_MAX=20099 \
python3 ./scripts/smoke_test.py
rm -f "$archive_path" "$archive_base"
'
```

Observed result:

```text
Smoke test passed
{
  "primary_challenge_id": 1,
  "timeout_challenge_id": 2,
  "instances": {
    "player1": "401a60d48b0e48d185940392cb3af109",
    "player2": "3b9f75af3f9d4ec9866f014ad6ad4781",
    "player3": "d9c18dc4c0344ae1b0c4957ed17984aa",
    "timeout": "2ce6880fdac441389188ece02abfb7fb"
  }
}
```

The smoke flow verified:

- initial CTFd setup
- archive upload and archive-backed image import
- three player accounts
- first runtime start
- idempotent second start for the same player
- concurrent runtime capacity enforcement
- solve-triggered cleanup
- manual stop
- timeout cleanup
- admin runtime history/log visibility

## Cleanup verification

After the smoke run completed:

```bash
limactl shell bp-docker-vm -- sudo docker ps -a
limactl shell bp-docker-vm -- sudo docker network ls
```

Observed result:

- no challenge containers remained
- no plugin-managed networks remained

## Findings

### 1. The plugin works across two actual VMs

This was not just a local `docker:dind` simulation. CTFd on `bp-ctfd-vm` created, stopped, and reaped challenge containers on `bp-docker-vm` over the remote Docker API.

### 2. Lima `user-v2` custom networks are sufficient for rootless cross-VM validation

The earlier attempt with default Lima networking put each VM behind its own isolated NAT and produced duplicate guest IPs. A named `user-v2` network fixed that without requiring `socket_vmnet` or privileged host setup.

### 3. The macOS host could not directly reach the guest subnet in this Lima mode

This prevented running the smoke test from the host against `192.168.150.x`. The workaround was to run the smoke flow from inside the CTFd VM itself.

### 4. Plain Docker TCP `2375` is acceptable for disposable validation only

The Docker daemon on the runtime VM emitted the expected deprecation and security warnings. A real deployment should use TLS-protected Docker API access or another protected control path.

### 5. Published port ranges behaved correctly on the real Docker VM

The generated challenge access URLs pointed at `192.168.150.3` with ports in the configured `20000-20099` range, and those services were reachable from the CTFd VM during the smoke flow.
