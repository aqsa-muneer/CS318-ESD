import threading
import time

import fake_upstream

BODY = {"city": "Karachi", "alert_above_c": 40}


def post(client, key="key-12345678", body=BODY):
    return client.post("/subscriptions", json=body, headers={"Idempotency-Key": key})


def test_first_request_creates(client):
    r = post(client)
    assert r.status_code == 201
    assert "idempotent-replayed" not in r.headers
    assert len(client.get("/subscriptions").json()) == 1


def test_repeated_request_is_replayed_not_duplicated(client):
    first = post(client)
    second = post(client)
    third = post(client)
    assert second.status_code == 201
    assert second.headers["idempotent-replayed"] == "true"
    assert first.json() == second.json() == third.json()
    assert len(client.get("/subscriptions").json()) == 1


def test_same_key_different_body_is_rejected(client):
    post(client)
    r = post(client, body={"city": "Karachi", "alert_above_c": 45})
    assert r.status_code == 422
    assert r.json()["detail"][0]["type"] == "idempotency_key_reuse"
    assert len(client.get("/subscriptions").json()) == 1


def test_missing_or_malformed_key_is_422(client):
    assert client.post("/subscriptions", json=BODY).status_code == 422
    assert post(client, key="short").status_code == 422
    assert post(client, key="has spaces in it!").status_code == 422


def test_invalid_body_is_422_and_creates_nothing(client):
    assert post(client, body={"city": "", "alert_above_c": 40}).status_code == 422
    assert post(client, key="key-87654321", body={"city": "X", "alert_above_c": 40, "extra": 1}).status_code == 422
    assert client.get("/subscriptions").json() == []


def test_failure_releases_key_so_retry_with_same_key_works(client):
    fake_upstream.fake.queue.append("fail")
    assert post(client).status_code == 503
    assert client.get("/subscriptions").json() == []
    r = post(client)
    assert r.status_code == 201
    assert len(client.get("/subscriptions").json()) == 1


def test_unknown_city_404_also_releases_key(client):
    bad = {"city": "Atlantis", "alert_above_c": 40}
    assert post(client, body=bad).status_code == 404
    assert post(client, body=bad).status_code == 404


def test_concurrent_duplicate_gets_409_then_replay(client):
    fake_upstream.fake.gate = gate = threading.Event()
    results = {}
    t = threading.Thread(target=lambda: results.update(first=post(client)))
    t.start()
    deadline = time.time() + 5
    while fake_upstream.fake.geocode_calls == 0 and time.time() < deadline:
        time.sleep(0.01)
    dup = post(client)
    assert dup.status_code == 409
    assert dup.headers["retry-after"] == "1"
    gate.set()
    t.join(5)
    assert results["first"].status_code == 201
    assert post(client).headers["idempotent-replayed"] == "true"
    assert len(client.get("/subscriptions").json()) == 1
