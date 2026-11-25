import re
from pathlib import Path

import pytest

from app.core.config import ConfigError, load_settings

BASE = {
    "SUPABASE_URL": "https://abc.supabase.co/",
    "SUPABASE_ANON_KEY": "anon-value-1",
    "SUPABASE_SERVICE_KEY": "service-value-2",
    "GEMINI_API_KEY": "gemini-value-3",
    "GEMINI_MODEL": "test-model-1",
}


def test_loads_with_defaults():
    s = load_settings(BASE)
    assert s.supabase_url == "https://abc.supabase.co"
    assert (s.rate_limit_parse_per_day, s.rate_limit_analysis_per_day) == (5, 10)
    assert s.llm_timeout_seconds == 90 and s.allowed_origins == ()


def test_missing_vars_are_named():
    env = {k: v for k, v in BASE.items() if k not in ("SUPABASE_SERVICE_KEY", "GEMINI_MODEL")}
    with pytest.raises(ConfigError) as e:
        load_settings(env)
    msg = str(e.value)
    assert "SUPABASE_SERVICE_KEY" in msg and "GEMINI_MODEL" in msg and "GEMINI_API_KEY" not in msg


def test_blank_value_counts_as_missing():
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        load_settings({**BASE, "GEMINI_API_KEY": "  "})


def test_secrets_never_in_repr_or_errors():
    s = load_settings(BASE)
    text = repr(s)
    for secret in ("anon-value-1", "service-value-2", "gemini-value-3"):
        assert secret not in text
    with pytest.raises(ConfigError) as e:
        load_settings({**BASE, "RATE_LIMIT_PARSE_PER_DAY": "abc-secret"})
    assert "abc-secret" not in str(e.value) and "RATE_LIMIT_PARSE_PER_DAY" in str(e.value)


def test_overrides_and_origins():
    s = load_settings({**BASE, "RATE_LIMIT_PARSE_PER_DAY": "2", "RATE_LIMIT_ANALYSIS_PER_DAY": "3",
                       "ALLOWED_ORIGINS": "http://localhost:5500/, https://app.example.com"})
    assert (s.rate_limit_parse_per_day, s.rate_limit_analysis_per_day) == (2, 3)
    assert s.allowed_origins == ("http://localhost:5500", "https://app.example.com")


@pytest.mark.parametrize("extra", [{"ALLOWED_ORIGINS": "*"}, {"ALLOWED_ORIGINS": "localhost:5500"},
                                   {"SUPABASE_URL": "abc.supabase.co"}, {"RATE_LIMIT_PARSE_PER_DAY": "-1"},
                                   {"RATE_LIMIT_ANALYSIS_PER_DAY": "-1"}])
def test_invalid_values_rejected(extra):
    with pytest.raises(ConfigError):
        load_settings({**BASE, **extra})


def test_daily_limits_can_be_disabled():
    settings = load_settings({**BASE, "RATE_LIMIT_PARSE_PER_DAY": "0", "RATE_LIMIT_ANALYSIS_PER_DAY": "0"})
    assert (settings.rate_limit_parse_per_day, settings.rate_limit_analysis_per_day) == (0, 0)


def test_create_app_exits_with_variable_name(monkeypatch, tmp_path):
    from app.main import create_app

    for k in BASE:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)  # do not read the real .env
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with pytest.raises(SystemExit) as e:
        create_app()
    assert "SUPABASE_URL" in str(e.value)


def test_default_provider_is_gemini_and_anthropic_vars_not_required():
    s = load_settings(BASE)
    assert s.llm_provider == "gemini" and s.gemini_model == "test-model-1"
    assert s.anthropic_api_key is None and s.gemini_thinking_budget is None


def test_anthropic_provider_needs_its_own_vars_not_gemini_ones():
    env = {k: v for k, v in BASE.items() if not k.startswith("GEMINI")}
    with pytest.raises(ConfigError) as e:
        load_settings({**env, "LLM_PROVIDER": "anthropic"})
    assert "ANTHROPIC_API_KEY" in str(e.value) and "CLAUDE_MODEL" in str(e.value) and "GEMINI" not in str(e.value)
    s = load_settings({**env, "LLM_PROVIDER": "Anthropic", "ANTHROPIC_API_KEY": "k-value-9", "CLAUDE_MODEL": "m"})
    assert s.llm_provider == "anthropic" and "k-value-9" not in repr(s)


@pytest.mark.parametrize("extra", [{"LLM_PROVIDER": "openai"}, {"GEMINI_MODEL": "gemini 2.5/../x"},
                                   {"GEMINI_THINKING_BUDGET": "abc"}])
def test_invalid_llm_settings_rejected(extra):
    with pytest.raises(ConfigError):
        load_settings({**BASE, **extra})


def test_thinking_budget_parsed():
    assert load_settings({**BASE, "GEMINI_THINKING_BUDGET": "0"}).gemini_thinking_budget == 0


# ---- S2b 任務 B：模型名稱只來自環境變數 ----
ROOT = Path(__file__).resolve().parents[3]


def test_missing_model_names_the_variable_without_suggesting_a_default():
    env = {k: v for k, v in BASE.items() if k != "GEMINI_MODEL"}
    with pytest.raises(ConfigError) as e:
        load_settings(env)
    assert "GEMINI_MODEL" in str(e.value) and "gemini-" not in str(e.value)
    env = {k: v for k, v in BASE.items() if not k.startswith("GEMINI")}
    with pytest.raises(ConfigError) as e:
        load_settings({**env, "LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "k"})
    assert "CLAUDE_MODEL" in str(e.value)


def test_startup_exits_naming_missing_model(monkeypatch, tmp_path):
    from app.main import create_app

    for k in (*BASE, "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    for k, v in BASE.items():
        if k != "GEMINI_MODEL":
            monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    with pytest.raises(SystemExit) as e:
        create_app()
    assert "GEMINI_MODEL" in str(e.value)


def test_no_model_name_hardcoded_in_core():
    pat = re.compile(r"gemini-\d|claude-(?:opus|sonnet|haiku|\d)", re.I)
    for name in ("config.py", "llm.py"):
        text = (ROOT / "backend" / "app" / "core" / name).read_text(encoding="utf-8")
        assert not pat.search(text), name


def test_env_example_warns_about_model_expiry_and_single_key():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "404" in text and "一把" in text
    for line in text.splitlines():  # 範例檔不得替模型名稱或金鑰填值
        if line.startswith(("GEMINI_MODEL", "CLAUDE_MODEL", "GEMINI_API_KEY")):
            assert line.split("=", 1)[1].strip() == ""
