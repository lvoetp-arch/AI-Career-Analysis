# 分站 S2b：補修站（S1／S2／S3 留下的小問題，S4 之前或之後皆可，S9 之前必須完成）

> **狀態：S2b 已完成並合併（歷史紀錄，勿再使用）。** 實際成果見 `docs/handoff/S2b.md`。

## 你的角色
你負責修補前幾站留下的小問題，範圍明確、每項都有驗收。共用規則見 `docs/00_總管理與共用契約.md`（v0.5）。

## 附檔（開 session 時附上；讀得到專案資料夾就先讀這些）
- 共用：`docs/00_總管理與共用契約.md`（v0.5）、`docs/handoff/S0.md`、`S2.md`、`S3.md`
- 任務 A：`frontend/` 的 `index.html`、`privacy.html`、`login.html`、`register.html`、`settings.html`、`css/s2-pages.css`、`js/s2-common.js`、`js/auth.js`；**已提交的佔位版 `js/config.js`**（不要附你電腦上帶真實設定的那份）；`backend/app/services/s2/privacy.py`、`backend/tests/s2/test_s2.py`；`.gitignore`；`scripts/check_no_secrets.py`、`backend/tests/s0/test_check_no_secrets.py`
- 任務 B：`backend/app/core/llm.py`、`backend/app/core/config.py`、`.env.example`、`backend/tests/s0/` 中與 llm、config 有關的測試
- 任務 C：`backend/app/routers/resumes.py`、`backend/app/services/resume/` 整個資料夾、`backend/tests/resume/` 整個資料夾、`backend/app/core/ratelimit.py`

## 本站暫時擁有的檔案（只限於下列各任務所需，其餘一律不得修改）
- 任務 A：上列「任務 A」的所有檔案，另含 `frontend/js/config.local.js`（新增）與 `frontend/vendor/`（若選擇本地提供 supabase-js）
- 任務 B：`backend/app/core/llm.py`、`backend/app/core/config.py`、`.env.example`、`backend/tests/s0/` 中與 llm、config 相關的測試
- 任務 C：`backend/app/routers/resumes.py`、`backend/app/services/resume/` 與 `backend/tests/resume/` 中與額度、總時限直接相關的程式與測試

## 工作環境與提交規則
- 工作目錄為專案根目錄；Windows；分支 `s2b-fixes`（從最新 `main` 開出）；不得直接提交到 `main`、不得 force push。
- **絕對不可提交**：`.env`、任何金鑰、真實履歷、`test-resumes/`、含個資的截圖。每次 commit 前看 `git status` 與 `git diff --staged`；只用明確檔名 `git add`。
- 完成後交接摘要存成 `docs/handoff/S2b.md`；不自行合併。
- 若你無法執行指令，請列出我要在 Windows 本機執行的逐行指令與「應該看到什麼」；測試結果以我貼回的輸出為準。
- 先讀 `docs/handoff/S0.md`、`S2.md`、`S3.md`；讀不到請我貼上，不要猜。

## 任務 A：隱私與前端小修（S1／S2）
1. **隱私說明頁**（`privacy.html`）：寫出實際供應商（Google Gemini）與「目前使用免費額度，送出的內容可能被供應商用於改善產品並由人工審閱」；新增警告「這是測試版本，請勿上傳含真實個資的履歷」；**如實列出解析階段送出的內容**：履歷全文（已遮蔽電話、Email、住址、身分證字號與個資標籤行；網址換成代號；姓名依你選填的寫法遮蔽），其中仍包含公司名稱、學校名稱、專案與自傳內容；寫入「可選填姓名以便遮蔽，不會儲存」；升 `PRIVACY_VERSION`（後端與前端各一處，測試比對）。
2. **首頁**：加上同樣的警告文字（顯眼，字級 ≥ 14px）。
3. **跑馬燈**（`index.html` 與 `css/s2-pages.css`）：自動動畫總時長 ≤ 5 秒後靜止；尊重 `prefers-reduced-motion`；不需要暫停按鈕。
4. **supabase-js**（`index`、`login`、`register`、`settings`、`privacy` 各頁凡有載入者）：目前從 CDN 載入固定版本且沒有完整性檢查。二擇一：加 `integrity` 與 `crossorigin`（需實際計算 SRI 雜湊，算不出來就不要猜），或把檔案放進 `frontend/vendor/` 由專案自己提供（優先）。
5. **config.js 改版**：`frontend/js/config.js` 只放佔位值並進版控；真實值放 `frontend/js/config.local.js`（加入 `.gitignore`），網頁在載入 `config.js` 之後載入 `config.local.js`（不存在時不報錯，只顯示「尚未設定」提示）。提供「使用者要做的 Windows 步驟」（複製真實值到 `config.local.js`、取消目前的 `skip-worktree`：`git update-index --no-skip-worktree frontend/js/config.js`），每步附「應該看到什麼」。

6. **`test_repo_itself_is_clean`（S0 的 `tests/s0/test_check_no_secrets.py`）改讀「已追蹤的內容」**：目前它讀的是工作目錄裡的檔案，使用者本機放了真實的 `config.js` 時會誤報（JWT 樣式、金鑰環境變數被填了值）。改成用 `git show :<路徑>`（暫存區內容）或 `git cat-file` 讀取已追蹤檔案的內容，工作目錄的本機修改不應影響結果；並新增一個測試：把真實樣式的值只寫在工作目錄、不寫進暫存區時，測試仍通過；寫進暫存區時，測試失敗。

## 任務 B：`core/llm.py` 強化（S0 檔案；S3 審查提出）
1. **不寫死預設模型**：模型名稱只來自 `GEMINI_MODEL`（或 anthropic 時的 `CLAUDE_MODEL`）；未設定時啟動失敗並指出變數名稱。`.env.example` 加註解：模型名稱可能過期（舊模型對新帳號會 404）、`GEMINI_API_KEY` 只能放**一把**金鑰。
2. **重試**：HTTP 503／429 以退避方式最多重試 2 次（約 1 秒、3 秒）；401、404 不重試。
3. **總時限**：`complete(...)` 新增選填參數 `deadline`（絕對時間或剩餘秒數，擇一並文件化）；不得在剩餘時間不足時開始新的重試。向下相容：不傳就維持原行為。
4. **日誌**：失敗時只記 HTTP 狀態碼與重試次數（不含任何輸入輸出內容）；對外錯誤碼仍是 `LLM_ERROR`。
5. 測試：用假 HTTP 驗證各狀態碼的重試次數、不重試條件、時限、日誌不含內容。

## 任務 C：履歷上傳額度與時限（S3 檔案）
1. **額度只在要呼叫 LLM 時才扣**：非 PDF、過大、加密、掃描檔等本機驗證不通過者不扣額度（仍須先通過 `require_consent`）。這類失敗仍可建立 `failed` 的 `resume_files` 紀錄，但不計入每日額度。
2. **總時限 90 秒**：`POST /api/resumes` 的整個解析流程（含 Schema 不符後的第二次呼叫）共用一個 90 秒時限，並傳入 `llm.complete(deadline=...)`；超過時回 `LLM_ERROR`。前端逾時 100 秒維持不變。
3. 測試：不合格檔案不增加 `usage_counters`；合格檔案才增加；時限情境。

## 不做
新功能；任何不在上述清單的檔案；Gemini JSON 模式（目前 6/6 一次通過，不需要；若之後失敗率上升再評估）。

## 驗收標準
- 隱私頁的供應商、方案、送出內容、警告與契約 §10-2、§10-10、§10-11 一致；`PRIVACY_VERSION` 兩處一致（測試）。
- 隱私模式開啟首頁：動畫 ≤ 5 秒後停止。
- 全新 clone 沒有 `config.local.js` 時頁面不報錯；`git ls-files` 看不到 `config.local.js`。
- 未設 `GEMINI_MODEL` 時啟動訊息明確。
- 本機全部測試通過：S2 的 21 項、S3 的 37 項、本站新增項目（貼回 `python -m pytest -q` 的輸出）。

## 交接
依契約 §14 回報，存成 `docs/handoff/S2b.md`。
