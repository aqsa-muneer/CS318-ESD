import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler #Part C - log retention

# ---------------------------------------------------------------------------
# A3: who is writing this log?
#   REPLICA_ID    -> A / B / C (set per container in docker-compose)
#   LOG_INTERFACE -> "http" or "grpc" (set per process in the Dockerfile CMD,
#                    because each container runs BOTH servers)
# Both are added to EVERY log line, so in Kibana you can filter by replica
# and interface without relying on the file name.
# ---------------------------------------------------------------------------
REPLICA_ID = os.getenv("REPLICA_ID")
LOG_INTERFACE = os.getenv("LOG_INTERFACE", "http")

# A3: directory comes from the environment. In Docker this is the folder
# mounted from ./logs (which Filebeat reads); a relative "logs" path would
# point INSIDE the container, where Filebeat can never see it.
LOG_DIRECTORY = os.getenv("LOG_DIR", "logs")

# A3: one file per process: weather-app-A-http.log, weather-app-A-grpc.log, ...
# RotatingFileHandler is only safe with ONE writer per file: when one process
# rotates (renames) the file, any other process writing to it keeps writing
# into the renamed old file, and lines end up lost or in the wrong place.
# Running locally without REPLICA_ID keeps the A2 name.
if REPLICA_ID:
    LOG_FILE_NAME = f"weather-app-{REPLICA_ID}-{LOG_INTERFACE}.log"
else:
    LOG_FILE_NAME = "weather-app.log"

# Create the directory if it does not already exist
os.makedirs(LOG_DIRECTORY, exist_ok=True)

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            # A3: record.created = when the event happened, not when it was formatted
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "severity": record.levelname,
            "service": "weather-api",
            "source": "app",                     # A3: Envoy's lines say "envoy"
            "replica_id": REPLICA_ID or "local", # A3
            "interface": LOG_INTERFACE,          # A3
            "message": record.getMessage()
        }

        # Add request-related fields when they exist
        extra_fields = [
            "request_id",
            "method",
            "endpoint",
            "status",
            "duration_ms",
            "city",
            "grpc_method",   # A3: gRPC per-call logs
            "grpc_status",   # A3
        ]

        for field in extra_fields:

            if hasattr(record, field):
                log_data[field] = getattr(record, field)

        # A3: logger.exception() attaches the stack trace as exc_info;
        # without this it was silently dropped from the JSON.
        if record.exc_info:
            log_data["error"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


# Create our application logger
logger = logging.getLogger("weather-api")

logger.setLevel(logging.INFO)

# Prevent duplicate logging
logger.propagate = False

# FILE HANDLER
# Writes JSON logs to <LOG_DIR>/<LOG_FILE_NAME>
file_handler = RotatingFileHandler(
    os.path.join(LOG_DIRECTORY, LOG_FILE_NAME),
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=5,             # Keep 5 old log files
    encoding="utf-8"
)

file_handler.setFormatter(JSONFormatter())

logger.addHandler(file_handler)

# CONSOLE HANDLER
# Also shows JSON logs in the terminal (and in `docker compose logs`)
console_handler = logging.StreamHandler()

console_handler.setFormatter(JSONFormatter())

logger.addHandler(console_handler)