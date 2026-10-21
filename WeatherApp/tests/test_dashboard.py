import json
import re
from pathlib import Path

import pytest
import yaml


def dashboard():
    return json.loads(Path("grafana/dashboards/weather-observability.json").read_text())


def test_every_promql_expression_parses():
    promql_parser = pytest.importorskip("promql_parser")
    exprs = [t["expr"] for p in dashboard()["panels"] for t in p.get("targets", [])]
    assert len(exprs) >= 12
    for e in exprs:
        promql_parser.parse(e.replace("$__rate_interval", "1m"))


def test_dashboard_only_uses_metrics_the_code_defines():
    defined = set()
    for f in ("metrics.py", "grpc_metrics.py"):
        defined |= set(re.findall(r'"(weather_[a-z_]+)"', Path(f).read_text()))
    used = set()
    for p in dashboard()["panels"]:
        for t in p.get("targets", []):
            used |= set(re.findall(r"\b(weather_[a-z_]+)", t["expr"]))
    used = {re.sub(r"_(bucket|sum|count)$", "", m) for m in used}
    assert used <= defined, f"dashboard uses undefined metrics: {used - defined}"


def test_prometheus_scrapes_both_services():
    cfg = yaml.safe_load(Path("prometheus/prometheus.yml").read_text())
    jobs = {j["job_name"]: j["static_configs"][0]["targets"] for j in cfg["scrape_configs"]}
    assert "host.docker.internal:8000" in jobs["weather-app"]
    assert "host.docker.internal:9101" in jobs["weather-grpc"]


def test_panel_datasources_match_provisioned_uid():
    ds = yaml.safe_load(Path("grafana/provisioning/datasources/prometheus.yml").read_text())
    uid = ds["datasources"][0]["uid"]
    assert all(p["datasource"]["uid"] == uid for p in dashboard()["panels"] if p["type"] != "row")
