import { useState, useEffect } from "react"; //useEffect runs the countdown timer
import "./App.css";

function App() {
  const [city, setCity] = useState("");
  const [weather, setWeather] = useState(null);
  const [error, setError] = useState("");
  const [waitSeconds, setWaitSeconds] = useState(0);

  useEffect(() => {
    if (waitSeconds <= 0) return;
    const timer = setTimeout(() => setWaitSeconds(waitSeconds - 1), 1000);
    return () => clearTimeout(timer);
  }, [waitSeconds]);

  async function searchWeather() {
    if (city.trim() === "") {
      setError("Please enter a city.");
      return;
    }

    if (waitSeconds > 0) {
      return;
    }

    setError("");

    try {
      const response = await fetch(
        `http://127.0.0.1:8000/weather?city=${encodeURIComponent(city.trim())}`
      );

      if (response.status === 429) {
        const retryAfter = parseInt(response.headers.get("Retry-After"), 10);
        setWeather(null);
        setWaitSeconds(Number.isNaN(retryAfter) ? 60 : retryAfter);
        return;
      }

      if (response.status === 404) {
        throw new Error("City not found.");
      }

      if (response.status === 422) {
        throw new Error("Please enter a valid city name (1-100 characters).");
      }

      if (response.status === 503) {
        throw new Error(
          "The weather service is temporarily unavailable. Please try again shortly."
        );
      }

      if (!response.ok) {
        throw new Error("Something went wrong. Please try again.");
      }

      const data = await response.json();

      setWeather(data);
    } catch (error) {
      setWeather(null);
      setError(
        error instanceof TypeError
          ? "Could not reach the server."
          : error.message
      );
    }
  }

  return (
    <div className="app">

      <h1>Weather App</h1>

      <div className="search">
        <input
          type="text"
          placeholder="Search for a city"
          value={city}
          onChange={(event) => setCity(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              searchWeather();
            }
          }}
        />

        <button onClick={searchWeather} disabled={waitSeconds > 0}>
          {waitSeconds > 0 ? `Wait ${waitSeconds}s` : "Search"}
        </button>
      </div>

      {error && <p>{error}</p>}

      {waitSeconds > 0 && (
        <p>Too many searches. You can search again in {waitSeconds} seconds.</p>
      )}

      {weather && (
        <div>
          <h2>
            {weather.location.city}, {weather.location.country}
          </h2>

          <h1>
            {weather.current_weather.temperature}
            {weather.current_weather.temperature_unit}
          </h1>

          <h3>
            {weather.current_weather.condition}
          </h3>

          <p>
            Feels like: {weather.current_weather.feels_like}
            {weather.current_weather.temperature_unit}
          </p>

          <p>
            Humidity: {weather.current_weather.humidity}
            {weather.current_weather.humidity_unit}
          </p>

          <p>
            Wind: {weather.current_weather.wind_speed}{" "}
            {weather.current_weather.wind_speed_unit}
          </p>

          <p>
            Precipitation: {weather.current_weather.precipitation}{" "}
            {weather.current_weather.precipitation_unit}
          </p>
        </div>
      )}

    </div>
  );
}

export default App;