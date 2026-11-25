"""LLM 呼叫統一入口。

- `LLMClient.complete(system, user, *, deadline=None) -> str`
- 逾時可設；失敗統一轉成 LLM_ERROR（逾時為其子類 LLMTimeoutError，S6 可轉成 ANALYSIS_TIMEOUT）
- 重試：HTTP 429／503 以退避方式最多重試 2 次（等待約 1 秒、3 秒）；其他暫時性錯誤（500／502／504、連線失敗）
  維持只重試 1 次；401、404、400 等不重試；逾時不重試（否則會超過總時限）
- 總時限：`deadline` 是「絕對時間」，單位與 `time.monotonic()` 相同（用 `deadline_in(秒數)` 產生）。
  同一個流程的多次呼叫（例如 Schema 不符後的第二次呼叫）傳同一個 deadline，就會共用同一個時限：
  已過期就不發出請求（LLMTimeoutError）；每次請求的逾時不會超過剩餘時間；
  剩餘時間不足（等待後不到 MIN_RETRY_SECONDS）就不開始新的重試。不傳 deadline = 與舊版行為相同。
- 日誌只記供應商、HTTP 狀態碼與重試次數；不記錄輸入、輸出或供應商回傳的訊息內容。例外訊息不含任何內容
- 供應商由 LLM_PROVIDER 決定（預設 gemini）。模型名稱只來自 GEMINI_MODEL（或 anthropic 時的 CLAUDE_MODEL），
  程式不內建預設值（模型會過期）；未設定時啟動失敗並指出變數名稱
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Protocol

import httpx
from fastapi import Request

from app.core.config import ConfigError, Settings
from app.core.errors import AppError, ErrorCode

log = logging.getLogger("llm")  # 只記狀態碼與次數，不記內容

BACKOFF_SECONDS = (1.0, 3.0)  # 第 1、2 次重試前的等待
MIN_RETRY_SECONDS = 10.0  # 有 deadline 時：等待後剩餘時間不到這個秒數，就不開始新的重試
_RATE_OR_OVERLOAD = frozenset({429, 503})  # 這兩種可重試 2 次；其他暫時性錯誤只重試 1 次


class LLMError(AppError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.LLM_ERROR)


class LLMTimeoutError(LLMError):
    pass


class LLMOutputTruncatedError(LLMError):
    pass


class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, deadline: float | None = None) -> str: ...


def deadline_in(seconds: float, clock: Callable[[], float] = time.monotonic) -> float:
    """從現在起算 seconds 秒後的 deadline（絕對時間，與 time.monotonic() 同一個時鐘）。"""
    return clock() + seconds


def _ensure_time_left(deadline: float | None, clock: Callable[[], float], provider: str, retries: int) -> None:
    if deadline is not None and deadline - clock() <= 0:
        log.warning("llm_deadline_exceeded provider=%s retries=%d", provider, retries)
        raise LLMTimeoutError()


def _call_timeout(configured: float, deadline: float | None, clock: Callable[[], float]) -> float | None:
    """有 deadline 時，單次請求的逾時不超過剩餘時間；沒有 deadline 回 None（沿用客戶端原本的設定）。"""
    if deadline is None:
        return None
    return max(0.001, min(configured, deadline - clock()))


def _retry_delay(status: int | None, retries_done: int, max_retries: int,
                 deadline: float | None, clock: Callable[[], float]) -> float | None:
    """下一次重試前要等幾秒；不該重試時回 None。status=None 表示連線失敗。"""
    limit = max_retries if status in _RATE_OR_OVERLOAD else min(max_retries, 1)
    if retries_done >= limit:
        return None
    delay = BACKOFF_SECONDS[min(retries_done, len(BACKOFF_SECONDS) - 1)]
    if deadline is not None and deadline - clock() - delay < MIN_RETRY_SECONDS:
        return None  # 剩餘時間不足，不開始新的重試
    return delay


class AnthropicLLMClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 90.0,
        max_tokens: int = 8192,
        *,
        client: Any = None,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        import anthropic

        self._anthropic = anthropic
        self._client = client or anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds, max_retries=0)
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout_seconds
        self._retries = retries
        self._sleep = sleep
        self._clock = clock

    @classmethod
    def from_settings(cls, settings: Settings) -> "AnthropicLLMClient":
        if not settings.claude_model:
            raise ConfigError("缺少必要環境變數：CLAUDE_MODEL（模型名稱不內建預設值，請在 .env 設定）。")
        if not settings.anthropic_api_key:
            raise ConfigError("缺少必要環境變數：ANTHROPIC_API_KEY。")
        return cls(settings.anthropic_api_key, settings.claude_model,
                   settings.llm_timeout_seconds, settings.llm_max_tokens)

    def complete(self, system: str, user: str, *, deadline: float | None = None) -> str:
        a = self._anthropic
        transient = (a.RateLimitError, a.InternalServerError, a.APIConnectionError)
        _ensure_time_left(deadline, self._clock, "anthropic", 0)
        retries = 0
        while True:
            extra: dict[str, Any] = {}
            timeout = _call_timeout(self._timeout, deadline, self._clock)
            if timeout is not None:
                extra["timeout"] = timeout
            try:
                resp = self._client.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    **extra,
                )
                break
            except a.APITimeoutError:
                log.warning("llm_call_failed provider=anthropic status=timeout retries=%d", retries)
                raise LLMTimeoutError() from None
            except transient as exc:
                status = getattr(exc, "status_code", None)
                label = status if status is not None else "network"
                delay = _retry_delay(status, retries, self._retries, deadline, self._clock)
                if delay is None:
                    log.warning("llm_call_failed provider=anthropic status=%s retries=%d", label, retries)
                    raise LLMError() from None
                log.info("llm_retry provider=anthropic status=%s retry=%d", label, retries + 1)
                self._sleep(delay)
                retries += 1
            except Exception:  # noqa: BLE001 - 一律轉成 LLM_ERROR，不帶原始訊息
                log.warning("llm_call_failed provider=anthropic status=error retries=%d", retries)
                raise LLMError() from None
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
        if not text.strip():
            log.warning("llm_empty_response provider=anthropic retries=%d", retries)
            raise LLMError()
        return text


class GeminiLLMClient:
    """Google Gemini（REST generateContent）。金鑰放標頭，不放網址，避免被任何網址日誌記到。"""

    BASE_URL = "https://generativelanguage.googleapis.com"
    _TRANSIENT_STATUS = {429, 500, 502, 503, 504}

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 90.0,
        max_tokens: int = 8192,
        thinking_budget: int | None = None,
        *,
        http: httpx.Client | None = None,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._thinking_budget = thinking_budget
        self._timeout = timeout_seconds
        self._http = http or httpx.Client(base_url=self.BASE_URL, timeout=timeout_seconds)
        self._retries = retries
        self._sleep = sleep
        self._clock = clock

    @classmethod
    def from_settings(cls, settings: Settings) -> "GeminiLLMClient":
        if not settings.gemini_model:
            raise ConfigError("缺少必要環境變數：GEMINI_MODEL（模型名稱不內建預設值，請在 .env 設定）。")
        if not settings.gemini_api_key:
            raise ConfigError("缺少必要環境變數：GEMINI_API_KEY。")
        return cls(settings.gemini_api_key, settings.gemini_model, settings.llm_timeout_seconds,
                   settings.llm_max_tokens, settings.gemini_thinking_budget)

    def complete(
        self, system: str, user: str, *, deadline: float | None = None, json_output: bool = False,
        retry_truncated: bool = False,
    ) -> str:
        max_tokens = self._max_tokens
        if retry_truncated:
            max_tokens = max(max_tokens, min(16_384, max_tokens * 2))
        gen_config: dict[str, Any] = {"maxOutputTokens": max_tokens}
        if json_output:
            gen_config["responseMimeType"] = "application/json"
        if self._thinking_budget is not None:
            gen_config["thinkingConfig"] = {"thinkingBudget": self._thinking_budget}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen_config,
        }
        url = f"/v1beta/models/{self._model}:generateContent"
        _ensure_time_left(deadline, self._clock, "gemini", 0)
        retries = 0
        while True:
            extra: dict[str, Any] = {}
            timeout = _call_timeout(self._timeout, deadline, self._clock)
            if timeout is not None:
                extra["timeout"] = timeout
            resp = None
            try:
                resp = self._http.post(url, headers={"x-goog-api-key": self._key}, json=body, **extra)
            except httpx.TimeoutException:
                log.warning("llm_call_failed provider=gemini status=timeout retries=%d", retries)
                raise LLMTimeoutError() from None
            except httpx.TransportError:
                resp = None  # 連線失敗：視為暫時性錯誤
            except Exception:  # noqa: BLE001
                log.warning("llm_call_failed provider=gemini status=error retries=%d", retries)
                raise LLMError() from None
            status = resp.status_code if resp is not None else None
            if resp is not None and status not in self._TRANSIENT_STATUS:
                break
            label = status if status is not None else "network"
            delay = _retry_delay(status, retries, self._retries, deadline, self._clock)
            if delay is None:
                log.warning("llm_call_failed provider=gemini status=%s retries=%d", label, retries)
                raise LLMError()
            log.info("llm_retry provider=gemini status=%s retry=%d", label, retries + 1)
            self._sleep(delay)
            retries += 1
        if not resp.is_success:
            log.warning("llm_call_failed provider=gemini status=%d retries=%d", resp.status_code, retries)
            raise LLMError()
        try:
            candidate = resp.json()["candidates"][0]
            if candidate.get("finishReason") == "MAX_TOKENS":
                log.warning("llm_output_truncated provider=gemini reason=MAX_TOKENS max_output_tokens=%d", max_tokens)
                if json_output:
                    raise LLMOutputTruncatedError()
            parts = candidate["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            log.warning("llm_empty_response provider=gemini retries=%d", retries)
            raise LLMError() from None  # 含被安全機制擋下、沒有候選內容的情況
        if not text.strip():
            log.warning("llm_empty_response provider=gemini retries=%d", retries)
            raise LLMError()
        return text


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "anthropic":
        return AnthropicLLMClient.from_settings(settings)
    return GeminiLLMClient.from_settings(settings)


class FakeLLMClient:
    """測試用：依序回傳預先設定的字串；放入 Exception 實例則會 raise。
    `calls` 記錄 (system, user)；`deadlines` 記錄每次呼叫收到的 deadline（沒傳為 None）。"""

    def __init__(self, responses: list[str | Exception] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[tuple[str, str]] = []
        self.deadlines: list[float | None] = []

    def complete(self, system: str, user: str, *, deadline: float | None = None) -> str:
        self.calls.append((system, user))
        self.deadlines.append(deadline)
        if not self._responses:
            raise LLMError()
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def get_llm(request: Request) -> LLMClient:
    """FastAPI 依賴：`llm: LLMClient = Depends(get_llm)`。"""
    return request.app.state.llm
