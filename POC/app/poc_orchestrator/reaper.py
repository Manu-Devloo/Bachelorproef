"""Background thread that expires timed-out instances."""

from __future__ import annotations

import logging
from threading import Event, Thread

from .service import OrchestratorService

LOGGER = logging.getLogger(__name__)


class ReaperThread(Thread):
    def __init__(self, *, service: OrchestratorService, interval_seconds: float) -> None:
        super().__init__(name="instance-reaper", daemon=True)
        self._service = service
        self._interval_seconds = max(interval_seconds, 1.0)
        self._stop_event = Event()

    def run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                expired = self._service.reap_expired_instances()
                if expired:
                    LOGGER.info("reaper expired %d instances", len(expired))
            except Exception:
                LOGGER.exception("reaper loop failed")

    def shutdown(self) -> None:
        self._stop_event.set()
