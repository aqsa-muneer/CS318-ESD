import httpx
import pytest

from client import CircuitBreaker, CircuitOpenError, RequestFailed, WeatherApiClient


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_client(handler, **kw):
    sleeps = []
    c = WeatherApiClient("http://test", transport=httpx.MockTransport(handler), jitter=False,
                         sleep=sleeps.append, log=lambda *_: None, **kw)
    c.sleeps = sleeps
    return c


def sequence(*statuses):
    items, seen = list(statuses), []

    def handler(request):
        seen.append(request)
        item = items.pop(0)
        if isinstance(item, Exception):
            raise item
        return httpx.Response(item, json={"ok": item < 400})
    handler.seen = seen
    return handler


def test_success_no_retry():
    h = sequence(200)
    c = make_client(h)
    assert c.get_weather("Karachi").status_code == 200
    assert len(h.seen) == 1 and c.sleeps == []


def test_retries_503_with_exponential_backoff_then_succeeds():
    h = sequence(503, 503, 200)
    c = make_client(h)
    assert c.get_weather("Karachi").status_code == 200
    assert len(h.seen) == 3
    assert c.sleeps == [0.2, 0.4]
    assert c.breaker.failures == 0


def test_timeout_is_retried():
    h = sequence(httpx.ReadTimeout("slow"), 200)
    assert make_client(h).get_weather("X").status_code == 200


def test_gives_up_after_max_attempts():
    h = sequence(503, 503, 503)
    c = make_client(h)
    with pytest.raises(RequestFailed):
        c.get_weather("X")
    assert len(h.seen) == 3


def test_4xx_is_not_retried():
    h = sequence(404)
    c = make_client(h)
    assert c.get_weather("Atlantis").status_code == 404
    assert len(h.seen) == 1


def test_post_retries_reuse_the_same_idempotency_key():
    h = sequence(503, 201)
    c = make_client(h)
    assert c.create_subscription("Karachi", 40).status_code == 201
    keys = {r.headers["idempotency-key"] for r in h.seen}
    assert len(h.seen) == 2 and len(keys) == 1


def test_post_without_key_is_never_retried():
    h = sequence(503, 201)
    c = make_client(h)
    with pytest.raises(RequestFailed, match="not retried"):
        c.request("POST", "/subscriptions", json={"city": "X", "alert_above_c": 1})
    assert len(h.seen) == 1


def test_retry_after_is_honoured_and_is_not_a_breaker_failure():
    def handler(request, _s=iter([429, 200])):
        s = next(_s)
        return httpx.Response(s, headers={"Retry-After": "2"} if s == 429 else {}, json={})
    c = make_client(handler)
    assert c.get_weather("X").status_code == 200
    assert c.sleeps == [2.0] and c.breaker.failures == 0


def test_breaker_opens_blocks_probes_and_recovers():
    clock = Clock()
    br = CircuitBreaker(failure_threshold=3, recovery_timeout=5, clock=clock)
    h = sequence(503, 503, 503, 200)
    c = make_client(h, breaker=br)
    with pytest.raises(RequestFailed):
        c.get_weather("X")
    assert br.state == "open"
    with pytest.raises(CircuitOpenError):
        c.get_weather("X")
    assert len(h.seen) == 3
    clock.now += 5
    assert c.get_weather("X").status_code == 200
    assert br.state == "closed" and len(h.seen) == 4


def test_failed_probe_reopens_breaker_for_another_full_period():
    clock = Clock()
    br = CircuitBreaker(failure_threshold=1, recovery_timeout=5, clock=clock)
    c = make_client(sequence(503, 503), breaker=br, max_attempts=1)
    with pytest.raises(RequestFailed):
        c.get_weather("X")
    clock.now += 5
    with pytest.raises(RequestFailed):
        c.get_weather("X")
    assert br.state == "open"
    clock.now += 4
    with pytest.raises(CircuitOpenError):
        c.get_weather("X")
