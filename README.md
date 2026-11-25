# 職話直說

## 🔗 線上展示 (Live Demo)
點擊前往體驗：[AI-Career-Analysis 線上版](https://ai-career-analysis-production.up.railway.app/)


上傳文字型 PDF 履歷 → 確認與編修 → 技能自評與求職條件 → AI 產出三個有證據、有差距、有行動的職涯方向。
共用規則見《00_總管理與共用契約》。本 README 涵蓋 S0（後端骨架 + Supabase）。

## 需求
- Python 3.11 以上、Git for Windows
- 一個 Supabase 專案（Auth + Postgres + Storage）

## 本機啟動（Windows PowerShell）
```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements-dev.txt

copy .env.example .env        # 然後用編輯器填入各變數（預設用 Gemini：GEMINI_API_KEY、GEMINI_MODEL）；.env 不會進版控
python -m uvicorn app.main:create_app --factory --app-dir backend --reload
```
開啟 http://127.0.0.1:8000/api/health 應看到 `{"status":"ok"}`；API 文件在 `/api/docs`。
缺少必要環境變數時，啟動會直接中止並列出變數名稱。

## 跑測試
```powershell
python -m pytest
```
- 不連線的測試（錯誤格式、速率限制、日誌、db 封裝、migration 靜態檢查、提交前檢查）預設全跑。
- **RLS 跨使用者整合測試**需要真的 Supabase 與兩個測試帳號，沒設定時會顯示為 skipped。
  在 `.env` 加上 `RLS_TEST_USER_A_EMAIL`、`RLS_TEST_USER_A_PASSWORD`、`RLS_TEST_USER_B_EMAIL`、
  `RLS_TEST_USER_B_PASSWORD`（專用測試帳號），先套用 migration，再跑 `python -m pytest backend/tests/s0/test_rls_integration.py`。
  S0 驗收需要這組測試實際通過。

## 套用 Supabase migration
兩個檔案在 `supabase/migrations/`，可重複套用：
- 用 Supabase CLI：`supabase link --project-ref <ref>` 後 `supabase db push`
- 或到 Dashboard > SQL Editor，依檔名順序貼上執行

## 啟用提交前檢查（每個 clone 只要做一次）
```powershell
git config core.hooksPath scripts/hooks
```
之後每次 `git commit` 都會掃描暫存區，命中金鑰樣式（`sk-ant-`、JWT、`service_role` 憑證、金鑰變數被填值）、
台灣手機、Email、身分證格式，或 `test-resumes/`、`.env`、`docs/` 以外的 PDF，就會阻擋。
- 手動檢查：`python scripts/check_no_secrets.py`（暫存區）或 `--all`（全部已追蹤檔案）
- 誤判時，在該行加上註記 `check-no-secrets: allow`
- 確實要提交的文件 PDF 只能放 `docs/`，並用 `git add -f docs/xxx.pdf`
- 緊急略過（不建議）：`git commit --no-verify`

### 第一次提交的建議順序
先提交防護，再提交其他內容：
```powershell
git checkout main && git pull
git checkout -b s0-foundation
git add .gitignore .gitattributes .env.example scripts README.md
git config core.hooksPath scripts/hooks
git commit -m "S0: 版控防護（.gitignore、提交前檢查）"
```
每次 commit 前先看 `git status` 與 `git diff --staged`。

## 結構（S0 擁有的部分）
```
backend/app/main.py        FastAPI 入口；自動掛載 routers/ 底下各模組的 router；最後掛載 frontend/
backend/app/core/          config, auth, errors, db, llm, ratelimit, logging
supabase/migrations/       資料表、索引、RLS、私有 bucket
scripts/                   check_no_secrets.py 與 hooks/pre-commit
```
各站的 router 只要在 `backend/app/routers/<檔名>.py` 提供 `router = APIRouter()` 就會被掛載，不必改 main.py。
