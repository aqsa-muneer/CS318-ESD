# Weather App — A3: Load balancing with Envoy

Three replicas (A, B, C) of the Weather App behind Envoy, for both HTTP (`GET /forecast`) and
gRPC (`WeatherService/GetForecast`).

## Contents

| Path | What it is |
| --- | --- |
| `docker-compose.yml` | Redis, replicas `app-a` / `app-b` / `app-c` (one template, fixed IPs), Envoy, observability stack |
| `Dockerfile` | One image per replica; runs the gRPC server and uvicorn in the same container |
| `envoy/envoy.yaml` | HTTP listener `:10000` (host `:8080`), gRPC listener `:10001` (host `:50051`, HTTP/2), two clusters, health checks, JSON access log |
| `prometheus/prometheus.yml` | Scrapes A, B, C (`:8000`, `:9101`) and Envoy (`:9901`) directly, adds a `replica` label |
| `filebeat/filebeat.yml` | Ships replica logs (`app.*`) and Envoy's access log (`envoy.*`) to Elasticsearch |
| `grafana/dashboards/a3-load-balancing.json` | Exported dashboard: per-replica rate, share, active requests, p50/p95, errors; Envoy view |
| `loadgen.py` | Fixed-rate (open-loop) test client for HTTP and gRPC; no retries |
| `failover.py` | Part D3 helper: stops and restarts B during a run and times Envoy's reaction |
| `redis_client.py`, `subscriptions.py`, `idempotency.py`, `ratelimit.py` | Shared state in Redis (A2 code moved out of process memory) |
| `logging_config.py`, `main.py`, `grpc_metrics.py`, `grpc_server.py` | A2 code with A3 changes marked `# A3`: replica ID, request-ID correlation, fault delay, gRPC health service |
| `results/runs.jsonl`, `results/warmup.jsonl` | One JSON summary per measured run and per warm-up |

Not in the repository: `venv/`, `logs/`, Docker volumes (`.gitignore`).

## Prerequisites

- Docker Desktop (Compose v2)
- Python 3.12+ with a virtual environment for the test client:

```
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` pins `grpcio==1.84.0`, `protobuf==7.36.2` and `grpcio-health-checking==1.84.0`
to match the generated `weather_pb2*.py` files.

## Setup

```
docker compose up -d --build
docker compose ps
```

All 11 services should be `Up` (allow 30–60 s for Elasticsearch and Kibana). Use `-d`: without it,
Compose follows every container's logs and Ctrl+C stops the whole stack.

Check that Envoy loaded its config and all replicas are healthy:

```
docker compose logs envoy | tail -3
curl -s http://localhost:9901/clusters | grep health_flags
```

| Address | Service |
| --- | --- |
| `http://localhost:8080` | Envoy, HTTP |
| `localhost:50051` | Envoy, gRPC |
| `http://localhost:9901` | Envoy admin (`/clusters`, `/stats/prometheus`, `/config_dump`) |
| `http://localhost:9090/targets` | Prometheus targets (8, all UP) |
| `http://localhost:3000` | Grafana → *A3 — Load balancing per replica (HTTP + gRPC)* |
| `http://localhost:5601` | Kibana → Discover, data view `filebeat-*` |

Quick checks:

```
curl -i "http://localhost:8080/forecast?city=Karachi"          # header x-replica-id
curl -i -H "x-request-id: demo-1" "http://localhost:8080/forecast?city=Karachi"
```

Kibana search that returns the Envoy event and the replica event for one request:

```
app.request_id : "demo-1" or envoy.request_id : "demo-1"
```

## Changing Envoy's configuration safely

Always validate before restarting; a broken file stops Envoy:

```
docker run --rm -v "$PWD/envoy/envoy.yaml:/etc/envoy/envoy.yaml:ro" -v "$PWD/logs/envoy:/var/log/envoy" \
  envoyproxy/envoy:v1.31-latest --mode validate -c /etc/envoy/envoy.yaml && docker compose up -d envoy
sleep 6
curl -s http://localhost:9901/clusters | grep -E "health_flags|weight"
```

If validation reports `illegal map value`, the file contains non-breaking spaces copied from a web
page; replace them and re-check:

```
sed -i '' $'s/\xc2\xa0/ /g' envoy/envoy.yaml      # macOS; on Linux use: sed -i ...
python -c "import yaml; yaml.safe_load(open('envoy/envoy.yaml')); print('YAML OK')"
```

## Workload (every run)

50 requests/s for 90 s per interface, HTTP then gRPC, each preceded by a 15 s warm-up and a 30 s
pause. Do not open `localhost:8080` in a browser during a run.

```
python loadgen.py --interface http --rate 50 --duration 15 --label warmup-<name>-http --out results/warmup.jsonl && sleep 30 && \
python loadgen.py --interface http --rate 50 --duration 90 --label <name>-http && sleep 30 && \
python loadgen.py --interface grpc --rate 50 --duration 15 --label warmup-<name>-grpc --out results/warmup.jsonl && sleep 30 && \
python loadgen.py --interface grpc --rate 50 --duration 90 --label <name>-grpc
```

Each run prints its window, achieved rate, successes/failures by status, A/B/C split, and client
p50/p95/p99, and appends the same to `results/runs.jsonl`.

Count one run's distribution in Envoy's access log:

```
for run in <name>-http <name>-grpc; do echo "== $run"; grep "\"request_id\":\"$run-" logs/envoy/access.log | grep -o '"upstream_host":"[^"]*"' | sort | uniq -c; done
```

In Grafana, set the time range to a run's `window` to read that run's share and request counts.

## Experiments

The repository's `envoy.yaml` is the final state: A, B and C, equal-weight `ROUND_ROBIN`, active
health checks on both clusters.

### Part B — baseline (A only)

In both clusters of `envoy/envoy.yaml`, comment out the B and C endpoints, validate and restart
Envoy, then run the workload with `<name>` = `baseline`. Expected: A 100%, B and C 0%.

### Part C — round robin

Restore the B and C endpoints (equal weights, `lb_policy: ROUND_ROBIN`), validate and restart, run
with `<name>` = `rr`. Expected: about 33% each.

### Part D1 — weighted round robin 2:1:1

Add `load_balancing_weight` to each endpoint in both clusters, aligned with `endpoint:`:

```yaml
        - endpoint:   # Replica A
            address:
              socket_address: { address: 172.28.0.11, port_value: 8000 }
          load_balancing_weight: 2
```

(weight `1` for B and C; port `50051` in the gRPC cluster). Validate, restart, check
`curl -s http://localhost:9901/clusters | grep "::weight::"`, run with `<name>` = `wrr`.

Restore:

```
sed -i '' '/load_balancing_weight/d' envoy/envoy.yaml
```

then validate and restart.

### Part D2 — slow B: round robin vs least request

Add a 200 ms delay to every HTTP request and gRPC call on B only, and confirm it:

```
B_DELAY_MS=200 docker compose up -d app-b
for r in a b c; do docker compose exec app-$r python -c "
import httpx, time
c = httpx.Client(); c.get('http://localhost:8000/forecast?city=Karachi')
t = time.perf_counter(); c.get('http://localhost:8000/forecast?city=Karachi')
print('$r', round((time.perf_counter() - t) * 1000), 'ms')"; done
```

Run 1, round robin: run with `<name>` = `d2rr`. Then switch both clusters to least request and
confirm the running config:

```
sed -i '' 's/lb_policy: ROUND_ROBIN/lb_policy: LEAST_REQUEST/' envoy/envoy.yaml
docker compose restart envoy && sleep 6
curl -s http://localhost:9901/config_dump | grep -o '"lb_policy": "[A-Z_]*"'
```

Run 2, least request: run with `<name>` = `d2lr`.

Restore:

```
sed -i '' 's/lb_policy: LEAST_REQUEST/lb_policy: ROUND_ROBIN/' envoy/envoy.yaml
docker compose up -d app-b            # B recreated with no delay
docker compose restart envoy
```

### Part D3 — failure and recovery

Health checks (both clusters): interval 2 s, timeout 1 s, unhealthy threshold 2, healthy
threshold 2; HTTP `GET /health`, gRPC `grpc.health.v1.Health/Check`. With round robin and no delay:

```
python loadgen.py --interface http --rate 50 --duration 15 --label warmup-d3-http --out results/warmup.jsonl && sleep 30 && \
python failover.py --interface http --label d3-http && sleep 30 && \
python loadgen.py --interface grpc --rate 50 --duration 15 --label warmup-d3-grpc --out results/warmup.jsonl && sleep 30 && \
python failover.py --interface grpc --label d3-grpc
```

`failover.py` stops B at 25 s and starts it at 55 s, prints when Envoy removed and re-added B, and
a 5-second table of A/B/C traffic and failures from Envoy's access log. Re-print the tables once the
log is fully flushed:

```
python -c "import failover; failover.timeline('http', 'd3-http'); failover.timeline('grpc', 'd3-grpc')"
```

## Recovery commands

| Situation | Command |
| --- | --- |
| A replica is stopped | `docker compose start app-b` (or `docker compose up -d app-b`) |
| B still has the delay | `docker compose up -d app-b` |
| Envoy stopped after a bad config | fix `envoy.yaml`, then the validate-and-restart command above |
| Check health | `curl -s http://localhost:9901/clusters \| grep health_flags` |
| Restart one service | `docker compose restart <service>` |
| Follow one service's logs | `docker compose logs -f <service>` (Ctrl+C stops only the log view) |
| Stop everything (keep data) | `docker compose down` |
| Stop and delete data volumes | `docker compose down -v` |

Restarting Envoy resets its counters; avoid it during a measured run.
