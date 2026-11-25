from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.core.db import AdminDb
from app.core.ratelimit import InMemoryUsageStore, RateLimiter, SupabaseUsageStore, rate_limit, today_taipei


def _app_with_endpoints(make_app, **kw):
    app = make_app(**kw)

    @app.post("/api/_parse", dependencies=[Depends(rate_limit("parse"))])
    def _parse():
        return {"ok": True}

    @app.post("/api/_analyze", dependencies=[Depends(rate_limit("analysis"))])
    def _analyze():
        return {"ok": True}

    return TestClient(app)


def test_parse_limit_default_5_then_rate_limited(make_app, auth_a):
    c = _app_with_endpoints(make_app)
    for _ in range(5):
        assert c.post("/api/_parse", headers=auth_a).status_code == 200
    r = c.post("/api/_parse", headers=auth_a)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMITED"
    assert "5" in r.json()["error"]["message"]


def test_analysis_limit_default_10(make_app, auth_a):
    c = _app_with_endpoints(make_app)
    for _ in range(10):
        assert c.post("/api/_analyze", headers=auth_a).status_code == 200
    assert c.post("/api/_analyze", headers=auth_a).status_code == 429


def test_limits_are_per_user_and_per_kind(make_app, auth_a, auth_b):
    c = _app_with_endpoints(make_app)
    for _ in range(5):
        c.post("/api/_parse", headers=auth_a)
    assert c.post("/api/_parse", headers=auth_a).status_code == 429
    assert c.post("/api/_parse", headers=auth_b).status_code == 200
    assert c.post("/api/_analyze", headers=auth_a).status_code == 200


def test_unauthenticated_request_does_not_consume(make_app):
    c = _app_with_endpoints(make_app)
    assert c.post("/api/_parse").status_code == 401


def test_limit_configurable_via_settings(make_app, settings):
    from dataclasses import replace

    c = _app_with_endpoints(make_app, settings_override=replace(settings, rate_limit_parse_per_day=1))
    h = {"Authorization": "Bearer token-a"}
    assert c.post("/api/_parse", headers=h).status_code == 200
    assert c.post("/api/_parse", headers=h).status_code == 429


def test_resets_next_day_taipei(settings, user_a):
    now = {"t": datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)}  # 台北 23:00
    limiter = RateLimiter(settings, InMemoryUsageStore(), clock=lambda: now["t"])
    for _ in range(5):
        limiter.consume(user_a, "parse")
    try:
        limiter.consume(user_a, "parse")
        raise AssertionError("應該被限制")
    except Exception as e:
        assert getattr(e, "status_code", None) == 429
    now["t"] += timedelta(hours=2)  # 台北隔天 01:00（UTC 仍是同一天）
    limiter.consume(user_a, "parse")
    assert today_taipei(now["t"]).isoformat() == "2026-11-11"


@pytest.mark.parametrize(("disabled", "enabled"), [("parse", "analysis"), ("analysis", "parse")])
def test_disabled_limit_does_not_use_usage_store(settings, user_a, disabled, enabled):
    from dataclasses import replace
    from unittest.mock import Mock

    store = Mock(spec=InMemoryUsageStore)
    limiter = RateLimiter(replace(settings, **{f"rate_limit_{disabled}_per_day": 0}), store)
    for _ in range(11):
        limiter.consume(user_a, disabled)
    store.consume.assert_not_called()
    limiter.consume(user_a, enabled)
    store.consume.assert_called_once()


def test_supabase_store_uses_atomic_rpc_and_maps_null_to_limited(settings, user_a):
    calls = []
    results = iter([1, None])

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=next(results))

    http = httpx.Client(base_url="https://example.supabase.co", transport=httpx.MockTransport(handler))
    store = SupabaseUsageStore(AdminDb(http, settings))
    from datetime import date

    assert store.consume(user_a, date(2026, 11, 10), "parse", 5) is True
    assert store.consume(user_a, date(2026, 11, 10), "parse", 5) is False
    req = calls[0]
    assert req.url.path == "/rest/v1/rpc/consume_usage"
    assert req.headers["authorization"] == "Bearer test-service-key"
    import json

    assert json.loads(req.content) == {"p_user_id": user_a, "p_day": "2026-11-10", "p_kind": "parse", "p_limit": 5}
