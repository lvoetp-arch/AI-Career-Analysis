"""同意記錄與「刪除我的資料」。日誌只記步驟名稱與錯誤型別，不記任何內容。
所有資料存取都經過 Store（見 compat.py），方便測試與對齊 S0。"""
import logging

from fastapi import Depends

from app.services.s2.compat import AppError, ErrorCode, current_user, get_store

log = logging.getLogger("s2.privacy")

CONSENT_TYPE = "privacy"
# 必須與 frontend/privacy.html 的 data-privacy-version 相同。改隱私說明時兩邊一起升版，
# 所有人就會被要求重新同意（require_consent 只認最新版）。
PRIVACY_VERSION = "2026-11-23"

# 由子到父，避免外鍵順序問題。第二欄是回應中統計的名稱（None = 不計入）。
TABLE_STEPS = [
    ("analysis_runs", "analyses"),
    ("career_inputs", "career_inputs"),
    ("resume_profiles", "profiles"),
    ("resume_files", "files"),
    ("usage_counters", None),
    ("consent_records", "consents"),
]


# ---------- 同意 ----------
def list_consents(store, uid):
    return [{"consent_type": r["consent_type"], "version": r["version"], "accepted_at": r["accepted_at"]}
            for r in store.list_consents(uid)]


def record_consent(store, uid, consent_type, version):
    if consent_type != CONSENT_TYPE:
        raise AppError(ErrorCode.VALIDATION_ERROR, "同意的類型不正確。",
                       [{"path": "consent_type", "message": "目前只有隱私說明需要同意。"}])
    if version != PRIVACY_VERSION:
        raise AppError(ErrorCode.VALIDATION_ERROR, "隱私說明已經更新，請重新整理頁面後再同意。",
                       [{"path": "version", "message": "不是最新版本。"}])
    row = store.find_consent(uid, consent_type, version) or store.add_consent(uid, consent_type, version)
    return {"id": row["id"], "accepted_at": row["accepted_at"]}


def has_current_consent(store, uid):
    return any(c["consent_type"] == CONSENT_TYPE and c["version"] == PRIVACY_VERSION
               for c in store.list_consents(uid))


def require_consent(user_id, store):
    """未同意最新版隱私說明就丟 CONSENT_REQUIRED（403）。user_id 是 current_user 回傳的字串。"""
    if not has_current_consent(store, user_id):
        raise AppError(ErrorCode.CONSENT_REQUIRED, "請先閱讀並同意隱私說明，才能繼續。")


def consent_required(user_id: str = Depends(current_user), store=Depends(get_store)) -> str:
    """依賴版：`user_id: str = Depends(consent_required)`，回傳通過檢查的 user_id。"""
    require_consent(user_id, store)
    return user_id


# ---------- 刪除我的資料 ----------
def _fail(step, code, exc):
    log.error("delete_my_data failed step=%s error=%s", step, type(exc).__name__)
    raise AppError(code, "刪除沒有完成。已經刪掉的部分不會復原，請再按一次「刪除我的資料」重試。",
                   [{"path": step, "message": "這一步失敗了"}]) from exc


def delete_user_data(store, uid):
    """依序刪檔案與各表；任何一步失敗就中止並丟錯（每一步冪等，可重試）。
    先刪 Storage、後刪資料列：資料列裡有檔案路徑，檔案沒刪乾淨就不能丟掉路徑。"""
    try:
        store.delete_all_files(uid)
    except Exception as exc:  # noqa: BLE001
        _fail("storage", ErrorCode.STORAGE_ERROR, exc)

    counts = {"files": 0, "profiles": 0, "career_inputs": 0, "analyses": 0, "consents": 0}
    for table, key in TABLE_STEPS:
        try:
            n = store.delete_rows(table, uid)
            if key:
                counts[key] = n
        except Exception as exc:  # noqa: BLE001
            _fail(table, ErrorCode.INTERNAL_ERROR, exc)

    try:  # 驗證：每張表都必須是 0 筆，否則不能回成功
        left = [t for t, _ in TABLE_STEPS if store.has_rows(t, uid)]
        if left or store.has_files(uid):
            raise RuntimeError("rows remain")
    except Exception as exc:  # noqa: BLE001
        _fail("verify", ErrorCode.INTERNAL_ERROR, exc)
    return counts
