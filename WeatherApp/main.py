from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from weather import (get_coordinates, get_current_weather, get_forecast)

# Part B
import time
import uuid # Part C
import logging # Part C
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

from logging_config import logger #Part C

from metrics import(
    HTTP_REQUESTS, ACTIVE_REQUESTS, REQUEST_DURATION, WEATHER_SEARCHES, WEATHER_SEARCH_RESULTS)

app = FastAPI(
    title="Weather App API",
    description="Weather API for the ESD Observability Project",
    version="1.0"
)

app.add_middleware( #from fastapi.middleware.cors import CORSMiddleware
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

        REQUEST_DURATION.labels(
            method=request.method,
            endpoint=request.url.path
        ).observe(duration)

        HTTP_REQUESTS.labels(
            method=request.method,
            endpoint=request.url.path,
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

@app.get("/")
def home():

    return {
        "message": "Weather API is running"
    }


@app.get("/weather")
def get_weather(city: str, request: Request):
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
    
@app.get("/forecast")
def forecast(city: str):

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


@app.get("/health")
def health():

    return {
        "status": "healthy",
        "service": "weather-api"
    }

@app.get("/metrics")
def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )