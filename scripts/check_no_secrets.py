#!/usr/bin/env python3
"""提交前檢查：掃描暫存區（staged）的檔案，命中就阻擋 commit。

用法：
    python scripts/check_no_secrets.py          # 檢查暫存區（hook 使用）
    python scripts/check_no_secrets.py --all    # 稽核目前所有已追蹤檔案

輸出只顯示「檔案:行號 規則名稱」，不會印出命中的內容，避免把金鑰或個資再次印到終端機。
若確認是誤判，可在該行任意位置加上註記：check-no-secrets: allow
"""
from __future__ import annotations

import re
import subprocess
import sys
from typing import Callable, Iterable

ALLOW_MARK = "check-no-secrets: allow"
MAX_SCAN_BYTES = 5 * 1024 * 1024

# ---- 路徑規則 ----
def _is_test_resumes(p: str) -> bool:
    return "/test-resumes/" in "/" + p


def _is_env_file(p: str) -> bool:
    name = p.rsplit("/", 1)[-1]
    return name.startswith(".env") and name != ".env.example"


def _is_stray_pdf(p: str) -> bool:
    return p.lower().endswith(".pdf") and not p.startswith("docs/")


PATH_RULES: list[tuple[str, Callable[[str], bool]]] = [
    ("test-resumes/ 路徑（真實履歷不得進版控）", _is_test_resumes),
    (".env 檔案（含金鑰）", _is_env_file),
    ("docs/ 以外的 PDF（可能是履歷）", _is_stray_pdf),
]

# ---- 內容規則 ----
CONTENT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("Anthropic 金鑰樣式", re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}")),
    ("Google API 金鑰樣式", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("JWT 樣式（可能是 Supabase 金鑰或使用者 token）",
     re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("service_role 憑證", re.compile(r"(?i)service_role[\w \"']{0,24}[=:]\s*[\"']?[A-Za-z0-9_\-\.]{20,}")),
    ("金鑰環境變數被填了值",
     re.compile(r"\b(?:SUPABASE_SERVICE_KEY|SUPABASE_ANON_KEY|ANTHROPIC_API_KEY|GEMINI_API_KEY)\s*[=:]\s*[\"']?[A-Za-z0-9_\-\.]{8,}")),
    ("台灣手機號碼", re.compile(r"(?<!\d)(?:\+?886[-\s]?|0)9\d{2}[-\s]?\d{3}[-\s]?\d{3}(?!\d)")),
    ("身分證字號格式", re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?![A-Za-z0-9])")),
]
EMAIL_RE = re.compile(r"([A-Za-z0-9._%+\-]+)@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,})")
SAFE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "users.noreply.github.com"}
SAFE_EMAIL_SUFFIXES = (".invalid", ".example", ".test", ".localhost")


def _email_is_safe(local: str, domain: str) -> bool:
    d = domain.lower()
    if d in SAFE_EMAIL_DOMAINS or d.endswith(SAFE_EMAIL_SUFFIXES):
        return True
    return local == "git" and d == "github.com"


def check_path(path: str) -> list[str]:
    p = path.replace("\\", "/")
    return [name for name, fn in PATH_RULES if fn(p)]


def scan_text(text: str) -> list[tuple[int, str]]:
    """回傳 [(行號, 規則名稱)]。"""
    findings: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARK in line:
            continue
        for name, pattern in CONTENT_RULES:
            if pattern.search(line):
                findings.append((lineno, name))
        for m in EMAIL_RE.finditer(line):
            if not _email_is_safe(m.group(1), m.group(2)):
                findings.append((lineno, "Email 格式"))
                break
    return findings


def scan_file(path: str, data: bytes) -> list[str]:
    msgs = [f"{path}  規則：{r}" for r in check_path(path)]
    if b"\0" in data[:8000] or len(data) > MAX_SCAN_BYTES:
        return msgs
    text = data.decode("utf-8", errors="replace")
    msgs += [f"{path}:{ln}  規則：{r}" for ln, r in scan_text(text)]
    return msgs


# ---- git 互動 ----
def _git(args: list[str], cwd: str | None = None) -> bytes:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True).stdout


def _repo_root() -> str:
    return _git(["rev-parse", "--show-toplevel"]).decode("utf-8").strip()


def _paths(args: list[str], cwd: str) -> list[str]:
    raw = _git(args, cwd)
    return [p.decode("utf-8", errors="replace") for p in raw.split(b"\0") if p]


def run(scan_all: bool = False) -> int:
    try:
        root = _repo_root()
        if scan_all:
            paths = _paths(["ls-files", "-z"], root)
        else:
            paths = _paths(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], root)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("無法執行 git，檢查中止。", file=sys.stderr)
        return 2

    problems: list[str] = []
    for path in paths:
        try:
            data = _git(["show", f":{path}"], root)  # 讀「暫存區」的版本
        except subprocess.CalledProcessError:
            data = b""
        problems += scan_file(path, data)

    if problems:
        print("[阻擋] 發現可能的金鑰、個資或不該提交的檔案：", file=sys.stderr)
        for line in problems:
            print("  - " + line, file=sys.stderr)
        print("\n處理方式：把該內容移出檔案後重新 git add。", file=sys.stderr)
        print("若是誤判，可在該行加上註記「" + ALLOW_MARK + "」。", file=sys.stderr)
        print("若真的是金鑰：先到服務商更換金鑰；若是真實履歷，需清除 git 歷史。", file=sys.stderr)
        return 1
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    args = list(argv if argv is not None else sys.argv[1:])
    return run(scan_all="--all" in args)


if __name__ == "__main__":
    sys.exit(main())
