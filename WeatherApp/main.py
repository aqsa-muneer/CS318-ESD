from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from weather import (
    get_coordinates,
    get_current_weather,
    get_forecast
)


app = FastAPI(
    title="Weather App API",
    description="Weather API for the ESD Observability Project",
    version="1.0"
)


@app.get("/")
def home():

    return {
        "message": "Weather API is running"
    }


@app.get("/weather")
def get_weather(city: str):

    try:
        # Step 1: Convert city name into coordinates
        location = get_coordinates(city)

        # City could not be found
        if location is None:
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)