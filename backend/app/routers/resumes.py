"""POST /api/resumes（契約 §8）。順序：登入 → 同意 → 讀檔 → 本機驗證 → 存私有 bucket → 抽文字與遮蔽 → 扣額度 → LLM 解析 → 存 profile。
額度（契約 §4 #5）只在「確定要呼叫 LLM」之前才扣：非 PDF、過大、加密、過多頁、掃描檔等本機驗證不通過者不扣額度。
總時限（契約 §11）：整個解析流程共用 PARSE_TOTAL_SECONDS 秒，並以 deadline 傳給 llm.complete。
日誌不記錄履歷內容、姓名、LLM 輸入輸出。"""
import logging
import time
import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.core.db import AdminDb, Db, get_admin_db, get_db, resume_storage_path
from app.core.errors import AppError, ErrorCode
from app.core.llm import LLMClient, deadline_in, get_llm
from app.core.ratelimit import KIND_PARSE
from app.services.resume.pdf_text import MAX_BYTES
from app.services.resume.pipeline import run_parse
from app.services.resume.storage import discard_failed_original
from app.services.s2.privacy import consent_required

log = logging.getLogger("s3.resumes")
router = APIRouter(prefix="/api", tags=["resumes"])  # 若 main.py 已統一加 /api 前綴，請拿掉這裡的 prefix
SCHEMA_VERSION = "0.3"
PARSE_TOTAL_SECONDS = 90.0  # 整個解析流程（含 Schema 不符後的第二次呼叫）的總時限；前端逾時為 100 秒


@router.post("/resumes")
def upload_resume(
    request: Request,
    file: UploadFile = File(...),
    name_variants: str | None = Form(None),  # 只用來遮蔽，不儲存、不記錄
    user_id: str = Depends(consent_required),  # 先同意（額度改在要呼叫 LLM 前才扣，見 charge()）
    db: Db = Depends(get_db),
    admin: AdminDb = Depends(get_admin_db),
    llm: LLMClient = Depends(get_llm),
):
    deadline = deadline_in(PARSE_TOTAL_SECONDS)  # 之後所有 LLM 呼叫共用這一個時限
    data = file.file.read(MAX_BYTES + 1)
    rid = str(uuid.uuid4())
    path = resume_storage_path(user_id, rid)
    db.insert("resume_files", {"id": rid, "storage_path": path, "size_bytes": min(len(data), MAX_BYTES + 1), "status": "uploaded"})

    def fail(code: str, exc: Exception | None = None):
        discard_failed_original(admin, user_id, rid, code)
        raise exc if exc else AppError(ErrorCode(code))

    if len(data) > MAX_BYTES or not data.startswith(b"%PDF"):
        # 明顯不合格就不佔用 bucket
        admin.update("resume_files", user_id, {"status": "failed", "error_code": "FILE_INVALID"}, filters={"id": rid})
        raise AppError(ErrorCode.FILE_INVALID)

    try:
        admin.upload_resume_pdf(user_id, rid, data)
    except AppError as e:
        admin.update("resume_files", user_id, {"status": "failed", "error_code": e.code.value}, filters={"id": rid})
        raise
    admin.update("resume_files", user_id, {"status": "parsing"}, filters={"id": rid})

    def charge() -> None:  # 本機驗證都過了、即將呼叫 LLM 才扣額度
        request.app.state.rate_limiter.consume(user_id, KIND_PARSE)

    t0 = time.monotonic()
    try:
        outcome = run_parse(data, llm, name_variants, deadline=deadline, before_llm=charge)
    except AppError as e:  # 只會是 RATE_LIMITED：沒有扣到額度，也沒有呼叫 LLM；丟棄原檔、保留失敗紀錄
        log.info("parse_rate_limited")
        fail(e.code.value, e)
    except Exception:  # noqa: BLE001
        log.error("parse_unexpected")
        fail("INTERNAL_ERROR")
    log.info("parse_done ok=%s code=%s attempts=%s secs=%.1f", outcome.ok, outcome.error_code, outcome.attempts, time.monotonic() - t0)
    del data, name_variants  # 處理完即丟棄

    if not outcome.ok:
        fail(outcome.error_code, AppError(ErrorCode(outcome.error_code), outcome.user_message))

    try:
        rows = db.insert("resume_profiles", {"resume_file_id": rid, "schema_version": SCHEMA_VERSION,
                                             "parsed_json": outcome.profile, "status": "draft"})
        admin.update("resume_files", user_id, {"status": "parsed"}, filters={"id": rid})
    except AppError as e:
        fail(e.code.value, e)
    return {"resume_id": rid, "profile_id": rows[0]["id"], "parsed_json": outcome.profile, "notes": outcome.notes}
