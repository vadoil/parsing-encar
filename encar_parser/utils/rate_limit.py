"""Async rate-limiting primitives."""

from __future__ import annotations

import asyncio
import random
import time


class TokenBucket:
    """A simple token-bucket rate limiter.

    `capacity` is the maximum number of tokens (max burst).
    `refill_per_sec` is the steady-state rate at which tokens refill.
    """

    def __init__(self, capacity: float, refill_per_sec: float) -> None:
        self._capacity = capacity
        self._refill_per_sec = refill_per_sec
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                self._capacity, self._tokens + elapsed * self._refill_per_sec
            )
            self._last_refill = now
            if self._tokens >= 1:
                self._tokens -= 1
                return True
            # Wait until next token
            wait_sec = (1 - self._tokens) / self._refill_per_sec
        await asyncio.sleep(wait_sec)
        async with self._lock:
            self._tokens -= 1
            return True


class RandomDelay:
    """Sleeps for a random duration between min_sec and max_sec."""

    def __init__(self, min_sec: float, max_sec: float) -> None:
        self._min = min_sec
        self._max = max_sec

    async def wait(self) -> None:
        await asyncio.sleep(random.uniform(self._min, self._max))
