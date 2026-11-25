(function () {
  'use strict';

  const el = UI.el;
  const state = document.getElementById('report-state');
  const content = document.getElementById('report-content');
  const sample = document.body.hasAttribute('data-report-sample');
  const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
  const strengths = { sufficient: '充分', partial: '部分', insufficient: '不足' };
  const fit = { match: '✓ 吻合', partial: '◐ 部分', mismatch: '× 不吻合', unknown: '？ 未知' };
  const priorities = { high: '高', medium: '中', low: '低' };
  const sources = { resume: '履歷', self_rating: '技能自評', preference: '求職偏好', autobiography: '自傳' };

  function text(tag, value, className) {
    return el(tag, { text: value, ...(className ? { class: className } : {}) });
  }

  function list(items) {
    return el('ul', {}, items);
  }

  function section(title, children) {
    return el('section', { class: 'report-section' }, [text('h3', title), ...children]);
  }

  function evidenceSummary(claims) {
    const counts = { sufficient: 0, partial: 0, insufficient: 0 };
    claims.forEach(({ strength }) => { counts[strength] += 1; });
    const entries = Object.entries(counts);
    return el('span', { class: 'report-evidence-summary' }, [
      el('span', { class: 'report-meter', 'aria-hidden': 'true' }, entries.filter(([, count]) => count > 0)
        .map(([key, count]) => el('span', { class: 'report-strength--' + key, style: 'flex-grow:' + count }))),
      text('span', entries.map(([key, count]) => count + ' 項' + strengths[key]).join('／')),
    ]);
  }

  function overview(direction) {
    const preferences = direction.preference_fit;
    return [
      el('span', { class: 'report-direction-title' }, [
        text('span', String(direction.rank), 'report-rank'),
        text('span', direction.role_name),
      ]),
      text('span', direction.summary, 'report-summary'),
      evidenceSummary(direction.fit_evidence),
      el('span', { class: 'report-fits' }, [
        ['employment_type', '雇用型態'], ['work_mode', '工作模式'], ['salary', '薪資'], ['schedule', '時間安排'],
      ].map(([key, label]) => text('span', label + ' ' + fit[preferences[key]]))),
    ];
  }

  function evidenceSources(refs, labels, excerpts, links) {
    if (!refs?.length) return [];
    const items = refs.map((ref) => {
      const label = labels[ref.source]?.[ref.path] || sources[ref.source] || '履歷';
      const excerpt = excerpts[ref.source]?.[ref.path];
      const link = ref.source === 'resume' ? links[ref.path] : null;
      return el('li', {}, [
        text('strong', label),
        ...(excerpt ? [text('p', '分析時的資料片段：' + excerpt)] : []),
        ...(link ? [el('a', { href: link, target: '_blank', rel: 'noopener noreferrer', text: '開啟履歷中的作品連結' })] : []),
        ...(ref.note ? [text('p', '模型註解：' + ref.note)] : []),
      ]);
    });
    return [el('details', { class: 'report-sources' }, [text('summary', '查看證據來源'), list(items)])];
  }

  function evidenceItem(claim, labels, excerpts, links) {
    return el('li', { class: 'report-claim' }, [
      text('span', strengths[claim.strength], 'report-tag report-strength--' + claim.strength),
      text('h4', claim.claim),
      ...(claim.strength_reason ? [text('p', claim.strength_reason)] : []),
      ...evidenceSources(claim.evidence_refs, labels, excerpts, links),
    ]);
  }

  function alternativeDirections(assessment, comparison, labels, excerpts, links) {
    if (!Array.isArray(assessment.alternatives)) return text('p', assessment.alternative);
    const options = [...assessment.alternatives, ...(comparison?.fallback_options || [])];
    if (!options.length) return text('p', assessment.alternative_strategy === 'skill_bridge'
      ? '目前的履歷證據不足以提出可驗證的新職務。'
      : '目前的履歷證據不足以比較其他方向。');
    return el('ul', { class: 'report-claims' }, options.map((option) =>
      el('li', { class: 'report-claim' }, [
        text('h4', option.role_name),
        ...(option.application_readiness ? [text('span', option.application_readiness, 'report-tag')] : []),
        ...(option.validation ? [el('p', {}, [text('strong', '先怎麼驗證'), ' ' + option.validation])] : []),
        el('details', { class: 'report-detail-disclosure' }, [
          text('summary', '查看能力證據與驗收條件'),
          ...(option.application_readiness ? [
            text('p', option.readiness_reason),
            el('p', {}, [text('strong', '探索類型'), ' ' + option.discovery_type]),
            el('p', {}, [text('strong', '工作問題'), ' ' + option.work_problem]),
            el('p', {}, [text('strong', '為什麼是新方向'), ' ' + option.why_new]),
            text('strong', '有證據的能力'),
            list(option.skill_bridge.source_skills.map((skill) => el('li', {}, [
              text('p', skill.skill),
              ...evidenceSources(skill.evidence_refs, labels, excerpts, links),
            ]))),
            el('p', {}, [text('strong', '待驗證能力或情境'), ' ' + option.skill_bridge.adjacent_skill]),
          ] : option.skill_bridge ? [
            el('p', {}, [text('strong', '履歷中的能力'), ' ' + option.skill_bridge.source_skill]),
            el('p', {}, [text('strong', '可延伸能力（待驗證）'), ' ' + option.skill_bridge.adjacent_skill]),
          ] : []),
          ...(option.transfer ? [el('p', {}, [text('strong', '如何運用現有能力'), ' ' + option.transfer])] : []),
          ...(option.gap ? [el('p', {}, [text('strong', '關鍵缺口與入場門檻'), ' ' + option.gap])] : []),
          ...(option.success_criteria ? [el('p', {}, [text('strong', '驗收條件'), ' ' + option.success_criteria])] : []),
          ...evidenceSources(option.evidence_refs, labels, excerpts, links),
        ]),
      ])));
  }

  function directionBody(direction, labels, excerpts, links) {
    const claims = direction.fit_evidence;
    const gaps = direction.gaps;
    const risks = direction.risks;
    const plan = direction.action_plan;
    return [
      section('為什麼值得探索', claims.length
        ? [el('ul', { class: 'report-claims' }, claims.map((item) => evidenceItem(item, labels, excerpts, links)))]
        : [text('p', '這個方向目前沒有可呈現的證據。')]),
      section('技能差距', gaps.length ? [el('ul', { class: 'report-gaps' }, gaps.map((gap) =>
        el('li', {}, [
          text('h4', gap.skill),
          text('span', '優先度：' + priorities[gap.priority], 'report-tag'),
          text('p', gap.current_self_level == null ? '未自評' : '你的自評：' + gap.current_self_level + ' 級'),
          text('p', gap.why_it_matters),
        ])))] : [text('p', '目前沒有列出可比較的自評技能差距。')]),
      ...(direction.preference_fit.note ? [section('求職條件補充', [text('p', direction.preference_fit.note)])] : []),
      section('風險與不確定性', risks.length ? [list(risks.map((risk) => text('li', risk)))] : [text('p', '這次分析未列出其他風險。')]),
      section('行動計畫', [
        el('div', { class: 'report-timeline' }, [
          ['day_30', '30 天內先做'], ['day_60', '60 天內接著做'], ['day_90', '90 天內再深化'],
        ].map(([key, title]) => el('div', { class: 'report-time' }, [
          text('h4', title),
          list(plan[key].map((step) => el('li', {}, [
            text('strong', step.action),
            text('p', '可檢驗的產出：' + step.deliverable),
            ...(step.success_criteria ? [text('p', '驗收條件：' + step.success_criteria)] : []),
            ...(step.decision_rule ? [text('p', '下一步判斷：' + step.decision_rule)] : []),
          ]))),
        ]))),
      ]),
    ];
  }

  function render(report) {
    const result = report.result;
    const labels = report.evidence_labels || {};
    const excerpts = report.evidence_excerpts || {};
    const links = report.evidence_links || {};
    const desktop = matchMedia('(min-width: 768px)');
    const directions = [...result.directions].sort((a, b) => a.rank - b.rank);
    const panels = [];
    const tabs = [];
    let selected = 0;

    function select(index, focus = false) {
      selected = index;
      tabs.forEach((tab, i) => {
        tab.setAttribute('aria-selected', String(i === selected));
        tab.tabIndex = i === selected ? 0 : -1;
        panels[i].tabPanel.hidden = i !== selected;
      });
      if (focus) tabs[selected].focus();
    }

    function onKey(event, index) {
      const next = { ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1 };
      if (!Object.hasOwn(next, event.key)) return;
      event.preventDefault();
      select(next[event.key], true);
    }

    directions.forEach((direction, index) => {
      const tabId = 'report-tab-' + index;
      const panelId = 'report-panel-' + index;
      const summary = el('summary', { class: 'report-mobile-heading' }, overview(direction));
      const body = el('div', { class: 'report-detail' }, [
        text('h2', direction.rank + '・' + direction.role_name),
        el('div', { class: 'report-print-overview' }, overview(direction).slice(1)),
        ...directionBody(direction, labels, excerpts, links),
      ]);
      const accordion = el('details', { class: 'report-direction' }, [summary, body]);
      accordion.open = index === 0;
      accordion.addEventListener('toggle', () => {
        if (accordion.open && !desktop.matches) selected = index;
      });
      const tabPanel = el('div', { class: 'report-tab-panel', id: panelId, role: 'tabpanel', 'aria-labelledby': tabId, tabindex: '0' });
      panels.push({ accordion, tabPanel, body });
      tabs.push(el('button', {
        class: 'report-tab', type: 'button', id: tabId, role: 'tab', 'aria-controls': panelId,
        onclick: () => select(index), onkeydown: (event) => onKey(event, index),
      }, overview(direction)));
    });

    const tablist = el('div', { class: 'report-tabs', role: 'tablist', 'aria-label': '三個探索方向' }, tabs);
    const layout = el('div', { class: 'report-directions' });
    let printing = false;
    function arrange() {
      if (desktop.matches || printing) {
        panels.forEach(({ tabPanel, body }) => tabPanel.replaceChildren(body));
        layout.replaceChildren(...panels.map(({ tabPanel }) => tabPanel));
        select(selected);
      } else {
        panels.forEach(({ accordion, body }) => accordion.appendChild(body));
        layout.replaceChildren(...panels.map(({ accordion }) => accordion));
      }
    }

    const gaps = result.data_gaps;
    const assessment = result.career_overview;
    const comparison = report.alternative_comparison;
    const priority = directions[0];
    const firstAction = priority?.action_plan?.day_30?.[0];
    const supportingClaim = priority?.fit_evidence?.find((claim) => claim.strength === 'sufficient')
      || priority?.fit_evidence?.find((claim) => claim.strength === 'partial');
    const overviewPanel = assessment ? el('section', { class: 'report-next report-overview' }, [
      text('h2', '優先試哪條路'),
      text('p', assessment.first_move),
      ...(supportingClaim ? [el('p', {}, [text('strong', '履歷裡的線索'), ' ' + supportingClaim.claim])] : []),
      ...(firstAction ? [el('div', { class: 'report-first-step' }, [
        text('strong', '這個月可以先做'),
        text('p', firstAction.action),
      ])] : []),
      el('details', { class: 'report-detail-disclosure' }, [
        text('summary', '整體履歷判讀'),
        text('p', assessment.assessment),
      ]),
    ]) : null;
    const explorePanel = assessment ? el('section', { class: 'report-next report-explore' }, [
      text('h2', assessment.alternative_strategy === 'skill_bridge'
        ? '新職涯探索'
        : comparison ? '首選以外的方向' : '原志願以外的方向'),
      ...(comparison?.fallback_options?.length && assessment.alternatives?.length === 0
        ? [text('p', '這裡沿用本次報告已列出的其他方向。')]
        : []),
      alternativeDirections(assessment, comparison, labels, excerpts, links),
    ]) : null;
    const next = gaps.length ? el('section', { class: 'report-next' }, [
      text('h2', '想讓分析更準，可以補充…'),
      list(gaps.map(({ topic, message }) => el('li', {}, [text('strong', topic), text('p', message)]))),
      el('a', { class: 'btn btn--secondary', href: sample ? 'index.html' : 'review.html', text: sample ? '返回首頁' : '返回修改履歷' }),
    ]) : null;

    const actions = el('div', { class: 'report-actions report-toolbar' }, [
      el('a', { class: 'btn btn--secondary', href: 'history.html', text: '歷史紀錄' }),
      el('button', { class: 'btn btn--secondary', type: 'button', text: '列印／另存 PDF', onclick: () => window.print() }),
    ]);

    state.replaceChildren();
    content.replaceChildren(actions, ...(overviewPanel ? [overviewPanel] : []), tablist, layout,
      ...(explorePanel ? [explorePanel] : []), ...(next ? [next] : []));
    content.hidden = false;
    desktop.addEventListener('change', arrange);
    let closedSources = [];
    window.addEventListener('beforeprint', () => {
      printing = true;
      arrange();
      closedSources = [...content.querySelectorAll('.report-sources:not([open]), .report-detail-disclosure:not([open])')];
      closedSources.forEach((item) => { item.open = true; });
    });
    window.addEventListener('afterprint', () => {
      closedSources.forEach((item) => { item.open = false; });
      printing = false;
      arrange();
    });
    arrange();
  }

  function showUnavailable(report) {
    if (report.status === 'running') {
      UI.renderState(state, 'loading', { title: '分析仍在處理中', actionText: '重新查看', onAction: load });
    } else {
      const retry = report.career_input_id && uuid.test(report.career_input_id);
      UI.renderState(state, 'error', {
        title: '分析未完成', message: '已儲存的自評可以再用來分析。',
        actionText: retry ? '重試分析' : '返回自評',
        onAction: () => {
          location.href = retry ? 'analyzing.html?career_input_id=' + encodeURIComponent(report.career_input_id) : 'assess.html';
        },
      });
    }
  }

  async function load() {
    const id = new URLSearchParams(location.search).get('id');
    if (!sample && (!id || !uuid.test(id))) {
      UI.renderState(state, 'empty', { title: '找不到報告', message: '請從分析完成頁開啟報告。' });
      return;
    }
    UI.renderState(state, 'loading', { title: '正在讀取報告' });
    try {
      if (!sample && !(await Auth.requireLogin())) {
        UI.renderState(state, 'empty', { title: '請先登入再查看報告' });
        return;
      }
      const report = sample
        ? await fetch('sample/analysis_sample.json').then((response) => {
          if (!response.ok) throw new Error('sample unavailable');
          return response.json();
        })
        : await Api.get('/analyses/' + encodeURIComponent(id));
      if (report.status === 'succeeded') render(report);
      else showUnavailable(report);
    } catch (err) {
      if (err.code === 'NOT_FOUND') UI.renderState(state, 'empty', { title: '找不到報告', message: '這份報告可能已刪除，或目前的帳號無法查看。' });
      else UI.renderState(state, 'error', { title: '暫時讀不到報告', actionText: '再試一次', onAction: load });
    }
  }

  load();
})();
