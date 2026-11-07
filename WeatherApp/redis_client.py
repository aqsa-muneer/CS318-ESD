"""connection is created once, on first use, and then reused.
Rate-limit counters, idempotency records and subscriptions live in Redis so
that ALL replicas see the same state. redis-py does not connect until the
first command, so importing this module never fails even if Redis is not up yet.
"""
import os

import redis


_client = None 
def get_redis() -> redis.Redis: #every module that calls this function shares one connection pool instead of opening its own.
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"), #0 is the first of Redis's numbered databases
            decode_responses=True,       # get str back instead of bytes (Redis stores raw bytes)
            socket_timeout=1.0,          # a stuck Redis fails fast instead of
            socket_connect_timeout=1.0,  # hanging every request
        )
    return _client
