/* js/upload.js — 上傳頁（S3）。只用 textContent 顯示內容；不把履歷、姓名寫進任何瀏覽器儲存。 */
(function () {
  'use strict';
  var MAX_BYTES = 5 * 1024 * 1024;
  var REDIRECT_TO_REVIEW = true;  // review.html 已完成（S4）

  var $ = function (id) { return document.getElementById(id); };
  var dz = $('dropzone'), input = $('file-input'), btn = $('submit-btn');
  var chosen = null, busy = false;

  var MSG = {
    FILE_INVALID: ['檔案不符', '請確認是未加密、5MB 以內、10 頁以內的 PDF，然後重新選擇檔案。'],
    SCANNED_OR_EMPTY: ['讀不到文字', '這份 PDF 沒有可讀取的文字（可能是掃描檔或圖片檔）。目前不支援，請改傳可以選取文字的 PDF。'],
    SCHEMA_INVALID: ['解析結果不完整', '系統已自動重試一次仍失敗。請稍後再試，或換一份檔案。'],
    LLM_ERROR: ['AI 服務暫時無法使用', '請稍後再試一次。'],
    CONSENT_REQUIRED: ['需要先同意隱私說明', '請先閱讀並同意隱私說明，再回來上傳。'],
    RATE_LIMITED: ['今天的上傳次數已達上限', '請明天再試。']
  };

  function show(id, on) { $(id).hidden = !on; }
  function setFileError(text) {
    $('file-error').textContent = text || '';
    show('file-error', !!text);
    dz.classList.toggle('is-error', !!text);
  }
  function refresh() { btn.disabled = busy || !chosen; }

  function pick(f) {
    show('error-box', false); show('success-box', false); setFileError('');
    chosen = null; dz.classList.remove('has-file'); $('dz-file').textContent = '';
    if (!f) { refresh(); return; }
    var isPdf = f.type === 'application/pdf' || /\.pdf$/i.test(f.name);
    if (!isPdf) { setFileError('只接受 PDF 檔。'); refresh(); return; }
    if (f.size > MAX_BYTES) { setFileError('檔案超過 5MB，請壓縮或改傳較小的檔案。'); refresh(); return; }
    chosen = f; dz.classList.add('has-file');
    $('dz-file').textContent = f.name + '（' + (f.size / 1024 / 1024).toFixed(2) + ' MB）';
    refresh();
  }

  input.addEventListener('change', function () { pick(input.files && input.files[0]); });
  ['dragenter', 'dragover'].forEach(function (ev) {
    dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.add('is-dragover'); });
  });
  ['dragleave', 'drop'].forEach(function (ev) {
    dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.remove('is-dragover'); });
  });
  dz.addEventListener('drop', function (e) {
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) { try { var dt = new DataTransfer(); dt.items.add(f); input.files = dt.files; } catch (x) { /* 舊瀏覽器 */ } pick(f); }
  });

  // 逾時後後端可能仍在處理：問一下有沒有新的草稿，有就直接去確認頁（S4）
  function checkResult(ev) {
    var cb = ev.currentTarget, none = '尚未產生結果，請重新上傳。';
    cb.disabled = true;
    window.Api.get('/profiles/latest').then(function (res) {
      if (res && res.status === 'draft') { location.href = 'review.html'; return; }
      $('error-msg').textContent = none; cb.disabled = false;
    }, function (err) {
      $('error-msg').textContent = (err && err.code === 'NOT_FOUND') ? none : window.Api.friendlyMessage(err);
      cb.disabled = false;
    });
  }

  function showError(err) {
    var code = err && err.code;
    var m = MSG[code];
    $('error-title').textContent = m ? m[0] : '上傳沒有成功';
    // 已知錯誤碼用本頁白話文字；其餘用 api.js 帶回的訊息（逾時、網路等）
    $('error-msg').textContent = m ? m[1] : window.Api.friendlyMessage(err);
    show('error-link', code === 'CONSENT_REQUIRED');
    show('error-box', true);
    var old = $('check-result-btn'); if (old) old.remove();
    if (code === 'TIMEOUT') {   // 按鈕用原生 DOM 建立（本頁沒有載入 ui.js）
      var cb = document.createElement('button');
      cb.type = 'button'; cb.id = 'check-result-btn'; cb.className = 'btn btn--secondary'; cb.textContent = '檢查結果';
      cb.addEventListener('click', checkResult);
      $('error-box').querySelector('.u-actions').appendChild(cb);
    }
  }

  function setBusy(on) {
    busy = on; refresh();
    show('busy', on); show('form-area', !on);
    btn.classList.toggle('is-loading', on);
  }

  btn.addEventListener('click', function () {
    if (!chosen || busy) return;
    show('error-box', false);
    var fd = new FormData();
    fd.append('file', chosen);
    var names = $('name-input').value.trim();
    if (names) fd.append('name_variants', names);
    setBusy(true);
    window.Api.upload('/resumes', fd, { timeout: 100000 }).then(function (res) {
      setBusy(false);
      var flags = (res && res.parsed_json && res.parsed_json.missing_flags || []).length;
      $('success-msg').textContent = flags
        ? '有 ' + flags + ' 項資料需要你在下一步確認或補充。'
        : '下一步請確認解析出來的內容是否正確。';
      show('form-area', false); show('success-box', true);
      if (REDIRECT_TO_REVIEW) location.href = 'review.html';
    }, function (err) {
      setBusy(false);        // 保留頁面，可重新上傳
      show('form-area', true);
      showError(err);
    });
  });

  window.Auth.requireLogin().then(function (ok) { if (ok) { refresh(); } });
})();
