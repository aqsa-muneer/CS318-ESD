# Image for the Weather App. One container = one replica, and each replica
# runs BOTH servers: gRPC (grpc_server.py) and HTTP (uvicorn main:app).
FROM python:3.12-slim

WORKDIR /app

# Print logs immediately instead of buffering them
ENV PYTHONUNBUFFERED=1

# Install dependencies first so Docker can cache this layer
# and skip reinstalling when only your code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000 50051 9101

CMD ["sh", "-c", "LOG_INTERFACE=grpc python grpc_server.py & exec env LOG_INTERFACE=http uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1"]
