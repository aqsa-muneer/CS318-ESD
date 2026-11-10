#!/usr/bin/env python3
"""Fixed-rate (open-loop) load generator for A3 -- HTTP and gRPC.

Open loop = requests are sent on a fixed SCHEDULE (request i at start + i/rate),
never "wait for the reply, then send the next". If the system slows down, the
client keeps sending at the same rate and the slowness shows up as latency and
in-flight requests, instead of silently lowering the load (which is what a
closed-loop client does).

Latency is measured from each request's SCHEDULED send time, so any delay on the
client side (event loop busy, connection pool full) counts against the result
instead of hiding it ("coordinated omission").

No retries: httpx never retries a request, and gRPC retries are switched off on
the channel, so every failure is counted exactly once (Part D requires this).

Each request carries x-request-id = <label>-<n>, so a whole run can be found in
Kibana with   envoy.request_id : <label>-*   (request ids are never metric labels).

Usage (from the project root, venv active):
  python loadgen.py --interface http --rate 50 --duration 60 --label baseline-http
  python loadgen.py --interface grpc --rate 50 --duration 60 --label baseline-grpc
"""
import argparse
import asyncio
import json
import math
import os
import time
from collections import Counter
from datetime import datetime, timezone


def percentile(sorted_values, p):
    """Nearest-rank percentile: the smallest value with at least p% of values <= it."""
    if not sorted_values:
        return None
    rank = max(1, math.ceil(p / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


# --------------------------------------------------------------------------
# One request per interface. Each returns (status, success, replica_id).
# --------------------------------------------------------------------------
def make_http_sender(args):
    import httpx

    client = httpx.AsyncClient(
        base_url=f"http://{args.target}",
        timeout=args.timeout,
        # Big pool: the client must never be the bottleneck. If it ever runs
        # out, the wait still counts, because latency starts at the schedule.
        limits=httpx.Limits(max_connections=args.max_connections,
                            max_keepalive_connections=args.max_connections),
    )

    async def send(request_id):
        try:
            r = await client.get("/forecast", params={"city": args.city},
                                 headers={"x-request-id": request_id})
            return str(r.status_code), 200 <= r.status_code < 300, r.headers.get("x-replica-id")
        except httpx.TimeoutException:
            return "timeout", False, None
        except httpx.HTTPError as e:
            return f"error:{type(e).__name__}", False, None

    return send, client.aclose


def make_grpc_sender(args):
    import grpc
    import weather_pb2
    import weather_pb2_grpc

    # ONE channel = one HTTP/2 connection to Envoy for the whole run, like a
    # normal gRPC client. Envoy (HTTP/2 listener) routes each call separately.
    channel = grpc.aio.insecure_channel(args.target, options=[("grpc.enable_retries", 0)])
    stub = weather_pb2_grpc.WeatherServiceStub(channel)
    request = weather_pb2.GetForecastRequest(city=args.city)

    def replica_of(metadata):
        return metadata.get("x-replica-id") if metadata else None

    async def send(request_id):
        call = stub.GetForecast(request, metadata=[("x-request-id", request_id)],
                                timeout=args.timeout)
        try:
            await call
            return "OK", True, replica_of(await call.initial_metadata())
        except grpc.aio.AioRpcError as e:
            return e.code().name, False, replica_of(e.initial_metadata())

    return send, channel.close
# --------------------------------------------------------------------------
async def run(args):
    if args.interface == "http":
        send, close = make_http_sender(args)
    else:
        send, close = make_grpc_sender(args)

    total = int(round(args.rate * args.duration))
    results = []           # (latency_s, status, success, replica)
    send_lags = []         # how late each request left vs its schedule
    in_flight = 0
    max_in_flight = 0

    async def one(n, scheduled):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        status, ok, replica = await send(f"{args.label}-{n}")
        in_flight -= 1
        results.append((time.perf_counter() - scheduled, status, ok, replica))

    start_wall = datetime.now(timezone.utc)
    start = time.perf_counter() + 0.2          # small lead so request 0 is on time
    tasks = []
    for n in range(total):
        scheduled = start + n / args.rate
        delay = scheduled - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        send_lags.append(max(0.0, time.perf_counter() - scheduled))
        # create_task = fire and move on: we do NOT wait for the reply here
        tasks.append(asyncio.create_task(one(n, scheduled)))
    send_end = time.perf_counter()
    await asyncio.gather(*tasks)               # now wait for the stragglers
    end_wall = datetime.now(timezone.utc)
    await close()

    # summary: prints each replica’s share, a second source for “share of total traffic” alongside the dashboard
    latencies_ms = sorted(r[0] * 1000 for r in results)
    ok_count = sum(1 for r in results if r[2])
    by_status = Counter(r[1] for r in results)
    by_replica = Counter(r[3] or "unknown" for r in results)
    send_span = send_end - start
    summary = {
        "label": args.label,
        "interface": args.interface,
        "target": args.target,
        "target_rate_rps": args.rate,
        "duration_s": args.duration,
        "start_utc": start_wall.isoformat(timespec="seconds"),
        "end_utc": end_wall.isoformat(timespec="seconds"),
        "sent": len(results),
        "achieved_send_rate_rps": round((total - 1) / send_span, 2) if total > 1 and send_span > 0 else None,
        "max_send_lag_ms": round(max(send_lags) * 1000, 2) if send_lags else None,
        "successes": ok_count,
        "failures": len(results) - ok_count,
        "error_rate_pct": round(100 * (len(results) - ok_count) / len(results), 2) if results else None,
        "by_status": dict(by_status.most_common()),
        "by_replica": dict(sorted(by_replica.items())),
        "share_pct": {k: round(100 * v / len(results), 1) for k, v in sorted(by_replica.items())},
        "client_p50_ms": round(percentile(latencies_ms, 50), 2) if results else None,
        "client_p95_ms": round(percentile(latencies_ms, 95), 2) if results else None,
        "client_p99_ms": round(percentile(latencies_ms, 99), 2) if results else None,
        "client_max_ms": round(latencies_ms[-1], 2) if results else None,
        "max_in_flight": max_in_flight,
    }

    local = lambda iso: datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"\nrun {summary['label']}  ({summary['interface']} -> {summary['target']})")
    print(f"  window    {local(summary['start_utc'])}  ->  {local(summary['end_utc'])}")
    print(f"  target    {args.rate:g} req/s for {args.duration:g} s = {total} requests")
    print(f"  sent      {summary['sent']}  (achieved {summary['achieved_send_rate_rps']} req/s, "
          f"max send lag {summary['max_send_lag_ms']} ms)")
    print(f"  results   {summary['successes']} ok, {summary['failures']} failed "
          f"({summary['error_rate_pct']}% errors)   by status {summary['by_status']}")
    print(f"  replicas  " + "  ".join(f"{k}: {v} ({summary['share_pct'][k]}%)"
                                      for k, v in summary["by_replica"].items()))
    print(f"  latency   p50 {summary['client_p50_ms']} ms   p95 {summary['client_p95_ms']} ms   "
          f"p99 {summary['client_p99_ms']} ms   max {summary['client_max_ms']} ms")
    print(f"  in flight max {summary['max_in_flight']}")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "a") as f:
        f.write(json.dumps(summary) + "\n")
    print(f"  saved to  {args.out}\n")


def main():
    p = argparse.ArgumentParser(description="Fixed-rate (open-loop) load generator for A3")
    p.add_argument("--interface", choices=["http", "grpc"], required=True)
    p.add_argument("--rate", type=float, required=True, help="requests per second")
    p.add_argument("--duration", type=float, required=True, help="seconds")
    p.add_argument("--label", required=True, help="run name, e.g. baseline-http (used in request ids)")
    p.add_argument("--target", help="host:port (default: localhost:8080 for http, localhost:50051 for grpc)")
    p.add_argument("--city", default="Karachi")
    p.add_argument("--timeout", type=float, default=5.0, help="per-request timeout in seconds")
    p.add_argument("--max-connections", type=int, default=1000, help="HTTP connection pool size")
    p.add_argument("--out", default="results/runs.jsonl", help="file to append the summary to")
    args = p.parse_args()
    if args.target is None:
        args.target = "localhost:8080" if args.interface == "http" else "localhost:50051"
    asyncio.run(run(args))

if __name__ == "__main__":
    main()
