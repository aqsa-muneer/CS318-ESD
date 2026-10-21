"""A small service-to-service caller for the Weather API.

Reliability features (each one explained where it is implemented):
  * TIMEOUT          every attempt gets a hard time limit
  * LIMITED RETRY    exponential backoff, only for transient failures, and only
                     when repeating the call cannot cause a duplicate effect
  * CIRCUIT BREAKER  after repeated failures stop calling for a while, then probe

Run a few calls against a running server:
    python client.py --base-url http://localhost:8000
"""
import argparse
import random
import threading
import time
import uuid

import httpx

RETRYABLE_STATUS = {502, 503, 504}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class CircuitOpenError(Exception):
    """Raised without sending any request while the breaker is open."""

    def __init__(self, retry_in: float):
        super().__init__(f"circuit open; next probe allowed in {retry_in:.1f}s")
        self.retry_in = retry_in


class RequestFailed(Exception):
    """All attempts were used up (or the call was not safe to retry)."""


class CircuitBreaker:
    """closed    -> normal; counts CONSECUTIVE failed attempts
    open      -> calls are refused instantly
    half_open -> after the recovery period ONE probe call is allowed:
                 success -> closed, failure -> open again for another full period
    """
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, failure_threshold=3, recovery_timeout=5.0, clock=time.monotonic):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._clock = clock
        self._lock = threading.Lock()
        self.state = self.CLOSED
        self.failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False

    def before_call(self):
        with self._lock:
            if self.state == self.OPEN:
                waited = self._clock() - self._opened_at
                if waited < self.recovery_timeout:
                    raise CircuitOpenError(self.recovery_timeout - waited)
                self.state, self._probe_in_flight = self.HALF_OPEN, False
            if self.state == self.HALF_OPEN:
                if self._probe_in_flight:
                    raise CircuitOpenError(0.0)
                self._probe_in_flight = True

    def record_success(self):
        with self._lock:
            self.state, self.failures, self._probe_in_flight = self.CLOSED, 0, False

    def record_failure(self):
        with self._lock:
            self._probe_in_flight = False
            self.failures += 1
            if self.state == self.HALF_OPEN or self.failures >= self.failure_threshold:
                self.state, self._opened_at = self.OPEN, self._clock()


class WeatherApiClient:
    def __init__(self, base_url, *, timeout=1.0, max_attempts=3, backoff_base=0.2,
                 backoff_cap=2.0, jitter=True, max_retry_after=5.0, breaker=None,
                 transport=None, sleep=time.sleep, log=print):
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)
        self.timeout, self.max_attempts = timeout, max_attempts
        self.backoff_base, self.backoff_cap, self.jitter = backoff_base, backoff_cap, jitter
        self.max_retry_after = max_retry_after
        self.breaker = breaker or CircuitBreaker()
        self._sleep, self._log = sleep, log

    def get_weather(self, city):
        return self.request("GET", "/weather", params={"city": city})

    def create_subscription(self, city, alert_above_c, idempotency_key=None):
        return self.request("POST", "/subscriptions",
                            json={"city": city, "alert_above_c": alert_above_c},
                            idempotency_key=idempotency_key or str(uuid.uuid4()))

    def close(self):
        self._http.close()

    def _backoff(self, attempt):
        delay = min(self.backoff_cap, self.backoff_base * 2 ** (attempt - 1))
        return delay / 2 + random.uniform(0, delay / 2) if self.jitter else delay

    def request(self, method, path, *, params=None, json=None, idempotency_key=None):
        retry_safe = method in SAFE_METHODS or idempotency_key is not None
        attempts = self.max_attempts if retry_safe else 1
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        label = f"{method} {path}" + (f"?city={params['city']}" if params else "")

        for attempt in range(1, attempts + 1):
            self.breaker.before_call()
            try:
                resp = self._http.request(method, path, params=params, json=json, headers=headers)
            except httpx.TransportError as exc:
                self.breaker.record_failure()
                outcome, retry_wait = type(exc).__name__, None
            else:
                if resp.status_code in RETRYABLE_STATUS:
                    self.breaker.record_failure()
                    outcome, retry_wait = f"{resp.status_code} (server failure)", None
                elif resp.status_code in (429, 409) and "retry-after" in resp.headers and retry_safe:
                    self.breaker.record_success()
                    wait = float(resp.headers["retry-after"])
                    if wait > self.max_retry_after:
                        self._log(f"  attempt {attempt}/{attempts} -> {resp.status_code}; "
                                  f"Retry-After {wait:g}s is too long, giving up")
                        return resp
                    outcome, retry_wait = f"{resp.status_code} (Retry-After {wait:g}s)", wait
                else:
                    self.breaker.record_success()
                    self._log(f"  attempt {attempt}/{attempts} -> {resp.status_code}  "
                              f"[breaker {self.breaker.state}, failures {self.breaker.failures}]")
                    return resp

            self._log(f"  attempt {attempt}/{attempts} -> {outcome}  "
                      f"[breaker {self.breaker.state}, failures {self.breaker.failures}]")
            if attempt == attempts:
                why = "" if retry_safe else " (not retried: write without Idempotency-Key)"
                raise RequestFailed(f"{label} failed after {attempt} attempt(s): {outcome}{why}")
            wait = retry_wait if retry_wait is not None else self._backoff(attempt)
            self._log(f"  waiting {wait:.2f}s before retry")
            self._sleep(wait)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Make several calls to the Weather API")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--cities", nargs="+", default=["Karachi", "Lahore", "London"])
    args = ap.parse_args()
    api = WeatherApiClient(args.base_url)
    for name in args.cities:
        print(f"GET weather {name}")
        try:
            r = api.get_weather(name)
            body = r.json()
            print("  ->", r.status_code, body.get("current_weather", {}).get("temperature", body))
        except (RequestFailed, CircuitOpenError) as e:
            print("  -> FAILED:", e)
    print("POST subscription")
    r = api.create_subscription(args.cities[0], 40)
    print("  ->", r.status_code, r.json())
