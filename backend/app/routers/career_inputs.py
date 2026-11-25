"""S5：技能自評與求職偏好（契約 §8）。
- PUT /api/career-inputs  `{profile_id, goal, skill_ratings[], preferences}` → `{career_input_id}`
一個 profile 對應一份 career_input，重複送出為更新。
career_inputs 表目前沒有 (resume_profile_id) 唯一約束，所以這裡用「先查再寫＋寫後去重」維持一份；
建議主 session 補唯一索引（見 docs/handoff/S5.md）。日誌不記錄姓名、技能名稱或偏好內容。"""
import logging
import re
from typing import Any

from fastapi import APIRouter, Body, Depends

from app.core.db import Db, get_db
from app.core.errors import AppError, ErrorCode
from app.services.s5.validation import normalize_ratings, validate_career_input

log = logging.getLogger("s5.career_inputs")
router = APIRouter(prefix="/api", tags=["career-inputs"])  # 與 S3／S4 相同寫法
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def _persist(db: Db, profile_id: str, values: dict[str, Any]) -> str:
    def existing() -> list[dict[str, Any]]:
        return db.select("career_inputs", filters={"resume_profile_id": profile_id}, columns="id", order="created_at.asc")

    rows = existing()
    if rows:
        keep = rows[0]["id"]
        if not db.update("career_inputs", values, filters={"id": keep}):
            raise AppError(ErrorCode.NOT_FOUND)
    else:
        mine = db.insert("career_inputs", {"resume_profile_id": profile_id, **values})[0]["id"]
        # 雙擊或兩個分頁同時送出：寫入後若出現多列，保留最早的一列並套用本次內容
        rows = existing()
        keep = rows[0]["id"] if rows else mine
        if keep != mine:
            db.update("career_inputs", values, filters={"id": keep})
    for extra in rows[1:]:
        db.delete("career_inputs", filters={"id": extra["id"]})
    return keep


@router.put("/career-inputs")
def put_career_input(payload: dict[str, Any] = Body(...), db: Db = Depends(get_db)):
    pid = payload.get("profile_id")
    profile = None
    if isinstance(pid, str) and pid:
        if not _UUID.match(pid):
            raise AppError(ErrorCode.NOT_FOUND)
        pid = pid.lower()
        profile = db.select_one("resume_profiles", filters={"id": pid}, columns="id,status,parsed_json")
        if not profile:                       # 不存在或不是本人的（RLS＋user_id 過濾）
            raise AppError(ErrorCode.NOT_FOUND)
    details = validate_career_input(payload, profile)
    if details:
        raise AppError(ErrorCode.VALIDATION_ERROR, details=details)
    values = {"goal": payload["goal"],
              "skill_ratings": normalize_ratings(payload["skill_ratings"], profile),
              "preferences": payload["preferences"]}
    career_input_id = _persist(db, pid, values)
    log.info("career_input saved")
    return {"career_input_id": career_input_id}
