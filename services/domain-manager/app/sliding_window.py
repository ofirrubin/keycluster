"""Sliding-window rate limiter used to bound outbound JWKS fetches.

Memory is bounded by ``max_events`` (the deque never grows past it), time is
read from time.monotonic() (or an injected clock) so wall-clock jumps cannot
reset or freeze the window, and a ``threading.Lock`` keeps prune-check-append
atomic even if a caller runs outside the event loop thread.
"""
import threading
import time
from collections import deque
from typing import Callable, Deque, Optional


class SlidingWindowLimiter:
    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max_events = max_events
        self._window = window_seconds
        self._clock = clock
        self._events: Deque[float] = deque(maxlen=max_events)
        self._lock = threading.Lock()

    def _now(self) -> float:
        return self._clock() if self._clock is not None else time.monotonic()

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        while self._events and self._events[0] <= cutoff:
            self._events.popleft()

    def try_acquire(self) -> bool:
        """Record one event and return True, or return False if the window is full."""
        with self._lock:
            now = self._now()
            self._prune(now)
            if len(self._events) >= self._max_events:
                return False
            self._events.append(now)
            return True

    def retry_after(self) -> float:
        """Seconds until the oldest event leaves the window (0 if a slot is free)."""
        with self._lock:
            now = self._now()
            self._prune(now)
            if len(self._events) < self._max_events:
                return 0.0
            return max(0.0, self._events[0] + self._window - now)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
