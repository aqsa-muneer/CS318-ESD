from prometheus_client import Counter, Gauge, Histogram, Summary

# APPLICATION METRICS
# COUNTER
# Total number of HTTP requests received by the application
HTTP_REQUESTS = Counter(
    "weather_http_requests_total",
    "Total number of HTTP requests",
    ["method", "endpoint", "status"]
)

# GAUGE
# Number of requests currently being processed
ACTIVE_REQUESTS = Gauge(
    "weather_active_requests",
    "Number of requests currently being processed"
)

# HISTOGRAM
# Measures how long HTTP requests take
REQUEST_DURATION = Histogram(
    "weather_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"]
)

# SUMMARY
# Measures time spent communicating with the weather API
WEATHER_API_DURATION = Summary(
    "weather_external_api_duration_seconds",
    "Time spent communicating with the external weather API"
)

#######################################################################
# BUSINESS METRICS - how people are using the actual product
# Counts weather searches performed by users
WEATHER_SEARCHES = Counter(
    "weather_searches_total",
    "Total number of weather searches"
)
# Records the result of weather searches
WEATHER_SEARCH_RESULTS = Counter(
    "weather_search_results_total",
    "Weather search results",
    ["result"]
)

# ASSINGMENT 2 CHANGES
# IDEMPOTENCY -- outcomes of POST /subscriptions idempotency handling
IDEMPOTENCY_REQUESTS = Counter(
    "weather_idempotency_requests_total",
    "Idempotency-Key outcomes for POST /subscriptions",
    ["result"]
)