import time


class CircuitBreaker:
    """Opens after N consecutive infrastructure failures, then lets one probe through."""

    def __init__(self, threshold: int, reset_seconds: float):
        self._threshold = threshold
        self._reset = reset_seconds
        self._failures = 0
        self._opened_at: float | None = None

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        if time.monotonic() - self._opened_at >= self._reset:
            self._opened_at = time.monotonic()  # half-open: one probe per reset window
            return True
        return False

    def success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = time.monotonic()
