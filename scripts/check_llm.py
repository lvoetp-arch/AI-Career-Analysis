"""診斷：用最小請求測 Gemini 連線。不含履歷。只印金鑰最後 4 碼（用來確認是不是新金鑰）。
在專案根目錄執行：python scripts/check_llm.py"""
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def read_env(path):
    """與 run_parse_samples.py 相同：同一檔案內有重複變數時，取第一行；並提出警告。"""
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                if k in out:
                    print(f"警告：{path.name} 內有重複的 {k}（只會使用第一行，其餘被忽略）")
                    continue
                out[k] = v.strip().strip('"').strip("'")
    return out


os_key = os.environ.get("GEMINI_API_KEY")  # 在讀 .env 之前，PowerShell 本身是否已設定
be, rt = read_env(ROOT / "backend" / ".env"), read_env(ROOT / ".env")
sources = [("PowerShell 環境變數", os_key), ("backend\\.env", be.get("GEMINI_API_KEY")), ("根目錄 .env", rt.get("GEMINI_API_KEY"))]
print("金鑰來源（最後 4 碼；先列出的優先被使用）：")
for name, v in sources:
    print(f"  {name}: " + (f"…{v[-4:]}（長度 {len(v)}）" if v else "（未設定）"))
used = next((v for _, v in sources if v), "")
print("實際使用的金鑰最後 4 碼 =", used[-4:] if used else "（無）")
vals = {v for _, v in sources if v}
if len(vals) > 1:
    print("警告：不同位置的金鑰不一樣，腳本只會用優先順序最前面的那個！")
model = os.environ.get("GEMINI_MODEL") or be.get("GEMINI_MODEL") or rt.get("GEMINI_MODEL") or "gemini-2.5-flash"
print("模型 =", model)
try:
    r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                   headers={"x-goog-api-key": used.strip()}, timeout=30,
                   json={"contents": [{"role": "user", "parts": [{"text": "請只回覆 OK"}]}]})
    print("HTTP 狀態 =", r.status_code)
    e = r.json().get("error") if r.headers.get("content-type", "").startswith("application/json") else None
    if e:
        print("Google 回報：", e.get("status"), "-", str(e.get("message"))[:300])
        for d in e.get("details", []):
            for v in d.get("violations", []):
                print("  觸發的額度：", v.get("quotaId") or v.get("quotaMetric"))
            if d.get("retryDelay"):
                print("  建議等待：", d["retryDelay"])
    else:
        print("成功，連線與金鑰正常。" if r.status_code == 200 else "非預期回應")
except Exception as ex:  # noqa: BLE001
    print("連線失敗：", type(ex).__name__)
