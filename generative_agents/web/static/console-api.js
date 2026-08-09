(() => {
  'use strict';

  const state = {
    page: 1,
    pageSize: 10,
    status: '',
    query: '',
    selectedExperimentId: null,
    experiment: null,
    draft: null,
    definition: null,
    revision: null,
    currentRun: null,
    latestRunId: null,
    selectedRunId: null,
    eventSource: null,
    activitySource: null,
    runHistory: [],
    runCursor: null,
    runHistoryExperimentId: null,
    runHistoryGeneration: 0,
    experimentListGeneration: 0,
    experimentOpenGeneration: 0,
    selectedExperimentGeneration: 0,
    latestSummaryGeneration: 0,
    conversationGeneration: 0,
    memoryGeneration: 0,
    activityGeneration: 0,
    globalRefreshTimer: null,
    pendingActivityExperimentIds: new Set(),
    forceGlobalRefresh: false,
    currentPromptKey: 'base_desc',
    resultGeneration: 0,
    resultRequestGeneration: 0,
    resultRefreshTimer: null,
    operationFactsGeneration: 0,
    operationsRunId: null,
    operationsAbortController: null,
    logSource: null,
    logGeneration: 0,
    checkpointGeneration: 0,
    selectedAttemptId: null,
    selectedTraceAttemptId: null,
    logCursor: 0,
    logFileId: null,
    logRecords: [],
    logCarry: '',
    logDiscardUntilNewline: false,
    logStreamPaused: false,
    operationEvents: [],
    eventCursor: 0,
    traceCursor: null,
    traceEof: true,
    traceItems: [],
    traceDetailState: null,
    checkpointPreviewState: null,
    timeline: null,
    timelineTimer: null,
    replayPlayer: null,
    replayAbortController: null,
    replayRunId: null,
    replayPlaying: false,
    replayMarkerFacts: new Map(),
    selectedReplayAgentKey: null,
    selectedReplayRevisionId: null,
    selectedAgentKey: null,
    agentResults: [],
    agentStatusFilter: 'all',
    selectedAgentContent: 'plan',
    agentDetailGeneration: 0,
    resultTab: 'summary',
    operationTab: 'logs',
    contentTabs: {
      overview: 'definition',
      models: 'chat',
      world: 'map',
      advanced: 'perception',
      summary: 'activity',
      'agent-editor': 'identity',
    },
    selectedConversationId: null,
    editingAgentKey: null,
    currentExperimentName: '',
    currentExperimentStatus: '草稿',
    workspaceReadonly: false,
    dirty: false,
    pendingGlobalPage: 'experiments',
    toastTimer: null,
    wizardStep: 1,
    selectedTemplate: '标准小镇模板',
    activeModalId: null,
    modalReturnFocus: null,
    pendingResumeRunId: null,
    pendingResumeStep: 0,
    workspacePage: 'experiments',
    remoteConflictKey: null,
    bootstrapped: false,
  };

  const $ = id => document.getElementById(id);
  const modalFocus = window.ConsoleModalFocus;
  if (!modalFocus) throw new Error('modal-focus.js 未在正式控制台脚本之前加载');
  const escapeHtml = value => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  const statusLabels = {
    DRAFT: '草稿', QUEUED: '排队中', STARTING: '正在启动', RUNNING: '运行中',
    PAUSE_REQUESTED: '正在暂停', PAUSED: '已暂停', CANCEL_REQUESTED: '正在取消',
    COMPLETED: '已完成', CANCELLED: '已取消', FAILED: '失败', INTERRUPTED: '已中断',
  };
  const statusClasses = {
    DRAFT: 'draft', QUEUED: 'queued', RUNNING: 'running', PAUSED: 'paused',
    COMPLETED: 'completed', CANCELLED: 'cancelled', FAILED: 'failed', INTERRUPTED: 'failed',
  };

  function showToast(message, title = '操作成功') {
    clearTimeout(state.toastTimer);
    $('toastTitle').textContent = title;
    $('toastText').textContent = message;
    $('toast').classList.add('show');
    state.toastTimer = setTimeout(() => $('toast').classList.remove('show'), 2600);
  }

  function workspaceUrl(pageName = state.workspacePage) {
    const url = new URL(window.location.href);
    url.search = '';
    url.hash = '';
    if (pageName !== 'experiments' && state.selectedExperimentId) {
      url.searchParams.set('experiment_id', state.selectedExperimentId);
      url.searchParams.set('view', pageName);
      if (pageName === 'results' && state.selectedRunId) {
        url.searchParams.set('run_id', state.selectedRunId);
      }
      if (pageName === 'results') {
        url.searchParams.set('result_tab', state.resultTab);
        const resultContentTab = state.resultTab === 'summary'
          ? state.contentTabs.summary
          : state.resultTab === 'agents'
            ? state.selectedAgentContent
            : state.resultTab === 'operations'
              ? state.operationTab
              : null;
        if (resultContentTab) url.searchParams.set('tab', resultContentTab);
      } else if (state.contentTabs[pageName]) {
        url.searchParams.set('tab', state.contentTabs[pageName]);
      }
    }
    return `${url.pathname}${url.search}`;
  }

  function syncWorkspaceUrl({ push = false } = {}) {
    const nextUrl = workspaceUrl();
    const currentUrl = `${window.location.pathname}${window.location.search}`;
    if (nextUrl !== currentUrl) history[push ? 'pushState' : 'replaceState'](null, '', nextUrl);
  }

  function goToPage(pageName) {
    const target = $(`page-${pageName}`);
    if (!target) throw new Error(`未知页面：${pageName}`);
    const isGlobal = pageName === 'experiments';
    state.workspacePage = pageName;
    document.querySelectorAll('.nav-item[data-page]').forEach(item => {
      item.classList.toggle('active', item.dataset.page === pageName);
    });
    document.querySelectorAll('.page').forEach(page => {
      page.classList.toggle('active', page === target);
    });
    document.body.classList.toggle('hub-mode', isGlobal);
    $('topbarTitle').textContent = isGlobal ? '实验中心' : state.currentExperimentName || '当前实验';
    $('statusPill').hidden = isGlobal;
    $('backToHub').classList.toggle('visible', !isGlobal);
    $('hubActions').hidden = !isGlobal;
    $('experimentActions').hidden = isGlobal;
    if (pageName !== 'results' && state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
      state.resultGeneration += 1;
      if (state.resultRefreshTimer) clearTimeout(state.resultRefreshTimer);
      state.resultRefreshTimer = null;
    }
    if (pageName !== 'results') {
      closeLogStream();
      state.operationsAbortController?.abort();
      state.operationsAbortController = null;
      state.operationsRunId = null;
    }
    if (isGlobal) scheduleGlobalReconcile({ full: true });
    syncWorkspaceUrl();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function markDirty() {
    if (state.workspaceReadonly || !state.draft) return;
    state.dirty = true;
    $('unsaved').hidden = false;
    $('unsaved').querySelector('span').textContent = '有未保存更改';
  }

  function clearDirty() {
    state.dirty = false;
    $('unsaved').hidden = true;
  }

  function requestGlobalNavigation(pageName) {
    if (state.dirty) {
      state.pendingGlobalPage = pageName;
      openModal('leaveModal', 'saveAndLeave');
      return;
    }
    goToPage(pageName);
  }

  function applyStatusPill(status) {
    const styles = {
      '运行中': ['#e3f3ef', '#c8e3dc', '#0f6e5d'],
      '排队中': ['#fff7e9', '#f0d3a2', '#986117'],
      '草稿': ['#fff7e9', '#f0d3a2', '#986117'],
      '已暂停': ['#edf2ff', '#d5dff8', '#3f6fd9'],
      '已完成': ['#edf2f0', '#dce5e1', '#64766f'],
      '失败': ['#fff0ed', '#edc7bd', '#a53f2b'],
      '已取消': ['#edf2f0', '#dce5e1', '#64766f'],
      '已中断': ['#fff0ed', '#edc7bd', '#a53f2b'],
    };
    const palette = styles[status] || styles['草稿'];
    $('statusPill').textContent = status;
    $('statusPill').style.background = palette[0];
    $('statusPill').style.borderColor = palette[1];
    $('statusPill').style.color = palette[2];
  }

  function setWorkspaceMode(status) {
    state.workspaceReadonly = !state.draft;
    document.body.classList.toggle('readonly-mode', state.workspaceReadonly);
    $('workspaceNotice').hidden = !state.workspaceReadonly;
    const descriptions = {
      '运行中': '当前运行绑定到已发布 Revision，配置不可直接修改。需要调整时请创建新的修订草稿。',
      '排队中': '当前运行已绑定发布 Revision，正在等待本机运行槽；配置不可直接修改。',
      '已暂停': '暂停只影响运行状态，已发布配置仍为只读。可以恢复运行，或创建新修订进行配置调整。',
      '已完成': '已完成运行及其配置快照永久只读。可查看结果，或基于该版本创建新的修订。',
    };
    const workspaceMessage = descriptions[status] || '';
    $('workspaceNoticeHelp').dataset.tooltip = workspaceMessage;
    $('workspaceNoticeHelp').setAttribute('aria-label', workspaceMessage || '只读实验说明');
    document.querySelectorAll('.dirty-track, .switch, .agent-check').forEach(control => {
      control.disabled = state.workspaceReadonly;
    });
    $('selectAllBtn').disabled = state.workspaceReadonly;
    $('cloneBtn').textContent = state.workspaceReadonly ? '创建新修订' : '复制实验';
    $('saveBtn').textContent = status === '运行中' ? '查看运行' : status === '排队中' ? '查看排队' : status === '已暂停' ? '恢复运行' : status === '已完成' ? '查看结果' : '保存草稿';
    $('publishBtn').textContent = status === '运行中' ? '查看当前运行' : status === '排队中' ? '取消排队' : status === '已暂停' ? '恢复此运行' : status === '已完成' ? '查看实验结果' : '发布版本并启动实验';
  }

  function setContentTab(groupName, tabName, { sync = true, push = false } = {}) {
    const root = document.querySelector(`[data-content-tabs="${groupName}"]`);
    if (!root || !root.querySelector(`[data-content-tab="${tabName}"]`)) return false;
    state.contentTabs[groupName] = tabName;
    root.querySelectorAll('[data-content-tab]').forEach(tab => {
      const active = tab.dataset.contentTab === tabName;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    root.querySelectorAll('[data-content-panel]').forEach(panel => {
      const active = panel.dataset.contentPanel === tabName;
      panel.classList.toggle('active', active);
      panel.hidden = !active;
    });
    const ownsUrl = groupName === state.workspacePage
      || (state.workspacePage === 'results' && state.resultTab === 'summary' && groupName === 'summary');
    if (sync && ownsUrl) syncWorkspaceUrl({ push });
    return true;
  }

  function setResultTab(tabName, { sync = true, push = false } = {}) {
    if (!document.querySelector(`[data-result-tab="${tabName}"]`)) return false;
    state.resultTab = tabName;
    document.querySelectorAll('[data-result-tab]').forEach(tab => {
      const active = tab.dataset.resultTab === tabName;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll('[data-result-panel]').forEach(panel => {
      panel.classList.toggle('active', panel.dataset.resultPanel === tabName);
    });
    if (tabName === 'timeline' && state.selectedRunId) {
      ensureReplayPlayer(state.selectedRunId, state.resultGeneration).catch(reportError);
    }
    if (sync) syncWorkspaceUrl({ push });
    return true;
  }

  function setOperationTab(tabName, { sync = true, push = false } = {}) {
    if (!document.querySelector(`[data-operation-tab="${tabName}"]`)) return false;
    state.operationTab = tabName;
    document.querySelectorAll('[data-operation-tab]').forEach(tab => {
      const active = tab.dataset.operationTab === tabName;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll('[data-operation-panel]').forEach(panel => {
      panel.classList.toggle('active', panel.dataset.operationPanel === tabName);
    });
    if (sync) syncWorkspaceUrl({ push });
    return true;
  }

  function renderWizardStep() {
    document.querySelectorAll('[data-wizard-step]').forEach(step => {
      const stepNumber = Number(step.dataset.wizardStep);
      step.classList.toggle('active', stepNumber === state.wizardStep);
      step.classList.toggle('done', stepNumber < state.wizardStep);
    });
    document.querySelectorAll('[data-wizard-panel]').forEach(panel => {
      panel.classList.toggle('active', Number(panel.dataset.wizardPanel) === state.wizardStep);
    });
    $('wizardBack').hidden = state.wizardStep === 1;
    $('wizardNext').textContent = state.wizardStep === 3 ? '创建实验' : '下一步';
    $('createSummaryName').textContent = $('newExperimentName').value.trim() || '未填写';
    $('createSummaryTemplate').textContent = state.selectedTemplate;
  }

  const modalFocusableSelector = [
    'button:not([disabled])',
    'a[href]',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',');

  function modalFocusableElements(modal) {
    return [...modal.querySelectorAll(modalFocusableSelector)].filter(element => (
      !element.hidden
      && element.getAttribute('aria-hidden') !== 'true'
      && element.getClientRects().length > 0
      && getComputedStyle(element).visibility !== 'hidden'
    ));
  }

  function setBackgroundInert(inert) {
    const shell = document.querySelector('.app-shell');
    shell.inert = inert;
    if (inert) shell.setAttribute('aria-hidden', 'true');
    else shell.removeAttribute('aria-hidden');
  }

  function openModal(id, initialFocusId = null, returnFocus = document.activeElement) {
    const modal = $(id);
    if (!modal) throw new Error(`未知弹窗：${id}`);
    state.modalReturnFocus = returnFocus instanceof HTMLElement ? returnFocus : null;
    state.activeModalId = id;
    modal.classList.add('open');
    setBackgroundInert(true);
    requestAnimationFrame(() => {
      if (state.activeModalId !== id) return;
      const requested = initialFocusId ? $(initialFocusId) : null;
      const target = requested && !requested.disabled ? requested : modalFocusableElements(modal)[0];
      target?.focus({ preventScroll: true });
    });
  }

  function closeModal(id, { restoreFocus = true } = {}) {
    const modal = $(id);
    if (!modal) return;
    modal.classList.remove('open');
    if (state.activeModalId !== id) return;
    state.activeModalId = null;
    const returnFocus = state.modalReturnFocus;
    state.modalReturnFocus = null;
    if (!document.querySelector('.modal-backdrop.open')) setBackgroundInert(false);
    if (restoreFocus && returnFocus?.isConnected) {
      requestAnimationFrame(() => returnFocus.focus({ preventScroll: true }));
    }
  }

  function handleModalKeydown(event, modal) {
    if (event.key === 'Escape') {
      event.preventDefault();
      closeModal(modal.id);
      return true;
    }
    if (event.key === 'Tab') {
      const focusables = modalFocusableElements(modal);
      const target = modalFocus.tabTarget(focusables, document.activeElement, event.shiftKey);
      event.preventDefault();
      target?.focus({ preventScroll: true });
      return true;
    }
    return false;
  }

  function openPublishModal() {
    if (!state.draft || !state.definition) throw new Error('当前实验没有可发布的 Draft');
    $('modalRevision').textContent = `revision ${String(state.draft.revision_no || 1).padStart(3, '0')}`;
    $('modalAgentCount').textContent = state.definition.agents.filter(agent => agent.enabled).length;
    $('modalModels').textContent = `${state.definition.models.chat.resolved_model || state.definition.models.chat.model} / ${state.definition.models.embedding.resolved_model || state.definition.models.embedding.model}`;
    $('modalWorld').textContent = state.definition.world.world_name || '世界待配置';
    $('modalHash').textContent = '将在发布事务中生成并锁定';
    openModal('publishModal', 'confirmPublish');
  }

  function openResumeRunModal() {
    const run = state.currentRun;
    if (!isRunRecoverable(run)) throw new Error('当前运行没有可用的恢复点');
    const step = Number(run.recoverable_step);
    state.pendingResumeRunId = run.run_id;
    state.pendingResumeStep = step;
    $('resumeRunIdentity').textContent = run.run_id.slice(0, 12);
    $('resumeRunStep').textContent = `Step ${step}`;
    $('resumeRunNextStep').textContent = `Step ${step + 1}`;
    openModal('resumeRunModal', 'confirmResumeRun');
  }

  async function api(path, options = {}) {
    const response = await fetch(`/api/v1${path}`, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      cache: 'no-store',
      ...options,
    });
    if (!response.ok) {
      let message = `请求失败（${response.status}）`;
      try { message = (await response.json()).error?.message || message; } catch (_) {}
      throw new Error(message);
    }
    return response.status === 204 ? null : response.json();
  }

  function formatTime(value) {
    if (!value) return '—';
    return new Intl.DateTimeFormat('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date(value));
  }

  function formatDuration(startedAt, finishedAt) {
    if (!startedAt) return '—';
    const start = new Date(startedAt).getTime();
    const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return '—';
    const seconds = Math.floor((end - start) / 1000);
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ${minutes % 60}m`;
  }

  function cardTemplate(item) {
    const core = item.core_parameters || {};
    const run = item.latest_run;
    const completed = run?.completed_steps || 0;
    const requested = run?.requested_steps || core.max_steps || 0;
    const percent = requested ? Math.min(100, Math.round(completed / requested * 100)) : 0;
    const status = statusLabels[item.status] || item.status;
    const runTitle = run ? '最近运行' : '配置状态';
    const runCode = run?.run_id ? run.run_id.slice(0, 12) : `revision ${String(item.revision_no || 1).padStart(3, '0')}`;
    const runValue = run ? `${completed} / ${requested} 步` : item.status === 'DRAFT' ? '待发布' : '尚未运行';
    const runDetail = run ? statusLabels[run.status] || run.status : '保存于独立实验草稿';
    return `
      <article class="experiment-card" data-id="${escapeHtml(item.id)}" data-status="${statusClasses[item.status] || 'draft'}" data-search="${escapeHtml(`${item.name} ${item.goal} ${core.chat_model || ''} ${core.embedding_model || ''}`.toLowerCase())}">
        <div class="experiment-main">
          <div>
            <div class="experiment-name-row"><div class="experiment-name"><button class="experiment-link api-open-experiment">${escapeHtml(item.name)}</button><code>${escapeHtml(item.experiment_key)}</code></div><span class="exp-status ${statusClasses[item.status] || 'draft'}">${escapeHtml(status)}</span></div>
            <p class="exp-description">${escapeHtml(item.goal || '尚未填写实验目标')}</p>
          </div>
          <div class="exp-tags"><span class="exp-tag">Revision ${String(item.revision_no || 1).padStart(3, '0')}</span><span class="exp-tag">${escapeHtml(core.world_name || '世界待配置')}</span></div>
        </div>
        <div class="experiment-params">
          <div class="param-cell"><span>Agent</span><strong>${core.agent_count ?? 0} 个</strong></div>
          <div class="param-cell"><span>聊天模型</span><strong>${escapeHtml(core.chat_model || '待配置')}</strong></div>
          <div class="param-cell"><span>Embedding</span><strong>${escapeHtml(core.embedding_model || '待配置')}</strong></div>
          <div class="param-cell"><span>虚拟时间 / 步长</span><strong>${escapeHtml(formatTime(core.start_time))} · ${core.stride_minutes || '—'}m</strong></div>
          <div class="param-cell"><span>世界</span><strong>${escapeHtml(core.world_name || '待配置')}</strong></div>
          <div class="param-cell"><span>Seed / Revision</span><strong><code>${core.random_seed ?? '未设置'} · rev ${String(item.revision_no || 1).padStart(3, '0')}</code></strong></div>
        </div>
        <div class="experiment-run">
          <div><div class="run-head"><span>${runTitle}</span><code>${escapeHtml(runCode)}</code></div><div class="run-value"><strong>${escapeHtml(runValue)}</strong><span>${escapeHtml(runDetail)}</span></div><div class="run-progress ${item.status === 'PAUSED' ? 'paused' : item.status === 'COMPLETED' ? 'completed' : ''}"><i style="width:${percent}%"></i></div></div>
          <div class="run-foot"><span>${formatTime(item.updated_at)} 更新</span><button class="run-cta ${run ? 'api-open-results' : 'api-open-experiment'}">${run ? '查看运行' : '继续配置'}</button></div>
        </div>
        <button class="experiment-menu" aria-label="实验操作">⋯</button>
      </article>`;
  }

  async function loadExperiments() {
    const generation = ++state.experimentListGeneration;
    const requestState = {
      page: state.page, pageSize: state.pageSize, status: state.status, query: state.query,
    };
    const params = new URLSearchParams({ page: state.page, page_size: state.pageSize, sort: '-updated_at' });
    if (state.query) params.set('q', state.query);
    if (state.status) params.set('status', state.status);
    const data = await api(`/experiments?${params}`);
    if (generation !== state.experimentListGeneration
      || requestState.page !== state.page
      || requestState.pageSize !== state.pageSize
      || requestState.status !== state.status
      || requestState.query !== state.query) return;
    const lastPage = Math.max(1, data.total_pages || 1);
    if (state.page > lastPage) {
      state.page = lastPage;
      await loadExperiments();
      return;
    }
    $('experimentList').innerHTML = data.items.map(cardTemplate).join('');
    $('experimentEmpty').hidden = data.total !== 0;
    $('experimentListFooter').hidden = data.total === 0;
    if (data.total) {
      const first = (data.page - 1) * data.page_size + 1;
      const last = Math.min(data.total, first + data.items.length - 1);
      $('experimentRange').textContent = `显示 ${first}–${last}，共 ${data.total} 个实验`;
      renderPages(data.total_pages || 1);
    }
    updateTabCounts(data.status_counts || {});
  }

  function renderPages(totalPages) {
    $('experimentPagination').hidden = totalPages <= 1;
    $('experimentPages').innerHTML = Array.from({ length: totalPages }, (_, index) => {
      const page = index + 1;
      return `<button class="page-button${page === state.page ? ' active' : ''}" data-api-page="${page}"${page === state.page ? ' aria-current="page"' : ''}>${page}</button>`;
    }).join('');
    $('experimentPrev').disabled = state.page <= 1;
    $('experimentNext').disabled = state.page >= totalPages;
    $('experimentPagination').dataset.totalPages = totalPages;
  }

  function updateTabCounts(counts) {
    const labels = { all: '全部', running: '运行中', queued: '排队中', draft: '草稿', paused: '已暂停', completed: '已完成', abnormal: '异常' };
    document.querySelectorAll('.filter-tab').forEach(tab => {
      const key = tab.dataset.filter;
      const count = key === 'all' ? counts.ALL : key === 'abnormal'
        ? (counts.FAILED || 0) + (counts.CANCELLED || 0)
        : counts[key.toUpperCase()];
      if (Number.isFinite(count)) tab.textContent = `${labels[key] || key} ${count}`;
    });
  }

  async function openExperiment(id, targetPage = 'overview', preferredRunId = null) {
    const generation = ++state.experimentOpenGeneration;
    const [experiment, draft] = await Promise.all([
      api(`/experiments/${id}`),
      api(`/experiments/${id}/draft`).catch(() => null),
    ]);
    if (generation !== state.experimentOpenGeneration) return;
    const published = !draft && experiment.current_published?.id
      ? await api(`/experiments/${id}/revisions/${experiment.current_published.id}`)
      : null;
    if (generation !== state.experimentOpenGeneration) return;
    const changingExperiment = id !== state.selectedExperimentId;
    if (changingExperiment || targetPage !== 'results') resetResultRuntime();
    if (changingExperiment) {
      state.runHistory = [];
      state.runCursor = null;
      state.runHistoryExperimentId = null;
    }
    state.selectedExperimentId = id;
    state.experiment = experiment;
    state.draft = draft;
    state.definition = draft?.definition || published?.definition || null;
    state.revision = draft || published;
    state.latestRunId = experiment.latest_run?.id || null;
    state.selectedRunId = targetPage === 'results' ? preferredRunId || state.latestRunId : null;
    $('navRunCount').textContent = experiment.run_count || 0;
    state.currentExperimentName = experiment.name;
    state.currentExperimentStatus = statusLabels[experiment.status] || experiment.status;
    $('expName').value = experiment.name;
    $('expKey').lastChild.textContent = experiment.experiment_key;
    if (state.definition) fillDraft(state.definition);
    $('addAgentBtn').disabled = !draft;
    fillDefinitionOverview(state.definition, state.revision);
    applyStatusPill(state.currentExperimentStatus);
    setWorkspaceMode(state.currentExperimentStatus);
    goToPage(targetPage);
    if (targetPage === 'results') await loadRunHistory(id, state.selectedRunId);
    fillLatestRunSummary(experiment).catch(reportError);
  }

  function applyExperimentRuntime(experiment) {
    if (!experiment || experiment.id !== state.selectedExperimentId) return;
    state.experiment = experiment;
    state.latestRunId = experiment.latest_run?.id || null;
    state.currentExperimentName = experiment.name;
    state.currentExperimentStatus = statusLabels[experiment.status] || experiment.status;
    $('navRunCount').textContent = experiment.run_count || 0;
    if (!state.dirty) $('expName').value = experiment.name;
    $('expKey').lastChild.textContent = experiment.experiment_key;
    if (state.workspacePage !== 'experiments') $('topbarTitle').textContent = experiment.name;
    applyStatusPill(state.currentExperimentStatus);
    setWorkspaceMode(state.currentExperimentStatus);
  }

  async function syncSelectedExperiment({ refreshDefinition = false, refreshOverview = true } = {}) {
    const experimentId = state.selectedExperimentId;
    if (!experimentId) return;
    const generation = ++state.selectedExperimentGeneration;
    const experiment = await api(`/experiments/${experimentId}`);
    if (generation !== state.selectedExperimentGeneration || experimentId !== state.selectedExperimentId) return;

    const remoteDraft = experiment.current_draft;
    const remotePublished = experiment.current_published;
    const localRevision = state.revision;
    const definitionChanged = refreshDefinition
      || Boolean(remoteDraft && (!state.draft
        || remoteDraft.id !== state.draft.id
        || remoteDraft.lock_version !== state.draft.lock_version))
      || Boolean(!remoteDraft && remotePublished
        && (!localRevision || localRevision.id !== remotePublished.id || localRevision.state !== 'PUBLISHED'));

    let nextRevision = null;
    if (definitionChanged && !state.dirty) {
      nextRevision = remoteDraft
        ? await api(`/experiments/${experimentId}/draft`)
        : remotePublished?.id
          ? await api(`/experiments/${experimentId}/revisions/${remotePublished.id}`)
          : null;
      if (generation !== state.selectedExperimentGeneration || experimentId !== state.selectedExperimentId) return;
      state.draft = remoteDraft ? nextRevision : null;
      state.revision = nextRevision;
      state.definition = nextRevision?.definition || null;
      state.remoteConflictKey = null;
      if (state.definition) {
        fillDraft(state.definition);
        fillDefinitionOverview(state.definition, state.revision);
      }
    } else if (definitionChanged && state.dirty) {
      const conflictKey = `${remoteDraft?.id || 'published'}:${remoteDraft?.lock_version || remotePublished?.id || ''}`;
      if (!remoteDraft) state.draft = null;
      if (state.remoteConflictKey !== conflictKey) {
        state.remoteConflictKey = conflictKey;
        showToast('实验配置已在其他页面发生变化；当前未保存内容仍保留，请重新载入后再继续编辑。', '检测到远端更新');
      }
    }

    if (!remoteDraft && !state.dirty) state.draft = null;
    applyExperimentRuntime(experiment);
    if (state.definition) fillDefinitionOverview(state.definition, state.revision);
    if (refreshOverview) await fillLatestRunSummary(experiment);
  }

  function fillDraft(definition) {
    state.definition = definition;
    const simulation = definition.simulation;
    $('startTime').value = simulation.start_time.slice(0, 16);
    $('stride').value = simulation.stride_minutes;
    $('seed').value = simulation.random_seed;
    $('timezone').value = definition.experiment.timezone;
    $('maxSteps').value = simulation.max_steps;
    $('recordInterval').value = simulation.record_interval_minutes;
    $('logLevel').value = simulation.log_level;
    $('checkpointInterval').value = simulation.checkpoint_interval_steps;
    $('checkpointRetention').value = simulation.checkpoint_retention;
    fillModelFields(definition.models);
    fillBehaviorFields(definition.behavior, definition.results);
    fillWorldFields(definition.world);
    renderAgentDraft(definition.agents);
    renderPromptDraft(definition.prompts);
    $('statAgentCount').textContent = definition.agents.filter(item => item.enabled).length;
    $('navAgentCount').textContent = definition.agents.filter(item => item.enabled).length;
  }

  function fillWorldFields(world) {
    const definition = world.definition || {};
    const tiles = Array.isArray(definition.tiles) ? definition.tiles : [];
    const collision = tiles.filter(tile => tile.collision).length;
    const size = Array.isArray(definition.size) ? definition.size : [];
    const keys = Array.isArray(definition.tile_address_keys) ? definition.tile_address_keys : [];
    $('worldName').value = world.world_name || '';
    $('worldKey').value = world.world_key || '';
    $('worldDefinition').value = JSON.stringify(definition, null, 2);
    $('worldPreviewTitle').textContent = world.world_name || '世界待配置';
    $('worldPreviewMeta').textContent = size.length >= 2 ? `${size[0]} × ${size[1]} 网格 · ${definition.tile_size || '—'}px tile · ${keys.length} 级语义地址` : `${tiles.length} 个 Tile`;
    $('worldAddressKeys').textContent = keys.length ? keys.join(' / ') : '尚未配置';
    $('worldDimensions').textContent = size.length >= 2 ? `height: ${size[0]} · width: ${size[1]} · tile: ${definition.tile_size || '—'}px` : '尚未配置';
    $('worldWalkableTiles').textContent = Math.max(0, tiles.length - collision).toLocaleString('zh-CN');
    $('worldCollisionTiles').textContent = collision.toLocaleString('zh-CN');
    $('worldAgentPositions').textContent = (state.definition?.agents || []).filter(item => item.enabled).length;
    $('worldRevisionCode').textContent = state.revision?.definition_hash ? state.revision.definition_hash.slice(0, 12) : '未发布';
    $('worldAssetInput').disabled = !state.draft;
    $('worldAssetList').innerHTML = world.assets?.length ? world.assets.map(asset => `<div class="asset-row"><span class="asset-icon">▧</span><div class="asset-copy"><strong>${escapeHtml(asset.logical_path)}</strong><span>${escapeHtml(asset.asset_hash.slice(0, 20))}… · ${(asset.size / 1024).toFixed(1)} KB · ${escapeHtml(asset.media_type)}</span></div><span class="asset-state">${state.draft ? '待发布' : '已锁定'}</span></div>`).join('') : '<div class="empty-state"><strong>暂无外部资源</strong></div>';
  }

  function fillDefinitionOverview(definition, revision) {
    if (!definition) return;
    const agents = definition.agents || [];
    const enabled = agents.filter(item => item.enabled).length;
    const prompts = Object.keys(definition.prompts || {});
    const tiles = definition.world?.definition?.tiles || [];
    const revisionNo = revision?.revision_no || 0;
    const hash = revision?.definition_hash || '';
    $('overviewPromptCount').textContent = prompts.length;
    $('overviewPromptMeta').textContent = `/ ${prompts.length} 已定义`;
    $('overviewTileCount').textContent = tiles.length.toLocaleString('zh-CN');
    $('overviewWorldMeta').textContent = definition.world?.world_name || '世界待配置';
    $('overviewBaseRevision').textContent = revision?.base_revision_id ? `revision ${String(Math.max(1, revisionNo - 1)).padStart(3, '0')}` : revisionNo ? `revision ${String(revisionNo).padStart(3, '0')}` : '新实验';
    $('overviewDefinitionHash').textContent = hash ? hash.slice(0, 12) : '草稿未发布';
    $('overviewAlgorithm').textContent = definition.engine?.algorithm_version || '—';
    $('overviewAgentDefinitionCount').textContent = `${agents.length} 份独立角色定义`;
    $('overviewChatCheck').textContent = `${definition.models.chat.provider} · ${definition.models.chat.resolved_model || definition.models.chat.model}`;
    $('overviewEmbeddingCheck').textContent = `${definition.models.embedding.provider} · ${definition.models.embedding.resolved_model || definition.models.embedding.model}`;
    $('overviewAgentCheck').textContent = `${enabled} / ${agents.length} 个角色已启用`;
    $('overviewPromptCheck').textContent = `${prompts.length} 个 Prompt 已物化`;
    $('overviewWorldCheck').textContent = `${definition.world?.world_name || '未命名'} · ${tiles.length} tiles`;
    $('overviewSnapshotHash').textContent = hash ? `sha256:${hash.slice(0, 12)}…` : '草稿尚未发布';
    $('overviewSnapshotAgents').textContent = `agents ×${agents.length}`;
    $('overviewSnapshotPrompts').textContent = `prompts ×${prompts.length}`;
    const errorCount = revision?.validation?.errors?.length || 0;
    $('overviewValidationCount').textContent = errorCount ? `${errorCount} 个阻塞项` : '6 / 6';
    $('overviewValidationCount').className = `chip ${errorCount ? 'amber' : 'teal'}`;
    $('overviewRevisionState').textContent = revision?.state === 'PUBLISHED' ? '已发布修订' : '草稿修订';
    $('overviewRevisionCode').textContent = revisionNo ? `revision ${String(revisionNo).padStart(3, '0')}` : 'draft';
    $('overviewRevisionTime').textContent = formatTime(revision?.updated_at);
    $('overviewRevisionChip').textContent = revision?.state === 'PUBLISHED' ? 'Published Revision' : 'Draft Revision';
    const previewAgents = agents.filter(item => item.enabled).slice(0, 5);
    $('overviewAgentStrip').innerHTML = previewAgents.map(agent => {
      const portrait = `/generative_agents/frontend/static/assets/village/agents/${encodeURIComponent(agent.name)}/portrait.png`;
      return `<div class="avatar"><img src="${portrait}" alt="${escapeHtml(agent.name)}" onerror="this.hidden=true" /></div>`;
    }).join('') + (enabled > previewAgents.length ? `<div class="avatar avatar-more">+${enabled - previewAgents.length}</div>` : '') + `<div class="agent-strip-meta"><strong id="overviewEnabledAgents">${enabled} 个角色已启用</strong><span id="overviewAgentMeta">${agents.length} 份身份定义 · ${agents.length} 份空间定义</span></div>`;
  }

  async function fillLatestRunSummary(experiment) {
    const generation = ++state.latestSummaryGeneration;
    const latest = experiment.latest_run;
    if (!latest?.id) {
      if (generation !== state.latestSummaryGeneration || state.selectedExperimentId !== experiment.id) return;
      $('overviewLatestStep').textContent = '—';
      $('overviewLatestMeta').textContent = '尚未运行';
      $('runSummaryTitle').textContent = '尚无运行';
      $('runSummaryMeta').textContent = '发布 Revision 后可启动';
      $('runSummaryStatus').textContent = '未开始';
      ['runMetricValue1', 'runMetricValue2', 'runMetricValue3'].forEach(id => { $(id).textContent = '—'; });
      return;
    }
    const [run, summary, operations] = await Promise.all([
      api(`/runs/${latest.id}`),
      api(`/runs/${latest.id}/results/summary`),
      api(`/runs/${latest.id}/results/operations`),
    ]);
    if (generation !== state.latestSummaryGeneration
      || state.selectedExperimentId !== experiment.id
      || state.latestRunId !== latest.id) return;
    $('overviewLatestStep').textContent = `${run.completed_steps}/${run.requested_steps}`;
    $('overviewLatestMeta').textContent = statusLabels[run.status] || run.status;
    $('runSummaryTitle').textContent = '最近一次运行';
    $('runSummaryMeta').textContent = `${run.run_id.slice(0, 12)} · revision ${String(run.revision_no || 1).padStart(3, '0')}`;
    $('runSummaryStatus').textContent = statusLabels[run.status] || run.status;
    $('runSummaryStatus').className = `chip ${['FAILED', 'INTERRUPTED', 'CANCELLED'].includes(run.status) ? 'amber' : 'teal'}`;
    $('runMetricLabel1').textContent = '模拟步数';
    $('runMetricValue1').textContent = `${run.completed_steps} / ${run.requested_steps}`;
    $('runMetricLabel2').textContent = 'LLM 调用';
    $('runMetricValue2').textContent = summary.counts.model_calls;
    $('runMetricLabel3').textContent = '物理尝试';
    $('runMetricValue3').textContent = operations.attempts.length;
  }

  function setSwitch(id, active) { $(id).classList.toggle('on', Boolean(active)); }
  function setRange(outputId, value) {
    const output = $(outputId);
    output.textContent = value;
    output.previousElementSibling.value = value;
  }
  function rangeValue(outputId) { return Number($(outputId).previousElementSibling.value); }

  function fillModelFields(models) {
    const chat = models.chat;
    $('chatProvider').value = chat.provider;
    $('chatModel').value = chat.model;
    $('chatBaseUrl').value = chat.base_url || '';
    $('chatTimeout').value = chat.timeout_seconds;
    $('chatMaxTokens').value = chat.max_tokens;
    $('chatTemperature').value = chat.temperature;
    $('chatRetries').value = chat.retry_attempts;
    $('chatBackoff').value = chat.retry_backoff_seconds;
    setSwitch('chatThinking', chat.enable_thinking);
    $('chatSecret').value = '';
    $('chatSecret').placeholder = chat.secret_ref ? '已配置 · 输入新值可替换' : '未设置';
    $('resolvedChatModel').textContent = chat.resolved_model || '尚未解析';
    const contextWindow = Number(chat.context_window || 0);
    $('chatServiceCapability').textContent = contextWindow
      ? `服务上下文窗口 ${contextWindow.toLocaleString('zh-CN')} tokens · Revision 已锁定`
      : '服务上下文：发布启动时自动检测';
    const embedding = models.embedding;
    $('embeddingProvider').value = embedding.provider;
    $('embeddingModel').value = embedding.model;
    $('embeddingBaseUrl').value = embedding.base_url || '';
    $('embeddingTimeout').value = embedding.timeout_seconds;
    $('embeddingTransportRetries').value = embedding.transport_retry_attempts;
    $('embeddingIndexRetries').value = embedding.index_operation_retry_attempts;
    $('embeddingBackoff').value = embedding.retry_backoff_seconds;
    $('embeddingSecret').value = '';
    $('embeddingSecret').placeholder = embedding.secret_ref ? '已配置 · 输入新值可替换' : '未设置';
    $('resolvedEmbeddingModel').textContent = embedding.resolved_model || '尚未解析';
  }

  function fillBehaviorFields(behavior, results) {
    setRange('visionOutput', behavior.percept.vision_radius);
    setRange('bandwidthOutput', behavior.percept.attention_bandwidth);
    setRange('reflectOutput', behavior.think.poignancy_max);
    setRange('focusOutput', behavior.think.reflection_focus_count);
    setRange('insightOutput', behavior.think.reflection_insight_count);
    setRange('retentionOutput', behavior.memory.retention);
    $('maxMemories').value = behavior.memory.max_memories_per_type;
    $('reflectionMemoryLimit').value = behavior.memory.reflection_memory_limit;
    $('recencyDecay').value = behavior.memory.recency_decay;
    setRange('recencyOutput', behavior.memory.recency_weight);
    setRange('relevanceOutput', behavior.memory.relevance_weight);
    setRange('importanceOutput', behavior.memory.importance_weight);
    setRange('chatOutput', behavior.chat.max_iterations);
    $('chatCooldown').value = behavior.chat.cooldown_minutes;
    $('chatStopHour').value = `${String(behavior.chat.stop_after_hour).padStart(2, '0')}:00`;
    setSwitch('repeatDetection', behavior.chat.repeat_detection_enabled);
    $('scheduleRetries').value = behavior.schedule.max_try;
    $('scheduleDiversity').value = behavior.schedule.diversity;
    $('memoryExpireDays').value = behavior.memory.default_expire_days;
    $('projectionInterval').value = results.agent_step_projection_interval_steps;
    $('replayFrames').value = results.replay_interpolation_frames;
    setSwitch('capturePayloads', results.capture_model_payloads);
  }

  function renderAgentDraft(agents) {
    $('agentRows').innerHTML = agents.map(agent => {
      const living = agent.spatial?.address?.living_area || [];
      const location = living.at(-1) || `${agent.coord[0]}, ${agent.coord[1]}`;
      const search = `${agent.name} ${agent.scratch.innate} ${agent.scratch.learned} ${location}`.toLowerCase();
      const portrait = `/generative_agents/frontend/static/assets/village/agents/${encodeURIComponent(agent.name)}/portrait.png`;
      return `<div class="agent-row" data-agent-key="${escapeHtml(agent.agent_key)}" data-search="${escapeHtml(search)}"><input class="checkbox agent-check" type="checkbox" ${agent.enabled ? 'checked' : ''} ${state.draft ? '' : 'disabled'} aria-label="启用 ${escapeHtml(agent.name)}" /><div class="agent-person"><div class="avatar"><img src="${portrait}" alt="" onerror="this.hidden=true" /></div><div><strong>${escapeHtml(agent.name)}</strong><span>${escapeHtml(agent.scratch.innate || '未填写特质')} · ${agent.scratch.age} 岁</span></div></div><div class="truncate">${escapeHtml(agent.currently || '尚未填写当前目标')}</div><div class="location">${escapeHtml(location)}</div><span class="chip teal">定义完整</span><button class="row-actions agent-edit-btn" type="button" aria-label="编辑 ${escapeHtml(agent.name)}">⋯</button></div>`;
    }).join('');
    const enabled = agents.filter(agent => agent.enabled).length;
    $('selectedAgentCount').textContent = `${enabled} / ${agents.length}`;
    $('agentRows').nextElementSibling.innerHTML = `<span>显示全部 ${agents.length} 个实验角色</span><span>每个定义只属于当前实验 Draft</span>`;
  }

  function renderPromptDraft(prompts) {
    const keys = Object.keys(prompts).sort();
    if (!prompts[state.currentPromptKey]) state.currentPromptKey = keys[0] || '';
    $('promptList').innerHTML = `<div class="prompt-list-head"><strong>实验 Prompt</strong><span id="promptListMeta">${keys.length} 个定义</span></div><div class="prompt-group">当前实验独立副本</div>` + keys.map(key => `<button class="prompt-item${key === state.currentPromptKey ? ' active' : ''}" data-prompt="${escapeHtml(key)}"><span>${escapeHtml(key)}.txt</span><i></i></button>`).join('');
    showPrompt(state.currentPromptKey);
  }

  function showPrompt(key) {
    if (!key || !state.definition?.prompts[key]) return;
    state.currentPromptKey = key;
    document.querySelectorAll('.prompt-item').forEach(item => item.classList.toggle('active', item.dataset.prompt === key));
    $('promptTitle').textContent = `${key}.txt`;
    $('promptMeta').textContent = '当前实验独立副本';
    $('promptEditor').value = state.definition.prompts[key].content;
  }

  async function refreshRunHistoryList(experimentId, preferredRunId = state.selectedRunId) {
    const generation = ++state.runHistoryGeneration;
    const sameExperiment = state.runHistoryExperimentId === experimentId;
    const previousItems = sameExperiment ? state.runHistory : [];
    const previousCursor = sameExperiment ? state.runCursor : null;
    const data = await api(`/experiments/${experimentId}/runs?limit=50`);
    if (generation !== state.runHistoryGeneration || experimentId !== state.selectedExperimentId) return null;
    const refreshedIds = new Set(data.items.map(item => item.run_id));
    const retainedHistory = previousItems.filter(item => !refreshedIds.has(item.run_id));
    state.runHistory = [...data.items, ...retainedHistory];
    state.runCursor = retainedHistory.length ? previousCursor : data.next_cursor;
    state.runHistoryExperimentId = experimentId;
    if (preferredRunId && !refreshedIds.has(preferredRunId)) {
      const selected = await api(`/runs/${preferredRunId}`).catch(() => null);
      if (generation !== state.runHistoryGeneration || experimentId !== state.selectedExperimentId) return null;
      if (selected?.experiment_id === experimentId) {
        const selectedIndex = state.runHistory.findIndex(item => item.run_id === preferredRunId);
        if (selectedIndex >= 0) state.runHistory[selectedIndex] = selected;
        else state.runHistory.unshift(selected);
      }
    }
    renderRunHistory(preferredRunId);
    return state.runHistory;
  }

  async function reconcileSelectedRunHistory(experimentId, preferredRunId = state.selectedRunId) {
    const runs = await refreshRunHistoryList(experimentId, preferredRunId);
    if (!runs || experimentId !== state.selectedExperimentId) return;
    if (!runs.length) {
      if (state.workspacePage === 'results') {
        $('resultEmpty').hidden = false;
        $('resultWorkspace').hidden = true;
        state.selectedRunId = null;
      }
      return;
    }
    if (state.workspacePage !== 'results' || state.selectedRunId) return;
    $('resultEmpty').hidden = true;
    $('resultWorkspace').hidden = false;
    const runId = runs.some(item => item.run_id === preferredRunId) ? preferredRunId : runs[0].run_id;
    $('resultRunSelect').value = runId;
    if (typeof loadResults === 'function') await loadResults(runId);
  }

  async function loadRunHistory(experimentId, preferredRunId) {
    const runs = await refreshRunHistoryList(experimentId, preferredRunId);
    if (!runs) return;
    if (!state.runHistory.length) {
      $('resultEmpty').hidden = false;
      $('resultWorkspace').hidden = true;
      state.selectedRunId = null;
      return;
    }
    $('resultEmpty').hidden = true;
    $('resultWorkspace').hidden = false;
    const runId = state.runHistory.some(item => item.run_id === preferredRunId) ? preferredRunId : state.runHistory[0].run_id;
    $('resultRunSelect').value = runId;
    await loadResults(runId);
  }

  function renderRunHistory(selectedRunId = state.selectedRunId) {
    $('navRunCount').textContent = state.experiment?.run_count ?? state.runHistory.length;
    $('resultRunSelect').innerHTML = state.runHistory.map(run => `<option value="${run.run_id}">${run.run_id.slice(0, 12)} · ${statusLabels[run.status] || run.status} · ${run.completed_steps}/${run.requested_steps}</option>`).join('') + '<option value="__all__">查看全部运行…</option>';
    if (selectedRunId && state.runHistory.some(run => run.run_id === selectedRunId)) $('resultRunSelect').value = selectedRunId;
    $('runHistoryList').innerHTML = state.runHistory.length ? state.runHistory.map(run => {
      const status = statusLabels[run.status] || run.status;
      const selected = run.run_id === selectedRunId ? ' selected' : '';
      const chip = ['COMPLETED', 'RUNNING'].includes(run.status) ? 'teal' : ['FAILED', 'CANCELLED', 'INTERRUPTED'].includes(run.status) ? 'amber' : '';
      return `<button class="run-history-item${selected}" data-history-run="${run.run_id}" data-history-search="${escapeHtml(`${run.run_id} ${status} revision ${run.revision_no || ''}`.toLowerCase())}"><strong>${run.run_id.slice(0, 12)} · revision ${String(run.revision_no || 1).padStart(3, '0')}</strong><small>${formatTime(run.created_at)} · ${run.completed_steps} / ${run.requested_steps} 步</small><span class="chip ${chip}">${escapeHtml(status)}</span></button>`;
    }).join('') : '<div class="empty-state"><strong>暂无运行记录</strong></div>';
    $('loadMoreRuns').hidden = !state.runCursor;
    $('loadMoreRuns').disabled = false;
    $('loadMoreRuns').textContent = '加载更多';
  }

  async function loadMoreRunHistory() {
    if (!state.runCursor || !state.selectedExperimentId) return;
    const generation = state.runHistoryGeneration;
    const cursor = state.runCursor;
    $('loadMoreRuns').disabled = true;
    $('loadMoreRuns').textContent = '正在加载…';
    const data = await api(`/experiments/${state.selectedExperimentId}/runs?limit=50&cursor=${encodeURIComponent(cursor)}`);
    if (generation !== state.runHistoryGeneration || cursor !== state.runCursor) return;
    const known = new Set(state.runHistory.map(item => item.run_id));
    state.runHistory.push(...data.items.filter(item => !known.has(item.run_id)));
    state.runCursor = data.next_cursor;
    renderRunHistory(state.selectedRunId);
    const query = $('runHistorySearch').value.trim().toLowerCase();
    document.querySelectorAll('.run-history-item').forEach(item => {
      item.hidden = Boolean(query && !item.dataset.historySearch.includes(query));
    });
  }

  function resetResultRuntime() {
    state.resultGeneration += 1;
    state.resultRequestGeneration += 1;
    state.operationFactsGeneration += 1;
    state.logGeneration += 1;
    state.checkpointGeneration += 1;
    state.agentDetailGeneration += 1;
    state.currentRun = null;
    state.agentResults = [];
    state.selectedRunId = null;
    if (state.resultRefreshTimer) clearTimeout(state.resultRefreshTimer);
    state.resultRefreshTimer = null;
    state.eventSource?.close();
    state.eventSource = null;
    closeLogStream();
    state.operationsAbortController?.abort();
    state.operationsAbortController = null;
    state.operationsRunId = null;
    teardownReplay();
  }

  async function loadResults(runId) {
    const generation = ++state.resultGeneration;
    const experimentId = state.selectedExperimentId;
    teardownReplay();
    state.conversationGeneration += 1;
    state.memoryGeneration += 1;
    state.selectedRunId = runId;
    if (state.eventSource) state.eventSource.close();
    closeLogStream();
    state.operationsAbortController?.abort();
    state.operationsAbortController = null;
    state.operationsRunId = null;
    if (state.resultRefreshTimer) clearTimeout(state.resultRefreshTimer);
    await refreshResultData(runId, generation);
    if (generation !== state.resultGeneration) return;
    const eventPage = await api(`/runs/${runId}/events?limit=500`);
    while (eventPage.next_after_id && eventPage.items.length === 500) {
      const cursor = eventPage.next_after_id;
      const nextPage = await api(`/runs/${runId}/events?limit=500&after_id=${cursor}`);
      if (generation !== state.resultGeneration) return;
      if (!nextPage.items.length || nextPage.next_after_id <= cursor) break;
      eventPage.items = nextPage.items;
      eventPage.next_after_id = nextPage.next_after_id;
    }
    if (generation !== state.resultGeneration) return;
    state.eventSource = new EventSource(`/api/v1/runs/${runId}/events/stream?after_id=${eventPage.next_after_id || 0}`);
    const handleRunEvent = event => {
      if (generation !== state.resultGeneration || experimentId !== state.selectedExperimentId) return;
      const payload = JSON.parse(event.data).payload || {};
      applyRunActivity({
        experiment_id: experimentId,
        run_id: runId,
        event_type: event.type,
        payload,
      });
      scheduleResultRefresh(runId, generation);
    };
    ['queue', 'state', 'reconcile', 'progress', 'result_rewound',
      'artifact_queued', 'artifact_running', 'artifact_retry', 'artifact_ready', 'artifact_error'].forEach(
      eventType => state.eventSource.addEventListener(eventType, handleRunEvent)
    );
    state.eventSource.onerror = () => scheduleResultRefresh(runId, generation);
  }

  function scheduleResultRefresh(runId, generation) {
    if (state.resultRefreshTimer
      || generation !== state.resultGeneration
      || runId !== state.selectedRunId) return;
    state.resultRefreshTimer = setTimeout(() => {
      state.resultRefreshTimer = null;
      if (generation === state.resultGeneration && runId === state.selectedRunId) {
        refreshResultData(runId, generation).catch(reportError);
      }
    }, 2000);
  }

  async function refreshResultData(runId, generation = state.resultGeneration) {
    const requestGeneration = state.resultRequestGeneration = (state.resultRequestGeneration || 0) + 1;
    const [run, summary, timeline, agents, conversations, memories, operations] = await Promise.all([
      api(`/runs/${runId}`), api(`/runs/${runId}/results/summary`),
      api(`/runs/${runId}/results/timeline?limit=500`),
      api(`/runs/${runId}/results/agents`), api(`/runs/${runId}/results/conversations?limit=50`),
      api(`/runs/${runId}/results/memories?limit=50`), api(`/runs/${runId}/results/operations`),
    ]);
    if (generation !== state.resultGeneration
      || requestGeneration !== state.resultRequestGeneration
      || runId !== state.selectedRunId) return;
    state.currentRun = run;
    $('resultStatusChip').textContent = statusLabels[run.status] || run.status;
    $('resultRevision').textContent = `revision ${String(run.revision_no || 1).padStart(3, '0')} · ${String(run.definition_hash || '').slice(0, 10)}`;
    $('resultWindow').textContent = run.virtual_time ? formatTime(run.virtual_time) : '等待首个已提交步骤';
    $('resultSync').textContent = summary.result_state === 'COMPLETE' ? '结果完整' : summary.result_state === 'EMPTY' ? '等待结果' : `部分结果 · v${summary.result_version}`;
    $('resultStepMetric').textContent = `${summary.available_step} / ${run.requested_steps}`;
    $('resultConversationMetric').textContent = summary.counts.conversations;
    $('resultMemoryMetric').textContent = summary.counts.memories;
    $('resultLlmMetric').textContent = summary.counts.model_calls;
    $('resultDurationMetric').textContent = formatDuration(run.started_at, run.finished_at);
    renderSummary(summary, agents.items);
    renderTimeline(timeline);
    renderAgents(agents.items);
    renderConversations(conversations.items);
    renderMemories(memories.items);
    renderOperations(operations);
    if (state.operationsRunId !== runId) {
      loadOperationsWorkspace(runId, generation).catch(error => {
        if (error.name !== 'AbortError') reportError(error);
      });
    } else {
      refreshOperationFacts(runId, generation).catch(error => {
        if (error.name !== 'AbortError') console.warn('运行事实刷新失败', error);
      });
    }
    renderRunActions(run);
    if (document.querySelector('[data-result-panel="timeline"]')?.classList.contains('active')) {
      ensureReplayPlayer(runId, generation).catch(reportError);
    } else if (state.replayPlayer && state.replayRunId === runId) {
      state.replayPlayer.refreshAvailable().catch(error => {
        if (error.name !== 'AbortError') console.warn('回放边界刷新失败', error);
      });
    }
    syncWorkspaceUrl();
  }

  function applyRunActivity(activity) {
    // Persisted events are invalidation signals. Their backlog can be older than
    // a completed API reconciliation, so event payloads never overwrite facts.
    if (activity.run_id === state.selectedRunId && state.workspacePage === 'results') {
      scheduleResultRefresh(activity.run_id, state.resultGeneration);
    }
    scheduleGlobalReconcile({ experimentId: activity.experiment_id });
  }

  function scheduleGlobalReconcile({ experimentId = null, full = false } = {}) {
    if (experimentId) state.pendingActivityExperimentIds.add(experimentId);
    state.forceGlobalRefresh = state.forceGlobalRefresh || full;
    if (state.globalRefreshTimer) return;
    state.globalRefreshTimer = setTimeout(() => {
      state.globalRefreshTimer = null;
      const experimentIds = new Set(state.pendingActivityExperimentIds);
      const force = state.forceGlobalRefresh;
      state.pendingActivityExperimentIds.clear();
      state.forceGlobalRefresh = false;
      reconcileGlobalState({ experimentIds, full: force }).catch(error => {
        console.warn('全局状态同步暂时失败，将在下次事件或页面恢复时重试。', error);
      });
    }, 250);
  }

  async function reconcileGlobalState({ experimentIds = new Set(), full = false } = {}) {
    const selectedId = state.selectedExperimentId;
    const selectedRunId = state.selectedRunId;
    const resultGeneration = state.resultGeneration;
    const selectedChanged = Boolean(selectedId && (full || experimentIds.has(selectedId)));
    const tasks = [loadExperiments()];
    if (selectedChanged) {
      tasks.push(syncSelectedExperiment({ refreshOverview: true }));
      if (state.workspacePage === 'results') {
        tasks.push(reconcileSelectedRunHistory(selectedId, selectedRunId || state.latestRunId));
        if (full && selectedRunId) tasks.push(refreshResultData(selectedRunId, resultGeneration));
      }
    }
    const settled = await Promise.allSettled(tasks);
    const failure = settled.find(item => item.status === 'rejected');
    if (failure) throw failure.reason;
  }

  async function startGlobalActivityStream() {
    const generation = ++state.activityGeneration;
    if (state.activitySource) state.activitySource.close();
    const tail = await api('/events?tail=true');
    await reconcileGlobalState({ full: true });
    if (generation !== state.activityGeneration) return;
    const source = new EventSource(`/api/v1/events/stream?after_id=${tail.next_after_id || 0}`);
    state.activitySource = source;
    source.addEventListener('activity', event => {
      if (generation !== state.activityGeneration) return;
      try { applyRunActivity(JSON.parse(event.data)); }
      catch (error) { console.warn('忽略无法解析的全局活动事件。', error); }
    });
    source.addEventListener('sync', () => {
      if (generation === state.activityGeneration) scheduleGlobalReconcile({ full: true });
    });
    source.onerror = () => {
      if (generation === state.activityGeneration) scheduleGlobalReconcile({ full: true });
    };
  }

  function isRunRecoverable(run) {
    return Boolean(run?.recoverable)
      && Number(run.recoverable_step) > 0
      && ['PAUSED', 'FAILED', 'INTERRUPTED'].includes(run.status);
  }

  function renderRunActions(run) {
    const pauseResume = $('runPauseResumeBtn');
    const cancel = $('runCancelBtn');
    const continueRun = $('runContinueBtn');
    const again = $('runAgainBtn');
    const canContinue = isRunRecoverable(run);
    pauseResume.hidden = run.status !== 'RUNNING';
    pauseResume.textContent = '暂停运行';
    cancel.hidden = !['QUEUED', 'RUNNING', 'PAUSE_REQUESTED', 'PAUSED'].includes(run.status);
    continueRun.hidden = !canContinue;
    continueRun.textContent = canContinue ? `继续执行 · Step ${run.recoverable_step}` : '继续执行';
    again.hidden = !['COMPLETED', 'CANCELLED', 'FAILED', 'INTERRUPTED'].includes(run.status);
    $('openReplayBtn').classList.toggle('btn-primary', !canContinue);
  }

  function renderAgents(items) {
    state.agentResults = [...items].sort((a, b) => String(a.display_name || a.agent_key).localeCompare(String(b.display_name || b.agent_key), 'zh-CN'));
    $('agentResultCount').textContent = state.agentResults.length;
    const options = '<option value="all">全部 Agent</option>' + items.map(item => `<option value="${escapeHtml(item.agent_key)}">${escapeHtml(item.display_name || item.agent_key)}</option>`).join('');
    $('conversationAgentFilter').innerHTML = options;
    $('memoryAgentFilter').innerHTML = options;
    if (!items.length) {
      state.selectedAgentKey = null;
      $('resultAgentButtons').innerHTML = '<div class="empty-state"><strong>暂无 Agent 结果</strong><span>首个步骤提交后会在这里生成 Agent 内容。</span></div>';
      $('resultAgentDetail').innerHTML = '<div class="empty-state"><strong>暂无 Agent 内容</strong></div>';
      $('resultAgentDetail').dataset.agentKey = '';
      return;
    }
    if (!state.agentResults.some(item => item.agent_key === state.selectedAgentKey)) {
      state.selectedAgentKey = state.agentResults[0].agent_key;
    }
    renderAgentTabs();
    showAgentDetail(state.selectedAgentKey).catch(reportError);
  }

  function renderAgentTabs() {
    const query = $('resultAgentSearch').value.trim().toLowerCase();
    const status = state.agentStatusFilter;
    const visible = state.agentResults.filter(item => {
      const searchable = [item.display_name, item.agent_key, item.address, item.currently,
        item.latest_action, item.definition?.daily_plan, item.definition?.learned].join(' ').toLowerCase();
      return (status === 'all' || item.latest_activity_kind === status) && (!query || searchable.includes(query));
    });
    if (!visible.length) {
      $('resultAgentButtons').innerHTML = '<div class="empty-state"><strong>没有符合条件的 Agent</strong><span>尝试清除搜索词或切换状态筛选。</span></div>';
      $('resultAgentDetail').innerHTML = '<div class="empty-state"><strong>没有可显示的 Agent 内容</strong><span>调整上方筛选后继续查看。</span></div>';
      $('resultAgentDetail').dataset.agentKey = '';
      return;
    }
    if (!visible.some(item => item.agent_key === state.selectedAgentKey)) state.selectedAgentKey = visible[0].agent_key;
    $('resultAgentButtons').innerHTML = visible.map(item => {
      const active = item.agent_key === state.selectedAgentKey;
      const name = item.display_name || item.agent_key;
      const statusText = { CHAT: '对话中', MOVING: '移动中', REST: '休息中', OTHER: '活动中' }[item.latest_activity_kind] || item.latest_activity_kind;
      return `<button type="button" role="tab" class="agent-result-tab${active ? ' active' : ''}" data-agent-key="${escapeHtml(item.agent_key)}" data-agent-status="${escapeHtml(item.latest_activity_kind)}" aria-selected="${String(active)}" aria-controls="resultAgentDetail" tabindex="${active ? '0' : '-1'}">
        <span class="agent-tab-avatar-fallback" aria-hidden="true">${escapeHtml(name.slice(0, 1))}</span><img class="agent-tab-portrait" src="${escapeHtml(item.portrait_url || '')}" alt=""/><span class="agent-tab-copy"><strong><i class="agent-tab-status" aria-hidden="true"></i>${escapeHtml(name)}</strong><small>${escapeHtml(statusText)} · 计划 ${item.plan_count || 0} · 事件 ${item.event_count || 0}</small></span>
      </button>`;
    }).join('');
    document.querySelectorAll('.agent-tab-portrait').forEach(image => image.addEventListener('error', () => {
      image.hidden = true;
      image.previousElementSibling.style.display = 'grid';
    }, { once: true }));
  }

  function ensureAgentTabVisible(tab) {
    const strip = $('resultAgentButtons');
    if (!tab || !strip) return;
    const left = tab.offsetLeft;
    const right = left + tab.offsetWidth;
    if (left < strip.scrollLeft) strip.scrollTo({ left: Math.max(0, left - 8), behavior: 'smooth' });
    else if (right > strip.scrollLeft + strip.clientWidth) strip.scrollTo({ left: right - strip.clientWidth + 8, behavior: 'smooth' });
  }

  async function showAgentDetail(agentKey) {
    state.selectedAgentKey = agentKey;
    const runId = state.selectedRunId;
    const generation = ++state.agentDetailGeneration;
    let activeTab = null;
    document.querySelectorAll('.agent-result-tab').forEach(tab => {
      const active = tab.dataset.agentKey === agentKey;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      if (active) activeTab = tab;
    });
    ensureAgentTabVisible(activeTab);
    const panel = $('resultAgentDetail');
    panel.dataset.agentKey = agentKey;
    panel.innerHTML = '<div class="agent-result-loading">正在读取 Agent 结构化内容…</div>';
    const detail = await api(`/runs/${runId}/results/agents/${encodeURIComponent(agentKey)}`);
    if (generation !== state.agentDetailGeneration
      || detail.run_id !== state.selectedRunId
      || runId !== state.selectedRunId
      || agentKey !== state.selectedAgentKey) return;
    if (panel.dataset.agentKey === agentKey) panel.innerHTML = `<div class="agent-result-body">${renderAgentDetail(detail)}</div>`;
  }

  function renderAgentDetail(detail) {
    const agent = detail.agent;
    const definition = agent.definition || {};
    const counts = detail.content_counts || {};
    const goal = definition.daily_plan || definition.initial_currently || agent.currently || '未记录角色目标';
    const latestPlan = detail.latest_schedule?.items?.[0];
    const currentPlan = latestPlan ? agentPlanText(latestPlan) : goal;
    const totalMinutes = Math.max(1, Object.values(agent.activity_minutes || {}).reduce((sum, value) => sum + value, 0));
    const activeMinutes = (agent.activity_minutes?.MOVING || 0) + (agent.activity_minutes?.CHAT || 0) + (agent.activity_minutes?.OTHER || 0);
    return `<div class="agent-result-overview">
      <div class="agent-overview-card"><small>角色目标</small><strong>${escapeHtml(goal)}</strong></div>
      <div class="agent-overview-card"><small>当前行动</small><strong>${escapeHtml(agent.currently || detail.actions?.[0]?.action || '尚无行动')}</strong></div>
      <div class="agent-overview-card"><small>位置与状态</small><strong>${escapeHtml(agent.address || '位置未记录')}<br>更新至 Step ${agent.updated_step}</strong></div>
      <div class="agent-overview-card"><small>活动占比</small><strong>非休息活动 ${Math.round(activeMinutes / totalMinutes * 100)}%</strong><div class="agent-overview-meter"><i style="width:${Math.round(activeMinutes / totalMinutes * 100)}%"></i></div></div>
    </div>
    <div class="agent-content-filters" role="tablist" aria-label="Agent 结构化内容">${agentContentChip('plan','计划',counts.plans)}${agentContentChip('event','事件',counts.events)}${agentContentChip('action','行动',counts.actions)}${agentContentChip('conversation','对话',counts.conversations)}${agentContentChip('memory','记忆',counts.memories)}${agentContentChip('state','状态变化',counts.state_changes)}</div>
    <div class="agent-content-grid">
      ${renderAgentPlanSection(detail, currentPlan)}
      ${renderAgentEventSection(detail.events || [])}
      ${renderAgentActionSection(detail.actions || [])}
      ${renderAgentConversationSection(detail.conversations || [])}
      ${renderAgentMemorySection(detail.memories || [])}
      ${renderAgentStateSection(detail.state_changes || [])}
    </div>`;
  }

  function agentContentChip(kind, label, count) {
    const active = state.selectedAgentContent === kind;
    return `<button type="button" role="tab" aria-selected="${String(active)}" tabindex="${active ? '0' : '-1'}" class="agent-content-filter${active ? ' active' : ''}" data-agent-content="${kind}">${label}${count === null || count === undefined ? '' : `<i>${count}</i>`}</button>`;
  }

  function agentSection(kind, icon, title, subtitle, count, content) {
    const hidden = state.selectedAgentContent !== kind ? ' hidden' : '';
    return `<section class="agent-content-section" role="tabpanel" data-agent-content-section="${kind}"${hidden}><div class="agent-section-head"><span class="agent-section-icon">${icon}</span><span><strong>${title}</strong><span>${subtitle}</span></span><span class="agent-section-count">${count}</span></div>${content}</section>`;
  }

  function agentRecord(time, title, detail, tag, tagClass = '') {
    return `<div class="agent-record"><time>${escapeHtml(time || '—')}</time><span class="agent-record-copy"><strong>${escapeHtml(title || '未命名记录')}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ''}</span><span class="agent-record-tag ${tagClass}">${escapeHtml(tag || '记录')}</span></div>`;
  }

  function renderAgentPlanSection(detail, currentPlan) {
    const definition = detail.agent.definition || {};
    const revisions = detail.plan_revisions || [];
    const records = revisions.slice(0, 4).map(item => {
      const plan = item.items?.length ? agentPlanText(item.items[0]) : '日程内容未记录';
      return agentRecord(`Step ${item.effective_step}`, plan, item.reason || '日程修订', `修订 ${item.revision_no}`);
    }).join('');
    const empty = records || '<div class="agent-section-empty">本次运行尚未产生计划修订；这里显示已发布角色的初始计划。</div>';
    return agentSection('plan','▤','计划','初始目标、日程与计划修订',revisions.length,
      `<div class="agent-current-plan"><small>当前计划</small><strong>${escapeHtml(currentPlan)}</strong><p>${escapeHtml(definition.daily_plan || definition.lifestyle || '未记录日常计划')}</p></div><div class="agent-record-list">${empty}</div>`);
  }

  function renderAgentEventSection(events) {
    const rows = events.slice(0, 8).map(event => {
      const payload = event.payload || {};
      const title = event.title && event.title !== event.event_type ? event.title : agentEventTitle(event.event_type, payload);
      const detail = event.detail || agentEventDetail(event.event_type, payload) || event.location || '';
      return agentRecord(`Step ${event.step_no}`, title, detail, agentEventLabel(event.event_type), 'event');
    }).join('');
    return agentSection('event','✦','事件','产生、感知与参与的领域事件',events.length,
      `<div class="agent-record-list">${rows || '<div class="agent-section-empty">当前 Agent 尚未产生可归属的领域事件。</div>'}</div>`);
  }

  function renderAgentActionSection(actions) {
    const rows = actions.slice(0, 8).map(action => {
      const context = action.decision_context || {};
      const perceptions = context.perceptions?.length || 0;
      const schedule = Object.keys(context.schedule || {});
      const evidence = (perceptions || schedule.length)
        ? `<div class="agent-decision-context">感知 ${perceptions} 条${schedule.length ? ` · 当步计划：${escapeHtml(schedule[0])}` : ''}</div>` : '';
      return `<div class="agent-record"><time>Step ${action.step_no}</time><span class="agent-record-copy"><strong>${escapeHtml(action.action || '未记录行动')}</strong><span>${escapeHtml(action.address || '位置未记录')} · ${escapeHtml(formatTime(action.virtual_time))}</span>${evidence}</span><span class="agent-record-tag">${escapeHtml(agentActivityLabel(action.activity_kind))}</span></div>`;
    }).join('');
    return agentSection('action','➜','行动','执行动作、移动与当步决策上下文',actions.length,
      `<div class="agent-record-list">${rows || '<div class="agent-section-empty">尚无已提交行动。</div>'}</div>`);
  }

  function renderAgentConversationSection(items) {
    const rows = items.slice(0, 6).map(item => agentRecord(`Step ${item.start_step}`,
      (item.participant_names || item.participants || []).join(' ↔ '),
      item.summary || `${item.message_count} 条消息 · ${item.location || '位置未记录'}`,
      `${item.message_count} 条`, '')) .join('');
    return agentSection('conversation','◌','对话','与其他 Agent 的实际交流',items.length,
      `<div class="agent-record-list">${rows || '<div class="agent-section-empty">当前 Agent 尚未产生对话。相邻的计划、事件和行动仍可用于定位原因。</div>'}</div>`);
  }

  function renderAgentMemorySection(items) {
    const rows = items.slice(0, 8).map(item => agentRecord(`Step ${item.created_step ?? '—'}`,
      item.description || item.memory_id,
      `重要度 ${item.poignancy ?? '—'} · ${item.state || 'UNKNOWN'}`,
      item.type || '记忆', 'memory')).join('');
    return agentSection('memory','◇','记忆','新增、访问与淘汰的记忆',items.length,
      `<div class="agent-record-list">${rows || '<div class="agent-section-empty">当前 Agent 尚未提交记忆变化。</div>'}</div>`);
  }

  function renderAgentStateSection(items) {
    const rows = items.slice(0, 10).map(item => agentRecord(`Step ${item.step_no}`,
      `${item.title}发生变化`, `${item.before || '—'} → ${item.after || '—'}`, item.kind)).join('');
    return agentSection('state','↕','状态变化','位置、当前状态与行动切换',items.length,
      `<div class="agent-record-list">${rows || '<div class="agent-section-empty">当前采样窗口内没有状态变化。</div>'}</div>`);
  }

  function agentPlanText(item) {
    if (!item || typeof item !== 'object') return String(item || '未记录计划');
    return item.description || item.activity || item.describe || item.task || item.plan || JSON.stringify(item);
  }

  function agentActivityLabel(kind) {
    return { CHAT: '对话', MOVING: '移动', REST: '休息', OTHER: '行动' }[kind] || kind || '行动';
  }

  function agentEventLabel(kind) {
    return { MOVED: '移动', CONVERSATION: '参与', MEMORY: '记忆', SCHEDULE: '计划' }[kind] || kind || '事件';
  }

  function agentEventTitle(kind, payload) {
    if (kind === 'MOVED') return 'Agent 移动到新的位置';
    if (kind === 'CONVERSATION') return 'Agent 参与了一次对话';
    return payload.title || kind || '领域事件';
  }

  function agentEventDetail(kind, payload) {
    if (kind === 'MOVED') return `${JSON.stringify(payload.from_coord || [])} → ${JSON.stringify(payload.to_coord || [])}`;
    if (kind === 'CONVERSATION') return `${payload.message_count || 0} 条消息`;
    return payload.detail || '';
  }

  function renderConversations(items) {
    $('conversationIndex').innerHTML = items.length ? items.map(item => `<button class="conversation-button" data-conversation-id="${item.conversation_id}"><div><strong>${escapeHtml((item.participant_names || item.participants).join(' ↔ '))}</strong><span>${escapeHtml(item.summary || '未生成摘要')} · ${item.message_count} 条消息</span></div><time>step ${item.start_step}</time></button>`).join('') : '<div class="empty-state"><strong>暂无对话</strong></div>';
    if (items.length) {
      const selected = items.some(item => item.conversation_id === state.selectedConversationId) ? state.selectedConversationId : items[0].conversation_id;
      showConversation(selected).catch(reportError);
    } else {
      state.selectedConversationId = null;
      $('conversationTitle').textContent = '暂无对话';
      $('conversationMeta').textContent = '当前运行还没有提交对话记录';
      $('conversationMessages').innerHTML = '<div class="empty-state"><strong>无可显示消息</strong></div>';
    }
  }

  async function showConversation(conversationId) {
    state.selectedConversationId = conversationId;
    const runId = state.selectedRunId;
    const detail = await api(`/runs/${runId}/results/conversations/${conversationId}`);
    if (detail.run_id !== state.selectedRunId
      || runId !== state.selectedRunId
      || conversationId !== state.selectedConversationId) return;
    document.querySelectorAll('.conversation-button').forEach(button => button.classList.toggle('active', button.dataset.conversationId === conversationId));
    $('conversationTitle').textContent = (detail.participant_names || detail.participants).join(' 与 ');
    $('conversationMeta').textContent = `${formatTime(detail.started_at)} · ${detail.duration_minutes} 分钟 · ${detail.location || '位置未记录'} · ${detail.message_count} 条消息`;
    $('conversationMessages').innerHTML = detail.messages.map((message, index) => `<div class="message${index % 2 ? ' reply' : ''}"><div><strong>${escapeHtml(message.speaker_name || message.speaker_agent_key)} · 第 ${message.sequence} 条</strong><p>${escapeHtml(message.content)}</p></div></div>`).join('');
  }

  function renderMemories(items) {
    $('memoryResultCount').textContent = `显示 ${items.length} 条已提交记忆`;
    $('memoryRows').innerHTML = items.map(item => `<tr data-memory-agent="${escapeHtml(item.agent_key)}" data-memory-type="${escapeHtml(item.type)}"><td><span class="memory-type ${escapeHtml(item.type)}">${escapeHtml(item.type)}</span></td><td>${escapeHtml(item.agent_name || item.agent_key)}</td><td class="memory-desc">${escapeHtml(item.description || '—')}</td><td>${item.poignancy ?? '—'}</td><td>${item.created_step} / ${item.last_accessed_step ?? '—'}</td><td><code>${escapeHtml(item.memory_id)}</code></td></tr>`).join('');
  }

  function renderSummary(summary, agents) {
    $('summaryKeyEvents').innerHTML = summary.key_events?.length ? summary.key_events.map(event => `<div class="result-event"><time>${formatTime(event.virtual_time)}</time><div class="event-track"><i></i></div><div class="event-copy"><strong>${escapeHtml(event.title)}</strong><span>${escapeHtml(event.detail || event.primary_agent_name || '')}</span></div></div>`).join('') : '<div class="empty-state"><strong>暂无关键事件</strong></div>';
    const active = [...agents].sort((a, b) => b.conversation_count - a.conversation_count).slice(0, 5);
    const maximum = Math.max(1, ...active.map(item => item.conversation_count));
    $('summaryActiveAgents').innerHTML = active.length ? active.map((item, index) => `<div class="rank-row"><span class="rank-index">${String(index + 1).padStart(2, '0')}</span><div class="rank-person"><strong>${escapeHtml(item.display_name || item.agent_key)}</strong></div><span class="rank-bar"><i style="width:${Math.round(item.conversation_count / maximum * 100)}%"></i></span><span class="rank-value">${item.conversation_count} 场</span></div>`).join('') : '<div class="empty-state"><strong>暂无活跃 Agent</strong></div>';
    const edges = summary.conversation_network?.edges || [];
    $('summaryNetworkMeta').textContent = `最大连通分量 ${largestComponentSize(edges)}`;
    const names = [...new Set(edges.flatMap(edge => [edge.agent_a_name, edge.agent_b_name]))].slice(0, 8);
    const positions = names.map((name, index) => ({ name, x: 50 + 36 * Math.cos(index / Math.max(1, names.length) * Math.PI * 2), y: 50 + 36 * Math.sin(index / Math.max(1, names.length) * Math.PI * 2) }));
    const lines = edges.slice(0, 12).map(edge => {
      const a = positions.find(item => item.name === edge.agent_a_name); const b = positions.find(item => item.name === edge.agent_b_name);
      return a && b ? `<line x1="${a.x * 6}" y1="${a.y * 2.5}" x2="${b.x * 6}" y2="${b.y * 2.5}" stroke-width="${Math.min(8, 1 + edge.conversation_count)}"/>` : '';
    }).join('');
    $('summaryNetwork').innerHTML = names.length ? `<svg viewBox="0 0 600 250" preserveAspectRatio="none" aria-hidden="true">${lines}</svg>${positions.map(item => `<button class="relation-node" style="left:${item.x}%;top:${item.y}%">${escapeHtml(item.name)}</button>`).join('')}` : '<div class="empty-state"><strong>暂无对话关系</strong></div>';
  }

  function largestComponentSize(edges) {
    const graph = new Map();
    edges.forEach(edge => {
      const a = edge.agent_a_name || edge.agent_a; const b = edge.agent_b_name || edge.agent_b;
      if (!graph.has(a)) graph.set(a, new Set()); if (!graph.has(b)) graph.set(b, new Set());
      graph.get(a).add(b); graph.get(b).add(a);
    });
    let largest = 0; const seen = new Set();
    graph.forEach((_neighbors, start) => {
      if (seen.has(start)) return;
      let size = 0; const stack = [start]; seen.add(start);
      while (stack.length) {
        const current = stack.pop(); size += 1;
        graph.get(current).forEach(next => { if (!seen.has(next)) { seen.add(next); stack.push(next); } });
      }
      largest = Math.max(largest, size);
    });
    return largest;
  }

  function renderTimeline(timeline) {
    timeline.steps ||= [];
    timeline.events ||= [];
    timeline.agent_steps ||= [];
    timeline.requested_steps ||= state.currentRun?.requested_steps || 0;
    state.timeline = timeline;
    renderActivityChart(timeline.steps);
    const slider = $('timelineRange');
    slider.min = timeline.steps.length ? timeline.steps[0].step_no : 0;
    slider.max = Math.max(0, timeline.available_step);
    slider.value = timeline.available_step;
    updateTimelineStep(Number(slider.value));
  }

  function renderActivityChart(steps) {
    $('activityChartMeta').textContent = steps.length ? `${steps.length} 个已提交步骤 · 点击“时间探索”查看明细` : '等待首个已提交步骤';
    if (!steps.length) {
      $('activityChart').innerHTML = '<text x="285" y="118">暂无已提交活动数据</text>';
      return;
    }
    const width = 658; const left = 42; const top = 28; const height = 166;
    const values = steps.flatMap(step => [step.actions, step.conversations, step.memories_created]);
    const maximum = Math.max(1, ...values);
    const point = (value, index) => {
      const x = steps.length === 1 ? left + width / 2 : left + index / (steps.length - 1) * width;
      const y = top + height - Number(value || 0) / maximum * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    };
    const series = key => steps.map((step, index) => point(step[key], index)).join(' ');
    const labels = [...new Map(
      [steps[0], steps[Math.floor((steps.length - 1) / 2)], steps.at(-1)]
        .map(step => [step.step_no, step])
    ).values()];
    $('activityChart').innerHTML = `
      <line class="grid" x1="${left}" y1="${top}" x2="${left + width}" y2="${top}"/>
      <line class="grid" x1="${left}" y1="${top + height}" x2="${left + width}" y2="${top + height}"/>
      <text x="12" y="${top + 4}">${maximum}</text><text x="22" y="${top + height + 4}">0</text>
      <polyline class="line" fill="none" points="${series('actions')}"/>
      <polyline class="chat-line" fill="none" points="${series('conversations')}"/>
      <polyline class="memory-line" fill="none" points="${series('memories_created')}"/>
      ${labels.map(step => {
        const index = steps.findIndex(item => item.step_no === step.step_no);
        const x = steps.length === 1 ? left + width / 2 : left + index / (steps.length - 1) * width;
        return `<text x="${x - 15}" y="216">#${step.step_no}</text>`;
      }).join('')}`;
  }

  function updateTimelineStep(stepNo) {
    const timeline = state.timeline;
    if (!timeline) return;
    const step = [...timeline.steps].reverse().find(item => item.step_no <= stepNo);
    $('timelineStep').textContent = `Step ${String(stepNo).padStart(3, '0')} / ${timeline.requested_steps || 0}`;
    $('timelineTime').textContent = step ? formatTime(step.virtual_time) : '等待结果';
    $('mapTimeLabel').textContent = `${step ? formatTime(step.virtual_time) : '—'} · Step ${String(stepNo).padStart(3, '0')}`;
    if (state.replayPlayer && state.replayRunId === state.selectedRunId) {
      state.replayPlayer.seek(stepNo).catch(reportError);
    }
    const events = timeline.events.filter(item => Math.abs(item.step_no - stepNo) <= 1);
    $('timelineStreamMeta').textContent = `Step ${stepNo} 附近 · ${events.length} 条`;
    $('timelineStreamItems').innerHTML = events.length ? events.map(event => `<div class="stream-item"><time class="stream-time">${formatTime(event.virtual_time)}</time><div class="stream-copy"><strong>${escapeHtml(event.title)}</strong><span>${escapeHtml(event.detail || event.location || '')}</span></div></div>`).join('') : '<div class="empty-state"><strong>当前窗口没有领域事件</strong></div>';
  }

  function teardownReplay() {
    state.replayAbortController?.abort();
    state.replayPlayer?.destroy();
    state.replayAbortController = null;
    state.replayPlayer = null;
    state.replayRunId = null;
    state.replayPlaying = false;
    state.replayMarkerFacts.clear();
    if ($('replayTimelineMarkers')) $('replayTimelineMarkers').innerHTML = '';
    if ($('replayAgentSelect')) $('replayAgentSelect').innerHTML = '<option value="">选择 Agent</option>';
    clearReplayInspector();
  }

  async function ensureReplayPlayer(runId, generation) {
    if (state.replayPlayer && state.replayRunId === runId) {
      await state.replayPlayer.refreshAvailable();
      return state.replayPlayer;
    }
    teardownReplay();
    const replayAbortController = new AbortController();
    state.replayAbortController = replayAbortController;
    state.replayRunId = runId;
    const replayPlayer = new GAReplayPlayer({
      canvas: $('resultMapCanvas'),
      onStatus: status => {
        if (runId !== state.selectedRunId || generation !== state.resultGeneration) return;
        $('replayStatus').textContent = status.state === 'AVAILABLE_STEP' ? `可播放至 Step ${status.availableStep}` : status.state;
        state.replayPlaying = status.state === 'PLAYING';
        $('timelinePlay').textContent = state.replayPlaying ? 'Ⅱ' : '▶';
        if (Number.isFinite(status.availableStep)) {
          $('timelineRange').max = status.availableStep;
          $('timelineRange').disabled = status.availableStep < 1;
        }
      },
      onStep: payload => renderReplayStep(payload, runId, generation),
      onAgent: payload => renderReplayInspector(payload, runId, generation),
      onError: error => {
        if (runId !== state.selectedRunId || generation !== state.resultGeneration) return;
        $('replayStatus').textContent = error.code || '回放资源不可用';
        console.warn('受控回放事实不可用', error);
      },
    });
    state.replayPlayer = replayPlayer;
    await replayPlayer.loadRun(runId, { signal: replayAbortController.signal });
    if (runId !== state.selectedRunId || generation !== state.resultGeneration || replayAbortController.signal.aborted) return null;
    $('replayAgentSelect').innerHTML = '<option value="">选择 Agent</option>' + replayPlayer.manifest.agents.map(agent => `<option value="${escapeHtml(agent.agent_key)}">${escapeHtml(agent.display_name)}</option>`).join('');
    const restoredAgentKey = GAReplayPlayer.resolveAgentSelection(
      state.selectedReplayAgentKey,
      state.selectedReplayRevisionId,
      state.currentRun?.revision_id,
      replayPlayer.manifest.agents,
    );
    if (restoredAgentKey) {
      $('replayAgentSelect').value = restoredAgentKey;
      replayPlayer.selectAgent(restoredAgentKey);
    } else {
      state.selectedReplayAgentKey = null;
      state.selectedReplayRevisionId = null;
      $('replayAgentSelect').value = '';
      replayPlayer.selectAgent(null);
      clearReplayInspector();
    }
    $('timelineRange').min = replayPlayer.availableStep ? 1 : 0;
    $('timelineRange').max = replayPlayer.availableStep;
    $('timelineRange').value = replayPlayer.currentStep || replayPlayer.availableStep;
    return replayPlayer;
  }

  function renderReplayStep(payload, runId, generation) {
    if (runId !== state.selectedRunId || generation !== state.resultGeneration || !payload.step) return;
    const step = payload.step;
    $('timelineRange').max = payload.availableStep || state.replayPlayer.availableStep;
    $('timelineRange').value = step.step_no;
    $('timelineStep').textContent = `Step ${step.step_no} / ${payload.availableStep || state.replayPlayer.availableStep}`;
    $('timelineTime').textContent = formatTime(step.virtual_time);
    $('mapTimeLabel').textContent = `${formatTime(step.virtual_time)} · Step ${step.step_no}`;
    const selectedAgent = $('replayAgentSelect').value;
    if (selectedAgent) state.replayPlayer.selectAgent(selectedAgent);
    const conversations = $('replayLayerConversations').checked ? step.conversations : [];
    const events = $('replayLayerKeyEvents').checked ? step.domain_events : [];
    const facts = [
      ...conversations.map(item => ({ type: '对话', text: (item.messages || []).map(message => `${message.speaker_agent_key}: ${message.content}`).join(' · ') })),
      ...events.map(item => ({ type: item.event_type || '事件', text: JSON.stringify(item.payload || {}) })),
    ];
    $('timelineStreamMeta').textContent = `Step ${step.step_no} · ${facts.length} 条`;
    $('timelineStreamItems').innerHTML = facts.length ? facts.map(item => `<div class="stream-item"><time class="stream-time">${escapeHtml(item.type)}</time><div class="stream-copy"><span>${escapeHtml(item.text)}</span></div></div>`).join('') : '<div class="empty-state"><strong>当前步骤没有可见事件</strong></div>';
    if (step.attempt_boundary || step.checkpoint || step.conversations.length || step.domain_events.length) {
      state.replayMarkerFacts.set(step.step_no, {
        attempt: step.attempt_boundary,
        checkpoint: step.checkpoint,
        conversation: Boolean(step.conversations.length),
        event: Boolean(step.domain_events.length),
      });
      renderReplayMarkers(payload.availableStep || state.replayPlayer.availableStep);
    }
  }

  function renderReplayMarkers(availableStep) {
    $('replayTimelineMarkers').innerHTML = [...state.replayMarkerFacts.entries()].map(([step, fact]) => {
      const kind = fact.checkpoint ? 'checkpoint' : fact.conversation ? 'conversation' : '';
      const label = [fact.attempt ? 'Attempt 边界' : '', fact.checkpoint ? 'Checkpoint' : '', fact.conversation ? '对话' : '', fact.event ? '事件' : ''].filter(Boolean).join(' / ');
      return `<button type="button" class="replay-marker ${kind}" data-replay-step="${step}" style="left:${Math.max(0, Math.min(100, step / Math.max(1, availableStep) * 100))}%" aria-label="Step ${step} · ${escapeHtml(label)}"></button>`;
    }).join('');
  }

  function renderReplayInspector(payload, runId, generation) {
    if (runId !== state.selectedRunId || generation !== state.resultGeneration) return;
    const fact = payload.fact; const step = payload.step;
    if (!fact || !step) {
      clearReplayInspector();
      return;
    }
    const key = fact.agent_key;
    state.selectedReplayAgentKey = key;
    state.selectedReplayRevisionId = state.currentRun?.revision_id || null;
    if ([...$('replayAgentSelect').options].some(option => option.value === key)) {
      $('replayAgentSelect').value = key;
    }
    const conversations = step.conversations.filter(item => (item.participant_agent_keys || []).includes(key));
    const memories = step.memory_deltas.filter(item => item.agent_key === key);
    const schedules = step.schedule_revisions.filter(item => item.agent_key === key);
    $('replayInspectorLocation').textContent = `${fact.address.join(' / ')} · [${fact.coord.join(', ')}]`;
    $('replayInspectorAction').textContent = fact.action.description || fact.action.emoji || '—';
    $('replayInspectorCurrently').textContent = fact.currently || '—';
    $('replayInspectorConversation').textContent = conversations.length ? `${conversations.length} 场 · ${conversations.flatMap(item => item.messages || []).length} 条消息` : '本步无对话';
    $('replayInspectorMemories').textContent = memories.length ? memories.map(item => `${item.kind} · ${item.description || item.memory_id}`).join('；') : '本步无记忆变化';
    $('replayInspectorSchedule').textContent = schedules.length ? schedules.map(item => `${item.reason} · ${item.item_count} 项`).join('；') : (fact.schedule_item_id || '本步无日程修订');
  }

  function clearReplayInspector() {
    if (!$('replayInspectorLocation')) return;
    $('replayInspectorLocation').textContent = '未选择 Agent';
    $('replayInspectorAction').textContent = '—';
    $('replayInspectorCurrently').textContent = '—';
    $('replayInspectorConversation').textContent = '—';
    $('replayInspectorMemories').textContent = '—';
    $('replayInspectorSchedule').textContent = '—';
  }

  function renderOperations(operations) {
    $('modelUsageRows').innerHTML = '<div class="usage-row head"><span>用途</span><span>调用数</span><span>最大延迟</span><span>重试</span></div>' + operations.model_usage.map(item => `<div class="usage-row"><strong>${escapeHtml(item.purpose)}</strong><code>${item.logical_calls}</code><span>${item.max_latency_ms} ms</span><span>${item.retries}</span></div>`).join('');
    const activeJobs = (operations.artifact_jobs || []).filter(item => item.status !== 'SUCCEEDED');
    const jobRows = activeJobs.map(item => `<div class="artifact-result"><span class="artifact-result-icon">◌</span><div><strong>${escapeHtml(item.type)}</strong><span>${escapeHtml(item.status)} · ${Math.round((item.progress || 0) * 100)}%${item.error_summary ? ` · ${escapeHtml(item.error_summary)}` : ''}</span></div><span class="chip ${item.status === 'FAILED' ? 'amber' : 'teal'}">${escapeHtml(item.status)}</span></div>`).join('');
    const artifactRows = operations.artifacts.map(item => `<div class="artifact-result"><span class="artifact-result-icon">▣</span><div><strong>${escapeHtml(item.logical_name)}</strong><span>${Math.ceil(item.size_bytes / 1024)} KB · ${escapeHtml(item.type)} · ${escapeHtml(item.sha256.slice(0, 12))}…</span></div><a class="artifact-action" href="/api/v1/runs/${state.selectedRunId}/artifacts/${item.artifact_id}/download">下载</a></div>`).join('');
    $('artifactMeta').textContent = `${operations.artifacts.length} 个可用 · ${activeJobs.length} 个构建中或失败`;
    $('artifactRows').innerHTML = jobRows + artifactRows || '<div class="empty-state"><strong>暂无制品，可点击“下载全部”创建结果包</strong></div>';
  }

  function closeLogStream() {
    if (state.logSource) state.logSource.close();
    state.logSource = null;
  }

  function renderLogViewport() {
    const query = $('logSearch').value.trim().toLowerCase();
    const level = $('logLevelFilter').value;
    const rows = state.logRecords.filter(item => {
      const message = String(item.message || '');
      return (!level || item.level === level) && (!query || message.toLowerCase().includes(query));
    });
    $('logViewport').textContent = rows.length
      ? rows.map(item => `${item.timestamp ? `[${item.timestamp}] ` : ''}${item.level || 'INFO'} ${item.message || ''}`).join('\n')
      : '当前筛选条件下没有日志。';
    if ($('logAutoFollow').checked) $('logViewport').scrollTop = $('logViewport').scrollHeight;
  }

  function parseLogLine(line) {
    const value = line.endsWith('\r') ? line.slice(0, -1) : line;
    if (!value) return null;
    try {
      const document = JSON.parse(value);
      if (document && typeof document === 'object') {
        return {
          timestamp: document.timestamp || document.time || document.created_at || null,
          level: String(document.level || document.levelname || 'INFO').toUpperCase(),
          message: String(document.message || document.event || JSON.stringify(document)),
        };
      }
    } catch (_) {}
    const match = value.match(/\b(TRACE|DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\b/i);
    const level = (match?.[1] || 'INFO').toUpperCase();
    return { timestamp: null, level: level === 'WARN' ? 'WARNING' : level, message: value };
  }

  function consumeLogPage(page, { initial = false } = {}) {
    if (initial) state.logDiscardUntilNewline = page.starts_mid_line;
    let chunk = page.content || '';
    if (state.logDiscardUntilNewline) {
      const boundary = chunk.indexOf('\n');
      if (boundary < 0) return;
      chunk = chunk.slice(boundary + 1);
      state.logDiscardUntilNewline = false;
    }
    const parts = `${state.logCarry}${chunk}`.split('\n');
    state.logCarry = parts.pop() || '';
    parts.forEach(line => {
      const parsed = parseLogLine(line);
      if (parsed) state.logRecords.push(parsed);
    });
    if (page.eof && page.terminal && state.logCarry) {
      const parsed = parseLogLine(state.logCarry);
      if (parsed) state.logRecords.push(parsed);
      state.logCarry = '';
    }
    if (state.logRecords.length > 5000) state.logRecords.splice(0, state.logRecords.length - 5000);
    renderLogViewport();
  }

  function startLogStream(runId, attemptId, generation) {
    closeLogStream();
    if (state.logStreamPaused || generation !== state.logGeneration || runId !== state.selectedRunId) return;
    const query = new URLSearchParams({ cursor: String(state.logCursor) });
    if (state.logFileId) query.set('file_id', state.logFileId);
    const source = new EventSource(`/api/v1/runs/${runId}/attempts/${attemptId}/log/stream?${query}`);
    state.logSource = source;
    source.addEventListener('log', event => {
      if (generation !== state.logGeneration || runId !== state.selectedRunId || attemptId !== state.selectedAttemptId) return;
      const page = JSON.parse(event.data);
      state.logCursor = page.next_cursor;
      state.logFileId = page.file_id;
      consumeLogPage(page);
    });
    source.addEventListener('eof', () => closeLogStream());
    source.addEventListener('error', event => {
      if (event.data) {
        try {
          const failure = JSON.parse(event.data).error || {};
          if (failure.code === 'ATTEMPT_LOG_ROTATED' || failure.code === 'ATTEMPT_LOG_TRUNCATED' || failure.details?.reset_cursor === 0) {
            closeLogStream();
            state.logFileId = null;
            state.logCursor = 0;
            if (generation === state.logGeneration && runId === state.selectedRunId && attemptId === state.selectedAttemptId) {
              selectAttemptLog(runId, attemptId).catch(reportError);
            }
            return;
          }
          closeLogStream();
          reportError(new Error(failure.message || '日志流已中断'));
        } catch (_) { closeLogStream(); }
      }
    });
  }

  async function selectAttemptLog(runId, attemptId) {
    const generation = ++state.logGeneration;
    closeLogStream();
    state.selectedAttemptId = attemptId;
    state.logRecords = [];
    state.logCarry = '';
    state.logDiscardUntilNewline = false;
    state.logCursor = 0;
    state.logFileId = null;
    $('logViewport').textContent = '正在读取日志…';
    $('logDownload').href = `/api/v1/runs/${runId}/attempts/${attemptId}/log/download`;
    let page;
    let cursor = 0;
    let initial = true;
    do {
      page = await api(`/runs/${runId}/attempts/${attemptId}/log?cursor=${cursor}&limit_bytes=65536`);
      if (generation !== state.logGeneration || runId !== state.selectedRunId || attemptId !== state.selectedAttemptId) return;
      state.logCursor = page.next_cursor;
      state.logFileId = page.file_id;
      consumeLogPage(page, { initial });
      initial = false;
      cursor = page.next_cursor;
    } while (!page.eof);
    if (!(page.eof && page.terminal)) startLogStream(runId, attemptId, generation);
  }

  function renderAttempts(document) {
    const options = document.items.map(item => `<option value="${item.attempt_id}">Attempt ${String(item.attempt_no).padStart(2, '0')} · ${escapeHtml(item.status)}</option>`).join('');
    $('attemptLogSelect').innerHTML = options || '<option value="">暂无 Attempt</option>';
    $('traceAttemptSelect').innerHTML = options || '<option value="">暂无 Attempt</option>';
    const selected = document.items.some(item => item.attempt_id === state.selectedAttemptId)
      ? state.selectedAttemptId : document.default_attempt_id;
    state.selectedAttemptId = selected;
    if (selected) {
      $('attemptLogSelect').value = selected;
      const traceSelected = document.items.some(item => item.attempt_id === state.selectedTraceAttemptId)
        ? state.selectedTraceAttemptId : selected;
      state.selectedTraceAttemptId = traceSelected;
      $('traceAttemptSelect').value = traceSelected;
    }
    $('attemptRows').innerHTML = document.items.length ? document.items.map(item => `
      <div class="attempt-item" data-attempt-id="${item.attempt_id}">
        <span class="attempt-num">${String(item.attempt_no).padStart(2, '0')}</span>
        <div><strong>${escapeHtml(item.stop_reason || item.status)}</strong><span>Step ${item.start_step} → ${item.end_step ?? '运行中'} · ${formatTime(item.started_at)}${item.error_message ? ` · ${escapeHtml(item.error_message)}` : ''}</span></div>
        <span class="chip ${item.status === 'ENDED' ? 'teal' : 'amber'}">${item.log.available ? `${Math.ceil(item.log.size_bytes / 1024)} KB` : '无日志'}</span>
      </div>`).join('') : '<div class="empty-state"><strong>尚未创建执行尝试</strong></div>';
    return selected;
  }

  function renderModelTraces() {
    const header = '<div class="trace-row head"><span>状态</span><span>用途 / 模型</span><span>延迟</span><span>重试</span><span>序号</span></div>';
    $('modelTraceRows').innerHTML = header + (state.traceItems.length ? state.traceItems.map(item => `<button type="button" class="trace-row" data-trace-id="${item.trace_id}"><span class="chip ${item.status === 'SUCCESS' ? 'teal' : 'amber'}">${escapeHtml(item.status || item.event_type)}</span><strong>${escapeHtml(item.purpose || 'unknown')}<br><code>${escapeHtml(item.resolved_model || item.model || '—')}</code></strong><span>${item.latency_ms ?? '—'} ms</span><span>${item.retry ? `#${item.attempt_no}` : '—'}</span><code>${item.event_seq}</code></button>`).join('') : '<div class="empty-state"><strong>该 Attempt 尚无模型调用明细</strong></div>');
    $('loadMoreTraces').hidden = state.traceEof;
  }

  async function loadModelTraces(runId, attemptId, signal, { append = false, factsGeneration = null } = {}) {
    if (factsGeneration !== null && factsGeneration !== state.operationFactsGeneration) return;
    if (!attemptId) {
      $('modelTraceRows').innerHTML = '<div class="empty-state"><strong>暂无模型调用</strong></div>';
      state.traceItems = [];
      state.traceCursor = null;
      state.traceEof = true;
      $('loadMoreTraces').hidden = true;
      return;
    }
    if (!append) {
      state.traceItems = [];
      state.traceCursor = 0;
      state.traceEof = false;
      $('modelTraceDetail').hidden = true;
      $('tracePayloadMore').hidden = true;
      state.traceDetailState = null;
    }
    const purpose = $('tracePurposeFilter').value.trim();
    const suffix = purpose ? `&purpose=${encodeURIComponent(purpose)}` : '';
    const page = await api(`/runs/${runId}/model-traces?attempt_id=${encodeURIComponent(attemptId)}&cursor=${state.traceCursor ?? 0}&limit=200${suffix}`, { signal });
    if ((factsGeneration !== null && factsGeneration !== state.operationFactsGeneration)
      || runId !== state.selectedRunId || signal.aborted
      || (attemptId !== state.selectedAttemptId && attemptId !== state.selectedTraceAttemptId)) return;
    const known = new Set(state.traceItems.map(item => item.trace_id));
    state.traceItems.push(...page.items.filter(item => !known.has(item.trace_id)));
    state.traceCursor = page.next_cursor;
    state.traceEof = page.eof;
    renderModelTraces();
  }

  async function loadTraceDetail({ append = false } = {}) {
    const current = state.traceDetailState;
    if (!current || current.runId !== state.selectedRunId) return;
    const cursor = append ? current.cursor : 0;
    const detail = await api(`/runs/${current.runId}/model-traces/${encodeURIComponent(current.traceId)}?cursor=${cursor}&limit_bytes=16384`);
    if (state.traceDetailState !== current || current.runId !== state.selectedRunId || current.generation !== state.logGeneration) return;
    current.content = append ? current.content + (detail.content || '') : (detail.content || '');
    current.cursor = detail.next_cursor;
    current.fileId = detail.file_id;
    current.trace = detail.trace;
    current.payloadAvailable = detail.payload_available;
    $('modelTraceDetail').hidden = false;
    $('modelTraceDetail').innerHTML = `<strong>${escapeHtml(detail.trace.purpose || detail.trace.event_type || '模型调用')}</strong><pre>${escapeHtml(JSON.stringify(detail.trace, null, 2))}</pre>${detail.payload_available ? `<strong>Payload（已脱敏）</strong><pre>${escapeHtml(current.content)}</pre>` : '<p>该记录未保存 Payload。</p>'}`;
    $('tracePayloadMore').hidden = detail.next_cursor === null;
  }

  function renderSystemEvents(items) {
    const merged = new Map(state.operationEvents.map(item => [item.id, item]));
    items.forEach(item => merged.set(item.id, item));
    state.operationEvents = [...merged.values()].sort((left, right) => left.id - right.id);
    const query = $('eventSearch').value.trim().toLowerCase();
    const filtered = state.operationEvents.filter(item => !query || `${item.event_type} ${JSON.stringify(item.payload)}`.toLowerCase().includes(query));
    const header = '<div class="event-row head"><span>时间</span><span>事件</span><span>事实</span></div>';
    $('systemEventRows').innerHTML = header + (filtered.length ? filtered.map(item => `<div class="event-row"><time>${formatTime(item.created_at)}</time><strong>${escapeHtml(item.event_type)}</strong><code>${escapeHtml(JSON.stringify(item.payload || {}))}</code></div>`).join('') : '<div class="empty-state"><strong>暂无匹配事件</strong></div>');
  }

  async function loadSystemEvents(runId, signal, { append = false, factsGeneration = null } = {}) {
    if (factsGeneration !== null && factsGeneration !== state.operationFactsGeneration) return;
    if (!append) {
      state.operationEvents = [];
      state.eventCursor = 0;
    }
    const page = await api(`/runs/${runId}/events?after_id=${state.eventCursor}&limit=200`, { signal });
    if ((factsGeneration !== null && factsGeneration !== state.operationFactsGeneration)
      || runId !== state.selectedRunId || signal.aborted) return;
    const known = new Set(state.operationEvents.map(item => item.id));
    state.operationEvents.push(...page.items.filter(item => !known.has(item.id)));
    state.eventCursor = page.next_after_id;
    $('loadMoreEvents').hidden = page.items.length < 200;
    renderSystemEvents(state.operationEvents);
  }

  function renderCheckpoints(document, generation) {
    if (generation !== state.checkpointGeneration) return;
    const header = '<div class="checkpoint-row head"><span>Step</span><span>状态</span><span>Attempt</span><span>Hash / 时间</span><span>大小</span><span>校验 / 恢复</span></div>';
    $('checkpointRows').innerHTML = header + (document.items.length ? document.items.map(item => `<button class="checkpoint-row" type="button" data-checkpoint-step="${item.step_no}"><code>${item.step_no}</code><span class="chip ${item.validated ? 'teal' : 'amber'}">${escapeHtml(item.status)}</span><code>${escapeHtml((item.attempt_id || '—').slice(0, 8))}</code><span><code>${escapeHtml((item.bundle_sha256 || '—').slice(0, 12))}</code><br>${formatTime(item.virtual_time)}</span><span>${Math.ceil(item.size_bytes / 1024)} KB · ${item.file_count} 文件</span><span>${item.resumable ? '<strong>可恢复</strong>' : escapeHtml(item.validation?.reason || item.validation?.code || '—')}</span></button>`).join('') : '<div class="empty-state"><strong>当前 Run 尚无检查点</strong></div>');
  }

  async function showCheckpointDetail(runId, stepNo) {
    const generation = state.checkpointGeneration;
    const detail = await api(`/runs/${runId}/checkpoints/${stepNo}`);
    if (generation !== state.checkpointGeneration || runId !== state.selectedRunId || detail.run_id !== runId) return;
    $('checkpointDetail').hidden = false;
    state.checkpointPreviewState = null;
    $('checkpointPreview').hidden = true;
    $('checkpointPreviewMore').hidden = true;
    const agentRows = detail.agent_state.items.map(item => `<li><strong>${escapeHtml(item.agent_key)}</strong> · 坐标 ${escapeHtml(JSON.stringify(item.coord))} · ${escapeHtml(item.currently || '无当前状态')}<br>${escapeHtml(item.action?.event || '无动作')} @ ${escapeHtml(item.action?.address || '—')} · 日程 ${item.schedule_item_count} 项</li>`).join('') || '<li>无 Agent 状态</li>';
    const conversationRows = detail.conversations.items.map(item => `<li><strong>${escapeHtml((item.participants || []).join(' ↔ '))}</strong><br>${(item.messages || []).map(message => `${escapeHtml(message.speaker || message.speaker_agent_key || '')}: ${escapeHtml(message.content || '')}`).join('<br>') || '无消息'}</li>`).join('') || '<li>无对话</li>';
    const storageRows = detail.storage.groups.map(item => `<li><strong>${escapeHtml(item.agent_key)}</strong> / ${escapeHtml(item.index_type)} · ${item.file_count} 文件 · ${item.size_bytes} bytes</li>`).join('') || '<li>无存储快照</li>';
    const fileRows = detail.files.map(item => `<li><code>${escapeHtml(item.path)}</code> · ${item.size_bytes} bytes · ${escapeHtml((item.sha256 || '').slice(0, 12))}</li>`).join('');
    $('checkpointDetailGrid').innerHTML = `
      <div><strong>Bundle / 校验</strong><p>${escapeHtml(detail.status)} · ${escapeHtml(detail.validation?.code || '—')}<br>Step ${detail.step_no} · Attempt ${escapeHtml(detail.attempt_id || '—')}<br><code>${escapeHtml(detail.bundle_sha256 || '')}</code></p></div>
      <div><strong>Agent 状态 (${detail.agent_state.count})</strong><ul>${agentRows}</ul></div>
      <div><strong>对话 (${detail.conversations.count})</strong><ul>${conversationRows}</ul></div>
      <div><strong>Storage (${detail.storage.group_count})</strong><ul>${storageRows}</ul></div>
      <div><strong>文件 manifest (${detail.file_count})</strong><ul>${fileRows}</ul></div>
      <div><button class="btn btn-sm" data-checkpoint-preview="state" data-step="${stepNo}">预览状态 JSON</button></div>
      <div><button class="btn btn-sm" data-checkpoint-preview="conversation" data-step="${stepNo}">预览对话 JSON</button></div>
      <div><button class="btn btn-sm" data-checkpoint-export="${stepNo}">创建 ZIP</button></div>`;
  }

  async function loadCheckpointPreview({ append = false } = {}) {
    const current = state.checkpointPreviewState;
    if (!current || current.runId !== state.selectedRunId) return;
    const query = new URLSearchParams({
      section: current.section,
      cursor: String(append ? current.cursor : 0),
      limit_bytes: '32768',
    });
    if (append && current.fileId) query.set('file_id', current.fileId);
    const page = await api(`/runs/${current.runId}/checkpoints/${current.step}/preview?${query}`);
    if (state.checkpointPreviewState !== current || current.runId !== state.selectedRunId || current.generation !== state.checkpointGeneration) return;
    current.content = append ? current.content + page.content : page.content;
    current.cursor = page.next_cursor;
    current.fileId = page.file_id;
    $('checkpointPreview').hidden = false;
    $('checkpointPreview').textContent = current.content;
    $('checkpointPreviewMore').hidden = page.next_cursor === null;
  }

  async function refreshOperationFacts(runId, resultGeneration) {
    const factsGeneration = state.operationFactsGeneration = (state.operationFactsGeneration || 0) + 1;
    const checkpointGeneration = ++state.checkpointGeneration;
    const signal = state.operationsAbortController?.signal;
    if (!signal) return;
    const [checkpoints, attempts] = await Promise.all([
      api(`/runs/${runId}/checkpoints`, { signal }),
      api(`/runs/${runId}/attempts`, { signal }),
    ]);
    if (factsGeneration !== state.operationFactsGeneration
      || resultGeneration !== state.resultGeneration
      || runId !== state.selectedRunId || signal.aborted) return;
    renderCheckpoints(checkpoints, checkpointGeneration);
    renderAttempts(attempts);
    await loadSystemEvents(runId, signal, { append: true, factsGeneration });
    const traceAttempt = $('traceAttemptSelect').value;
    if (traceAttempt) await loadModelTraces(runId, traceAttempt, signal, { append: true, factsGeneration });
  }

  async function loadOperationsWorkspace(runId, resultGeneration) {
    state.operationsAbortController?.abort();
    const controller = new AbortController();
    state.operationsAbortController = controller;
    state.operationsRunId = runId;
    const factsGeneration = state.operationFactsGeneration = (state.operationFactsGeneration || 0) + 1;
    const logGeneration = ++state.logGeneration;
    const checkpointGeneration = ++state.checkpointGeneration;
    const [attempts, checkpoints] = await Promise.all([
      api(`/runs/${runId}/attempts`, { signal: controller.signal }),
      api(`/runs/${runId}/checkpoints`, { signal: controller.signal }),
    ]);
    if (factsGeneration !== state.operationFactsGeneration
      || resultGeneration !== state.resultGeneration
      || runId !== state.selectedRunId || controller.signal.aborted) return;
    const selectedAttempt = renderAttempts(attempts);
    renderCheckpoints(checkpoints, checkpointGeneration);
    await loadSystemEvents(runId, controller.signal, { factsGeneration });
    await loadModelTraces(runId, selectedAttempt, controller.signal, { factsGeneration });
    if (logGeneration !== state.logGeneration || runId !== state.selectedRunId) return;
    const selectedMeta = attempts.items.find(item => item.attempt_id === selectedAttempt);
    if (selectedAttempt && selectedMeta?.log.available) await selectAttemptLog(runId, selectedAttempt);
    else $('logViewport').textContent = '该 Attempt 尚未产生可读日志。';
  }

  function simulationStartTime(value, timezone) {
    if (timezone === 'Asia/Shanghai') return `${value}:00+08:00`;
    if (timezone === 'UTC') return `${value}:00Z`;
    return new Date(value).toISOString();
  }

  async function saveSecret(inputId, existingRef) {
    const input = $(inputId);
    if (!input.value) return existingRef || null;
    const path = existingRef ? `/secrets/${existingRef}/replacement` : '/secrets';
    const saved = await api(path, {
      method: 'POST',
      body: JSON.stringify({ kind: 'OPENAI_API_KEY', value: input.value }),
    });
    input.value = '';
    return saved.secret_id;
  }

  async function saveDraft({ silent = false } = {}) {
    if (!state.draft) return;
    if (state.currentPromptKey) {
      state.draft.definition.prompts[state.currentPromptKey].content = $('promptEditor').value;
    }
    const requestedName = $('expName').value.trim();
    if (requestedName !== state.experiment.name) {
      state.experiment = await api(`/experiments/${state.selectedExperimentId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          row_version: state.experiment.row_version,
          name: requestedName,
          goal: state.experiment.goal || '',
        }),
      });
      state.draft = await api(`/experiments/${state.selectedExperimentId}/draft`);
      state.currentExperimentName = requestedName;
      applyExperimentRuntime(state.experiment);
    }
    const definition = structuredClone(state.draft.definition);
    definition.experiment.name = requestedName;
    definition.experiment.timezone = $('timezone').value;
    definition.simulation.start_time = simulationStartTime($('startTime').value, $('timezone').value);
    definition.simulation.stride_minutes = Number($('stride').value);
    definition.simulation.max_steps = Number($('maxSteps').value);
    definition.simulation.record_interval_minutes = Number($('recordInterval').value);
    definition.simulation.random_seed = Number($('seed').value);
    definition.simulation.log_level = $('logLevel').value;
    definition.simulation.checkpoint_interval_steps = Number($('checkpointInterval').value);
    definition.simulation.checkpoint_retention = Number($('checkpointRetention').value);

    const oldChat = definition.models.chat;
    const chatProvider = $('chatProvider').value;
    const chatBaseUrl = $('chatBaseUrl').value.trim();
    const chatIdentityUnchanged = oldChat.provider === chatProvider && oldChat.model === $('chatModel').value.trim() && String(oldChat.base_url || '').replace(/\/$/, '') === chatBaseUrl.replace(/\/$/, '');
    const chatSecretRef = await saveSecret('chatSecret', oldChat.secret_ref);
    definition.models.chat = {
      provider: chatProvider,
      model: $('chatModel').value.trim(),
      resolved_model: chatIdentityUnchanged ? oldChat.resolved_model : null,
      context_window: chatIdentityUnchanged ? oldChat.context_window : null,
      base_url: chatBaseUrl,
      secret_ref: chatSecretRef,
      timeout_seconds: Number($('chatTimeout').value),
      max_tokens: Number($('chatMaxTokens').value),
      temperature: Number($('chatTemperature').value),
      enable_thinking: $('chatThinking').classList.contains('on'),
      retry_attempts: Number($('chatRetries').value),
      retry_backoff_seconds: Number($('chatBackoff').value),
    };

    const oldEmbedding = definition.models.embedding;
    const embeddingProvider = $('embeddingProvider').value;
    const embeddingBaseUrl = $('embeddingBaseUrl').value.trim();
    const embeddingIdentityUnchanged = oldEmbedding.provider === embeddingProvider && oldEmbedding.model === $('embeddingModel').value.trim() && String(oldEmbedding.base_url || '').replace(/\/$/, '') === embeddingBaseUrl.replace(/\/$/, '');
    const embeddingSecretRef = await saveSecret('embeddingSecret', oldEmbedding.secret_ref);
    definition.models.embedding = {
      provider: embeddingProvider,
      model: $('embeddingModel').value.trim(),
      resolved_model: embeddingIdentityUnchanged ? oldEmbedding.resolved_model : null,
      timeout_seconds: Number($('embeddingTimeout').value),
      transport_retry_attempts: Number($('embeddingTransportRetries').value),
      index_operation_retry_attempts: Number($('embeddingIndexRetries').value),
      retry_backoff_seconds: Number($('embeddingBackoff').value),
      ...(embeddingProvider === 'hugging_face' ? {} : { base_url: embeddingBaseUrl, secret_ref: embeddingSecretRef }),
    };

    definition.behavior.percept.vision_radius = rangeValue('visionOutput');
    definition.behavior.percept.attention_bandwidth = rangeValue('bandwidthOutput');
    definition.behavior.think.poignancy_max = rangeValue('reflectOutput');
    definition.behavior.think.reflection_focus_count = rangeValue('focusOutput');
    definition.behavior.think.reflection_insight_count = rangeValue('insightOutput');
    definition.behavior.memory.retention = rangeValue('retentionOutput');
    definition.behavior.memory.max_memories_per_type = Number($('maxMemories').value);
    definition.behavior.memory.reflection_memory_limit = Number($('reflectionMemoryLimit').value);
    definition.behavior.memory.recency_decay = Number($('recencyDecay').value);
    definition.behavior.memory.recency_weight = rangeValue('recencyOutput');
    definition.behavior.memory.relevance_weight = rangeValue('relevanceOutput');
    definition.behavior.memory.importance_weight = rangeValue('importanceOutput');
    definition.behavior.memory.default_expire_days = Number($('memoryExpireDays').value);
    definition.behavior.chat.max_iterations = rangeValue('chatOutput');
    definition.behavior.chat.cooldown_minutes = Number($('chatCooldown').value);
    definition.behavior.chat.stop_after_hour = Number($('chatStopHour').value.split(':')[0]);
    definition.behavior.chat.repeat_detection_enabled = $('repeatDetection').classList.contains('on');
    definition.behavior.schedule.max_try = Number($('scheduleRetries').value);
    definition.behavior.schedule.diversity = Number($('scheduleDiversity').value);
    definition.results.agent_step_projection_interval_steps = Number($('projectionInterval').value);
    definition.results.replay_interpolation_frames = Number($('replayFrames').value);
    definition.results.capture_model_payloads = $('capturePayloads').classList.contains('on');
    let worldDefinition;
    try { worldDefinition = JSON.parse($('worldDefinition').value || '{}'); }
    catch (_) { throw new Error('完整世界定义必须是有效 JSON'); }
    definition.world.world_name = $('worldName').value.trim();
    definition.world.world_key = $('worldKey').value.trim();
    definition.world.definition = worldDefinition;
    document.querySelectorAll('#agentRows .agent-row').forEach(row => {
      const agent = definition.agents.find(item => item.agent_key === row.dataset.agentKey);
      if (agent) agent.enabled = row.querySelector('.agent-check').checked;
    });
    if (state.currentPromptKey && definition.prompts[state.currentPromptKey]) {
      definition.prompts[state.currentPromptKey] = { content: $('promptEditor').value, sha256: null };
    }
    const saved = await api(`/experiments/${state.selectedExperimentId}/draft`, {
      method: 'PUT', body: JSON.stringify({ lock_version: state.draft.lock_version, data: definition }),
    });
    state.draft = saved;
    clearDirty();
    fillDraft(saved.definition);
    scheduleGlobalReconcile({ full: true });
    if (!silent) showToast('草稿已保存到当前实验，不影响其他实验。', '保存成功');
    return saved;
  }

  async function testModelConnection(purpose) {
    await saveDraft({ silent: true });
    const result = await api(`/experiments/${state.selectedExperimentId}/draft/models/${purpose}/test`, {
      method: 'POST', body: JSON.stringify({ lock_version: state.draft.lock_version }),
    });
    state.draft = await api(`/experiments/${state.selectedExperimentId}/draft`);
    fillDraft(state.draft.definition);
    if (purpose === 'chat') {
      const contextWindow = Number(result.service?.context_window || 0);
      $('chatServiceCapability').textContent = contextWindow
        ? `服务上下文窗口 ${contextWindow.toLocaleString('zh-CN')} tokens · 刚刚检测`
        : '模型可用 · 服务未返回上下文窗口';
    }
    scheduleGlobalReconcile({ full: true });
    showToast(`${result.resolved_model} · ${result.latency_ms} ms`, purpose === 'chat' ? '聊天模型可用' : 'Embedding 可用');
  }

  async function uploadWorldAssets(files) {
    if (!state.draft) throw new Error('已发布 Revision 只读，请先创建新修订');
    const world = structuredClone(state.draft.definition.world);
    for (const file of files) {
      const form = new FormData(); form.append('file', file, file.name);
      const response = await fetch('/api/v1/assets', { method: 'POST', body: form });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.error?.message || `资源上传失败（${response.status}）`);
      }
      const asset = await response.json();
      if (!world.assets.some(item => item.asset_hash === `sha256:${asset.sha256}`)) {
        world.assets.push({
          logical_path: `assets/${file.name}`,
          asset_hash: `sha256:${asset.sha256}`,
          media_type: asset.media_type,
          size: asset.size_bytes,
        });
      }
    }
    const saved = await api(`/experiments/${state.selectedExperimentId}/draft/world`, {
      method: 'PUT', body: JSON.stringify({ lock_version: state.draft.lock_version, data: world }),
    });
    state.draft = saved; state.definition = saved.definition;
    fillDraft(saved.definition); fillDefinitionOverview(saved.definition, saved); clearDirty();
    scheduleGlobalReconcile({ full: true });
    showToast(`${files.length} 个资源已按内容哈希保存并关联到当前 Draft。`, '资源已上传');
  }

  async function createExperiment() {
    const template = state.selectedTemplate;
    const sourceType = template === '从空白开始' ? 'BLANK' : template === '复制已有实验' ? 'REVISION' : 'BUILTIN_DEFAULT';
    const revisionId = sourceType === 'REVISION' ? $('copyRevisionSelect').value : null;
    if (sourceType === 'REVISION' && !revisionId) throw new Error('请选择一个已发布 Revision');
    const created = await api('/experiments', {
      method: 'POST',
      body: JSON.stringify({
        name: $('newExperimentName').value.trim(),
        goal: $('newExperimentGoal').value.trim(),
        source: { type: sourceType, ...(revisionId ? { revision_id: revisionId } : {}) },
      }),
    });
    closeModal('createModal', { restoreFocus: false });
    await loadExperiments();
    await openExperiment(created.id);
    showToast('独立实验草稿已创建。', '实验已创建');
  }

  async function loadCopyRevisionOptions() {
    const all = []; let page = 1; let totalPages = 1;
    do {
      const data = await api(`/experiments?page=${page}&page_size=50&sort=-updated_at`);
      all.push(...data.items); totalPages = data.total_pages || 1; page += 1;
    } while (page <= totalPages);
    const candidates = all.filter(item => item.published_revision_id);
    $('copyRevisionSelect').innerHTML = '<option value="">请选择已发布实验</option>' + candidates.map(item => `<option value="${item.published_revision_id}">${escapeHtml(item.name)} · revision ${String(item.revision_no || 1).padStart(3, '0')}</option>`).join('');
  }

  async function duplicateExperiment(experimentId) {
    const created = await api(`/experiments/${experimentId}/duplicate`, {
      method: 'POST', body: JSON.stringify({}),
    });
    await loadExperiments();
    await openExperiment(created.id);
    showToast('来源定义已深复制为新的独立实验草稿。', '实验已复制');
  }

  function openAgentEditor(agentKey = null) {
    if (!state.draft) throw new Error('已发布 Revision 只读，请先创建新修订');
    const existing = agentKey ? state.draft.definition.agents.find(item => item.agent_key === agentKey) : null;
    const used = new Set(state.draft.definition.agents.map(item => item.agent_key));
    let index = state.draft.definition.agents.length + 1;
    while (used.has(`resident-${String(index).padStart(3, '0')}`)) index += 1;
    const agent = existing || {
      agent_key: `resident-${String(index).padStart(3, '0')}`, enabled: true, name: '', portrait_asset: null,
      coord: [0, 0], currently: '', scratch: { age: 30, innate: '', learned: '', lifestyle: '', daily_plan: '' },
      spatial: { address: {}, tree: {} },
    };
    state.editingAgentKey = existing?.agent_key || null;
    $('agentEditorTitle').textContent = existing ? `编辑 ${agent.name}` : '新增 Agent';
    $('agentEditKey').value = agent.agent_key; $('agentEditKey').disabled = Boolean(existing);
    $('agentEditName').value = agent.name; $('agentEditAge').value = agent.scratch.age;
    $('agentEditPortrait').value = agent.portrait_asset || '';
    $('agentEditX').value = agent.coord[0]; $('agentEditY').value = agent.coord[1];
    $('agentEditCurrently').value = agent.currently || ''; $('agentEditInnate').value = agent.scratch.innate || '';
    $('agentEditLearned').value = agent.scratch.learned || ''; $('agentEditLifestyle').value = agent.scratch.lifestyle || '';
    $('agentEditDailyPlan').value = agent.scratch.daily_plan || '';
    $('agentEditSpatial').value = JSON.stringify(agent.spatial || { address: {}, tree: {} }, null, 2);
    $('deleteAgentBtn').hidden = !existing;
    setContentTab('agent-editor', 'identity', { sync: false });
    const agentEditorReturnFocus = document.activeElement;
    const agentEditorInitialFocus = existing ? $('agentEditName') : $('agentEditKey');
    openModal('agentEditorModal', agentEditorInitialFocus.id, agentEditorReturnFocus);
    requestAnimationFrame(() => agentEditorInitialFocus.focus());
  }

  async function saveAgentEditor() {
    if (!state.draft) throw new Error('当前没有可编辑 Draft');
    const key = $('agentEditKey').value.trim();
    let spatial;
    try { spatial = JSON.parse($('agentEditSpatial').value || '{}'); }
    catch (_) { throw new Error('空间定义必须是有效 JSON'); }
    const previous = state.editingAgentKey ? state.draft.definition.agents.find(item => item.agent_key === state.editingAgentKey) : null;
    const agent = {
      agent_key: key,
      enabled: previous?.enabled ?? true,
      name: $('agentEditName').value.trim(),
      portrait_asset: $('agentEditPortrait').value.trim() || null,
      coord: [Number($('agentEditX').value), Number($('agentEditY').value)],
      currently: $('agentEditCurrently').value,
      scratch: {
        age: Number($('agentEditAge').value), innate: $('agentEditInnate').value,
        learned: $('agentEditLearned').value, lifestyle: $('agentEditLifestyle').value,
        daily_plan: $('agentEditDailyPlan').value,
      },
      spatial,
    };
    const saved = await api(`/experiments/${state.selectedExperimentId}/draft/agents/${encodeURIComponent(key)}`, {
      method: 'PUT', body: JSON.stringify({ lock_version: state.draft.lock_version, data: agent }),
    });
    state.draft = saved; state.definition = saved.definition;
    fillDraft(saved.definition); fillDefinitionOverview(saved.definition, saved);
    state.modalReturnFocus = state.editingAgentKey
      ? document.querySelector(`#agentRows .agent-row[data-agent-key="${CSS.escape(key)}"] .agent-edit-btn`)
      : $('addAgentBtn');
    closeModal('agentEditorModal'); clearDirty();
    scheduleGlobalReconcile({ full: true });
    showToast('角色定义已保存到当前实验 Draft。', 'Agent 已保存');
  }

  async function deleteEditingAgent() {
    if (!state.draft || !state.editingAgentKey) return;
    const saved = await api(`/experiments/${state.selectedExperimentId}/draft/agents/${encodeURIComponent(state.editingAgentKey)}`, {
      method: 'DELETE', body: JSON.stringify({ lock_version: state.draft.lock_version, data: {} }),
    });
    state.draft = saved; state.definition = saved.definition;
    fillDraft(saved.definition); fillDefinitionOverview(saved.definition, saved);
    state.modalReturnFocus = $('addAgentBtn');
    closeModal('agentEditorModal'); clearDirty();
    scheduleGlobalReconcile({ full: true });
    showToast('角色已从当前实验 Draft 移除。', 'Agent 已删除');
  }

  async function publishAndRun() {
    if (!state.draft) throw new Error('当前实验没有可发布草稿');
    await saveDraft({ silent: true });
    const run = await api(`/experiments/${state.selectedExperimentId}/actions/publish-and-run`, {
      method: 'POST',
      body: JSON.stringify({
        draft_revision_id: state.draft.id,
        lock_version: state.draft.lock_version,
      }),
    });
    closeModal('publishModal', { restoreFocus: false });
    state.latestRunId = run.run_id;
    state.selectedRunId = run.run_id;
    await syncSelectedExperiment({ refreshDefinition: true, refreshOverview: true });
    await loadRunHistory(state.selectedExperimentId, run.run_id);
    goToPage('results');
    showToast('Revision 已固化，运行已进入本机调度队列。', '实验已启动');
  }

  async function createResultBundle() {
    if (!state.selectedRunId) throw new Error('请先选择一个运行');
    const runId = state.selectedRunId;
    const generation = state.resultGeneration;
    const job = await api(`/runs/${runId}/artifact-jobs`, {
      method: 'POST', body: JSON.stringify({ job_type: 'RESULT_BUNDLE', parameters: {} }),
    });
    showToast(`制品任务 ${job.job_id.slice(0, 8)} 已${job.status === 'SUCCEEDED' ? '完成' : '进入队列'}。`, '结果导出');
    if (runId === state.selectedRunId && generation === state.resultGeneration) {
      scheduleResultRefresh(runId, generation);
    }
  }

  async function createFilteredArtifact(jobType, parameters) {
    if (!state.selectedRunId) throw new Error('请先选择一个运行');
    const runId = state.selectedRunId;
    const generation = state.resultGeneration;
    const job = await api(`/runs/${runId}/artifact-jobs`, {
      method: 'POST', body: JSON.stringify({ job_type: jobType, parameters }),
    });
    showToast(`制品任务 ${job.job_id.slice(0, 8)} 已进入持久化队列，可在“运行与制品”查看。`, '筛选导出已创建');
    if (runId === state.selectedRunId && generation === state.resultGeneration) {
      scheduleResultRefresh(runId, generation);
    }
  }

  async function controlRun(action, options = {}) {
    if (!state.selectedRunId) throw new Error('请先选择一个运行');
    const runId = state.selectedRunId;
    const experimentId = state.selectedExperimentId;
    const generation = state.resultGeneration;
    const body = action === 'cancel' ? JSON.stringify({ force: Boolean(options.force) }) : undefined;
    const run = await api(`/runs/${runId}/${action}`, {
      method: 'POST', ...(body ? { body } : {}),
    });
    if (runId === state.selectedRunId
      && experimentId === state.selectedExperimentId
      && generation === state.resultGeneration) {
      state.currentRun = run;
      renderRunActions(run);
      await Promise.all([
        syncSelectedExperiment({ refreshOverview: true }),
        refreshRunHistoryList(state.selectedExperimentId, state.selectedRunId),
      ]);
      scheduleResultRefresh(runId, generation);
    } else {
      scheduleGlobalReconcile({ experimentId });
    }
    showToast(
      action === 'pause' ? '会在当前安全步骤完成后暂停。' : action === 'resume' ? '运行已重新进入本机队列。' : '取消请求已提交。',
      action === 'pause' ? '暂停请求已提交' : action === 'resume' ? '继续运行' : '取消运行',
    );
  }

  async function runPublishedRevision() {
    const revisionId = state.currentRun?.revision_id || state.experiment?.current_published?.id;
    if (!revisionId) throw new Error('没有可再次运行的已发布 Revision');
    const run = await api(`/experiments/${state.selectedExperimentId}/revisions/${revisionId}/runs`, { method: 'POST' });
    state.latestRunId = run.run_id;
    state.selectedRunId = run.run_id;
    await syncSelectedExperiment({ refreshOverview: true });
    await loadRunHistory(state.selectedExperimentId, run.run_id);
    goToPage('results');
    showToast('使用完全相同的只读 Revision 创建了新 Run。', '再次运行已排队');
  }

  async function forkCurrentRevision() {
    const revisionId = state.experiment?.current_published?.id;
    if (!revisionId) throw new Error('当前实验还没有可派生的已发布 Revision');
    await api(`/experiments/${state.selectedExperimentId}/revisions/${revisionId}/fork`, { method: 'POST' });
    await openExperiment(state.selectedExperimentId);
    showToast('已从已发布版本创建独立 Draft，原 Revision 与历史 Run 均未改变。', '新修订已创建');
  }

  function reportError(error) {
    console.error(error);
    showToast(error.message || String(error), '操作失败');
  }

  function openWorkspacePage(pageName) {
    if (pageName === 'experiments') {
      requestGlobalNavigation(pageName);
      return;
    }
    if (!state.selectedExperimentId) {
      showToast('请先从实验列表选择一个实验。', '尚未选择实验');
      return;
    }
    goToPage(pageName);
    if (pageName === 'results') {
      loadRunHistory(state.selectedExperimentId, state.selectedRunId || state.latestRunId).catch(reportError);
    }
  }

  document.querySelectorAll('.nav-item[data-page]').forEach(item => item.addEventListener('click', () => {
    openWorkspacePage(item.dataset.page);
  }));
  $('backToHub').addEventListener('click', () => requestGlobalNavigation('experiments'));
  document.querySelectorAll('[data-goto]').forEach(button => button.addEventListener('click', () => {
    openWorkspacePage(button.dataset.goto);
  }));
  document.querySelectorAll('[data-result-tab]').forEach(tab => tab.addEventListener('click', () => {
    setResultTab(tab.dataset.resultTab, { push: true });
  }));
  document.querySelector('.result-tabs').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const tabs = [...document.querySelectorAll('[data-result-tab]')];
    const index = Math.max(0, tabs.indexOf(event.target.closest('[data-result-tab]')));
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
      : event.key === 'ArrowLeft' ? Math.max(0, index - 1) : Math.min(tabs.length - 1, index + 1);
    event.preventDefault();
    tabs[nextIndex].focus();
    setResultTab(tabs[nextIndex].dataset.resultTab, { push: true });
  });
  document.querySelectorAll('[data-open-result-tab]').forEach(button => button.addEventListener('click', () => {
    setResultTab(button.dataset.openResultTab, { push: true });
  }));
  $('openReplayBtn').addEventListener('click', () => setResultTab('timeline', { push: true }));
  document.addEventListener('click', event => {
    const tab = event.target.closest('[data-content-tab]');
    if (!tab) return;
    const root = tab.closest('[data-content-tabs]');
    if (!root) return;
    setContentTab(root.dataset.contentTabs, tab.dataset.contentTab, { push: true });
  });
  document.addEventListener('keydown', event => {
    const tab = event.target.closest('[data-content-tab]');
    if (!tab || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const root = tab.closest('[data-content-tabs]');
    const tabs = [...root.querySelectorAll('[data-content-tab]')];
    const index = Math.max(0, tabs.indexOf(tab));
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
      : event.key === 'ArrowLeft' ? Math.max(0, index - 1) : Math.min(tabs.length - 1, index + 1);
    event.preventDefault();
    tabs[nextIndex].focus();
    setContentTab(root.dataset.contentTabs, tabs[nextIndex].dataset.contentTab, { push: true });
  });
  document.querySelectorAll('[data-toast]').forEach(button => button.addEventListener('click', () => {
    showToast(button.dataset.toast);
  }));

  document.querySelectorAll('.dirty-track').forEach(control => {
    control.addEventListener(control.tagName === 'SELECT' ? 'change' : 'input', markDirty);
  });
  document.querySelectorAll('.switch').forEach(button => button.addEventListener('click', () => {
    if (button.disabled || state.workspaceReadonly) return;
    button.classList.toggle('on');
    markDirty();
  }));
  document.querySelectorAll('input[type="range"][data-range-output]').forEach(input => {
    input.addEventListener('input', () => {
      $(input.dataset.rangeOutput).value = input.value;
      markDirty();
    });
  });

  $('createExperimentBtn').addEventListener('click', () => {
    state.wizardStep = 1;
    state.selectedTemplate = '标准小镇模板';
    $('newExperimentName').value = '';
    $('newExperimentGoal').value = '';
    $('newExperimentTag').value = '';
    $('copyRevisionField').hidden = true;
    document.querySelectorAll('.template-option').forEach(option => {
      option.classList.toggle('selected', option.dataset.template === state.selectedTemplate);
    });
    renderWizardStep();
    openModal('createModal', 'newExperimentName');
    $('newExperimentName').focus();
  });
  $('closeCreateModal').addEventListener('click', () => closeModal('createModal'));
  $('wizardBack').addEventListener('click', () => {
    state.wizardStep = Math.max(1, state.wizardStep - 1);
    renderWizardStep();
  });
  $('closeModal').addEventListener('click', () => closeModal('publishModal'));
  $('cancelModal').addEventListener('click', () => closeModal('publishModal'));
  [$('closeResumeRun'), $('cancelResumeRun')].forEach(button => button.addEventListener('click', () => closeModal('resumeRunModal')));
  [$('closeLeaveModal'), $('cancelLeave')].forEach(button => button.addEventListener('click', () => closeModal('leaveModal')));
  $('saveAndLeave').addEventListener('click', () => {
    saveDraft().then(() => {
      closeModal('leaveModal');
      goToPage(state.pendingGlobalPage);
    }).catch(reportError);
  });
  $('discardAndLeave').addEventListener('click', () => {
    clearDirty();
    closeModal('leaveModal');
    goToPage(state.pendingGlobalPage);
  });
  $('closeRunHistory').addEventListener('click', () => closeModal('runHistoryModal'));
  $('runHistorySearch').addEventListener('input', event => {
    const query = event.target.value.trim().toLowerCase();
    document.querySelectorAll('.run-history-item').forEach(item => {
      item.hidden = Boolean(query && !item.dataset.historySearch.includes(query));
    });
  });
  document.querySelectorAll('.modal-backdrop').forEach(backdrop => backdrop.addEventListener('click', event => {
    if (event.target === backdrop) closeModal(backdrop.id);
  }));
  $('agentEditorModal').addEventListener('keydown', event => {
    if (handleModalKeydown(event, $('agentEditorModal'))) event.stopPropagation();
  });
  document.addEventListener('keydown', event => {
    const activeModal = state.activeModalId ? $(state.activeModalId) : null;
    if (activeModal && handleModalKeydown(event, activeModal)) {
      $('experimentContextMenu').hidden = true;
      return;
    }
    if (event.key === 'Escape') $('experimentContextMenu').hidden = true;
  });

  $('experimentList').addEventListener('click', event => {
    const card = event.target.closest('.experiment-card');
    if (!card) return;
    if (event.target.closest('.api-open-results')) {
      event.stopImmediatePropagation();
      openExperiment(card.dataset.id, 'results').catch(reportError);
    } else if (event.target.closest('.api-open-experiment')) {
      event.stopImmediatePropagation();
      openExperiment(card.dataset.id).catch(reportError);
    }
  }, true);

  let contextExperimentId = null;
  $('experimentList').addEventListener('click', event => {
    const menu = event.target.closest('.experiment-menu');
    if (!menu) return;
    event.stopPropagation();
    contextExperimentId = menu.closest('.experiment-card')?.dataset.id || null;
    const contextMenu = $('experimentContextMenu');
    contextMenu.hidden = !contextExperimentId;
    contextMenu.style.left = `${Math.min(event.clientX, window.innerWidth - 190)}px`;
    contextMenu.style.top = `${Math.min(event.clientY, window.innerHeight - 100)}px`;
  });
  $('experimentContextMenu').addEventListener('click', event => {
    const action = event.target.dataset.contextAction;
    if (!action || !contextExperimentId) return;
    event.stopImmediatePropagation();
    $('experimentContextMenu').hidden = true;
    if (action === 'open') openExperiment(contextExperimentId).catch(reportError);
    else if (action === 'duplicate') duplicateExperiment(contextExperimentId).catch(reportError);
  }, true);
  document.addEventListener('click', event => {
    if (!event.target.closest('#experimentContextMenu, .experiment-menu')) $('experimentContextMenu').hidden = true;
  });

  document.querySelectorAll('.filter-tab').forEach(tab => tab.addEventListener('click', event => {
    event.stopImmediatePropagation();
    document.querySelectorAll('.filter-tab').forEach(item => item.classList.remove('active'));
    tab.classList.add('active');
    const filter = tab.dataset.filter;
    state.status = filter === 'all' ? '' : filter === 'abnormal' ? 'ABNORMAL' : filter.toUpperCase();
    state.page = 1;
    loadExperiments().catch(reportError);
  }, true));

  let searchTimer;
  $('experimentSearch').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.query = $('experimentSearch').value.trim();
      state.page = 1;
      loadExperiments().catch(reportError);
    }, 250);
  });

  $('experimentPagination').addEventListener('click', event => {
    event.stopImmediatePropagation();
    const page = event.target.closest('[data-api-page]');
    const total = Number($('experimentPagination').dataset.totalPages || 1);
    if (page) state.page = Number(page.dataset.apiPage);
    else if (event.target === $('experimentPrev')) state.page = Math.max(1, state.page - 1);
    else if (event.target === $('experimentNext')) state.page = Math.min(total, state.page + 1);
    else return;
    loadExperiments().catch(reportError);
  }, true);

  $('saveBtn').addEventListener('click', event => {
    if (!state.selectedExperimentId) return;
    event.stopImmediatePropagation();
    if (state.draft && !state.workspaceReadonly) saveDraft().catch(reportError);
    else if (state.experiment?.status === 'PAUSED') controlRun('resume').catch(reportError);
    else {
      goToPage('results');
      loadRunHistory(state.selectedExperimentId, state.selectedRunId || state.latestRunId).catch(reportError);
    }
  }, true);
  $('publishBtn').addEventListener('click', event => {
    if (state.draft && !state.workspaceReadonly) {
      event.stopImmediatePropagation();
      try { openPublishModal(); } catch (error) { reportError(error); }
      return;
    }
    if (!state.selectedExperimentId) return;
    event.stopImmediatePropagation();
    if (state.experiment?.status === 'QUEUED') controlRun('cancel').catch(reportError);
    else if (state.experiment?.status === 'PAUSED') controlRun('resume').catch(reportError);
    else {
      goToPage('results');
      loadRunHistory(state.selectedExperimentId, state.selectedRunId || state.latestRunId).catch(reportError);
    }
  }, true);
  $('cloneBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    if (state.draft) duplicateExperiment(state.selectedExperimentId).catch(reportError);
    else forkCurrentRevision().catch(reportError);
  }, true);
  $('agentRows').addEventListener('change', event => {
    if (!event.target.classList.contains('agent-check')) return;
    const rows = [...document.querySelectorAll('#agentRows .agent-check')];
    const enabled = rows.filter(input => input.checked).length;
    $('selectedAgentCount').textContent = `${enabled} / ${rows.length}`;
    $('statAgentCount').textContent = enabled;
    $('navAgentCount').textContent = enabled;
  }, true);
  $('worldAssetInput').addEventListener('change', event => {
    const files = [...event.target.files]; event.target.value = '';
    if (files.length) uploadWorldAssets(files).catch(reportError);
  });
  $('agentRows').addEventListener('click', event => {
    const button = event.target.closest('.agent-edit-btn');
    if (!button) return;
    event.stopImmediatePropagation();
    try { openAgentEditor(button.closest('.agent-row').dataset.agentKey); } catch (error) { reportError(error); }
  }, true);
  $('addAgentBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    try { openAgentEditor(); } catch (error) { reportError(error); }
  }, true);
  $('saveAgentEditor').addEventListener('click', event => { event.stopImmediatePropagation(); saveAgentEditor().catch(reportError); }, true);
  $('deleteAgentBtn').addEventListener('click', event => { event.stopImmediatePropagation(); deleteEditingAgent().catch(reportError); }, true);
  [$('closeAgentEditor'), $('cancelAgentEditor')].forEach(button => button.addEventListener('click', event => {
    event.stopImmediatePropagation(); closeModal('agentEditorModal');
  }, true));
  $('resultAgentButtons').addEventListener('click', event => {
    const tab = event.target.closest('.agent-result-tab');
    if (!tab) return;
    event.stopImmediatePropagation();
    showAgentDetail(tab.dataset.agentKey).catch(reportError);
  }, true);
  $('resultAgentButtons').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const tabs = [...document.querySelectorAll('.agent-result-tab')];
    if (!tabs.length) return;
    const currentIndex = Math.max(0, tabs.findIndex(tab => tab.dataset.agentKey === state.selectedAgentKey));
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
      : event.key === 'ArrowLeft' ? Math.max(0, currentIndex - 1) : Math.min(tabs.length - 1, currentIndex + 1);
    event.preventDefault();
    tabs[nextIndex].focus();
    showAgentDetail(tabs[nextIndex].dataset.agentKey).catch(reportError);
  });
  $('resultAgentDetail').addEventListener('click', event => {
    const contentFilter = event.target.closest('[data-agent-content]');
    if (!contentFilter) return;
    state.selectedAgentContent = contentFilter.dataset.agentContent;
    $('resultAgentDetail').querySelectorAll('[data-agent-content]').forEach(item => {
      const active = item === contentFilter;
      item.classList.toggle('active', active);
      item.setAttribute('aria-selected', String(active));
      item.tabIndex = active ? 0 : -1;
    });
    $('resultAgentDetail').querySelectorAll('[data-agent-content-section]').forEach(section => {
      section.hidden = section.dataset.agentContentSection !== state.selectedAgentContent;
    });
    syncWorkspaceUrl({ push: true });
  }, true);
  $('resultAgentDetail').addEventListener('keydown', event => {
    if (!event.target.closest('[data-agent-content]') || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const tabs = [...$('resultAgentDetail').querySelectorAll('[data-agent-content]')];
    const index = Math.max(0, tabs.indexOf(event.target.closest('[data-agent-content]')));
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
      : event.key === 'ArrowLeft' ? Math.max(0, index - 1) : Math.min(tabs.length - 1, index + 1);
    event.preventDefault();
    tabs[nextIndex].focus();
    tabs[nextIndex].click();
  });
  [$('agentTabPrev'), $('agentTabNext')].forEach(button => button.addEventListener('click', () => {
    const direction = button === $('agentTabPrev') ? -1 : 1;
    $('resultAgentButtons').scrollBy({ left: direction * Math.max(260, $('resultAgentButtons').clientWidth * .72), behavior: 'smooth' });
  }));
  $('conversationIndex').addEventListener('click', event => {
    const button = event.target.closest('.conversation-button');
    if (!button) return;
    event.stopImmediatePropagation();
    showConversation(button.dataset.conversationId).catch(reportError);
  }, true);
  $('timelineRange').addEventListener('input', event => {
    event.stopImmediatePropagation();
    if (state.replayPlayer) state.replayPlayer.seek(Number(event.target.value)).catch(reportError);
    else updateTimelineStep(Number(event.target.value));
  }, true);
  [$('timelinePrev'), $('timelineNext')].forEach(button => button.addEventListener('click', event => {
    event.stopImmediatePropagation();
    const delta = button === $('timelinePrev') ? -1 : 1;
    if (state.replayPlayer) state.replayPlayer.stepBy(delta).catch(reportError);
    else {
      const slider = $('timelineRange');
      slider.value = Math.max(Number(slider.min), Math.min(Number(slider.max), Number(slider.value) + delta));
      updateTimelineStep(Number(slider.value));
    }
  }, true));
  $('timelinePlay').addEventListener('click', event => {
    event.stopImmediatePropagation();
    if (!state.replayPlayer) return;
    if (state.replayPlaying) state.replayPlayer.pause();
    else state.replayPlayer.play();
  }, true);
  $('replaySpeed').addEventListener('change', event => state.replayPlayer?.setSpeed(Number(event.target.value)));
  $('replayAgentSelect').addEventListener('change', event => {
    state.selectedReplayAgentKey = event.target.value || null;
    state.selectedReplayRevisionId = state.selectedReplayAgentKey ? state.currentRun?.revision_id || null : null;
    state.replayPlayer?.selectAgent(state.selectedReplayAgentKey);
    if ($('replayCameraMode').value === 'follow') state.replayPlayer?.followAgent(event.target.value || null);
  });
  $('replayCameraMode').addEventListener('change', event => {
    state.replayPlayer?.followAgent(event.target.value === 'follow' ? $('replayAgentSelect').value || null : null);
  });
  [
    ['replayLayerAgentNames', 'agentNames'],
    ['replayLayerActionBubbles', 'actionBubbles'],
    ['replayLayerTrails', 'trails'],
    ['replayLayerConversations', 'conversations'],
    ['replayLayerKeyEvents', 'keyEvents'],
  ].forEach(([id, layer]) => $(id).addEventListener('change', event => {
    state.replayPlayer?.setLayerVisibility(layer, event.target.checked);
    if (state.replayPlayer?.currentStep) state.replayPlayer.seek(state.replayPlayer.currentStep).catch(reportError);
  }));
  $('replayTimelineMarkers').addEventListener('click', event => {
    const marker = event.target.closest('[data-replay-step]');
    if (marker) state.replayPlayer?.seek(Number(marker.dataset.replayStep)).catch(reportError);
  });
  $('resultAgentSearch').addEventListener('input', event => {
    renderAgentTabs();
    if (document.querySelector('.agent-result-tab.active') && state.selectedAgentKey
      && $('resultAgentDetail').dataset.agentKey !== state.selectedAgentKey) {
      showAgentDetail(state.selectedAgentKey).catch(reportError);
    }
  });
  document.querySelectorAll('[data-agent-status]').forEach(button => button.addEventListener('click', () => {
    state.agentStatusFilter = button.dataset.agentStatus;
    document.querySelectorAll('[data-agent-status]').forEach(item => item.classList.toggle('active', item === button));
    renderAgentTabs();
    if (document.querySelector('.agent-result-tab.active') && state.selectedAgentKey
      && $('resultAgentDetail').dataset.agentKey !== state.selectedAgentKey) {
      showAgentDetail(state.selectedAgentKey).catch(reportError);
    }
  }));
  let resultFilterTimer;
  async function reloadConversations() {
    const generation = ++state.conversationGeneration;
    const runId = state.selectedRunId;
    const params = new URLSearchParams({ limit: '50' });
    if ($('conversationSearch').value.trim()) params.set('q', $('conversationSearch').value.trim());
    if ($('conversationAgentFilter').value !== 'all') params.set('agent_key', $('conversationAgentFilter').value);
    const result = await api(`/runs/${runId}/results/conversations?${params}`);
    if (generation !== state.conversationGeneration || runId !== state.selectedRunId) return;
    renderConversations(result.items);
  }
  async function reloadMemories() {
    const generation = ++state.memoryGeneration;
    const runId = state.selectedRunId;
    const params = new URLSearchParams({ limit: '50' });
    if ($('memorySearch').value.trim()) params.set('q', $('memorySearch').value.trim());
    if ($('memoryAgentFilter').value !== 'all') params.set('agent_key', $('memoryAgentFilter').value);
    if ($('memoryTypeFilter').value !== 'all') params.set('memory_type', $('memoryTypeFilter').value);
    const result = await api(`/runs/${runId}/results/memories?${params}`);
    if (generation !== state.memoryGeneration || runId !== state.selectedRunId) return;
    renderMemories(result.items);
  }
  [$('conversationSearch'), $('conversationAgentFilter')].forEach(control => control.addEventListener(control.tagName === 'INPUT' ? 'input' : 'change', () => {
    clearTimeout(resultFilterTimer); resultFilterTimer = setTimeout(() => reloadConversations().catch(reportError), 250);
  }));
  [$('memorySearch'), $('memoryAgentFilter'), $('memoryTypeFilter')].forEach(control => control.addEventListener(control.tagName === 'INPUT' ? 'input' : 'change', () => {
    clearTimeout(resultFilterTimer); resultFilterTimer = setTimeout(() => reloadMemories().catch(reportError), 250);
  }));
  $('selectAllBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    const rows = [...document.querySelectorAll('#agentRows .agent-check')];
    const shouldEnable = rows.some(input => !input.checked);
    rows.forEach(input => { input.checked = shouldEnable; });
    $('selectedAgentCount').textContent = `${shouldEnable ? rows.length : 0} / ${rows.length}`;
    $('statAgentCount').textContent = shouldEnable ? rows.length : 0;
    $('navAgentCount').textContent = shouldEnable ? rows.length : 0;
    event.currentTarget.textContent = shouldEnable ? '取消全选' : '全部启用';
    markDirty();
  }, true);
  $('promptList').addEventListener('click', event => {
    const item = event.target.closest('.prompt-item');
    if (!item || !state.definition) return;
    event.stopImmediatePropagation();
    if (state.currentPromptKey && state.draft) {
      state.draft.definition.prompts[state.currentPromptKey].content = $('promptEditor').value;
    }
    showPrompt(item.dataset.prompt);
  }, true);
  $('promptEditor').addEventListener('input', () => {
    if (state.currentPromptKey && state.draft) {
      state.draft.definition.prompts[state.currentPromptKey].content = $('promptEditor').value;
    }
  });
  $('restorePromptBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    if (!state.draft || !state.currentPromptKey) return;
    api(`/experiments/${state.selectedExperimentId}/draft/prompts/${state.currentPromptKey}/restore-base`, {
      method: 'POST',
      body: JSON.stringify({ lock_version: state.draft.lock_version, data: {} }),
    }).then(saved => {
      state.draft = saved;
      fillDraft(saved.definition);
      clearDirty();
      scheduleGlobalReconcile({ full: true });
      showToast('已恢复为基线 Revision 中的正文。', 'Prompt 已恢复');
    }).catch(reportError);
  }, true);
  $('validatePromptBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    saveDraft({ silent: true }).then(() => api(`/experiments/${state.selectedExperimentId}/draft/validate`, { method: 'POST' })).then(report => {
      const issue = report.errors.find(item => item.path === `prompts.${state.currentPromptKey}`);
      if (issue) throw new Error(issue.message);
      showToast('正文哈希、模板语法和必需定义均已检查。', 'Prompt 有效');
    }).catch(reportError);
  }, true);
  document.querySelectorAll('.test-connection').forEach(button => button.addEventListener('click', event => {
    event.stopImmediatePropagation();
    const purpose = button.dataset.kind === '聊天模型' ? 'chat' : 'embedding';
    const original = button.textContent;
    button.disabled = true;
    button.textContent = '检测中…';
    testModelConnection(purpose).catch(reportError).finally(() => {
      button.disabled = false;
      button.textContent = original;
    });
  }, true));
  $('resultRunSelect').addEventListener('change', event => {
    if (event.target.value === '__all__') {
      openModal('runHistoryModal', 'runHistorySearch');
      event.target.value = state.selectedRunId;
    } else loadResults(event.target.value).catch(reportError);
  }, true);
  $('operationsSubtabs').addEventListener('click', event => {
    const tab = event.target.closest('[data-operation-tab]');
    if (tab) setOperationTab(tab.dataset.operationTab, { push: true });
  });
  $('operationsSubtabs').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const tabs = [...document.querySelectorAll('[data-operation-tab]')];
    const index = Math.max(0, tabs.indexOf(event.target.closest('[data-operation-tab]')));
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
      : event.key === 'ArrowLeft' ? Math.max(0, index - 1) : Math.min(tabs.length - 1, index + 1);
    event.preventDefault();
    tabs[nextIndex].focus();
    setOperationTab(tabs[nextIndex].dataset.operationTab, { push: true });
  });
  $('attemptLogSelect').addEventListener('change', event => {
    if (event.target.value && state.selectedRunId) {
      selectAttemptLog(state.selectedRunId, event.target.value).catch(reportError);
    }
  });
  $('attemptRows').addEventListener('click', event => {
    const row = event.target.closest('[data-attempt-id]');
    if (!row || !state.selectedRunId) return;
    $('attemptLogSelect').value = row.dataset.attemptId;
    selectAttemptLog(state.selectedRunId, row.dataset.attemptId).catch(reportError);
  });
  $('traceAttemptSelect').addEventListener('change', event => {
    if (!state.selectedRunId || !event.target.value || !state.operationsAbortController) return;
    state.selectedTraceAttemptId = event.target.value;
    loadModelTraces(state.selectedRunId, event.target.value, state.operationsAbortController.signal).catch(reportError);
  });
  $('refreshTraces').addEventListener('click', () => {
    if (!state.selectedRunId || !$('traceAttemptSelect').value || !state.operationsAbortController) return;
    loadModelTraces(state.selectedRunId, $('traceAttemptSelect').value, state.operationsAbortController.signal).catch(reportError);
  });
  $('loadMoreTraces').addEventListener('click', () => {
    if (!state.selectedRunId || !$('traceAttemptSelect').value || !state.operationsAbortController || state.traceCursor === null) return;
    loadModelTraces(state.selectedRunId, $('traceAttemptSelect').value, state.operationsAbortController.signal, { append: true }).catch(reportError);
  });
  $('modelTraceRows').addEventListener('click', event => {
    const row = event.target.closest('[data-trace-id]');
    if (!row || !state.selectedRunId) return;
    const runId = state.selectedRunId;
    state.traceDetailState = {
      runId,
      traceId: row.dataset.traceId,
      cursor: 0,
      fileId: null,
      content: '',
      generation: state.logGeneration,
    };
    loadTraceDetail().catch(reportError);
  });
  $('tracePayloadMore').addEventListener('click', () => loadTraceDetail({ append: true }).catch(reportError));
  $('tracePurposeFilter').addEventListener('keydown', event => {
    if (event.key === 'Enter') $('refreshTraces').click();
  });
  $('logSearch').addEventListener('input', renderLogViewport);
  $('logLevelFilter').addEventListener('change', renderLogViewport);
  $('logAutoFollow').addEventListener('change', renderLogViewport);
  $('logPauseScroll').addEventListener('click', () => {
    state.logStreamPaused = !state.logStreamPaused;
    $('logPauseScroll').textContent = state.logStreamPaused ? '继续流' : '暂停流';
    if (state.logStreamPaused) closeLogStream();
    else if (state.selectedRunId && state.selectedAttemptId) {
      startLogStream(state.selectedRunId, state.selectedAttemptId, state.logGeneration);
    }
  });
  $('eventSearch').addEventListener('input', () => renderSystemEvents(state.operationEvents));
  $('loadMoreEvents').addEventListener('click', () => {
    if (!state.selectedRunId || !state.operationsAbortController) return;
    loadSystemEvents(state.selectedRunId, state.operationsAbortController.signal, { append: true }).catch(reportError);
  });
  $('checkpointRows').addEventListener('click', event => {
    const row = event.target.closest('[data-checkpoint-step]');
    if (row && state.selectedRunId) showCheckpointDetail(state.selectedRunId, Number(row.dataset.checkpointStep)).catch(reportError);
  });
  $('checkpointDetail').addEventListener('click', event => {
    const preview = event.target.closest('[data-checkpoint-preview]');
    const exporter = event.target.closest('[data-checkpoint-export]');
    if (preview && state.selectedRunId) {
      const runId = state.selectedRunId;
      state.checkpointPreviewState = {
        runId,
        step: Number(preview.dataset.step),
        section: preview.dataset.checkpointPreview,
        cursor: 0,
        fileId: null,
        content: '',
        generation: state.checkpointGeneration,
      };
      loadCheckpointPreview().catch(reportError);
    }
    if (exporter && state.selectedRunId) {
      api(`/runs/${state.selectedRunId}/checkpoints/${exporter.dataset.checkpointExport}/artifact-job`, { method: 'POST' })
        .then(() => showToast('检查点 ZIP 已进入制品队列。', '任务已创建')).catch(reportError);
    }
  });
  $('checkpointPreviewMore').addEventListener('click', () => loadCheckpointPreview({ append: true }).catch(reportError));
  $('openRunHistory').addEventListener('click', event => {
    event.stopImmediatePropagation();
    renderRunHistory();
    openModal('runHistoryModal', 'runHistorySearch');
  }, true);
  $('runHistoryList').addEventListener('click', event => {
    const item = event.target.closest('.run-history-item');
    if (!item) return;
    event.stopImmediatePropagation();
    closeModal('runHistoryModal');
    $('resultRunSelect').value = item.dataset.historyRun;
    loadResults(item.dataset.historyRun).catch(reportError);
  }, true);
  $('loadMoreRuns').addEventListener('click', event => {
    event.stopImmediatePropagation();
    loadMoreRunHistory().catch(reportError);
  }, true);
  $('runPauseResumeBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    controlRun(state.currentRun?.status === 'PAUSED' ? 'resume' : 'pause').catch(reportError);
  }, true);
  $('runCancelBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    controlRun('cancel').catch(reportError);
  }, true);
  $('runContinueBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    try { openResumeRunModal(); } catch (error) { reportError(error); }
  }, true);
  $('runAgainBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    runPublishedRevision().catch(reportError);
  }, true);
  $('wizardNext').addEventListener('click', event => {
    event.stopImmediatePropagation();
    if (state.wizardStep === 1 && !$('newExperimentName').value.trim()) {
      showToast('请填写实验名称。', '无法继续');
      $('newExperimentName').focus();
      return;
    }
    if (state.wizardStep < 3) {
      state.wizardStep += 1;
      renderWizardStep();
      return;
    }
    createExperiment().catch(reportError);
  }, true);
  document.querySelectorAll('.template-option').forEach(button => button.addEventListener('click', event => {
    event.preventDefault();
    state.selectedTemplate = button.dataset.template;
    document.querySelectorAll('.template-option').forEach(option => option.classList.toggle('selected', option === button));
    const copying = button.dataset.template === '复制已有实验';
    $('copyRevisionField').hidden = !copying;
    if (copying) loadCopyRevisionOptions().catch(reportError);
    renderWizardStep();
  }));
  $('confirmPublish').addEventListener('click', event => {
    if (!state.draft) return;
    event.stopImmediatePropagation();
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = '正在自动解析模型并启动…';
    publishAndRun().catch(reportError).finally(() => {
      button.disabled = false;
      button.textContent = '确认发布并启动';
    });
  }, true);
  $('confirmResumeRun').addEventListener('click', event => {
    event.stopImmediatePropagation();
    const button = event.currentTarget;
    if (!state.pendingResumeRunId || state.pendingResumeRunId !== state.selectedRunId) {
      closeModal('resumeRunModal');
      reportError(new Error('当前选择的 Run 已变更，请重新确认'));
      return;
    }
    button.disabled = true;
    button.textContent = '正在恢复…';
    controlRun('resume').then(() => {
      closeModal('resumeRunModal');
      state.pendingResumeRunId = null;
      state.pendingResumeStep = 0;
    }).catch(reportError).finally(() => {
      button.disabled = false;
      button.textContent = '继续执行';
    });
  }, true);
  [$('exportBundleBtn'), $('exportResultsBtn')].forEach(button => button?.addEventListener('click', event => {
    event.stopImmediatePropagation();
    createResultBundle().catch(reportError);
  }, true));
  document.querySelector('[data-artifact="memories.ndjson"]').addEventListener('click', event => {
    event.stopImmediatePropagation();
    const parameters = {};
    if ($('memorySearch').value.trim()) parameters.q = $('memorySearch').value.trim();
    if ($('memoryAgentFilter').value !== 'all') parameters.agent_key = $('memoryAgentFilter').value;
    if ($('memoryTypeFilter').value !== 'all') parameters.memory_type = $('memoryTypeFilter').value.toUpperCase();
    createFilteredArtifact('FILTERED_MEMORIES', parameters).catch(reportError);
  }, true);
  $('exportConversationsFilter').addEventListener('click', event => {
    event.stopImmediatePropagation();
    const parameters = {};
    if ($('conversationSearch').value.trim()) parameters.q = $('conversationSearch').value.trim();
    if ($('conversationAgentFilter').value !== 'all') parameters.agent_key = $('conversationAgentFilter').value;
    createFilteredArtifact('FILTERED_CONVERSATIONS', parameters).catch(reportError);
  }, true);

  function reconcileAfterPageResume() {
    if (!state.bootstrapped) return;
    scheduleGlobalReconcile({ full: true });
    if (!state.activitySource || state.activitySource.readyState === EventSource.CLOSED) {
      startGlobalActivityStream().catch(error => console.warn('恢复全局状态流失败。', error));
    }
  }
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') reconcileAfterPageResume();
  });
  window.addEventListener('focus', reconcileAfterPageResume);
  window.addEventListener('online', reconcileAfterPageResume);
  window.addEventListener('pageshow', reconcileAfterPageResume);
  window.addEventListener('beforeunload', () => {
    state.eventSource?.close();
    state.activitySource?.close();
    closeLogStream();
    state.operationsAbortController?.abort();
    teardownReplay();
  });

  async function bootstrapConsole() {
    const params = new URLSearchParams(location.search);
    const experimentId = params.get('experiment_id');
    const requestedView = params.get('view');
    const targetPage = requestedView && requestedView !== 'experiments' && $(`page-${requestedView}`)
      ? requestedView
      : 'overview';
    const requestedTab = params.get('tab');
    const requestedResultTab = params.get('result_tab') || 'summary';

    // Apply deep-link state before loading the experiment. Result renderers use
    // these values while creating Agent panels, so a direct URL must never
    // become stuck on a different nested tab.
    if (experimentId && targetPage === 'results') {
      state.resultTab = requestedResultTab;
      if (requestedResultTab === 'agents' && requestedTab) state.selectedAgentContent = requestedTab;
      if (requestedResultTab === 'summary' && requestedTab) state.contentTabs.summary = requestedTab;
      if (requestedResultTab === 'operations' && requestedTab) state.operationTab = requestedTab;
    } else if (experimentId && requestedTab && Object.hasOwn(state.contentTabs, targetPage)) {
      state.contentTabs[targetPage] = requestedTab;
    }
    Object.entries(state.contentTabs).forEach(([groupName, tabName]) => {
      setContentTab(groupName, tabName, { sync: false });
    });
    setResultTab(state.resultTab, { sync: false });
    setOperationTab(state.operationTab, { sync: false });
    await loadExperiments();
    if (experimentId) {
      await openExperiment(experimentId, targetPage, params.get('run_id'));
      if (targetPage === 'results') {
        setResultTab(requestedResultTab, { sync: false });
        if (requestedResultTab === 'summary' && requestedTab) {
          setContentTab('summary', requestedTab, { sync: false });
        } else if (requestedResultTab === 'operations' && requestedTab) {
          setOperationTab(requestedTab, { sync: false });
        }
      } else if (requestedTab) {
        setContentTab(targetPage, requestedTab, { sync: false });
      }
      syncWorkspaceUrl();
    }
    state.bootstrapped = true;
    await startGlobalActivityStream();
  }
  window.addEventListener('popstate', () => window.location.reload());
  bootstrapConsole().catch(reportError);
})();
