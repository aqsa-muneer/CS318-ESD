import json

DS = {"type": "prometheus", "uid": "weather-prometheus"}
HTTP = 'endpoint="/weather"'
SERVER_ERRORS = "UNAVAILABLE|INTERNAL|UNKNOWN|DEADLINE_EXCEEDED|DATA_LOSS"

_next_id = [0]

def target(expr, legend, ref="A", fmt=None):
    t = {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref}
    if fmt:
        t["format"] = fmt
    return t

def panel(title, targets, x, y, w=12, h=8, unit="short", kind="timeseries", desc="", extra=None):
    _next_id[0] += 1
    p = {"id": _next_id[0], "type": kind, "title": title, "description": desc, "datasource": DS,
         "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": targets,
         "fieldConfig": {"defaults": {"unit": unit}, "overrides": []}}
    p.update(extra or {})
    return p

def row(title, y):
    _next_id[0] += 1
    return {"id": _next_id[0], "type": "row", "title": title, "collapsed": False,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}

def heatmap(title, expr, x, y, desc):
    return panel(title, [target(expr, "{{le}}", fmt="heatmap")], x, y, kind="heatmap", unit="s", desc=desc,
                 extra={"options": {"calculate": False, "rowsFrame": {"layout": "le"},
                                    "yAxis": {"unit": "s"}, "cellGap": 1,
                                    "color": {"scheme": "Spectral", "mode": "scheme"}}})

def quantiles(metric, matcher, by=""):
    sel = f"{{{matcher}}}" if matcher else ""
    group = f"le, {by}" if by else "le"
    leg = f" {{{{{by}}}}}" if by else ""
    return [target(f"histogram_quantile({q}, sum by ({group}) (rate({metric}_bucket{sel}[5m])))",
                   f"p{int(q * 100)}{leg}", ref)
            for q, ref in ((0.5, "A"), (0.95, "B"))]

def build():
    panels = []
    y = 0
    panels.append(row("Calls and error rate (HTTP left, gRPC right)", y)); y += 1
    panels += [
        panel("HTTP GET /weather: requests/s by status",
              [target(f"sum by (status) (rate(weather_http_requests_total{{{HTTP}}}[1m]))", "{{status}}")],
              0, y, unit="reqps", desc="rate() over 1m. 429 = rate limited, 404 = unknown city, 422 = invalid input."),
        panel("gRPC: calls/s by method and status",
              [target("sum by (rpc_method, grpc_code) (rate(weather_grpc_requests_total[1m]))",
                      "{{rpc_method}} {{grpc_code}}")],
              12, y, unit="reqps", desc="rate() over 1m."),
    ]; y += 8
    panels += [
        panel("HTTP GET /weather: server error rate (5xx / all, 5m window)",
              [target(f'(sum(rate(weather_http_requests_total{{{HTTP},status=~"5.."}}[5m])) or vector(0)) '
                      f"/ sum(rate(weather_http_requests_total{{{HTTP}}}[5m]))", "5xx share")],
              0, y, unit="percentunit",
              desc="Only server-side failures. 404/422/429 are caller errors and are excluded."),
        panel("gRPC: server error rate by method (5m window)",
              [target(f'(sum by (rpc_method) (rate(weather_grpc_requests_total{{grpc_code=~"{SERVER_ERRORS}"}}[5m])) '
                      f"or (0 * sum by (rpc_method) (rate(weather_grpc_requests_total[5m])))) "
                      f"/ sum by (rpc_method) (rate(weather_grpc_requests_total[5m]))", "{{rpc_method}}")],
              12, y, unit="percentunit",
              desc="UNAVAILABLE/INTERNAL/UNKNOWN/DEADLINE_EXCEEDED/DATA_LOSS, the gRPC analogue of 5xx. "
                   "NOT_FOUND and INVALID_ARGUMENT are caller errors."),
    ]; y += 8

    panels.append(row("Latency: p50 / p95 over a 5 minute rate window", y)); y += 1
    panels += [
        panel("HTTP GET /weather: p50 and p95 (5m window)",
              quantiles("weather_http_request_duration_seconds", HTTP), 0, y, unit="s",
              desc="histogram_quantile over rate(...[5m]). Measured at the server by the HTTP middleware."),
        panel("gRPC: p50 and p95 by method (5m window)",
              quantiles("weather_grpc_request_duration_seconds", "", by="rpc_method"), 12, y, unit="s",
              desc="Measured at the SERVER, around the handler (excludes network and queueing)."),
    ]; y += 8
    panels += [
        heatmap("HTTP GET /weather: duration histogram",
                f"sum by (le) (increase(weather_http_request_duration_seconds_bucket{{{HTTP}}}[$__rate_interval]))",
                0, y, "Each cell = calls whose duration fell in that bucket during the interval."),
        heatmap("gRPC: duration histogram (all methods)",
                "sum by (le) (increase(weather_grpc_request_duration_seconds_bucket[$__rate_interval]))",
                12, y, "Same bucket boundaries as the HTTP histogram."),
    ]; y += 8

    panels.append(row("Saturation: in-flight calls", y)); y += 1
    panels += [
        panel("HTTP: active requests (all endpoints)",
              [target('weather_active_requests{job="weather-app"}', "active")], 0, y,
              desc="Existing A1 gauge. Includes the Prometheus scrape of /metrics itself."),
        panel("gRPC: in-flight calls by method",
              [target("sum by (rpc_method) (weather_grpc_in_flight_calls)", "{{rpc_method}}")], 12, y,
              desc="Chosen over message size: the server has a fixed worker pool (default 10). "
                   "When in-flight reaches the pool size, new calls queue and latency rises."),
    ]; y += 8

    panels.append(row("Project metrics", y)); y += 1
    panels += [
        panel("Idempotency outcomes (POST /subscriptions, 5m rate)",
              [target("sum by (result) (rate(weather_idempotency_requests_total[5m]))", "{{result}}")],
              0, y, unit="reqps",
              desc="created / replayed / in_progress / key_reuse. Shows retries being absorbed instead of duplicated."),
        panel("Open-Meteo call latency, mean by process (5m)",
              [target("sum by (job) (rate(weather_external_api_duration_seconds_sum[5m])) "
                      "/ sum by (job) (rate(weather_external_api_duration_seconds_count[5m]))", "{{job}}")],
              12, y, unit="s",
              desc="Existing A1 Summary. A Summary exposes only _sum and _count, so a mean is all it can give."),
    ]

    return {"uid": "weather-a2", "title": "Weather API: HTTP vs gRPC", "schemaVersion": 39, "version": 1,
            "editable": True, "refresh": "5s", "time": {"from": "now-15m", "to": "now"},
            "tags": ["weather", "a2"], "panels": panels, "templating": {"list": []}, "annotations": {"list": []}}

if __name__ == "__main__":
    with open("grafana/dashboards/weather-observability.json", "w", encoding="utf-8") as f:
        json.dump(build(), f, indent=2)
    print("wrote grafana/dashboards/weather-observability.json")
