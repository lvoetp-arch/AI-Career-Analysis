"""S5 前端靜態檢查（沒有瀏覽器也能跑）：載入順序、等級說明逐字、不用 innerHTML／localStorage、下一步路徑。"""
import re
from pathlib import Path

FRONT = Path(__file__).resolve().parents[3] / "frontend"
HTML = (FRONT / "assess.html").read_text(encoding="utf-8")
JS = (FRONT / "js" / "assess.js").read_text(encoding="utf-8")

# 《S5_自評規格摘要》的等級錨點，必須逐字相同
ANCHORS = ["了解概念、需指導", "能完成基礎任務", "能獨立完成常見任務", "能處理複雜問題並改善流程", "能主導、設計方法並指導他人"]


def test_script_order_follows_contract_12():
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', HTML)
    assert srcs[:3] == ["js/config.js", "vendor/supabase-js-2.45.4.umd.js", "js/auth.js"]
    assert srcs[3:] == ["js/api.js", "js/ui.js", "js/assess.js"]
    assert not any(s.startswith(("http:", "https:", "//")) for s in srcs)  # 不從 CDN 載入


def test_level_anchors_verbatim_and_in_order():
    pos = [JS.find(a) for a in ANCHORS]
    assert all(p >= 0 for p in pos) and pos == sorted(pos)
    for n, a in enumerate(ANCHORS, 1):
        assert f"[{n}, '{a}']" in JS


def test_no_unsafe_dom_or_storage():
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "localStorage", "sessionStorage", "eval("):
        assert bad not in JS, bad


def test_does_not_use_forbidden_profile_fields():
    assert "missing_flags" not in JS and ".years_text" not in JS and "years_text" not in JS


def test_steps_bar_marks_step_3_current():
    assert re.search(r'is-current" aria-current="step"><span class="steps__num">3</span>', HTML)


def test_review_page_points_to_assess():
    review = FRONT / "js" / "review.js"
    if review.exists():
        assert "NEXT_PAGE = 'assess.html'" in review.read_text(encoding="utf-8")


def test_login_gate_comes_before_any_api_call():
    """靜態防線：requireLogin 必須在第一個 Api 呼叫之前；load() 只能在 requireLogin 通過後呼叫。"""
    assert "Auth.requireLogin" in JS or "A.requireLogin" in JS
    first_api = JS.find("Api.get(")
    assert 0 < JS.find("requireLogin") < first_api
    assert not re.search(r"^\s*load\(\);", JS, re.MULTILINE)          # 不可在載入時直接呼叫
    assert re.search(r"requireLogin\(\)\.then\(\(ok\) => \{\s*if \(ok\) load\(\);", JS)
    assert re.search(r"^\s*start\(\);\s*$", JS, re.MULTILINE)


def test_navigation_only_after_put_succeeds():
    assert JS.find("Api.put(") < JS.find("location.href = NEXT_PAGE")
    assert "res.career_input_id" in JS and "typeof res.career_input_id !== 'string'" in JS
