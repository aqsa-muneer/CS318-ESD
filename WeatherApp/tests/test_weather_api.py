from prometheus_client import REGISTRY


def counter(endpoint, status, method="GET"):
    return REGISTRY.get_sample_value(
        "weather_http_requests_total", {"method": method, "endpoint": endpoint, "status": status}) or 0


def test_valid_request(client):
    r = client.get("/weather", params={"city": "  Tokyo  "})
    assert r.status_code == 200
    assert r.json()["location"]["city"] == "Tokyo"
    assert r.json()["current_weather"]["condition"] == "Mainly clear"


def test_invalid_input_is_rejected_before_calling_upstream(client):
    import fake_upstream
    for bad in ["", "   ", "x" * 101]:
        r = client.get("/weather", params={"city": bad})
        assert r.status_code == 422, bad
    r = client.get("/weather")
    assert r.status_code == 422
    assert fake_upstream.fake.calls == 0


def test_unknown_city_is_404_not_503(client):
    assert client.get("/weather", params={"city": "Atlantis"}).json() == {"detail": "City not found"}


def test_unhandled_exception_is_counted_as_500(client):
    before = counter("/test-error", "500")
    assert client.get("/test-error").status_code == 500
    assert counter("/test-error", "500") == before + 1


def test_unmatched_urls_share_one_metric_label(client):
    before = counter("unmatched", "404")
    for i in range(5):
        client.get(f"/random-scanner-path-{i}")
    assert counter("unmatched", "404") == before + 5
    assert REGISTRY.get_sample_value(
        "weather_http_requests_total",
        {"method": "GET", "endpoint": "/random-scanner-path-0", "status": "404"}) is None
