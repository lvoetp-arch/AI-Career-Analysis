import httpx
from fastapi.testclient import TestClient

from app.core.auth import FakeTokenVerifier
from app.main import create_app


def test_history_filters_authenticated_owner_and_projects_summary(settings, user_a, user_b):
    older = "2026-11-20T10:00:00+00:00"
    newer = "2026-11-21T10:00:00+00:00"
    roles = ["營運資料分析", "採購助理", "服務流程設計"]
    rows = [
        {"id": "a-old", "user_id": user_a, "started_at": older, "status": "failed",
         "career_input_id": "a-input", "result_json": None},
        {"id": "b-only", "user_id": user_b, "started_at": newer, "status": "running",
         "career_input_id": "b-input", "result_json": None},
        {"id": "a-new", "user_id": user_a, "started_at": newer, "status": "succeeded",
         "career_input_id": "a-other", "result_json": {"directions": [
             {"role_name": role, "summary": "Only the name belongs in history"} for role in roles
         ]}},
    ]
    tokens = {"token-a": user_a, "token-b": user_b}
    called = []

    def mock_supabase(request):
        assert request.method == "GET" and request.url.path == "/rest/v1/analysis_runs"
        owner = tokens[request.headers["Authorization"].removeprefix("Bearer ")]
        assert request.url.params["user_id"] == f"eq.{owner}"
        assert request.url.params["order"] == "started_at.desc"
        assert "input_snapshot" not in request.url.params["select"]
        called.append(owner)
        owned = [row for row in rows if row["user_id"] == owner]
        return httpx.Response(200, json=sorted(owned, key=lambda row: row["started_at"], reverse=True))

    db_http = httpx.Client(base_url=settings.supabase_url, transport=httpx.MockTransport(mock_supabase))
    app = create_app(settings, token_verifier=FakeTokenVerifier(tokens), http_client=db_http)
    with TestClient(app) as client:
        assert client.get("/api/analyses").status_code == 401
        a = client.get("/api/analyses", headers={"Authorization": "Bearer token-a"})
        b = client.get("/api/analyses", headers={"Authorization": "Bearer token-b"})
        assert a.json() == [
            {"analysis_id": "a-new", "created_at": newer, "status": "succeeded",
             "career_input_id": "a-other", "directions": roles},
            {"analysis_id": "a-old", "created_at": older, "status": "failed",
             "career_input_id": "a-input", "directions": []},
        ]
        assert b.json() == [
            {"analysis_id": "b-only", "created_at": newer, "status": "running",
             "career_input_id": "b-input", "directions": []},
        ]
        assert called == [user_a, user_b]
        assert client.get("/history.html").is_success
