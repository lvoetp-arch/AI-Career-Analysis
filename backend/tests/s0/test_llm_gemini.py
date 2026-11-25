import json
import logging

import httpx
import pytest

from app.core.config import ConfigError, Settings
from app.core.llm import (AnthropicLLMClient, FakeLLMClient, GeminiLLMClient, LLMError, LLMTimeoutError,
                          build_llm_client, deadline_in)

PROMPT = "PROMPT_SENTINEL_履歷內容"


MODEL = "test-model-1"  # 測試用假名稱；程式不內建任何預設模型


class Clock:
    """假時鐘：sleep 只記錄等待秒數並推進時間，測試不用真的等。"""

    def __init__(self, t=1000.0):
        self.t, self.sleeps = t, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def _client(handler, clock=None, **kw):
    http = httpx.Client(base_url="https://generativelanguage.googleapis.com", timeout=90,
                        transport=httpx.MockTransport(handler))
    if clock is not None:
        kw.update(sleep=clock.sleep, clock=clock)
    else:
        kw.setdefault("sleep", lambda s: None)
    return GeminiLLMClient("test-gemini-key", MODEL, 90, 123, http=http, **kw)


def _ok(text="hello"):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def test_request_shape_key_in_header_not_url():
    seen = {}

    def h(req):
        seen["url"], seen["key"], seen["body"] = str(req.url), req.headers.get("x-goog-api-key"), json.loads(req.content)
        return _ok("hi")

    assert _client(h).complete("SYS", "USR") == "hi"
    assert seen["url"].endswith(f"/v1beta/models/{MODEL}:generateContent")
    assert "test-gemini-key" not in seen["url"] and seen["key"] == "test-gemini-key"
    b = seen["body"]
    assert b["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert b["contents"] == [{"role": "user", "parts": [{"text": "USR"}]}]
    assert b["generationConfig"] == {"maxOutputTokens": 123}


def test_thinking_budget_sent_only_when_set():
    bodies = []

    def h(req):
        bodies.append(json.loads(req.content))
        return _ok()

    _client(h, thinking_budget=0).complete("s", "u")
    assert bodies[0]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


def test_joins_text_parts_and_skips_thought_parts():
    resp = httpx.Response(200, json={"candidates": [{"content": {"parts": [
        {"text": "secret reasoning", "thought": True}, {"text": "a"}, {"text": "b"}]}}]})
    assert _client(lambda r: resp).complete("s", "u") == "ab"


def test_transient_status_retried_once_then_succeeds():
    codes = iter([429, 200])
    calls = []

    def h(req):
        calls.append(1)
        return _ok("ok") if next(codes) == 200 else httpx.Response(429, json={"error": PROMPT})

    assert _client(h).complete("s", "u") == "ok" and len(calls) == 2


def test_persistent_503_is_llm_error_after_two_retries():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(LLMError):
        _client(h).complete("s", "u")
    assert len(calls) == 3  # 第一次 + 重試 2 次


def test_network_error_retried_then_llm_error():
    def h(req):
        raise httpx.ConnectError("down")

    with pytest.raises(LLMError):
        _client(h).complete("s", PROMPT)


def test_timeout_maps_to_timeout_error_without_retry():
    calls = []

    def h(req):
        calls.append(1)
        raise httpx.ReadTimeout("slow")

    with pytest.raises(LLMTimeoutError) as e:
        _client(h).complete("s", PROMPT)
    assert len(calls) == 1 and e.value.code.value == "LLM_ERROR"


def test_client_error_not_retried_and_body_not_leaked():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": PROMPT}})

    with pytest.raises(LLMError) as e:
        _client(h).complete("s", PROMPT)
    assert len(calls) == 1 and PROMPT not in str(e.value) and PROMPT not in e.value.message


@pytest.mark.parametrize("payload", [{}, {"candidates": []}, {"candidates": [{"finishReason": "SAFETY"}]},
                                     {"candidates": [{"content": {"parts": [{"text": "  "}]}}]}])
def test_blocked_or_empty_responses_are_llm_error(payload):
    with pytest.raises(LLMError):
        _client(lambda r: httpx.Response(200, json=payload)).complete("s", "u")


def test_does_not_log_inputs_or_outputs(caplog):
    with caplog.at_level("DEBUG"):
        _client(lambda r: _ok(PROMPT)).complete(PROMPT, PROMPT)
    assert PROMPT not in caplog.text


def test_build_llm_client_selects_provider():
    base = dict(supabase_url="https://x.supabase.co", supabase_anon_key="a", supabase_service_key="b")
    g = build_llm_client(Settings(**base, gemini_api_key="k", gemini_model="m"))
    assert isinstance(g, GeminiLLMClient)
    a = build_llm_client(Settings(**base, llm_provider="anthropic", anthropic_api_key="k", claude_model="m"))
    assert isinstance(a, AnthropicLLMClient)


# ======== S2b 任務 B：重試、總時限、日誌、模型名稱 ========
def _script(codes, seen=None):
    """依序回傳指定狀態碼（200 = 成功）；呼叫次數超過預期就讓測試失敗。"""
    it = iter(codes)

    def h(req):
        if seen is not None:
            seen.append(req)
        try:
            code = next(it)
        except StopIteration:
            raise AssertionError("呼叫次數超過預期")
        return _ok("ok") if code == 200 else httpx.Response(code, json={"error": {"message": PROMPT}})

    return h


@pytest.mark.parametrize("status", [503, 429])
def test_overload_and_rate_limit_retry_twice_with_backoff(status):
    clock, seen = Clock(), []
    with pytest.raises(LLMError):
        _client(_script([status] * 3, seen), clock).complete("s", "u")
    assert len(seen) == 3 and clock.sleeps == [1.0, 3.0]


def test_retry_then_success_uses_backoff_schedule():
    clock, seen = Clock(), []
    assert _client(_script([429, 503, 200], seen), clock).complete("s", "u") == "ok"
    assert len(seen) == 3 and clock.sleeps == [1.0, 3.0]
    clock2, seen2 = Clock(), []
    assert _client(_script([503, 200], seen2), clock2).complete("s", "u") == "ok"
    assert len(seen2) == 2 and clock2.sleeps == [1.0]


@pytest.mark.parametrize("status", [401, 403, 404, 400])
def test_auth_and_not_found_are_never_retried(status, caplog):
    clock, seen = Clock(), []
    with caplog.at_level(logging.DEBUG), pytest.raises(LLMError) as e:
        _client(_script([status], seen), clock).complete(PROMPT, PROMPT)
    assert len(seen) == 1 and clock.sleeps == []
    assert e.value.code.value == "LLM_ERROR" and PROMPT not in str(e.value) and PROMPT not in e.value.message
    assert f"status={status}" in caplog.text and "retries=0" in caplog.text


@pytest.mark.parametrize("status", [500, 502, 504])
def test_other_server_errors_keep_single_retry(status):
    clock, seen = Clock(), []
    with pytest.raises(LLMError):
        _client(_script([status, status], seen), clock).complete("s", "u")
    assert len(seen) == 2 and clock.sleeps == [1.0]


def test_failure_log_has_only_status_and_retry_count(caplog):
    clock = Clock()
    with caplog.at_level(logging.DEBUG), pytest.raises(LLMError):
        _client(_script([503, 503, 503]), clock).complete(PROMPT, PROMPT)
    assert "status=503" in caplog.text and "retries=2" in caplog.text
    assert PROMPT not in caplog.text and "test-gemini-key" not in caplog.text


# ---- 總時限 ----
def test_expired_deadline_makes_no_request():
    clock, seen = Clock(), []
    with pytest.raises(LLMTimeoutError) as e:
        _client(_script([200], seen), clock).complete("s", "u", deadline=clock.t - 1)
    assert seen == [] and e.value.code.value == "LLM_ERROR"


def test_no_new_retry_when_remaining_time_is_too_short():
    # 剩 10 秒：第一次失敗後再等 1 秒就只剩 9 秒（< 10），不重試
    clock, seen = Clock(), []
    with pytest.raises(LLMError):
        _client(_script([503], seen), clock).complete("s", "u", deadline=clock.t + 10)
    assert len(seen) == 1 and clock.sleeps == []
    # 剩 12 秒：可以做第 1 次重試（等 1 秒、剩 11 秒），第 2 次重試要再等 3 秒只剩 8 秒，不做
    clock2, seen2 = Clock(), []
    with pytest.raises(LLMError):
        _client(_script([503, 503], seen2), clock2).complete("s", "u", deadline=clock2.t + 12)
    assert len(seen2) == 2 and clock2.sleeps == [1.0]


def test_plenty_of_time_allows_all_retries():
    clock, seen = Clock(), []
    with pytest.raises(LLMError):
        _client(_script([503] * 3, seen), clock).complete("s", "u", deadline=clock.t + 90)
    assert len(seen) == 3 and clock.sleeps == [1.0, 3.0]


def test_request_timeout_never_exceeds_remaining_time():
    seen = []

    def h(req):
        seen.append(req.extensions["timeout"]["read"])
        return _ok()

    clock = Clock()
    _client(h, clock).complete("s", "u")  # 不傳 deadline：沿用客戶端原本的逾時
    _client(h, clock).complete("s", "u", deadline=clock.t + 30)
    _client(h, clock).complete("s", "u", deadline=clock.t + 500)
    assert seen == [90, 30, 90]


def test_deadline_in_is_absolute_monotonic_time():
    t = Clock(50.0)
    assert deadline_in(90, t) == 140.0


def test_deadline_is_optional_and_backward_compatible():
    assert _client(lambda r: _ok("same")).complete("s", "u") == "same"


# ---- 模型名稱不寫死 ----
def test_missing_model_fails_with_variable_name():
    base = dict(supabase_url="https://x.supabase.co", supabase_anon_key="a", supabase_service_key="b")
    with pytest.raises(ConfigError, match="GEMINI_MODEL"):
        build_llm_client(Settings(**base, gemini_api_key="k"))
    with pytest.raises(ConfigError, match="CLAUDE_MODEL"):
        build_llm_client(Settings(**base, llm_provider="anthropic", anthropic_api_key="k"))


def test_fake_client_records_deadline():
    f = FakeLLMClient(["a", "b"])
    f.complete("s", "u")
    f.complete("s", "u", deadline=5.0)
    assert f.deadlines == [None, 5.0]
