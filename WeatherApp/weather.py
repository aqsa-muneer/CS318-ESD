import httpx

# Converts Open-Meteo weather codes into readable conditions
def get_weather_condition(code):
    weather_codes = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",

        45: "Fog",
        48: "Rime fog",

        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Heavy drizzle",

        56: "Light freezing drizzle",
        57: "Heavy freezing drizzle",

        61: "Light rain",
        63: "Moderate rain",
        65: "Heavy rain",

        66: "Light freezing rain",
        67: "Heavy freezing rain",

        71: "Light snow",
        73: "Moderate snow",
        75: "Heavy snow",
        77: "Snow grains",

        80: "Light rain showers",
        81: "Moderate rain showers",
        82: "Heavy rain showers",

        85: "Light snow showers",
        86: "Heavy snow showers",

        95: "Thunderstorm",
        96: "Thunderstorm with light hail",
        99: "Thunderstorm with heavy hail"
    }

    return weather_codes.get(code, "Unknown")


# Takes a city name and finds its latitude and longitude
def get_coordinates(city):

    url = "https://geocoding-api.open-meteo.com/v1/search"

    parameters = {
        "name": city,
        "count": 1,
        "language": "en",
        "format": "json"
    }

    try:
        response = httpx.get(
            url,
            params=parameters,
            timeout=5.0
        )

        # Raises an error if the API returned 4xx or 5xx
        response.raise_for_status()

        data = response.json()

        # No matching city was found
        if "results" not in data or len(data["results"]) == 0:
            return None

        result = data["results"][0]

        return {
            "name": result["name"],
            "country": result.get("country", ""),
            "region": result.get("admin1", ""),
            "latitude": result["latitude"],
            "longitude": result["longitude"],
            "timezone": result.get("timezone", "auto")
        }

    except httpx.RequestError:
        raise Exception("Could not connect to the location service")

    except httpx.HTTPStatusError:
        raise Exception("Location service returned an error")


# Gets the current weather using latitude and longitude
def get_current_weather(latitude, longitude, timezone):

    url = "https://api.open-meteo.com/v1/forecast"

    parameters = {
        "latitude": latitude,
        "longitude": longitude,

        "current": [
            "temperature_2m",
            "relative_humidity_2m",
            "apparent_temperature",
            "precipitation",
            "weather_code",
            "cloud_cover",
            "wind_speed_10m",
            "wind_direction_10m"
        ],

        "timezone": timezone
    }

    try:
        response = httpx.get(
            url,
            params=parameters,
            timeout=5.0
        )

        response.raise_for_status()

        data = response.json()

        current = data["current"]
        units = data["current_units"]

        return {
            "time": current["time"],

            "temperature": current["temperature_2m"],
            "temperature_unit": units["temperature_2m"],

            "feels_like": current["apparent_temperature"],

            "humidity": current["relative_humidity_2m"],
            "humidity_unit": units["relative_humidity_2m"],

            "precipitation": current["precipitation"],
            "precipitation_unit": units["precipitation"],

            "cloud_cover": current["cloud_cover"],
            "cloud_cover_unit": units["cloud_cover"],

            "wind_speed": current["wind_speed_10m"],
            "wind_speed_unit": units["wind_speed_10m"],

            "wind_direction": current["wind_direction_10m"],
            "wind_direction_unit": units["wind_direction_10m"],

            "weather_code": current["weather_code"],
            "condition": get_weather_condition(
                current["weather_code"]
            )
        }

    except httpx.RequestError:
        raise Exception("Could not connect to the weather service")

    except httpx.HTTPStatusError:
        raise Exception("Weather service returned an error")
    
def get_forecast(latitude, longitude, timezone):

    url = "https://api.open-meteo.com/v1/forecast"

    parameters = {
        "latitude": latitude,
        "longitude": longitude,

        "daily": [
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_probability_max"
        ],

        "timezone": timezone,
        "forecast_days": 5
    }

    try:
        response = httpx.get(
            url,
            params=parameters,
            timeout=5.0
        )

        response.raise_for_status()

        data = response.json()

        daily = data["daily"]
        units = data["daily_units"]

        forecast = []

        for i in range(len(daily["time"])):

            day = {
                "date": daily["time"][i],

                "max_temperature": daily["temperature_2m_max"][i],
                "max_temperature_unit": units["temperature_2m_max"],

                "min_temperature": daily["temperature_2m_min"][i],
                "min_temperature_unit": units["temperature_2m_min"],

                "precipitation_probability":
                    daily["precipitation_probability_max"][i],

                "precipitation_probability_unit":
                    units["precipitation_probability_max"],

                "weather_code": daily["weather_code"][i],

                "condition": get_weather_condition(
                    daily["weather_code"][i]
                )
            }

            forecast.append(day)

        return forecast

    except httpx.RequestError:
        raise Exception("Could not connect to the weather service")

    except httpx.HTTPStatusError:
        raise Exception("Weather service returned an error")