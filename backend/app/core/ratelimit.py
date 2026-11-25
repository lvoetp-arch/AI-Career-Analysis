"""每人每日速率限制（以 usage_counters 為準，原子計數）。"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Protocol

from fastapi import Depends, Request

from app.core.auth import current_user
from app.core.config import Settings
from app.core.db import AdminDb
from app.core.errors import AppError, ErrorCode

KIND_PARSE = "parse"
KIND_ANALYSIS = "analysis"
_LABELS = {KIND_PARSE: "履歷上傳解析", KIND_ANALYSIS: "職涯分析"}
_TAIPEI = timezone(timedelta(hours=8))  # 台灣無日光節約時間，固定 UTC+8，避免 Windows 需要 tzdata


def today_taipei(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(_TAIPEI).date()


class UsageStore(Protocol):
    def consume(self, user_id: str, day: date, kind: str, limit: int) -> bool:
        """次數 +1 並回傳 True；已達上限則不加並回傳 False。"""


class SupabaseUsageStore:
    def __init__(self, admin: AdminDb) -> None:
        self._admin = admin

    def consume(self, user_id: str, day: date, kind: str, limit: int) -> bool:
        return self._admin.consume_usage(user_id, day, kind, limit) is not None


class InMemoryUsageStore:
    """測試用。"""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, date, str], int] = {}
        self._lock = threading.Lock()

    def consume(self, user_id: str, day: date, kind: str, limit: int) -> bool:
        with self._lock:
            key = (user_id, day, kind)
            if self._counts.get(key, 0) >= limit:
                return False
            self._counts[key] = self._counts.get(key, 0) + 1
            return True


class RateLimiter:
    def __init__(self, settings: Settings, store: UsageStore, clock: Callable[[], datetime] | None = None) -> None:
        self._limits = {
            KIND_PARSE: settings.rate_limit_parse_per_day,
            KIND_ANALYSIS: settings.rate_limit_analysis_per_day,
        }
        self._store = store
        self._clock = clock

    def limit_for(self, kind: str) -> int:
        return self._limits[kind]

    def consume(self, user_id: str, kind: str) -> None:
        """用掉一次額度；超過上限 raise RATE_LIMITED。"""
        limit = self._limits[kind]
        if limit == 0:
            return
        day = today_taipei(self._clock() if self._clock else None)
        if not self._store.consume(user_id, day, kind, limit):
            raise AppError(
                ErrorCode.RATE_LIMITED,
                f"今天的{_LABELS[kind]}次數已達上限（{limit} 次），請明天再試。",
            )


def rate_limit(kind: str) -> Callable[..., None]:
    """FastAPI 依賴工廠：`dependencies=[Depends(rate_limit("parse"))]`。

    依賴在進入 handler 前就會扣額度。若要先做其他檢查（例如 CONSENT_REQUIRED）再扣，
    請在 handler 內呼叫 `request.app.state.rate_limiter.consume(user_id, kind)`。
    """
    if kind not in _LABELS:
        raise ValueError("未知的速率限制類型")

    def _dependency(request: Request, user_id: str = Depends(current_user)) -> None:
        request.app.state.rate_limiter.consume(user_id, kind)

    return _dependency
