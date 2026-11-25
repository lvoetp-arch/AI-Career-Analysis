import time

import anthropic
import httpx
import pytest

from app.core.llm import AnthropicLLMClient, FakeLLMClient, LLMError, LLMTimeoutError

PROMPT = "PROMPT_SENTINEL_履歷內容"
_REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


class _Block:
    def __init__(self, text, type="text"):
        self.text, self.type = text, type


class _Resp:
    def __init__(self, *blocks):
        self.content = list(blocks)


class _Messages:
    def __init__(self, outcomes):
        self.outcomes, self.kwargs = list(outcomes), []

    def create(self, **kw):
        self.kwargs.append(kw)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


class _Stub:
    def __init__(self, outcomes):
        self.messages = _Messages(outcomes)


def _client(outcomes, retries=1, sleeps=None):
    stub = _Stub(outcomes)
    sleep = sleeps.append if sleeps is not None else (lambda s: None)
    return AnthropicLLMClient("k", "model-x", client=stub, retries=retries, sleep=sleep, max_tokens=123), stub


def test_complete_uses_configured_model_and_returns_text():
    c, stub = _client([_Resp(_Block("hel"), _Block("lo"), _Block("x", type="thinking"))])
    assert c.complete("sys", "usr") == "hello"
    kw = stub.messages.kwargs[0]
    assert kw["model"] == "model-x" and kw["system"] == "sys" and kw["max_tokens"] == 123
    assert kw["messages"] == [{"role": "user", "content": "usr"}]


def test_timeout_maps_to_timeout_error_without_retry():
    c, stub = _client([anthropic.APITimeoutError(request=_REQ)])
    with pytest.raises(LLMTimeoutError) as e:
        c.complete("s", PROMPT)
    assert e.value.code.value == "LLM_ERROR" and len(stub.messages.kwargs) == 1
    assert PROMPT not in str(e.value) and PROMPT not in e.value.message


def test_transient_error_retried_once_then_succeeds():
    c, stub = _client([anthropic.APIConnectionError(request=_REQ), _Resp(_Block("ok"))])
    assert c.complete("s", "u") == "ok" and len(stub.messages.kwargs) == 2


def test_persistent_failure_is_llm_error_after_one_retry():
    c, stub = _client([anthropic.APIConnectionError(request=_REQ)] * 2)
    with pytest.raises(LLMError):
        c.complete("s", "u")
    assert len(stub.messages.kwargs) == 2


def test_unknown_exception_converted_without_content():
    c, _ = _client([ValueError(PROMPT)])
    with pytest.raises(LLMError) as e:
        c.complete("s", "u")
    assert PROMPT not in str(e.value) and e.value.__cause__ is None


def test_empty_output_is_llm_error():
    c, _ = _client([_Resp()])
    with pytest.raises(LLMError):
        c.complete("s", "u")


def test_client_does_not_log_inputs_or_outputs(caplog):
    c, _ = _client([_Resp(_Block(PROMPT))])
    with caplog.at_level("DEBUG"):
        c.complete(PROMPT, PROMPT)
    assert PROMPT not in caplog.text


def test_fake_client_is_injectable_and_can_fail():
    f = FakeLLMClient(["a", LLMError()])
    assert f.complete("s", "u") == "a"
    with pytest.raises(LLMError):
        f.complete("s", "u")
    assert len(f.calls) == 2


# ---- S2b 任務 B：Anthropic 路徑也遵守重試與總時限 ----
def _status_error(cls, code):
    return cls("x", response=httpx.Response(code, request=_REQ), body=None)


def test_rate_limit_retried_twice_with_backoff():
    sleeps = []
    c, stub = _client([_status_error(anthropic.RateLimitError, 429)] * 3, retries=2, sleeps=sleeps)
    with pytest.raises(LLMError):
        c.complete("s", PROMPT)
    assert len(stub.messages.kwargs) == 3 and sleeps == [1.0, 3.0]


def test_auth_error_not_retried():
    sleeps = []
    c, stub = _client([_status_error(anthropic.AuthenticationError, 401)], retries=2, sleeps=sleeps)
    with pytest.raises(LLMError):
        c.complete("s", PROMPT)
    assert len(stub.messages.kwargs) == 1 and sleeps == []


def test_expired_deadline_makes_no_call():
    c, stub = _client([_Resp(_Block("x"))])
    with pytest.raises(LLMTimeoutError):
        c.complete("s", "u", deadline=time.monotonic() - 1)
    assert stub.messages.kwargs == []


def test_request_timeout_follows_deadline():
    c, stub = _client([_Resp(_Block("x")), _Resp(_Block("y"))])
    c.complete("s", "u")
    c.complete("s", "u", deadline=time.monotonic() + 30)
    assert "timeout" not in stub.messages.kwargs[0]
    assert 29 < stub.messages.kwargs[1]["timeout"] <= 30
