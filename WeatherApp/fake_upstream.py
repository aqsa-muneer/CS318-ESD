"""Offline stand-in for Open-Meteo, used by tests, demos and load scripts.

It replaces ONLY the `httpx` name inside weather.py, so your real parsing,
timing (WEATHER_API_DURATION) and error handling still run unchanged.
Enable in a server with WEATHER_FAKE_UPSTREAM=1.

Magic city names (work across processes, so the load script is repeatable):
  Failville -> upstream unreachable  (weather.py raises -> API 503 / gRPC UNAVAILABLE)
  Slowville -> upstream answers after `slow_seconds`
Any city not in CITIES is "not found" (empty geocoding result).

In-process controls (used by the caller demo/tests):
  fake.mode   : "ok" | "fail" | "slow"   applied to every call
  fake.queue  : behaviours consumed one per GEOCODING call (first call of a request)
  fake.gate   : threading.Event; geocoding blocks until it is set
"""
import os
import threading
import time
import types
from collections import deque
from datetime import date, timedelta

import httpx

# name -> (country, region, lat, lon, timezone, base latency in ms, temp)
CITIES = {
    "karachi": ("Pakistan", "Sindh", 24.86, 67.01, "Asia/Karachi", 60, 33.0),
    "lahore": ("Pakistan", "Punjab", 31.55, 74.34, "Asia/Karachi", 120, 29.5),
    "london": ("United Kingdom", "England", 51.51, -0.13, "Europe/London", 250, 12.4),
    "tokyo": ("Japan", "Tokyo", 35.69, 139.69, "Asia/Tokyo", 400, 18.2),
    "new york": ("United States", "New York", 40.71, -74.01, "America/New_York", 700, 15.1),
    # Resolves normally; the magic name additionally makes the upstream slow (see below)
    "slowville": ("Pakistan", "Sindh", 25.0, 68.0, "Asia/Karachi", 100, 30.0),
}


class FakeUpstream:
    def __init__(self):
        self.latency_scale = float(os.getenv("FAKE_LATENCY_SCALE", "1"))
        self.slow_seconds = 1.6
        self.reset()

    def reset(self):
        self.mode = "ok"
        self.queue = deque()
        self.gate = None
        self.calls = 0           # every upstream HTTP call
        self.geocode_calls = 0
        self._lock = threading.Lock()

    # Same signature weather.py uses: httpx.get(url, params=..., timeout=...)
    def get(self, url, params=None, timeout=None):
        params = params or {}
        request = httpx.Request("GET", url, params={"x": "1"})
        is_geocode = "geocoding" in url
        with self._lock:
            self.calls += 1
            if is_geocode:
                self.geocode_calls += 1
                behaviour = self.queue.popleft() if self.queue else self.mode
            else:
                behaviour = self.mode
        city = str(params.get("name", "")).lower()
        if city == "failville":
            behaviour = "fail"
        elif city == "slowville":
            behaviour = "slow"

        if is_geocode and self.gate is not None:
            self.gate.wait(10)
        if behaviour == "fail":
            raise httpx.ConnectError("fake upstream is down", request=request)
        if behaviour == "slow":
            time.sleep(self.slow_seconds)
        elif behaviour == "http_error":
            return httpx.Response(500, request=request)

        if is_geocode:
            info = CITIES.get(city)
            if info is None:
                time.sleep(0.02 * self.latency_scale)
                return httpx.Response(200, json={"generationtime_ms": 0.2}, request=request)
            time.sleep(info[5] / 1000 * self.latency_scale / 2)
            return httpx.Response(200, request=request, json={"results": [{
                "name": city.title(), "country": info[0], "admin1": info[1],
                "latitude": info[2], "longitude": info[3], "timezone": info[4]}]})

        # Forecast API: find which city the coordinates belong to
        info = next((c for c in CITIES.values() if c[2] == params.get("latitude")), CITIES["karachi"])
        time.sleep(info[5] / 1000 * self.latency_scale / 2)
        if "current" in params:
            return httpx.Response(200, request=request, json={
                "current_units": {"temperature_2m": "°C", "relative_humidity_2m": "%",
                                  "apparent_temperature": "°C", "precipitation": "mm",
                                  "cloud_cover": "%", "wind_speed_10m": "km/h",
                                  "wind_direction_10m": "°"},
                "current": {"time": "2026-10-20T10:00", "temperature_2m": info[6],
                            "relative_humidity_2m": 55, "apparent_temperature": info[6] + 1.5,
                            "precipitation": 0.0, "weather_code": 1, "cloud_cover": 20,
                            "wind_speed_10m": 11.5, "wind_direction_10m": 240}})
        days = [(date(2026, 10, 20) + timedelta(days=i)).isoformat() for i in range(5)]
        return httpx.Response(200, request=request, json={
            "daily_units": {"temperature_2m_max": "°C", "temperature_2m_min": "°C",
                            "precipitation_probability_max": "%"},
            "daily": {"time": days, "weather_code": [0, 1, 2, 61, 3],
                      "temperature_2m_max": [info[6] + i for i in range(5)],
                      "temperature_2m_min": [info[6] - 8 + i for i in range(5)],
                      "precipitation_probability_max": [0, 5, 10, 70, 20]}})


fake = FakeUpstream()
_original = None


def install():
    """Point weather.py at the fake. Safe to call more than once."""
    global _original
    import weather
    if _original is None:
        _original = weather.httpx
    weather.httpx = types.SimpleNamespace(
        get=fake.get, RequestError=httpx.RequestError, HTTPStatusError=httpx.HTTPStatusError)


def uninstall():
    global _original
    import weather
    if _original is not None:
        weather.httpx, _original = _original, None