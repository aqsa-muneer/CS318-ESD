import os

os.environ.update(WEATHER_FAKE_UPSTREAM="1", FAKE_LATENCY_SCALE="0.2", RATE_LIMIT_REQUESTS="1000")

import logging
import threading
import time

import httpx
import uvicorn

import fake_upstream
import main
from client import CircuitBreaker, CircuitOpenError, RequestFailed, WeatherApiClient
from logging_config import logger

logger.setLevel(logging.CRITICAL)

PORT = 8765
RECOVERY = 3.0
TIMEOUT, ATTEMPTS = 1.0, 3
BREAKER = dict(failure_threshold=3, recovery_timeout=RECOVERY)


def start_server():
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PORT, log_level="critical"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)


def step(title):
    print(f"\n=== {title}")


def call(label, fn):
    print(f"{label}")
    try:
        r = fn()
        print(f"  RESULT: HTTP {r.status_code}")
    except CircuitOpenError as e:
        print(f"  RESULT: BLOCKED by open breaker, no request sent ({e})")
    except RequestFailed as e:
        print(f"  RESULT: FAILED ({e})")


def main_demo():
    fake = fake_upstream.fake
    fake.slow_seconds = 1.6
    start_server()
    api = WeatherApiClient(f"http://127.0.0.1:{PORT}", timeout=TIMEOUT, max_attempts=ATTEMPTS,
                           backoff_base=0.2, backoff_cap=2.0, jitter=False,
                           breaker=CircuitBreaker(**BREAKER),
                           log=lambda m: print(f"{time.strftime('%H:%M:%S')}{m}"))
    print(f"Settings: timeout={TIMEOUT}s/attempt, max attempts={ATTEMPTS} (2 retries), "
          f"backoff=0.2s*2^n (no jitter in demo), breaker: open after {BREAKER['failure_threshold']} "
          f"consecutive failures, recovery={RECOVERY}s (demo; default 5s)")

    step("1. Healthy: several successful calls")
    call("GET /weather Karachi", lambda: api.get_weather("Karachi"))
    call("GET /weather London", lambda: api.get_weather("London"))
    call("POST /subscriptions Karachi", lambda: api.create_subscription("Karachi", 40))

    step("2. Transient failures: first a TIMEOUT, then a 503, then success (retry + backoff)")
    fake.queue.extend(["slow", "fail"])
    call("GET /weather Tokyo", lambda: api.get_weather("Tokyo"))
    print("POST with Idempotency-Key: first attempt hits a 503, retry reuses the same key")
    fake.queue.extend(["fail"])
    call("POST /subscriptions Lahore", lambda: api.create_subscription("Lahore", 38))
    subs = httpx.get(f"http://127.0.0.1:{PORT}/subscriptions").json()
    print(f"  server now holds {len(subs)} subscriptions: {[s['city'] for s in subs]}  (no duplicate)")

    step("3. Outage: repeated failures open the circuit breaker")
    fake.mode = "fail"
    call("GET /weather Lahore", lambda: api.get_weather("Lahore"))
    print(f"  breaker state = {api.breaker.state}")
    before = fake.calls
    print("Calls while the breaker is open:")
    call("GET /weather Karachi", lambda: api.get_weather("Karachi"))
    call("POST /subscriptions London", lambda: api.create_subscription("London", 30))
    call("GET /weather Tokyo", lambda: api.get_weather("Tokyo"))
    print(f"  upstream calls made by these 3 blocked calls: {fake.calls - before}")

    step(f"4. Recovery: dependency restored, wait {RECOVERY}s, breaker probes and closes")
    fake.mode = "ok"
    time.sleep(RECOVERY + 0.2)
    call("GET /weather Karachi   (half-open probe)", lambda: api.get_weather("Karachi"))
    print(f"  breaker state = {api.breaker.state}")
    call("GET /weather London", lambda: api.get_weather("London"))


if __name__ == "__main__":
    main_demo()