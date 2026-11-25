import copy
import json

import pymupdf

from app.services.resume.pdf_text import extract
from app.services.resume.pipeline import prepare_text, run_parse
from app.services.resume.sections import find_sections
from tests.resume.test_s3_resume import FIX, MockLLM


def layout_pdf():
    with pymupdf.open() as doc:
        first = doc.new_page()
        first.insert_textbox(pymupdf.Rect(30, 30, 540, 780),
                             "個人資料\n姓名：虛構人物甲\n學歷\n虛構風車學院\n工作經驗\n虛構紙盒工作室\n產品企劃\n"
                             "負責整理製造報價需求及原型設計，與使用者確認例外情形和驗收方式。",
                             fontname="china-t", fontsize=11)
        second = doc.new_page()
        second.insert_textbox(pymupdf.Rect(30, 30, 265, 780),
                              "虛構飛船工作室\n營運助理\n2025/1~2025/4\n整理進貨表單與盤點紀錄。",
                              fontname="china-t", fontsize=11)
        second.insert_textbox(pymupdf.Rect(300, 30, 550, 780),
                              "求職條件\n固定日班\n專長\nPython\n自傳\n希望把資料整理用在製造流程。",
                              fontname="china-t", fontsize=11)
        return doc.tobytes()


def test_104_sections_are_classified_before_cleaning_and_keep_order():
    ex = extract(layout_pdf())
    sections = find_sections(ex.pages)
    assert [(block.page, block.name) for block in sections if block.name != "基本資料"] == [
        (1, "學歷"), (1, "工作經歷"), (2, "工作經歷"),
        (2, "求職條件"), (2, "技能與專長"), (2, "自傳"),
    ]
    safe, _, _ = prepare_text(ex.text, ex.links, None, sections=sections)
    assert "虛構人物甲" not in safe
    assert safe.index("產品企劃") < safe.index("【第 2 頁｜工作經歷】") < safe.index("營運助理")
    assert safe.index("2025/1~2025/4") < safe.index("【第 2 頁｜求職條件】")


def test_104_parser_keeps_missing_values_as_null_and_does_not_move_existing_dates():
    profile = copy.deepcopy(FIX)
    profile["work_experience"] = [
        {"id": "work-1", "title": "產品企劃", "company": "虛構紙盒工作室"},
        {"id": "work-2", "title": "營運助理", "company": "虛構飛船工作室", "start": "2025-01", "end": "2025-04"},
    ]
    profile["education"] = [{"id": "edu-1", "school": "虛構風車學院", "degree": " "}]
    profile["skills"] = [{"id": "skill-1", "name": "Python"}]
    llm = MockLLM(json.dumps(profile))
    outcome = run_parse(layout_pdf(), llm)
    assert outcome.ok, outcome.error_code
    assert "【第 2 頁｜工作經歷】" in llm.prompts[0]
    assert "不要以其他段落的值補猜" in llm.prompts[0]
    first, second = outcome.profile["work_experience"]
    assert (first["start"], first["end"]) == (None, None)
    assert (second["start"], second["end"]) == ("2025-01", "2025-04")
    assert outcome.profile["education"][0]["degree"] is None
    assert outcome.profile["skills"][0]["stated_level_text"] is None


def test_single_generic_heading_does_not_change_format():
    assert find_sections(["技能\nPython\n資料清理"]) == []


def test_parse_requests_gemini_json_mode_and_retries_truncated_text(settings, caplog):
    import httpx

    from app.core.llm import GeminiLLMClient

    requests = []

    def reply(request):
        requests.append(json.loads(request.content))
        first = len(requests) == 1
        return httpx.Response(200, json={"candidates": [{
            "content": {"parts": [{"text": '{"work_experience": [' if first else json.dumps(FIX)}]},
            "finishReason": "MAX_TOKENS" if first else "STOP",
        }]})

    with httpx.Client(base_url="https://generativelanguage.googleapis.com", transport=httpx.MockTransport(reply)) as http:
        llm = GeminiLLMClient(settings.gemini_api_key, settings.gemini_model, 90, 8192, http=http)
        outcome = run_parse(layout_pdf(), llm)
    assert outcome.ok and outcome.attempts == 2
    assert all(r["generationConfig"]["responseMimeType"] == "application/json" for r in requests)
    assert [r["generationConfig"]["maxOutputTokens"] for r in requests] == [8192, 16384]
    assert "縮短重複敘述與原文片段" in requests[1]["contents"][0]["parts"][0]["text"]
    assert "llm_output_truncated provider=gemini reason=MAX_TOKENS" in caplog.text
    assert '{"work_experience": [' not in caplog.text


def test_104_autobiography_uses_cleaned_source_instead_of_model_summary():
    profile = copy.deepcopy(FIX)
    profile["autobiography"] = {"text": "模型摘要…", "stated_goals": ["資料整理"]}
    result = run_parse(layout_pdf(), MockLLM(profile))
    assert result.ok
    assert result.profile["autobiography"]["text"] == "希望把資料整理用在製造流程。"
    assert result.profile["autobiography"]["stated_goals"] == ["資料整理"]


def test_complete_project_url_survives_without_pdf_link_annotation():
    from app.services.resume.urlmask import restore_urls, tokenize_urls

    url = "https://example.test/tool"
    _, mapping = tokenize_urls(url, [])
    restored, _ = restore_urls({"projects": [{"url": "[URL-1]"}]}, mapping)
    assert restored["projects"][0]["url"] == url


def test_bilingual_sections_end_the_autobiography():
    sections = find_sections([
        "學歷\n虛構學校\n工作經驗\n虛構公司\n自傳\n自我介紹第二行仍在。",
        "跨頁接續自傳。\n【Portfolio】\nhttps://example.test/demo\n【Experience Highlights】\n摘要\n"
        "【Independent Projects】\n虛構盤點專案",
    ])
    assert [(section.name, section.text) for section in sections] == [
        ("學歷", "虛構學校"), ("工作經歷", "虛構公司"), ("自傳", "自我介紹第二行仍在。"),
        ("自傳", "跨頁接續自傳。"), ("作品集", "https://example.test/demo"),
        ("經歷摘要", "摘要"), ("專案", "虛構盤點專案"),
    ]


def test_nonempty_pdf_sections_omitted_by_model_raise_review_flags():
    profile = copy.deepcopy(FIX)
    for field in ("work_experience", "education", "skills", "projects"):
        profile[field] = []
    profile["missing_flags"] = []
    result = run_parse(layout_pdf(), MockLLM(profile))
    assert result.ok
    flags = {flag["field_path"]: flag["message"] for flag in result.profile["missing_flags"]}
    assert {"work_experience", "education", "skills"} <= flags.keys()
    assert "projects" not in flags
    assert all("請核對" in msg for msg in flags.values())


def test_104_keeps_portfolio_links_omitted_by_model_and_ignores_unrelated_attachments():
    dynamic = "https://example.test/portfolio"
    static = "https://drive.google.com/files/portfolio-full-link"
    with pymupdf.open(stream=layout_pdf(), filetype="pdf") as doc:
        page = doc.new_page()
        page.insert_textbox(
            pymupdf.Rect(30, 30, 540, 780),
            f"【Portfolio】\n{dynamic}\n附件\n{dynamic}\n動態作品連結\n"
            "drive.google.com/files/portfolio…\n靜態作品集\n"
            "https://example.test/cert\n結業證明",
            fontname="china-t", fontsize=11,
        )
        link_area = page.search_for("drive.google.com/files/portfolio")[0]
        page.insert_link({"kind": pymupdf.LINK_URI, "from": link_area, "uri": static})
        pdf = doc.tobytes()

    profile = copy.deepcopy(FIX)
    profile["portfolio"] = [{"id": "portfolio-1", "title": "靜態作品集", "url": None}]
    llm = MockLLM(profile)
    result = run_parse(pdf, llm)
    assert result.ok, result.error_code
    assert all(url not in llm.prompts[0] for url in (dynamic, static, "example.test/cert"))
    assert [(item["title"], item["url"]) for item in result.profile["portfolio"]] == [
        ("靜態作品集", static), ("動態作品連結", dynamic),
    ]
    assert len({item["id"] for item in result.profile["portfolio"]}) == 2
