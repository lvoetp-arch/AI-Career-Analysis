# frontend/vendor

專案自己提供的第三方前端檔案（不從 CDN 載入，避免外部檔案被替換）。

## supabase-js 2.45.4

- 來源：npm 套件 `@supabase/supabase-js@2.45.4` 的 `dist/umd/supabase.js`（未改動），授權 MIT（見 supabase-js-LICENSE.txt）。
- 套件完整性（npm）：`sha512-E5p8/zOLaQ3a462MZnmnz03CrduA5ySH9hZyL03Y+QZLIOO4/Gs8Rdy4ZCKDHsN7x0xdanVEWWFN3pJFQr9/hg==`
- 檔案 SHA-256：`8596965fe918e656600a1b568d3a168f5c0d3d22a600886bb6f44a6555db01e7`
- 載入方式：各頁 `<script src="vendor/supabase-js-2.45.4.umd.js">`，在 `js/config.js` 之後、`js/auth.js` 之前。
- 瀏覽器環境不會用到同套件的 `591.supabase.js`（那只是給非瀏覽器環境的 ws 佔位檔），所以沒有放進來。

### 如何檢查檔案沒被改過（Windows PowerShell，在專案根目錄）

    Get-FileHash frontend\vendor\supabase-js-2.45.4.umd.js -Algorithm SHA256

輸出的 Hash 應該與上面的 SHA-256 相同（大小寫不影響）。

### 如何升級

不要直接覆蓋。先確認新版本發佈已超過兩週，再放新檔名（例如 supabase-js-<新版本>.umd.js），
更新各頁的 script 路徑、本檔的來源與雜湊，並重新跑 `python -m pytest -q`。
