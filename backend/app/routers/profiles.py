"""S4：履歷資料讀取、編修、確認（契約 §8）。
- GET  /api/profiles/latest
- PUT  /api/profiles/{id}          送入的 parsed_json 必須通過 Schema 驗證；含 Schema 沒有的欄位（phone、gender…）一律拒絕
- POST /api/profiles/{id}/confirm  改 confirmed、寫 confirmed_at，並呼叫 S3 的 delete_original_file
日誌不記錄履歷內容、姓名。"""
import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends

from app.core.db import AdminDb, Db, get_admin_db, get_db
from app.core.errors import AppError, ErrorCode
from app.services.resume.storage import delete_original_file
from app.services.s4.profile_validation import too_large, validate_profile

log = logging.getLogger("s4.profiles")
router = APIRouter(prefix="/api", tags=["profiles"])  # 與 S3 相同：若 main.py 已統一加 /api 前綴，請拿掉這裡的 prefix
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid(profile_id: str) -> str:
    if not _UUID.match(profile_id):
        raise AppError(ErrorCode.NOT_FOUND)
    return profile_id.lower()


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    return {"profile_id": row["id"], "status": row["status"], "parsed_json": row["parsed_json"]}


@router.get("/profiles/latest")
def get_latest_profile(db: Db = Depends(get_db)):
    row = db.select_one("resume_profiles", columns="id,status,parsed_json", order="created_at.desc")
    if not row:
        raise AppError(ErrorCode.NOT_FOUND)
    return _shape(row)


@router.put("/profiles/{profile_id}")
def update_profile(profile_id: str, payload: dict[str, Any] = Body(...), db: Db = Depends(get_db)):
    pid = _pid(profile_id)
    details = [{"path": k, "message": "不允許這個欄位"} for k in payload if k != "parsed_json"]
    data = payload.get("parsed_json")
    if not isinstance(data, dict):
        details.append({"path": "parsed_json", "message": "此欄位為必填"})
    elif too_large(data):
        details.append({"path": "parsed_json", "message": "內容太大，請精簡後再儲存"})
    else:
        details += validate_profile(data)
    if details:
        raise AppError(ErrorCode.VALIDATION_ERROR, details=details[:50])
    rows = db.update("resume_profiles", {"parsed_json": data, "updated_at": _now()}, filters={"id": pid})
    if not rows:
        raise AppError(ErrorCode.NOT_FOUND)
    return _shape(rows[0])


def _delete_original(admin: AdminDb, user_id: str, file_id: str | None) -> bool:
    """回傳原始檔是否已刪除。失敗不丟例外：確認照樣完成，檔案狀態標 delete_failed（可重按確認重試）。"""
    if not file_id:
        return True  # 沒有原始檔紀錄 → 沒東西可刪
    try:
        rows = admin.select("resume_files", user_id, filters={"id": file_id}, columns="id,status", limit=1)
        if not rows or rows[0].get("status") == "deleted":
            return True
        return bool(delete_original_file(admin, user_id, file_id))
    except Exception:  # noqa: BLE001  只記型別，不記內容
        log.error("confirm_delete_unexpected")
        try:  # 盡力補標狀態；再失敗就算了（下次確認會重試）
            admin.update("resume_files", user_id, {"status": "delete_failed"}, filters={"id": file_id})
        except Exception:  # noqa: BLE001
            log.error("confirm_mark_failed_unexpected")
        return False


@router.post("/profiles/{profile_id}/confirm")
def confirm_profile(profile_id: str, db: Db = Depends(get_db), admin: AdminDb = Depends(get_admin_db)):
    pid = _pid(profile_id)
    rows = db.select("resume_profiles", filters={"id": pid}, columns="id,status,resume_file_id", limit=1)
    if not rows:
        raise AppError(ErrorCode.NOT_FOUND)
    row = rows[0]
    if row["status"] != "confirmed":  # 已確認者再按：不改 confirmed_at，只重試刪檔
        now = _now()
        if not db.update("resume_profiles", {"status": "confirmed", "confirmed_at": now, "updated_at": now},
                         filters={"id": pid}):
            raise AppError(ErrorCode.NOT_FOUND)
    return {"profile_id": pid, "status": "confirmed",
            "original_file_deleted": _delete_original(admin, db.user_id, row.get("resume_file_id"))}
