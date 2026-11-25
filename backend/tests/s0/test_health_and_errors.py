import json

import pytest
from fastapi import Depends
from pydantic import BaseModel

from app.core.auth import current_user
from app.core.errors import DEFAULT_MESSAGES, HTTP_STATUS, AppError, ErrorCode

# 契約 §9
CONTRACT = {
    "AUTH_FAILED": 401, "FORBIDDEN": 403, "NOT_FOUND": 404, "CONSENT_REQUIRED": 403,
    "FILE_INVALID": 400, "SCANNED_OR_EMPTY": 422, "SCHEMA_INVALID": 502, "VALIDATION_ERROR": 422,
    "LLM_ERROR": 502, "ANALYSIS_TIMEOUT": 504, "ANALYSIS_INVALID": 502, "RATE_LIMITED": 429,
    "STORAGE_ERROR": 500,
}


def test_health_needs_no_auth(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert r.headers["x-request-id"]
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_all_contract_error_codes_exist_with_status_and_chinese_message():
    for code, status in CONTRACT.items():
        ec = ErrorCode(code)
        assert HTTP_STATUS[ec] == status
        assert any("\u4e00" <= ch <= "\u9fff" for ch in DEFAULT_MESSAGES[ec])


@pytest.mark.parametrize("code", list(ErrorCode))
def test_app_error_format(make_app, code):
    app = make_app()

    @app.get("/api/_raise")
    def _raise():
        raise AppError(code)

    from fastapi.testclient import TestClient

    r = TestClient(app).get("/api/_raise")
    assert r.status_code == HTTP_STATUS[code]
    assert r.json() == {"error": {"code": code.value, "message": DEFAULT_MESSAGES[code]}}


def test_unknown_route_is_not_found_json(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_method_not_allowed_uses_error_format(client):
    r = client.post("/api/health")
    assert r.status_code == 405
    assert set(r.json()["error"]) >= {"code", "message"}


def test_missing_and_invalid_token_is_auth_failed(make_app):
    from fastapi.testclient import TestClient

    app = make_app()

    @app.get("/api/_me")
    def _me(uid: str = Depends(current_user)):
        return {"uid": uid}

    c = TestClient(app)
    assert c.get("/api/_me").json()["error"]["code"] == "AUTH_FAILED"
    r = c.get("/api/_me", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_FAILED"
    assert c.get("/api/_me", headers={"Authorization": "Basic abc"}).status_code == 401
    ok = c.get("/api/_me", headers={"Authorization": "Bearer token-a"})
    assert ok.status_code == 200


def test_validation_error_has_paths_and_never_echoes_input(make_app):
    from fastapi.testclient import TestClient

    class Body(BaseModel):
        rating: int
        name: str

    app = make_app()

    @app.put("/api/_v")
    def _v(body: Body):
        return {}

    r = TestClient(app).put("/api/_v", json={"rating": "SECRET_VALUE_XYZ"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    paths = {d["path"] for d in err["details"]}
    assert paths == {"rating", "name"}
    assert "SECRET_VALUE_XYZ" not in r.text


def test_unexpected_exception_returns_generic_500_without_details(make_app):
    from fastapi.testclient import TestClient

    app = make_app()

    @app.get("/api/_boom")
    def _boom():
        raise RuntimeError("SECRET_INTERNAL_DETAIL /home/user/file.py")

    r = TestClient(app, raise_server_exceptions=True).get("/api/_boom")  # 即使要求 raise 也不該外洩
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "INTERNAL_ERROR", "message": DEFAULT_MESSAGES[ErrorCode.INTERNAL_ERROR]}}
    assert "SECRET_INTERNAL_DETAIL" not in r.text


def test_app_error_str_contains_only_code():
    assert str(AppError(ErrorCode.LLM_ERROR, "任何訊息")) == "LLM_ERROR"
    json.dumps(AppError(ErrorCode.NOT_FOUND).message, ensure_ascii=False)
