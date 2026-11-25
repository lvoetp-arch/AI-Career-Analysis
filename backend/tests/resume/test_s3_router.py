import copy, json, time, uuid
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth import FakeTokenVerifier
from app.core.db import get_admin_db, get_db
from app.core.errors import AppError, ErrorCode, register_exception_handlers
from app.core.llm import FakeLLMClient, get_llm
from types import SimpleNamespace
from app.core.ratelimit import InMemoryUsageStore, RateLimiter
from app.routers.resumes import router
from app.services.resume.storage import delete_original_file
from fastapi import Depends
from app.core.auth import current_user
from app.services.s2.privacy import consent_required
from tests.resume.test_s3_resume import make_pdf, FIX

UID = str(uuid.uuid4())


class FakeDb:
    def __init__(self, store): self.s = store
    def insert(self, table, rows):
        r = {**rows, "user_id": UID}; r.setdefault("id", str(uuid.uuid4()))
        self.s.setdefault(table, []).append(r); return [r]


class FakeAdmin:
    def __init__(self, store, fail_delete=False): self.s, self.files, self.fail_delete = store, {}, fail_delete
    def upload_resume_pdf(self, uid, rid, data): self.files[rid] = data
    def delete_resume_pdf(self, uid, rid):
        if self.fail_delete: raise AppError(ErrorCode.STORAGE_ERROR)
        self.files.pop(rid, None); return True
    def update(self, table, uid, values, *, filters):
        for r in self.s[table]:
            if r["id"] == filters["id"]: r.update(values)
        return []


def make_app(llm, consented=True, fail_delete=False, limit=5):
    app = FastAPI(); register_exception_handlers(app); app.include_router(router)
    rows = {}
    admin = FakeAdmin(rows, fail_delete)
    app.state.token_verifier = FakeTokenVerifier({"t": UID})
    def _consent(uid: str = Depends(current_user)):
        if not consented:
            raise AppError(ErrorCode.CONSENT_REQUIRED)
        return uid
    app.dependency_overrides[consent_required] = _consent  # 只測 S3 自己的邏輯，不依賴 S2 的 get_store
    app.state.rate_limiter = RateLimiter(SimpleNamespace(rate_limit_parse_per_day=limit, rate_limit_analysis_per_day=10), InMemoryUsageStore())  # RateLimiter 只讀這兩個欄位，不必建真正的 Settings
    app.dependency_overrides[get_db] = lambda: FakeDb(rows)
    app.dependency_overrides[get_admin_db] = lambda: admin
    app.dependency_overrides[get_llm] = lambda: llm
    return TestClient(app), rows, admin


H = {"Authorization": "Bearer t"}
def post(c, data, name=None):
    return c.post("/api/resumes", headers=H, files={"file": ("r.pdf", data, "application/pdf")}, data={"name_variants": name} if name else {})


def test_success_flow_and_name_not_stored(caplog):
    caplog.set_level("DEBUG")
    llm = FakeLLMClient([json.dumps(FIX)]); c, rows, admin = make_app(llm)
    r = post(c, make_pdf(), "林佳慧")
    assert r.status_code == 200, r.text
    b = r.json(); assert set(b) == {"resume_id", "profile_id", "parsed_json", "notes"}
    assert rows["resume_files"][0]["status"] == "parsed" and rows["resume_profiles"][0]["status"] == "draft"
    assert rows["resume_files"][0]["storage_path"] == f"{UID}/{b['resume_id']}.pdf"
    assert "林佳慧" not in json.dumps(rows, ensure_ascii=False) and "林佳慧" not in caplog.text
    assert "林佳慧" not in llm.calls[0][1]
    assert b["resume_id"] in admin.files  # 成功後原檔保留到使用者確認


def test_consent_required_before_rate_limit():
    c, rows, _ = make_app(FakeLLMClient([]), consented=False, limit=1)
    assert post(c, make_pdf()).json()["error"]["code"] == "CONSENT_REQUIRED"
    assert post(c, make_pdf()).json()["error"]["code"] == "CONSENT_REQUIRED"  # 沒有被扣額度
    assert "resume_files" not in rows


def test_rate_limited():
    c, *_ = make_app(FakeLLMClient([json.dumps(FIX)]), limit=1)
    assert post(c, make_pdf()).status_code == 200
    r = post(c, make_pdf()); assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"


def test_requires_login():
    c, *_ = make_app(FakeLLMClient([]))
    r = c.post("/api/resumes", files={"file": ("r.pdf", b"x", "application/pdf")})
    assert r.status_code == 401


@pytest.mark.parametrize("data,code,status", [(b"hello", "FILE_INVALID", 400), (make_pdf(blank=True, link=False), "SCANNED_OR_EMPTY", 422)])
def test_invalid_files(data, code, status):
    c, rows, admin = make_app(FakeLLMClient([]))
    r = post(c, data); assert r.status_code == status and r.json()["error"]["code"] == code
    assert rows["resume_files"][0]["status"] in ("failed",) and rows["resume_files"][0]["error_code"] == code
    assert not admin.files and "resume_profiles" not in rows


def test_schema_invalid_discards_original():
    c, rows, admin = make_app(FakeLLMClient(["APPROVED", "APPROVED"]))
    r = post(c, make_pdf()); assert r.status_code == 502 and r.json()["error"]["code"] == "SCHEMA_INVALID"
    assert rows["resume_files"][0]["status"] == "failed" and not admin.files


def test_llm_error_and_delete_failed_marked():
    from app.core.llm import LLMError
    c, rows, admin = make_app(FakeLLMClient([LLMError()]), fail_delete=True)
    r = post(c, make_pdf()); assert r.json()["error"]["code"] == "LLM_ERROR"
    assert rows["resume_files"][0]["status"] == "delete_failed" and rows["resume_files"][0]["error_code"] == "LLM_ERROR"


def test_delete_original_file_ok_and_failed():
    rows = {"resume_files": [{"id": "a"}, {"id": "b"}]}
    assert delete_original_file(FakeAdmin(rows), UID, "a") is True and rows["resume_files"][0]["status"] == "deleted"
    assert delete_original_file(FakeAdmin(rows, fail_delete=True), UID, "b") is False and rows["resume_files"][1]["status"] == "delete_failed"


# ---- S2b 任務 C：額度只在要呼叫 LLM 時才扣 ----
def used(c):
    """目前已扣的額度總數（讀測試用的記憶體計數）。"""
    return sum(c.app.state.rate_limiter._store._counts.values())


def test_local_validation_failures_do_not_consume_quota():
    c, rows, admin = make_app(FakeLLMClient([json.dumps(FIX)]), limit=1)
    bad = [(b"hello", 400), (b"%PDF" + b"0" * (5 * 1024 * 1024 + 1), 400),
           (make_pdf(encrypt=True), 400), (make_pdf(blank=True, link=False), 422)]
    for data, status in bad * 2:  # 反覆失敗也不會耗盡額度
        assert post(c, data).status_code == status
    assert used(c) == 0
    assert len(rows["resume_files"]) == 8 and all(r["status"] == "failed" for r in rows["resume_files"])
    assert not admin.files
    assert post(c, make_pdf()).status_code == 200  # 額度還在：limit=1 的這一次可以用
    assert used(c) == 1


def test_valid_file_consumes_exactly_one_quota_even_with_schema_retry_or_llm_error():
    from app.core.llm import LLMError
    c, *_ = make_app(FakeLLMClient(["APPROVED", json.dumps(FIX)]))  # 第一次 Schema 不符、重試成功：兩次 LLM、一次額度
    assert post(c, make_pdf()).status_code == 200 and used(c) == 1
    c2, *_ = make_app(FakeLLMClient([LLMError()]))  # 呼叫了 LLM 才失敗：額度照扣
    assert post(c2, make_pdf()).status_code == 502 and used(c2) == 1


def test_rate_limited_after_local_validation_discards_file_and_never_calls_llm():
    llm = FakeLLMClient([json.dumps(FIX)])
    c, rows, admin = make_app(llm, limit=1)
    ok = post(c, make_pdf()); assert ok.status_code == 200
    r = post(c, make_pdf())
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"
    assert len(llm.calls) == 1 and used(c) == 1
    assert rows["resume_files"][1]["status"] == "failed" and rows["resume_files"][1]["error_code"] == "RATE_LIMITED"
    assert list(admin.files) == [ok.json()["resume_id"]]  # 被限流的那份原檔已丟棄


def test_invalid_file_still_needs_consent_but_not_quota():
    c, rows, _ = make_app(FakeLLMClient([]), consented=False)
    assert post(c, b"hello").json()["error"]["code"] == "CONSENT_REQUIRED"
    assert "resume_files" not in rows and used(c) == 0


# ---- S2b 任務 C：總時限 90 秒 ----
def test_total_deadline_is_90_seconds_and_shared_by_both_calls():
    from app.routers import resumes as mod
    assert mod.PARSE_TOTAL_SECONDS == 90
    llm = FakeLLMClient(["APPROVED", json.dumps(FIX)])
    c, *_ = make_app(llm)
    t0 = time.monotonic()
    assert post(c, make_pdf()).status_code == 200
    d1, d2 = llm.deadlines
    assert d1 is not None and d1 == d2
    assert 85 < d1 - t0 <= 91  # 從請求開始起算約 90 秒


def test_deadline_exceeded_returns_llm_error_and_discards_original(monkeypatch):
    from app.routers import resumes as mod
    monkeypatch.setattr(mod, "PARSE_TOTAL_SECONDS", 0.1)

    class SlowLLM(FakeLLMClient):
        def complete(self, system, user, *, deadline=None):
            time.sleep(0.25)  # 第一次呼叫就用完時限，且回傳的內容不合 Schema
            return super().complete(system, user, deadline=deadline)

    llm = SlowLLM(["APPROVED", json.dumps(FIX)])
    c, rows, admin = make_app(llm)
    r = post(c, make_pdf())
    assert r.status_code == 502 and r.json()["error"]["code"] == "LLM_ERROR"
    assert len(llm.calls) == 1  # 時限已過，不做第二次呼叫
    assert rows["resume_files"][0]["status"] == "failed" and rows["resume_files"][0]["error_code"] == "LLM_ERROR"
    assert not admin.files and "resume_profiles" not in rows
