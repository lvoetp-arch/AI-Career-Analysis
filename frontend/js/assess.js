/* S5：技能自評與求職偏好。動態內容一律用文字節點（UI.el），不用字串拼 HTML。
   登入：必須先 Auth.requireLogin() 通過才呼叫 API（api.js 取 token 時不會等待）。
   資料來源：GET /api/profiles/latest（已確認的履歷）→ PUT /api/career-inputs。
   不依賴 S4 的缺漏旗標、也不顯示履歷年資原文；自評與履歷證據分開呈現。 */
(function () {
  'use strict';

  const NEXT_PAGE = 'analyzing.html';   // S6 的頁面；以 ?career_input_id= 帶入
  const REVIEW_PAGE = 'review.html';
  const UPLOAD_PAGE = 'upload.html';

  // 等級說明：逐字採用《S5_自評規格摘要》的錨點，請勿改寫
  const LEVELS = [
    [1, '了解概念、需指導'],
    [2, '能完成基礎任務'],
    [3, '能獨立完成常見任務'],
    [4, '能處理複雜問題並改善流程'],
    [5, '能主導、設計方法並指導他人']
  ];
  const GOALS = [
    ['continue', '延續目前領域', '想在現有的專長與產業繼續往前走'],
    ['switch', '轉職', '想換到不同的職類或產業'],
    ['undecided', '尚未決定', '先看分析結果，再決定方向']
  ];
  const EMPLOYMENT = [['full_time', '全職'], ['part_time', '兼職'], ['contract', '約聘'], ['internship', '實習'], ['freelance', '接案']];
  const MODES = [['onsite', '現場上班'], ['remote', '遠端'], ['hybrid', '混合']];
  const REMOTE = [['unknown', '沒特別想法'], ['preferred', '偏好遠端'], ['willing', '有意願遠端'], ['not_willing', '不想遠端']];
  const MAX_SKILLS = 30, MAX_ADDED = 10, MAX_NAME = 60, MAX_STR = 200, MAX_LIST = 20;
  const CTRL = /[\x00-\x1f\x7f]/;

  const $ = (id) => document.getElementById(id);
  const el = UI.el;
  const S = { profileId: null, resume: [], added: [], goal: null, pref0: {}, uid: 0, submitting: false, sent: [] };

  /* ---------- 小工具 ---------- */
  const normName = (n) => n.normalize('NFKC').trim().toLowerCase();
  const asStr = (v) => (typeof v === 'string' ? v.trim() : '');
  const asList = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === 'string' && x.trim()).map((x) => x.trim()) : []);
  const splitList = (t) => Array.from(new Set(t.split(/[,，、;；\n]+/).map((x) => x.trim()).filter(Boolean)));
  const activeCount = () => S.resume.filter((s) => !s.removed).length + S.added.length;
  const levelText = (n) => (LEVELS.find((l) => l[0] === n) || [0, ''])[1];

  function setStatus(msg) { $('as-status').textContent = msg || ''; }

  /* ---------- 啟動：先等登入就緒，才呼叫任何 API（做法同 review.js） ---------- */
  function start() {
    $('editor').hidden = true; $('as-bar').hidden = true;
    const A = window.Auth;
    if (!A || typeof A.requireLogin !== 'function') {   // auth.js 沒載入：不送任何請求
      UI.renderState($('state'), 'error', { title: '登入功能沒有載入', message: '請重新整理頁面；如果還是這樣，請回到登入頁。', actionText: '重新整理', onAction: () => location.reload() });
      return;
    }
    UI.renderState($('state'), 'loading', { title: '正在確認登入狀態' });
    A.requireLogin().then((ok) => {
      if (ok) load();   // 沒登入時 requireLogin 會自己導向登入頁，這裡不呼叫 API
      else UI.renderState($('state'), 'empty', { title: '需要先登入', message: '登入後才能做自評。', actionText: '前往登入', onAction: () => { location.href = 'login.html'; } });
    }, () => {
      UI.renderState($('state'), 'error', { title: '確認不了登入狀態', message: '請再試一次。', actionText: '重新載入', onAction: start });
    });
  }

  /* ---------- 載入（只能由 start() 在登入就緒後呼叫） ---------- */
  function load() {
    $('editor').hidden = true; $('as-bar').hidden = true;
    UI.renderState($('state'), 'loading', { title: '正在讀取你確認過的履歷' });
    Api.get('/profiles/latest').then(onProfile, (err) => {
      if (err && err.code === 'NOT_FOUND') {
        UI.renderState($('state'), 'empty', { title: '還沒有履歷', message: '請先上傳並確認履歷，再回來做自評。', actionText: '前往上傳', onAction: () => { location.href = UPLOAD_PAGE; } });
      } else {
        UI.renderState($('state'), 'error', { title: '讀不到你的履歷', message: Api.friendlyMessage(err), actionText: '重新載入', onAction: start });
      }
    });
  }

  function onProfile(p) {
    if (!p || p.status !== 'confirmed') {
      UI.renderState($('state'), 'empty', { title: '履歷還沒有確認', message: '確認履歷內容後，才能進行自評。', actionText: '前往確認', onAction: () => { location.href = REVIEW_PAGE; } });
      return;
    }
    const data = p.parsed_json || {};
    S.profileId = p.profile_id;
    S.pref0 = data.job_preferences && typeof data.job_preferences === 'object' ? data.job_preferences : {};
    const evidenceIds = new Set([].concat(data.work_experience || [], data.projects || []).map((x) => x && x.id).filter(Boolean));
    S.resume = (Array.isArray(data.skills) ? data.skills : [])
      .filter((s) => s && typeof s.id === 'string' && typeof s.name === 'string' && s.name.trim())
      .map((s, i) => ({
        kind: 'resume', key: 'r' + i, skillId: s.id, name: s.name.trim(), group: asStr(s.group) || '其他技能',
        stated: asStr(s.stated_level_text),
        links: (Array.isArray(s.linked_ids) ? s.linked_ids : []).filter((id) => evidenceIds.has(id)).length,
        rating: null, removed: false
      }));
    $('state').replaceChildren();
    $('editor').hidden = false; $('as-bar').hidden = false;
    renderGoal(); renderLevels(); renderFewSkills(); renderSkills(); renderPrefs();
  }

  /* ---------- 目標 ---------- */
  function renderGoal() {
    const fs = el('fieldset', { class: 'as-goal', id: 'f-goal' }, [el('legend', { class: 'sr-only', text: '職涯目標' })]);
    GOALS.forEach(([value, title, desc]) => {
      const input = el('input', { type: 'radio', name: 'goal', value, checked: S.goal === value });
      input.addEventListener('change', () => { S.goal = value; clearErrorOn(fs); });
      fs.appendChild(el('label', { class: 'as-goal__opt' }, [input, el('span', { class: 'as-goal__body' }, [
        el('span', { class: 'as-goal__title', text: title }), el('span', { class: 'as-goal__desc', text: desc })])]));
    });
    $('goal-host').replaceChildren(fs);
  }

  /* ---------- 技能 ---------- */
  function renderLevels() {
    const items = LEVELS.map(([n, t]) => el('li', { text: `${n} 級：${t}` }));
    $('levels-host').replaceChildren(el('details', { class: 'accordion as-levels' }, [
      el('summary', { text: '1–5 級是什麼意思？' }), el('div', { class: 'accordion__body' }, [el('ol', {}, items)])]));
  }

  function renderFewSkills() {
    $('few-skills').replaceChildren();
    if (S.resume.length < 3) {   // 不強制加入，只提示（待主 session 決策）
      $('few-skills').appendChild(el('p', { class: 'alert alert--info', text: `履歷中讀到的技能只有 ${S.resume.length} 項。資料越少，分析越保守；你可以在下方加入其他想評估的技能。` }));
    }
  }

  function evidenceBlock(s) {
    if (s.kind === 'added') {
      return el('div', { class: 'as-evidence' }, [el('p', { class: 'as-evidence__label', text: '履歷證據' }), el('p', { class: 'as-evidence__text', text: '這項是你自己加入的，履歷裡沒有可對照的內容。' })]);
    }
    const kids = [el('p', { class: 'as-evidence__label', text: '履歷證據（系統從履歷讀到的）' }),
      el('p', { class: 'as-evidence__text', text: s.links ? `履歷中有 ${s.links} 項經歷或專案提到這項技能` : '只列在技能欄，履歷沒有實作佐證' })];
    if (s.stated) kids.push(el('p', { class: 'as-evidence__text', text: `履歷自述程度：「${s.stated}」` }));
    return el('div', { class: 'as-evidence' }, kids);
  }

  function hintText(s) {
    return s.rating ? `你的自評：${s.rating} 級，${levelText(s.rating)}（這是自評，不是系統已證實的能力）` : '請選 1–5 級，或按「不評這項」。';
  }

  function skillField(s) {
    const hint = el('p', { class: 'skill__hint', id: 'hint-' + s.key, 'aria-live': 'polite', text: hintText(s) });
    const fs = el('fieldset', { class: 'skill as-skill', id: 'sk-' + s.key, tabindex: '-1' }, [el('legend', { class: 'skill__name', text: s.name }), evidenceBlock(s)]);
    const scale = el('div', { class: 'skill__scale' });
    LEVELS.forEach(([n, t]) => {
      const input = el('input', { type: 'radio', name: 'rate-' + s.key, value: String(n), 'aria-label': `${s.name}：${n} 級，${t}`, checked: s.rating === n });
      input.addEventListener('change', () => { s.rating = n; hint.textContent = hintText(s); clearErrorOn(fs); updateProgress(); });
      scale.appendChild(el('label', { class: 'skill__opt' }, [input, el('span', { text: String(n) })]));
    });
    fs.appendChild(scale); fs.appendChild(hint);
    fs.appendChild(el('button', { type: 'button', class: 'btn btn--secondary btn--small as-skill__drop', text: s.kind === 'added' ? '移除這項' : '不評這項',
      onclick: () => dropSkill(s) }));
    s.fs = fs;
    return fs;
  }

  function dropSkill(s) {
    const order = visibleSkills(); const idx = order.indexOf(s);
    if (s.kind === 'added') S.added = S.added.filter((x) => x !== s); else s.removed = true;
    renderSkills();
    const next = visibleSkills()[Math.min(idx, visibleSkills().length - 1)];
    (next && next.fs ? next.fs : $('sec-skills')).focus({ preventScroll: false });
  }

  function visibleSkills() { return S.resume.filter((s) => !s.removed).concat(S.added); }

  function renderSkills() {
    const host = $('skill-groups'); host.replaceChildren();
    const groups = new Map();
    S.resume.filter((s) => !s.removed).forEach((s) => { if (!groups.has(s.group)) groups.set(s.group, []); groups.get(s.group).push(s); });
    groups.forEach((items, title) => host.appendChild(el('section', { class: 'as-group', 'aria-label': title }, [el('h3', { class: 'as-group__title', text: title })].concat(items.map(skillField)))));
    if (S.added.length) host.appendChild(el('section', { class: 'as-group', 'aria-label': '你另外加入的技能' }, [el('h3', { class: 'as-group__title', text: '你另外加入的技能' })].concat(S.added.map(skillField))));
    if (!visibleSkills().length) host.appendChild(el('p', { class: 'as-note', text: '目前沒有要評的技能。你可以在下方加入，也可以直接進入下一步。' }));

    const removed = S.resume.filter((s) => s.removed);
    $('removed-box').hidden = !removed.length;
    $('removed-summary').textContent = `已選擇不評的技能（${removed.length}）`;
    $('removed-list').replaceChildren(...removed.map((s) => el('li', {}, [el('button', { type: 'button', class: 'btn btn--secondary btn--small', text: `還原「${s.name}」`,
      onclick: () => { s.removed = false; renderSkills(); s.fs.focus(); } })])));
    updateProgress();
  }

  function updateProgress() {
    const act = visibleSkills(); const rated = act.filter((s) => s.rating).length;
    const p = $('as-progress'); p.classList.remove('is-warn');
    let t = act.length ? `已評 ${rated} / ${act.length} 項` : '目前沒有要評的技能';
    if (act.length > MAX_SKILLS) { t += `；最多 ${MAX_SKILLS} 項，請用「不評這項」移除 ${act.length - MAX_SKILLS} 項`; p.classList.add('is-warn'); }
    p.textContent = t;
    const next = act.find((skill) => !skill.rating);
    if (next) p.append(' ', el('button', {
      class: 'btn btn--small btn--secondary', type: 'button', text: '下一項未評',
      onclick: () => next.fs.focus(),
    }));
  }

  function addSkill() {
    const input = $('add-name'); const msg = $('add-msg'); const name = input.value.trim();
    const fail = (m) => { msg.textContent = m; input.focus(); };
    msg.textContent = '';
    if (!name) return fail('請先輸入技能名稱。');
    if (name.length > MAX_NAME) return fail(`技能名稱請在 ${MAX_NAME} 字以內。`);
    if (CTRL.test(name)) return fail('技能名稱含有不能使用的字元。');
    if (S.added.length >= MAX_ADDED) return fail(`最多只能加入 ${MAX_ADDED} 項。`);
    if (activeCount() >= MAX_SKILLS) return fail(`技能已達 ${MAX_SKILLS} 項上限，請先用「不評這項」移除幾項。`);
    if (visibleSkills().some((s) => normName(s.name) === normName(name))) return fail('這項技能已經在清單裡了。');
    const s = { kind: 'added', key: 'a' + (++S.uid), skillId: null, name, rating: null, removed: false };
    S.added.push(s); input.value = '';
    renderSkills();
    s.fs.focus();
  }

  /* ---------- 偏好 ---------- */
  function fieldWrap(id, kids) { return el('div', { class: 'field', id: 'f-' + id }, kids); }
  function label(id, text) { return el('label', { class: 'field__label', for: id, text }); }
  function hintP(t) { return t ? el('p', { class: 'field__hint', text: t }) : null; }
  function textField(id, text, value, hint, tag) {
    const ctl = tag === 'textarea' ? el('textarea', { class: 'textarea', id, rows: '3' }) : el('input', { class: 'input', id, type: 'text', autocomplete: 'off' });
    ctl.value = value || '';
    return fieldWrap(id, [label(id, text), ctl, hintP(hint)]);
  }
  function checkGroup(id, legend, opts, current) {
    return el('fieldset', { class: 'field', id: 'f-' + id }, [el('legend', { class: 'field__label', text: legend }),
      el('div', { class: 'as-checks' }, opts.map(([v, t]) => el('label', { class: 'as-check' }, [
        el('input', { type: 'checkbox', name: id, value: v, checked: current.includes(v) }), el('span', { text: t })])))]);
  }

  function renderPrefs() {
    const p = S.pref0; const sal = p.salary_expectation && typeof p.salary_expectation === 'object' ? p.salary_expectation : null;
    const av = p.availability && typeof p.availability === 'object' ? p.availability : {};
    const remote = REMOTE.some((r) => r[0] === p.remote_willingness) ? p.remote_willingness : 'unknown';
    const sel = el('select', { class: 'select', id: 'remote_willingness' }, REMOTE.map(([v, t]) => el('option', { value: v, text: t, selected: v === remote })));
    $('prefs-host').replaceChildren(el('div', { class: 'as-prefs' }, [
      checkGroup('employment_types', '雇用型態', EMPLOYMENT, asList(p.employment_types)),
      textField('preferred_locations', '工作地點', asList(p.preferred_locations).join('，'), '可填多個，用逗號分開。'),
      checkGroup('work_modes', '上班方式', MODES, asList(p.work_modes)),
      fieldWrap('remote_willingness', [label('remote_willingness', '遠端意願'), sel]),
      textField('shift', '上班時段', asStr(p.shift), '例如：日班。'),
      textField('salary', '待遇', sal ? asStr(sal.raw_text) : '', '照你的想法填，可以寫「面議」或一個範圍；系統只照你寫的記錄，不會補市場資料。'),
      fieldWrap('availability', [el('div', { class: 'as-two' }, [
        el('div', {}, [label('earliest_start', '最快可上班日'), el('input', { class: 'input', id: 'earliest_start', type: 'text', autocomplete: 'off', value: asStr(av.earliest_start) })]),
        el('div', {}, [label('notice_period', '離職預告期'), el('input', { class: 'input', id: 'notice_period', type: 'text', autocomplete: 'off', value: asStr(av.notice_period) })])])]),
      textField('leave_and_schedule', '休假與工時', asStr(p.leave_and_schedule), '只寫你希望的安排（例如週休二日、可配合輪班），不需要寫原因。', 'textarea'),
      textField('desired_roles', '希望職稱', asList(p.desired_roles).join('，'), '可填多個，用逗號分開。'),
      textField('desired_job_categories', '希望職類', asList(p.desired_job_categories).join('，'), '可填多個，用逗號分開。'),
      textField('desired_industries', '希望產業', asList(p.desired_industries).join('，'), '可填多個，用逗號分開。'),
      textField('other_conditions', '其他條件', asList(p.other_conditions).join('\n'), '一行寫一項。', 'textarea')
    ]));
  }

  const val = (id) => ($(id) ? $(id).value.trim() : '');
  const checked = (name) => Array.from(document.querySelectorAll(`input[name="${name}"]:checked`)).map((i) => i.value);

  function collectPrefs() {
    const p = {};
    const setList = (k, arr) => { if (arr.length) p[k] = arr; };
    setList('employment_types', checked('employment_types'));
    setList('work_modes', checked('work_modes'));
    setList('preferred_locations', splitList(val('preferred_locations')));
    setList('desired_roles', splitList(val('desired_roles')));
    setList('desired_job_categories', splitList(val('desired_job_categories')));
    setList('desired_industries', splitList(val('desired_industries')));
    setList('other_conditions', val('other_conditions').split('\n').map((x) => x.trim()).filter(Boolean));
    p.remote_willingness = $('remote_willingness').value;
    if (val('shift')) p.shift = val('shift');
    if (val('leave_and_schedule')) p.leave_and_schedule = val('leave_and_schedule');
    const raw = val('salary'); const old = S.pref0.salary_expectation;
    if (raw) p.salary_expectation = old && typeof old === 'object' && asStr(old.raw_text) === raw ? Object.assign({}, old) : { raw_text: raw };
    if (val('earliest_start') || val('notice_period')) p.availability = { earliest_start: val('earliest_start') || null, notice_period: val('notice_period') || null };
    return p;
  }

  /* ---------- 錯誤顯示 ---------- */
  function clearErrorOn(node) {
    node.classList.remove('field--error', 'as-skill--error');
    node.querySelectorAll('[data-as-error]').forEach((n) => n.remove());
  }
  function clearErrors() {
    document.querySelectorAll('[data-as-error]').forEach((n) => n.remove());
    document.querySelectorAll('.field--error, .as-skill--error').forEach((n) => n.classList.remove('field--error', 'as-skill--error'));
    $('as-errors').hidden = true; $('as-errors').replaceChildren();
  }
  function focusTarget(t) {
    t.scrollIntoView({ block: 'center' });
    const f = t.matches('input,select,textarea,button') ? t : t.querySelector('input,select,textarea,button');
    (f || t).focus({ preventScroll: true });
  }
  /* items: [{target: Element|null, label, message}] */
  function showErrors(items) {
    const box = $('as-errors'); const list = el('ul');
    items.forEach((it) => {
      if (it.target) {
        it.target.classList.add(it.target.classList.contains('as-skill') ? 'as-skill--error' : 'field--error');
        it.target.appendChild(el('p', { class: 'field__error', 'data-as-error': '', text: it.message }));
      }
      const text = it.label ? `${it.label}：${it.message}` : it.message;
      list.appendChild(el('li', {}, [it.target ? el('a', { href: '#' + (it.target.id || ''), text, onclick: (e) => { e.preventDefault(); focusTarget(it.target); } }) : text]));
    });
    box.replaceChildren(el('p', { text: `有 ${items.length} 處需要修改：` }), list);
    box.hidden = false; box.scrollIntoView({ block: 'start' }); box.focus({ preventScroll: true });
  }

  function validateClient(prefs) {
    const errs = []; const add = (target, lab, message) => errs.push({ target, label: lab, message });
    if (!S.goal) add($('f-goal'), '職涯目標', '請選一個方向');
    const act = visibleSkills();
    act.forEach((s) => { if (!s.rating) add(s.fs, s.name, '請選 1–5 級，或按「不評這項」'); });
    if (act.length > MAX_SKILLS) add($('sec-skills'), '技能', `最多 ${MAX_SKILLS} 項，請用「不評這項」移除 ${act.length - MAX_SKILLS} 項`);
    const seen = new Set();
    act.forEach((s) => { const k = normName(s.name); if (seen.has(k)) add(s.fs, s.name, '技能名稱重複了'); seen.add(k); });
    const longField = { shift: 'shift', leave_and_schedule: 'leave_and_schedule', salary_expectation: 'salary' };
    Object.keys(longField).forEach((k) => {
      const v = k === 'salary_expectation' ? (prefs[k] || {}).raw_text : prefs[k];
      if (typeof v === 'string' && v.length > MAX_STR) add($('f-' + longField[k]), '', `內容請在 ${MAX_STR} 字以內`);
    });
    [['availability', 'availability'], ['desired_roles', 'desired_roles'], ['desired_job_categories', 'desired_job_categories'],
      ['desired_industries', 'desired_industries'], ['other_conditions', 'other_conditions'], ['preferred_locations', 'preferred_locations']].forEach(([k, id]) => {
      const v = prefs[k]; if (!v) return;
      const strs = Array.isArray(v) ? v : Object.keys(v).map((x) => v[x]).filter((x) => typeof x === 'string');
      if (Array.isArray(v) && v.length > MAX_LIST) add($('f-' + id), '', `最多 ${MAX_LIST} 項`);
      if (strs.some((x) => x.length > MAX_STR)) add($('f-' + id), '', `每一項請在 ${MAX_STR} 字以內`);
    });
    return errs;
  }

  function applyServerErrors(details) {
    const items = []; const prefMap = { salary_expectation: 'salary', availability: 'availability' };
    (details || []).forEach((d) => {
      const path = String(d.path || ''); const message = d.message || '資料有誤';
      let m = /^skill_ratings\[(\d+)\]/.exec(path);
      if (m && S.sent[+m[1]] && S.sent[+m[1]].fs) { items.push({ target: S.sent[+m[1]].fs, label: S.sent[+m[1]].name, message }); return; }
      if (/^skill_ratings/.test(path)) { items.push({ target: $('sec-skills'), label: '技能', message }); return; }
      if (path === 'goal') { items.push({ target: $('f-goal'), label: '職涯目標', message }); return; }
      m = /^preferences(?:[.[]([a-z_]+))?/.exec(path);
      if (m) { const id = m[1] ? (prefMap[m[1]] || m[1]) : null; items.push({ target: (id && $('f-' + id)) || $('sec-prefs'), label: '求職偏好', message }); return; }
      items.push({ target: null, label: '', message });
    });
    showErrors(items.length ? items : [{ target: null, label: '', message: '資料有誤，請檢查後再試一次。' }]);
  }

  /* ---------- 送出 ---------- */
  function setBusy(on) {
    S.submitting = on;
    const b = $('btn-next'); b.disabled = on; b.classList.toggle('is-loading', on); b.setAttribute('aria-busy', String(on));
    setStatus(on ? '儲存中…' : '');
  }

  function submit() {
    if (S.submitting) return;
    clearErrors();
    const prefs = collectPrefs(); const errs = validateClient(prefs);
    if (errs.length) { showErrors(errs); return; }
    const act = visibleSkills(); S.sent = act;
    const payload = {
      profile_id: S.profileId, goal: S.goal,
      skill_ratings: act.map((s) => ({ skill_id: s.skillId, name: s.name, rating: s.rating, source: s.kind === 'added' ? 'added' : 'resume' })),
      preferences: prefs
    };
    setBusy(true);
    Api.put('/career-inputs', payload).then((res) => {
      if (!res || typeof res.career_input_id !== 'string' || !res.career_input_id) {   // 200 但沒有編號：不導向
        setBusy(false); UI.toast('儲存結果不完整，請再按一次「下一步」。', { type: 'error' }); return;
      }
      location.href = NEXT_PAGE + '?career_input_id=' + encodeURIComponent(res.career_input_id);
    }, (err) => {
      setBusy(false);
      if (err && err.code === 'VALIDATION_ERROR') applyServerErrors(err.details);
      else if (err && err.code === 'NOT_FOUND') showErrors([{ target: null, label: '', message: '找不到你的履歷資料，請回到確認頁再試一次。' }]);
      else UI.toast(Api.friendlyMessage(err), { type: 'error' });
    });
  }

  /* ---------- 啟動 ---------- */
  $('btn-add').addEventListener('click', addSkill);
  $('add-name').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addSkill(); } });
  $('btn-next').addEventListener('click', submit);
  start();
})();
