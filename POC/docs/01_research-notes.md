# Research Notes

Date: 2026-02-27

Goal: choose pragmatic implementation options for a BP PoC that manages challenge containers dynamically.

## Primary sources consulted

1. CTFd deployment docs: [CTFd - Docker deployment](https://docs.ctfd.io/docs/deployment/installation/)
2. CTFd plugins docs: [CTFd - Plugins](https://docs.ctfd.io/docs/plugins/overview/)
3. Docker SDK for Python: [Docker SDK - Getting started](https://docker-py.readthedocs.io/en/stable/)
4. Docker docs (Python guide): [Docker - Build and run with Python](https://docs.docker.com/guides/python/)
5. OWASP cheat sheet: [OWASP Docker Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html)
6. Locust docs: [Locust - What is Locust?](https://docs.locust.io/en/stable/what-is-locust.html)

## Extracted constraints and decisions

## CTFd integration model

- CTFd supports plugin-based extension via Python (`load(app)` entrypoint).
- Decision: keep this PoC as a standalone orchestrator API first, then integrate via plugin calls.
- Reason: faster validation of lifecycle/security/load mechanics before CTFd UI coupling.

## Container orchestration mechanism

- Docker SDK for Python provides direct Engine API access from Python.
- Decision: use Docker SDK in the PoC backend (`docker.from_env()`, networks, container run/remove).
- Rejected alternative: shelling out to `docker` CLI for core logic because parsing is brittle and harder to test.

## Security baseline

From OWASP Docker guidance and container-hardening practice:

- Run with dropped capabilities.
- Enforce `no-new-privileges`.
- Use read-only filesystems where possible.
- Enforce resource limits.

Decision in PoC:

- `cap_drop=['ALL']`
- `security_opt=['no-new-privileges:true']`
- `read_only=True`
- `mem_limit`, `nano_cpus`, `pids_limit`
- per-instance isolated Docker network

## Load testing approach

- Locust supports scriptable HTTP load with headless mode.
- Decision: include a simple Locust scenario for repeated instance-start calls and API listing.

## Open questions for next iteration

- Should challenge-level routing be done directly from CTFd through reverse proxy labels (Traefik/Nginx) instead of host-port mapping?
- Should hardening add custom seccomp profiles per challenge class?
- Should instance fairness be user-quota based (per team) in addition to global `max_instances`?
