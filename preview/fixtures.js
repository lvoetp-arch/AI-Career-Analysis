(function () {
  'use strict';
  const page = location.pathname.split('/').pop();
  const id = '11111111-1111-4111-8111-111111111111';
  const mailbox = 'preview@example.org';
  const profileKey = 'career-preview-profile';
  const params = new URLSearchParams(location.search);
  const current = () => JSON.parse(sessionStorage.getItem(profileKey) || JSON.stringify(window.previewProfile));
  const report = async () => {
    const response = await fetch('sample/analysis_sample.json');
    if (!response.ok) throw new Error('讀不到範例報告');
    return response.json();
  };
  const profile = () => ({
    profile_id: id, status: page === 'assess.html' ? 'confirmed' : 'draft', parsed_json: current(),
  });
  const unavailable = (path) => { throw new Error('預覽未提供：' + path); };
  window.Auth = {
    ready: Promise.resolve(), isConfigured: () => true,
    isLoggedIn: () => page !== 'login.html' && page !== 'register.html',
    requireLogin: async () => true, getEmail: () => mailbox,
    login: async () => ({}), register: async () => ({ needsEmailConfirmation: true }),
    checkPassword: (value) => value.length < 8 ? '至少需要 8 個字元' : '',
    friendlyError: (err) => err.message, logout: () => { location.href = 'login.html'; },
  };
  window.Api = {
    friendlyMessage: (err) => err.message || '請再試一次。',
    get: async (path) => {
      if (path === '/profiles/latest') return profile();
      if (path === '/consents/current') return { version: '2026-11-23', accepted_at: '2026-11-25T04:00:00Z' };
      if (path === '/analyses') {
        const sample = await report();
        return [
          { analysis_id: id, career_input_id: id, created_at: '2026-11-25T04:00:00Z',
            status: 'succeeded', directions: sample.result.directions.map(({ role_name }) => role_name) },
          { analysis_id: id, career_input_id: id, created_at: '2026-11-23T04:00:00Z', status: 'failed', directions: [] },
        ];
      }
      if (path.startsWith('/analyses/')) return report();
      return unavailable(path);
    },
    upload: async () => profile(),
    post: async (path) => {
      if (path.endsWith('/confirm')) return { original_file_deleted: true };
      if (path === '/consents') return {};
      return unavailable(path);
    },
    put: async (path, body) => {
      if (path === '/career-inputs') return { career_input_id: id };
      if (path.startsWith('/profiles/')) {
        sessionStorage.setItem(profileKey, JSON.stringify(body.parsed_json));
        return profile();
      }
      return unavailable(path);
    },
    del: async () => { sessionStorage.removeItem(profileKey); return { deleted: { profiles: 1 } }; },
    analyze: async () => {
      await new Promise((resolve) => setTimeout(resolve, 5000));
      if (params.get('demo') === 'error') throw new Error('這是用來預覽錯誤提示的模擬狀態。');
      return { analysis_id: id, status: 'succeeded' };
    },
  };
  document.addEventListener('DOMContentLoaded', () => {
    const switchLink = document.getElementById('preview-switch');
    switchLink.href += location.search + location.hash;
  });
})();
