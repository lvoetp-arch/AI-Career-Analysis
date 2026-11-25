"""存取日誌：只允許記錄請求 ID、路徑、狀態碼、耗時、錯誤碼（契約 §10-3）。

不記錄：查詢字串、標頭、請求/回應本文、例外訊息與堆疊。
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import ErrorCode, internal_error_response

ACCESS_LOGGER_NAME = "career.access"
ALLOWED_FIELDS = ("request_id", "path", "status", "duration_ms", "error_code")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
_VALID_CODES = {c.value for c in ErrorCode}


def get_access_logger() -> logging.Logger:
    return logging.getLogger(ACCESS_LOGGER_NAME)


class JsonFormatter(logging.Formatter):
    """只輸出白名單欄位；就算有人多塞 extra 或訊息，也不會被印出。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": "request",
        }
        for name in ALLOWED_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    logger = get_access_logger()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(getattr(h, "_career_handler", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        handler._career_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    # 第三方套件的請求日誌會含完整網址（含使用者 id 與查詢條件），一律壓低；uvicorn 的存取日誌含查詢字串，關掉。
    for name in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True


def _safe_path(path: str) -> str:
    return re.sub(r"[^\x21-\x7e]", "?", path)[:200]


def log_request(*, request_id: str, path: str, status: int, duration_ms: float, error_code: str | None = None) -> None:
    extra: dict[str, Any] = {
        "request_id": request_id,
        "path": _safe_path(path),
        "status": int(status),
        "duration_ms": round(float(duration_ms), 1),
    }
    if error_code in _VALID_CODES:
        extra["error_code"] = error_code
    get_access_logger().info("request", extra=extra)


class RequestContextMiddleware:
    """請求 ID、存取日誌、安全標頭，並攔截未預期例外（回通用 500，不洩漏細節）。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._request_id(scope)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        path = scope.get("path", "")
        started = False
        status = 500
        t0 = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal started, status
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Frame-Options"] = "DENY"
                if path.startswith("/api/"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:  # noqa: BLE001 - 刻意不記錄例外內容
            status = 500
            state["error_code"] = ErrorCode.INTERNAL_ERROR.value
            if not started:
                await internal_error_response()(scope, receive, send_wrapper)
        finally:
            log_request(
                request_id=request_id,
                path=path,
                status=status,
                duration_ms=(time.perf_counter() - t0) * 1000,
                error_code=state.get("error_code"),
            )

    @staticmethod
    def _request_id(scope: Scope) -> str:
        for key, value in scope.get("headers", []):
            if key == b"x-request-id":
                candidate = value.decode("latin-1")
                if _REQUEST_ID_RE.match(candidate):
                    return candidate
        return str(uuid.uuid4())
