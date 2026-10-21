import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

import main

SPEC = yaml.safe_load(Path("openapi.yaml").read_text(encoding="utf-8"))


def assert_matches_spec(method, path, response):
    declared = SPEC["paths"][path][method.lower()]["responses"]
    status = str(response.status_code)
    assert status in declared, f"{method} {path} returned {status}; spec declares {list(declared)}"
    spec_resp = declared[status]
    for name, header in spec_resp.get("headers", {}).items():
        if header.get("required"):
            assert name.lower() in response.headers, f"{status} is missing required header {name}"
    content = spec_resp.get("content", {}).get("application/json")
    if content:
        schema = {**content["schema"], "components": SPEC["components"]}
        Draft202012Validator(schema).validate(response.json())


def test_committed_spec_matches_code():
    generated = json.loads(json.dumps(main.app.openapi()))
    assert SPEC == generated, "openapi.yaml is stale: run `python -m scripts.export_openapi`"


def test_weather_success_matches_spec(client):
    r = client.get("/weather", params={"city": "Karachi"})
    assert r.status_code == 200
    assert_matches_spec("GET", "/weather", r)


def test_weather_not_found_matches_spec(client):
    r = client.get("/weather", params={"city": "Atlantis"})
    assert r.status_code == 404
    assert_matches_spec("GET", "/weather", r)


def test_weather_upstream_down_matches_spec(client):
    r = client.get("/weather", params={"city": "Failville"})
    assert r.status_code == 503
    assert_matches_spec("GET", "/weather", r)


def test_weather_invalid_input_matches_spec(client):
    r = client.get("/weather", params={"city": ""})
    assert r.status_code == 422
    assert_matches_spec("GET", "/weather", r)


def test_forecast_matches_spec(client):
    r = client.get("/forecast", params={"city": "London"})
    assert r.status_code == 200
    assert len(r.json()["forecast"]) == 5
    assert_matches_spec("GET", "/forecast", r)


def test_subscription_create_and_list_match_spec(client):
    r = client.post("/subscriptions", json={"city": "Karachi", "alert_above_c": 40},
                    headers={"Idempotency-Key": "contract-test-1"})
    assert r.status_code == 201
    assert_matches_spec("POST", "/subscriptions", r)
    assert_matches_spec("GET", "/subscriptions", client.get("/subscriptions"))


def test_every_route_in_code_is_in_spec():
    code_routes = {(m.lower(), r.path) for r in main.app.routes
                   if hasattr(r, "methods") and r.path not in ("/openapi.json", "/docs", "/redoc",
                                                              "/docs/oauth2-redirect")
                   for m in r.methods if m not in ("HEAD",)}
    spec_routes = {(m, p) for p, ms in SPEC["paths"].items() for m in ms}
    assert code_routes == spec_routes
