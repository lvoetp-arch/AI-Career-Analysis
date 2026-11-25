/* js/auth.js — 真實版（S2）。取代 S1 的假實作。
   介面：Auth.ready（Promise）、isConfigured()、getToken()、isLoggedIn()、getEmail()、
         login(email, pw)、register(email, pw)、logout(opts)、requireLogin()、checkPassword(pw)、friendlyError(err)
   token 只存在 supabase-js 自己的 session 儲存；本檔不把履歷或個資寫進任何瀏覽器儲存。 */
(function () {
  'use strict';
  var cfg = window.APP_CONFIG || {};
  var client = null;
  var session = null;
  var resolveReady;
  var ready = new Promise(function (r) { resolveReady = r; });

  function AuthError(code, message) {
    var e = new Error(message);
    e.name = 'AuthError';
    e.code = code;
    return e;
  }

  var MESSAGES = {
    INVALID_CREDENTIALS: 'Email 或密碼不對，請再試一次。',
    EMAIL_EXISTS: '這個 Email 已經註冊過了，請直接登入。',
    WEAK_PASSWORD: '密碼太容易被猜到。請至少 8 個字元，並同時包含英文字母與數字。',
    INVALID_EMAIL: 'Email 格式看起來不對，請檢查一下。',
    EMAIL_NOT_CONFIRMED: '這個 Email 還沒完成驗證，請先到信箱點確認連結。',
    RATE_LIMITED: '嘗試太多次了，請稍等幾分鐘再試。',
    NOT_CONFIGURED: '系統尚未設定好登入服務，請聯絡管理者。',
    NETWORK: '連不上登入服務，請檢查網路後再試。',
    UNKNOWN: '登入服務發生問題，請稍後再試。'
  };

  function mapError(err) {
    var code = (err && (err.code || '')) + '';
    var msg = ((err && err.message) || '').toLowerCase();
    var status = err && err.status;
    if (code === 'invalid_credentials' || msg.indexOf('invalid login credentials') >= 0) return 'INVALID_CREDENTIALS';
    if (code === 'user_already_exists' || code === 'email_exists' || msg.indexOf('already registered') >= 0) return 'EMAIL_EXISTS';
    if (code === 'weak_password' || msg.indexOf('password should') >= 0) return 'WEAK_PASSWORD';
    if (code === 'email_address_invalid' || code === 'validation_failed' && msg.indexOf('email') >= 0) return 'INVALID_EMAIL';
    if (code === 'email_not_confirmed') return 'EMAIL_NOT_CONFIRMED';
    if (code === 'over_request_rate_limit' || code === 'over_email_send_rate_limit' || status === 429) return 'RATE_LIMITED';
    if (msg.indexOf('failed to fetch') >= 0 || msg.indexOf('network') >= 0) return 'NETWORK';
    return 'UNKNOWN';
  }

  function fail(err) {
    var c = mapError(err);
    throw AuthError(c, MESSAGES[c]);
  }

  function init() {
    if (!window.supabase || !cfg.SUPABASE_URL || !cfg.SUPABASE_ANON_KEY) { resolveReady(); return; }
    client = window.supabase.createClient(cfg.SUPABASE_URL, cfg.SUPABASE_ANON_KEY, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false }
    });
    // 注意：這個 callback 內不要再呼叫 supabase 方法（會死結），只更新快取。
    client.auth.onAuthStateChange(function (_event, s) { session = s; });
    client.auth.getSession()
      .then(function (r) { session = r.data.session; })
      .catch(function () { session = null; })
      .then(function () { resolveReady(); });
  }

  function currentFile() {
    var p = location.pathname.split('/').pop();
    if (p === 'report.html' || p === 'analyzing.html') return p + location.search;
    return /^[a-z_]+\.html$/.test(p) ? p : 'index.html';
  }

  var Auth = {
    ready: ready,
    isConfigured: function () { return !!client; },
    getToken: function () { return session && session.access_token ? session.access_token : null; },
    isLoggedIn: function () { return !!(session && session.access_token); },
    getEmail: function () { return session && session.user ? session.user.email : null; },

    checkPassword: function (pw) {
      if (!pw || pw.length < 8) return '密碼至少要 8 個字元。';
      if (!/[A-Za-z]/.test(pw) || !/[0-9]/.test(pw)) return '密碼要同時包含英文字母與數字。';
      return null;
    },
    friendlyError: function (err) {
      return err && err.name === 'AuthError' ? err.message : MESSAGES.UNKNOWN;
    },

    login: function (email, password) {
      if (!client) return Promise.reject(AuthError('NOT_CONFIGURED', MESSAGES.NOT_CONFIGURED));
      return client.auth.signInWithPassword({ email: email, password: password }).then(function (r) {
        if (r.error) fail(r.error);
        session = r.data.session;
        return { ok: true };
      }, fail);
    },

    /* 回傳 { ok: true, needsConfirm: bool }。Email 已註冊時丟 EMAIL_EXISTS。 */
    register: function (email, password) {
      if (!client) return Promise.reject(AuthError('NOT_CONFIGURED', MESSAGES.NOT_CONFIGURED));
      return client.auth.signUp({ email: email, password: password }).then(function (r) {
        if (r.error) fail(r.error);
        var u = r.data.user;
        // 開啟 Email 驗證時，重複註冊不會報錯，而是回傳 identities 為空的使用者。
        if (u && Array.isArray(u.identities) && u.identities.length === 0) throw AuthError('EMAIL_EXISTS', MESSAGES.EMAIL_EXISTS);
        session = r.data.session || null;
        return { ok: true, needsConfirm: !r.data.session };
      }, fail);
    },

    /* 登出並清除 token。預設導向 login.html（api.js 遇到 401 會無參數呼叫）。 */
    logout: function (opts) {
      var go = !(opts && opts.redirect === false);
      var done = function () { session = null; if (go) location.replace('login.html'); };
      if (!client) { done(); return Promise.resolve(); }
      return client.auth.signOut().then(done, done);
    },

    /* 需要登入的頁面：未登入則導向 login.html?next=<本頁>。回傳 Promise<boolean>。 */
    requireLogin: function () {
      return ready.then(function () {
        if (Auth.isLoggedIn()) return true;
        location.replace('login.html?next=' + encodeURIComponent(currentFile()));
        return false;
      });
    }
  };

  window.Auth = Auth;
  init();
})();
