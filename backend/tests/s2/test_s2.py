import pathlib
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.s2 import privacy
from app.services.s2.compat import AppError, current_user, get_store
from tests.s2.fakes import FakeDB

ROOT = pathlib.Path(__file__).resolve().parents[3]
FRONT = ROOT / "frontend"
A, B = "user-a", "user-b"


@pytest.fixture
def env():
    db, who = FakeDB(), {"u": A}
    app = create_app()
    app.dependency_overrides[get_store] = lambda: db
    app.dependency_overrides[current_user] = lambda: who["u"]
    return db, who, TestClient(app, raise_server_exceptions=False)


# ---- 同意記錄 ----
def test_consent_create_and_list(env):
    db, who, c = env
    r = c.post("/api/consents", json={"consent_type": "privacy", "version": privacy.PRIVACY_VERSION})
    assert r.status_code == 201 and {"id", "accepted_at"} <= r.json().keys()
    assert [x["version"] for x in c.get("/api/consents").json()] == [privacy.PRIVACY_VERSION]
    # 重複送出不會重複記錄
    c.post("/api/consents", json={"consent_type": "privacy", "version": privacy.PRIVACY_VERSION})
    assert len(db.tables["consent_records"]) == 1


@pytest.mark.parametrize("body", [
    {"consent_type": "marketing", "version": privacy.PRIVACY_VERSION},
    {"consent_type": "privacy", "version": "1999-01-01"},
])
def test_consent_rejects_bad_input(env, body):
    _, _, c = env
    r = c.post("/api/consents", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_consents_are_per_user(env):
    db, who, c = env
    c.post("/api/consents", json={"consent_type": "privacy", "version": privacy.PRIVACY_VERSION})
    who["u"] = B
    assert c.get("/api/consents").json() == []


# ---- require_consent ----
def test_require_consent(env):
    db, _, _ = env
    with pytest.raises(AppError) as e:
        privacy.require_consent(A, db)
    assert e.value.code == "CONSENT_REQUIRED"
    db.tables["consent_records"].append({"user_id": A, "consent_type": "privacy", "version": "2020-01-01"})
    with pytest.raises(AppError):  # 舊版不算
        privacy.require_consent(A, db)
    privacy.record_consent(db, A, "privacy", privacy.PRIVACY_VERSION)
    privacy.require_consent(A, db)
    with pytest.raises(AppError):  # 別人同意不算我的
        privacy.require_consent(B, db)


# ---- 刪除我的資料 ----
def test_delete_removes_everything_and_only_mine(env):
    db, _, c = env
    db.seed_user(A); db.seed_user(B)
    db.files.add(f"{A}/orphan.pdf")  # 資料表沒有紀錄的孤兒檔也要清掉
    r = c.delete("/api/me/data")
    assert r.status_code == 200
    assert r.json()["deleted"] == {"files": 1, "profiles": 1, "career_inputs": 1, "analyses": 1, "consents": 1}
    assert db.count(A) == 0 and db.files_of(A) == []
    assert db.count(B) == len(db.TABLES) and db.files_of(B) == [f"{B}/resume_files-{B}.pdf"]


def test_delete_storage_failure_keeps_rows_and_retry_works(env):
    db, _, c = env
    db.seed_user(A)
    db.fail.add(("storage", "remove"))
    r = c.delete("/api/me/data")
    assert r.status_code == 500 and r.json()["error"]["code"] == "STORAGE_ERROR"
    assert db.count(A) == len(db.TABLES)  # 路徑還在，才有辦法重試
    db.fail.clear()
    assert c.delete("/api/me/data").status_code == 200 and db.count(A) == 0 and db.files_of(A) == []


def test_delete_table_failure_is_not_reported_as_success(env):
    db, _, c = env
    db.seed_user(A)
    db.fail.add(("career_inputs", "delete"))
    r = c.delete("/api/me/data")
    assert r.status_code == 500 and r.json()["error"]["details"][0]["path"] == "career_inputs"
    db.fail.clear()
    assert c.delete("/api/me/data").status_code == 200 and db.count(A) == 0


def test_delete_is_idempotent(env):
    _, _, c = env
    r = c.delete("/api/me/data")
    assert r.status_code == 200 and sum(r.json()["deleted"].values()) == 0


# ---- 前端內容與契約 §10 對照 ----
def read(n): return (FRONT / n).read_text(encoding="utf-8")


def test_privacy_page_matches_contract():
    t = read("privacy.html")
    for item in ["姓名", "性別", "年齡／生日", "役別", "婚姻／家庭", "照片", "電話", "Email", "住址",
                 "身分證字號", "國籍", "宗教", "健康狀況", "就業狀態", "交通工具", "聯絡時段"]:
        assert f"<li>{item}</li>" in t, item
    assert "Google（Gemini）" in t and "姓名無法可靠遮蔽" in t and "再按「確認並繼續」重試" in t
    assert privacy.PRIVACY_VERSION in t


def test_frontend_privacy_version_matches_backend():
    assert f'data-privacy-version="{privacy.PRIVACY_VERSION}"' in read("privacy.html")


def test_index_page_rules():
    t = read("index.html")
    assert "履歷文字會傳給 AI 供應商處理；系統在確認時刪除原始檔案，若失敗可重試；你可以隨時刪除全部資料。" in t
    assert "不是職缺保證" in t
    assert not re.search(r"\d+\s*%", re.sub(r'style="[^"]*"', "", t))  # 示意卡不得有百分比分數（§10.9）
    assert "font-size:1rem" in read("css/s2-pages.css").split(".site-footer p{")[1].split("}")[0]


@pytest.mark.parametrize("f", ["index.html", "login.html", "register.html", "privacy.html", "settings.html",
                               "js/auth.js", "js/s2-common.js", "js/config.js"])
def test_no_innerhtml_or_storage_of_resume_data(f):
    t = read(f)
    assert "innerHTML" not in t and "localStorage" not in t and "sessionStorage" not in t


# ======== S2b 任務 A：隱私頁、警告、跑馬燈、supabase-js、config ========
PAGES = ["index.html", "login.html", "register.html", "settings.html", "privacy.html"]
WARNING = "這是測試版本，請勿上傳含真實個資的履歷"


def test_privacy_page_discloses_provider_plan_and_what_parsing_sends():
    t = read("privacy.html")
    for must in ["Google（Gemini）", "免費額度", "用於改善產品", "人工審閱",  # §10-11
                 WARNING,
                 "履歷全文", "公司名稱", "學校名稱", "專案內容", "自傳內容",  # §10-2：解析階段如實列出
                 "電話", "身分證字號", "網址會換成代號", "姓名依你選填的寫法遮蔽",
                 "你填的姓名不會被儲存", "姓名無法可靠遮蔽", "更換供應商"]:
        assert must in t, must
    assert t.index(WARNING) < t.index("<h2>我們會收集什麼</h2>")  # 警告在同意之前、最上面就看得到


def test_privacy_version_was_bumped_and_matches_everywhere():
    assert privacy.PRIVACY_VERSION >= "2026-11-18"  # 舊版 2026-11-12 的人必須重新同意
    assert f"版本 {privacy.PRIVACY_VERSION}" in read("privacy.html")
    assert f'data-privacy-version="{privacy.PRIVACY_VERSION}"' in read("privacy.html")


def _css_rule(css, selector):
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def test_home_and_privacy_show_test_warning_in_large_enough_type():
    assert WARNING in read("index.html") and WARNING in read("privacy.html")
    for f in ("index.html", "privacy.html"):
        assert 'class="test-warning"' in read(f)
    size = re.search(r"font-size:\s*([\d.]+)(rem|px)", _css_rule(read("css/s2-pages.css"), ".test-warning"))
    assert size and (float(size.group(1)) * (16 if size.group(2) == "rem" else 1)) >= 14


def test_marquee_runs_at_most_5_seconds_once_and_respects_reduced_motion():
    css = read("css/s2-pages.css")
    rule = _css_rule(css, ".marquee__track")
    assert "infinite" not in css  # 沒有任何無限循環的動畫
    m = re.search(r"animation:\s*marquee\s+([\d.]+)s\b[^;]*?\b1\b[^;]*forwards", rule)
    assert m and float(m.group(1)) <= 5, rule  # 只跑 1 次、≤ 5 秒、停在最後一格
    assert "animation-delay" not in rule
    reduced = css.split("prefers-reduced-motion: reduce")[1]
    assert "animation: none" in reduced.split("}")[0]


@pytest.mark.parametrize("f", PAGES)
def test_supabase_js_is_served_by_the_project_not_a_cdn(f):
    t = read(f)
    assert "cdn.jsdelivr.net" not in t and "unpkg.com" not in t and "cdnjs" not in t
    assert t.count('<script src="vendor/supabase-js-2.45.4.umd.js"></script>') == 1
    assert t.index("js/config.js") < t.index("vendor/supabase-js") < t.index("js/auth.js")


def test_vendored_supabase_js_exists_and_has_license_and_hash_note():
    js = FRONT / "vendor" / "supabase-js-2.45.4.umd.js"
    assert js.stat().st_size > 50_000 and b"createClient" in js.read_bytes()
    assert (FRONT / "vendor" / "supabase-js-LICENSE.txt").exists()
    note = (FRONT / "vendor" / "README.md").read_text(encoding="utf-8")
    assert "2.45.4" in note and re.search(r"[0-9a-f]{64}", note)


def test_committed_config_has_only_placeholders_and_loads_local_config():
    t = read("js/config.js")
    assert re.search(r'SUPABASE_URL:\s*""', t) and re.search(r'SUPABASE_ANON_KEY:\s*""', t)
    assert not re.search(r"eyJ[A-Za-z0-9_\-]{8,}\.", t) and not re.search(r"https://(?!x+\.)[a-z0-9]+\.supabase\.co", t)  # 說明文字裡的 xxxx 範例不算
    assert "config.local.js" in t and "__APP_CONFIG_LOCAL_REQUESTED__" in t  # 載入本機設定，且有防止循環載入


def test_local_config_is_ignored_and_not_tracked():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "frontend/js/config.local.js" in [line.strip() for line in ignore]
    if shutil.which("git"):
        r = subprocess.run(["git", "ls-files", "frontend/js"], cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0:
            assert "config.local.js" not in r.stdout


def test_pages_show_a_not_configured_notice_without_innerhtml():
    t = read("js/s2-common.js")
    assert "尚未設定登入服務" in t and "config.local.js" in t and "isConfigured()" in t
    assert "innerHTML" not in t
