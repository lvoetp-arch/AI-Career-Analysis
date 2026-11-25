"""Supabase JWT 驗證。

做法：把 token 交給 Supabase Auth（GET /auth/v1/user）驗證，不在本地解 JWT。
好處是不必管簽章演算法（HS256 或新版非對稱金鑰）與金鑰輪替；代價是每個請求多一次往返。
無效 token 一律回 AUTH_FAILED；Supabase 暫時連不上則回 500（不會讓使用者被誤登出）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import AppError, ErrorCode

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_MAX_TOKEN_LEN = 8192
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    token: str = field(repr=False)


class TokenVerifier(Protocol):
    def verify(self, token: str) -> str:
        """回傳 user_id（UUID 字串）；無效則 raise AppError(AUTH_FAILED)。"""


class SupabaseTokenVerifier:
    def __init__(self, http: httpx.Client, anon_key: str) -> None:
        self._http = http
        self._anon_key = anon_key

    def verify(self, token: str) -> str:
        try:
            resp = self._http.get(
                "/auth/v1/user",
                headers={"apikey": self._anon_key, "Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError:
            raise AppError(ErrorCode.INTERNAL_ERROR, "登入驗證服務暫時無法使用，請稍後再試。") from None
        if resp.status_code in (401, 403):
            raise AppError(ErrorCode.AUTH_FAILED, headers={"WWW-Authenticate": "Bearer"})
        if resp.status_code != 200:
            raise AppError(ErrorCode.INTERNAL_ERROR, "登入驗證服務暫時無法使用，請稍後再試。")
        try:
            user_id = str(resp.json().get("id", "")).lower()
        except ValueError:
            user_id = ""
        if not _UUID_RE.match(user_id):
            raise AppError(ErrorCode.AUTH_FAILED, headers={"WWW-Authenticate": "Bearer"})
        return user_id


class FakeTokenVerifier:
    """測試用：tokens = {"token-a": "<uuid>"}。"""

    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = dict(tokens)

    def verify(self, token: str) -> str:
        try:
            return self._tokens[token]
        except KeyError:
            raise AppError(ErrorCode.AUTH_FAILED, headers={"WWW-Authenticate": "Bearer"}) from None


def current_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(ErrorCode.AUTH_FAILED, headers={"WWW-Authenticate": "Bearer"})
    token = credentials.credentials.strip()
    if not token or len(token) > _MAX_TOKEN_LEN:
        raise AppError(ErrorCode.AUTH_FAILED, headers={"WWW-Authenticate": "Bearer"})
    user_id = request.app.state.token_verifier.verify(token)
    return AuthContext(user_id=user_id, token=token)


def current_user(session: AuthContext = Depends(current_session)) -> str:
    """FastAPI 依賴：回傳已驗證的 user_id。"""
    return session.user_id
