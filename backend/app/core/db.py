"""資料存取封裝（Supabase PostgREST / Storage，同步版）。

- `Db`（預設）：帶「使用者 JWT」呼叫，RLS 生效；所有查詢仍自動加上 user_id 條件（雙保險）。
- `AdminDb`：用 service key（繞過 RLS）。只用於刪檔、清理、用量計數；每個方法都必須傳 user_id 並套用條件。

RLS 擋掉的「讀取」會得到空列表，請當作 NOT_FOUND 處理（不洩漏資料是否存在）；
RLS 擋掉的「寫入」PostgREST 回 403，這裡轉成 FORBIDDEN。錯誤訊息一律不含回應本文。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Mapping

import httpx
from fastapi import Depends, Request

from app.core.auth import AuthContext, current_session
from app.core.config import Settings
from app.core.errors import AppError, ErrorCode

TABLES = frozenset(
    {"consent_records", "resume_files", "resume_profiles", "career_inputs", "analysis_runs", "usage_counters"}
)
RESUME_BUCKET = "resumes"

_COL_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_SELECT_RE = re.compile(r"^[A-Za-z0-9_,*()]+$")
_ORDER_RE = re.compile(r"^[a-z_][a-z0-9_]*\.(asc|desc)$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _uuid(value: Any, name: str) -> str:
    s = str(value).lower()
    if not _UUID_RE.match(s):
        raise ValueError(f"{name} 不是有效的 UUID")
    return s


def _table(name: str) -> str:
    if name not in TABLES:
        raise ValueError("未知的資料表")
    return name


def _filters(filters: Mapping[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (filters or {}).items():
        if not _COL_RE.match(key):
            raise ValueError("不合法的欄位名稱")
        if key == "user_id":
            raise ValueError("user_id 由 db 層自動加入，不可自行指定")
        if value is None:
            out[key] = "is.null"
        elif isinstance(value, bool):
            out[key] = f"eq.{str(value).lower()}"
        else:
            out[key] = f"eq.{value}"
    return out


def resume_storage_path(user_id: str, resume_file_id: str) -> str:
    """契約 §6：{user_id}/{resume_file_id}.pdf（也是 resume_files.storage_path 的值）。"""
    return f"{_uuid(user_id, 'user_id')}/{_uuid(resume_file_id, 'resume_file_id')}.pdf"


def _send(http: httpx.Client, method: str, url: str, *, storage: bool = False, **kwargs: Any) -> httpx.Response:
    try:
        return http.request(method, url, **kwargs)
    except httpx.HTTPError:
        if storage:
            raise AppError(ErrorCode.STORAGE_ERROR) from None
        raise AppError(ErrorCode.INTERNAL_ERROR, "資料服務暫時無法使用，請稍後再試。") from None


def _check_pg(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    s = resp.status_code
    if s == 401:
        raise AppError(ErrorCode.AUTH_FAILED, headers={"WWW-Authenticate": "Bearer"})
    if s == 403:
        raise AppError(ErrorCode.FORBIDDEN)
    if s in (400, 409, 422):
        raise AppError(ErrorCode.VALIDATION_ERROR)
    raise AppError(ErrorCode.INTERNAL_ERROR)


def _check_storage(resp: httpx.Response, *, ok_if_missing: bool = False) -> None:
    if resp.is_success:
        return
    if resp.status_code == 404:
        if ok_if_missing:
            return
        raise AppError(ErrorCode.NOT_FOUND)
    raise AppError(ErrorCode.STORAGE_ERROR)


class Db:
    """使用者身分的資料存取。由 `get_db` 依賴建立。"""

    def __init__(self, http: httpx.Client, settings: Settings, user_id: str, token: str) -> None:
        self._http = http
        self._anon = settings.supabase_anon_key
        self.user_id = _uuid(user_id, "user_id")
        self._token = token

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        h = {"apikey": self._anon, "Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        if prefer:
            h["Prefer"] = prefer
        return h

    def select(
        self,
        table: str,
        *,
        filters: Mapping[str, Any] | None = None,
        columns: str = "*",
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if not _SELECT_RE.match(columns) or (order and not _ORDER_RE.match(order)):
            raise ValueError("不合法的 select/order 參數")
        params: dict[str, str] = {"select": columns, "user_id": f"eq.{self.user_id}", **_filters(filters)}
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = str(int(limit))
        resp = _send(self._http, "GET", f"/rest/v1/{_table(table)}", headers=self._headers(), params=params)
        _check_pg(resp)
        return resp.json()

    def select_one(self, table: str, *, filters: Mapping[str, Any] | None = None, columns: str = "*",
                   order: str | None = None) -> dict[str, Any] | None:
        rows = self.select(table, filters=filters, columns=columns, order=order, limit=1)
        return rows[0] if rows else None

    def _rows_with_owner(self, rows: Mapping[str, Any] | list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        items = [rows] if isinstance(rows, Mapping) else list(rows)
        out = []
        for row in items:
            if "user_id" in row and str(row["user_id"]).lower() != self.user_id:
                raise ValueError("user_id 與目前使用者不符")
            out.append({**row, "user_id": self.user_id})
        return out

    def insert(self, table: str, rows: Mapping[str, Any] | list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        resp = _send(self._http, "POST", f"/rest/v1/{_table(table)}", headers=self._headers("return=representation"),
                     json=self._rows_with_owner(rows))
        _check_pg(resp)
        return resp.json()

    def upsert(self, table: str, row: Mapping[str, Any], *, on_conflict: str) -> list[dict[str, Any]]:
        if not all(_COL_RE.match(c) for c in on_conflict.split(",")):
            raise ValueError("不合法的 on_conflict")
        resp = _send(self._http, "POST", f"/rest/v1/{_table(table)}",
                     headers=self._headers("resolution=merge-duplicates,return=representation"),
                     params={"on_conflict": on_conflict}, json=self._rows_with_owner(row))
        _check_pg(resp)
        return resp.json()

    def update(self, table: str, values: Mapping[str, Any], *, filters: Mapping[str, Any]) -> list[dict[str, Any]]:
        """回傳被更新的列；空列表代表沒有符合的列（不存在或不是你的）→ 呼叫端請回 NOT_FOUND。"""
        if "user_id" in values:
            raise ValueError("不可更改 user_id")
        resp = _send(self._http, "PATCH", f"/rest/v1/{_table(table)}", headers=self._headers("return=representation"),
                     params={"user_id": f"eq.{self.user_id}", **_filters(filters)}, json=dict(values))
        _check_pg(resp)
        return resp.json()

    def delete(self, table: str, *, filters: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        resp = _send(self._http, "DELETE", f"/rest/v1/{_table(table)}", headers=self._headers("return=representation"),
                     params={"user_id": f"eq.{self.user_id}", **_filters(filters)})
        _check_pg(resp)
        return resp.json()


class AdminDb:
    """service key（繞過 RLS）。只用於管理操作；每個方法都要帶 user_id 並套用條件。"""

    def __init__(self, http: httpx.Client, settings: Settings) -> None:
        self._http = http
        self._url = settings.supabase_url
        self._key = settings.supabase_service_key

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        h = {"apikey": self._key, "Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        if prefer:
            h["Prefer"] = prefer
        return h

    # ---- 資料表 ----
    def select(self, table: str, user_id: str, *, filters: Mapping[str, Any] | None = None,
               columns: str = "*", limit: int | None = None) -> list[dict[str, Any]]:
        if not _SELECT_RE.match(columns):
            raise ValueError("不合法的 select 參數")
        params = {"select": columns, "user_id": f"eq.{_uuid(user_id, 'user_id')}", **_filters(filters)}
        if limit is not None:
            params["limit"] = str(int(limit))
        resp = _send(self._http, "GET", f"/rest/v1/{_table(table)}", headers=self._headers(), params=params)
        _check_pg(resp)
        return resp.json()

    def update(self, table: str, user_id: str, values: Mapping[str, Any], *,
               filters: Mapping[str, Any]) -> list[dict[str, Any]]:
        if "user_id" in values:
            raise ValueError("不可更改 user_id")
        resp = _send(self._http, "PATCH", f"/rest/v1/{_table(table)}", headers=self._headers("return=representation"),
                     params={"user_id": f"eq.{_uuid(user_id, 'user_id')}", **_filters(filters)}, json=dict(values))
        _check_pg(resp)
        return resp.json()

    def delete(self, table: str, user_id: str, *, filters: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """filters 省略 = 刪除該使用者在此表的全部列（例如「刪除我的資料」）。"""
        resp = _send(self._http, "DELETE", f"/rest/v1/{_table(table)}", headers=self._headers("return=representation"),
                     params={"user_id": f"eq.{_uuid(user_id, 'user_id')}", **_filters(filters)})
        _check_pg(resp)
        return resp.json()

    def consume_usage(self, user_id: str, day: date, kind: str, limit: int) -> int | None:
        """原子加 1；回傳新次數，None = 已達上限。"""
        resp = _send(self._http, "POST", "/rest/v1/rpc/consume_usage", headers=self._headers(),
                     json={"p_user_id": _uuid(user_id, "user_id"), "p_day": day.isoformat(),
                           "p_kind": kind, "p_limit": int(limit)})
        _check_pg(resp)
        value = resp.json() if resp.content.strip() else None  # 純量 NULL 可能是 null 或空本文
        return int(value) if value is not None else None

    # ---- Storage（私有 bucket resumes；路徑一律由 user_id + file_id 組成）----
    def _obj(self, user_id: str, resume_file_id: str) -> str:
        return f"/storage/v1/object/{RESUME_BUCKET}/{resume_storage_path(user_id, resume_file_id)}"

    def upload_resume_pdf(self, user_id: str, resume_file_id: str, data: bytes) -> None:
        resp = _send(self._http, "POST", self._obj(user_id, resume_file_id), storage=True,
                     headers={**self._headers(), "Content-Type": "application/pdf", "x-upsert": "false"}, content=data)
        _check_storage(resp)

    def download_resume_pdf(self, user_id: str, resume_file_id: str) -> bytes:
        resp = _send(self._http, "GET", self._obj(user_id, resume_file_id), storage=True, headers=self._headers())
        _check_storage(resp)
        return resp.content

    def delete_resume_pdf(self, user_id: str, resume_file_id: str) -> bool:
        """刪除原始 PDF。檔案本來就不存在也算成功（可重試）；失敗 raise STORAGE_ERROR，呼叫端須標 delete_failed。"""
        resp = _send(self._http, "DELETE", self._obj(user_id, resume_file_id), storage=True, headers=self._headers())
        _check_storage(resp, ok_if_missing=True)
        return True

    def list_user_files(self, user_id: str) -> list[str]:
        """列出 bucket 中 `{user_id}/` 底下所有物件的完整路徑（含資料表已無紀錄的孤兒檔）。"""
        uid = _uuid(user_id, "user_id")
        paths: list[str] = []
        offset = 0
        while True:
            resp = _send(self._http, "POST", f"/storage/v1/object/list/{RESUME_BUCKET}", storage=True,
                         headers=self._headers(), json={"prefix": uid, "limit": 100, "offset": offset})
            _check_storage(resp)
            items = resp.json()
            paths += [f"{uid}/{i['name']}" for i in items if i.get("name")]
            if len(items) < 100:
                return paths
            offset += 100

    def delete_user_files(self, user_id: str) -> int:
        """刪除 `{user_id}/` 底下全部物件（「刪除我的資料」用）。路徑一律來自該使用者前綴的列表；
        失敗 raise STORAGE_ERROR；沒有檔案也算成功。回傳刪除的檔案數。"""
        paths = self.list_user_files(user_id)
        for i in range(0, len(paths), 100):
            resp = _send(self._http, "DELETE", f"/storage/v1/object/{RESUME_BUCKET}", storage=True,
                         headers=self._headers(), json={"prefixes": paths[i:i + 100]})
            _check_storage(resp)
        return len(paths)

    def signed_resume_url(self, user_id: str, resume_file_id: str, expires_in: int = 60) -> str:
        path = resume_storage_path(user_id, resume_file_id)
        resp = _send(self._http, "POST", f"/storage/v1/object/sign/{RESUME_BUCKET}/{path}", storage=True,
                     headers=self._headers(), json={"expiresIn": int(expires_in)})
        _check_storage(resp)
        signed = resp.json().get("signedURL")
        if not signed:
            raise AppError(ErrorCode.STORAGE_ERROR)
        return f"{self._url}/storage/v1{signed}"


def get_db(request: Request, session: AuthContext = Depends(current_session)) -> Db:
    """FastAPI 依賴：使用者身分的 Db（RLS 生效）。"""
    return Db(request.app.state.http, request.app.state.settings, session.user_id, session.token)


def get_admin_db(request: Request) -> AdminDb:
    """FastAPI 依賴：service key 的 AdminDb。只在刪檔、清理等管理操作使用。"""
    return request.app.state.admin_db
