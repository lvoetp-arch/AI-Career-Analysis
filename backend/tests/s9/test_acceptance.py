import json
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.core.auth import FakeTokenVerifier
from app.core.llm import FakeLLMClient
from app.core.ratelimit import InMemoryUsageStore
from app.main import create_app
from app.services.s2.privacy import PRIVACY_VERSION, TABLE_STEPS
from tests.resume.test_s3_resume import make_pdf
from tests.s6.test_analyses import model_response, valid_result


class TestSupabase:
    """Small in-memory REST and Storage adapter for the application-level acceptance run."""

    __test__ = False

    def __init__(self):
        self.tables = {name: [] for name, _ in TABLE_STEPS}
        self.objects = {}
        self.delete_unavailable = False

    def handle(self, request):
        path = request.url.path
        data = json.loads(request.content) if request.content and "application/json" in request.headers.get("content-type", "") else None
        if path.startswith("/rest/v1/"):
            return self.table_request(request, self.tables[path.rsplit("/", 1)[1]], data)
        assert path.startswith("/storage/v1/object/")
        if path.endswith("/list/resumes"):
            prefix = data["prefix"] + "/"
            return httpx.Response(200, json=[
                {"name": name.removeprefix(prefix)} for name in self.objects if name.startswith(prefix)
            ])
        if path.endswith("/resumes") and request.method == "DELETE":
            for name in data["prefixes"]:
                self.objects.pop(name, None)
            return httpx.Response(200, json=[])
        name = path.split("/resumes/", 1)[1]
        if request.method == "POST":
            self.objects[name] = request.content
            return httpx.Response(200, json={})
        assert request.method == "DELETE"
        if self.delete_unavailable:
            return httpx.Response(503)
        self.objects.pop(name, None)
        return httpx.Response(200, json={})

    @staticmethod
    def table_request(request, rows, data):
        params = request.url.params
        if request.method == "POST":
            assert all(item.get("user_id") for item in data)
            inserted = [{"id": str(uuid4()), "created_at": "2026-11-23T00:00:00Z",
                         "accepted_at": "2026-11-23T00:00:00Z", "result_json": None, **item} for item in data]
            rows.extend(inserted)
            return httpx.Response(201, json=inserted)
        assert "user_id" in params
        filters = {key: value[3:] for key, value in params.items() if value.startswith("eq.")}
        matched = [row for row in rows if all(row.get(key) == value for key, value in filters.items())]
        if request.method == "PATCH":
            for row in matched:
                row.update(data)
        elif request.method == "DELETE":
            rows[:] = [row for row in rows if row not in matched]
        else:
            assert request.method == "GET"
            if "order" in params:
                field, direction = params["order"].split(".")
                matched.sort(key=lambda row: row[field], reverse=direction == "desc")
            if "limit" in params:
                matched = matched[:int(params["limit"])]
        return httpx.Response(200, json=matched)


def fictional_profile():
    return {
        "schema_version": "0.3", "basics": {},
        "work_experience": [{"id": "work-1", "company": "虛構風車文具坊", "title": "庫存助理",
                             "responsibilities": ["以 Python 整理盤點資料"]}],
        "education": [{"id": "edu-1", "school": "虛構雲朵學院"}],
        "skills": [{"id": "skill-1", "name": "Python", "linked_ids": ["work-1"]}],
        "projects": [], "portfolio": [], "job_preferences": {"remote_willingness": "unknown"},
        "autobiography": {"text": "協助整理虛構風車文具坊的盤點資料。"},
        "missing_flags": [], "awards": [],
    }


def test_upload_to_report_history_and_delete(settings, user_a, user_b, caplog):
    db = TestSupabase()
    profile = fictional_profile()
    result = valid_result()
    llm = FakeLLMClient([json.dumps(profile), model_response(result), model_response(result)])
    transport = httpx.Client(base_url=settings.supabase_url, transport=httpx.MockTransport(db.handle))
    app = create_app(settings, http_client=transport, llm_client=llm, usage_store=InMemoryUsageStore(),
                     token_verifier=FakeTokenVerifier({"token-a": user_a, "token-b": user_b}))
    owner = {"Authorization": "Bearer token-a"}
    other = {"Authorization": "Bearer token-b"}
    consent = {"consent_type": "privacy", "version": PRIVACY_VERSION}
    name = "虛構人物甲"
    email = "fictional" + chr(64) + "example.test"
    pdf = make_pdf(text=f"姓名：{name}\nEmail：{email}\n" + "虛構風車文具坊，盤點整理與 Python。\n" * 12, link=False)
    db.tables["consent_records"].append({
        "id": str(uuid4()), "user_id": user_a, "consent_type": "privacy",
        "version": "2026-11-18", "accepted_at": "2026-11-20T00:00:00Z",
    })
    with TestClient(app) as client:
        assert client.get("/api/analyses").status_code == 401
        assert client.get("/api/consents/current").status_code == 401
        current = client.get("/api/consents/current", headers=owner)
        assert current.json() == {"version": PRIVACY_VERSION, "accepted_at": None}
        assert current.headers["cache-control"] == "no-store"
        blocked = client.post("/api/resumes", headers=owner,
                              files={"file": ("fictional.pdf", pdf, "application/pdf")})
        assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "CONSENT_REQUIRED"
        assert client.post("/api/consents", headers=owner, json={**consent, "version": "old"}).status_code == 422
        assert client.post("/api/consents", headers=owner, json=consent).status_code == 201
        assert client.get("/api/consents/current", headers=owner).json()["accepted_at"]
        assert client.get("/api/consents/current", headers=other).json()["accepted_at"] is None
        assert client.post("/api/consents", headers=other, json=consent).status_code == 201

        upload = client.post("/api/resumes", headers=owner,
                             files={"file": ("fictional.pdf", pdf, "application/pdf")}, data={"name_variants": name})
        assert upload.status_code == 200, upload.json()
        profile_id = upload.json()["profile_id"]
        assert len(db.objects) == 1
        assert client.get("/api/profiles/latest", headers=owner).json()["status"] == "draft"
        profile_url = f"/api/profiles/{profile_id}"

        # A failed object deletion is recoverable by confirming again.
        db.delete_unavailable = True
        assert client.post(profile_url + "/confirm", headers=owner).json()["original_file_deleted"] is False
        assert db.tables["resume_files"][0]["status"] == "delete_failed"
        db.delete_unavailable = False
        assert client.post(profile_url + "/confirm", headers=owner).json()["original_file_deleted"] is True
        assert not db.objects

        ratings = [{"skill_id": "skill-1", "name": "Python", "rating": 3, "source": "resume"}]
        career = client.put("/api/career-inputs", headers=owner, json={
            "profile_id": profile_id, "goal": "switch", "skill_ratings": ratings, "preferences": {},
        })
        assert career.is_success, career.json()
        analysis_input = career.json()
        analyzed = client.post("/api/analyses", headers=owner, json=analysis_input)
        assert analyzed.is_success, analyzed.json()
        analysis_id = analyzed.json()["analysis_id"]
        report_url = f"/api/analyses/{analysis_id}"
        report = client.get(report_url, headers=owner).json()
        assert report["result"] == result
        assert report["evidence_labels"]["resume"]["work_experience[0].title"] == "工作經歷：虛構風車文具坊"
        assert len(client.get("/api/analyses", headers=owner).json()) == 1
        assert client.get(report_url, headers=other).status_code == 404
        assert client.get("/api/analyses", headers=other).json() == []

        updated = client.get("/api/profiles/latest", headers=owner).json()["parsed_json"]
        updated["work_experience"][0]["title"] = "庫存資料助理"
        assert client.put(profile_url, headers=owner, json={"parsed_json": updated}).is_success
        changed = client.post("/api/analyses", headers=owner, json=analysis_input)
        assert changed.is_success and changed.json()["analysis_id"] != analysis_id
        assert "庫存資料助理" in llm.calls[-1][1]
        assert all(marker not in llm.calls[0][1] for marker in (name, email))
        assert all(marker not in llm.calls[-1][1] for marker in ("虛構風車文具坊", "虛構雲朵學院"))
        assert "公司-1" in llm.calls[-1][1] and "學校-1" in llm.calls[-1][1]
        assert all(marker not in json.dumps(db.tables, ensure_ascii=False) for marker in (name, email))
        assert all(marker not in caplog.text for marker in (name, email, "庫存資料助理"))

        assert client.delete("/api/me/data", headers=owner).is_success
        assert all(row["user_id"] != user_a for rows in db.tables.values() for row in rows)
        assert len(db.tables["consent_records"]) == 1
        assert not db.objects
        assert client.get(report_url, headers=owner).status_code == 404


def test_protected_pages_use_the_vendored_auth_script_in_order():
    frontend = Path(__file__).resolve().parents[3] / "frontend"
    for path in frontend.glob("*.html"):
        html = path.read_text(encoding="utf-8")
        if 'src="js/auth.js"' in html:
            assert html.index('src="js/config.js"') < html.index('src="vendor/supabase-js-2.45.4.umd.js"') < html.index('src="js/auth.js"')
        assert all("supabase-js" not in line or 'src="vendor/' in line
                   for line in html.splitlines() if "<script" in line)
