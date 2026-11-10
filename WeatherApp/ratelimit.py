import math
import os
import time
import uuid
from dataclasses import dataclass
import redis
from fastapi import HTTPException, Request, Response
from logging_config import logger
from redis_client import get_redis

# KEYS[1] = this client's sorted set
# ARGV    = now, window_seconds, limit, unique member id for this request
# Returns {allowed (1/0), remaining, retry_after_seconds_as_string}
# (retry is returned as a string because Redis truncates Lua numbers to ints)
_SLIDING_WINDOW_LUA = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])

-- Forget requests that have left the window (same <= rule as A2)
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)

local count = redis.call('ZCARD', key)
if count < limit then
  redis.call('ZADD', key, now, ARGV[4])
  -- Idle clients disappear on their own (replaces A2's _gc)
  redis.call('PEXPIRE', key, math.ceil(window * 1000))
  return {1, limit - count - 1, '0'}
end

-- Full: allowed again when the oldest request expires
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
return {0, 0, tostring(tonumber(oldest[2]) + window - now)}
"""


@dataclass
class Decision:
    allowed: bool
    remaining: int
    retry_after: int  # whole seconds; 0 when allowed


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, client: redis.Redis | None = None,
                 clock=time.time, prefix: str = "ratelimit:weather:"):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock          # still injectable for tests
        self._prefix = prefix
        self._redis = client or get_redis()
        self._script = self._redis.register_script(_SLIDING_WINDOW_LUA)

    def check(self, key: str) -> Decision:
        now = self._clock()
        member = f"{now}:{uuid.uuid4().hex}"   # unique, so equal timestamps don't collide
        allowed, remaining, retry = self._script(
            keys=[self._prefix + key], args=[now, self.window, self.limit, member])
        if int(allowed) == 1:
            return Decision(True, int(remaining), 0)
        # Rounded UP because Retry-After is whole seconds (rounding down = retry too early).
        return Decision(False, 0, max(1, math.ceil(float(retry))))

    def reset(self) -> None:
        for k in self._redis.scan_iter(match=self._prefix + "*"):
            self._redis.delete(k)


weather_limiter = SlidingWindowLimiter(
    limit=int(os.getenv("RATE_LIMIT_REQUESTS", "60")),
    window_seconds=float(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")),
)

TRUSTED_PROXIES = {ip.strip() for ip in os.getenv("TRUSTED_PROXIES", "").split(",") if ip.strip()}


def client_ip(request: Request) -> str:
    """The real caller's IP. X-Forwarded-For is trusted ONLY from our own proxy."""
    peer = request.client.host if request.client else "unknown"
    if peer in TRUSTED_PROXIES:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[-1].strip()   # the entry Envoy itself appended
    return peer


def rate_limit_weather(request: Request, response: Response) -> None:
    """FastAPI dependency: runs BEFORE the endpoint, so a limited request never
    reaches Open-Meteo. Every request to /weather counts, valid or not."""
    client = client_ip(request)
    try:
        d = weather_limiter.check(client)
    except redis.RedisError:
        logger.warning("Rate limiter unavailable, allowing request", extra={
            "request_id": getattr(request.state, "request_id", None), "endpoint": "/weather"})
        return
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