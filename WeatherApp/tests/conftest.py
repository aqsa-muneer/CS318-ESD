import logging
import os

os.environ["WEATHER_FAKE_UPSTREAM"] = "1"
os.environ["FAKE_LATENCY_SCALE"] = "0"

import pytest
from fastapi.testclient import TestClient

import fake_upstream
import main
from logging_config import logger
from ratelimit import weather_limiter

logger.setLevel(logging.CRITICAL)


@pytest.fixture(autouse=True)
def clean_state():
    fake_upstream.fake.reset()
    weather_limiter.reset()
    main.idempotency_store.clear()
    main.subscription_repo.clear()
    yield


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)
