(function () {
  'use strict';

  const el = UI.el;
  const state = document.getElementById('history-state');
  const list = document.getElementById('history-list');
  const dateFormat = new Intl.DateTimeFormat('zh-TW', { dateStyle: 'medium', timeStyle: 'short' });
  const statuses = { succeeded: '已完成', failed: '分析失敗', running: '分析中' };

  function row(item) {
    const date = new Date(item.created_at);
    const time = Number.isNaN(date.getTime())
      ? el('span', { text: '時間未知' })
      : el('time', { datetime: item.created_at, text: dateFormat.format(date) });
    const actions = [];
    if (item.status === 'succeeded' || item.status === 'running') {
      actions.push(el('a', {
        class: 'btn btn--secondary', href: 'report.html?id=' + encodeURIComponent(item.analysis_id),
        text: item.status === 'succeeded' ? '查看報告' : '查看狀態',
      }));
    }
    if (item.status === 'failed') {
      actions.push(el('a', {
        class: 'btn btn--secondary',
        href: item.career_input_id ? 'analyzing.html?career_input_id=' + encodeURIComponent(item.career_input_id) : 'assess.html',
        text: item.career_input_id ? '重試分析' : '返回自評',
      }));
    }
    return el('li', { class: 'history-item history-item--' + item.status }, [
      el('div', { class: 'history-entry' }, [
        el('div', { class: 'history-meta' }, [
          time, el('strong', { class: 'history-status', text: statuses[item.status] || '狀態未知' }),
        ]),
        ...(item.directions.length ? [el('ul', { class: 'history-directions' },
          item.directions.map((name) => el('li', { text: name })))] : []),
      ]),
      ...actions,
    ]);
  }

  async function load() {
    list.hidden = true;
    UI.renderState(state, 'loading', { title: '正在載入分析紀錄' });
    try {
      if (!(await Auth.requireLogin())) return;
      const entries = await Api.get('/analyses');
      if (!entries.length) {
        UI.renderState(state, 'empty', {
          title: '還沒有分析紀錄', message: '完成第一次分析後，報告就會出現在這裡。',
          actionText: '返回首頁', onAction: () => { location.href = 'index.html'; },
        });
        return;
      }
      list.replaceChildren(...entries.map(row));
      state.replaceChildren();
      list.hidden = false;
    } catch (err) {
      UI.renderState(state, 'error', {
        title: '暫時讀不到分析紀錄', message: Api.friendlyMessage(err), actionText: '重試', onAction: load,
      });
    }
  }

  load();
})();
