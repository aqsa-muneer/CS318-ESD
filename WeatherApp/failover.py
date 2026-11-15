#!/usr/bin/env python3
"""Part D3 -- failure and recovery of replica B during a fixed-rate run.

What it does, in order:
  1. Starts loadgen.py (same workload as every other run) in the background.
  2. Watches Envoy's admin page (/clusters) every 0.2 s and records every change
     in B's health flags, with a timestamp.
  3. At --stop-at seconds: `docker compose stop app-b`.  At --start-at: `docker compose start app-b`.
  4. After the run: reads Envoy's access log for this run's request ids and prints
     a 5-second timeline of where requests went (A/B/C) and how many failed.
  5. Prints how long Envoy took to remove B after the stop and to add it back
     after the start, so they can be compared with the health-check settings.

Usage (project root, venv active, Envoy health checks configured):
  python failover.py --interface http --label d3-http
  python failover.py --interface grpc --label d3-grpc
"""
import argparse
import json
import subprocess
import threading
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime

B_ENDPOINT = {"http": "172.28.0.12:8000", "grpc": "172.28.0.12:50051"}
CLUSTER = {"http": "weather_http", "grpc": "weather_grpc"}
REPLICA = {"172.28.0.11": "A", "172.28.0.12": "B", "172.28.0.13": "C"}


def clock():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def b_health(interface):
    """B's health flags as Envoy reports them: 'healthy', '/failed_active_hc', ..."""
    try:
        text = urllib.request.urlopen("http://localhost:9901/clusters", timeout=1).read().decode()
    except Exception:
        return "envoy-unreachable"
    key = f"{CLUSTER[interface]}::{B_ENDPOINT[interface]}::health_flags::"
    for line in text.splitlines():
        if line.startswith(key):
            return line[len(key):]
    return "not-listed"


def watch(interface, t0, events, done):
    last = None
    while not done.is_set():
        state = b_health(interface)
        if state != last:
            events.append((time.monotonic() - t0, clock(), f"Envoy reports B as: {state}"))
            last = state
        time.sleep(0.2)


def compose(*args):
    subprocess.run(["docker", "compose", *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def timeline(interface, label, bucket=5):
    """Per-5-second counts from Envoy's access log: requests per replica and failures."""
    rows = defaultdict(Counter)
    flags = Counter()
    first = None
    prefix = f"{label}-"
    with open("logs/envoy/access.log") as f:
        for line in f:
            if prefix not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not str(e.get("request_id", "")).startswith(prefix):
                continue
            t = datetime.fromisoformat(e["start_time"].replace("Z", "+00:00")).timestamp()
            first = t if first is None else min(first, t)
            ok = (str(e.get("grpc_status")) == "OK") if interface == "grpc" else (str(e.get("status")) == "200")
            host = str(e.get("upstream_host", "-")).split(":")[0]
            rows[t][REPLICA.get(host, "none") if ok else "failed"] += 1
            if not ok:
                flags[f"{e.get('status')}/{e.get('grpc_status')}/{e.get('response_flags')} via {REPLICA.get(host, 'no host')}"] += 1
    if first is None:
        print("  (no access-log lines found for this label yet)")
        return
    buckets = defaultdict(Counter)
    for t, c in rows.items():
        buckets[int((t - first) // bucket)].update(c)
    print(f"\n  Envoy access log, {bucket}-second buckets from the first request:")
    print("     seconds      A     B     C   failed")
    for b in sorted(buckets):
        c = buckets[b]
        print(f"    {b*bucket:3d}-{b*bucket+bucket:<3d}  {c['A']:5d} {c['B']:5d} {c['C']:5d}  {c['failed']:6d}")
    total = sum(sum(c.values()) for c in buckets.values())
    failed = sum(c["failed"] for c in buckets.values())
    print(f"    total {total}, failed {failed}")
    if flags:
        print("  failures by HTTP status / gRPC status / Envoy response flags:")
        for k, v in flags.most_common():
            print(f"    {v:5d}  {k}")


def main():
    p = argparse.ArgumentParser(description="D3: stop and restart B during a fixed-rate run")
    p.add_argument("--interface", choices=["http", "grpc"], required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--rate", type=float, default=50)
    p.add_argument("--duration", type=float, default=90)
    p.add_argument("--stop-at", type=float, default=25, help="seconds into the run to stop B")
    p.add_argument("--start-at", type=float, default=55, help="seconds into the run to start B again")
    a = p.parse_args()

    if b_health(a.interface) != "healthy":
        raise SystemExit(f"B is not healthy in Envoy before the run ({b_health(a.interface)}); fix that first.")

    events = []
    done = threading.Event()
    t0 = time.monotonic()
    watcher = threading.Thread(target=watch, args=(a.interface, t0, events, done), daemon=True)
    watcher.start()
    load = subprocess.Popen(["python", "loadgen.py", "--interface", a.interface, "--rate", str(a.rate),
                             "--duration", str(a.duration), "--label", a.label])

    def at(seconds):
        time.sleep(max(0.0, seconds - (time.monotonic() - t0)))

    at(a.stop_at)
    events.append((time.monotonic() - t0, clock(), "docker compose stop app-b  (issued)"))
    compose("stop", "app-b")
    events.append((time.monotonic() - t0, clock(), "docker compose stop app-b  (B is down)"))
    at(a.start_at)
    events.append((time.monotonic() - t0, clock(), "docker compose start app-b (issued)"))
    compose("start", "app-b")
    events.append((time.monotonic() - t0, clock(), "docker compose start app-b (container running)"))

    load.wait()
    # keep watching until Envoy reports B healthy again (or 30 s), so recovery is always captured
    deadline = time.monotonic() + 30
    while b_health(a.interface) != "healthy" and time.monotonic() < deadline:
        time.sleep(0.2)
    time.sleep(0.5)
    done.set()
    watcher.join()
    time.sleep(1.5)  # Envoy flushes its access log about once per second

    events.sort()
    print(f"\n=== {a.label}: timeline (seconds since the run started) ===")
    for t, wall, what in events:
        print(f"  {t:6.2f}s  {wall}  {what}")

    def first(pred, after):
        return next((t for t, _, w in events if t >= after and pred(w)), None)

    issued_stop = next(t for t, _, w in events if "stop app-b  (issued)" in w)
    down = next(t for t, _, w in events if "(B is down)" in w)
    issued_start = next(t for t, _, w in events if "start app-b (issued)" in w)
    removed = first(lambda w: "B as:" in w and w.split("B as: ")[1] != "healthy", issued_stop)
    back = first(lambda w: w.endswith("B as: healthy"), issued_start)
    print()
    print(f"  stopping B took {down - issued_stop:5.2f} s (docker compose stop)")
    if removed is not None:
        print(f"  removal:  Envoy marked B unhealthy {removed - issued_stop:5.2f} s after the stop was issued"
              f" ({removed - down:+.2f} s relative to B being fully down)")
    if back is not None:
        print(f"  recovery: Envoy marked B healthy   {back - issued_start:5.2f} s after 'docker compose start'")
    timeline(interface=a.interface, label=a.label)


if __name__ == "__main__":
    main()
