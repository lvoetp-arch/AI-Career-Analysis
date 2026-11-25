import json

from fastapi import Request
from fastapi.testclient import TestClient

from app.core.logging import ALLOWED_FIELDS, JsonFormatter, configure_logging

SENTINEL = "RESUME_SENTINEL_陳大文_0912"


def _app(make_app):
    app = make_app()

    @app.post("/api/_echo")
    async def _echo(request: Request):
        await request.body()
        return {"ok": True}

    @app.post("/api/_boom")
    async def _boom(request: Request):
        raise RuntimeError(SENTINEL)

    return TestClient(app)


def _dump(records):
    fmt = JsonFormatter()
    return "\n".join(fmt.format(r) for r in records)


def test_log_contains_only_allowed_fields(make_app, access_records):
    c = _app(make_app)
    c.get("/api/health")
    rec = access_records[-1]
    out = json.loads(JsonFormatter().format(rec))
    assert set(out) <= {"ts", "level", "event", *ALLOWED_FIELDS}
    assert out["path"] == "/api/health" and out["status"] == 200
    assert isinstance(out["duration_ms"], float) and out["request_id"]


def test_log_never_contains_request_body_query_or_headers(make_app, access_records):
    c = _app(make_app)
    c.post(f"/api/_echo?q={SENTINEL}", json={"resume": SENTINEL}, headers={"Authorization": "Bearer RESUME_SENTINEL_TOKEN"})
    assert SENTINEL not in _dump(access_records)
    assert "RESUME_SENTINEL_TOKEN" not in _dump(access_records)
    assert SENTINEL not in repr([r.__dict__ for r in access_records])


def test_log_never_contains_exception_message_and_records_code(make_app, access_records):
    c = _app(make_app)
    r = c.post("/api/_boom", json={"resume": SENTINEL})
    assert r.status_code == 500 and SENTINEL not in r.text
    dumped = _dump(access_records)
    assert SENTINEL not in dumped
    assert json.loads(dumped.splitlines()[-1])["error_code"] == "INTERNAL_ERROR"


def test_error_code_is_logged_for_app_errors(make_app, access_records):
    c = _app(make_app)
    c.get("/api/nope")
    assert json.loads(JsonFormatter().format(access_records[-1]))["error_code"] == "NOT_FOUND"


def test_formatter_drops_unexpected_extras():
    import logging

    rec = logging.LogRecord("x", logging.INFO, __file__, 1, f"msg {SENTINEL}", None, None)
    rec.resume_text = SENTINEL
    assert SENTINEL not in JsonFormatter().format(rec)


def test_configure_logging_quiets_third_party_loggers():
    import logging

    configure_logging()
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("uvicorn.access").disabled
