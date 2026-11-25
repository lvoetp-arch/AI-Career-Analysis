"""最小的 supabase-py 假實作（記憶體內），只支援 S2 用到的呼叫。"""
import itertools


class _Res:
    def __init__(self, data): self.data = data


class _Q:
    def __init__(self, db, table): self.db, self.t, self.mode, self.filters, self.payload, self.lim = db, table, "select", [], None, None
    def select(self, *_a, **_k): self.mode = "select"; return self
    def insert(self, row): self.mode, self.payload = "insert", row; return self
    def delete(self): self.mode = "delete"; return self
    def eq(self, k, v): self.filters.append((k, v)); return self
    def limit(self, n): self.lim = n; return self
    def execute(self):
        self.db.check_fail(self.t, self.mode)
        rows = self.db.tables[self.t]
        match = [r for r in rows if all(r.get(k) == v for k, v in self.filters)]
        if self.mode == "insert":
            row = dict(self.payload); row.setdefault("id", f"id{next(self.db.ids)}"); row.setdefault("accepted_at", "2026-11-12T00:00:00Z")
            rows.append(row); return _Res([row])
        if self.mode == "delete":
            self.db.tables[self.t] = [r for r in rows if r not in match]; return _Res(match)
        return _Res(match[: self.lim] if self.lim else match)


class _Bucket:
    def __init__(self, db): self.db = db
    def list(self, prefix):
        self.db.check_fail("storage", "list")
        return [{"name": p.split("/", 1)[1]} for p in self.db.files if p.startswith(prefix + "/")]
    def remove(self, paths):
        self.db.check_fail("storage", "remove")
        for p in paths: self.db.files.discard(p)


class _Storage:
    def __init__(self, db): self.db = db
    def from_(self, _b): return _Bucket(self.db)


class FakeDB:
    TABLES = ["consent_records", "resume_files", "resume_profiles", "career_inputs", "analysis_runs", "usage_counters"]
    def __init__(self):
        self.tables = {t: [] for t in self.TABLES}
        self.files, self.ids, self.fail = set(), itertools.count(1), set()
        self.storage = _Storage(self)
    def table(self, name): return _Q(self, name)
    def check_fail(self, target, op):
        if (target, op) in self.fail: raise RuntimeError("boom")
    def seed_user(self, uid):
        for t in self.TABLES:
            self.tables[t].append({"id": f"{t}-{uid}", "user_id": uid})
        self.files.add(f"{uid}/resume_files-{uid}.pdf")
    # ---- Store 介面（與 compat.CoreStore 相同）----
    def list_consents(self, uid): return [r for r in self.tables["consent_records"] if r["user_id"] == uid]
    def find_consent(self, uid, t, v):
        return next((r for r in self.list_consents(uid) if r["consent_type"] == t and r["version"] == v), None)
    def add_consent(self, uid, t, v):
        return self.table("consent_records").insert({"user_id": uid, "consent_type": t, "version": v}).execute().data[0]
    def delete_all_files(self, uid):
        self.check_fail("storage", "remove")
        for p in [p for p in self.files if p.startswith(uid + "/")]: self.files.discard(p)
    def has_files(self, uid): return bool(self.files_of(uid))
    def delete_rows(self, table, uid): return len(self.table(table).delete().eq("user_id", uid).execute().data)
    def has_rows(self, table, uid): return bool(self.table(table).select().eq("user_id", uid).limit(1).execute().data)

    def count(self, uid): return sum(1 for t in self.TABLES for r in self.tables[t] if r["user_id"] == uid)
    def files_of(self, uid): return [p for p in self.files if p.startswith(uid + "/")]
