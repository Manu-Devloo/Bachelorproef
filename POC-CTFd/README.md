# CTFd Container Challenge Plugin PoC

This second PoC packages the container lifecycle flow as a real CTFd plugin instead of a standalone orchestrator dashboard.
It adds a custom `containerized` challenge type to CTFd, launches per-user containers through the Docker socket, and cleans them up on solve or timeout.

## What is included

- Custom CTFd challenge type: `containerized`
- Docker-backed runtime API inside the plugin
- Per-challenge controls for image, exposed port, CPU, memory, timeout, and concurrency
- Optional Docker archive upload for challenge images (`docker save` tarballs imported into the local Docker daemon)
- Idempotent start behavior for the same user/account
- Automatic timeout reaper
- Automatic stop when the challenge is solved
- Admin runtime page inside CTFd: `/admin/plugins/containerized-challenges`
- Admin controls to force-stop one running instance, stop all running instances for a challenge, and inspect each challenge's active resource profile
- Automated smoke test that boots CTFd, configures it, creates a challenge, starts a runtime, verifies access, solves the flag, and confirms cleanup

## Quick start

Prerequisites:

- Docker Desktop / Docker Engine running
- Docker Compose v2+

Run the full end-to-end smoke test:

```bash
cd POC-CTFd
./scripts/smoke_test.sh
```

Open CTFd:

- [http://127.0.0.1:8001](http://127.0.0.1:8001)

The smoke test uses the existing demo image from [`../POC/challenges/demo-http`](../POC/challenges/demo-http) and validates the plugin against a live Dockerized CTFd instance.

## Architecture

```mermaid
flowchart LR
    Admin["Admin User"] -->|"create/edit containerized challenge"| CTFd["CTFd + containerized plugin"]
    Player["Player or Team"] -->|"Start Instance / Stop Instance"| CTFd

    Admin -->|"upload docker save archive"| Upload["Admin upload API"]
    Upload -->|"store archive metadata"| RuntimeDB["Plugin runtime SQLite"]
    Upload -->|"save archive file"| ArchiveStore["Archive storage (/var/ctfd-container/images)"]
    Upload -->|"docker load + local tag"| Docker["Docker daemon via socket"]

    CTFd -->|"challenge config + runtime API"| RuntimeService["Runtime service"]
    RuntimeService -->|"instance records + logs"| RuntimeDB
    RuntimeService -->|"re-import archive if image is missing"| ArchiveStore
    RuntimeService -->|"start / stop containers"| Docker

    Docker -->|"publish random host port"| Challenge["Per-user or per-team challenge container"]
    Challenge -->|"temporary access URL"| Player

    Reaper["Background reaper / admin reaper button"] -->|"expire timed-out instances"| RuntimeService
    Solver["Challenge solve event"] -->|"auto-stop active instance"| RuntimeService
```

## Runtime behavior

- Challenge instances are scoped per authenticated player in `users` mode.
- In `teams` mode, players need to belong to a team before they can launch an instance.
- Duplicate start requests reuse the already running instance.
- Containers are launched with the same baseline hardening as the first PoC:
  - read-only root filesystem
  - dropped Linux capabilities
  - `no-new-privileges`
  - PID limit
  - CPU and memory limits
  - dedicated bridge network per instance
- Solving the challenge stops the active instance for that player.
- Expired instances are reaped automatically in the background.

## Challenge settings summary

| Setting | Purpose | Effect on runtime behavior |
| --- | --- | --- |
| `image` or uploaded archive | Selects the challenge container image | Determines which workload is started for each participant |
| `container_port` | Declares the service port inside the container | Used to publish a reachable host port back to the player |
| `cpu_limit` | Caps CPU consumption per instance | Prevents one challenge from monopolizing host CPU |
| `memory_limit_mb` | Caps memory usage per instance | Limits blast radius of runaway processes |
| `timeout_seconds` | Sets the maximum lifetime of an instance | Drives automatic cleanup by the reaper |
| `max_instances` | Caps concurrent active runtimes per challenge | Enforces back-pressure under peak demand |

## Admin controls

- The challenge create/edit screens expose per-challenge CPU, memory, timeout, port, image, and concurrency settings.
- Admins can either reference a normal Docker image name or upload a `.tar`, `.tar.gz`, or `.tgz` archive produced by `docker save`.
- Uploaded archives are stored in the plugin runtime volume and re-imported automatically if the local Docker image is later removed.
- The admin runtime page shows a configuration table for all `containerized` challenges so the effective resource profile is visible without opening each challenge editor.
- Admins can force-stop a single running instance or stop all active instances for a specific challenge from the runtime page.
- Admins can run the timeout reaper manually from the runtime page.

## Stack layout

- `Dockerfile`: CTFd image with the plugin and Docker SDK installed
- `docker-compose.yml`: single-container CTFd PoC with persisted SQLite and plugin runtime state
- `plugins/ctfd_container_challenges/`: plugin code, runtime service, admin page, and challenge assets
- `scripts/smoke_test.sh`: build, boot, and verify the PoC
- `scripts/smoke_test.py`: HTTP-level end-to-end verification

## Notes

- This PoC mounts the host Docker socket into CTFd because the goal is to validate plugin behavior quickly. That is acceptable for the prototype but not a production deployment pattern.
- The compose stack uses SQLite deliberately so the plugin can bootstrap cleanly through CTFd’s plugin migration path without extra database services.
- After changing plugin frontend assets, rebuild the stack with `docker compose up -d --build` and refresh the browser. The plugin now appends versioned asset URLs, but an already open tab can still show stale UI until it reloads.
