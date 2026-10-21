# ASSIGNMENT 2 PART A
import os
from typing import Annotated
from fastapi import FastAPI, HTTPException, Request, Depends, Header, Query
from fastapi.exceptions import RequestValidationError

from fastapi.middleware.cors import CORSMiddleware
from weather import (get_coordinates, get_current_weather, get_forecast)

# Part B
import time
import uuid # Part C
import logging # Part C
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

from logging_config import logger #Part C

from metrics import(HTTP_REQUESTS, ACTIVE_REQUESTS, REQUEST_DURATION, WEATHER_SEARCHES, WEATHER_SEARCH_RESULTS, IDEMPOTENCY_REQUESTS)

# ASSIGNMENT 2 PART A
from schemas import(ErrorResponse, WeatherResponse, ForecastResponse, HomeResponse, HealthResponse, SubscriptionCreate, Subscription)
from ratelimit import rate_limit_weather
from idempotency import IdempotencyStore, fingerprint
from subscriptions import SubscriptionRepository

if os.getenv("WEATHER_FAKE_UPSTREAM") == "1":
    import fake_upstream
    fake_upstream.install()

app = FastAPI(
    title="Weather App API",
    description="Weather API",
    version="1.1.0", # changed from 1.0 to 1.1: validation, rate limit, POST /subscriptions
    servers=[{"url": "http://localhost:8000", "description": "Local development"}]
)

idempotency_store = IdempotencyStore()
subscription_repo = SubscriptionRepository()

app.add_middleware( #from fastapi.middleware.cors import CORSMiddleware
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Idempotent-Replayed"]
)
def endpoint_label(request) -> str:
    """Route TEMPLATE (e.g. "/weather") instead of the raw URL path.
    Raw paths make a metric label unbounded: a scanner requesting /a, /b, /c...
    would create one time series per path. Unmatched URLs share one label."""
    route = request.scope.get("route")
    return route.path if route is not None else "unmatched"

#Part B + C -- metrics + request logging
@app.middleware("http")
async def observability_middleware(request, call_next):
    # Give every HTTP request a unique ID.
    request_id = str(uuid.uuid4())
    # Make the request ID available to endpoint functions.
    request.state.request_id = request_id
    # Prometheus Gauge: one more request is now active.
    ACTIVE_REQUESTS.inc()
    # Start timing the complete HTTP request.
    start_time = time.perf_counter()
    try:
        # Allow FastAPI to process the request.
        response = await call_next(request)
        # Calculate complete request duration.
        duration = time.perf_counter() - start_time
        duration_ms = duration * 1000
        # The router has matched a route by now, so the label can be read.
        endpoint = endpoint_label(request)

        REQUEST_DURATION.labels(
            method=request.method,
            endpoint=endpoint
        ).observe(duration)

        HTTP_REQUESTS.labels(
            method=request.method,
            endpoint=endpoint,
            status=str(response.status_code)
        ).inc()
        # 4xx and 5xx responses are logged as WARNING.
        if response.status_code >= 400:
            log_level = logging.WARNING
        else:
            log_level = logging.INFO

        logger.log(
            log_level,
            "Request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "endpoint": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 2)
            }
        )
        # Return request ID to the client as well.
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception:
        # Calculate how long the request ran before failing.
        duration = time.perf_counter() - start_time
        duration_ms = duration * 1000
        # unhandled exceptions counted as status 500.
        endpoint = endpoint_label(request)
        REQUEST_DURATION.labels(method=request.method, endpoint=endpoint).observe(duration)
        HTTP_REQUESTS.labels(method=request.method, endpoint=endpoint, status="500").inc()
        # Record unexpected failures as ERROR.
        logger.exception(
            "Request failed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "endpoint": request.url.path,
                "status": 500,
                "duration_ms": round(duration_ms, 2)
            }
        )
        raise
    finally:
        ACTIVE_REQUESTS.dec() #request has finished, thus no longer active

# Input rule shared by /weather and /forecast: 1-100 characters with at least one
# non-space character. Empty input used to reach Open-Meteo and come back as a misleading 503.
CityQuery = Annotated[str, Query(
    min_length=1, max_length=100, pattern=r"\S",
    description="City name to look up", examples=["Karachi"])]

# Reusable documentation for the error responses, so the OpenAPI document lists them.
R_404 = {"model": ErrorResponse, "description": "City not found"}
R_503 = {"model": ErrorResponse,
         "description": "Location or weather service unreachable or returned an error"}
RATE_HEADERS = {
    "X-RateLimit-Limit": {"description": "Requests allowed per window", "required": True,
                          "schema": {"type": "integer"}},
    "X-RateLimit-Remaining": {"description": "Requests left in the current window", "required": True,
                              "schema": {"type": "integer"}},
}
R_429 = {
    "model": ErrorResponse,
    "description": "Rate limit exceeded (per client IP). Wait Retry-After seconds.",
    "headers": {
        "Retry-After": {"description": "Whole seconds until a request is allowed again",
                        "required": True, "schema": {"type": "integer", "minimum": 1}},
        **RATE_HEADERS,
    },
}

@app.get("/", response_model=HomeResponse, tags=["system"])
def home():

    return {
        "message": "Weather API is running"
    }

@app.get("/weather", response_model=WeatherResponse, tags=["weather"], summary="Current weather for a city",
    # The limiter runs before the function body, so a limited call never costs an upstream request.
    dependencies=[Depends(rate_limit_weather)],
    responses={200: {"description": "Current weather", "headers": RATE_HEADERS}, 404: R_404, 429: R_429, 503: R_503})

def get_weather(city: CityQuery, request: Request):
    city = city.strip()
    WEATHER_SEARCHES.inc() #every user search increases weather_searches_total
    logger.info(
        "Weather search started",
        extra={
            "request_id": request.state.request_id,
            "city": city,
            "endpoint": "/weather"
        }
    )
    try:
        # Step 1: Convert city name into coordinates
        location = get_coordinates(city)
        # City could not be found
        if location is None:
            WEATHER_SEARCH_RESULTS.labels(result = "not_found").inc() # invalid search
            logger.warning(
                "City not found",
                extra={
                    "request_id": request.state.request_id,
                    "city": city,
                    "endpoint": "/weather"
                }
            )
            raise HTTPException(
                status_code=404,
                detail="City not found"
            )
        # Step 2: Get weather using those coordinates
        weather = get_current_weather(
            location["latitude"],
            location["longitude"],
            location["timezone"]
        )
        WEATHER_SEARCH_RESULTS.labels(result = "success").inc()

        logger.info(
            "Weather search successful",
            extra={
                "request_id": request.state.request_id,
                "city": city,
                "endpoint": "/weather"
            }
        )

        # Step 3: Return clean data to the frontend
        return {
            "location": {
                "city": location["name"],
                "region": location["region"],
                "country": location["country"],
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "timezone": location["timezone"]
            },

            "current_weather": weather
        }

    except HTTPException:
        # Allow our 404 error to pass through normally
        raise

    except Exception as error:
        WEATHER_SEARCH_RESULTS.labels(result = "error").inc()
        logger.error(
            "Weather search failed.",
            extra = {
                "request_id": request.state.request_id,
                "city": city,
                "endpoint": "/weather"
            }
        )
        raise HTTPException(
            status_code=503,
            detail=str(error)
        )

@app.get("/forecast", response_model=ForecastResponse, tags=["weather"],
         summary="5-day forecast for a city",
         responses={404: R_404, 503: R_503})
def forecast(city: CityQuery):
    city = city.strip()
    try:
        # Find the city
        location = get_coordinates(city)

        if location is None:
            raise HTTPException(
                status_code=404,
                detail="City not found"
            )

        # Get the 5-day forecast
        forecast_data = get_forecast(
            location["latitude"],
            location["longitude"],
            location["timezone"]
        )

        return {
            "location": {
                "city": location["name"],
                "region": location["region"],
                "country": location["country"],
                "timezone": location["timezone"]
            },

            "forecast": forecast_data
        }

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail=str(error)
        )

@app.post(
    "/subscriptions",
    status_code=201,
    response_model=Subscription,
    tags=["subscriptions"],
    summary="Create a heat-alert subscription (safe to retry with the same Idempotency-Key)",
    responses={
        201: {"description": "Created, or a replay of the earlier identical request",
              "headers": {"Idempotent-Replayed": {
                  "description": "\"true\" when this is a replay of a stored response",
                  "schema": {"type": "string"}}}},
        404: R_404,
        409: {"model": ErrorResponse,
              "description": "The first request with this key is still running; retry shortly",
              "headers": {"Retry-After": {"required": True, "schema": {"type": "integer"},
                                          "description": "Seconds to wait before retrying"}}},
        503: R_503,
    },
)
def create_subscription(
    body: SubscriptionCreate,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(
        alias="Idempotency-Key", min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$",
        description="Client-generated unique key (e.g. a UUID). Reuse it when retrying.")],
):
    key = idempotency_key
    decision = idempotency_store.begin(key, fingerprint(body.model_dump()))

    if decision.kind == "replay":
        # Same key, same body, already done: return the stored answer, do NOT create again.
        IDEMPOTENCY_REQUESTS.labels(result="replayed").inc()
        response.headers["Idempotent-Replayed"] = "true"
        response.status_code = decision.status
        return decision.body

    if decision.kind == "in_progress":
        IDEMPOTENCY_REQUESTS.labels(result="in_progress").inc()
        raise HTTPException(
            status_code=409,
            detail="A request with this Idempotency-Key is still being processed",
            headers={"Retry-After": "1"}
        )

    if decision.kind == "key_reuse":
        # Raised as a validation error so the 422 has the same shape as every other 422.
        IDEMPOTENCY_REQUESTS.labels(result="key_reuse").inc()
        raise RequestValidationError([{
            "type": "idempotency_key_reuse",
            "loc": ("header", "Idempotency-Key"),
            "msg": "Idempotency-Key was already used with a different request body",
            "input": key
        }])

    # decision.kind == "new": this request owns the key.
    # Everything that can fail happens BEFORE the side effect. If it fails we release the key, so a retry with the same key simply runs again.
    try:
        location = get_coordinates(body.city)
        if location is None:
            raise HTTPException(status_code=404, detail="City not found")
    except HTTPException:
        idempotency_store.abandon(key)
        raise
    except Exception as error:
        idempotency_store.abandon(key)
        logger.error("Subscription failed", extra={
            "request_id": request.state.request_id, "city": body.city, "endpoint": "/subscriptions"})
        raise HTTPException(status_code=503, detail=str(error))

    # THE side effect, kept as the last step; the result is stored immediately after.
    record = subscription_repo.create(location["name"], location["country"], body.alert_above_c)
    result = Subscription(**record).model_dump(mode="json")
    idempotency_store.finish(key, 201, result)
    IDEMPOTENCY_REQUESTS.labels(result="created").inc()
    return result

@app.get("/subscriptions", response_model=list[Subscription], tags=["subscriptions"],
         summary="List subscriptions (used to verify retries created no duplicates)")
def list_subscriptions():
    return subscription_repo.all()

@app.get("/health", response_model=HealthResponse, tags=["system"])
def health():
    return {
        "status": "healthy",
        "service": "weather-api"
    }

@app.get("/metrics", tags=["system"], response_class=Response, responses={200: {"description": "Prometheus text exposition format", "content": {"text/plain": {"schema": {"type": "string"}}}}})
def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )

# Part C: controlled endpoint to test ERROR logging
# creates a new FastAPI endpoint: GET /test-error
@app.get("/test-error", tags=["internal"], status_code=500, responses={500: {"description": "Always fails (test hook for ERROR logging)"}})
def test_error():
    raise RuntimeError("Simulated server failure for observability testing")