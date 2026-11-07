"""Subscription repository, shared by ALL replicas through Redis (A3).

A2 kept subscriptions in a Python list inside each process. With three replicas
that means three separate lists: a subscription created on A does not exist on
B or C, so GET /subscriptions would return a different answer depending on which
replica Envoy picked.

Now every replica reads and writes ONE Redis list:
  key "subscriptions" -> [json(record1), json(record2), ...]   (oldest first)

RPUSH is a single atomic Redis command, so it replaces A2's threading.Lock --
and, unlike that lock, it also protects against the OTHER replicas, which a lock
inside one process never could.

The interface (create / all / clear) and the record shape are unchanged, so
main.py does not need to change.
"""
import json
import uuid
from datetime import datetime, timezone

import redis

from redis_client import get_redis


class SubscriptionRepository:
    def __init__(self, client: redis.Redis | None = None, key: str = "subscriptions"):
        self._redis = client or get_redis()
        self._key = key

    def create(self, city: str, country: str, alert_above_c: float) -> dict:
        record = {
            # uuid4 is still safe across replicas: no shared counter needed,
            # and two replicas will never produce the same id.
            "id": str(uuid.uuid4()),
            "city": city,
            "country": country,
            "alert_above_c": alert_above_c,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._redis.rpush(self._key, json.dumps(record))
        return record

    def all(self) -> list[dict]:
        return [json.loads(item) for item in self._redis.lrange(self._key, 0, -1)]

    def clear(self) -> None:
        self._redis.delete(self._key)
