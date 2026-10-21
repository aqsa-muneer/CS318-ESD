import pytest

import ratelimit
from ratelimit import SlidingWindowLimiter


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_limiter_accepts_up_to_limit_then_rejects():
    clock = FakeClock()
    lim = SlidingWindowLimiter(limit=3, window_seconds=60, clock=clock)
    assert [lim.check("a").allowed for _ in range(3)] == [True, True, True]
    d = lim.check("a")
    assert (d.allowed, d.remaining, d.retry_after) == (False, 0, 60)


def test_retry_after_counts_down_and_calls_resume_exactly_then():
    clock = FakeClock()
    lim = SlidingWindowLimiter(limit=2, window_seconds=60, clock=clock)
    lim.check("a")
    clock.now += 20
    lim.check("a")
    clock.now += 10
    assert lim.check("a").retry_after == 30
    clock.now += 29.5
    assert lim.check("a").retry_after == 1
    clock.now += 0.5
    assert lim.check("a").allowed


def test_rejected_requests_are_not_counted():
    clock = FakeClock()
    lim = SlidingWindowLimiter(limit=1, window_seconds=10, clock=clock)
    lim.check("a")
    for _ in range(50):
        lim.check("a")
    clock.now += 10
    assert lim.check("a").allowed


def test_clients_have_separate_budgets():
    lim = SlidingWindowLimiter(limit=1, window_seconds=60, clock=FakeClock())
    assert lim.check("10.0.0.1").allowed
    assert not lim.check("10.0.0.1").allowed
    assert lim.check("10.0.0.2").allowed


@pytest.fixture
def small_limit(monkeypatch):
    clock = FakeClock()
    lim = SlidingWindowLimiter(limit=3, window_seconds=60, clock=clock)
    monkeypatch.setattr(ratelimit, "weather_limiter", lim)
    return clock


def test_endpoint_accepts_then_429_with_retry_after(client, small_limit):
    statuses = []
    for i in range(5):
        r = client.get("/weather", params={"city": "Karachi"})
        statuses.append(r.status_code)
        if i < 3:
            assert r.headers["x-ratelimit-remaining"] == str(2 - i)
    assert statuses == [200, 200, 200, 429, 429]
    assert r.headers["retry-after"] == "60"
    assert "Retry after 60 seconds" in r.json()["detail"]
    small_limit.now += 60
    assert client.get("/weather", params={"city": "Karachi"}).status_code == 200


def test_limited_request_never_reaches_upstream(client, small_limit):
    import fake_upstream
    for _ in range(3):
        client.get("/weather", params={"city": "Karachi"})
    calls = fake_upstream.fake.calls
    assert client.get("/weather", params={"city": "Karachi"}).status_code == 429
    assert fake_upstream.fake.calls == calls


def test_only_weather_is_limited(client, small_limit):
    for _ in range(3):
        client.get("/weather", params={"city": "Karachi"})
    assert client.get("/weather", params={"city": "Karachi"}).status_code == 429
    assert client.get("/forecast", params={"city": "Karachi"}).status_code == 200
    assert client.get("/health").status_code == 200


def test_invalid_requests_also_count(client, small_limit):
    for _ in range(3):
        assert client.get("/weather", params={"city": ""}).status_code == 422
    assert client.get("/weather", params={"city": "Karachi"}).status_code == 429


def test_429_matches_spec(client, small_limit):
    from tests.test_contract import assert_matches_spec
    for _ in range(3):
        client.get("/weather", params={"city": "Karachi"})
    r = client.get("/weather", params={"city": "Karachi"})
    assert_matches_spec("GET", "/weather", r)
