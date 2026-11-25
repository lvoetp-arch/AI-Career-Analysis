"""不需要連線的靜態檢查：migration 是否符合契約 §6 的安全要求。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SQL = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "supabase" / "migrations").glob("*.sql")))
TABLES = ["consent_records", "resume_files", "resume_profiles", "career_inputs", "analysis_runs", "usage_counters"]


def test_every_table_created_with_rls_and_anon_revoked():
    for t in TABLES:
        assert re.search(rf"create table if not exists public\.{t}\b", SQL), t
        assert re.search(rf"alter table public\.{t}\s+enable row level security", SQL), t
    assert re.search(r"revoke all on .*public\.usage_counters.* from anon", SQL, re.S)


def test_owner_policies_use_auth_uid_and_only_authenticated():
    for t in TABLES[:-1]:
        m = re.search(rf"create policy {t}_owner on public\.{t}(.*?);", SQL, re.S)
        assert m, t
        body = m.group(1)
        assert "to authenticated" in body and "user_id = auth.uid()" in body
        assert "with check" in body
    m = re.search(r"create policy usage_counters_select_own.*?;", SQL, re.S)
    assert m and "for select" in m.group(0)


def test_usage_counters_not_writable_by_clients():
    assert "revoke all on public.usage_counters from authenticated" in SQL
    assert re.search(r"grant select on public\.usage_counters to authenticated", SQL)
    assert "grant execute on function public.consume_usage(uuid, date, text, integer) to service_role" in SQL
    assert re.search(r"revoke all on function public\.consume_usage.*from public, anon, authenticated", SQL)


def test_references_must_belong_to_same_user():
    for col in ("resume_file_id", "resume_profile_id", "career_input_id"):
        assert re.search(rf"{col}.*?user_id = auth\.uid\(\)", SQL, re.S), col


def test_bucket_is_private_with_restrictive_policy_and_path_check():
    assert re.search(r"values \('resumes', 'resumes', false", SQL)
    assert "as restrictive" in SQL and "bucket_id <> 'resumes'" in SQL
    assert "storage_path = user_id::text || '/' || id::text || '.pdf'" in SQL


def test_migrations_are_idempotent_by_construction():
    assert "drop policy if exists" in SQL and "create or replace function" in SQL
    for stmt in re.findall(r"^create (?:table|index)[^\n]*", SQL, re.M | re.I):
        assert "if not exists" in stmt.lower(), stmt
