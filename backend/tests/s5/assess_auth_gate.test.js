/* S5：自評頁必須「登入就緒後」才送任何請求。
   用真實的 frontend/js/api.js、ui.js、assess.js、assess.html，只替換 Auth 與 fetch：
   - 假 Auth：token 要等 requireLogin() 完成才有（和真實的 session 還原時序相同）
   - 假 fetch：沒帶 Authorization 就回 401 AUTH_FAILED（這就是實測看到的「閃一下回登入頁」）
   執行：node backend/tests/s5/assess_auth_gate.test.js（需要 jsdom；pytest 會自動呼叫，找不到就略過） */
'use strict';
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

const FRONT = path.resolve(__dirname, '..', '..', '..', 'frontend');
const read = (p) => fs.readFileSync(path.join(FRONT, p), 'utf8').replace(/^\uFEFF/, '');
const HTML = read('assess.html').replace(/<script[^>]*src=[^>]*><\/script>/g, '');
const SRC = { ui: read('js/ui.js'), api: read('js/api.js'), assess: read('js/assess.js') };

const PID = '11111111-1111-4111-8111-111111111111';
const PROFILE = { profile_id: PID, status: 'confirmed', parsed_json: {
  skills: [{ id: 'skill-1', name: 'Python', group: '開發', linked_ids: ['work-1'] }, { id: 'skill-2', name: 'Excel', group: '辦公', linked_ids: [] },
           { id: 'skill-3', name: 'SQL', group: '開發', linked_ids: [] }],
  work_experience: [{ id: 'work-1' }], projects: [], job_preferences: {} } };

let failed = 0;
const check = (cond, msg) => { if (cond) console.log('ok   ' + msg); else { failed++; console.log('FAIL ' + msg); } };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

/* auth: 'delayed' | 'rejects' | 'missing'。putReply: 控制 PUT 的回應。 */
function boot({ auth, putReply }) {
  const log = []; const nav = { count: 0 };
  const vc = new VirtualConsole(); vc.on('jsdomError', (e) => { if (/navigation/i.test(e.message)) nav.count++; });
  const dom = new JSDOM(HTML, { runScripts: 'outside-only', url: 'http://localhost/assess.html', pretendToBeVisual: true, virtualConsole: vc });
  const w = dom.window;
  w.Element.prototype.scrollIntoView = function () {}; w.HTMLElement.prototype.focus = function () {};
  let token = null; let release;
  const gate = new Promise((res, rej) => { release = { ok: (v) => { if (v) token = 'valid-token'; res(v); }, fail: () => rej(new Error('boom')) }; });
  if (auth !== 'missing') {
    w.Auth = { getToken: () => token, logout: () => log.push({ t: 'logout' }),
      requireLogin: () => { log.push({ t: 'requireLogin' }); return gate; } };
  }
  const reply = (status, body) => Promise.resolve({ status, ok: status >= 200 && status < 300, text: () => Promise.resolve(JSON.stringify(body)) });
  let putResolve = null;
  w.fetch = (url, init) => {
    const hasToken = !!(init && init.headers && init.headers.Authorization);
    log.push({ t: 'fetch', method: init.method, url, hasToken, tokenReady: token !== null });
    if (!hasToken) return reply(401, { error: { code: 'AUTH_FAILED', message: '登入已失效', details: [] } });
    if (init.method === 'GET' && /\/profiles\/latest$/.test(url)) return reply(200, PROFILE);
    if (init.method === 'PUT' && /\/career-inputs$/.test(url)) {
      log.push({ t: 'put-body', body: JSON.parse(init.body) });
      if (putReply === 'pending') return new Promise((res) => { putResolve = () => res({ status: 200, ok: true, text: () => Promise.resolve(JSON.stringify({ career_input_id: 'cid-123' })) }); });
      if (putReply === 'noid') return reply(200, {});
      return reply(200, { career_input_id: 'cid-123' });
    }
    return reply(404, { error: { code: 'NOT_FOUND', message: 'x', details: [] } });
  };
  w.eval(SRC.ui); w.eval(SRC.api); w.eval(SRC.assess);
  return { w, d: w.document, log, nav, release, finishPut: () => putResolve && putResolve() };
}
const fetches = (log) => log.filter((e) => e.t === 'fetch');
const fire = (w, node, type) => node.dispatchEvent(new w.Event(type, { bubbles: true }));

async function fillAndSubmit(env) {
  const { w, d } = env;
  fire(w, d.querySelector('input[name=goal][value=continue]'), 'change');
  d.querySelectorAll('fieldset.as-skill').forEach((f, i) => { const r = f.querySelectorAll('input[type=radio]')[(i % 5)]; r.checked = true; fire(w, r, 'change'); });
  d.getElementById('btn-next').click();
}

(async () => {
  // 1. 登入尚未就緒：任何 fetch 都不得送出（修正前這裡會送出沒帶 token 的請求並被登出）
  let e = boot({ auth: 'delayed' });
  await wait(60);
  check(fetches(e.log).length === 0, '登入就緒前：沒有任何 fetch');
  check(!e.log.some((x) => x.t === 'logout'), '登入就緒前：沒有被登出');
  check(e.d.getElementById('editor').hidden, '登入就緒前：編輯區未顯示');
  e.release.ok(true); await wait(60);
  const f1 = fetches(e.log);
  check(f1.length === 1 && f1[0].method === 'GET' && /profiles\/latest$/.test(f1[0].url) && f1[0].hasToken, '就緒後：只送一次帶 token 的 GET /profiles/latest');
  check(!e.d.getElementById('editor').hidden && e.d.querySelectorAll('fieldset.as-skill').length === 3, '就緒後：技能列表正常載入（3 項）');
  check(!e.log.some((x) => x.t === 'logout'), '全程沒有被登出');

  // 2. requireLogin 回 false：不得呼叫 API
  e = boot({ auth: 'delayed' }); e.release.ok(false); await wait(60);
  check(fetches(e.log).length === 0 && e.d.getElementById('editor').hidden, 'requireLogin 回 false：不呼叫 API、不顯示編輯區');
  check(e.d.body.textContent.includes('需要先登入'), 'requireLogin 回 false：顯示「需要先登入」');

  // 3. requireLogin 被 reject：不得呼叫 API，顯示錯誤
  e = boot({ auth: 'delayed' }); e.release.fail(); await wait(60);
  check(fetches(e.log).length === 0 && e.d.getElementById('editor').hidden && e.d.body.textContent.includes('確認不了登入狀態'), 'requireLogin 失敗：不呼叫 API、顯示錯誤');

  // 4. 完全沒有 Auth：不得呼叫 API
  e = boot({ auth: 'missing' }); await wait(60);
  check(fetches(e.log).length === 0 && e.d.body.textContent.includes('登入功能沒有載入'), '沒有 Auth：不呼叫 API、顯示錯誤');

  // 5. 導向只能發生在 PUT 回 200 且有編號之後
  e = boot({ auth: 'delayed', putReply: 'pending' }); e.release.ok(true); await wait(60);
  await fillAndSubmit(e); await wait(30);
  const put = e.log.find((x) => x.t === 'put-body');
  check(put && put.body.profile_id === PID && put.body.skill_ratings.length === 3, 'PUT 帶了 profile_id 與 3 項自評');
  check(fetches(e.log).filter((x) => x.method === 'PUT').every((x) => x.hasToken), 'PUT 有帶 token');
  check(e.nav.count === 0 && e.d.getElementById('btn-next').disabled, 'PUT 還沒回來：尚未導向，按鈕鎖住（防雙擊）');
  e.d.getElementById('btn-next').click(); await wait(10);
  check(fetches(e.log).filter((x) => x.method === 'PUT').length === 1, '送出中再按一次：不會送第二次 PUT');
  e.finishPut(); await wait(40);
  check(e.nav.count === 1, 'PUT 回 200 並有 career_input_id 之後：導向一次');

  // 6. PUT 回 200 但沒有編號：不導向、按鈕恢復
  e = boot({ auth: 'delayed', putReply: 'noid' }); e.release.ok(true); await wait(60);
  await fillAndSubmit(e); await wait(40);
  check(e.nav.count === 0 && !e.d.getElementById('btn-next').disabled, '回應沒有編號：不導向、按鈕恢復可按');

  console.log(failed ? `\n${failed} 項失敗` : '\n全部通過');
  process.exit(failed ? 1 : 0);
})();
