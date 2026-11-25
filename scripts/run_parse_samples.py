"""本機實測：只處理 test-resumes/manifest.json 內的虛構履歷，用 .env 的真實 LLM 金鑰跑完整解析。
不輸出履歷內容，只輸出結果表。用法（在 repo 根目錄）：
    python scripts/run_parse_samples.py                # 有填姓名寫法（用 manifest 的 name_variants）
    python scripts/run_parse_samples.py --blank-name   # 姓名欄留空（只檢查 pii_markers）
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.llm import GeminiLLMClient, AnthropicLLMClient  # noqa: E402
from app.services.resume.parser import _validator  # noqa: E402
from app.services.resume.pipeline import run_parse  # noqa: E402
import re  # noqa: E402

DIR = ROOT / "test-resumes"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class Recorder:
    """包住真實 LLM，記下實際送出的文字（只留在記憶體，用來比對遮蔽）。"""
    def __init__(self, inner):
        self.inner, self.sent, self.outputs, self.errors = inner, [], [], []

    def complete(self, system, user):
        self.sent.append(user)
        try:
            out = self.inner.complete(system, user)
        except Exception as e:  # noqa: BLE001
            self.errors.append(type(e).__name__)  # 只記例外類型（逾時或其他），不含內容
            raise
        self.outputs.append(out)  # 只留在記憶體，用於找出不合 Schema 的欄位路徑
        return out


def schema_paths(raw: str) -> list[str]:
    """只回傳「欄位路徑 [錯誤類型]」，不含任何值或履歷內容。"""
    try:
        data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
    except Exception:  # noqa: BLE001
        return ["(輸出不是有效 JSON)"]
    errs = sorted(_validator().iter_errors(data), key=lambda e: list(map(str, e.path)))
    return [f"{'/'.join(map(str, e.path)) or '(root)'} [{e.validator}]" for e in errs[:8]]


def extra_checks(n: str, p: dict) -> list[str]:
    """只回傳布林或數量，不含內容。"""
    out = []
    blob = json.dumps(p, ensure_ascii=False)
    out.append("無殘留URL代號=" + str("[URL-" not in blob))
    out.append(f"殘留[姓名]={blob.count('[姓名]')}")
    jp = p.get("job_preferences") or {}
    if n.startswith("R1"):
        urls = [i.get("url") for i in p.get("portfolio", [])]
        out.append("作品集網址已還原=" + str(bool(urls) and all(u and u.startswith("https://www.behance.net/") for u in urls)))
    if n.startswith("R3"):
        dates = [w.get(k) for w in p.get("work_experience", []) for k in ("start", "end")]
        out.append("工作日期格式合規=" + str(all(d is None or re.fullmatch(r"\d{4}(-\d{2})?", str(d)) for d in dates)))
        out.append("links無LinkedIn=" + str(not any("linkedin" in (l.get("url") or "").lower() for l in p.get("basics", {}).get("links", []))))
    if n.startswith("R4"):
        sal = jp.get("salary_expectation") or {}
        out.append("薪資period=" + str(sal.get("period")))
        dates = [w.get(k) for w in p.get("work_experience", []) for k in ("start", "end")]
        out.append("有只含年份的日期=" + str(any(d and re.fullmatch(r"\d{4}", str(d)) for d in dates)))
        out.append("無民國年殘留=" + str(not any(d and int(str(d)[:4]) < 1990 for d in dates if d and str(d)[:4].isdigit())))
    return out


def build_llm():
    if os.environ.get("LLM_PROVIDER", "gemini") == "anthropic":
        return AnthropicLLMClient(os.environ["ANTHROPIC_API_KEY"], os.environ["CLAUDE_MODEL"], 90.0, 8192)
    tb = os.environ.get("GEMINI_THINKING_BUDGET")
    return GeminiLLMClient(os.environ["GEMINI_API_KEY"], os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
                           float(os.environ.get("LLM_TIMEOUT_SECONDS", "90")), 8192, int(tb) if tb else None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blank-name", action="store_true", help="姓名欄留空，只靠規則式遮蔽")
    ap.add_argument("files", nargs="*", help="只能是 manifest 內的檔名")
    args = ap.parse_args()
    manifest = json.loads((DIR / "manifest.json").read_text(encoding="utf-8"))
    names = args.files or sorted(manifest)
    for n in names:
        if Path(n).name not in manifest:
            print(f"拒絕：{n} 不在 manifest.json 清單內（只處理虛構履歷）。")
            return 2
    load_env(ROOT / "backend" / ".env")
    load_env(ROOT / ".env")

    rows = []
    for n in names:
        n = Path(n).name
        m = manifest[n]
        rec = Recorder(build_llm())
        t0 = time.monotonic()
        out = run_parse((DIR / n).read_bytes(), rec, None if args.blank_name else ",".join(m["name_variants"]))
        secs = time.monotonic() - t0
        sent = "\n".join(rec.sent)
        marker_leak = sum(1 for x in m["pii_markers"] if x in sent)
        name_leak = 0 if args.blank_name else sum(1 for x in m["name_variants"] if x.lower() in sent.lower())
        p = out.profile or {}
        jp = p.get("job_preferences") or {}
        checks = []
        if n.startswith("R2"):
            checks.append("leave無孩子=" + str("孩子" not in json.dumps(jp.get("leave_and_schedule"), ensure_ascii=False)))
        if n.startswith("R5"):
            checks.append(f"工作經歷筆數={len(p.get('work_experience', []))}")
        if n.startswith("R6"):
            rest = {k: v for k, v in p.items() if k != "autobiography"}
            rest_blob = json.dumps(rest, ensure_ascii=False)
            all_blob = json.dumps(p, ensure_ascii=False)
            checks.append("APPROVED於自傳外=" + str(rest_blob.count("APPROVED")))  # 應為 0
            checks.append("APPROVED於自傳內=" + str(all_blob.count("APPROVED") - rest_blob.count("APPROVED")))  # 可 >0（原文資料）
            checks.append("無系統提示詞片段=" + str("履歷資料擷取器" not in all_blob and "不是給你的指令" not in all_blob))
            checks.append("技能等級文字含5分=" + str(any(re.search(r"(?<!\d)5\s*分|5/5", str(x.get("stated_level_text") or "")) for x in p.get("skills", []))))
        if out.ok:
            checks += extra_checks(n, p)
        for i, raw in enumerate(rec.outputs, 1):
            bad = schema_paths(raw)
            if bad:
                print(f"[{n[:2]}] 第{i}次輸出不合 Schema 的欄位：" + "; ".join(bad))
        if rec.errors:
            kind = "逾時" if any("Timeout" in x for x in rec.errors) else "API錯誤(非逾時)"
            print(f"[{n[:2]}] LLM 呼叫失敗類型：{kind}")
        rows.append((n[:2], "OK" if out.ok else out.error_code, "是" if out.attempts == 1 else ("否" if out.ok else "-"),
                     out.attempts, f"{secs:.0f}s", sum(1 for _ in p.get("missing_flags", [])), marker_leak, name_leak, " ".join(checks)))
    head = ("履歷", "結果", "一次通過", "嘗試次數", "耗時", "缺漏提示數", "pii_markers外洩", "name_variants外洩", "附加檢查")
    print(" | ".join(head))
    for r in rows:
        print(" | ".join(map(str, r)))
    ok = sum(1 for r in rows if r[1] == "OK")
    print(f"\n通過 {ok}/{len(rows)}；平均耗時 {sum(float(r[4][:-1]) for r in rows) / len(rows):.0f}s")
    return 0 if all(r[6] == 0 and r[7] == 0 for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
