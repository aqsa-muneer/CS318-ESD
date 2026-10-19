"""Tiny in-memory repository for the write operation used to demonstrate idempotency"""
import threading
import uuid
from datetime import datetime, timezone


class SubscriptionRepository:
    def __init__(self):
        self._items: list[dict] = []
        self._lock = threading.Lock()

    def create(self, city: str, country: str, alert_above_c: float) -> dict:
        record = {
            "id": str(uuid.uuid4()),
            "city": city,
            "country": country,
            "alert_above_c": alert_above_c,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._items.append(record)
        return record

    def all(self) -> list[dict]:
        with self._lock:
            return list(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()