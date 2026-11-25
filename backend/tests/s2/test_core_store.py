"""CoreStore 對真實 core/db.py（Db、AdminDb）的相容性測試：用 httpx.MockTransport 假裝 Supabase。"""
import json
from types import SimpleNamespace

import httpx

from app.core.db import AdminDb, Db
from app.services.s2 import privacy
from app.services.s2.compat import CoreStore

UID = "11111111-1111-1111-1111-111111111111"
FID = "22222222-2222-2222-2222-222222222222"
S = SimpleNamespace(supabase_anon_key="anon", supabase_url="http://sb", supabase_service_key="svc")


def make(handler):
    http = httpx.Client(base_url="http://sb", transport=httpx.MockTransport(handler))
    return CoreStore(Db(http, S, UID, "jwt"), AdminDb(http, S))


def test_delete_flow_hits_expected_endpoints():
    calls, state = [], {"files_deleted": False, "rows_deleted": set()}

    def handler(req):
        calls.append((req.method, req.url.path, req.url.params.get("user_id")))
        path = req.url.path
        if path == "/storage/v1/object/list/resumes":
            assert json.loads(req.content)["prefix"] == UID
            return httpx.Response(200, json=[] if state["files_deleted"] else [{"name": f"{FID}.pdf"}, {"name": "orphan.pdf"}])
        if req.method == "DELETE" and path == "/storage/v1/object/resumes":
            assert sorted(json.loads(req.content)["prefixes"]) == [f"{UID}/{FID}.pdf", f"{UID}/orphan.pdf"]
            state["files_deleted"] = True
            return httpx.Response(200, json=[])
        table = path.rsplit("/", 1)[-1]
        if req.method == "DELETE":
            state["rows_deleted"].add(table)
            return httpx.Response(200, json=[{"user_id": UID}])
        return httpx.Response(200, json=[] if table in state["rows_deleted"] else [{"user_id": UID}])

    counts = privacy.delete_user_data(make(handler), UID)
    assert state["files_deleted"]
    assert all(p == f"eq.{UID}" for m, path, p in calls if "/rest/" in path)  # 每個查詢都帶 user_id
    assert counts == {"files": 1, "profiles": 1, "career_inputs": 1, "analyses": 1, "consents": 1}


def test_consent_roundtrip():
    def handler(req):
        if req.method == "POST":
            body = json.loads(req.content)[0]
            assert body["user_id"] == UID
            return httpx.Response(201, json=[{**body, "id": "c1", "accepted_at": "2026-11-12T00:00:00Z"}])
        return httpx.Response(200, json=[])

    st = make(handler)
    assert privacy.record_consent(st, UID, "privacy", privacy.PRIVACY_VERSION)["id"] == "c1"
