# CS318-ESD
FastAPI weather service with an OpenAPI spec, idempotent writes, rate limiting, a seilient Python caller, and a gRPC along with Prometheus/Grafana metrics.

# Setup
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# To run the frontend: cd frontend % npm run dev

# Run tests: 
pytest -q
Tests use fake_stream.py, an offline stand-in for Open-Meteo. 

# Run services:  
1. ensure docker desktop is up, and engine is running: docker compose up -d prometheus grafana node-exporter
Then in three separate terminals ensure you are in WeatherApp % and run the following three commands, simultaneously:
3. uvicorn main:app --port 8000
4. python grpc_server.py
5. WEATHER_FAKE_UPSTREAM=1 uvicorn main:app --port 8000
