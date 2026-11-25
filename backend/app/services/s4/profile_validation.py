"""S4：履歷資料（parsed_json）的 Schema 驗證。
回傳的 path 用與 missing_flags 相同的寫法（work_experience[0].gender）；message 一律是固定中文，
絕不含使用者輸入的值（契約 §10-3，jsonschema 原生訊息會帶出原值，所以不使用）。"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "resume_profile.schema.json"  # app/schemas/
MAX_DETAILS = 50
MAX_BYTES = 200_000  # parsed_json 序列化後上限（fixture 約 17 KB）

_DATE_MSG = "日期請填 YYYY-MM 或 YYYY（例如 2024-03）"
_BAD_SCHEME = re.compile(r"^(javascript|data|vbscript|file):", re.IGNORECASE)
_CTRL = re.compile(r"[\x00-\x20]")  # 瀏覽器解析 scheme 時會忽略這些字元（java\tscript:）


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8-sig")))


def _fmt(parts: list[Any]) -> str:
    out = ""
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else (("." if out else "") + str(p)[:80])
    return out or "parsed_json"


def _message(validator: str) -> str:
    return {
        "pattern": _DATE_MSG,
        "maxLength": "內容太長，請縮短",
        "maxItems": "項目太多",
        "type": "資料型別不正確",
        "enum": "不是允許的選項",
        "const": "版本不符",
        "minLength": "此欄位不可為空",
        "required": "此欄位為必填",
    }.get(validator, "格式不正確")


def _unsafe_url_errors(data: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    def check(url: Any, path: str) -> None:
        if isinstance(url, str) and _BAD_SCHEME.match(_CTRL.sub("", url)):
            out.append({"path": path, "message": "網址只接受 http 或 https"})

    basics = data.get("basics")
    if isinstance(basics, dict) and isinstance(basics.get("links"), list):
        for i, link in enumerate(basics["links"]):
            if isinstance(link, dict):
                check(link.get("url"), f"basics.links[{i}].url")
    for section in ("projects", "portfolio"):
        items = data.get(section)
        if isinstance(items, list):
            for i, item in enumerate(items):
                if isinstance(item, dict):
                    check(item.get("url"), f"{section}[{i}].url")
    return out


def validate_profile(data: dict[str, Any]) -> list[dict[str, str]]:
    """回傳 [{path, message}, ...]；空列表代表通過。含 Schema 沒有的欄位一律回報（additionalProperties:false）。"""
    details: list[dict[str, str]] = []
    for err in _validator().iter_errors(data):
        base = list(err.absolute_path)
        if err.validator == "additionalProperties" and isinstance(err.instance, dict):
            known = err.schema.get("properties", {})
            for key in err.instance:
                if key not in known:
                    details.append({"path": _fmt(base + [key]), "message": "不允許這個欄位"})
        elif err.validator == "required" and isinstance(err.instance, dict):
            for key in err.validator_value:
                if key not in err.instance:
                    details.append({"path": _fmt(base + [key]), "message": _message("required")})
        else:
            details.append({"path": _fmt(base), "message": _message(err.validator)})
    details += _unsafe_url_errors(data)
    seen: set[tuple[str, str]] = set()
    uniq = []
    for d in sorted(details, key=lambda d: d["path"]):
        k = (d["path"], d["message"])
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    return uniq[:MAX_DETAILS]


def too_large(data: dict[str, Any]) -> bool:
    return len(json.dumps(data, ensure_ascii=False)) > MAX_BYTES
