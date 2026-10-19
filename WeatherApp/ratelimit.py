"""Sliding-window rate limiter (single instance, in memory).

Policy applied to GET /weather:
  * LIMIT    : RATE_LIMIT_REQUESTS requests (default 60)
  * WINDOW   : per rolling RATE_LIMIT_WINDOW_SECONDS (default 60 s)
  * SHARED BY: everyone with the same client IP (request.client.host).
               X-Forwarded-For is NOT trusted, so callers behind one NAT/proxy
               share one budget.
  * ALLOWED AGAIN: when the oldest counted request leaves the window; the
               429 response says how many seconds that is in `Retry-After`.
"""
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response


@dataclass
class Decision:
    allowed: bool
    remaining: int
    retry_after: int  # whole seconds; 0 when allowed


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, clock=time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock          # injectable so tests need no real sleeping
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()  # FastAPI runs sync endpoints in a thread pool

    def check(self, key: str) -> Decision:
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            # Forget requests that have left the window
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) < self.limit:
                hits.append(now)
                self._gc(now)
                return Decision(True, self.limit - len(hits), 0)
            # Full: allowed again when the oldest request expires. Rounded UP
            # because Retry-After is whole seconds (rounding down = retry too early).
            retry = math.ceil(hits[0] + self.window - now)
            return Decision(False, 0, max(1, retry))

    def _gc(self, now: float) -> None:
        """Drop idle clients so the dict cannot grow without bound."""
        if len(self._hits) < 10_000:
            return
        for k in [k for k, h in self._hits.items() if not h or h[-1] <= now - self.window]:
            del self._hits[k]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


weather_limiter = SlidingWindowLimiter(
    limit=int(os.getenv("RATE_LIMIT_REQUESTS", "60")),
    window_seconds=float(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")),
)


def rate_limit_weather(request: Request, response: Response) -> None:
    """FastAPI dependency: runs BEFORE the endpoint, so a limited request never
    reaches Open-Meteo. Every request to /weather counts, valid or not."""
    client = request.client.host if request.client else "unknown"
    d = weather_limiter.check(client)
    limit_headers = {
        "X-RateLimit-Limit": str(weather_limiter.limit),
        "X-RateLimit-Remaining": str(d.remaining),
    }
    if d.allowed:
        response.headers.update(limit_headers)  # merged into the 200 response
        return
    raise HTTPException(
        status_code=429,
        detail=(f"Rate limit exceeded: {weather_limiter.limit} requests per "
                f"{weather_limiter.window:g} seconds. Retry after {d.retry_after} seconds."),
        headers={"Retry-After": str(d.retry_after), **limit_headers},
    )