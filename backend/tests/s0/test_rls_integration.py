"""RLS 跨使用者整合測試：需要真的 Supabase 專案與兩個測試帳號，未設定環境變數時自動略過。

需要的環境變數（放在本機 .env 或 shell，不要提交）：
  SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY（清理測試資料用）
  RLS_TEST_USER_A_EMAIL / RLS_TEST_USER_A_PASSWORD
  RLS_TEST_USER_B_EMAIL / RLS_TEST_USER_B_PASSWORD
兩個帳號請用專門建立的測試帳號（Email 驗證關閉或已驗證）。
"""
import os
import uuid

import httpx
import pytest

NEEDED = ["SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY", "RLS_TEST_USER_A_EMAIL",
          "RLS_TEST_USER_A_PASSWORD", "RLS_TEST_USER_B_EMAIL", "RLS_TEST_USER_B_PASSWORD"]


def _env():
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    return {k: os.environ.get(k, "").strip() for k in NEEDED}


ENV = _env()
pytestmark = pytest.mark.skipif(not all(ENV.values()), reason="未設定 RLS 測試用的 Supabase 環境變數")


def _login(email, password):
    r = httpx.post(f"{ENV['SUPABASE_URL']}/auth/v1/token?grant_type=password",
                   headers={"apikey": ENV["SUPABASE_ANON_KEY"]}, json={"email": email, "password": password})
    assert r.status_code == 200, "測試帳號登入失敗"
    return r.json()["access_token"], r.json()["user"]["id"]


class Api:
    def __init__(self, token=None, key=None):
        key = key or ENV["SUPABASE_ANON_KEY"]
        self.h = {"apikey": key, "Authorization": f"Bearer {token or key}", "Prefer": "return=representation"}
        self.base = ENV["SUPABASE_URL"]

    def get(self, table, **params):
        return httpx.get(f"{self.base}/rest/v1/{table}", headers=self.h, params=params)

    def post(self, table, row):
        return httpx.post(f"{self.base}/rest/v1/{table}", headers=self.h, json=row)

    def patch(self, table, row, **params):
        return httpx.patch(f"{self.base}/rest/v1/{table}", headers=self.h, params=params, json=row)

    def delete(self, table, **params):
        return httpx.delete(f"{self.base}/rest/v1/{table}", headers=self.h, params=params)


@pytest.fixture(scope="module")
def world():
    ta, ua = _login(ENV["RLS_TEST_USER_A_EMAIL"], ENV["RLS_TEST_USER_A_PASSWORD"])
    tb, ub = _login(ENV["RLS_TEST_USER_B_EMAIL"], ENV["RLS_TEST_USER_B_PASSWORD"])
    a, b, anon, admin = Api(ta), Api(tb), Api(), Api(key=ENV["SUPABASE_SERVICE_KEY"])
    file_id = str(uuid.uuid4())
    r = a.post("resume_files", {"id": file_id, "user_id": ua, "storage_path": f"{ua}/{file_id}.pdf", "size_bytes": 1})
    assert r.status_code == 201, "A 應該能建立自己的資料"
    prof = a.post("resume_profiles", {"user_id": ua, "resume_file_id": file_id, "schema_version": "t", "parsed_json": {}})
    assert prof.status_code == 201
    consent = a.post("consent_records", {"user_id": ua, "consent_type": "rls_test", "version": "1"})
    assert consent.status_code == 201
    yield dict(a=a, b=b, anon=anon, ua=ua, ub=ub, file_id=file_id, profile_id=prof.json()[0]["id"])
    for t in ("analysis_runs", "career_inputs", "resume_profiles", "resume_files", "consent_records"):
        admin.delete(t, user_id=f"eq.{ua}")
        admin.delete(t, user_id=f"eq.{ub}")


TABLES = ["consent_records", "resume_files", "resume_profiles", "career_inputs", "analysis_runs", "usage_counters"]


@pytest.mark.parametrize("table", TABLES)
def test_b_and_anon_cannot_read_a(world, table):
    for who in ("b", "anon"):
        r = world[who].get(table, user_id=f"eq.{world['ua']}")
        assert r.status_code in (200, 401, 403)
        if r.status_code == 200:
            assert r.json() == []


def test_a_can_read_own(world):
    assert len(world["a"].get("resume_files", id=f"eq.{world['file_id']}").json()) == 1


def test_b_cannot_update_or_delete_a_rows(world):
    r = world["b"].patch("resume_profiles", {"status": "confirmed"}, id=f"eq.{world['profile_id']}")
    assert r.status_code in (200, 403) and (r.status_code == 403 or r.json() == [])
    d = world["b"].delete("resume_files", id=f"eq.{world['file_id']}")
    assert d.status_code in (200, 403) and (d.status_code == 403 or d.json() == [])
    still = world["a"].get("resume_profiles", id=f"eq.{world['profile_id']}").json()
    assert still and still[0]["status"] == "draft"


def test_b_cannot_insert_rows_as_a(world):
    r = world["b"].post("consent_records", {"user_id": world["ua"], "consent_type": "x", "version": "1"})
    assert r.status_code in (401, 403)


def test_b_cannot_attach_to_a_resume_file_or_profile(world):
    r = world["b"].post("resume_profiles", {"user_id": world["ub"], "resume_file_id": world["file_id"],
                                            "schema_version": "t", "parsed_json": {}})
    assert r.status_code in (401, 403)
    r2 = world["b"].post("career_inputs", {"user_id": world["ub"], "resume_profile_id": world["profile_id"],
                                           "goal": "continue"})
    assert r2.status_code in (401, 403)


def test_cannot_point_storage_path_at_someone_else(world):
    fid = str(uuid.uuid4())
    r = world["b"].post("resume_files", {"id": fid, "user_id": world["ub"],
                                         "storage_path": f"{world['ua']}/{fid}.pdf"})
    assert r.status_code in (400, 403)


def test_clients_cannot_write_usage_counters_or_call_rpc(world):
    r = world["a"].post("usage_counters", {"user_id": world["ua"], "day": "2026-11-10", "kind": "parse", "count": 0})
    assert r.status_code in (401, 403)
    rpc = httpx.post(f"{ENV['SUPABASE_URL']}/rest/v1/rpc/consume_usage", headers=world["a"].h,
                     json={"p_user_id": world["ua"], "p_day": "2026-11-10", "p_kind": "parse", "p_limit": 5})
    assert rpc.status_code in (401, 403, 404)


def test_storage_resumes_bucket_blocks_clients(world):
    path = f"{world['ua']}/{world['file_id']}.pdf"
    up = httpx.post(f"{ENV['SUPABASE_URL']}/storage/v1/object/resumes/{path}", headers={
        "apikey": ENV["SUPABASE_ANON_KEY"], "Authorization": world["a"].h["Authorization"],
        "Content-Type": "application/pdf"}, content=b"%PDF-1.4")
    assert up.status_code in (400, 401, 403)  # 連 A 自己也不能直接上傳，只有後端可以
    for who in ("b", "anon"):
        dl = httpx.get(f"{ENV['SUPABASE_URL']}/storage/v1/object/resumes/{path}", headers=world[who].h)
        assert dl.status_code in (400, 401, 403, 404)
        pub = httpx.get(f"{ENV['SUPABASE_URL']}/storage/v1/object/public/resumes/{path}")
        assert pub.status_code in (400, 401, 403, 404)
