import httpx
import pytest

from app.core.auth import SupabaseTokenVerifier
from app.core.errors import AppError


def _verifier(handler):
    http = httpx.Client(base_url="https://example.supabase.co", transport=httpx.MockTransport(handler))
    return SupabaseTokenVerifier(http, "anon-key")


def test_valid_token_returns_user_id(user_a):
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["apikey"] = req.headers["apikey"]
        seen["path"] = req.url.path
        return httpx.Response(200, json={"id": user_a.upper()})

    assert _verifier(handler).verify("tok") == user_a
    assert seen == {"auth": "Bearer tok", "apikey": "anon-key", "path": "/auth/v1/user"}


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_token_is_auth_failed(status):
    with pytest.raises(AppError) as e:
        _verifier(lambda r: httpx.Response(status, json={})).verify("tok")
    assert e.value.code.value == "AUTH_FAILED" and e.value.status_code == 401


def test_garbage_user_id_is_auth_failed():
    with pytest.raises(AppError) as e:
        _verifier(lambda r: httpx.Response(200, json={"id": "../../etc"})).verify("tok")
    assert e.value.code.value == "AUTH_FAILED"


def test_upstream_outage_is_not_reported_as_auth_failure():
    def boom(req):
        raise httpx.ConnectError("down")

    with pytest.raises(AppError) as e:
        _verifier(boom).verify("secret-token")
    assert e.value.code.value == "INTERNAL_ERROR"
    assert "secret-token" not in e.value.message
    with pytest.raises(AppError) as e2:
        _verifier(lambda r: httpx.Response(503)).verify("tok")
    assert e2.value.code.value == "INTERNAL_ERROR"
