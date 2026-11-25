/* js/review.js — 確認與編修頁（S4）。
   使用者與 LLM 的內容一律以 textContent / value 渲染，不使用 innerHTML；
   不把履歷資料存進任何瀏覽器儲存；離開頁面前若有未儲存變更會提醒。
   事件處理一律用「路徑字串」讀寫 state.data，不持有項目物件（儲存時會整份重建）。 */
(function () {
  'use strict';
  var NEXT_PAGE = 'assess.html';   // S5 完成前此頁可能還不存在
  var YM = /^[0-9]{4}(-(0[1-9]|1[0-2]))?$/;
  var SOFT = '履歷沒寫這項，可以在這裡補充（不補也可以）';
  var H = window.UI.el;
  var $ = function (id) { return document.getElementById(id); };
  var narrow = window.matchMedia('(max-width: 767px)');
  var uidN = 0, idN = 0;
  function nid() { uidN += 1; return 'rv' + uidN; }
  function newId(p) { idN += 1; return p + '-n' + Date.now().toString(36) + idN; }
  function deep(o) { return JSON.parse(JSON.stringify(o)); }

  var OPT = {
    kind: [['employment', '受僱工作'], ['self_employed', '自營／個人創業'], ['freelance', '接案'], ['other', '其他']],
    emp: [['full_time', '全職'], ['part_time', '兼職'], ['contract', '約聘'], ['internship', '實習'], ['freelance', '接案'], ['unknown', '未標示']],
    mode: [['onsite', '到班'], ['remote', '遠端'], ['hybrid', '混合']],
    remote: [['unknown', '未填寫'], ['preferred', '偏好遠端'], ['willing', '可接受遠端'], ['not_willing', '不接受遠端']],
    edu: [['graduated', '畢業'], ['in_progress', '就學中'], ['dropped', '肄業'], ['unknown', '未標示']],
    ptype: [['website', '網站'], ['app', '應用程式'], ['code_repo', '程式碼庫'], ['design', '設計'], ['writing', '文章'], ['video', '影片'], ['document', '文件'], ['other', '其他']],
    period: [['monthly', '月薪'], ['yearly', '年薪'], ['hourly', '時薪'], ['negotiable', '面議']]
  };

  var state = { id: null, status: null, data: null, baseline: '', flags: [], busy: false, saved: false, openIds: new Set(), activePaths: new Set() };

  /* ---------- 路徑工具 ---------- */
  function toKeys(p) { return (String(p).match(/[^.\[\]]+/g) || []).map(function (k) { return /^\d+$/.test(k) ? Number(k) : k; }); }
  function getAt(o, p) {
    var ks = toKeys(p), cur = o;
    for (var i = 0; i < ks.length; i++) { if (cur == null) return undefined; cur = cur[ks[i]]; }
    return cur;
  }
  function setAt(o, p, v) {
    var ks = toKeys(p), cur = o;
    for (var i = 0; i < ks.length - 1; i++) {
      if (cur[ks[i]] == null) cur[ks[i]] = typeof ks[i + 1] === 'number' ? [] : {};
      cur = cur[ks[i]];
    }
    cur[ks[ks.length - 1]] = v;
  }
  function delAt(o, p) {
    var ks = toKeys(p), cur = getAt(o, ks.slice(0, -1).join('.').replace(/\.(\d+)/g, '[$1]'));
    if (cur != null) delete cur[ks[ks.length - 1]];
  }
  function isEmpty(v) {
    return v == null || (typeof v === 'string' && v.trim() === '') ||
      (Array.isArray(v) && v.every(function (x) { return typeof x !== 'object' && isEmpty(x); }));
  }

  /* ---------- 整理（送出前）與比較 ---------- */
  var REQ = {
    'work_experience[]': ['id', 'company', 'title'], 'education[]': ['id', 'school'], 'skills[]': ['id', 'name'],
    'projects[]': ['id', 'name'], 'portfolio[]': ['id', 'title'], 'certifications[]': ['name'], 'languages[]': ['language'],
    'awards[]': ['name'], 'basics.links[]': ['url'], 'autobiography.sections[]': ['text'], 'job_preferences.salary_expectation': ['raw_text']
  };
  var LISTS = ['work_experience', 'education', 'skills', 'projects', 'portfolio', 'certifications', 'languages', 'awards', 'basics.links', 'autobiography.sections'];
  var SKIP_BLANK = { id: 1, source_text: 1, related_project_ids: 1, linked_ids: 1, url_truncated: 1, related_project_id: 1,
    employment_type: 1, kind: 1, status: 1, type: 1, source_section: 1, language: 0 };

  function isBlankItem(it) {
    if (it == null || typeof it !== 'object') return isEmpty(it);
    return Object.keys(it).every(function (k) {
      if (SKIP_BLANK[k]) return true;
      var v = it[k];
      if (typeof v === 'string') return v.trim() === '';
      if (Array.isArray(v)) return v.every(function (x) { return typeof x === 'string' ? x.trim() === '' : false; });
      if (typeof v === 'number') return false;
      return v == null || v === false;
    });
  }
  function walk(node, path) {
    if (Array.isArray(node)) {
      if (node.every(function (x) { return typeof x === 'string'; })) {
        for (var j = node.length - 1; j >= 0; j--) { node[j] = node[j].trim(); if (!node[j]) node.splice(j, 1); }
        return;
      }
      node.forEach(function (x, i) { walk(x, path + '[' + i + ']'); });
    } else if (node && typeof node === 'object') {
      var req = REQ[path.replace(/\[\d+\]/g, '[]')] || [];
      Object.keys(node).forEach(function (k) {
        var v = node[k];
        if (typeof v === 'string') { v = v.trim(); if (v === '' && req.indexOf(k) < 0) v = null; node[k] = v; }
        else walk(v, path ? path + '.' + k : k);
      });
    }
  }
  function clean(d) {
    var c = deep(d);
    walk(c, '');
    LISTS.forEach(function (p) {
      var a = getAt(c, p);
      if (Array.isArray(a)) setAt(c, p, a.filter(function (it) { return !isBlankItem(it); }));
    });
    var jp = c.job_preferences;
    if (jp) {
      var s = jp.salary_expectation;
      if (s && isEmpty(s.raw_text) && s.min == null && s.max == null && isEmpty(s.currency) && !s.period) jp.salary_expectation = null;
      else if (s && s.raw_text == null) s.raw_text = '';
      var a = jp.availability;
      if (a && isEmpty(a.earliest_start) && isEmpty(a.notice_period)) jp.availability = null;
    }
    return c;
  }
  function canon(d) {   // 比較用：null、false、空陣列視為「沒有」
    return JSON.stringify(clean(d), function (k, v) { return (v === null || v === false || (Array.isArray(v) && v.length === 0)) ? undefined : v; });
  }
  function isDirty() { return !!state.data && canon(state.data) !== state.baseline; }

  /* ---------- 單欄檢查 ---------- */
  function urlProblem(v) {
    v = (v || '').trim();
    if (!v) return '';
    try { var u = new URL(v); if (u.protocol === 'http:' || u.protocol === 'https:') return ''; } catch (e) { /* 下面統一回應 */ }
    return '網址需以 http:// 或 https:// 開頭';
  }
  function checkValue(kind, v) {
    v = (v || '').trim();
    if (kind === 'ym') return v && !YM.test(v) ? '日期請填 YYYY-MM 或 YYYY（例如 2024-03）' : '';
    if (kind === 'url') return urlProblem(v);
    return '';
  }
  function setFieldError(ctl, msg) {
    var wrap = ctl.closest('.field'); if (!wrap) return;
    var p = wrap.querySelector('.field__error');
    wrap.classList.toggle('field--error', !!msg);
    if (p) { p.textContent = msg || ''; p.hidden = !msg; }
    ctl.setAttribute('aria-invalid', msg ? 'true' : 'false');
  }

  /* ---------- 表單元件 ---------- */
  function softHint(key, path) { return H('p', { class: 'rv-soft', 'data-soft': key, 'data-soft-path': path || '', text: SOFT }); }

  function field(path, label, o) {
    o = o || {};
    var id = nid(), cur = getAt(state.data, path), kind = o.type || 'text', ctl;
    if (kind === 'textarea') ctl = H('textarea', { class: 'textarea', id: id, rows: o.rows || 4, maxlength: o.max });
    else if (kind === 'select') {
      var opts = ((o.optional || o.nullable) ? [['', o.nullable ? '（未填）' : '（未選）']] : []).concat(o.options);
      ctl = H('select', { class: 'select', id: id }, opts.map(function (p) { return H('option', { value: p[0], text: p[1] }); }));
    } else {
      ctl = H('input', { class: 'input', id: id, type: kind === 'number' ? 'number' : 'text',
        inputmode: kind === 'ym' ? 'numeric' : (kind === 'url' ? 'url' : (kind === 'number' ? 'decimal' : null)),
        maxlength: kind === 'ym' ? 7 : o.max, placeholder: o.placeholder || (kind === 'ym' ? 'YYYY-MM' : null),
        autocomplete: 'off', list: o.list, step: kind === 'number' ? (o.step || 'any') : null });
    }
    ctl.value = cur == null ? '' : String(cur);
    ctl.setAttribute('data-path', path);
    ctl.addEventListener(kind === 'select' ? 'change' : 'input', function () {
      var v = ctl.value;
      if (kind === 'number') v = v === '' ? null : Number(v);
      if (kind === 'select' && v === '') { if (o.nullable) setAt(state.data, path, null); else delAt(state.data, path); }
      else setAt(state.data, path, v);
      setFieldError(ctl, '');
      if (o.onSet) o.onSet(v);
      if (o.counter) o.counter.textContent = ctl.value.length + ' / ' + o.max;
      state.saved = false; touch();
    });
    if (kind === 'ym' || kind === 'url') ctl.addEventListener('blur', function () { setFieldError(ctl, checkValue(kind, ctl.value)); });
    if (o.onCommit) ctl.addEventListener('change', o.onCommit);
    var kids = [H('div', { class: 'rv-labelrow' }, [H('label', { class: 'field__label', for: id, text: label + (o.req ? '（必填）' : '') }), H('span', { class: 'rv-flagslot', 'data-for': path })])];
    if (o.hint) { kids.push(H('p', { class: 'field__hint', id: id + '-h', text: o.hint })); ctl.setAttribute('aria-describedby', id + '-h'); }
    kids.push(ctl);
    if (o.counter) { o.counter.textContent = ctl.value.length + ' / ' + o.max; kids.push(o.counter); }
    kids.push(H('p', { class: 'field__error', role: 'alert', hidden: true }));
    if (o.soft) kids.push(softHint(o.soft, path));
    return H('div', { class: 'field' }, kids);
  }

  function check(path, label, o) {
    o = o || {};
    var inp = H('input', { type: 'checkbox', id: nid() });
    inp.checked = !!getAt(state.data, path);
    inp.addEventListener('change', function () {
      setAt(state.data, path, inp.checked);
      if (o.onSet) o.onSet(inp.checked);
      state.saved = false; touch();
    });
    return H('label', { class: 'rv-check' }, [inp, H('span', { text: label })]);
  }

  function checkGroup(path, label, options) {
    var arr = getAt(state.data, path); if (!Array.isArray(arr)) { arr = []; setAt(state.data, path, arr); }
    var boxes = options.map(function (op) {
      var inp = H('input', { type: 'checkbox', id: nid() });
      inp.checked = arr.indexOf(op[0]) >= 0;
      inp.addEventListener('change', function () {
        var a = getAt(state.data, path), i = a.indexOf(op[0]);
        if (inp.checked && i < 0) a.push(op[0]); else if (!inp.checked && i >= 0) a.splice(i, 1);
        state.saved = false; touch();
      });
      return H('label', { class: 'rv-check' }, [inp, H('span', { text: op[1] })]);
    });
    return H('fieldset', { class: 'rv-fieldset' }, [H('legend', { text: label }), H('div', { class: 'rv-checks' }, boxes)]);
  }

  function strList(path, label, o) {
    o = o || {};
    var box = H('div', { class: 'rv-strlist' });
    function draw(focusLast) {
      var arr = getAt(state.data, path); if (!Array.isArray(arr)) { arr = []; setAt(state.data, path, arr); }
      box.replaceChildren();
      arr.forEach(function (v, i) {
        var inp = H('input', { class: 'input', id: nid(), type: 'text', autocomplete: 'off', 'aria-label': label + ' ' + (i + 1) });
        inp.value = v == null ? '' : String(v);
        inp.setAttribute('data-path', path + '[' + i + ']');
        inp.addEventListener('input', function () { getAt(state.data, path)[i] = inp.value; state.saved = false; touch(); });
        box.appendChild(H('div', { class: 'rv-strrow' }, [inp, H('button', { type: 'button', class: 'btn btn--secondary btn--small', text: '刪除', 'aria-label': '刪除' + label + ' ' + (i + 1),
          onclick: function () { getAt(state.data, path).splice(i, 1); draw(); state.saved = false; touch(); } })]));
      });
      box.appendChild(H('button', { type: 'button', class: 'btn btn--secondary btn--small', text: '新增一項',
        onclick: function () { getAt(state.data, path).push(''); draw(true); state.saved = false; touch(); } }));
      if (focusLast) { var ins = box.querySelectorAll('input.input'); if (ins.length) ins[ins.length - 1].focus(); }
    }
    draw();
    var kids = [H('legend', { text: label }), H('span', { class: 'rv-flagslot', 'data-for': path })];
    if (o.soft) kids.push(softHint(o.soft, path));
    kids.push(box);
    return H('fieldset', { class: 'rv-fieldset' }, kids);
  }

  /* 可收合的項目清單。add / delete 之後整個區塊重繪（保留已展開的項目）。 */
  var curSec = null;
  function itemList(arrPath, o) {
    var arr = getAt(state.data, arrPath); if (!Array.isArray(arr)) { arr = []; setAt(state.data, arrPath, arr); }
    var box = H('div', { class: 'rv-items' });
    var idxs = o.indices ? o.indices(arr) : arr.map(function (x, i) { return i; });
    idxs.forEach(function (i) {
      var base = arrPath + '[' + i + ']', item = arr[i];
      var key = item.id || (base);
      var det = H('details', { class: 'rv-item', 'data-key': key });
      det.open = state.openIds.has(key);
      var sumText = H('span', { class: 'rv-item__title' });
      curSec.updaters.push(function () { var it = getAt(state.data, base); sumText.textContent = (it && o.title(it)) || o.empty; });
      sumText.textContent = o.title(item) || o.empty;
      var del = H('button', { type: 'button', class: 'btn btn--danger btn--small', text: '刪除這' + o.unit, onclick: function () {
        var it = getAt(state.data, base);
        window.UI.confirmDialog({ title: '刪除這' + o.unit + '？', message: '「' + (o.title(it) || o.empty) + '」會從你的內容中移除，儲存後無法復原。', confirmText: '刪除', cancelText: '取消', danger: true }).then(function (ok) {
          if (!ok) return;
          var a = getAt(state.data, arrPath), gone = a.splice(i, 1)[0];
          if (gone && gone.id) cascade(gone.id);
          state.openIds.delete(key); state.saved = false;
          renderSection(secOf(arrPath)); touch();
        });
      } });
      det.appendChild(H('summary', {}, [sumText]));
      det.appendChild(H('div', { class: 'rv-item__body' }, [o.render(i, base), H('div', { class: 'rv-item__actions' }, [del])]));
      box.appendChild(det);
    });
    var wrap = H('div', { class: 'rv-items-wrap' }, [box]);
    wrap.appendChild(H('button', { type: 'button', class: 'btn btn--secondary', text: o.addText || ('新增' + o.unit), onclick: function () {
      var it = o.blank(); getAt(state.data, arrPath).push(it);
      var k = it.id || (arrPath + '[' + (getAt(state.data, arrPath).length - 1) + ']');
      state.openIds.add(k); state.saved = false;
      var sec = secOf(arrPath); renderSection(sec); touch();
      var d = sec.body.querySelector('details[data-key="' + k + '"]');
      if (d) { var f = d.querySelector('.input, .textarea'); if (f) { d.scrollIntoView({ block: 'nearest' }); f.focus(); } }
    } }));
    return wrap;
  }
  function cascade(id) {   // 刪除項目後，清掉其他地方對它的引用
    var d = state.data;
    (d.work_experience || []).forEach(function (w) { if (Array.isArray(w.related_project_ids)) w.related_project_ids = w.related_project_ids.filter(function (x) { return x !== id; }); });
    (d.skills || []).forEach(function (s) { if (Array.isArray(s.linked_ids)) s.linked_ids = s.linked_ids.filter(function (x) { return x !== id; }); });
    (d.awards || []).forEach(function (a) { if (a.related_project_id === id) a.related_project_id = null; });
  }
  function rng(a, b) { a = a || ''; b = b || ''; return (a || b) ? (a + ' – ' + b).trim() : ''; }
  function row(n, kids) { return H('div', { class: 'rv-row rv-row--' + n }, kids); }

  /* ---------- 各區塊 ---------- */
  function renderPrefs() {
    var jp = 'job_preferences';
    return frag([
      strList(jp + '.desired_roles', '希望職稱', { soft: 'roles' }),
      strList(jp + '.desired_job_categories', '希望職類'),
      strList(jp + '.desired_industries', '希望產業'),
      checkGroup(jp + '.employment_types', '希望的僱用型態', OPT.emp.filter(function (x) { return x[0] !== 'unknown'; })),
      checkGroup(jp + '.work_modes', '希望的工作方式', OPT.mode),
      field(jp + '.remote_willingness', '遠端意願', { type: 'select', options: OPT.remote, soft: 'remote' }),
      strList(jp + '.preferred_locations', '希望地點', { soft: 'locations' }),
      field(jp + '.shift', '班別'),
      field(jp + '.leave_and_schedule', '休假與工時', { type: 'textarea', rows: 2, hint: '只寫安排本身（例如「週休二日」「彈性上下班」），不需要寫原因。', soft: 'leave' }),
      field(jp + '.salary_expectation.raw_text', '希望待遇（原文）', { hint: '例如「面議」「月薪 5 萬以上」', soft: 'salary' }),
      row(3, [field(jp + '.salary_expectation.min', '待遇下限', { type: 'number' }), field(jp + '.salary_expectation.max', '待遇上限', { type: 'number' }),
        field(jp + '.salary_expectation.currency', '幣別', { placeholder: 'TWD' })]),
      field(jp + '.salary_expectation.period', '計薪方式', { type: 'select', options: OPT.period, nullable: true }),
      row(2, [field(jp + '.availability.earliest_start', '最快到職'), field(jp + '.availability.notice_period', '需提前通知的時間')]),
      strList(jp + '.other_conditions', '其他條件')
    ]);
  }
  function renderWork() {
    return frag([softHint('expproj'), itemList('work_experience', {
      unit: '筆經歷', empty: '（未命名經歷）', addText: '新增工作經歷',
      title: function (w) { var r = rng(w.start, w.is_current ? '在職中' : w.end); return [w.company, w.title].filter(Boolean).join('・') + (r ? '（' + r + '）' : ''); },
      blank: function () { return { id: newId('work'), company: '', title: '', employment_type: 'unknown', kind: 'employment', is_current: false, responsibilities: [], achievements: [], tools_used: [], tags: [] }; },
      render: function (i, b) {
        var wrap = H('div', { class: 'rv-item__fields', style: 'display:grid;gap:var(--s4)' });
        var endField = field(b + '.end', '結束', { type: 'ym' });
        function syncEnd(byUser) {   // 載入時只鎖欄位，不改資料；使用者勾選時才清掉結束日期
          var cur = getAt(state.data, b + '.is_current'), inp = endField.querySelector('[data-path]');
          if (cur && byUser === true) { setAt(state.data, b + '.end', null); inp.value = ''; setFieldError(inp, ''); }
          inp.disabled = !!cur;
        }
        var chk = check(b + '.is_current', '目前仍在這份工作', { onSet: function () { syncEnd(true); } });
        [row(2, [field(b + '.company', '公司／單位', { req: true }), field(b + '.title', '職稱', { req: true })]),
          row(2, [field(b + '.kind', '性質', { type: 'select', options: OPT.kind, optional: true }), field(b + '.employment_type', '僱用型態', { type: 'select', options: OPT.emp, optional: true })]),
          row(2, [field(b + '.start', '開始', { type: 'ym' }), endField]), chk,
          row(2, [field(b + '.location', '地點'), field(b + '.industry', '產業')]),
          row(2, [field(b + '.job_category', '職類'), field(b + '.company_size', '公司規模')]),
          field(b + '.management_scope', '管理範圍'),
          strList(b + '.responsibilities', '工作內容'), strList(b + '.achievements', '成果'),
          strList(b + '.tools_used', '使用工具'), strList(b + '.tags', '標籤')].forEach(function (n) { wrap.appendChild(n); });
        syncEnd(false);
        return wrap;
      }
    })]);
  }
  function renderEdu() {
    return itemList('education', {
      unit: '筆學歷', empty: '（未命名學歷）', addText: '新增學歷',
      title: function (e) { var r = rng(e.start, e.end); return [e.school, e.degree, e.field].filter(Boolean).join('・') + (r ? '（' + r + '）' : ''); },
      blank: function () { return { id: newId('edu'), school: '', status: 'unknown' }; },
      render: function (i, b) {
        return frag([field(b + '.school', '學校', { req: true }), row(2, [field(b + '.degree', '學位'), field(b + '.field', '科系')]),
          row(2, [field(b + '.start', '開始', { type: 'ym' }), field(b + '.end', '結束', { type: 'ym' })]),
          field(b + '.status', '狀態', { type: 'select', options: OPT.edu, optional: true })]);
      }
    });
  }
  function renderSkills() {
    var skills = state.data.skills || [], groups = new Map();
    skills.forEach(function (s) { var g = (s.group || '').trim(); if (!groups.has(g)) groups.set(g, true); });
    if (!groups.size) groups.set('', true);
    var dl = H('datalist', { id: 'rv-groups' });
    groups.forEach(function (v, g) { if (g) dl.appendChild(H('option', { value: g })); });
    var kids = [softHint('skills'), dl];
    groups.forEach(function (v, g) {
      kids.push(H('h3', { class: 'rv-groupname', text: g || '未分組' }));
      kids.push(itemList('skills', {
        unit: '項專長', empty: '（未命名專長）', addText: '新增專長' + (g ? '到「' + g + '」' : ''),
        indices: function (arr) { var out = []; arr.forEach(function (s, i) { if ((s.group || '').trim() === g) out.push(i); }); return out; },
        title: function (s) { return s.name; },
        blank: function () { return { id: newId('skill'), name: '', group: g || null }; },
        render: function (i, b) {
          return frag([field(b + '.name', '專長名稱', { req: true }),
            field(b + '.group', '分組', { list: 'rv-groups', hint: '改完分組後，離開這個欄位時會重新分組。', onCommit: function () { renderSection(secOf('skills')); touch(); } }),
            row(2, [field(b + '.category', '類別'), field(b + '.stated_level_text', '履歷寫的程度')])]);
        }
      }));
    });
    return frag(kids);
  }
  function renderProjects() {
    return frag([softHint('expproj'), itemList('projects', {
      unit: '個專案', empty: '（未命名專案）', addText: '新增專案',
      title: function (p) { var r = rng(p.start, p.end); return (p.name || '') + (r ? '（' + r + '）' : ''); },
      blank: function () { return { id: newId('project'), name: '', tech: [], outcomes: [], source_section: 'other' }; },
      render: function (i, b) {
        return frag([field(b + '.name', '專案名稱', { req: true }), field(b + '.role', '角色'), field(b + '.description', '說明', { type: 'textarea', rows: 4 }),
          row(2, [field(b + '.start', '開始', { type: 'ym' }), field(b + '.end', '結束', { type: 'ym' })]),
          field(b + '.url', '網址', { type: 'url', placeholder: 'https://' }), strList(b + '.tech', '使用技術'), strList(b + '.outcomes', '成果')]);
      }
    })]);
  }
  function renderLinks() {
    return frag([field('basics.headline', '履歷標題'),
      H('h3', { class: 'rv-groupname', text: '個人連結' }),
      itemList('basics.links', {
        unit: '個連結', empty: '（未命名連結）', addText: '新增連結',
        title: function (l) { return l.label || l.url; }, blank: function () { return { label: '', url: '' }; },
        render: function (i, b) { return row(2, [field(b + '.label', '名稱'), field(b + '.url', '網址', { type: 'url', req: true, placeholder: 'https://' })]); }
      }),
      H('h3', { class: 'rv-groupname', text: '作品集' }),
      itemList('portfolio', {
        unit: '件作品', empty: '（未命名作品）', addText: '新增作品',
        title: function (p) { return p.title; }, blank: function () { return { id: newId('portfolio'), title: '', type: 'other', url_truncated: false }; },
        render: function (i, b) {
          return frag([field(b + '.title', '作品名稱', { req: true }), field(b + '.type', '類型', { type: 'select', options: OPT.ptype, optional: true }),
            field(b + '.url', '網址', { type: 'url', placeholder: 'https://', onSet: function (v) { if (v && getAt(state.data, b + '.url_truncated')) setAt(state.data, b + '.url_truncated', false); } }),
            field(b + '.description', '說明', { type: 'textarea', rows: 3 })]);
        }
      })]);
  }
  function renderCerts() {
    return frag([
      H('h3', { class: 'rv-groupname', text: '證照' }),
      itemList('certifications', { unit: '張證照', empty: '（未命名證照）', addText: '新增證照', title: function (c) { return c.name; }, blank: function () { return { name: '' }; },
        render: function (i, b) { return frag([field(b + '.name', '證照名稱', { req: true }), row(2, [field(b + '.issuer', '發證單位'), field(b + '.date', '取得日期', { type: 'ym' })])]); } }),
      H('h3', { class: 'rv-groupname', text: '語言' }),
      itemList('languages', { unit: '項語言', empty: '（未命名語言）', addText: '新增語言', title: function (l) { return [l.language, l.overall].filter(Boolean).join('・'); }, blank: function () { return { language: '' }; },
        render: function (i, b) {
          return frag([field(b + '.language', '語言', { req: true }), field(b + '.overall', '整體程度'),
            row(2, [field(b + '.listening', '聽'), field(b + '.speaking', '說')]), row(2, [field(b + '.reading', '讀'), field(b + '.writing', '寫')])]);
        } }),
      H('h3', { class: 'rv-groupname', text: '獎項' }),
      itemList('awards', { unit: '個獎項', empty: '（未命名獎項）', addText: '新增獎項', title: function (a) { return a.name; }, blank: function () { return { id: newId('award'), name: '' }; },
        render: function (i, b) { return frag([field(b + '.name', '獎項名稱', { req: true }), row(2, [field(b + '.year', '年度', { type: 'number', step: 1 }), field(b + '.issuer', '頒發單位')])]); } })]);
  }
  function renderBio() {
    return frag([
      field('autobiography.text', '自傳原文', { type: 'textarea', rows: 10, max: 6000, counter: H('p', { class: 'rv-count' }) }),
      H('h3', { class: 'rv-groupname', text: '自傳分段' }),
      itemList('autobiography.sections', { unit: '段', empty: '（未命名段落）', addText: '新增一段', title: function (s) { return s.heading || (s.text || '').slice(0, 24); }, blank: function () { return { heading: '', text: '' }; },
        render: function (i, b) { return frag([field(b + '.heading', '標題'), field(b + '.text', '內容', { type: 'textarea', rows: 6, req: true })]); } }),
      strList('autobiography.stated_goals', '自述的目標'), strList('autobiography.stated_motivations', '自述的動機'), strList('autobiography.stated_strengths', '自述的強項')]);
  }
  function frag(nodes) { var f = document.createDocumentFragment(); nodes.forEach(function (n) { if (n) f.appendChild(n); }); return f; }

  var SECS = [
    { id: 'prefs', title: '求職條件', prefix: ['job_preferences'], render: renderPrefs },
    { id: 'work', title: '工作經歷', prefix: ['work_experience'], render: renderWork, count: function (d) { return (d.work_experience || []).length + ' 筆'; } },
    { id: 'edu', title: '學歷', prefix: ['education'], render: renderEdu, count: function (d) { return (d.education || []).length + ' 筆'; } },
    { id: 'skills', title: '專長', prefix: ['skills'], render: renderSkills, count: function (d) { return (d.skills || []).length + ' 項'; } },
    { id: 'projects', title: '專案', prefix: ['projects'], render: renderProjects, count: function (d) { return (d.projects || []).length + ' 個'; } },
    { id: 'links', title: '作品集與連結', prefix: ['portfolio', 'basics'], render: renderLinks, count: function (d) { return (d.portfolio || []).length + ' 件作品'; } },
    { id: 'certs', title: '證照與語言', prefix: ['certifications', 'languages', 'awards'], render: renderCerts, count: function (d) { return (d.certifications || []).length + ' 證照・' + (d.languages || []).length + ' 語言'; } },
    { id: 'bio', title: '自傳', prefix: ['autobiography'], render: renderBio }
  ];
  function secOf(path) {
    var k = String(path).split(/[.\[]/)[0];
    return SECS.filter(function (s) { return s.prefix.indexOf(k) >= 0; })[0];
  }
  function buildShell() {
    var root = $('rv-sections'); root.replaceChildren();
    SECS.forEach(function (s) {
      s.updaters = []; s.countEl = H('span', { class: 'rv-sec__count' }); s.badgeEl = H('span', { class: 'rv-sec__badge', hidden: true });
      s.body = H('div', { class: 'accordion__body' });
      s.det = H('details', { class: 'accordion rv-sec', 'data-section': s.id }, [H('summary', {}, [H('span', { class: 'rv-sec__title', text: s.title }), s.countEl, s.badgeEl]), s.body]);
      root.appendChild(s.det);
    });
  }
  function renderSection(s) { curSec = s; s.updaters = []; s.body.replaceChildren(s.render()); }
  function renderAll() { SECS.forEach(renderSection); }

  /* 手機一次展開一個；項目展開狀態記在 openIds */
  document.addEventListener('toggle', function (e) {
    var t = e.target; if (!t || t.tagName !== 'DETAILS') return;
    if (t.classList.contains('rv-item')) { var k = t.getAttribute('data-key'); if (t.open) state.openIds.add(k); else state.openIds.delete(k); return; }
    if (t.classList.contains('rv-sec') && t.open && narrow.matches) {
      SECS.forEach(function (s) { if (s.det !== t && s.det.open) s.det.open = false; });
    }
  }, true);

  /* ---------- 提示：missing_flags 與空欄位 ---------- */
  function buildFlags(p) {
    return (p.missing_flags || []).map(function (f) {
      var fp = String(f.field_path || ''), m = /^(.*?)\[(\d+)\](.*)$/.exec(fp), ref = null;
      if (m) { var a = getAt(p, m[1]), it = Array.isArray(a) ? a[Number(m[2])] : null; ref = { arr: m[1], idx: Number(m[2]), id: it && it.id, rest: m[3] }; }
      return { path: fp, ref: ref, reason: f.reason, message: String(f.message || ''), orig: deep({ v: getAt(p, fp) === undefined ? null : getAt(p, fp) }).v };
    });
  }
  function flagPath(f) {   // 項目有 id 就跟著 id 走（刪除或新增後索引會變）
    if (!f.ref) return f.path;
    var a = getAt(state.data, f.ref.arr); if (!Array.isArray(a)) return null;
    var i = f.ref.idx;
    if (f.ref.id) { i = -1; a.forEach(function (x, j) { if (x && x.id === f.ref.id) i = j; }); }
    return (i < 0 || i >= a.length) ? null : f.ref.arr + '[' + i + ']' + f.ref.rest;
  }
  function flagActive(f, p) {
    var v = getAt(state.data, p);
    if (f.reason === 'missing' || f.reason === 'truncated') return isEmpty(v);
    return JSON.stringify(v == null ? null : v) === JSON.stringify(f.orig);
  }
  function findCtl(path) { var all = document.querySelectorAll('[data-path]'); for (var i = 0; i < all.length; i++) if (all[i].getAttribute('data-path') === path) return all[i]; return null; }
  function findSlot(path) { var all = document.querySelectorAll('.rv-flagslot'); for (var i = 0; i < all.length; i++) if (all[i].getAttribute('data-for') === path) return all[i]; return null; }
  function flagBtn(path, msg) { return H('button', { type: 'button', class: 'rv-flag', text: '提示：' + msg, onclick: function () { jumpTo(path); } }); }

  function refreshFlags() {
    document.querySelectorAll('.rv-flagslot').forEach(function (s) { s.replaceChildren(); });
    var act = [], paths = new Set(), perSec = new Map();
    state.flags.forEach(function (f) {
      var p = flagPath(f); if (!p || !flagActive(f, p)) return;
      act.push({ f: f, p: p }); paths.add(p);
      var sec = secOf(p); if (sec) perSec.set(sec, (perSec.get(sec) || 0) + 1);
      var slot = findSlot(p); if (slot) slot.appendChild(flagBtn(p, f.message));
    });
    state.activePaths = paths;
    var list = $('rv-flaglist'); list.replaceChildren();
    if (act.length) {
      list.appendChild(H('div', { class: 'alert alert--warn', role: 'note' }, [H('div', {}, [
        H('p', { class: 'alert__title', text: '有 ' + act.length + ' 項內容系統不太確定' }),
        H('p', { text: '點一下可跳到該欄位。這只是提示，不補也可以。' }),
        H('div', { class: 'rv-flaglist' }, act.map(function (a) { return flagBtn(a.p, a.f.message); }))])]));
    }
    SECS.forEach(function (s) { var n = perSec.get(s) || 0; s.badgeEl.hidden = !n; s.badgeEl.textContent = n + ' 項提示'; });
  }
  function softEmpty(key) {
    var d = state.data, jp = d.job_preferences || {};
    switch (key) {
      case 'roles': return isEmpty(jp.desired_roles);
      case 'salary': var s = jp.salary_expectation; return !s || (isEmpty(s.raw_text) && s.min == null && s.max == null);
      case 'remote': return !jp.remote_willingness || jp.remote_willingness === 'unknown';
      case 'leave': return isEmpty(jp.leave_and_schedule);
      case 'locations': return isEmpty(jp.preferred_locations);
      case 'skills': return !(d.skills || []).length;
      case 'expproj': return !(d.work_experience || []).length && !(d.projects || []).length;
    }
    return false;
  }
  function refreshSoft() {
    document.querySelectorAll('[data-soft]').forEach(function (h) {
      var p = h.getAttribute('data-soft-path');
      h.hidden = !softEmpty(h.getAttribute('data-soft')) || (p && state.activePaths.has(p));
    });
  }

  /* ---------- 狀態列、錯誤顯示 ---------- */
  function updateStatus() {
    var st = $('rv-status'), dirty = isDirty();
    st.classList.toggle('is-dirty', dirty);
    st.textContent = state.busy ? st.textContent : (dirty ? '有尚未儲存的變更' : (state.saved ? '已儲存' : ''));
  }
  function touch() {
    refreshFlags(); refreshSoft();
    SECS.forEach(function (s) { s.updaters.forEach(function (f) { f(); }); s.countEl.textContent = s.count ? s.count(state.data) : ''; });
    updateStatus();
  }
  function setBusy(on, label) {
    state.busy = on;
    ['btn-save', 'btn-confirm'].forEach(function (id) { $(id).disabled = on; $(id).setAttribute('aria-busy', String(on)); });
    if (on) $('rv-status').textContent = label || '處理中…'; else updateStatus();
  }
  function setBanner(type, title, items) {
    var b = $('rv-banner'); b.replaceChildren();
    if (!title) return;
    var inner = [H('p', { class: 'alert__title', text: title })];
    if (items && items.length) inner.push(H('ul', {}, items.map(function (t) { return H('li', { text: t }); })));
    b.appendChild(H('div', { class: 'alert alert--' + type, role: type === 'error' ? 'alert' : 'note' }, [H('div', {}, inner)]));
  }
  function clearErrors() {
    document.querySelectorAll('.field--error').forEach(function (w) { var c = w.querySelector('[data-path]'); if (c) setFieldError(c, ''); else w.classList.remove('field--error'); });
    setBanner('', '');
  }
  function jumpToEl(el) {
    for (var n = el.parentElement; n; n = n.parentElement) if (n.tagName === 'DETAILS') n.open = true;
    setTimeout(function () { el.scrollIntoView({ block: 'center' }); el.focus(); }, 60);
  }
  function jumpTo(path) {
    var sec = secOf(path); if (sec) sec.det.open = true;
    var el = findCtl(path);
    if (el) jumpToEl(el); else if (sec) setTimeout(function () { sec.det.scrollIntoView({ block: 'start' }); }, 60);
  }
  function showErrors(errs) {
    clearErrors();
    var loose = [], first = null;
    errs.forEach(function (e) {
      var el = findCtl(e.path);
      if (el) { setFieldError(el, e.message); first = first || el; } else loose.push(e.path + '：' + e.message);
    });
    setBanner('error', '有 ' + errs.length + ' 個欄位需要修正' + (first ? '，已在欄位旁標示。' : '。'), loose);
    if (first) jumpToEl(first);
    else $('rv-banner').scrollIntoView({ block: 'center' });
  }

  /* ---------- 本機驗證（儲存前；與後端 Schema 對齊的子集） ---------- */
  function validateLive() {
    var d = state.data, errs = [];
    function add(p, m) { errs.push({ path: p, message: m }); }
    function req(p, v) { if (isEmpty(v)) add(p, '此欄位必填'); }
    function ym(p, v) { var m = v == null ? '' : checkValue('ym', v); if (m) add(p, m); }
    function url(p, v) { var m = urlProblem(v); if (m) add(p, m); }
    (d.work_experience || []).forEach(function (w, i) { var b = 'work_experience[' + i + ']'; req(b + '.company', w.company); req(b + '.title', w.title); ym(b + '.start', w.start); ym(b + '.end', w.end); });
    (d.education || []).forEach(function (e, i) { var b = 'education[' + i + ']'; req(b + '.school', e.school); ym(b + '.start', e.start); ym(b + '.end', e.end); });
    (d.skills || []).forEach(function (s, i) { req('skills[' + i + '].name', s.name); });
    (d.projects || []).forEach(function (p, i) { var b = 'projects[' + i + ']'; req(b + '.name', p.name); ym(b + '.start', p.start); ym(b + '.end', p.end); url(b + '.url', p.url); });
    (d.portfolio || []).forEach(function (p, i) { var b = 'portfolio[' + i + ']'; req(b + '.title', p.title); url(b + '.url', p.url); });
    ((d.basics || {}).links || []).forEach(function (l, i) { var b = 'basics.links[' + i + ']'; req(b + '.url', l.url); url(b + '.url', l.url); });
    (d.certifications || []).forEach(function (c, i) { req('certifications[' + i + '].name', c.name); ym('certifications[' + i + '].date', c.date); });
    (d.languages || []).forEach(function (l, i) { req('languages[' + i + '].language', l.language); });
    (d.awards || []).forEach(function (a, i) { req('awards[' + i + '].name', a.name); if (a.year != null && !Number.isInteger(a.year)) add('awards[' + i + '].year', '年度請填整數'); });
    var bio = d.autobiography || {};
    if (bio.text && bio.text.length > 6000) add('autobiography.text', '內容太長，請縮短到 6000 字以內');
    (bio.sections || []).forEach(function (s, i) { req('autobiography.sections[' + i + '].text', s.text); });
    return errs;
  }

  /* 把畫面上的內容整理成要送出的樣子（去空白、移除空白項目）並重繪，讓索引與畫面一致 */
  function commitClean() { state.data = clean(state.data); renderAll(); touch(); }

  /* ---------- 儲存、確認 ---------- */
  function save() {
    commitClean();
    var errs = validateLive();
    if (errs.length) { showErrors(errs); return Promise.resolve(false); }
    clearErrors();
    return window.Api.put('/profiles/' + state.id, { parsed_json: state.data }).then(function (res) {
      state.status = res.status; state.baseline = canon(state.data); state.saved = true; return true;
    }, function (err) {
      if (err && err.code === 'VALIDATION_ERROR' && err.details && err.details.length) showErrors(err.details);
      else window.UI.toast(window.Api.friendlyMessage(err), { type: 'error' });
      return false;
    });
  }
  function onSave() {
    if (state.busy) return;
    setBusy(true, '儲存中…');
    save().then(function (ok) { setBusy(false); touch(); if (ok) window.UI.toast('已儲存', { type: 'success' }); });
  }
  function onConfirm() {
    if (state.busy) return;
    commitClean();
    var errs = validateLive();
    if (errs.length) { showErrors(errs); return; }
    clearErrors();
    var ask = state.status === 'confirmed' ? Promise.resolve(true) : window.UI.confirmDialog({
      title: '確認並繼續？', message: '確認後會刪除您上傳的原始 PDF，之後分析只使用這裡的內容。', confirmText: '確認並繼續', cancelText: '再檢查一下' });
    ask.then(function (ok) {
      if (!ok) return;
      setBusy(true, '處理中…');
      (isDirty() ? save() : Promise.resolve(true)).then(function (saved) {
        if (!saved) { setBusy(false); return; }
        return window.Api.post('/profiles/' + state.id + '/confirm').then(function (res) {
          state.status = 'confirmed'; setBusy(false); showDone(res);
        }, function (err) { setBusy(false); window.UI.toast(window.Api.friendlyMessage(err), { type: 'error' }); });
      });
    });
  }
  function showDone(res) {
    $('editor').hidden = true; $('rv-bar').hidden = true;
    var deleted = res && res.original_file_deleted === true;
    window.UI.renderState($('state'), 'success', {
      title: '內容已確認',
      message: deleted ? '原始 PDF 已刪除，之後分析只會使用這裡的內容。' : '這次未能刪除原始 PDF。請回去繼續修改，再按「確認並繼續」重試刪除。',
      actionText: '前往下一步', onAction: function () { location.href = NEXT_PAGE; }
    });
    $('state').firstChild.appendChild(H('button', { type: 'button', class: 'btn btn--secondary', text: '回去繼續修改', onclick: function () { $('state').replaceChildren(); showEditor(); } }));
  }

  /* ---------- 載入 ---------- */
  function showEditor() {
    var confirmed = state.status === 'confirmed';
    $('editor').hidden = false; $('rv-bar').hidden = false;
    $('rv-note').hidden = confirmed;
    $('btn-save').textContent = confirmed ? '儲存變更' : '儲存草稿';
    setBanner(confirmed ? 'info' : '', confirmed ? '你已經確認過這份內容' : '', confirmed ? ['仍可修改；儲存後會更新，再按「確認並繼續」前往下一步。'] : null);
  }
  function start(res) {
    state.id = res.profile_id; state.status = res.status;
    state.data = deep(res.parsed_json); state.flags = buildFlags(state.data);
    state.baseline = canon(state.data); state.saved = false; state.openIds.clear();
    buildShell(); renderAll();
    SECS[0].det.open = true;
    $('state').replaceChildren(); showEditor(); touch();
  }
  function load() {
    $('editor').hidden = true; $('rv-bar').hidden = true;
    window.UI.renderState($('state'), 'loading', { title: '正在載入你的履歷內容…' });
    window.Api.get('/profiles/latest').then(start, function (err) {
      if (err && err.code === 'NOT_FOUND') {
        window.UI.renderState($('state'), 'empty', { title: '還沒有履歷解析結果', message: '請先上傳履歷，解析完成後才能在這裡確認。', actionText: '前往上傳', onAction: function () { location.href = 'upload.html'; } });
      } else {
        window.UI.renderState($('state'), 'error', { title: '讀取失敗', message: window.Api.friendlyMessage(err), actionText: '重新載入', onAction: load });
      }
    });
  }

  window.addEventListener('beforeunload', function (e) { if (isDirty()) { e.preventDefault(); e.returnValue = ''; } });
  $('btn-save').addEventListener('click', onSave);
  $('btn-confirm').addEventListener('click', onConfirm);
  window.Auth.requireLogin().then(function (ok) { if (ok) load(); });
})();
