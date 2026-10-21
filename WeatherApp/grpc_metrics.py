import time

import grpc
from prometheus_client import Counter, Gauge, Histogram

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0)

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

class MetricsInterceptor(grpc.ServerInterceptor):
    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler
        method = handler_call_details.method.rsplit("/", 1)[-1]
        inner = handler.unary_unary

        def wrapped(request, context): # increases in-flight gauge and starts a timer
            # runs the real handler
            GRPC_IN_FLIGHT.labels(rpc_method=method).inc()
            start = time.perf_counter()
            code = grpc.StatusCode.OK
            try:
                response = inner(request, context)
                code = context.code() or grpc.StatusCode.OK
                return response
            except Exception:
                code = context.code() or grpc.StatusCode.UNKNOWN
                raise
            finally: # runs on success and failure, records the duration and counts the call under its final status code
                GRPC_DURATION.labels(rpc_method=method).observe(time.perf_counter() - start)
                GRPC_REQUESTS.labels(rpc_method=method, grpc_code=code.name).inc()
                GRPC_IN_FLIGHT.labels(rpc_method=method).dec() # lastly, decreases gauge

        return grpc.unary_unary_rpc_method_handler(
            wrapped,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
