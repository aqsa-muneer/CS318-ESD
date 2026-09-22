import { useState } from "react";
import "./App.css";

function App() {
  const [city, setCity] = useState("");
  const [weather, setWeather] = useState(null);
  const [error, setError] = useState("");

  async function searchWeather() {
    if (city.trim() === "") {
      setError("Please enter a city.");
      return;
    }

    setError("");

    try {
      const response = await fetch(
        `http://127.0.0.1:8000/weather?city=${encodeURIComponent(city)}`
      );

      if (!response.ok) {
        throw new Error("City not found.");
      }

      const data = await response.json();

      setWeather(data);
    } catch (error) {
      setWeather(null);
      setError(error.message);
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

        <button onClick={searchWeather}>
          Search
        </button>
      </div>

      {error && <p>{error}</p>}

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