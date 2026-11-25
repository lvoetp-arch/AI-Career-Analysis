"""S3 對外提供的檔案處理：確認後刪原檔（給 S4 呼叫）、解析失敗時丟棄原檔。日誌只記錯誤型別。"""
import logging
from datetime import datetime, timezone

from app.core.errors import AppError

log = logging.getLogger("s3.storage")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def delete_original_file(admin, user_id: str, resume_file_id: str) -> bool:
    """刪除原始 PDF 並標 deleted；刪檔失敗標 delete_failed（不可靜默忽略），回傳 False。
    admin 為 core.db.AdminDb。S4 在 confirm 時呼叫。"""
    try:
        admin.delete_resume_pdf(user_id, resume_file_id)
    except AppError:
        log.error("delete_original_failed")
        admin.update("resume_files", user_id, {"status": "delete_failed"}, filters={"id": resume_file_id})
        return False
    admin.update("resume_files", user_id, {"status": "deleted", "deleted_at": _now()}, filters={"id": resume_file_id})
    return True


def discard_failed_original(admin, user_id: str, resume_file_id: str, error_code: str) -> None:
    """解析失敗：標 failed 並盡力刪原檔；刪不掉改標 delete_failed（錯誤碼仍保留）。"""
    status = "failed"
    try:
        admin.delete_resume_pdf(user_id, resume_file_id)
    except AppError:
        log.error("discard_original_failed")
        status = "delete_failed"
    try:
        admin.update("resume_files", user_id, {"status": status, "error_code": error_code}, filters={"id": resume_file_id})
    except AppError:
        log.error("status_update_failed")
