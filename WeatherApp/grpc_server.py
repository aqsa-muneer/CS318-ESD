# reuses get_coordinates, get_current_weather and get_forecast, so the gRPC and HTTP APIs return the same data
import os
import signal
from concurrent import futures
import grpc
from prometheus_client import start_http_server
import weather_pb2
import weather_pb2_grpc
from grpc_metrics import MetricsInterceptor
from metrics import WEATHER_SEARCHES, WEATHER_SEARCH_RESULTS
from weather import get_coordinates, get_current_weather, get_forecast

if os.getenv("WEATHER_FAKE_UPSTREAM") == "1":
    import fake_upstream
    fake_upstream.install()

def _location_message(location):
    return weather_pb2.Location(
        city=location["name"], region=location["region"], country=location["country"],
        latitude=location["latitude"], longitude=location["longitude"],
        timezone=location["timezone"])

def _fill(message_cls, data):
    fields = message_cls.DESCRIPTOR.fields_by_name
    return message_cls(**{k: v for k, v in data.items() if k in fields and v is not None})

class WeatherService(weather_pb2_grpc.WeatherServiceServicer):
    def _resolve_city(self, city, context):
        city = city.strip()
        if not city or len(city) > 100:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "city must be 1-100 non-blank characters")
        try:
            location = get_coordinates(city)
        except Exception as error:
            location, failure = None, str(error)
        else:
            failure = None
        if failure:
            context.abort(grpc.StatusCode.UNAVAILABLE, failure)
        if location is None:
            context.abort(grpc.StatusCode.NOT_FOUND, "City not found")
        return location

    def GetCurrentWeather(self, request, context):
        WEATHER_SEARCHES.inc()
        try:
            location = self._resolve_city(request.city, context)
            try:
                weather = get_current_weather(
                    location["latitude"], location["longitude"], location["timezone"])
            except Exception as error:
                weather, failure = None, str(error)
            else:
                failure = None
            if failure:
                context.abort(grpc.StatusCode.UNAVAILABLE, failure)
        except Exception:
            code = context.code()
            result = {grpc.StatusCode.NOT_FOUND: "not_found"}.get(code, "error")
            WEATHER_SEARCH_RESULTS.labels(result=result).inc()
            raise
        WEATHER_SEARCH_RESULTS.labels(result="success").inc()
        return weather_pb2.GetCurrentWeatherResponse(
            location=_location_message(location),
            current_weather=_fill(weather_pb2.CurrentWeather, weather))

    def GetForecast(self, request, context):
        location = self._resolve_city(request.city, context)
        try:
            days = get_forecast(location["latitude"], location["longitude"], location["timezone"])
        except Exception as error:
            days, failure = None, str(error)
        else:
            failure = None
        if failure:
            context.abort(grpc.StatusCode.UNAVAILABLE, failure)
        return weather_pb2.GetForecastResponse(
            location=_location_message(location),
            forecast=[_fill(weather_pb2.ForecastDay, d) for d in days])

def build_server(port=50051, max_workers=10):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers),
                         interceptors=[MetricsInterceptor()])
    weather_pb2_grpc.add_WeatherServiceServicer_to_server(WeatherService(), server)
    bound = server.add_insecure_port(f"[::]:{port}")
    return server, bound

if __name__ == "__main__":
    metrics_port = int(os.getenv("GRPC_METRICS_PORT", "9101"))
    server, port = build_server(int(os.getenv("GRPC_PORT", "50051")),
                                int(os.getenv("GRPC_MAX_WORKERS", "10")))
    start_http_server(metrics_port)
    server.start()
    print(f"gRPC on :{port}, metrics on :{metrics_port}/metrics", flush=True)
    signal.signal(signal.SIGTERM, lambda *_: server.stop(grace=5))
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(0)
