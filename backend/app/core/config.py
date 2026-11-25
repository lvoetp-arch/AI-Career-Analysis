"""環境變數載入與驗證。缺少必要變數時，錯誤訊息只列變數名稱，不顯示任何值。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Mapping


class ConfigError(Exception):
    pass


REQUIRED_COMMON = ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY")
# 依 LLM_PROVIDER 決定還要哪些變數（預設 gemini）
PROVIDER_VARS = {
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL"),
    "anthropic": ("ANTHROPIC_API_KEY", "CLAUDE_MODEL"),
}
_MODEL_RE = re.compile(r"^[A-Za-z0-9._\-]{1,100}$")


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_anon_key: str = field(repr=False)
    supabase_service_key: str = field(repr=False)
    llm_provider: str = "gemini"
    gemini_api_key: str | None = field(default=None, repr=False)
    gemini_model: str | None = None
    gemini_thinking_budget: int | None = None
    anthropic_api_key: str | None = field(default=None, repr=False)
    claude_model: str | None = None
    allowed_origins: tuple[str, ...] = ()
    rate_limit_parse_per_day: int = 5
    rate_limit_analysis_per_day: int = 10
    llm_timeout_seconds: float = 90.0
    llm_max_tokens: int = 8192


def _get(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None  # 空字串視為未設定（.env.example 複製後留空的情況）


def _number(env: Mapping[str, str], name: str, default, cast, minimum) -> float | int:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ConfigError(f"環境變數 {name} 必須是數字") from None
    if value < minimum:
        raise ConfigError(f"環境變數 {name} 不可小於 {minimum}")
    return value


def load_settings(env: Mapping[str, str] | None = None, *, load_dotenv_file: bool = False) -> Settings:
    if env is None:
        if load_dotenv_file:
            from dotenv import load_dotenv

            load_dotenv()  # 不覆蓋已存在的系統環境變數
        env = os.environ

    provider = (_get(env, "LLM_PROVIDER") or "gemini").lower()
    if provider not in PROVIDER_VARS:
        raise ConfigError("環境變數 LLM_PROVIDER 只能是 gemini 或 anthropic")

    missing = [name for name in (*REQUIRED_COMMON, *PROVIDER_VARS[provider]) if _get(env, name) is None]
    if missing:
        raise ConfigError(
            "缺少必要環境變數：" + "、".join(missing) + "。請複製 .env.example 為 .env 並填入。"
        )

    url = _get(env, "SUPABASE_URL")
    assert url is not None
    if not url.startswith(("http://", "https://")):
        raise ConfigError("環境變數 SUPABASE_URL 必須以 http:// 或 https:// 開頭")

    gemini_model = _get(env, "GEMINI_MODEL")
    if gemini_model and not _MODEL_RE.match(gemini_model):
        raise ConfigError("環境變數 GEMINI_MODEL 格式不正確（只能有英數字、點、底線、連字號）")

    origins: list[str] = []
    for item in (_get(env, "ALLOWED_ORIGINS") or "").split(","):
        item = item.strip().rstrip("/")
        if not item:
            continue
        if item == "*":
            raise ConfigError("環境變數 ALLOWED_ORIGINS 不可使用 *，請列出明確的網址")
        if not item.startswith(("http://", "https://")):
            raise ConfigError("環境變數 ALLOWED_ORIGINS 的每一項必須以 http:// 或 https:// 開頭")
        origins.append(item)

    return Settings(
        supabase_url=url.rstrip("/"),
        supabase_anon_key=_get(env, "SUPABASE_ANON_KEY"),  # type: ignore[arg-type]
        supabase_service_key=_get(env, "SUPABASE_SERVICE_KEY"),  # type: ignore[arg-type]
        llm_provider=provider,
        gemini_api_key=_get(env, "GEMINI_API_KEY"),
        gemini_model=gemini_model,
        gemini_thinking_budget=_number(env, "GEMINI_THINKING_BUDGET", None, int, -1),  # type: ignore[arg-type]
        anthropic_api_key=_get(env, "ANTHROPIC_API_KEY"),
        claude_model=_get(env, "CLAUDE_MODEL"),
        allowed_origins=tuple(origins),
        rate_limit_parse_per_day=int(_number(env, "RATE_LIMIT_PARSE_PER_DAY", 5, int, 0)),
        rate_limit_analysis_per_day=int(_number(env, "RATE_LIMIT_ANALYSIS_PER_DAY", 10, int, 0)),
        llm_timeout_seconds=float(_number(env, "LLM_TIMEOUT_SECONDS", 90.0, float, 1)),
        llm_max_tokens=int(_number(env, "LLM_MAX_TOKENS", 8192, int, 256)),
    )
