import os

os.environ.update(WEATHER_FAKE_UPSTREAM="1", FAKE_LATENCY_SCALE="0", RATE_LIMIT_REQUESTS="5",
                  RATE_LIMIT_WINDOW_SECONDS="10")

import logging
import threading
import time

import httpx
import uvicorn

import main
from logging_config import logger

logger.setLevel(logging.CRITICAL)
BASE = "http://127.0.0.1:8766"


def show(i, r):
    extra = f"Retry-After={r.headers['retry-after']}s " if r.status_code == 429 else ""
    print(f"request {i:2}: HTTP {r.status_code}  limit={r.headers.get('x-ratelimit-limit')} "
          f"remaining={r.headers.get('x-ratelimit-remaining')} {extra}")


if __name__ == "__main__":
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=8766, log_level="critical"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)

    print("Limit: 5 requests / 10 s, shared by everyone with the same client IP\n")
    for i in range(1, 8):
        show(i, httpx.get(f"{BASE}/weather", params={"city": "Karachi"}))
    r = httpx.get(f"{BASE}/weather", params={"city": "Karachi"})
    wait = int(r.headers["retry-after"])
    print(f"\n429 body: {r.json()}")
    print(f"/forecast is not limited: HTTP {httpx.get(f'{BASE}/forecast', params={'city': 'Karachi'}).status_code}")
    print(f"\nWaiting Retry-After = {wait}s ...")
    time.sleep(wait)
    show(9, httpx.get(f"{BASE}/weather", params={"city": "Karachi"}))