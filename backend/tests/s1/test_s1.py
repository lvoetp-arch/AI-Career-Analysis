"""S1 測試：對比度（輸出 docs/handoff/S1_contrast.md）與靜態檢查。只檢查 S1 擁有的檔案。"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FE = ROOT / "frontend"
OWNED = sorted((FE / "css").glob("*.css")) + [FE / "js" / n for n in ("api.js", "ui.js", "auth.js")] + [FE / "ui-kit.html"]

# (前景 token, 背景 token, 類別: normal=4.5 / large=3 / ui=3, 用途)
PAIRS = [
    ("ink", "bg", "normal", "內文／頁面"), ("ink", "surface", "normal", "卡片內文"),
    ("ink", "yellow", "normal", "黃底（標籤、步驟完成、頁尾）"), ("ink", "pink", "normal", "粉底（貼紙、部分證據）"),
    ("ink", "red", "normal", "紅底按鈕／Logo／不足標籤"), ("ink", "blue", "normal", "藍底（備用，墨字）"),
    ("ink-muted", "bg", "normal", "次要文字"), ("ink-muted", "surface", "normal", "卡片次要文字／未完成步驟"),
    ("red-text", "bg", "normal", "紅字（錯誤訊息）"), ("red-text", "surface", "normal", "卡片上的紅字"),
    ("red-text", "error-bg", "normal", "錯誤欄位底色上的紅字"),
    ("surface", "ink", "normal", "選中評分／Toast"), ("yellow", "ink", "normal", "目前步驟"),
    ("surface", "blue-deep", "normal", "藍色小標籤"), ("surface", "red-text", "normal", "危險按鈕、錯誤 Toast"),
    ("surface", "success", "normal", "成功 Toast"),
    ("ink", "info-bg", "normal", "資訊提示條"), ("ink", "warn-bg", "normal", "警示提示條"),
    ("ink", "error-bg", "normal", "錯誤提示條"), ("ink", "success-bg", "normal", "成功提示條"),
    ("disabled-text", "disabled-bg", "normal", "停用按鈕（WCAG 豁免，仍檢查）"),
    ("blue-deep", "bg", "ui", "焦點框／非文字"), ("blue-deep", "yellow", "ui", "焦點框於黃底"),
    ("ink", "bg", "ui", "邊框"),
]
MIN = {"normal": 4.5, "large": 3.0, "ui": 3.0}


def tokens():
    css = (FE / "css" / "tokens.css").read_text(encoding="utf-8")
    return {k: v.upper() for k, v in re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", css)}


def lum(hex_):
    def ch(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = hex_[1:3], hex_[3:5], hex_[5:7]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_contrast_table():
    t = tokens()
    rows, fails = [], []
    for fg, bg, cls, use in PAIRS:
        r = ratio(t[fg], t[bg])
        ok = r >= MIN[cls]
        rows.append(f"| {use} | `--{fg}` {t[fg]} | `--{bg}` {t[bg]} | {cls} | {r:.2f} | {MIN[cls]} | {'通過' if ok else '失敗'} |")
        if not ok:
            fails.append(f"{fg}/{bg} {r:.2f}")
    head = ("# S1 對比度表（由測試自動產生）\n\n| 用途 | 前景 | 背景 | 類別 | 對比度 | 門檻 | 結果 |\n|---|---|---|---|---|---|---|\n")
    out = ROOT / "docs" / "handoff"
    out.mkdir(parents=True, exist_ok=True)
    (out / "S1_contrast.md").write_text(head + "\n".join(rows) + "\n", encoding="utf-8")
    assert not fails, fails


@pytest.mark.parametrize("path", OWNED, ids=lambda p: p.name)
def test_no_innerhtml_or_frameworks(path):
    s = path.read_text(encoding="utf-8")
    assert "innerHTML" not in s
    for bad in ("bootstrap", "tailwind", "jquery", "unpkg.com", "cdn.jsdelivr", "cdnjs"):
        assert bad not in s.lower(), bad
    for url in re.findall(r"""(?:src|href)=["'](https?://[^"']+)""", s):
        assert "fonts.googleapis.com" in url or "fonts.gstatic.com" in url, url


@pytest.mark.parametrize("name", ["api.js", "ui.js"])
def test_no_localstorage_js(name):
    assert "localStorage" not in (FE / "js" / name).read_text(encoding="utf-8")


def kit():
    return (FE / "ui-kit.html").read_text(encoding="utf-8")


def test_kit_basics():
    s = kit()
    assert '<html lang="zh-Hant">' in s
    assert re.search(r'<meta name="viewport"[^>]*width=device-width', s)
    assert "localStorage" not in s
    assert "wght@400;500;700;900" in s and "display=swap" in s


def test_kit_no_pii_or_scores():
    s = kit()
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", s), "不得出現 Email 樣式"
    assert not re.search(r"\b0\d{1,2}[- ]?\d{3,4}[- ]?\d{3,4}\b", s), "電話樣式"
    assert not re.search(r"\b[A-Z][12]\d{8}\b", s), "身分證樣式"
    visible = re.sub(r"<(style|script)\b.*?</\1>", "", s, flags=re.S)
    assert not re.search(r"\d+\s*[%％]", visible), "不得出現百分比分數"


def test_reduced_motion_and_focus():
    base = (FE / "css" / "base.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in base and ":focus-visible" in base
    comp = (FE / "css" / "components.css").read_text(encoding="utf-8")
    assert "min-height: var(--tap)" in comp
