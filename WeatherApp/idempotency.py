"""Idempotency-Key support for POST endpoints (single instance, in memory).

Contract (modelled on draft-ietf-httpapi-idempotency-key-header):
  new key                          -> run the operation once, remember the result
  same key + same body, finished   -> replay the stored response, no new effect
  same key + same body, running    -> 409 (the first attempt is still in flight)
  same key + DIFFERENT body        -> 422 (a key identifies exactly one request)

Only successful results are remembered. If the operation fails before its side
effect, the key is released so the caller can retry with the same key.
"""
import copy
import hashlib
import json
import threading
import time
from dataclasses import dataclass

PROCESSING_LEASE_SECONDS = 60  # a crashed/hung attempt cannot block its key forever

@dataclass
class Decision:
    kind: str  # "new" | "replay" | "in_progress" | "key_reuse"
    status: int | None = None
    body: dict | None = None


@dataclass
class _Entry:
    fingerprint: str
    state: str  # "processing" | "done"
    expires_at: float
    status: int | None = None
    body: dict | None = None


def fingerprint(payload: dict) -> str:
    """Stable hash of the request body, so we can detect key reuse with a different body."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyStore:
    def __init__(self, ttl_seconds: float = 24 * 3600, max_entries: int = 10_000, clock=time.monotonic):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def begin(self, key: str, fp: str) -> Decision:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at <= now:
                entry = None  # expired -> treat as never seen
            if entry is None:
                self._evict(now)
                self._entries[key] = _Entry(fp, "processing", now + PROCESSING_LEASE_SECONDS)
                return Decision("new")
            if entry.fingerprint != fp:
                return Decision("key_reuse")
            if entry.state == "processing":
                return Decision("in_progress")
            return Decision("replay", entry.status, copy.deepcopy(entry.body))

    def finish(self, key: str, status: int, body: dict) -> None:
        with self._lock:
            entry = self._entries[key]
            entry.state, entry.status, entry.body = "done", status, copy.deepcopy(body)
            entry.expires_at = self._clock() + self.ttl

    def abandon(self, key: str) -> None:
        """Release a key whose attempt failed before any side effect happened."""
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _evict(self, now: float) -> None:
        # Keeps memory bounded: drop expired entries first, then the oldest.
        if len(self._entries) < self.max_entries:
            return
        for k in [k for k, e in self._entries.items() if e.expires_at <= now]:
            del self._entries[k]
        while len(self._entries) >= self.max_entries:
            del self._entries[next(iter(self._entries))]