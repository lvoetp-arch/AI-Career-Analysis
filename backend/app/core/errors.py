"""統一錯誤格式與錯誤碼（契約 §9）。回應中絕不放入使用者輸入或內部細節。"""
from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorCode(str, Enum):
    AUTH_FAILED = "AUTH_FAILED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONSENT_REQUIRED = "CONSENT_REQUIRED"
    FILE_INVALID = "FILE_INVALID"
    SCANNED_OR_EMPTY = "SCANNED_OR_EMPTY"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    LLM_ERROR = "LLM_ERROR"
    ANALYSIS_TIMEOUT = "ANALYSIS_TIMEOUT"
    ANALYSIS_INVALID = "ANALYSIS_INVALID"
    RATE_LIMITED = "RATE_LIMITED"
    STORAGE_ERROR = "STORAGE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"  # 契約 §9 沒有；未預期例外用（契約變更請求，見交接摘要）


HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.AUTH_FAILED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONSENT_REQUIRED: 403,
    ErrorCode.FILE_INVALID: 400,
    ErrorCode.SCANNED_OR_EMPTY: 422,
    ErrorCode.SCHEMA_INVALID: 502,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.LLM_ERROR: 502,
    ErrorCode.ANALYSIS_TIMEOUT: 504,
    ErrorCode.ANALYSIS_INVALID: 502,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.STORAGE_ERROR: 500,
    ErrorCode.INTERNAL_ERROR: 500,
}

DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.AUTH_FAILED: "登入已失效或帳號密碼不正確，請重新登入。",
    ErrorCode.FORBIDDEN: "你沒有權限存取這項資料。",
    ErrorCode.NOT_FOUND: "找不到指定的資料。",
    ErrorCode.CONSENT_REQUIRED: "請先閱讀並同意隱私說明，才能繼續。",
    ErrorCode.FILE_INVALID: "檔案無法使用，請確認是未加密、大小與頁數在限制內的 PDF。",
    ErrorCode.SCANNED_OR_EMPTY: "這份 PDF 讀不到文字，可能是掃描檔或圖片檔，請改用可選取文字的 PDF。",
    ErrorCode.SCHEMA_INVALID: "履歷解析結果格式不正確，請稍後再試一次。",
    ErrorCode.VALIDATION_ERROR: "輸入的資料格式不正確，請檢查標示的欄位。",
    ErrorCode.LLM_ERROR: "AI 服務暫時無法使用，請稍後再試。",
    ErrorCode.ANALYSIS_TIMEOUT: "分析花的時間太長，請稍後重試，你填寫的資料都還在。",
    ErrorCode.ANALYSIS_INVALID: "分析結果未通過檢查，請重試一次。",
    ErrorCode.RATE_LIMITED: "今天的使用次數已達上限，請明天再試。",
    ErrorCode.STORAGE_ERROR: "檔案處理失敗，請稍後再試。",
    ErrorCode.INTERNAL_ERROR: "系統發生錯誤，請稍後再試。",
}


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        details: list[dict[str, str]] | None = None,
        *,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.code = code
        self.message = message or DEFAULT_MESSAGES[code]
        self.details = details
        self.status_code = status_code or HTTP_STATUS[code]
        self.headers = headers
        super().__init__(code.value)  # str(exc) 只含錯誤碼，避免日誌或堆疊洩漏內容


def error_body(code: ErrorCode, message: str, details: list[dict[str, str]] | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code.value, "message": message}
    if details:
        err["details"] = details
    return {"error": err}


def error_response(
    code: ErrorCode,
    message: str | None = None,
    details: list[dict[str, str]] | None = None,
    *,
    status_code: int | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        error_body(code, message or DEFAULT_MESSAGES[code], details),
        status_code=status_code or HTTP_STATUS[code],
        headers=headers,
    )


def internal_error_response() -> JSONResponse:
    return error_response(ErrorCode.INTERNAL_ERROR)


def _validation_details(exc: RequestValidationError) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for err in exc.errors()[:50]:
        path = ".".join(str(p) for p in err.get("loc", ()) if p != "body") or "body"
        message = "此欄位為必填" if err.get("type") == "missing" else "格式不正確"
        out.append({"path": path, "message": message})  # 不回傳 input，避免回顯使用者內容
    return out


_HTTP_TO_CODE = {
    401: ErrorCode.AUTH_FAILED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
}


def register_exception_handlers(app: FastAPI) -> None:
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request.state.error_code = exc.code.value
        return error_response(exc.code, exc.message, exc.details, status_code=exc.status_code, headers=exc.headers)

    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        request.state.error_code = ErrorCode.VALIDATION_ERROR.value
        return error_response(ErrorCode.VALIDATION_ERROR, details=_validation_details(exc))

    async def http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        status = exc.status_code
        code = _HTTP_TO_CODE.get(status)
        message = None
        if code is None:
            code = ErrorCode.VALIDATION_ERROR if 400 <= status < 500 else ErrorCode.INTERNAL_ERROR
            message = "請求格式不正確。" if code is ErrorCode.VALIDATION_ERROR else None
        request.state.error_code = code.value
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        return error_response(code, message, status_code=status, headers=headers)

    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_handler)  # type: ignore[arg-type]
    # 未預期例外（Exception）由 logging.RequestContextMiddleware 攔截並回 internal_error_response()，
    # 這樣框架不會把含內容的堆疊印到日誌。
