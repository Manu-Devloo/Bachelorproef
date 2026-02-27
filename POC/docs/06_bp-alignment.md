# BP Alignment Check

Date: 2026-02-27

Question checked: "Is this PoC implemented as described in the BP?"

## Reference scope

Compared against:

- `bachproef/inleiding.tex` research objective text
- `bachproef/poc.tex` expected PoC sections and TODO bullets

## Alignment summary

- **Core lifecycle automation**: aligned
- **Security baseline and isolation**: mostly aligned
- **CTFd-native plugin integration**: not aligned yet (current PoC is standalone service + dashboard)
- **Scalability/load-balancing across multiple VMs**: not aligned yet

## Detailed matrix

1. Dynamic challenge container lifecycle (start/stop/timeout/cleanup)
- Status: **Implemented**
- Evidence: `service.py`, `reaper.py`, API endpoints `/api/instances/start`, `/api/instances/<id>/stop`, `/api/reaper/run`

2. Docker Engine API integration in Python
- Status: **Implemented**
- Evidence: `backend.py` using Docker SDK for Python

3. Challenge configuration by organizer (image, port, limits, timeout, max instances)
- Status: **Implemented**
- Evidence: `POST /api/challenges`, dashboard challenge form

4. Security controls (capabilities drop, read-only FS, no-new-privileges, CPU/RAM limits, network isolation)
- Status: **Mostly implemented**
- Evidence: `backend.py` container launch options + per-instance network
- Gap: explicit custom seccomp profile support not implemented yet

5. Isolation between participants
- Status: **Implemented (baseline)**
- Evidence: per-instance bridge networks and validated DNS isolation experiment

6. CTFd plugin integration and in-CTFd participant UX
- Status: **Not implemented yet**
- Current state: standalone orchestrator API and operator dashboard
- Needed for full BP alignment: CTFd plugin that calls this API and exposes challenge controls inside CTFd pages

7. Reverse proxy entrypoint (Traefik/Nginx) and LB architecture
- Status: **Not implemented yet**
- Current state: direct host-port mapping for each instance

8. Multi-host/VM distribution strategy
- Status: **Not implemented yet**
- Current state: single Docker engine host PoC

## Conclusion

The current PoC correctly validates the **core research mechanism** (automated, constrained, isolated challenge containers on self-hosted Docker), but it is **not yet fully equal** to the end-state architecture described in `bachproef/poc.tex` because the following are still open:

- CTFd plugin integration
- reverse proxy/LB integration
- multi-host scaling design
- optional seccomp profile hardening

This means the PoC is strong for lifecycle/security proof, but still needs one integration iteration for full BP chapter alignment.
