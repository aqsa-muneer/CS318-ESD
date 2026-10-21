import argparse

import grpc

import weather_pb2
import weather_pb2_grpc

ap = argparse.ArgumentParser()
ap.add_argument("city")
ap.add_argument("--method", default="GetCurrentWeather", choices=["GetCurrentWeather", "GetForecast"])
ap.add_argument("--target", default="localhost:50051")
args = ap.parse_args()

stub = weather_pb2_grpc.WeatherServiceStub(grpc.insecure_channel(args.target))
request = getattr(weather_pb2, f"{args.method}Request")(city=args.city)
try:
    print(getattr(stub, args.method)(request, timeout=5))
except grpc.RpcError as error:
    print(f"gRPC error: {error.code().name}: {error.details()}")
