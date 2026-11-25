import json

import httpx
import pytest

from app.core.db import AdminDb, Db, resume_storage_path
from app.core.errors import AppError

FILE_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def _http(handler):
    return httpx.Client(base_url="https://example.supabase.co", transport=httpx.MockTransport(handler))


def _capture(status=200, body=None):
    reqs = []

    def handler(req):
        reqs.append(req)
        return httpx.Response(status, json=[] if body is None else body)

    return reqs, handler


def test_user_db_uses_user_jwt_and_always_filters_by_user_id(settings, user_a):
    reqs, h = _capture()
    db = Db(_http(h), settings, user_a, "user-jwt")
    db.select("resume_profiles", filters={"status": "draft"}, order="created_at.desc", limit=1)
    r = reqs[0]
    assert r.headers["authorization"] == "Bearer user-jwt"
    assert r.headers["apikey"] == "test-anon-key"
    assert "test-service-key" not in str(r.headers)
    p = r.url.params
    assert p["user_id"] == f"eq.{user_a}" and p["status"] == "eq.draft" and p["limit"] == "1"


def test_update_and_delete_also_scoped(settings, user_a):
    reqs, h = _capture()
    db = Db(_http(h), settings, user_a, "jwt")
    db.update("resume_profiles", {"status": "confirmed"}, filters={"id": FILE_ID})
    db.delete("career_inputs", filters={"id": FILE_ID})
    for r in reqs:
        assert r.url.params["user_id"] == f"eq.{user_a}" and r.url.params["id"] == f"eq.{FILE_ID}"


def test_insert_injects_user_id_and_rejects_mismatch(settings, user_a, user_b):
    reqs, h = _capture(201, [{}])
    db = Db(_http(h), settings, user_a, "jwt")
    db.insert("consent_records", {"consent_type": "privacy", "version": "1"})
    assert json.loads(reqs[0].content) == [{"consent_type": "privacy", "version": "1", "user_id": user_a}]
    with pytest.raises(ValueError):
        db.insert("consent_records", {"consent_type": "privacy", "version": "1", "user_id": user_b})
    with pytest.raises(ValueError):
        db.update("resume_profiles", {"user_id": user_b}, filters={"id": FILE_ID})
    with pytest.raises(ValueError):
        db.select("resume_profiles", filters={"user_id": user_b})


def test_rejects_unknown_table_and_injection(settings, user_a):
    db = Db(_http(lambda r: httpx.Response(200, json=[])), settings, user_a, "jwt")
    with pytest.raises(ValueError):
        db.select("auth.users")
    with pytest.raises(ValueError):
        db.select("resume_profiles", filters={"id) or (1": "x"})
    with pytest.raises(ValueError):
        db.select("resume_profiles", columns="*;drop")


@pytest.mark.parametrize("status,code", [(401, "AUTH_FAILED"), (403, "FORBIDDEN"), (409, "VALIDATION_ERROR"),
                                         (500, "INTERNAL_ERROR")])
def test_postgrest_errors_are_mapped_without_body(settings, user_a, status, code):
    db = Db(_http(lambda r: httpx.Response(status, json={"message": "ROW_SECRET"})), settings, user_a, "jwt")
    with pytest.raises(AppError) as e:
        db.insert("consent_records", {"consent_type": "x", "version": "1"})
    assert e.value.code.value == code and "ROW_SECRET" not in e.value.message


def test_admin_always_requires_and_applies_user_id(settings, user_a):
    reqs, h = _capture()
    admin = AdminDb(_http(h), settings)
    admin.delete("resume_profiles", user_a)
    admin.select("resume_files", user_a, filters={"status": "delete_failed"})
    for r in reqs:
        assert r.url.params["user_id"] == f"eq.{user_a}"
        assert r.headers["authorization"] == "Bearer test-service-key"
    with pytest.raises(ValueError):
        admin.delete("resume_profiles", "not-a-uuid")
    with pytest.raises(TypeError):
        admin.delete("resume_profiles")  # type: ignore[call-arg]


def test_storage_paths_and_delete_semantics(settings, user_a):
    assert resume_storage_path(user_a, FILE_ID) == f"{user_a}/{FILE_ID}.pdf"
    with pytest.raises(ValueError):
        resume_storage_path(user_a, "../../other")
    reqs, h = _capture(200, [])
    admin = AdminDb(_http(h), settings)
    assert admin.delete_resume_pdf(user_a, FILE_ID) is True
    assert reqs[0].method == "DELETE"
    assert reqs[0].url.path == f"/storage/v1/object/resumes/{user_a}/{FILE_ID}.pdf"
    assert AdminDb(_http(lambda r: httpx.Response(404)), settings).delete_resume_pdf(user_a, FILE_ID) is True
    with pytest.raises(AppError) as e:
        AdminDb(_http(lambda r: httpx.Response(500)), settings).delete_resume_pdf(user_a, FILE_ID)
    assert e.value.code.value == "STORAGE_ERROR"


def test_signed_url_and_upload(settings, user_a):
    def h(req):
        if "sign" in req.url.path:
            assert json.loads(req.content) == {"expiresIn": 60}
            return httpx.Response(200, json={"signedURL": "/object/sign/resumes/x?token=t"})
        assert req.headers["content-type"] == "application/pdf" and req.content == b"%PDF"
        return httpx.Response(200, json={})

    admin = AdminDb(_http(h), settings)
    assert admin.signed_resume_url(user_a, FILE_ID).startswith("https://example.supabase.co/storage/v1/object/sign/")
    admin.upload_resume_pdf(user_a, FILE_ID, b"%PDF")
