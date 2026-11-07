import os
import time
import uuid

import grpc
from prometheus_client import Counter, Gauge, Histogram

from logging_config import logger  # A3: per-call logs

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0)

# A3: which replica this is (A/B/C), and an optional injected delay (ms).
# Same two settings the HTTP side reads in main.py.
REPLICA_ID = os.getenv("REPLICA_ID", "local")
FAULT_DELAY_MS = int(os.getenv("FAULT_DELAY_MS", "0"))

GRPC_REQUESTS = Counter(
    "weather_grpc_requests_total",
    "Completed gRPC calls by method and final status code",
    ["rpc_method", "grpc_code"],
)
GRPC_DURATION = Histogram(
    "weather_grpc_request_duration_seconds",
    "Server-side time spent inside the gRPC handler",
    ["rpc_method"],
    buckets=LATENCY_BUCKETS,
)
GRPC_IN_FLIGHT = Gauge(
    "weather_grpc_in_flight_calls",
    "gRPC calls currently executing a handler",
    ["rpc_method"],
)


def _request_id(handler_call_details) -> str:
    """A3: reuse the x-request-id Envoy added, so Envoy's access-log line and this
    replica's log line carry the SAME id. gRPC metadata keys are always lowercase."""
    for key, value in handler_call_details.invocation_metadata or ():
        if key == "x-request-id":
            return value
    return str(uuid.uuid4())  # called directly (no Envoy): make our own


class MetricsInterceptor(grpc.ServerInterceptor):
    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler
        full_method = handler_call_details.method          # /weather.v1.WeatherService/GetForecast
        method = full_method.rsplit("/", 1)[-1]            # GetForecast (bounded metric label)
        request_id = _request_id(handler_call_details)     # A3
        inner = handler.unary_unary

        def wrapped(request, context): # increases in-flight gauge and starts a timer
            # runs the real handler
            GRPC_IN_FLIGHT.labels(rpc_method=method).inc()
            start = time.perf_counter()
            code = grpc.StatusCode.OK
            try:
                # A3: response HEADERS (gRPC "initial metadata"), sent before the
                # handler runs. Envoy logs x-replica-id via %RESP(X-REPLICA-ID)%;
                # x-request-id lets the client see the id too, like HTTP's header.
                context.send_initial_metadata((
                    ("x-replica-id", REPLICA_ID),
                    ("x-request-id", request_id),
                ))
                # A3: injected slowness, inside the timed + in-flight window so B
                # looks like a genuinely slow replica. Note it also occupies one of
                # the server's GRPC_MAX_WORKERS threads while sleeping.
                if FAULT_DELAY_MS:
                    time.sleep(FAULT_DELAY_MS / 1000)
                response = inner(request, context)
                code = context.code() or grpc.StatusCode.OK
                return response
            except Exception:
                code = context.code() or grpc.StatusCode.UNKNOWN
                raise
            finally: # runs on success and failure, records the duration and counts the call under its final status code
                duration = time.perf_counter() - start
                GRPC_DURATION.labels(rpc_method=method).observe(duration)
                GRPC_REQUESTS.labels(rpc_method=method, grpc_code=code.name).inc()
                GRPC_IN_FLIGHT.labels(rpc_method=method).dec() # lastly, decreases gauge
                # A3: one log line per call (the gRPC server logged nothing before),
                # mirroring the HTTP middleware: non-OK outcomes are WARNING.
                logger.log(
                    20 if code == grpc.StatusCode.OK else 30,   # INFO / WARNING
                    "gRPC call completed",
                    extra={
                        "request_id": request_id,
                        "grpc_method": full_method,
                        "grpc_status": code.name,
                        "duration_ms": round(duration * 1000, 2),
                    },
                )

        return grpc.unary_unary_rpc_method_handler(
            wrapped,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )