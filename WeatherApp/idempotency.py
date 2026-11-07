import hashlib
import json
from dataclasses import dataclass

import redis

from redis_client import get_redis

PROCESSING_LEASE_SECONDS = 60  # a crashed/hung attempt cannot block its key forever


@dataclass
class Decision:
    kind: str  # "new" | "replay" | "in_progress" | "key_reuse"
    status: int | None = None
    body: dict | None = None


def fingerprint(payload: dict) -> str:
    """Stable hash of the request body, so we can detect key reuse with a different body."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyStore:
    def __init__(self, client: redis.Redis | None = None, ttl_seconds: float = 24 * 3600,
                 prefix: str = "idem:subscriptions:"):
        self.ttl = int(ttl_seconds)
        self._prefix = prefix
        self._redis = client or get_redis()

    def _k(self, key: str) -> str:
        return self._prefix + key

    def begin(self, key: str, fp: str) -> Decision:
        k = self._k(key)
        processing = json.dumps({"fp": fp, "state": "processing"})
        for _ in range(3):
            # Atomic "claim if nobody has it": only one replica can win.
            if self._redis.set(k, processing, nx=True, ex=PROCESSING_LEASE_SECONDS): # nx=True --> “only set this if the key doesn’t exist yet”, and Redis does the check and the write as one step. If A and B both try to claim K at the same instant, exactly one gets True (→ "new") and the other gets False.
                return Decision("new")
            raw = self._redis.get(k)
            if raw is None:
                continue  # it expired between our SET and GET -> try to claim again
            entry = json.loads(raw)
            if entry["fp"] != fp:
                return Decision("key_reuse")
            if entry["state"] == "processing":
                return Decision("in_progress")
            return Decision("replay", entry["status"], entry["body"])
        return Decision("in_progress")  # extremely unlikely churn: ask the client to retry

    def finish(self, key: str, status: int, body: dict, fp: str) -> None:
        # fp is passed in (A2 read it from the in-memory entry) so the record stays
        # correct even if the lease expired while the operation was running.
        self._redis.set(self._k(key),
                        json.dumps({"fp": fp, "state": "done", "status": status, "body": body}),
                        ex=self.ttl)

    def abandon(self, key: str) -> None:
        """Release a key whose attempt failed before any side effect happened."""
        self._redis.delete(self._k(key))

    def clear(self) -> None:
        for k in self._redis.scan_iter(match=self._prefix + "*"):
            self._redis.delete(k)