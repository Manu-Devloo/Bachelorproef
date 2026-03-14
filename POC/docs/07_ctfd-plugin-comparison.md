# Comparison With `CTFd-Docker-Challenges`

Date: 2026-03-06

Reference compared:

- `https://github.com/offsecginger/CTFd-Docker-Challenges`

## Purpose of this comparison

This note compares the current PoC with the `CTFd-Docker-Challenges` reference project and explains why the standalone orchestrator architecture used in this bachelor project is the better fit for the research goals.

## High-level difference

The two projects solve a similar problem, but they do so at different architectural layers.

- `CTFd-Docker-Challenges` is a **CTFd plugin**. It adds a `docker` challenge type directly into the scoreboard and manages container creation, status tracking, and stop/revert actions from inside CTFd.
- This PoC is a **standalone orchestration service**. It runs as a separate Flask application with its own API, persistence layer, lifecycle logic, cleanup process, logging, and operator dashboard.

In short: the reference project embeds orchestration inside the CTF platform, while this PoC separates orchestration into its own service.

## Functional comparison

### Reference plugin strengths

The reference plugin is stronger in direct scoreboard integration:

- Docker challenge type is available directly in CTFd.
- Players can start a challenge container from the challenge view.
- Connection information is updated inside the challenge interface.
- Containers can be stopped automatically when a challenge is solved.
- Admins get a status page inside CTFd.

This makes the plugin convenient when the only goal is to add Docker-backed challenges to a running CTFd installation as quickly as possible.

### PoC strengths

The PoC is stronger in orchestration design and control:

- Separate challenge registry with explicit runtime configuration.
- Per-challenge limits for CPU, memory, timeout, and maximum concurrent instances.
- Idempotent start behavior for the same `challenge_id + user_id`.
- Automatic timeout reaper and explicit bulk stop support.
- Structured event logging and health endpoints.
- Dedicated service boundary that can later be consumed by a CTFd plugin or another frontend.

This makes the PoC stronger as a platform component rather than only a CTFd extension.

## Why the standalone PoC is better for this project

### 1. Better separation of concerns

The standalone architecture keeps CTFd responsible for competition logic and user interaction, while container lifecycle management is handled by a dedicated orchestration service.

This separation is beneficial because:

- orchestration logic is easier to reason about in isolation;
- the service can evolve independently from CTFd internals;
- failures in container management are easier to observe and diagnose;
- the same orchestrator can later serve multiple frontends, not only CTFd.

For a bachelor project, this is a stronger systems design than placing all runtime concerns directly inside scoreboard plugin code.

### 2. Lower coupling to CTFd internals

A plugin-based design depends on CTFd APIs, templates, plugin hooks, challenge classes, and frontend behavior. That creates version-coupling and maintenance risk whenever CTFd changes.

The standalone PoC reduces this coupling:

- the orchestrator exposes a stable JSON API;
- integration with CTFd can be added as a thin adapter layer later;
- the orchestration logic remains reusable even if the scoreboard platform changes.

That portability is an important advantage in a research-oriented prototype.

### 3. Stronger lifecycle management

The PoC treats container management as a first-class backend problem instead of a side effect of challenge rendering.

Compared with the reference plugin, the PoC provides:

- explicit challenge registration;
- capacity enforcement with `max_instances`;
- duplicate-start protection through idempotent handling;
- timeout-based cleanup as a service function;
- operator-visible logs and instance state tracking.

This better matches the research goal of controlled, per-user challenge environments.

### 4. Stronger security baseline

The PoC applies baseline hardening during container launch, including:

- read-only root filesystem;
- dropped Linux capabilities;
- `no-new-privileges`;
- PID limits;
- CPU and memory limits;
- per-instance Docker network isolation.

The reference plugin focuses primarily on creating and deleting challenge containers through the Docker API. It is effective for integration, but it does not implement the same hardening baseline in its launch path.

For a thesis that evaluates secure and isolated challenge execution, the standalone orchestrator is therefore the stronger implementation.

### 5. Better observability and testability

Because the PoC is a separate service, it is easier to validate with:

- unit tests;
- API tests;
- load tests;
- explicit log inspection;
- health checks and manual lifecycle testing.

This is valuable in an academic context because it provides clearer evidence for validation, reproducibility, and technical evaluation.

## Fair conclusion

`CTFd-Docker-Challenges` is better when the primary objective is fast and convenient CTFd integration with minimal extra infrastructure.

The standalone PoC used in this bachelor project is better when the objective is to design and evaluate a reusable, maintainable, secure orchestration layer for per-user challenge containers.

That is why the standalone approach is the stronger architectural choice for this research:

- it is more modular;
- it is less tightly coupled to one platform;
- it provides better lifecycle control;
- it enables stronger security controls;
- it is easier to test, observe, and extend.

## Implication for the bachelor project

The current PoC should be seen as the **orchestration core** of the final architecture, not as a replacement for participant-facing CTFd integration.

The most logical next step is:

1. keep the standalone orchestrator as the runtime control plane;
2. add a thin CTFd plugin that calls the orchestrator API;
3. let CTFd handle UI and challenge flow, while the orchestrator handles execution and cleanup.

This preserves the usability advantages of CTFd integration without giving up the architectural strengths of a dedicated service.
