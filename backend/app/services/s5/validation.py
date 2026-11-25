"""S5：自評與求職偏好（career_inputs）的驗證與整理。
path 沿用契約 §9 的 `section[index].field` 寫法；message 一律是固定中文，絕不含使用者輸入的值。
preferences 以 resume_profile.schema.json 的 job_preferences 子結構驗證（不另造一份規格）。"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "resume_profile.schema.json"  # app/schemas/

GOALS = ("continue", "switch", "undecided")
SOURCES = ("resume", "added")
TOP_KEYS = ("profile_id", "goal", "skill_ratings", "preferences")
RATING_KEYS = ("skill_id", "name", "rating", "source")

MAX_SKILLS = 30          # 總技能數上限
MAX_ADDED = 10           # 使用者自行加入的技能上限
MAX_ADDED_NAME = 60      # 自行加入的技能名稱長度
MAX_RESUME_NAME = 200    # 履歷技能名稱（Schema 沒有上限，這裡只防濫用；存檔時以履歷內名稱為準）
MAX_PREF_CHARS = 20_000
MAX_PREF_STR = 200       # preferences 內任一字串
MAX_PREF_LIST = 20       # preferences 內任一陣列
MAX_DETAILS = 50

_BAD_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # PostgreSQL jsonb 不接受 \u0000；其餘控制字元也沒有用途
_NAME_BAD = re.compile(r"[\x00-\x1f\x7f]")                     # 技能名稱：連換行、tab 都不收


@lru_cache(maxsize=1)
def _prefs_validator() -> Draft202012Validator:
    root = json.loads(SCHEMA_PATH.read_text(encoding="utf-8-sig"))
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$defs": root["$defs"],
              **root["properties"]["job_preferences"]}
    return Draft202012Validator(schema)


def _fmt(base: str, parts: list[Any]) -> str:
    out = base
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else ("." if out else "") + str(p)[:80]
    return out


def _message(validator: str) -> str:
    return {
        "type": "資料型別不正確",
        "enum": "不是允許的選項",
        "required": "此欄位為必填",
        "maxLength": "內容太長，請縮短",
        "maxItems": "項目太多",
    }.get(validator, "格式不正確")


def _norm_key(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip().casefold()


def profile_skill_map(profile: dict[str, Any] | None) -> dict[str, str]:
    """已確認履歷中的技能：{skill_id: name}。只讀 parsed_json.skills 的 id 與 name。"""
    skills = ((profile or {}).get("parsed_json") or {}).get("skills")
    out: dict[str, str] = {}
    if isinstance(skills, list):
        for s in skills:
            if isinstance(s, dict) and isinstance(s.get("id"), str) and isinstance(s.get("name"), str):
                out[s["id"]] = s["name"]
    return out


def _check_ratings(items: Any, skills: dict[str, str]) -> list[dict[str, str]]:
    d: list[dict[str, str]] = []
    if not isinstance(items, list):
        return [{"path": "skill_ratings", "message": _message("type")}] if items is not None else \
               [{"path": "skill_ratings", "message": _message("required")}]
    seen_names: set[str] = set()
    seen_ids: set[str] = set()
    added = 0
    for i, it in enumerate(items):
        p = f"skill_ratings[{i}]"
        if not isinstance(it, dict):
            d.append({"path": p, "message": _message("type")})
            continue
        for k in it:
            if k not in RATING_KEYS:
                d.append({"path": _fmt(p, [k]), "message": "不允許這個欄位"})
        source = it.get("source")
        if source not in SOURCES:
            d.append({"path": f"{p}.source", "message": _message("enum")})
        r = it.get("rating")
        if type(r) is not int or not 1 <= r <= 5:      # bool、3.0、"3" 一律拒絕
            d.append({"path": f"{p}.rating", "message": "自評請填 1 到 5 的整數"})
        name = it.get("name")
        final_name: str | None = None
        if not isinstance(name, str) or not name.strip():
            d.append({"path": f"{p}.name", "message": "技能名稱不可為空"})
        elif _NAME_BAD.search(name):
            d.append({"path": f"{p}.name", "message": "技能名稱含不允許的字元"})
        elif len(name.strip()) > (MAX_ADDED_NAME if source == "added" else MAX_RESUME_NAME):
            d.append({"path": f"{p}.name", "message": "內容太長，請縮短"})
        else:
            final_name = name.strip()
        sid = it.get("skill_id")
        if source == "resume":
            if not isinstance(sid, str) or sid not in skills:
                d.append({"path": f"{p}.skill_id", "message": "找不到這項履歷技能"})
            else:
                final_name = skills[sid]               # 存檔一律以履歷內的名稱為準
                if sid in seen_ids:
                    d.append({"path": f"{p}.skill_id", "message": "這項技能重複了"})
                seen_ids.add(sid)
        elif source == "added":
            added += 1
            if sid is not None:
                d.append({"path": f"{p}.skill_id", "message": "自行加入的技能不可帶技能代號"})
        if final_name is not None:
            key = _norm_key(final_name)
            if key in seen_names:
                d.append({"path": f"{p}.name", "message": "技能名稱重複了"})
            seen_names.add(key)
    if added > MAX_ADDED:
        d.append({"path": "skill_ratings", "message": f"自行加入的技能最多 {MAX_ADDED} 項"})
    if len(items) > MAX_SKILLS:
        d.append({"path": "skill_ratings", "message": f"技能最多 {MAX_SKILLS} 項，請用「不評」移除幾項"})
    return d


def _walk_limits(node: Any, path: str, out: list[dict[str, str]]) -> None:
    if isinstance(node, str):
        if len(node) > MAX_PREF_STR:
            out.append({"path": path, "message": "內容太長，請縮短"})
        elif _BAD_CHARS.search(node):
            out.append({"path": path, "message": "含不允許的字元"})
    elif isinstance(node, list):
        if len(node) > MAX_PREF_LIST:
            out.append({"path": path, "message": "項目太多"})
        for i, v in enumerate(node[:MAX_PREF_LIST * 2]):
            _walk_limits(v, f"{path}[{i}]", out)
    elif isinstance(node, dict):
        for k, v in node.items():
            _walk_limits(v, f"{path}.{str(k)[:80]}", out)


def _check_prefs(prefs: Any) -> list[dict[str, str]]:
    if prefs is None:
        return [{"path": "preferences", "message": _message("required")}]
    if not isinstance(prefs, dict):
        return [{"path": "preferences", "message": _message("type")}]
    d: list[dict[str, str]] = []
    try:
        too_big = len(json.dumps(prefs, ensure_ascii=False)) > MAX_PREF_CHARS
    except (TypeError, ValueError):
        too_big = True
    if too_big:
        return [{"path": "preferences", "message": "內容太大，請精簡"}]
    for err in _prefs_validator().iter_errors(prefs):
        base = list(err.absolute_path)
        if err.validator == "additionalProperties" and isinstance(err.instance, dict):
            known = err.schema.get("properties", {})
            for key in err.instance:
                if key not in known:
                    d.append({"path": _fmt("preferences", base + [key]), "message": "不允許這個欄位"})
        elif err.validator == "required" and isinstance(err.instance, dict):
            for key in err.validator_value:
                if key not in err.instance:
                    d.append({"path": _fmt("preferences", base + [key]), "message": _message("required")})
        else:
            d.append({"path": _fmt("preferences", base), "message": _message(err.validator)})
    _walk_limits(prefs, "preferences", d)
    return d


def validate_career_input(payload: dict[str, Any], profile: dict[str, Any] | None) -> list[dict[str, str]]:
    """回傳 [{path, message}]；空列表代表通過。profile 為 resume_profiles 的列（含 status、parsed_json）或 None。"""
    d: list[dict[str, str]] = []
    for k in payload:
        if k not in TOP_KEYS:
            d.append({"path": str(k)[:80], "message": "不允許這個欄位"})
    pid = payload.get("profile_id")
    if not isinstance(pid, str) or not pid:
        d.append({"path": "profile_id", "message": _message("required" if pid is None else "type")})
    elif profile is not None and profile.get("status") != "confirmed":
        d.append({"path": "profile_id", "message": "履歷尚未確認，請先回到確認頁完成確認"})
    if payload.get("goal") not in GOALS:
        d.append({"path": "goal", "message": _message("required" if payload.get("goal") is None else "enum")})
    d += _check_ratings(payload.get("skill_ratings"), profile_skill_map(profile))
    d += _check_prefs(payload.get("preferences"))
    seen: set[tuple[str, str]] = set()
    uniq = []
    for x in sorted(d, key=lambda x: (x["path"], x["message"])):
        k = (x["path"], x["message"])
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq[:MAX_DETAILS]


def normalize_ratings(items: list[dict[str, Any]], profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    """只在 validate_career_input 通過後呼叫。履歷技能的名稱以履歷內為準；只存 {skill_id,name,rating,source}。
    自評級別與履歷證據分開：證據不複製進 career_inputs，需要時從 profile 現查。"""
    skills = profile_skill_map(profile)
    out = []
    for it in items:
        if it["source"] == "resume":
            out.append({"skill_id": it["skill_id"], "name": skills[it["skill_id"]], "rating": it["rating"], "source": "resume"})
        else:
            out.append({"skill_id": None, "name": it["name"].strip(), "rating": it["rating"], "source": "added"})
    return out
