import argparse
import threading
import time
import uuid

import grpc
import httpx

import weather_pb2
import weather_pb2_grpc

CITIES = ["Karachi", "Lahore", "London", "Tokyo", "New York"]


def http_round(base, n):
    with httpx.Client(base_url=base, timeout=10) as c:
        c.get("/weather", params={"city": CITIES[n % len(CITIES)]})
        c.get("/forecast", params={"city": CITIES[(n + 1) % len(CITIES)]})
        if n % 3 == 0:
            c.get("/weather", params={"city": "Atlantis"})
        if n % 5 == 0:
            c.get("/weather", params={"city": ""})
        if n % 7 == 0:
            c.get("/weather", params={"city": "Failville"})
        if n % 4 == 0:  # a write plus an immediate duplicate of it (replay)
            key = str(uuid.uuid4())
            body = {"city": CITIES[n % len(CITIES)], "alert_above_c": 40}
            c.post("/subscriptions", json=body, headers={"Idempotency-Key": key})
            c.post("/subscriptions", json=body, headers={"Idempotency-Key": key})
        if n % 9 == 0:  # same key, different body -> 422 key_reuse
            key = str(uuid.uuid4())
            c.post("/subscriptions", json={"city": "Lahore", "alert_above_c": 30}, headers={"Idempotency-Key": key})
            c.post("/subscriptions", json={"city": "Lahore", "alert_above_c": 31}, headers={"Idempotency-Key": key})


def grpc_call(stub, method, city):
    try:
        getattr(stub, method)(getattr(weather_pb2, f"{method}Request")(city=city), timeout=10)
    except grpc.RpcError:
        pass  # failures are intended; the server counts them


def grpc_round(stub, n):
    grpc_call(stub, "GetCurrentWeather", CITIES[n % len(CITIES)])
    grpc_call(stub, "GetForecast", CITIES[(n + 2) % len(CITIES)])
    if n % 3 == 0:
        grpc_call(stub, "GetCurrentWeather", "Atlantis")
    if n % 5 == 0:
        grpc_call(stub, "GetCurrentWeather", "")
    if n % 7 == 0:
        grpc_call(stub, "GetCurrentWeather", "Failville")
    if n % 2 == 0:  # 4 parallel slow calls -> in-flight gauge > 1 and slow buckets
        threads = [threading.Thread(target=grpc_call, args=(stub, "GetCurrentWeather", "Slowville"))
                   for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", default="http://localhost:8000")
    ap.add_argument("--grpc", default="localhost:50051")
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between rounds")
    ap.add_argument("--burst", type=int, default=0, help="extra rapid /weather calls to trigger 429")
    args = ap.parse_args()

    stub = weather_pb2_grpc.WeatherServiceStub(grpc.insecure_channel(args.grpc))
    for n in range(1, args.rounds + 1):
        http_round(args.http, n)
        grpc_round(stub, n)
        print(f"round {n}/{args.rounds} done", flush=True)
        time.sleep(args.pause)

    if args.burst:
        codes = {}
        with httpx.Client(base_url=args.http, timeout=10) as c:
            for _ in range(args.burst):
                s = c.get("/weather", params={"city": "Karachi"}).status_code
                codes[s] = codes.get(s, 0) + 1
        print("burst result (status: count):", codes)


if __name__ == "__main__":
    main()