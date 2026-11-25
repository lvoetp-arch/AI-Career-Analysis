/* API 工具：路徑傳 '/resumes' 這種形式，會接在 apiBase（預設 '/api'）後面。 */
(function () {
  'use strict';
  var GENERIC = '系統暫時無法使用，請稍後再試。';
  var NETWORK = '無法連線，請檢查網路後再試一次。';
  var TIMEOUT = '處理時間比預期久，請稍後再試一次。';

  function ApiError(code, message, status, details) {
    this.name = 'ApiError';
    this.code = code;
    this.message = message;
    this.status = status || 0;
    this.details = details || [];
  }
  ApiError.prototype = Object.create(Error.prototype);

  function base() {
    return (window.APP_CONFIG && window.APP_CONFIG.apiBase) || '/api';
  }

  function request(method, path, opts) {
    opts = opts || {};
    var timeoutMs = opts.timeout || 30000;
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, timeoutMs);
    var headers = { Accept: 'application/json' };
    var token = window.Auth && window.Auth.getToken();
    if (token) headers.Authorization = 'Bearer ' + token;
    var init = { method: method, headers: headers, signal: ctrl.signal };
    if (opts.formData) {
      init.body = opts.formData;              // 不手動設 Content-Type，由瀏覽器帶 boundary
    } else if (opts.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    return fetch(base() + path, init).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = undefined; }
        if (res.status === 401) {
          if (window.location.pathname.indexOf('/login.html') === -1 && window.Auth) window.Auth.logout();
        }
        if (res.ok) {
          if (data === undefined) throw new ApiError('INTERNAL_ERROR', GENERIC, res.status);
          return data;
        }
        if (data && data.error && data.error.code) {
          throw new ApiError(data.error.code, data.error.message || GENERIC, res.status, data.error.details);
        }
        throw new ApiError('INTERNAL_ERROR', GENERIC, res.status);   // 502 網頁等非 JSON 回應
      });
    }, function (err) {
      if (err && err.name === 'AbortError') throw new ApiError('TIMEOUT', TIMEOUT);
      throw new ApiError('NETWORK_ERROR', NETWORK);
    }).then(function (v) { clearTimeout(timer); return v; }, function (e) { clearTimeout(timer); throw e; });
  }

  window.Api = {
    ApiError: ApiError,
    get: function (p, o) { return request('GET', p, o); },
    post: function (p, body, o) { o = o || {}; o.body = body; return request('POST', p, o); },
    put: function (p, body, o) { o = o || {}; o.body = body; return request('PUT', p, o); },
    del: function (p, o) { return request('DELETE', p, o); },
    upload: function (p, formData, o) { o = o || {}; o.formData = formData; return request('POST', p, o); },
    analyze: function (p, body) { return request('POST', p, { body: body, timeout: 100000 }); },
    friendlyMessage: function (err) { return (err && err.message) || GENERIC; }
  };
})();
