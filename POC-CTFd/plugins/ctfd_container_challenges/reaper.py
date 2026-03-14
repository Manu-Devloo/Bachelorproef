"""Background reaper thread for timed-out challenge containers."""

from __future__ import annotations

import logging
from threading import Event, Thread

from .service import RuntimeService

LOGGER = logging.getLogger(__name__)


class ReaperThread(Thread):
    def __init__(self, *, service: RuntimeService, interval_seconds: float) -> None:
        super().__init__(name="ctfd-container-reaper", daemon=True)
        self._service = service
        self._interval_seconds = max(interval_seconds, 1.0)
        self._stop_event = Event()

    def run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                expired = self._service.reap_expired_instances()
                if expired:
                    LOGGER.info("expired %d runtime instance(s)", len(expired))
            except Exception:
                LOGGER.exception("runtime reaper loop failed")

    def shutdown(self) -> None:
        self._stop_event.set()
