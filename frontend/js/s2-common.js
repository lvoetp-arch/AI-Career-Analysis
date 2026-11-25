/* js/s2-common.js — S2 各頁共用：頁首登入狀態、需登入的連結、next 參數驗證。
   只用 textContent 與屬性設定（不插入 HTML 字串）。 */
(function () {
  'use strict';
  function safeNext(raw, fallback) {
    // 只接受同資料夾頁面，以及報告或分析頁需要的 UUID 參數。
    var destination = raw || '';
    if (/^[a-z_]+\.html$/.test(destination)) return destination;
    var run = /^(?:report\.html\?id=|analyzing\.html\?career_input_id=)[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
    return run.test(destination) ? destination : fallback;
  }
  function param(name) { return new URLSearchParams(location.search).get(name); }

  function applyHeaderState() {
    var loggedIn = window.Auth && Auth.isLoggedIn();
    document.querySelectorAll('[data-auth="in"]').forEach(function (el) { el.hidden = !loggedIn; });
    document.querySelectorAll('[data-auth="out"]').forEach(function (el) { el.hidden = !!loggedIn; });
    document.querySelectorAll('[data-requires-login]').forEach(function (a) {
      if (!loggedIn) a.setAttribute('href', 'login.html?next=' + encodeURIComponent((a.getAttribute('href') || '').split('?')[0]));
    });
  }

  // config.local.js 不存在或沒填 Supabase 設定（或 supabase-js 沒載入）時，Auth 沒有可用的用戶端：
  // 在頁面最上方顯示提示，而不是靜悄悄地壞掉。只用 textContent。
  function showNotConfigured() {
    if (!window.Auth || Auth.isConfigured()) return;
    var main = document.querySelector('main');
    if (!main || main.querySelector('.config-missing')) return;
    var box = document.createElement('div');
    box.className = 'alert alert--error config-missing';
    box.setAttribute('role', 'alert');
    var p = document.createElement('p');
    p.textContent = '尚未設定登入服務。請確認 js/config.local.js 存在，並填好 Supabase 網址與公開金鑰，然後重新整理頁面。';
    box.appendChild(p);
    main.insertBefore(box, main.firstChild);
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest && e.target.closest('[data-action="logout"]');
    if (t) { e.preventDefault(); Auth.logout(); }
  });

  window.S2 = { safeNext: safeNext, param: param };
  if (window.Auth) Auth.ready.then(function () { applyHeaderState(); showNotConfigured(); });
})();
