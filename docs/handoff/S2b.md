# S2b 交接摘要：補修站（S1／S2／S3 小問題）

## 1. 完成了什麼
**任務 A（隱私與前端）**
- `frontend/privacy.html`：寫明供應商 Google（Gemini）、免費額度（內容可能被用於改善產品並由人工審閱）、頂端「這是測試版本，請勿上傳含真實個資的履歷」警告；如實列出解析階段送出履歷全文（含公司名稱、學校名稱、專案與自傳內容）；姓名選填、不儲存；規則遮蔽仍會漏的項目；同意勾選文字同步更新。
- `PRIVACY_VERSION`：`2026-11-12` → `2026-11-18`（`backend/app/services/s2/privacy.py` 與 `frontend/js/s2-common.js` 各一處，另有測試比對頁面上的「版本 …」）。**所有人需重新同意。**
- `frontend/index.html`、`css/s2-pages.css`：首頁警告（`.test-warning`，字級 1rem）；跑馬燈改為只跑 1 次、4 秒、停在最後一格（`prefers-reduced-motion` 時完全靜態）；移除「滑鼠移上暫停」規則。
- supabase-js：五頁（index／login／register／settings／privacy）改載入 `frontend/vendor/supabase-js-2.45.4.umd.js`（npm 套件 2.45.4 的 `dist/umd/supabase.js`，未改動；含授權檔與 `vendor/README.md` 記錄來源、npm 完整性與檔案 SHA-256）。沒有使用 SRI。
- `frontend/js/config.js`：進版控的佔位版（只有空字串）＋載入同資料夾 `config.local.js` 的一小段；`.gitignore` 加入 `frontend/js/config.local.js`；`s2-common.js` 在沒有可用設定時於頁面頂端顯示「尚未設定登入服務…」。
- `backend/tests/s0/test_check_no_secrets.py`：`test_repo_itself_is_clean` 改讀「暫存區內容」（`git ls-files -s` + `git cat-file --batch`）；新增 4 個測試（只在工作目錄有真實樣式的值 → 通過；寫進暫存區 → 失敗；工作目錄改乾淨但暫存區仍髒 → 失敗；被忽略的本機設定檔不在範圍）。
**任務 B（`core/llm.py`）**
- 不寫死預設模型：模型名稱只來自 `GEMINI_MODEL`／`CLAUDE_MODEL`；未設定時 `load_settings`（原本就會）與 `build_llm_client`（新增）都以 `ConfigError` 指出變數名稱。`.env.example` 加註：模型名稱可能過期（舊模型對新帳號 404）、`GEMINI_API_KEY` 只能放一把。
- 重試：429／503 最多重試 2 次（等 1 秒、3 秒）；401／403／404／400 不重試；500／502／504 與連線失敗維持原本「只重試 1 次」；逾時不重試。
- 總時限：`complete(system, user, *, deadline=None)`；`deadline` 是 **`time.monotonic()` 的絕對時間**（`deadline_in(秒數)` 產生）。已過期不發請求（`LLMTimeoutError`，對外仍是 `LLM_ERROR`）；單次請求逾時不超過剩餘時間；等待後剩餘不到 `MIN_RETRY_SECONDS`（10 秒，我定的）就不開始新的重試；不傳 deadline = 舊行為。Gemini 與 Anthropic 兩條路徑都遵守。
- 日誌：只記供應商、HTTP 狀態碼（或 timeout／network）與重試次數；不含輸入、輸出、供應商回傳訊息、金鑰。
- `FakeLLMClient` 接受 `deadline` 並記錄在 `deadlines`。
**任務 C（`POST /api/resumes`）**
- 額度只在要呼叫 LLM 前才扣：`run_parse` 新增 `before_llm` 掛鉤（本機驗證與遮蔽全部通過後、第一次 LLM 呼叫前執行一次），路由用它呼叫 `rate_limiter.consume`。非 PDF、過大、加密、過多頁、掃描檔不扣額度（仍須先通過 `consent_required`；仍建立 `failed` 的 `resume_files` 紀錄）。Schema 重試不會再扣一次；LLM 呼叫失敗額度照扣（因為已經呼叫）。
- 總時限：`PARSE_TOTAL_SECONDS = 90`，從 handler 開頭起算，同一個 deadline 傳給兩次 `llm.complete`；`structure_resume` 在第二次呼叫前若已逾時，直接回 `LLM_ERROR`，不再呼叫。前端 100 秒不動。
- 檔案清單見第 6 節的 `git add` 清單。

## 2. 與任務書／契約的差異
- **config.local.js 由 config.js 自己載入**（`document.write`，同步，保證早於 auth.js 執行），而不是在各頁加 `<script>`。原因：S3 的 `upload.html` 與之後 S4–S8 的頁面都載入 `config.js`，若只改 S2 的五頁，其他頁一旦 config.js 變回佔位版就全部壞掉。檔案不存在時瀏覽器主控台會有一行 404（無法避免），但沒有 JavaScript 錯誤。內有防止循環載入的旗標。
- 跑馬燈的 `<button class="marquee__pause">` 仍留在 `index.html`（CSS 隱藏）：我沒有 `js/ui.js`，不確定它是否綁定該按鈕，刪掉可能讓 ui.js 出錯。確認後可刪（第 6 節步驟 5）。
- 隱私頁我多寫了兩點（不在任務書逐項清單，但屬於 §10-10「如實」）：規則遮蔽仍會漏的項目（取自 S3 交接摘要第 5 節）、分析階段送出的內容（依 §10-12b 公司與學校改代號）。**後者依賴 S6 真的照 §10-12b 實作，否則要改回來。**
- 重試策略：只有 429／503 是 2 次；其他暫時性錯誤維持 1 次（任務書沒有要求改）。

## 3. 契約變更請求
1. §5：新增 `frontend/vendor/`（專案自供的第三方檔案）與 `frontend/js/config.local.js`（不進版控）；`frontend/js/config.js` 改為「進版控的佔位版＋載入 config.local.js」。（v0.4 寫「config.js 為本機檔，不進版控」，與 S2 的實作不一致，請一併更正。）
2. §11：補上 `deadline` 的語義（`time.monotonic()` 絕對時間）與「剩餘不足 10 秒不開始新的重試」。
3. **`.gitattributes`（S0 檔案，我沒有改）**：建議加 `frontend/vendor/* -text`。否則 Windows 的 autocrlf 會在 clone 時把 vendored 檔案換行改成 CRLF，`Get-FileHash` 會對不上 README 的雜湊（功能不受影響）。
4. S4–S8 各頁（以及 S3 的 `upload.html`）請把 supabase-js 的 CDN 網址換成 `vendor/supabase-js-2.45.4.umd.js`；順序維持 config.js → vendor → auth.js。

## 4. 未完成或已知問題
- **`frontend/upload.html`（S3 檔案）仍從 CDN 載入 supabase-js**，我沒有改（不在本站範圍）。任務 A-4 的目標要等它也改完才算全站達成。
- **我無法在你的 repo 跑完整測試**：附檔缺 `core/errors|auth|db`、`main.py`、`conftest.py`、JSON Schema、fixtures、S2 的 `compat`／`fakes`。我用替身補齊後驗證：S0 的 llm／config／check_no_secrets 測試與 S3 全部新測試通過；S3 舊測試中 3 項（`test_schema_rejects_phone_email_extra_fields`、`test_overlong_source_text_is_truncated_not_failed`、`test_truncated_json_still_retries`）在替身環境失敗，但**未修改的原始程式碼在同一替身環境也是同樣 3 項失敗**（因為替身 Schema 太寬鬆），應不是本次改動造成；S2 要建立 `create_app()` 的 9 項因替身 `main.py` 無法執行。**一切以你貼回的 `python -m pytest -q` 為準。** `test_startup_exits_naming_missing_model` 假設 `create_app()` 對缺少設定回 `SystemExit` 並帶變數名稱（與既有 `test_create_app_exits_with_variable_name` 同一種寫法），未在真實 `main.py` 上驗證。
- 額度改在 LLM 之前才扣後：超過每日上限的使用者，每次上傳仍會先做本機驗證與抽文字（5 MB 以內），並產生一次 bucket 上傳／刪除與一筆 `failed` 紀錄（`error_code=RATE_LIMITED`），之後才回 429。`core/ratelimit.py` 不是本站擁有的檔案，沒有「只查不扣」的介面；若在意，建議由 S0 加 `peek`，路由先查再做昂貴的事。
- 90 秒是 LLM 階段的軟性上限：httpx 的逾時是每個階段（連線／讀取）而不是總時間，所以一個持續慢速吐資料的連線理論上可能略超過；實務上由 `deadline` 縮短單次逾時加上「不在時間不足時重試」已足夠，不是硬性中止。
- 跑馬燈停住時的畫面與起點相同（位移剛好一整組），所以只看得到「滑了一下停住」；超寬螢幕（> 約 3000px）右側可能留白。
- 首頁「先看範例報告」等其他項目未動。

## 5. 需要主 session 決策的事項
- 是否接受第 3 節 1–4；`.gitattributes` 由誰改。
- 隱私頁「分析階段」那一點是否保留（取決於 S6 是否照 §10-12b 做）。
- 是否要 S0 加 `RateLimiter.peek`（見第 4 節）。
- 是否要把 `MIN_RETRY_SECONDS=10` 寫進契約或調整。

## 6. 如何本機執行與測試（Windows PowerShell，專案根目錄）
**使用者要做的步驟（每步附「應該看到什麼」）**

1. 開分支  
   `git checkout main` → `git pull` → `git checkout -b s2b-fixes`  
   應看到：`Switched to a new branch 's2b-fixes'`。
2. **在解壓縮之前**，把真實的 `config.js` 複製成 `config.local.js`（解壓縮會把 config.js 換成佔位版）  
   `Copy-Item frontend\js\config.js frontend\js\config.local.js`  
   `(Select-String -Path frontend\js\config.local.js -Pattern 'SUPABASE_(URL|ANON_KEY)').Count`  
   應看到：`2`；`(Get-Item frontend\js\config.local.js).Length` 應明顯大於 314（佔位版只有 314 位元組）。
3. 取消 skip-worktree  
   `git update-index --no-skip-worktree frontend/js/config.js` → `git status --short`  
   應看到：` M frontend/js/config.js`（它含真實值，**不能 add**）；沒有 `config.local.js`。
4. 解壓縮變更包到專案根目錄（路徑依你存放的位置調整）  
   `Expand-Archive -Path "$HOME\Downloads\S2b_changes.zip" -DestinationPath . -Force`  
   應看到：沒有輸出、沒有錯誤。接著 `git status --short` 應列出第 7 步清單裡的檔案；`git check-ignore -v frontend/js/config.local.js` 應看到一行以 `.gitignore:` 開頭、結尾是 `frontend/js/config.local.js`；`git diff frontend/js/config.js` 應只看到註解與載入區塊的差異，**看不到任何金鑰**。
5. 檢查 ui.js 是否用到跑馬燈暫停鈕  
   `Select-String -Path frontend\js\ui.js -Pattern 'marquee'`  
   應看到：沒有輸出 → 可（選擇性）刪掉 `index.html` 裡的 `<button ... class="marquee__pause" ...>`；有輸出 → 請貼給我，不要刪。
6. 跑測試  
   `.venv\Scripts\activate` → `python -m pytest -q`  
   應看到：全部通過（S2 原有 21 項、S3 原有 37 項、本站新增 61 項：S3 16、`llm` 23、config 4、check_no_secrets 4、S2 前端 14）；`git` 可用時 `test_repo_itself_is_clean` 為 passed 而不是 skipped。請把輸出貼回。
7. 手動驗證  
   a. `python -m uvicorn app.main:create_app --factory --app-dir backend`，開 `http://127.0.0.1:8000/index.html`：頂端附近有黃底「這是測試版本，請勿上傳含真實個資的履歷。」；跑馬燈約 4 秒後停住；可以登入（代表 config.local.js 有被載入）。  
   b. 暫時改名 `Rename-Item frontend\js\config.local.js config.local.js.bak`，重新整理首頁：頁面頂端出現紅色「尚未設定登入服務…」；主控台只有一行 `config.local.js` 的 404，沒有 `Uncaught` 錯誤。改回名稱。  
   c. Windows 設定 > 協助工具 > 視覺效果 > 關閉「動畫效果」，重新整理首頁：跑馬燈完全不動。測完再打開。  
   d. 開 `privacy.html`：頂端有警告；看得到「免費額度」「人工審閱」「履歷全文」「公司名稱、學校名稱、專案內容與自傳內容」。登入後要求重新同意（版本 2026-11-18）。  
   e. 暫時把 `.env` 的 `GEMINI_MODEL=` 那行整行註解掉再啟動 uvicorn：程式立即結束，訊息含「缺少必要環境變數：GEMINI_MODEL」。測完還原（模型名稱請填目前可用的，S3 實測用過 `gemini-3.6-flash`）。  
   f. `git ls-files | Select-String 'config.local'` 應沒有輸出。  
   g. `Get-FileHash frontend\vendor\supabase-js-2.45.4.umd.js -Algorithm SHA256`：應與 `frontend/vendor/README.md` 的 SHA-256 相同（見上方第 3 節 3，clone 後可能因換行不同）。
8. 提交前檢查與提交（只用明確檔名）  
   `git add .env.example .gitignore backend/app/core/llm.py backend/app/routers/resumes.py backend/app/services/resume/parser.py backend/app/services/resume/pipeline.py backend/app/services/s2/privacy.py backend/tests/resume/test_s3_resume.py backend/tests/resume/test_s3_router.py backend/tests/s0/test_check_no_secrets.py backend/tests/s0/test_config.py backend/tests/s0/test_llm.py backend/tests/s0/test_llm_gemini.py backend/tests/s2/test_s2.py frontend/index.html frontend/login.html frontend/register.html frontend/settings.html frontend/privacy.html frontend/css/s2-pages.css frontend/js/s2-common.js frontend/js/config.js frontend/vendor/supabase-js-2.45.4.umd.js frontend/vendor/supabase-js-LICENSE.txt frontend/vendor/README.md docs/handoff/S2b.md`  
   `git diff --staged --stat`：應是上面這些檔案，沒有 `.env`、`config.local.js`、任何 PDF。  
   `git diff --staged frontend/js/config.js`：確認只有空字串佔位。  
   `python scripts/check_no_secrets.py`：應沒有輸出（結束碼 0）。  
   `git commit -m "S2b: privacy page, config.local.js, vendored supabase-js, llm retry/deadline, resume quota"`（不要 push 到 main、不要合併）。
