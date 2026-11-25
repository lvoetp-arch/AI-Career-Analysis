-- 私有 bucket `resumes`（契約 §6）。路徑 {user_id}/{resume_file_id}.pdf。可重複套用。
-- 只有後端用 service key 存取；anon 與 authenticated 一律擋下，也就沒有跨使用者存取。

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('resumes', 'resumes', false, 10485760, array['application/pdf'])
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- RESTRICTIVE 政策：即使日後有人替其他 bucket 加了寬鬆政策，resumes 仍然擋住 anon / authenticated。
drop policy if exists resumes_deny_client_access on storage.objects;
create policy resumes_deny_client_access on storage.objects
  as restrictive for all to anon, authenticated
  using (bucket_id <> 'resumes')
  with check (bucket_id <> 'resumes');
