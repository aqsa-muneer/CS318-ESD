import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler #Part C - log retention

# Directory where application logs will be stored
LOG_DIRECTORY = "logs"

# Create the directory if it does not already exist
os.makedirs(LOG_DIRECTORY, exist_ok=True)

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": record.levelname,
            "service": "weather-api",
            "message": record.getMessage()
        }

        # Add request-related fields when they exist
        extra_fields = [
            "request_id",
            "method",
            "endpoint",
            "status",
            "duration_ms",
            "city"
        ]

        for field in extra_fields:

            if hasattr(record, field):
                log_data[field] = getattr(record, field)

        return json.dumps(log_data)


# Create our application logger
logger = logging.getLogger("weather-api")

logger.setLevel(logging.INFO)

# Prevent duplicate logging
logger.propagate = False

# FILE HANDLER
# Writes JSON logs to logs/weather-app.log
file_handler = RotatingFileHandler(
    os.path.join(LOG_DIRECTORY, "weather-app.log"),
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=5,             # Keep 5 old log files
    encoding="utf-8"
)

file_handler.setFormatter(JSONFormatter())

logger.addHandler(file_handler)

# CONSOLE HANDLER
# Also shows JSON logs in the terminal
console_handler = logging.StreamHandler()

console_handler.setFormatter(JSONFormatter())

logger.addHandler(console_handler)