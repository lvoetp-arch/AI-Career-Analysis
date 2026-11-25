/* UI 工具：動態內容一律用文字節點，不使用字串拼 HTML。 */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var ICONS = {
    info: ['circle:12,12,9', 'M12 11v6', 'M12 7.5v.01'],
    warn: ['M12 3l10 18H2z', 'M12 10v5', 'M12 18v.01'],
    error: ['circle:12,12,9', 'M8 8l8 8', 'M16 8l-8 8'],
    success: ['circle:12,12,9', 'M7.5 12.5l3 3 6-7'],
    empty: ['M4 5h16v14H4z', 'M4 12h5l1 2h4l1-2h5'],
    file: ['M6 3h8l4 4v14H6z', 'M14 3v4h4', 'M9 13h6', 'M9 17h6']
  };

  function icon(kind) {
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2.2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    (ICONS[kind] || ICONS.info).forEach(function (d) {
      var p;
      if (d.indexOf('circle:') === 0) {
        var v = d.slice(7).split(',');
        p = document.createElementNS(NS, 'circle');
        p.setAttribute('cx', v[0]); p.setAttribute('cy', v[1]); p.setAttribute('r', v[2]);
      } else {
        p = document.createElementNS(NS, 'path'); p.setAttribute('d', d);
      }
      svg.appendChild(p);
    });
    return svg;
  }

  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k.slice(0, 2) === 'on' && typeof v === 'function') n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    });
    (children || []).forEach(function (c) {
      if (c != null) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return n;
  }

  var DEFAULTS = {
    loading: ['處理中，請稍候', 'status'],
    empty: ['這裡還沒有內容', null],
    error: ['出了點問題', 'alert'],
    success: ['完成了', 'status']
  };

  /* renderState(container, 'loading'|'empty'|'error'|'success', {title, message, actionText, onAction}) */
  function renderState(container, state, o) {
    o = o || {};
    var d = DEFAULTS[state];
    if (!d) throw new Error('unknown state: ' + state);
    var kids = [state === 'loading' ? el('span', { class: 'spinner', 'aria-hidden': 'true' }) : icon(state === 'empty' ? 'empty' : state),
      el('p', { class: 'state__title', text: o.title || d[0] })];
    if (o.message) kids.push(el('p', { class: 'state__msg', text: o.message }));
    if (o.actionText) kids.push(el('button', { type: 'button', class: 'btn btn--secondary', text: o.actionText, onclick: o.onAction }));
    container.replaceChildren(el('div', { class: 'state state--' + state, role: d[1] }, kids));
  }

  var region;
  function toast(message, o) {
    o = o || {};
    var type = o.type || 'info';
    if (!region) {
      region = el('div', { class: 'toast-region', 'aria-live': 'polite', 'aria-atomic': 'false' });
      document.body.appendChild(region);
    }
    var t = el('div', { class: 'toast toast--' + type }, [
      el('p', { class: 'toast__msg', text: message }),
      el('button', { type: 'button', class: 'toast__close', 'aria-label': '關閉通知', text: '×', onclick: function () { t.remove(); } })
    ]);
    region.appendChild(t);
    setTimeout(function () { t.remove(); }, o.duration || (type === 'error' ? 8000 : 5000));
    return t;
  }

  /* confirmDialog({title, message, confirmText, cancelText, danger}) → Promise<boolean> */
  function confirmDialog(o) {
    return new Promise(function (resolve) {
      var cancelBtn = el('button', { type: 'submit', value: 'cancel', class: 'btn btn--secondary', text: o.cancelText || '取消', autofocus: true });
      var okBtn = el('button', { type: 'submit', value: 'ok', class: 'btn ' + (o.danger ? 'btn--danger' : 'btn--primary'), text: o.confirmText || '確定' });
      var dlg = el('dialog', { class: 'dialog', 'aria-labelledby': 'dlg-title' }, [
        el('h2', { class: 'dialog__title', id: 'dlg-title', text: o.title || '請確認' }),
        el('p', { text: o.message || '' }),
        el('form', { method: 'dialog', class: 'dialog__actions' }, [cancelBtn, okBtn])
      ]);
      dlg.addEventListener('close', function () { var ok = dlg.returnValue === 'ok'; dlg.remove(); resolve(ok); });
      document.body.appendChild(dlg);
      dlg.showModal();
    });
  }

  function initNav() {
    document.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('.nav-toggle');
      if (!b) return;
      var nav = document.getElementById(b.getAttribute('aria-controls'));
      var open = nav.classList.toggle('is-open');
      b.setAttribute('aria-expanded', String(open));
    });
  }

  /* initDropzone(zone, {onFiles(fileList)})；檔案驗證交給各頁面 */
  function initDropzone(zone, o) {
    var input = zone.querySelector('input[type="file"]');
    function handle(files) { if (files && files.length && o && o.onFiles) o.onFiles(files); }
    ['dragenter', 'dragover'].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add('is-dragover'); });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.remove('is-dragover'); });
    });
    zone.addEventListener('drop', function (e) { handle(e.dataTransfer.files); });
    input.addEventListener('change', function () { handle(input.files); });
  }

  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.marquee__pause');
    if (!b) return;
    var m = b.closest('.marquee'), p = m.classList.toggle('is-paused');
    b.setAttribute('aria-pressed', String(p)); b.textContent = p ? '播放' : '暫停';
  });
  initNav();
  window.UI = { el: el, icon: icon, renderState: renderState, toast: toast, confirmDialog: confirmDialog, initDropzone: initDropzone };
})();
