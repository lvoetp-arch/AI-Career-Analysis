-- 職話直說 S0：核心資料表、索引、RLS（契約 §6）。可重複套用。
-- 原則：每張表都開 RLS，使用者只能碰 user_id = auth.uid() 的列；anon 完全沒有權限。

create or replace function public.set_updated_at()
returns trigger language plpgsql set search_path = '' as $$
begin
  new.updated_at = now();
  return new;
end $$;

-- ===== consent_records =====
create table if not exists public.consent_records (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  consent_type text not null,
  version      text not null,
  accepted_at  timestamptz not null default now()
);
create index if not exists consent_records_user_type_idx
  on public.consent_records (user_id, consent_type, accepted_at desc);

-- ===== resume_files =====
create table if not exists public.resume_files (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  storage_path text not null,
  size_bytes   bigint not null default 0 check (size_bytes >= 0),
  status       text not null default 'uploaded'
               check (status in ('uploaded','parsing','parsed','failed','deleted','delete_failed')),
  error_code   text,
  created_at   timestamptz not null default now(),
  deleted_at   timestamptz,
  -- 路徑固定為 {user_id}/{id}.pdf，使用者無法讓紀錄指向別人的檔案
  constraint resume_files_path_format check (storage_path = user_id::text || '/' || id::text || '.pdf')
);
create index if not exists resume_files_user_created_idx on public.resume_files (user_id, created_at desc);
create index if not exists resume_files_delete_failed_idx on public.resume_files (status) where status = 'delete_failed';

-- ===== resume_profiles =====
create table if not exists public.resume_profiles (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users(id) on delete cascade,
  resume_file_id uuid references public.resume_files(id) on delete set null,
  schema_version text not null,
  parsed_json    jsonb not null,
  status         text not null default 'draft' check (status in ('draft','confirmed')),
  confirmed_at   timestamptz,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create index if not exists resume_profiles_user_created_idx on public.resume_profiles (user_id, created_at desc);
create index if not exists resume_profiles_file_idx on public.resume_profiles (resume_file_id);
drop trigger if exists resume_profiles_set_updated_at on public.resume_profiles;
create trigger resume_profiles_set_updated_at before update on public.resume_profiles
  for each row execute function public.set_updated_at();

-- ===== career_inputs =====
create table if not exists public.career_inputs (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references auth.users(id) on delete cascade,
  resume_profile_id uuid not null references public.resume_profiles(id) on delete cascade,
  goal              text not null check (goal in ('continue','switch','undecided')),
  skill_ratings     jsonb not null default '[]'::jsonb,
  preferences       jsonb not null default '{}'::jsonb,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index if not exists career_inputs_user_profile_idx on public.career_inputs (user_id, resume_profile_id);
drop trigger if exists career_inputs_set_updated_at on public.career_inputs;
create trigger career_inputs_set_updated_at before update on public.career_inputs
  for each row execute function public.set_updated_at();

-- ===== analysis_runs =====
create table if not exists public.analysis_runs (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users(id) on delete cascade,
  career_input_id uuid references public.career_inputs(id) on delete set null,
  status          text not null default 'running' check (status in ('running','succeeded','failed')),
  error_code      text,
  model           text,
  schema_version  text,
  input_snapshot  jsonb,
  result_json     jsonb,
  attempts        integer not null default 0 check (attempts >= 0),
  started_at      timestamptz not null default now(),
  finished_at     timestamptz
);
create index if not exists analysis_runs_user_started_idx on public.analysis_runs (user_id, started_at desc);

-- ===== usage_counters（速率限制；使用者只能讀，寫入只走後端 service key）=====
create table if not exists public.usage_counters (
  user_id uuid not null references auth.users(id) on delete cascade,
  day     date not null,
  kind    text not null check (kind in ('parse','analysis')),
  count   integer not null default 0 check (count >= 0),
  primary key (user_id, day, kind)
);

-- ===== RLS：啟用、收回 anon、只開給 authenticated 的自己的列 =====
alter table public.consent_records  enable row level security;
alter table public.resume_files     enable row level security;
alter table public.resume_profiles  enable row level security;
alter table public.career_inputs    enable row level security;
alter table public.analysis_runs    enable row level security;
alter table public.usage_counters   enable row level security;

revoke all on public.consent_records, public.resume_files, public.resume_profiles,
              public.career_inputs, public.analysis_runs, public.usage_counters from anon;

grant select, insert, update, delete on public.consent_records, public.resume_files,
      public.resume_profiles, public.career_inputs, public.analysis_runs to authenticated;
revoke all on public.usage_counters from authenticated;
grant select on public.usage_counters to authenticated;

drop policy if exists consent_records_owner on public.consent_records;
create policy consent_records_owner on public.consent_records
  for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists resume_files_owner on public.resume_files;
create policy resume_files_owner on public.resume_files
  for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- 以下三張表另外檢查「引用的上游資料也必須是自己的」，避免拿別人的 id 掛關聯
drop policy if exists resume_profiles_owner on public.resume_profiles;
create policy resume_profiles_owner on public.resume_profiles
  for all to authenticated
  using (user_id = auth.uid())
  with check (
    user_id = auth.uid()
    and (resume_file_id is null or exists (
      select 1 from public.resume_files f where f.id = resume_file_id and f.user_id = auth.uid()))
  );

drop policy if exists career_inputs_owner on public.career_inputs;
create policy career_inputs_owner on public.career_inputs
  for all to authenticated
  using (user_id = auth.uid())
  with check (
    user_id = auth.uid()
    and exists (select 1 from public.resume_profiles p
                where p.id = resume_profile_id and p.user_id = auth.uid())
  );

drop policy if exists analysis_runs_owner on public.analysis_runs;
create policy analysis_runs_owner on public.analysis_runs
  for all to authenticated
  using (user_id = auth.uid())
  with check (
    user_id = auth.uid()
    and (career_input_id is null or exists (
      select 1 from public.career_inputs c where c.id = career_input_id and c.user_id = auth.uid()))
  );

drop policy if exists usage_counters_select_own on public.usage_counters;
create policy usage_counters_select_own on public.usage_counters
  for select to authenticated
  using (user_id = auth.uid());

-- ===== 原子的「用量 +1，超過上限就不加」，只有後端 service key 能呼叫 =====
-- 回傳加完後的次數；回傳 null 代表已達上限。
create or replace function public.consume_usage(p_user_id uuid, p_day date, p_kind text, p_limit integer)
returns integer language plpgsql security definer set search_path = public as $$
declare
  v_count integer;
begin
  if p_limit is null or p_limit <= 0 then
    return null;
  end if;
  insert into public.usage_counters as u (user_id, day, kind, count)
  values (p_user_id, p_day, p_kind, 1)
  on conflict (user_id, day, kind)
  do update set count = u.count + 1 where u.count < p_limit
  returning u.count into v_count;
  return v_count;
end $$;

revoke all on function public.consume_usage(uuid, date, text, integer) from public, anon, authenticated;
grant execute on function public.consume_usage(uuid, date, text, integer) to service_role;
