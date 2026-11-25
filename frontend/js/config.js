/* 前端公開設定（進版控的佔位版）：只放佔位值。
   真實的 Supabase URL 與 anon（公開）金鑰請放在 js/config.local.js（已加入 .gitignore，不進版控）。
   絕對不要放 service key 或任何 LLM 金鑰。

   每一頁都已經在載入本檔，所以本檔最後會接著載入同資料夾的 config.local.js（不必改各頁的 HTML）。
   config.local.js 不存在時，瀏覽器主控台只會多一行 404，不會造成 JavaScript 錯誤；
   這時 auth.js 判定「尚未設定」，有載入 s2-common.js 的頁面會顯示提示。

   config.local.js 的內容（整份取代 window.APP_CONFIG；本檔不要複製成 config.local.js）：
     window.APP_CONFIG = { SUPABASE_URL: "https://xxxx.supabase.co", SUPABASE_ANON_KEY: "..." };
*/
window.APP_CONFIG = {
  SUPABASE_URL: "",
  SUPABASE_ANON_KEY: ""
};
(function () {
  // 只插入一次：即使有人把本檔誤複製成 config.local.js，也不會無限循環載入。
  if (window.__APP_CONFIG_LOCAL_REQUESTED__) return;
  window.__APP_CONFIG_LOCAL_REQUESTED__ = true;
  var me = document.currentScript;
  if (!me || !me.src || !/config\.js(\?.*)?$/.test(me.src)) return;
  // document.write 才能保證「在後面的 <script>（auth.js）執行之前」載入完成。
  document.write('<script src="' + me.src.replace(/config\.js(\?.*)?$/, 'config.local.js') + '"><\/script>');
})();
