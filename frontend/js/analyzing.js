(function () {
  'use strict';

  const state = document.getElementById('analysis-state');
  const elapsed = document.getElementById('elapsed');
  const edit = document.getElementById('edit-input');
  const id = new URLSearchParams(location.search).get('career_input_id');
  const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

  async function analyze() {
    const started = performance.now();
    edit.hidden = true;
    elapsed.hidden = false;
    elapsed.textContent = '已等待 0 秒';
    UI.renderState(state, 'loading', { title: '正在分析履歷與自評', message: '通常需要 30–60 秒。' });
    const timer = setInterval(() => {
      elapsed.textContent = '已等待 ' + Math.floor((performance.now() - started) / 1000) + ' 秒';
    }, 1000);

    try {
      let run = await Api.analyze('/analyses', { career_input_id: id });
      while (run && uuid.test(run.analysis_id) && run.status === 'running' && performance.now() - started < 155000) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        run = await Api.get('/analyses/' + encodeURIComponent(run.analysis_id), { timeout: 10000 });
      }
      if (!run || !uuid.test(run.analysis_id) || run.status !== 'succeeded') {
        throw new Error('這次分析未完成，已儲存的自評仍可用來重試。');
      }
      UI.renderState(state, 'success', { title: '分析完成' });
      location.replace('report.html?id=' + encodeURIComponent(run.analysis_id));
    } catch (err) {
      const limited = err?.code === 'RATE_LIMITED';
      UI.renderState(state, 'error', {
        title: limited ? '今天的分析次數已達上限' : '分析未完成',
        message: limited ? Api.friendlyMessage(err) : Api.friendlyMessage(err) + ' 履歷與自評已保留，可直接重試。',
        actionText: limited ? '查看歷史紀錄' : '重試',
        onAction: limited ? () => { location.href = 'history.html'; } : analyze,
      });
      edit.hidden = false;
    } finally {
      clearInterval(timer);
      elapsed.hidden = true;
    }
  }

  if (!id || !uuid.test(id)) {
    UI.renderState(state, 'empty', { title: '找不到自評資料', message: '請回到自評頁儲存後再開始。' });
    edit.hidden = false;
    return;
  }
  UI.renderState(state, 'loading', { title: '正在確認登入狀態' });
  Auth.requireLogin().then((ok) => {
    if (ok) analyze();
    else UI.renderState(state, 'empty', { title: '需要先登入' });
  }).catch(() => {
    UI.renderState(state, 'error', {
      title: '無法確認登入狀態', actionText: '重新載入', onAction: () => location.reload()
    });
  });
})();
