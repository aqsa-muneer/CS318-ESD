"""Pydantic models. FastAPI turns these into the OpenAPI schemas, so the spec cannot drift away from what the code actually returns."""
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# A city name: trimmed, 1-100 characters (so "   " is rejected as empty)
CityName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ErrorResponse(BaseModel):
    """Shape of every error we raise ourselves (404, 409, 429, 503)."""
    detail: str = Field(examples=["City not found"])


class Location(BaseModel):
    city: str
    region: str
    country: str
    latitude: float
    longitude: float
    timezone: str


class ForecastLocation(BaseModel):
    city: str
    region: str
    country: str
    timezone: str


# Upstream values can be null, so the numeric fields are optional.
class CurrentWeather(BaseModel):
    time: str
    temperature: float | None
    temperature_unit: str
    feels_like: float | None
    humidity: float | None
    humidity_unit: str
    precipitation: float | None
    precipitation_unit: str
    cloud_cover: float | None
    cloud_cover_unit: str
    wind_speed: float | None
    wind_speed_unit: str
    wind_direction: float | None
    wind_direction_unit: str
    weather_code: int | None
    condition: str


class WeatherResponse(BaseModel):
    location: Location
    current_weather: CurrentWeather


class ForecastDay(BaseModel):
    date: str
    max_temperature: float | None
    max_temperature_unit: str
    min_temperature: float | None
    min_temperature_unit: str
    precipitation_probability: float | None
    precipitation_probability_unit: str
    weather_code: int | None
    condition: str


class ForecastResponse(BaseModel):
    location: ForecastLocation
    forecast: list[ForecastDay]


class HomeResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str
    service: str


class SubscriptionCreate(BaseModel):
    """Body of POST /subscriptions."""
    model_config = ConfigDict(extra="forbid")  # unknown fields -> 422, not silently ignored
    city: CityName = Field(examples=["Karachi"])
    alert_above_c: float = Field(ge=-50, le=60, examples=[40.0],
                                 description="Alert when the temperature goes above this (deg C)")


class Subscription(BaseModel):
    id: str
    city: str
    country: str
    alert_above_c: float
    created_at: datetime