"""S5 後端測試：用假 Db 驅動真實的 router 與 errors（做法與 S4 相同）。"""
import copy
import itertools
import json
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.core.errors import register_exception_handlers
from app.routers import career_inputs
from app.services.s5 import validation

USER = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
PID = str(uuid.uuid4())
ROOT = Path(__file__).resolve().parents[3]


def _find_fixture():
    for d in ("docs", "backend", "frontend", "scripts", "fixtures", "tests"):
        for p in (ROOT / d).rglob("sample_resume_profile.fixture.json"):
            return p
    return None


def base_profile():
    f = _find_fixture()
    if f:
        return json.loads(f.read_text(encoding="utf-8-sig"))
    pytest.skip("找不到 sample_resume_profile.fixture.json")


class FakeDb:
    def __init__(self, user_id, tables):
        self.user_id, self.tables, self._n = user_id, tables, itertools.count(1)
        self.on_insert = None  # 測試用：寫入前後插入「另一個分頁」的競爭列

    def _rows(self, table, filters):
        return [r for r in self.tables[table] if r["user_id"] == self.user_id and all(r.get(k) == v for k, v in (filters or {}).items())]

    def select(self, table, *, filters=None, columns="*", order=None, limit=None):
        rows = self._rows(table, filters)
        if order:
            col, direction = order.split(".")
            rows = sorted(rows, key=lambda r: r[col], reverse=direction == "desc")
        return [dict(r) for r in (rows[:limit] if limit else rows)]

    def select_one(self, table, *, filters=None, columns="*", order=None):
        rows = self.select(table, filters=filters, columns=columns, order=order, limit=1)
        return rows[0] if rows else None

    def insert(self, table, row):
        new = {**row, "id": str(uuid.uuid4()), "user_id": self.user_id, "created_at": f"2026-11-20T10:00:{next(self._n):02d}+00:00"}
        self.tables[table].append(new)
        if self.on_insert:
            self.on_insert(self)
        return [dict(new)]

    def update(self, table, values, *, filters):
        hit = self._rows(table, filters)
        for r in hit:
            r.update(values)
        return [dict(r) for r in hit]

    def delete(self, table, *, filters=None):
        hit = self._rows(table, filters)
        self.tables[table][:] = [r for r in self.tables[table] if r not in hit]
        return [dict(r) for r in hit]


@pytest.fixture
def env():
    prof = base_profile()
    tables = {
        "resume_profiles": [{"id": PID, "user_id": USER, "status": "confirmed", "parsed_json": copy.deepcopy(prof)}],
        "career_inputs": [],
    }
    db = FakeDb(USER, tables)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(career_inputs.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app), db, prof


def body(prof, **over):
    ratings = [{"skill_id": s["id"], "name": s["name"], "rating": 3, "source": "resume"} for s in prof["skills"][:3]]
    b = {"profile_id": PID, "goal": "switch", "skill_ratings": ratings, "preferences": {}}
    b.update(over)
    return b


def paths(resp):
    return {d["path"] for d in resp.json()["error"]["details"]}


# ---------- 成功與更新 ----------
def test_creates_and_stores_only_rating_fields(env):
    c, db, prof = env
    b = body(prof)
    b["skill_ratings"][0]["name"] = "使用者改過的名稱"  # 履歷技能一律以履歷內名稱存檔
    b["skill_ratings"].append({"skill_id": None, "name": "  Excel  ", "rating": 5, "source": "added"})
    b["preferences"] = {"remote_willingness": "willing", "employment_types": ["full_time"], "shift": "日班"}
    r = c.put("/api/career-inputs", json=b)
    assert r.status_code == 200, r.text
    row = db.tables["career_inputs"][0]
    assert r.json() == {"career_input_id": row["id"]} and row["resume_profile_id"] == PID and row["goal"] == "switch"
    assert row["skill_ratings"][0] == {"skill_id": prof["skills"][0]["id"], "name": prof["skills"][0]["name"], "rating": 3, "source": "resume"}
    assert row["skill_ratings"][-1] == {"skill_id": None, "name": "Excel", "rating": 5, "source": "added"}
    assert all(set(x) == {"skill_id", "name", "rating", "source"} for x in row["skill_ratings"])  # 不夾帶履歷證據
    assert row["preferences"]["shift"] == "日班"


def test_resubmit_updates_same_row(env):
    c, db, prof = env
    first = c.put("/api/career-inputs", json=body(prof)).json()["career_input_id"]
    second = c.put("/api/career-inputs", json=body(prof, goal="continue", skill_ratings=[])).json()["career_input_id"]
    assert first == second and len(db.tables["career_inputs"]) == 1
    assert db.tables["career_inputs"][0]["goal"] == "continue" and db.tables["career_inputs"][0]["skill_ratings"] == []


def test_fixture_job_preferences_are_accepted_as_is(env):
    c, db, prof = env
    r = c.put("/api/career-inputs", json=body(prof, preferences=prof["job_preferences"]))
    assert r.status_code == 200, r.text


def test_existing_duplicates_are_collapsed_to_oldest(env):
    c, db, prof = env
    for i in (1, 2, 3):
        db.tables["career_inputs"].append({"id": f"id-{i}", "user_id": USER, "resume_profile_id": PID, "goal": "undecided",
                                           "skill_ratings": [], "preferences": {}, "created_at": f"2026-11-1{i}T00:00:00+00:00"})
    r = c.put("/api/career-inputs", json=body(prof))
    assert r.json()["career_input_id"] == "id-1"
    assert [x["id"] for x in db.tables["career_inputs"]] == ["id-1"] and db.tables["career_inputs"][0]["goal"] == "switch"


def test_concurrent_insert_race_ends_with_one_row(env):
    c, db, prof = env

    def other_tab(d):  # 我們 insert 的同時，另一個分頁也寫入了一列（較早）
        d.on_insert = None
        d.tables["career_inputs"].insert(0, {"id": "early", "user_id": USER, "resume_profile_id": PID, "goal": "continue",
                                             "skill_ratings": [], "preferences": {}, "created_at": "2026-11-20T09:00:00+00:00"})

    db.on_insert = other_tab
    r = c.put("/api/career-inputs", json=body(prof))
    assert r.status_code == 200 and r.json()["career_input_id"] == "early"
    assert len(db.tables["career_inputs"]) == 1 and db.tables["career_inputs"][0]["goal"] == "switch"


def test_empty_skill_ratings_allowed(env):
    c, _, prof = env
    assert c.put("/api/career-inputs", json=body(prof, skill_ratings=[])).status_code == 200


# ---------- 自評分數 ----------
@pytest.mark.parametrize("bad", [0, 6, -1, 3.5, 3.0, "3", "SENTINEL-RATING", True, None, [3], {"a": 1}])
def test_bad_rating_rejected_without_echo(env, bad):
    c, db, prof = env
    b = body(prof)
    b["skill_ratings"][1]["rating"] = bad
    r = c.put("/api/career-inputs", json=b)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "skill_ratings[1].rating" in paths(r)
    assert "SENTINEL-RATING" not in r.text and db.tables["career_inputs"] == []


@pytest.mark.parametrize("good", [1, 2, 3, 4, 5])
def test_every_level_accepted(env, good):
    c, _, prof = env
    b = body(prof)
    b["skill_ratings"][0]["rating"] = good
    assert c.put("/api/career-inputs", json=b).status_code == 200


# ---------- 其他欄位 ----------
@pytest.mark.parametrize("goal", ["SENTINEL-GOAL", "", 1, None, ["switch"]])
def test_bad_goal(env, goal):
    c, _, prof = env
    r = c.put("/api/career-inputs", json=body(prof, goal=goal))
    assert r.status_code == 422 and "goal" in paths(r) and "SENTINEL-GOAL" not in r.text


def test_missing_and_extra_top_level_keys(env):
    c, _, prof = env
    r = c.put("/api/career-inputs", json={"profile_id": PID})
    assert r.status_code == 422 and {"goal", "skill_ratings", "preferences"} <= paths(r)
    r = c.put("/api/career-inputs", json=body(prof, phone="x"))
    assert r.status_code == 422 and "phone" in paths(r)
    r = c.put("/api/career-inputs", json=["not", "an", "object"])
    assert r.status_code == 422
    r = c.put("/api/career-inputs", json=body(prof, skill_ratings="oops"))
    assert r.status_code == 422 and "skill_ratings" in paths(r)


def test_skill_item_problems(env):
    c, _, prof = env
    b = body(prof)
    b["skill_ratings"][0]["skill_id"] = "skill-9999"
    b["skill_ratings"][1]["name"] = "   "
    b["skill_ratings"][2]["source"] = "SENTINEL-SRC"
    b["skill_ratings"].append({"skill_id": None, "name": "ok", "rating": 3, "source": "added", "years": 5})
    b["skill_ratings"].append({"skill_id": "skill-1", "name": "x", "rating": 3, "source": "added"})
    b["skill_ratings"].append("string-item")
    r = c.put("/api/career-inputs", json=b)
    assert r.status_code == 422
    assert {"skill_ratings[0].skill_id", "skill_ratings[1].name", "skill_ratings[2].source", "skill_ratings[3].years",
            "skill_ratings[4].skill_id", "skill_ratings[5]"} <= paths(r)
    assert "SENTINEL-SRC" not in r.text


def test_name_rules_and_duplicates(env):
    c, _, prof = env
    first = prof["skills"][0]
    added = lambda n: {"skill_id": None, "name": n, "rating": 2, "source": "added"}  # noqa: E731
    for bad_name, expect in [("字" * 61, "內容太長，請縮短"), ("a\x00b", "技能名稱含不允許的字元"), ("a\nb", "技能名稱含不允許的字元")]:
        r = c.put("/api/career-inputs", json=body(prof, skill_ratings=[added(bad_name)]))
        assert r.status_code == 422 and expect in r.text
    # 與履歷技能同名（忽略大小寫與全形）、兩個相同的自加技能、同一個履歷技能送兩次
    b = body(prof, skill_ratings=[{"skill_id": first["id"], "name": first["name"], "rating": 3, "source": "resume"},
                                  added(first["name"].upper()), added("Ｐython"), added("python"),
                                  {"skill_id": first["id"], "name": first["name"], "rating": 4, "source": "resume"}])
    r = c.put("/api/career-inputs", json=b)
    assert r.status_code == 422 and {"skill_ratings[1].name", "skill_ratings[3].name", "skill_ratings[4].skill_id"} <= paths(r)


def test_added_limit_and_total_limit(env):
    c, db, prof = env
    eleven = [{"skill_id": None, "name": f"自加{i}", "rating": 3, "source": "added"} for i in range(11)]
    r = c.put("/api/career-inputs", json=body(prof, skill_ratings=eleven))
    assert r.status_code == 422 and "skill_ratings" in paths(r)
    ten = eleven[:10]
    assert c.put("/api/career-inputs", json=body(prof, skill_ratings=ten)).status_code == 200
    # 履歷有 25 項技能 + 自加 10 項 = 35 > 30
    big = db.tables["resume_profiles"][0]["parsed_json"]["skills"]
    big += [{"id": f"skill-x{i}", "name": f"額外{i}", "linked_ids": []} for i in range(11)]
    resume = [{"skill_id": s["id"], "name": s["name"], "rating": 3, "source": "resume"} for s in big]
    r = c.put("/api/career-inputs", json=body(prof, skill_ratings=resume + ten))
    assert r.status_code == 422 and "skill_ratings" in paths(r)
    # 剛好 30 項可以
    assert c.put("/api/career-inputs", json=body(prof, skill_ratings=(resume + ten)[:30])).status_code == 200


# ---------- profile 的權限與狀態 ----------
def test_unconfirmed_profile_cannot_create(env):
    c, db, prof = env
    db.tables["resume_profiles"][0]["status"] = "draft"
    r = c.put("/api/career-inputs", json=body(prof))
    assert r.status_code == 422 and "profile_id" in paths(r) and db.tables["career_inputs"] == []


def test_unknown_foreign_or_malformed_profile_is_404(env):
    c, db, prof = env
    assert c.put("/api/career-inputs", json=body(prof, profile_id=str(uuid.uuid4()))).status_code == 404
    assert c.put("/api/career-inputs", json=body(prof, profile_id="not-a-uuid")).status_code == 404
    db.user_id = OTHER
    assert c.put("/api/career-inputs", json=body(prof)).status_code == 404
    assert db.tables["career_inputs"] == []
    r = c.put("/api/career-inputs", json=body(prof, profile_id=123))
    assert r.status_code == 422 and "profile_id" in paths(r)


def test_resume_skill_must_belong_to_this_profile(env):
    c, _, prof = env
    b = body(prof)
    b["skill_ratings"][0]["skill_id"] = "skill-from-another-profile"
    assert "skill_ratings[0].skill_id" in paths(c.put("/api/career-inputs", json=b))


# ---------- preferences ----------
@pytest.mark.parametrize("prefs,path", [
    ({"remote_willingness": "SENTINEL-ENUM"}, "preferences.remote_willingness"),
    ({"employment_types": ["full_time", "SENTINEL-ENUM"]}, "preferences.employment_types[1]"),
    ({"work_modes": ["office"]}, "preferences.work_modes[0]"),
    ({"phone": "SENTINEL-PHONE-VALUE"}, "preferences.phone"),
    ({"salary_expectation": {"min": "SENTINEL-ENUM"}}, "preferences.salary_expectation.min"),
    ({"salary_expectation": {"min": 40000}}, "preferences.salary_expectation.raw_text"),
    ({"availability": {"earliest_start": 5}}, "preferences.availability.earliest_start"),
    ({"shift": "字" * 201}, "preferences.shift"),
    ({"desired_roles": ["x"] * 21}, "preferences.desired_roles"),
    ({"leave_and_schedule": "a\x00b"}, "preferences.leave_and_schedule"),
    ({"other_conditions": "不是陣列"}, "preferences.other_conditions"),
])
def test_bad_preferences_rejected_without_echo(env, prefs, path):
    c, db, prof = env
    r = c.put("/api/career-inputs", json=body(prof, preferences=prefs))
    assert r.status_code == 422 and path in paths(r)
    assert "SENTINEL-ENUM" not in r.text and "SENTINEL-PHONE-VALUE" not in r.text and db.tables["career_inputs"] == []


def test_preferences_must_be_object_and_not_huge(env):
    c, _, prof = env
    assert "preferences" in paths(c.put("/api/career-inputs", json=body(prof, preferences=[1])))
    huge = {"other_conditions": ["字" * 200] * 20, "desired_roles": ["字" * 200] * 20, "desired_industries": ["字" * 200] * 20,
            "desired_job_categories": ["字" * 200] * 20, "preferred_locations": ["字" * 200] * 20}
    assert "preferences" in paths(c.put("/api/career-inputs", json=body(prof, preferences=huge)))


def test_salary_is_stored_verbatim_without_market_data(env):
    c, db, prof = env
    prefs = {"salary_expectation": {"raw_text": "月薪 5 萬以上，可談"}, "leave_and_schedule": "週休二日"}
    assert c.put("/api/career-inputs", json=body(prof, preferences=prefs)).status_code == 200
    assert db.tables["career_inputs"][0]["preferences"] == prefs


# ---------- 純函式 ----------
def test_validate_function_returns_empty_for_good_input(env):
    _, db, prof = env
    assert validation.validate_career_input(body(prof), db.tables["resume_profiles"][0]) == []
