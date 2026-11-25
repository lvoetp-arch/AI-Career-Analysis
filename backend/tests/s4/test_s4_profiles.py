"""S4 後端測試：用假 Db / 假 AdminDb 驅動真實的 router、errors 與 S3 的 delete_original_file。"""
import copy
import json
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_admin_db, get_db
from app.core.errors import AppError, ErrorCode, register_exception_handlers
from app.routers import profiles

USER = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
PID = str(uuid.uuid4())
FID = str(uuid.uuid4())


def _find_fixture():
    root = Path(__file__).resolve().parents[3]
    for d in ("docs", "backend", "frontend", "scripts", "fixtures", "tests"):
        for p in (root / d).rglob("sample_resume_profile.fixture.json"):
            return p
    return None


def base_profile():
    f = _find_fixture()
    if f:
        return json.loads(f.read_text(encoding="utf-8-sig"))
    pytest.skip("找不到 sample_resume_profile.fixture.json")


class FakeDb:
    def __init__(self, user_id, rows):
        self.user_id, self.rows = user_id, rows

    def select(self, table, *, filters=None, columns="*", order=None, limit=None):
        rows = [r for r in self.rows if r["user_id"] == self.user_id and all(r.get(k) == v for k, v in (filters or {}).items())]
        if order == "created_at.desc":
            rows = sorted(rows, key=lambda r: r["created_at"], reverse=True)
        return [dict(r) for r in (rows[:limit] if limit else rows)]

    def select_one(self, table, *, filters=None, columns="*", order=None):
        rows = self.select(table, filters=filters, columns=columns, order=order, limit=1)
        return rows[0] if rows else None

    def update(self, table, values, *, filters):
        hit = [r for r in self.rows if r["user_id"] == self.user_id and all(r.get(k) == v for k, v in filters.items())]
        for r in hit:
            r.update(values)
        return [dict(r) for r in hit]


class FakeAdmin:
    def __init__(self):
        self.files = {FID: {"user_id": USER, "status": "parsed"}}
        self.storage = {FID}
        self.fail_delete = False
        self.delete_calls = 0

    def select(self, table, user_id, *, filters=None, columns="*", limit=None):
        return [{"id": k, **v} for k, v in self.files.items() if v["user_id"] == user_id and k == filters["id"]]

    def update(self, table, user_id, values, *, filters):
        f = self.files.get(filters["id"])
        if f and f["user_id"] == user_id:
            f.update(values)
            return [f]
        return []

    def delete_resume_pdf(self, user_id, resume_file_id):
        self.delete_calls += 1
        if self.fail_delete:
            raise AppError(ErrorCode.STORAGE_ERROR)
        self.storage.discard(resume_file_id)
        return True


@pytest.fixture
def env():
    prof = base_profile()
    rows = [{"id": PID, "user_id": USER, "status": "draft", "parsed_json": copy.deepcopy(prof), "resume_file_id": FID,
             "created_at": "2026-11-18T10:00:00+00:00", "confirmed_at": None}]
    db, admin = FakeDb(USER, rows), FakeAdmin()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(profiles.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_admin_db] = lambda: admin
    return TestClient(app), db, admin, prof


def paths(resp):
    return {d["path"] for d in resp.json()["error"]["details"]}


# ---------- GET latest ----------
def test_latest_returns_newest(env):
    c, db, _, prof = env
    db.rows.append({"id": str(uuid.uuid4()), "user_id": USER, "status": "draft", "parsed_json": {"x": 1},
                    "resume_file_id": None, "created_at": "2026-11-19T10:00:00+00:00"})
    r = c.get("/api/profiles/latest")
    assert r.status_code == 200 and r.json()["parsed_json"] == {"x": 1}


def test_latest_404_when_none(env):
    c, db, _, _ = env
    db.rows.clear()
    assert c.get("/api/profiles/latest").status_code == 404


# ---------- PUT ----------
def test_put_accepts_fixture_with_12_jobs_and_edits(env):
    c, db, _, prof = env
    assert len(prof["work_experience"]) == 12
    prof["work_experience"].pop(0)
    prof["work_experience"][0]["company"] = "改過的公司"
    r = c.put(f"/api/profiles/{PID}", json={"parsed_json": prof})
    assert r.status_code == 200, r.text
    assert len(db.rows[0]["parsed_json"]["work_experience"]) == 11
    assert db.rows[0]["parsed_json"]["work_experience"][0]["company"] == "改過的公司"
    assert db.rows[0]["updated_at"]


def test_put_rejects_phone_and_gender_with_paths_and_no_echo(env):
    c, db, _, prof = env
    before = copy.deepcopy(db.rows[0]["parsed_json"])
    prof["phone"] = "SENTINEL-PHONE-VALUE"
    prof["basics"]["phone"] = "SENTINEL-PHONE-VALUE"
    prof["work_experience"][0]["gender"] = "female"
    r = c.put(f"/api/profiles/{PID}", json={"parsed_json": prof})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert {"phone", "basics.phone", "work_experience[0].gender"} <= paths(r)
    assert "SENTINEL-PHONE-VALUE" not in r.text and "female" not in r.text  # 不回顯使用者輸入
    assert db.rows[0]["parsed_json"] == before  # 沒有被寫入


def test_put_bad_date_and_long_autobiography(env):
    c, _, _, prof = env
    prof["work_experience"][1]["start"] = "2020/05"
    prof["autobiography"]["text"] = "字" * 6001
    r = c.put(f"/api/profiles/{PID}", json={"parsed_json": prof})
    assert r.status_code == 422
    assert {"work_experience[1].start", "autobiography.text"} <= paths(r)


def test_put_missing_required(env):
    c, _, _, prof = env
    del prof["skills"]
    r = c.put(f"/api/profiles/{PID}", json={"parsed_json": prof})
    assert r.status_code == 422 and "skills" in paths(r)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "  JaVa\tScript:alert(1)", "data:text/html,x"])
def test_put_rejects_dangerous_url_schemes(env, url):
    c, _, _, prof = env
    prof["portfolio"][0]["url"] = url
    r = c.put(f"/api/profiles/{PID}", json={"parsed_json": prof})
    assert r.status_code == 422 and "portfolio[0].url" in paths(r)


def test_put_rejects_extra_body_key_and_missing_parsed_json(env):
    c, _, _, prof = env
    r = c.put(f"/api/profiles/{PID}", json={"parsed_json": prof, "phone": "x"})
    assert r.status_code == 422 and "phone" in paths(r)
    r = c.put(f"/api/profiles/{PID}", json={})
    assert r.status_code == 422 and "parsed_json" in paths(r)


def test_put_too_large(env):
    c, _, _, prof = env
    prof["autobiography"]["sections"] = [{"title": "x", "text": "字" * 5000}] * 60
    r = c.put(f"/api/profiles/{PID}", json={"parsed_json": prof})
    assert r.status_code == 422


def test_put_unknown_or_foreign_or_bad_id_is_404(env):
    c, db, _, prof = env
    assert c.put(f"/api/profiles/{uuid.uuid4()}", json={"parsed_json": prof}).status_code == 404
    assert c.put("/api/profiles/not-a-uuid", json={"parsed_json": prof}).status_code == 404
    db.user_id = OTHER
    assert c.put(f"/api/profiles/{PID}", json={"parsed_json": prof}).status_code == 404


# ---------- confirm ----------
def test_confirm_deletes_original(env):
    c, db, admin, _ = env
    r = c.post(f"/api/profiles/{PID}/confirm")
    assert r.status_code == 200 and r.json()["original_file_deleted"] is True
    assert db.rows[0]["status"] == "confirmed" and db.rows[0]["confirmed_at"]
    assert admin.files[FID]["status"] == "deleted" and FID not in admin.storage


def test_confirm_delete_failure_still_confirms_and_marks_delete_failed(env):
    c, db, admin, _ = env
    admin.fail_delete = True
    r = c.post(f"/api/profiles/{PID}/confirm")
    assert r.status_code == 200 and r.json()["original_file_deleted"] is False
    assert db.rows[0]["status"] == "confirmed"
    assert admin.files[FID]["status"] == "delete_failed" and FID in admin.storage


def test_confirm_again_retries_delete_and_keeps_confirmed_at(env):
    c, db, admin, _ = env
    admin.fail_delete = True
    c.post(f"/api/profiles/{PID}/confirm")
    first = db.rows[0]["confirmed_at"]
    admin.fail_delete = False
    r = c.post(f"/api/profiles/{PID}/confirm")
    assert r.json()["original_file_deleted"] is True and admin.files[FID]["status"] == "deleted"
    assert db.rows[0]["confirmed_at"] == first


def test_confirm_again_after_deleted_does_not_call_storage(env):
    c, _, admin, _ = env
    c.post(f"/api/profiles/{PID}/confirm")
    calls = admin.delete_calls
    r = c.post(f"/api/profiles/{PID}/confirm")
    assert r.json()["original_file_deleted"] is True and admin.delete_calls == calls


def test_confirm_survives_unexpected_exception_in_delete(env, monkeypatch):
    c, db, admin, _ = env
    monkeypatch.setattr(profiles, "delete_original_file", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    r = c.post(f"/api/profiles/{PID}/confirm")
    assert r.status_code == 200 and r.json()["original_file_deleted"] is False
    assert db.rows[0]["status"] == "confirmed" and admin.files[FID]["status"] == "delete_failed"


def test_confirm_404_for_unknown_or_foreign(env):
    c, db, _, _ = env
    assert c.post(f"/api/profiles/{uuid.uuid4()}/confirm").status_code == 404
    db.user_id = OTHER
    assert c.post(f"/api/profiles/{PID}/confirm").status_code == 404
