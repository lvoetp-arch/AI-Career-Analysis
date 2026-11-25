import json
from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from app.services.report import evidence_labels, evidence_links
from tests.s6.test_analyses import CAREER, CAREER_DATA, PROFILE_DATA, model_response, scenario, valid_result

ROOT = Path(__file__).resolve().parents[3]


def test_sample_conforms_to_result_schema():
    sample = json.loads((ROOT / "frontend/sample/analysis_sample.json").read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "backend/app/schemas/analysis_result.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(sample["result"])


def test_owner_report_includes_labels_but_keeps_snapshot_private(scenario):
    _, client, llm, _ = scenario([model_response(valid_result())])
    created = client.post("/api/analyses", json={"career_input_id": CAREER})
    assert created.is_success
    run = client.get("/api/analyses/" + created.json()["analysis_id"]).json()
    assert run["career_input_id"] == CAREER
    assert run["result"] == valid_result()
    assert run["alternative_comparison"] is None
    assert run["evidence_labels"] == {"resume": {"work_experience[0].title": "工作經歷：虛構紙飛機商店"}}
    assert run["evidence_excerpts"] == {"resume": {"work_experience[0].title": "庫存助理"}}
    assert "input_snapshot" not in run
    assert len(llm.calls) == 1
    assert client.get("/report.html").is_success
    assert client.get("/sample_report.html").is_success


def test_legacy_or_unavailable_evidence_uses_category():
    assert evidence_labels(valid_result(), {}) == {"resume": {"work_experience[0].title": "工作經歷"}}


def test_saved_report_restores_organizations_and_hides_inline_evidence_ids(scenario):
    _, client, llm, db = scenario([])
    result = valid_result()
    overview = result["career_overview"]
    overview["assessment"] = "在公司-1執行盤點（E-0ef4714354ad），並運用學校-1的訓練。"
    overview["first_move"] = "可以依E-f274bbfe80e6整理案例 [E-0ef4714354ad、E-f274bbfe80e6]。"
    ref = result["directions"][0]["fit_evidence"][0]["evidence_refs"][0]
    ref["note"] = "曾在公司-1整理表單（E-0ef4714354ad）。"
    run_id = str(uuid4())
    db.insert("analysis_runs", {
        "id": run_id, "status": "succeeded", "career_input_id": CAREER, "started_at": "2026-11-23T00:00:00+00:00",
        "input_snapshot": {**PROFILE_DATA, "career_input": CAREER_DATA}, "result_json": result,
    })

    response = client.get(f"/api/analyses/{run_id}")
    assert response.is_success
    report = response.json()
    display = report["result"]["career_overview"]
    assert display["assessment"] == "在虛構紙飛機商店執行盤點，並運用虛構月球學院的訓練。"
    assert display["first_move"] == "可以依履歷資料整理案例。"
    display_ref = report["result"]["directions"][0]["fit_evidence"][0]["evidence_refs"][0]
    assert display_ref == {**ref, "note": "曾在虛構紙飛機商店整理表單。"}
    assert "E-0ef4714354ad" in overview["assessment"]
    assert not llm.calls


@pytest.mark.parametrize("preferences", [{}, {"desired_roles": ["資深後端工程師"]}])
def test_saved_report_reuses_ranked_directions_when_no_alternatives_were_generated(scenario, preferences):
    _, client, llm, db = scenario([])
    result = valid_result()
    result["career_overview"].pop("alternative_strategy")
    etl, quality = result["directions"][1:]
    etl.update(role_name="資料管線自動化工程師", summary="把自動對帳經驗用在資料管線和例外處理。")
    quality.update(role_name="工程品質工程師", summary="把導入測試與 CI 經驗用在交付流程。")
    duties_path = "work_experience[0].responsibilities"
    for index, direction in enumerate((etl, quality)):
        direction["fit_evidence"][0]["evidence_refs"] = [{"source": "resume", "path": f"{duties_path}[{index}]"}]
    snapshot = {
        **PROFILE_DATA, "career_input": {**CAREER_DATA, "preferences": preferences},
        "work_experience": [{
            **PROFILE_DATA["work_experience"][0], "responsibilities": ["用 Python 自動對帳", "導入 pytest 與 CI"],
        }],
    }
    run_id = str(uuid4())
    db.insert("analysis_runs", {
        "id": run_id, "status": "succeeded", "career_input_id": CAREER, "started_at": "2026-11-23T00:00:00+00:00",
        "input_snapshot": snapshot, "result_json": result,
    })

    response = client.get(f"/api/analyses/{run_id}")
    assert response.is_success
    report = response.json()
    options = report["alternative_comparison"]["fallback_options"]
    assert [option["role_name"] for option in options] == [etl["role_name"], quality["role_name"]]
    assert options[0]["validation"] == etl["action_plan"]["day_30"][0]["action"]
    assert options[0]["success_criteria"] == etl["action_plan"]["day_30"][0]["success_criteria"]
    assert report["evidence_excerpts"]["resume"][options[1]["evidence_refs"][0]["path"]] == "導入 pytest 與 CI"
    assert report["result"]["career_overview"]["alternatives"] == []
    assert not llm.calls


def test_report_only_links_cited_portfolio_or_projects(scenario):
    result = valid_result()
    result["directions"][0]["fit_evidence"][0]["evidence_refs"].append({"source": "resume", "path": "portfolio[0]"})
    _, client, llm, db = scenario([model_response(result)])
    url = "https://example.test/portfolio"
    db.tables["resume_profiles"][0]["parsed_json"]["portfolio"] = [
        {"title": "庫存案例", "url": url}, {"title": "其他案例", "url": "https://example.test/other"},
    ]
    created = client.post("/api/analyses", json={"career_input_id": CAREER})
    assert created.is_success
    report = client.get("/api/analyses/" + created.json()["analysis_id"]).json()
    assert report["evidence_links"] == {"portfolio[0]": url}
    assert url not in llm.calls[0][1]
    assert evidence_links(report["evidence_labels"], {"portfolio": [{"url": "javascript:alert(1)"}]}) == {}


def test_alternative_directions_cite_distinct_duties_and_skip_invalid_options(scenario, caplog):
    result = valid_result()
    first_ref = {"source": "resume", "path": "work_experience[0].responsibilities[0]"}
    second_ref = {"source": "resume", "path": "projects[0]"}
    first = {
        "role_name": "庫存內控助理",
        "discovery_type": "能力重組",
        "work_problem": "追查盤點差異。",
        "why_new": "負責設計內控流程與差異追蹤。",
        "skill_bridge": {
            "source_skills": [
                {"skill": "盤點並記錄差異", "evidence_refs": [first_ref]},
                {"skill": "製作盤點工具", "evidence_refs": [second_ref]},
            ],
            "adjacent_skill": "庫存例外作業設計",
        },
        "transfer": "結合現場盤點流程與盤點工具，留下可核對的差異紀錄。",
        "application_readiness": "驗證後投遞",
        "readiness_reason": "有盤點與工具製作經驗，仍待驗證差異追查。",
        "gap": "還需證明能重現差異來源。",
        "validation": "請營運同事只看表格重現一筆盤點差異。",
        "success_criteria": "同事能核對差異發生的步驟。",
    }
    second = {
        **first,
        "role_name": "營運訓練助理",
        "discovery_type": "直接遷移",
        "work_problem": "協助新同事獨立完成標準作業。",
        "why_new": "以新人訓練作為主要責任。",
        "skill_bridge": {
            "source_skills": [{
                "skill": "協助新人理解盤點步驟",
                "evidence_refs": [{"source": "resume", "path": "work_experience[0].responsibilities[1]"}],
            }],
            "adjacent_skill": "在新團隊帶領入職訓練",
        },
        "application_readiness": "可嘗試投遞",
        "readiness_reason": "已有新人教學經驗，可用既有流程示範。",
    }
    unsupported = {
        **first,
        "role_name": "營運稽核",
        "skill_bridge": {
            **first["skill_bridge"],
            "source_skills": [
                first["skill_bridge"]["source_skills"][0],
                {"skill": "追查事故", "evidence_refs": [{"source": "resume", "path": "work_experience[99]"}]},
            ],
        },
    }
    result["data_gaps"] = []
    result["career_overview"]["alternatives"] = [
        first, unsupported,
        {
            **second, "role_name": "報表助理",
            "skill_bridge": {
                **second["skill_bridge"],
                "source_skills": [{"skill": "Excel", "evidence_refs": [second_ref]}],
            },
        },
        {**second, "role_name": "客戶需求分析師", "gap": "累積了 14 年產品設計經驗。"},
        {
            **unsupported, "role_name": "流程助理",
            "skill_bridge": {
                **unsupported["skill_bridge"],
                "source_skills": [
                    first["skill_bridge"]["source_skills"][0],
                    {"skill": "協助新人", "evidence_refs": second["skill_bridge"]["source_skills"][0]["evidence_refs"]},
                ],
            },
        },
        {**first, "role_name": "營運 專員"},
        {**first, "role_name": "採購助理（供應鏈）"},
        {**first, "role_name": "庫存助理（門市）"},
        {**first, "role_name": "庫存內控 助理"},
        {**first, "role_name": "其他方向", "validation": "  "},
        {**first, "role_name": "另一個方向", "gap": "錄取機率很高。"},
        second,
    ]
    _, client, llm, db = scenario([model_response(result)])
    profile = db.tables["resume_profiles"][0]["parsed_json"]
    profile["work_experience"][0]["responsibilities"] = ["每週盤點並記錄差異", "協助新人理解盤點步驟"]
    profile["projects"] = [{"name": "盤點工具", "tech": ["Excel"]}]
    db.tables["career_inputs"][0]["preferences"] = {
        "desired_job_categories": ["採購助理"], "other_conditions": ["排除回到純工業設計"],
    }

    created = client.post("/api/analyses", json={"career_input_id": CAREER})
    assert created.is_success, created.text
    report = client.get("/api/analyses/" + created.json()["analysis_id"]).json()
    assert report["result"]["career_overview"]["alternatives"] == [second, first]
    assert report["result"]["data_gaps"] == []
    assert report["alternative_comparison"] is None
    assert report["evidence_labels"]["resume"]["projects[0]"] == "專案：盤點工具"
    assert report["evidence_excerpts"]["resume"][first_ref["path"]] == "每週盤點並記錄差異"
    assert report["evidence_excerpts"]["resume"]["work_experience[0].responsibilities[1]"] == "協助新人理解盤點步驟"
    assert "analysis_alternatives candidates=12 kept=2" in caplog.text
    for reason, count in (("evidence", 1), ("recombination_evidence", 1), ("original_role", 1), ("resume_role", 1),
                          ("duplicate_role", 2), ("schema", 1), ("text_rule", 2), ("tool_name", 1)):
        assert f"'{reason}': {count}" in caplog.text
    assert len(llm.calls) == 1
    assert "使用者明確排除的職務或產業不可推薦" in llm.calls[0][0]
    assert "排除回到純工業設計" in llm.calls[0][1]


def test_saved_report_keeps_legacy_skill_bridge(scenario):
    result = valid_result()
    direction = result["directions"][1]
    option = {
        "role_name": direction["role_name"], "transfer": direction["summary"],
        "skill_bridge": {"source_skill": "庫存盤點", "adjacent_skill": "差異覆核流程設計"},
        "gap": "還需證明能重現差異來源。", "validation": "請同事只看表格重現一筆盤點差異。",
        "evidence_refs": direction["fit_evidence"][0]["evidence_refs"],
    }
    result["data_gaps"] = []
    _, client, _, db = scenario([model_response(result)])
    created = client.post("/api/analyses", json={"career_input_id": CAREER})
    assert created.is_success, created.text
    saved = db.tables["analysis_runs"][0]["result_json"]
    assert saved["data_gaps"][0]["topic"] == "新職涯探索"
    saved["career_overview"]["alternatives"] = [option]
    report = client.get("/api/analyses/" + created.json()["analysis_id"]).json()
    assert report["result"]["career_overview"]["alternatives"] == [option]
    assert report["alternative_comparison"] is None
