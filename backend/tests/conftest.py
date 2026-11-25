import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.auth import FakeTokenVerifier
from app.core.config import Settings
from app.core.llm import FakeLLMClient
from app.core.ratelimit import InMemoryUsageStore
from app.main import create_app

USER_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@pytest.fixture
def user_a():
    return USER_A


@pytest.fixture
def user_b():
    return USER_B


@pytest.fixture
def settings():
    return Settings(
        supabase_url="https://example.supabase.co",
        supabase_anon_key="test-anon-key",
        supabase_service_key="test-service-key",
        gemini_api_key="test-gemini-key",
        gemini_model="test-model",
        rate_limit_parse_per_day=5,
        rate_limit_analysis_per_day=10,
    )


@pytest.fixture
def make_app(settings):
    """建立測試用 app（假驗證、假 LLM、記憶體計數）。可傳入 settings 覆寫與 supabase handler。"""

    def _make(settings_override=None, supabase_handler=None, **kwargs):
        http = None
        if supabase_handler is not None:
            http = httpx.Client(base_url="https://example.supabase.co", transport=httpx.MockTransport(supabase_handler))
        return create_app(
            settings_override or settings,
            token_verifier=kwargs.pop("token_verifier", FakeTokenVerifier({"token-a": USER_A, "token-b": USER_B})),
            usage_store=kwargs.pop("usage_store", InMemoryUsageStore()),
            llm_client=kwargs.pop("llm_client", FakeLLMClient()),
            http_client=http,
            serve_frontend=False,
            **kwargs,
        )

    return _make


@pytest.fixture
def client(make_app):
    return TestClient(make_app(), raise_server_exceptions=False)


@pytest.fixture
def auth_a():
    return {"Authorization": "Bearer token-a"}


@pytest.fixture
def auth_b():
    return {"Authorization": "Bearer token-b"}


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def access_records():
    from app.core.logging import get_access_logger

    handler = ListHandler()
    logger = get_access_logger()
    logger.addHandler(handler)
    yield handler.records
    logger.removeHandler(handler)
