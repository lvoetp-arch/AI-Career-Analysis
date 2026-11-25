"""S2 與 S0（core）的唯一接點，已對照 core/db.py 與 S0 交接摘要。

- `current_user`：依賴，回傳 user_id（str）
- `Db`（get_db）：使用者 JWT、RLS 生效、自動帶 user_id；`AdminDb`（get_admin_db）：service key，方法必須傳 user_id
- `AppError(ErrorCode.X, message=..., details=[{"path","message"}])`
S2 其餘程式只依賴 CoreStore 這個窄介面。
"""
from fastapi import Depends

from app.core.auth import current_user  # noqa: F401
from app.core.db import get_admin_db, get_db
from app.core.errors import AppError, ErrorCode  # noqa: F401


class CoreStore:
    def __init__(self, db, admin):
        self.db, self.admin = db, admin

    # --- 同意（使用者 Db，RLS 生效，自動帶 user_id）---
    def list_consents(self, uid):
        return self.db.select("consent_records")

    def find_consent(self, uid, ctype, version):
        return self.db.select_one("consent_records", filters={"consent_type": ctype, "version": version})

    def add_consent(self, uid, ctype, version):
        return self.db.insert("consent_records", {"consent_type": ctype, "version": version})[0]

    # --- 刪除（AdminDb，每個方法都傳 user_id；只列出、刪除自己 user_id 前綴底下的物件）---
    # 需要 S0 補丁 s0-patch/db_admin_user_files.patch（AdminDb.list_user_files / delete_user_files）
    def delete_all_files(self, uid):
        self.admin.delete_user_files(uid)  # 刪 {uid}/ 底下全部物件，含資料表沒有紀錄的孤兒檔

    def has_files(self, uid):
        return bool(self.admin.list_user_files(uid))

    def delete_rows(self, table, uid):
        return len(self.admin.delete(table, uid))

    def has_rows(self, table, uid):
        return bool(self.admin.select(table, uid, columns="user_id", limit=1))


def get_store(db=Depends(get_db), admin=Depends(get_admin_db)):
    return CoreStore(db, admin)
