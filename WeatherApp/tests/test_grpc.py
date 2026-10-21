import grpc
import pytest
from prometheus_client import REGISTRY

import weather_pb2
import weather_pb2_grpc
from grpc_server import build_server

# check the success path, status code for each error, the metrics, that in flight returns to 0, and that an unknown method creates no metric series
@pytest.fixture(scope="module")
def channel():
    server, port = build_server(port=0)
    server.start()
    ch = grpc.insecure_channel(f"localhost:{port}")
    yield ch
    ch.close()
    server.stop(0)


@pytest.fixture(scope="module")
def stub(channel):
    return weather_pb2_grpc.WeatherServiceStub(channel)


def handled(method, code):
    return REGISTRY.get_sample_value(
        "weather_grpc_requests_total", {"rpc_method": method, "grpc_code": code}) or 0


def test_success_and_metrics(stub):
    before = handled("GetCurrentWeather", "OK")
    reply = stub.GetCurrentWeather(weather_pb2.GetCurrentWeatherRequest(city="Karachi"), timeout=5)
    assert reply.location.city == "Karachi"
    assert reply.current_weather.temperature == 33.0
    assert reply.current_weather.condition == "Mainly clear"
    assert handled("GetCurrentWeather", "OK") == before + 1
    assert REGISTRY.get_sample_value("weather_grpc_request_duration_seconds_count",
                                     {"rpc_method": "GetCurrentWeather"}) >= 1


def test_forecast(stub):
    reply = stub.GetForecast(weather_pb2.GetForecastRequest(city="London"), timeout=5)
    assert len(reply.forecast) == 5


@pytest.mark.parametrize("city, code, label", [
    ("", grpc.StatusCode.INVALID_ARGUMENT, "INVALID_ARGUMENT"),
    ("   ", grpc.StatusCode.INVALID_ARGUMENT, "INVALID_ARGUMENT"),
    ("Atlantis", grpc.StatusCode.NOT_FOUND, "NOT_FOUND"),
    ("Failville", grpc.StatusCode.UNAVAILABLE, "UNAVAILABLE"),
])
def test_errors_use_the_right_status_and_are_counted(stub, city, code, label):
    before = handled("GetCurrentWeather", label)
    with pytest.raises(grpc.RpcError) as err:
        stub.GetCurrentWeather(weather_pb2.GetCurrentWeatherRequest(city=city), timeout=5)
    assert err.value.code() == code
    assert handled("GetCurrentWeather", label) == before + 1


def test_in_flight_returns_to_zero(stub):
    stub.GetCurrentWeather(weather_pb2.GetCurrentWeatherRequest(city="Tokyo"), timeout=5)
    assert REGISTRY.get_sample_value("weather_grpc_in_flight_calls",
                                     {"rpc_method": "GetCurrentWeather"}) == 0


def test_unknown_method_is_not_labelled(channel):
    call = channel.unary_unary("/weather.v1.WeatherService/DoesNotExist",
                               request_serializer=lambda b: b, response_deserializer=lambda b: b)
    with pytest.raises(grpc.RpcError) as err:
        call(b"", timeout=5)
    assert err.value.code() == grpc.StatusCode.UNIMPLEMENTED
    assert REGISTRY.get_sample_value(
        "weather_grpc_requests_total",
        {"rpc_method": "DoesNotExist", "grpc_code": "UNIMPLEMENTED"}) is None
