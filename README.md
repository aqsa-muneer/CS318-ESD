# CS318-ESD
FastAPI weather service with an OpenAPI spec, idempotent writes, rate limiting, a resilient Python caller, and a gRPC along with Prometheus/Grafana metrics.

## Setup
-> **python -m venv .venv**

-> **source .venv/bin/activate**

-> **pip install -r requirements.txt**

## Run tests: 
-> **pytest -q**

About 50 tests: API, OpenAPI contract, idempotency, rate limit, caller, gRPC and dashboard checks. The PromQL syntax test is skipped unless **'pip install promql-parser'**. Tests use 'fake_upstream.py', an offline stand-in for Open-Meteo.

## Run the app (real weather data + frontend)

Use one terminal per command:

1. **uvicorn main:app --port 8000**
2. **python grpc_server.py**
3. **cd frontend % npm run dev**

This mode calls the real Open-Meteo API, so any real city works.

## Run the monitoring demo:

Stop the HTTP and gRPC servers from the section above first, because only one process can use port 8000.

1. Make sure Docker Desktop is running, then start Prometheus, Grafana and node exporter: **docker compose up -d prometheus grafana node-exporter**

2. Start both servers in fake mode, one terminal each:

-> **WEATHER_FAKE_UPSTREAM=1 uvicorn main:app --port 8000**

-> **WEATHER_FAKE_UPSTREAM=1 python grpc_server.py**

3. In a third terminal, generate traffic:

-> **python -m scripts.load --rounds 40 --pause 2 --burst 80**

It sends a repeatable mix of successes and failures to HTTP and gRPC, then a burst that triggers 429s. It takes about 3 minutes and ends with 'burst result (status: count): {200: 38, 429: 42}'.
   
4. Open http://localhost:3000, go to Dashboards, then **Weather API: HTTP vs gRPC**, and set the range to the last 15 minutes.

With 'WEATHER_FAKE_UPSTREAM=1' the weather values are hard-coded and only a few cities exist (Karachi, Lahore, London, Tokyo, New York, plus the magic names 'Atlantis' = not found, 'Failville' = upstream down, 'Slowville' = slow). Use fake mode only for load and tests.

To stop: press ctrl+c in each server terminal, and run '**docker compose stop prometheus grafana node-exporter**'.

## Try the API features by hand

### Rate limit (429 and Retry-After)

Stop any server on port 8000, then start the HTTP server with a low limit so the 429 appears quickly:

-> **RATE_LIMIT_REQUESTS=10 uvicorn main:app --port 8000**

In a second terminal, send 12 requests. The first 10 return 200 and the last 2 return 429:

-> **for i in $(seq 1 12); do curl -s -o /dev/null -w "%{http_code} " "localhost:8000/weather?city=Karachi"; done; echo**

Show the full 429 response. Look for the 'retry-after' header (seconds until you may call again) and the 'x-ratelimit-*' headers:

-> **curl -i "localhost:8000/weather?city=Karachi"**

The default limit is 60 requests per rolling 60 seconds per client IP. 'RATE_LIMIT_REQUESTS' and 'RATE_LIMIT_WINDOW_SECONDS' change it. The counter lives in memory, so restarting the server resets it.

### Idempotent write
Send the same POST twice with the same key. The second response is a replay with the same id and an 'Idempotent-Replayed: true' header:

-> **curl -i -X POST localhost:8000/subscriptions -H "Idempotency-Key: demo-key-0001" -H "Content-Type: application/json" -d '{"city":"Karachi","alert_above_c":40}'**

-> **curl -i -X POST localhost:8000/subscriptions -H "Idempotency-Key: demo-key-0001" -H "Content-Type: application/json" -d '{"city":"Karachi","alert_above_c":40}'**

Reusing the key with a different body returns 422:

-> **curl -i -X POST localhost:8000/subscriptions -H "Idempotency-Key: demo-key-0001" -H "Content-Type: application/json" -d '{"city":"Lahore","alert_above_c":30}'**

### OpenAPI spec

Regenerate 'openapi.yaml' from the code, then print every route, its inputs and its declared responses:

-> **python -m scripts.export_openapi**

-> **python -m scripts.inspect_spec**

The interactive version is at http://localhost:8000/docs while the server is running. A test fails if 'openapi.yaml' is out of date.

### Resilient Python caller

Each demo starts its own server (ports 8765 and 8766) and prints its output:

-> **python -m scripts.demo_caller**

-> **python -m scripts.demo_rate_limit**

'demo_caller' shows a success, a retry, calls blocked while the circuit breaker is open, and recovery.

'demo_rate_limit' shows 5 accepted calls, a 429 with 'Retry-After', and being allowed again. To use the caller against a running server:

-> **python client.py --base-url http://localhost:8000**

### gRPC
1. **python -m scripts.grpc_client Karachi**

2. **python -m scripts.grpc_client Atlantis**

3. **python -m scripts.grpc_client "" --method GetForecast**

The service contract is 'weather.proto'. After editing it, regenerate the Python code:

-> **python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. weather.proto**

This writes 'weather_pb2.py' (message classes) and 'weather_pb2_grpc.py' (server and client stubs). 

With 'grpcurl' installed, no reflection is needed because '-proto' is given:

-> **grpcurl -plaintext -proto weather.proto -d '{"city":"Karachi"}' localhost:50051 weather.v1.WeatherService/GetCurrentWeather**
