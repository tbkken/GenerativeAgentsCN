/**
 * 实验控制台的浏览器端总协调器。
 *
 * 推荐阅读顺序：先看 state 理解页面保存什么，再看 api() 的错误协议；随后按功能阅读
 * loadExperiments()/openExperiment()、草稿编辑函数、loadResults() 和操作诊断函数。
 * generation 字段用于丢弃过期异步响应，避免快速切换实验或 Run 时旧数据覆盖新页面。
 */
(() => {
  'use strict';

  // 页面只有这一份可变状态；DOM 是 state 的投影，不作为服务器事实来源。
  const state = {
    page: 1,
    pageSize: 5,
    status: '',
    query: '',
    sort: '-updated_at',
    ownerFilter: '',
    tagFilter: '',
    modelFilter: '',
    mapFilter: '',
    archiveFilter: 'active',
    listView: 'cards',
    selectedExperimentIds: new Set(),
    visibleExperimentIds: [],
    selectedAgentKeys: new Set(),
    lastAgentBatchUndo: null,
    pendingAgentBatch: null,
    pendingAgentImport: null,
    pendingAgentDeleteKeys: [],
    agentImageFiles: { portrait: null, sprite: null },
    agentImageObjectUrls: { portrait: null, sprite: null },
    currentComparison: null,
    pendingExperimentOrganizeAction: null,
    modelStatus: null,
    validationReport: null,
    runEstimate: null,
    operationHistory: [],
    selectedExperimentId: null,
    selectedMapId: null,
    selectedBrainId: null,
    selectedCrowdId: null,
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
    formDirty: false,
    resultGeneration: 0,
    resultRequestGeneration: 0,
    resultRefreshTimer: null,
    resultDurationTimer: null,
    operationFactsGeneration: 0,
    operationsRunId: null,
    operationsAbortController: null,
    logSource: null,
    logGeneration: 0,
    logRunId: null,
    logAttemptId: null,
    checkpointGeneration: 0,
    checkpointItems: [],
    checkpointPage: 1,
    selectedAttemptId: null,
    selectedTraceAttemptId: null,
    logCursor: 0,
    logFileId: null,
    logRecords: [],
    logCarry: '',
    logDiscardUntilNewline: false,
    logStreamPaused: false,
    logTimeZoneMode: 'user',
    operationEvents: [],
    eventCursor: 0,
    eventPage: 1,
    traceCursor: null,
    traceEof: true,
    traceItems: [],
    tracePage: 1,
    tracePollTimer: null,
    tracePollBusy: false,
    tracePollTerminalRunId: null,
    modelUsageItems: [],
    modelUsagePage: 1,
    traceDetailState: null,
    checkpointPreviewState: null,
    timeline: null,
    timelineTimer: null,
    replayPlayer: null,
    replayAbortController: null,
    replayRunId: null,
    replayPlaying: false,
    replayReady: false,
    replayMarkerFacts: new Map(),
    replayAgentDefinitions: [],
    selectedReplayAgentKey: null,
    selectedReplayRevisionId: null,
    selectedAgentKey: null,
    agentResults: [],
    agentStatusFilter: 'all',
    selectedAgentContent: 'plan',
    agentDetailGeneration: 0,
    agentDetailSignatures: new Map(),
    agentDetailCache: new Map(),
    agentContentPages: new Map(),
    renderedAgentDetailKey: null,
    resultTab: 'timeline',
    operationTab: 'logs',
    contentTabs: {
      models: 'chat',
      world: 'map',
      advanced: 'perception',
      'agent-editor': 'identity',
    },
    selectedConversationId: null,
    editingAgentKey: null,
    agentEditorContext: { ownerType: 'experiment' },
    currentExperimentName: '',
    currentExperimentStatus: '草稿',
    workspaceReadonly: false,
    dirty: false,
    pendingGlobalPage: 'experiments',
    toastTimer: null,
    wizardStep: 1,
    activeModalId: null,
    modalReturnFocus: null,
    pendingResumeRunId: null,
    pendingResumeStep: 0,
    pendingResourceDelete: null,
    workspacePage: 'experiments',
    remoteConflictKey: null,
    draftMutation: Promise.resolve(),
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

  const operationHistoryKey = 'agent-foundry.operation-history';

  function persistOperationHistory() {
    try { localStorage.setItem(operationHistoryKey, JSON.stringify(state.operationHistory.slice(0, 50))); } catch (_error) {}
  }

  function recordOperation(title, message, level = 'success', diagnostic = null) {
    state.operationHistory.unshift({
      id: crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`,
      timestamp: new Date().toISOString(),
      page: state.workspacePage || 'experiments',
      title,
      message,
      level,
      diagnostic,
    });
    state.operationHistory = state.operationHistory.slice(0, 50);
    persistOperationHistory();
  }

  function restoreOperationHistory() {
    try {
      const saved = JSON.parse(localStorage.getItem(operationHistoryKey) || '[]');
      state.operationHistory = Array.isArray(saved) ? saved.slice(0, 50) : [];
    } catch (_error) { state.operationHistory = []; }
  }

  function showToast(message, title = '操作成功', { level = null, diagnostic = null, record = true } = {}) {
    clearTimeout(state.toastTimer);
    $('toastTitle').textContent = title;
    $('toastText').textContent = message;
    $('toast').classList.add('show');
    state.toastTimer = setTimeout(() => $('toast').classList.remove('show'), 2600);
    const inferredLevel = level || (/失败|错误|异常/.test(title) ? 'error' : /警告|注意/.test(title) ? 'warning' : 'success');
    if (record) recordOperation(title, message, inferredLevel, diagnostic);
  }

  function workspaceUrl(pageName = state.workspacePage) {
    const url = new URL(window.location.href);
    url.search = '';
    url.hash = '';
    if (['maps', 'brains', 'crowds', 'skills'].includes(pageName)) {
      url.searchParams.set('view', pageName);
      if (pageName === 'maps' && state.selectedMapId) url.searchParams.set('map_id', state.selectedMapId);
      if (pageName === 'brains' && state.selectedBrainId) url.searchParams.set('brain_id', state.selectedBrainId);
      if (pageName === 'crowds' && state.selectedCrowdId) url.searchParams.set('crowd_id', state.selectedCrowdId);
    } else if (pageName !== 'experiments' && state.selectedExperimentId) {
      url.searchParams.set('experiment_id', state.selectedExperimentId);
      url.searchParams.set('view', pageName);
      if (pageName === 'results' && state.selectedRunId) {
        url.searchParams.set('run_id', state.selectedRunId);
      }
      if (pageName === 'results') {
        url.searchParams.set('result_tab', state.resultTab);
        const resultContentTab = state.resultTab === 'agents'
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

  const sidebarPreferenceKey = 'agent-foundry.sidebar-collapsed';

  function setSidebarCollapsed(collapsed, { persist = true } = {}) {
    document.body.classList.toggle('sidebar-collapsed', collapsed);
    const toggle = $('sidebarToggle');
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.setAttribute('aria-label', collapsed ? '展开导航' : '收起导航');
    toggle.querySelector('span').textContent = collapsed ? '›' : '‹';
    if (persist) {
      try {
        localStorage.setItem(sidebarPreferenceKey, collapsed ? '1' : '0');
      } catch (_error) {
        // The navigation remains usable when storage is unavailable.
      }
    }
  }

  function restoreSidebarPreference() {
    let collapsed = false;
    try {
      collapsed = localStorage.getItem(sidebarPreferenceKey) === '1';
    } catch (_error) {
      collapsed = false;
    }
    setSidebarCollapsed(collapsed, { persist: false });
  }

  function syncMapEditorTopbar() {
    const active = state.workspacePage === 'maps' && Boolean(state.selectedMapId);
    const crowdActive = state.workspacePage === 'crowds' && Boolean(state.selectedCrowdId);
    document.body.classList.toggle('map-editor-mode', active);
    document.body.classList.toggle('crowd-editor-mode', crowdActive);
    $('defaultTopbarContext').hidden = active || crowdActive;
    $('mapEditorTopbarContext').hidden = !active;
    $('mapEditorActions').hidden = !active;
    $('crowdEditorTopbarContext').hidden = !crowdActive;
    $('crowdEditorTopbarActions').hidden = !crowdActive;
    if (state.workspacePage === 'maps') $('hubActions').hidden = active;
    if (state.workspacePage === 'crowds') $('hubActions').hidden = crowdActive;
  }

  function goToPage(pageName) {
    const target = $(`page-${pageName}`);
    if (!target) throw new Error(`未知页面：${pageName}`);
    const isGlobal = ['experiments', 'maps', 'brains', 'crowds', 'skills'].includes(pageName);
    window.SkillWorkspace?.deactivateTopbar?.();
    state.workspacePage = pageName;
    document.querySelectorAll('.nav-item[data-page]').forEach(item => {
      item.classList.toggle('active', item.dataset.page === pageName);
    });
    document.querySelectorAll('.page').forEach(page => {
      page.classList.toggle('active', page === target);
    });
    document.body.classList.toggle('hub-mode', isGlobal);
    document.body.classList.toggle('brain-mode', pageName === 'brains');
    if (pageName !== 'brains') document.body.classList.remove('brain-editor-mode');
    $('topbarTitle').textContent = pageName === 'maps' ? '地图中心' : pageName === 'brains' ? '大脑中心' : pageName === 'crowds' ? '人群中心' : pageName === 'skills' ? '技能中心' : isGlobal ? '实验中心' : state.currentExperimentName || '当前实验';
    $('statusPill').hidden = isGlobal;
    $('experimentHeaderMeta').hidden = isGlobal;
    $('backToHub').classList.toggle('visible', !isGlobal);
    $('hubActions').hidden = !isGlobal;
    $('commuteDemoBtn').hidden = pageName !== 'experiments';
    $('mapConfigurationDemoBtn').hidden = pageName !== 'maps';
    $('createExperimentBtn').hidden = pageName !== 'experiments';
    $('createMapBtn').hidden = pageName !== 'maps';
    $('createBrainBtn').hidden = pageName !== 'brains';
    $('createCrowdBtn').hidden = pageName !== 'crowds';
    $('createSkillBtn').hidden = pageName !== 'skills';
    $('experimentActions').hidden = isGlobal;
    syncMapEditorTopbar();
    $('resultRunSelect').hidden = pageName !== 'results';
    $('resultHeaderActions').hidden = pageName !== 'results';
    if (pageName === 'results' && state.currentRun) renderRunActions(state.currentRun);
    else $('resultRunControls').hidden = true;
    if (pageName !== 'results' && state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
      state.resultGeneration += 1;
      if (state.resultRefreshTimer) clearTimeout(state.resultRefreshTimer);
      state.resultRefreshTimer = null;
    }
    if (pageName !== 'results') {
      clearResultDurationTimer();
      closeLogStream();
      state.operationsAbortController?.abort();
      state.operationsAbortController = null;
      state.operationsRunId = null;
    }
    if (pageName === 'experiments') scheduleGlobalReconcile({ full: true });
    if (pageName === 'maps') window.MapWorkspace?.activate().catch(reportError);
    if (pageName === 'brains') window.SkillWorkspace?.activate('brains').catch(reportError);
    if (pageName === 'crowds') window.CrowdWorkspace?.activate().catch(reportError);
    if (pageName === 'skills') window.SkillWorkspace?.activate('skills').catch(reportError);
    syncWorkspaceUrl();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function renderDirtyState() {
    state.dirty = Boolean(state.formDirty);
    $('unsaved').hidden = !state.dirty;
    if (state.dirty) $('unsaved').querySelector('span').textContent = '有未保存更改';
  }

  function markDirty() {
    if (state.workspaceReadonly || !state.draft) return;
    state.formDirty = true;
    renderDirtyState();
  }

  function clearDirty() {
    state.formDirty = false;
    renderDirtyState();
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

  function executionLocksRevision(experiment = state.experiment) {
    return ['QUEUED', 'RUNNING', 'PAUSED'].includes(experiment?.status);
  }

  function setWorkspaceMode(status) {
    state.workspaceReadonly = executionLocksRevision() || !state.draft;
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
    ['experimentBrainRevisionSelect', 'experimentMapRevisionSelect', 'saveExperimentComposition'].forEach(id => {
      if ($(id)) $(id).disabled = state.workspaceReadonly;
    });
    $('selectAllBtn').disabled = state.workspaceReadonly;
    $('cloneBtn').textContent = state.workspaceReadonly ? '创建新修订' : '复制实验';
    const terminal = ['已完成', '失败', '已取消', '已中断'].includes(status);
    $('saveBtn').textContent = status === '运行中' ? '查看运行' : status === '排队中' ? '查看排队' : status === '已暂停' ? '恢复运行' : terminal ? '查看结果' : '保存草稿';
    $('publishBtn').textContent = status === '运行中' ? '查看当前运行' : status === '排队中' ? '取消排队' : status === '已暂停' ? '恢复此运行' : terminal ? '查看实验结果' : '发布版本并启动实验';
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
    const ownsUrl = groupName === state.workspacePage;
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
    const brainOption = $('newExperimentBrain')?.selectedOptions?.[0];
    const mapOption = $('newExperimentMap')?.selectedOptions?.[0];
    if ($('createSummaryBrain')) $('createSummaryBrain').textContent = $('newExperimentBrain')?.value ? brainOption?.textContent || $('newExperimentBrain').value : '未选择';
    if ($('createSummaryMap')) $('createSummaryMap').textContent = mapOption?.textContent?.replace(/ · v\d+(?: · 默认)?$/, '') || '未选择';
    const crowdSummary = window.CrowdWorkspace?.getCreationSummary?.() || { names: [], crowdCount: 0, agentCount: 0, duplicateCount: 0 };
    if ($('createSummaryCrowds')) $('createSummaryCrowds').textContent = crowdSummary.names.length ? crowdSummary.names.join('、') : '未选择';
    if ($('createSummaryAgents')) $('createSummaryAgents').textContent = crowdSummary.crowdCount
      ? `${crowdSummary.agentCount} 个 Agent${crowdSummary.duplicateCount ? ` · 已去重 ${crowdSummary.duplicateCount} 个同名项` : ' · 无同名重复'}`
      : '请选择至少一个人群';
  }

  async function prepareExperimentBrainChoices() {
    const selector = $('newExperimentBrain');
    selector.disabled = true;
    selector.replaceChildren(new Option('正在加载 Brain Skill…', ''));
    const response = await api('/skills?kind=brain');
    selector.replaceChildren(new Option('请选择已发布的 Brain Skill', ''));
    (response.items || []).forEach(item => {
      const option = new Option(
        `${item.name}${item.description ? ` · ${item.description}` : ''}`,
        item.revision_id,
      );
      option.dataset.skillName = item.name;
      option.dataset.revisionHash = item.revision;
      selector.appendChild(option);
    });
    selector.disabled = !(response.items || []).length;
    if (selector.disabled) selector.options[0].textContent = '暂无可用 Brain Skill';
    const composition = $('experimentBrainRevisionSelect');
    if (composition) {
      const selectedRevision = state.definition?.engine?.brain_revision_id || '';
      composition.replaceChildren(new Option('请选择 Brain Skill Revision', ''));
      (response.items || []).forEach(item => {
        const option = new Option(`${item.name} · r${item.revision_no}`, item.revision_id);
        option.dataset.skillName = item.name;
        option.dataset.revisionHash = item.revision;
        composition.appendChild(option);
      });
      composition.value = selectedRevision;
      composition.disabled = state.workspaceReadonly || !state.draft;
      $('experimentBrainRevisionMeta').textContent = selectedRevision
        ? `${state.definition.engine.brain_skill} · ${String(state.definition.engine.brain_revision_hash || '').slice(0, 12)}…`
        : '必须选择不可变 Brain Revision';
    }
    renderWizardStep();
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
    if (id === 'agentEditorModal') releaseAgentImageObjectUrls();
    modal.classList.remove('open');
    if (id === 'resourceDeleteModal' && state.pendingResourceDelete) {
      const pending = state.pendingResourceDelete;
      state.pendingResourceDelete = null;
      pending.resolve(false);
    }
    if (state.activeModalId !== id) return;
    state.activeModalId = null;
    const returnFocus = state.modalReturnFocus;
    state.modalReturnFocus = null;
    if (!document.querySelector('.modal-backdrop.open')) setBackgroundInert(false);
    if (restoreFocus && returnFocus?.isConnected) {
      requestAnimationFrame(() => returnFocus.focus({ preventScroll: true }));
    }
  }

  function confirmResourceDeletion({ type = '资源', name = '当前资源', message = '' } = {}) {
    if (state.pendingResourceDelete) {
      state.pendingResourceDelete.resolve(false);
      state.pendingResourceDelete = null;
    }
    $('resourceDeleteTitle').textContent = `删除${type}`;
    $('resourceDeleteName').textContent = name;
    $('resourceDeleteMessage').textContent = message || '删除后将从数据库移除；被不可变 Revision 引用时，系统会拒绝操作。';
    return new Promise(resolve => {
      state.pendingResourceDelete = { resolve };
      openModal('resourceDeleteModal', 'cancelResourceDelete');
    });
  }

  function settleResourceDeletion(confirmed) {
    const pending = state.pendingResourceDelete;
    if (!pending) return;
    state.pendingResourceDelete = null;
    closeModal('resourceDeleteModal');
    pending.resolve(Boolean(confirmed));
  }

  window.confirmResourceDeletion = confirmResourceDeletion;

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

  function validationItemMarkup(issue, icon = '×') {
    return `<div class="validation-item"><span>${icon}</span><div><strong>${escapeHtml(issue.message)}</strong><small>${escapeHtml(issue.code || issue.path || '')}</small></div>${issue.fix_page ? `<button class="btn btn-sm" data-fix-page="${escapeHtml(issue.fix_page)}" data-fix-control="${escapeHtml(issue.fix_control || '')}">去修复</button>` : ''}</div>`;
  }

  function modelAutoProbeMarkup(report) {
    const probe = report.auto_model_probe;
    if (!probe?.enabled) return '';
    const purposes = (probe.purposes || []).map(item => item === 'chat' ? 'Chat' : item === 'embedding' ? 'Embedding' : item);
    const summary = `<div class="validation-item model-auto-probe"><span>↻</span><div><strong>模型服务由系统自动检测</strong><small>确认发布后会一次性检测 ${escapeHtml(purposes.join(' + '))}，并将 auto 固化为实际模型。无需逐项操作。</small></div></div>`;
    return summary;
  }

  function renderOverviewValidation(report) {
    state.validationReport = report;
    const counts = report.counts || { blocking: report.errors?.length || 0, warning: report.warnings?.length || 0, automatic: 0, passed: 0 };
    $('overviewValidationCount').textContent = `${counts.blocking} 阻断 · ${counts.warning} 警告 · ${counts.automatic || 0} 自动检查 · ${counts.passed} 通过`;
    $('overviewValidationCount').className = `chip ${counts.blocking ? 'amber' : counts.warning ? 'blue' : 'teal'}`;
    const checklist = $('overviewValidationCount').closest('.panel-section').querySelector('.checklist');
    checklist.innerHTML = [
      modelAutoProbeMarkup(report),
      ...(report.errors || []).map(issue => validationItemMarkup(issue, '×')),
      ...(report.warnings || []).map(issue => validationItemMarkup(issue, '!')),
      `<div class="validation-item"><span>✓</span><div><strong>${counts.passed} 项检查已通过</strong><small>Schema、版本和可物化配置按当前草稿实时计算</small></div></div>`,
    ].join('');
    $('publishBtn').disabled = false;
    $('publishBtn').title = report.valid ? '' : '打开发布确认，查看并处理阻断项';
  }

  async function refreshValidation() {
    if (!state.selectedExperimentId || !state.draft) return null;
    const experimentId = state.selectedExperimentId;
    const definitionHash = state.draft.definition_hash;
    const report = await api(`/experiments/${experimentId}/draft/validate`, { method: 'POST' });
    if (experimentId !== state.selectedExperimentId
      || definitionHash !== state.draft?.definition_hash
      || report.definition_hash !== state.draft?.definition_hash) return null;
    renderOverviewValidation(report);
    return report;
  }

  function formatRange(range, formatter = value => Number(value).toLocaleString('zh-CN')) {
    if (range.low === range.high) return formatter(range.low);
    return `${formatter(range.low)}–${formatter(range.high)}`;
  }

  function formatDurationMs(value) {
    const milliseconds = Number(value || 0);
    if (milliseconds < 60_000) return `${milliseconds / 1000} 秒`;
    if (milliseconds < 3_600_000) return `${milliseconds / 60_000} 分钟`;
    return `${milliseconds / 3_600_000} 小时`;
  }

  function formatSeconds(value) {
    if (value < 60) return `${value} 秒`;
    if (value < 3600) return `${Math.ceil(value / 60)} 分钟`;
    return `${(value / 3600).toFixed(1)} 小时`;
  }

  function formatBytes(value) {
    if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
    if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
    return `${(value / 1024 ** 3).toFixed(1)} GB`;
  }

  function renderPublishValidation(report, estimate) {
    const counts = report.counts;
    const box = $('publishValidationSummary');
    box.className = `publish-validation-summary${counts.blocking ? ' has-errors' : counts.warning ? ' has-warnings' : ''}`;
    box.innerHTML = `<strong>${counts.blocking} 个阻断项 · ${counts.warning} 个警告 · ${counts.automatic || 0} 项自动检查 · ${counts.passed} 项通过</strong>${[
      modelAutoProbeMarkup(report),
      ...(report.errors || []).map(issue => validationItemMarkup(issue, '×')),
      ...(report.warnings || []).map(issue => validationItemMarkup(issue, '!')),
    ].join('')}`;
    if (estimate.high_scale && report.valid) {
      box.innerHTML += `<label class="map-check-row"><input type="checkbox" id="confirmHighScale" /> 我已了解高规模风险：${escapeHtml(estimate.threshold_reasons.join('；'))}</label>`;
      document.getElementById('confirmHighScale').addEventListener('change', event => { $('confirmPublish').disabled = !event.target.checked; });
    }
    $('confirmPublish').disabled = !report.valid || estimate.high_scale;
  }

  async function openPublishModal() {
    if (!state.draft || !state.definition) throw new Error('当前实验没有可发布的 Draft');
    await saveDraft({ silent: true });
    $('confirmPublish').disabled = true;
    $('modalRevision').textContent = `revision ${String(state.draft.revision_no || 1).padStart(3, '0')}`;
    $('modalAgentCount').textContent = state.definition.agents.filter(agent => agent.enabled).length;
    $('modalModels').textContent = `${state.definition.models.chat.resolved_model || state.definition.models.chat.model} / ${state.definition.models.embedding.resolved_model || state.definition.models.embedding.model}`;
    $('modalWorld').textContent = state.definition.world.world_name || '世界待配置';
    $('modalHash').textContent = '将在发布事务中生成并锁定';
    openModal('publishModal', 'confirmPublish');
    const [report, estimate] = await Promise.all([
      refreshValidation(),
      api(`/experiments/${state.selectedExperimentId}/run-estimate`),
    ]);
    state.runEstimate = estimate;
    const scale = estimate.scale;
    if (scale.execution_mode === 'SKILL_BRAIN') {
      $('modalAgentCount').textContent = scale.agents;
      $('modalModels').textContent = `${state.definition.models.chat.resolved_model || state.definition.models.chat.model} / ${scale.brain_skill}`;
      $('modalScale').textContent = `${scale.agents} Agent x ${scale.steps} steps / SKILL Brain`;
    }
    $('modalCalls').textContent = formatRange(estimate.estimate.model_calls);
    $('modalTokens').textContent = formatRange(estimate.estimate.tokens);
    $('modalWallTime').textContent = formatRange(estimate.estimate.wall_seconds, formatSeconds);
    $('modalStorage').textContent = formatRange(estimate.estimate.storage_bytes, formatBytes);
    $('publishEstimateNote').textContent = estimate.basis;
    renderPublishValidation(report, estimate);
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
    // 所有请求都通过统一错误信封，调用者无需分别解析 FastAPI/业务错误格式。
    const { transportRetries = 0, ...fetchOptions } = options;
    let response;
    for (let attempt = 0; ; attempt += 1) {
      try {
        response = await fetch(`/api/v1${path}`, {
          headers: { 'Content-Type': 'application/json', ...(fetchOptions.headers || {}) },
          cache: 'no-store',
          ...fetchOptions,
        });
        break;
      } catch (cause) {
        if (fetchOptions.signal?.aborted) throw cause;
        if (!(cause instanceof TypeError) || attempt >= transportRetries) {
          const error = new Error('浏览器未能把请求送到系统服务，请检查本机服务或代理后重试');
          error.code = 'CONTROL_PLANE_NETWORK_ERROR';
          error.details = { transport_attempts: attempt + 1 };
          error.path = path;
          error.cause = cause;
          throw error;
        }
        await new Promise(resolve => setTimeout(resolve, 200 * (attempt + 1)));
      }
    }
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const document = payload.error || {};
      const error = new Error(document.message || `请求失败（${response.status}）`);
      error.code = document.code || 'HTTP_ERROR';
      error.details = document.details || {};
      error.requestId = document.request_id || response.headers.get('X-Request-ID');
      error.status = response.status;
      error.path = path;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }

  function formatTime(value) {
    if (!value) return '—';
    return new Intl.DateTimeFormat('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date(value));
  }

  const userTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';

  function formatSystemTime(value) {
    if (!value) return '—';
    const instant = parseApiInstant(value);
    if (!Number.isFinite(instant)) return String(value);
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: userTimeZone,
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
      hour12: false,
    }).format(new Date(instant));
  }

  function systemTimeMarkup(value, suffix = '') {
    if (!value) return '—';
    const instant = parseApiInstant(value);
    if (!Number.isFinite(instant)) return escapeHtml(String(value));
    const iso = new Date(instant).toISOString();
    return `<time datetime="${escapeHtml(iso)}" title="${escapeHtml(`${iso} · 显示时区 ${userTimeZone}`)}">${escapeHtml(formatSystemTime(value))} ${escapeHtml(userTimeZone)}${escapeHtml(suffix)}</time>`;
  }

  function formatLogTime(value) {
    if (!value) return '';
    const date = new Date(parseApiInstant(value));
    if (Number.isNaN(date.getTime())) return String(value);
    const timeZone = state.logTimeZoneMode === 'UTC' ? 'UTC' : userTimeZone;
    const display = new Intl.DateTimeFormat('zh-CN', {
      timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }).format(date);
    return `${display} ${timeZone}`;
  }

  function parseApiInstant(value) {
    if (!value) return Number.NaN;
    const text = String(value);
    // SQLite drops timezone metadata even though persisted system timestamps
    // are UTC. Treat only timezone-less Run instants as UTC at this boundary.
    const zoned = /(?:Z|[+-]\d{2}:\d{2})$/i.test(text);
    return new Date(zoned ? text : `${text}Z`).getTime();
  }

  function formatDuration(startedAt, finishedAt) {
    if (!startedAt) return '—';
    const start = parseApiInstant(startedAt);
    const end = finishedAt ? parseApiInstant(finishedAt) : Date.now();
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return '—';
    const seconds = Math.floor((end - start) / 1000);
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ${minutes % 60}m`;
  }

  function clearResultDurationTimer() {
    if (state.resultDurationTimer) clearInterval(state.resultDurationTimer);
    state.resultDurationTimer = null;
    $('resultDurationMeta').hidden = true;
  }

  function renderRunDuration(run) {
    $('resultDurationMeta').hidden = state.workspacePage !== 'results' || !run;
    $('resultDurationMetric').textContent = formatDuration(run?.started_at, run?.finished_at);
  }

  function startResultDurationTimer(run) {
    clearResultDurationTimer();
    renderRunDuration(run);
    if (!run?.started_at || run.finished_at
      || ['COMPLETED', 'FAILED', 'CANCELLED', 'INTERRUPTED'].includes(run.status)) return;
    const runId = run.run_id;
    state.resultDurationTimer = setInterval(() => {
      if (state.workspacePage !== 'results' || state.selectedRunId !== runId) {
        clearResultDurationTimer();
        return;
      }
      renderRunDuration(state.currentRun);
    }, 1000);
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
    const runValue = run ? `${completed} / ${requested} steps` : item.status === 'DRAFT' ? '待发布' : '尚未运行';
    const runDetail = run ? statusLabels[run.status] || run.status : '保存于独立实验草稿';
    const selected = state.selectedExperimentIds?.has(item.id) || false;
    const metadataTags = [item.owner ? `负责人：${item.owner}` : '未指定负责人', ...(item.tags || [])];
    const updatedAtMarkup = typeof systemTimeMarkup === 'function'
      ? systemTimeMarkup(item.updated_at, ' 更新')
      : `${escapeHtml(formatTime(item.updated_at))} 更新`;
    return `
      <article class="experiment-card${selected ? ' is-selected' : ''}${item.archived_at ? ' archived' : ''}" data-id="${escapeHtml(item.id)}" data-archived="${item.archived_at ? 'true' : 'false'}" data-status="${statusClasses[item.status] || 'draft'}" data-search="${escapeHtml(`${item.name} ${item.goal} ${item.owner || ''} ${(item.tags || []).join(' ')} ${core.chat_model || ''} ${core.embedding_model || ''}`.toLowerCase())}">
        <label class="experiment-select-wrap"><input type="checkbox" class="experiment-select" ${selected ? 'checked' : ''} aria-label="选择 ${escapeHtml(item.name)}" /><span>选择</span></label>
        <div class="experiment-main">
          <div>
            <div class="experiment-name-row"><div class="experiment-name"><button class="experiment-link api-open-experiment">${escapeHtml(item.name)}</button><code>${escapeHtml(item.experiment_key)}</code></div><span class="exp-status ${statusClasses[item.status] || 'draft'}">${escapeHtml(status)}</span></div>
            <p class="exp-description">${escapeHtml(item.goal || '尚未填写实验目标')}</p>
          </div>
          <div class="exp-tags"><span class="exp-tag">Revision ${String(item.revision_no || 1).padStart(3, '0')}</span><span class="exp-tag">${escapeHtml(core.world_name || '世界待配置')}</span>${metadataTags.map(tag => `<span class="exp-tag">${escapeHtml(tag)}</span>`).join('')}</div>
        </div>
        <div class="experiment-params">
          <div class="param-cell"><span>Agent</span><strong>${core.agent_count ?? 0}</strong></div>
          <div class="param-cell"><span>Brain Skill</span><strong>${escapeHtml(core.brain_skill || 'stanford-town-brain')}</strong></div>
          <div class="param-cell"><span>Chat model</span><strong>${escapeHtml(core.chat_model || 'pending')}</strong></div>
          <div class="param-cell"><span>Virtual time / stride</span><strong>${escapeHtml(formatTime(core.start_time))} / ${core.stride_minutes || '-'}m</strong></div>
          <div class="param-cell"><span>World</span><strong>${escapeHtml(core.world_name || 'pending')}</strong></div>
          <div class="param-cell"><span>Seed / Revision</span><strong><code>${core.random_seed ?? 'unset'} / rev ${String(item.revision_no || 1).padStart(3, '0')}</code></strong></div>
        </div>        <div class="experiment-run">
          <div><div class="run-head"><span>${runTitle}</span><code>${escapeHtml(runCode)}</code></div><div class="run-value"><strong>${escapeHtml(runValue)}</strong><span>${escapeHtml(runDetail)}</span></div><div class="run-progress ${item.status === 'PAUSED' ? 'paused' : item.status === 'COMPLETED' ? 'completed' : ''}"><i style="width:${percent}%"></i></div></div>
          <div class="run-foot"><span>${updatedAtMarkup} · ${item.run_count || 0} 次运行</span><button class="run-cta ${run ? 'api-open-results' : 'api-open-experiment'}">${run ? '查看运行' : '继续配置'}</button></div>
        </div>
        <button class="experiment-menu" aria-label="实验操作">⋯</button>
      </article>`;
  }

  async function loadExperiments() {
    // 每次查询获得新的 generation；较慢的旧请求返回后会被下方守卫直接忽略。
    const generation = ++state.experimentListGeneration;
    const requestState = {
      page: state.page, pageSize: state.pageSize, status: state.status, query: state.query,
      sort: state.sort, ownerFilter: state.ownerFilter, tagFilter: state.tagFilter,
      modelFilter: state.modelFilter, mapFilter: state.mapFilter, archiveFilter: state.archiveFilter,
    };
    const isStale = () => generation !== state.experimentListGeneration
      || requestState.page !== state.page
      || requestState.pageSize !== state.pageSize
      || requestState.status !== state.status
      || requestState.query !== state.query
      || requestState.sort !== state.sort
      || requestState.ownerFilter !== state.ownerFilter
      || requestState.tagFilter !== state.tagFilter
      || requestState.modelFilter !== state.modelFilter
      || requestState.mapFilter !== state.mapFilter
      || requestState.archiveFilter !== state.archiveFilter;
    const params = new URLSearchParams({ page: state.page, page_size: state.pageSize, sort: state.sort, archived: state.archiveFilter });
    if (state.query) params.set('q', state.query);
    if (state.status) params.set('status', state.status);
    if (state.ownerFilter) params.set('owner', state.ownerFilter);
    if (state.tagFilter) params.set('tag', state.tagFilter);
    if (state.modelFilter) params.set('model', state.modelFilter);
    if (state.mapFilter) params.set('map_key', state.mapFilter);
    const list = $('experimentList');
    list.setAttribute('aria-busy', 'true');
    let data;
    try {
      data = await api(`/experiments?${params}`);
    } catch (error) {
      if (isStale()) return;
      state.visibleExperimentIds = [];
      list.innerHTML = `<div class="empty-state experiment-list-error" role="alert"><span class="empty-state-icon">!</span><strong>实验列表加载失败</strong><span>${escapeHtml(error.message || '系统服务没有返回可用的实验列表。')}</span><button class="btn btn-sm" id="retryExperimentList" type="button">重新加载</button></div>`;
      list.removeAttribute('aria-busy');
      $('experimentEmpty').hidden = true;
      $('experimentListFooter').hidden = true;
      updateTabCounts({ ALL: 0, RUNNING: 0, QUEUED: 0, DRAFT: 0, PAUSED: 0, COMPLETED: 0, FAILED: 0, CANCELLED: 0 });
      updateExperimentSelectionControls();
      list.querySelector('#retryExperimentList').addEventListener('click', () => loadExperiments());
      reportError(error);
      return;
    }
    if (isStale()) return;
    list.removeAttribute('aria-busy');
    const lastPage = Math.max(1, data.total_pages || 1);
    if (state.page > lastPage) {
      state.page = lastPage;
      await loadExperiments();
      return;
    }
    list.innerHTML = data.items.map(cardTemplate).join('');
    state.visibleExperimentIds = data.items.map(item => item.id);
    $('experimentList').classList.toggle('compact-view', state.listView === 'compact');
    $('experimentEmpty').hidden = data.total !== 0;
    $('experimentListFooter').hidden = data.total === 0;
    if (data.total) {
      const first = (data.page - 1) * data.page_size + 1;
      const last = Math.min(data.total, first + data.items.length - 1);
      $('experimentRange').textContent = `显示 ${first}–${last}，共 ${data.total} 个实验`;
      renderPages(data.total_pages || 1);
    }
    updateTabCounts(data.status_counts || {});
    updateExperimentSelectionControls();
  }

  function updateExperimentSelectionControls() {
    const count = state.selectedExperimentIds?.size || 0;
    $('compareSelectionCount').textContent = count;
    $('compareExperimentsBtn').disabled = count < 2;
    $('archiveSelectedBtn').disabled = count < 1;
    $('restoreSelectedBtn').disabled = count < 1;
    $('tagSelectedBtn').disabled = count < 1;
    $('ownerSelectedBtn').disabled = count < 1;
    const archived = state.archiveFilter === 'archived';
    $('archiveSelectedBtn').hidden = archived;
    $('restoreSelectedBtn').hidden = !archived;
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
    document.querySelectorAll('.filter-tab[data-filter]').forEach(tab => {
      const key = tab.dataset.filter;
      const count = key === 'all' ? counts.ALL : key === 'abnormal'
        ? (counts.FAILED || 0) + (counts.CANCELLED || 0)
        : counts[key.toUpperCase()];
      if (Number.isFinite(count)) tab.textContent = `${labels[key] || key} ${count}`;
    });
  }

  async function openExperiment(id, targetPage = 'overview', preferredRunId = null) {
    // 实验详情与草稿并行加载；运行中的实验会锁定到已发布 Revision，而非当前草稿。
    const generation = ++state.experimentOpenGeneration;
    const [experiment, draft] = await Promise.all([
      api(`/experiments/${id}`),
      api(`/experiments/${id}/draft`).catch(() => null),
    ]);
    if (generation !== state.experimentOpenGeneration) return;
    const lockedToPublished = executionLocksRevision(experiment);
    const published = (!draft || lockedToPublished) && experiment.current_published?.id
      ? await api(`/experiments/${id}/revisions/${experiment.current_published.id}`)
      : null;
    if (generation !== state.experimentOpenGeneration) return;
    const changingExperiment = id !== state.selectedExperimentId;
    if (changingExperiment || targetPage !== 'results') resetResultRuntime();
    if (changingExperiment) {
      state.runHistory = [];
      state.runHistoryExperimentId = null;
    }
    state.selectedExperimentId = id;
    state.experiment = experiment;
    state.draft = lockedToPublished ? null : draft;
    state.definition = (lockedToPublished ? published?.definition : draft?.definition) || published?.definition || null;
    state.revision = (lockedToPublished ? published : draft) || published;
    state.latestRunId = experiment.latest_run?.id || null;
    state.selectedRunId = targetPage === 'results' ? preferredRunId || state.latestRunId : null;
    $('navRunCount').textContent = experiment.run_count || 0;
    state.currentExperimentName = experiment.name;
    state.currentExperimentStatus = statusLabels[experiment.status] || experiment.status;
    $('experimentOwnerMeta').textContent = experiment.owner || '未设置';
    $('experimentTagsMeta').textContent = experiment.tags?.length ? experiment.tags.join(' · ') : '未设置';
    if (state.definition) fillDraft(state.definition);
    $('addAgentBtn').disabled = !draft;
    fillDefinitionOverview(state.definition, state.revision);
    refreshRunEstimateOverview(id, state.revision?.id).catch(reportError);
    applyStatusPill(state.currentExperimentStatus);
    setWorkspaceMode(state.currentExperimentStatus);
    goToPage(targetPage);
    if (targetPage === 'results') await loadRunHistory(id, state.selectedRunId);
    if (state.draft) {
      refreshModelStatus().catch(reportError);
      refreshValidation().catch(reportError);
    }
    fillLatestRunSummary(experiment).catch(reportError);
  }

  function applyExperimentRuntime(experiment) {
    if (!experiment || experiment.id !== state.selectedExperimentId) return;
    state.experiment = experiment;
    state.latestRunId = experiment.latest_run?.id || null;
    state.currentExperimentName = experiment.name;
    state.currentExperimentStatus = statusLabels[experiment.status] || experiment.status;
    $('navRunCount').textContent = experiment.run_count || 0;
    $('experimentOwnerMeta').textContent = experiment.owner || '未设置';
    $('experimentTagsMeta').textContent = experiment.tags?.length ? experiment.tags.join(' · ') : '未设置';
    if (!['experiments', 'maps', 'brains', 'crowds', 'skills'].includes(state.workspacePage)) $('topbarTitle').textContent = experiment.name;
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
    const lockedToPublished = executionLocksRevision(experiment);
    const effectiveRemoteDraft = lockedToPublished ? null : remoteDraft;
    const localRevision = state.revision;
    const definitionChanged = refreshDefinition
      || Boolean(effectiveRemoteDraft && (!state.draft
        || effectiveRemoteDraft.id !== state.draft.id
        || effectiveRemoteDraft.lock_version !== state.draft.lock_version))
      || Boolean(!effectiveRemoteDraft && remotePublished
        && (!localRevision || localRevision.id !== remotePublished.id || localRevision.state !== 'PUBLISHED'));

    let nextRevision = null;
    if (definitionChanged && !state.dirty) {
      nextRevision = effectiveRemoteDraft
        ? await api(`/experiments/${experimentId}/draft`)
        : remotePublished?.id
          ? await api(`/experiments/${experimentId}/revisions/${remotePublished.id}`)
          : null;
      if (generation !== state.selectedExperimentGeneration || experimentId !== state.selectedExperimentId) return;
      state.draft = effectiveRemoteDraft ? nextRevision : null;
      state.revision = nextRevision;
      state.definition = nextRevision?.definition || null;
      state.remoteConflictKey = null;
      if (state.definition) {
        fillDraft(state.definition);
        fillDefinitionOverview(state.definition, state.revision);
      }
    } else if (definitionChanged && state.dirty) {
      const conflictKey = `${effectiveRemoteDraft?.id || 'published'}:${effectiveRemoteDraft?.lock_version || remotePublished?.id || ''}`;
      if (!effectiveRemoteDraft) state.draft = null;
      if (state.remoteConflictKey !== conflictKey) {
        state.remoteConflictKey = conflictKey;
        showToast('实验配置已在其他页面发生变化；当前未保存内容仍保留，请重新载入后再继续编辑。', '检测到远端更新');
      }
    }

    if (!effectiveRemoteDraft && !state.dirty) state.draft = null;
    applyExperimentRuntime(experiment);
    if (state.definition) fillDefinitionOverview(state.definition, state.revision);
    if (refreshOverview) await fillLatestRunSummary(experiment);
  }

  function fillDraft(definition) {
    state.definition = definition;
    const simulation = definition.simulation;
    if ($('experimentNameDraft')) $('experimentNameDraft').value = definition.experiment?.name || state.experiment?.name || '';
    if ($('experimentGoalDraft')) $('experimentGoalDraft').value = definition.experiment?.goal || state.experiment?.goal || '';
    $('startTime').value = simulation.start_time.slice(0, 16);
    $('stride').value = simulation.stride_minutes;
    $('seed').value = simulation.random_seed;
    $('timezone').value = definition.experiment.timezone;
    $('maxSteps').value = simulation.max_steps;
    $('checkpointInterval').value = simulation.checkpoint_interval_steps;
    $('checkpointRetention').value = simulation.checkpoint_retention;
    fillModelFields(definition.models);
    $('projectionInterval').value = definition.results.agent_step_projection_interval_steps;
    $('capturePayloads').classList.toggle('on', Boolean(definition.results.capture_model_payloads));
    fillExperimentComposition(definition);
    renderAgentDraft(definition.agents);
    $('statAgentCount').textContent = definition.agents.filter(item => item.enabled).length;
    $('navAgentCount').textContent = definition.agents.filter(item => item.enabled).length;
  }

  async function saveExperimentMetadata() {
    if (!state.selectedExperimentId || !state.experiment || !state.draft) {
      throw new Error('请先打开可编辑的实验草稿');
    }
    const name = $('experimentNameDraft').value.trim();
    const goal = $('experimentGoalDraft').value.trim();
    if (!name) throw new Error('实验名称不能为空');
    const updated = await api(`/experiments/${state.selectedExperimentId}`, {
      method: 'PATCH',
      body: JSON.stringify({
        row_version: state.experiment.row_version,
        name,
        goal,
        owner: state.experiment.owner || '',
        tags: state.experiment.tags || [],
      }),
    });
    state.experiment = updated;
    state.currentExperimentName = updated.name;
    $('topbarTitle').textContent = updated.name;
    state.draft = await api(`/experiments/${state.selectedExperimentId}/draft`);
    state.revision = state.draft;
    state.definition = state.draft.definition;
    fillDraft(state.definition);
    fillDefinitionOverview(state.definition, state.revision);
    clearDirty();
    scheduleGlobalReconcile({ full: true });
    showToast('实验名称、故事目标与当前 Draft 已同步。', '实验信息已保存');
  }

  function enqueueDraftMutation(operation) {
    const queued = state.draftMutation.catch(() => {}).then(operation);
    state.draftMutation = queued.catch(() => {});
    return queued;
  }

  async function acceptSavedDraft(saved, { refreshDerived = true } = {}) {
    state.draft = saved;
    state.revision = saved;
    state.definition = saved.definition;
    state.runEstimate = null;
    fillDraft(saved.definition);
    fillDefinitionOverview(saved.definition, saved);
    clearDirty();
    if (refreshDerived) {
      await Promise.all([
        refreshRunEstimateOverview(state.selectedExperimentId, saved.id),
        refreshValidation(),
      ]);
    }
    scheduleGlobalReconcile({ full: true });
    return saved;
  }

  async function saveExperimentComposition() {
    if (!state.selectedExperimentId || !state.draft) {
      throw new Error('已发布版本只读，请先创建新修订');
    }
    if (state.formDirty) await saveDraftUnlocked({ silent: true });
    const brainRevisionId = $('experimentBrainRevisionSelect').value;
    const mapRevisionId = $('experimentMapRevisionSelect').value;
    if (!brainRevisionId || !mapRevisionId) {
      throw new Error('Brain 与地图都必须选择已发布的不可变 Revision');
    }
    let saved = state.draft;
    if (saved.definition.engine.brain_revision_id !== brainRevisionId) {
      saved = await api(`/experiments/${state.selectedExperimentId}/draft/brain`, {
        method: 'PUT',
        body: JSON.stringify({ lock_version: saved.lock_version, brain_revision_id: brainRevisionId }),
      });
    }
    if (saved.definition.world.map_revision_id !== mapRevisionId) {
      saved = await api(`/experiments/${state.selectedExperimentId}/draft/map`, {
        method: 'PUT',
        body: JSON.stringify({ lock_version: saved.lock_version, map_revision_id: mapRevisionId }),
      });
    }
    await acceptSavedDraft(saved);
    showToast('Brain 与地图 Revision 已锁定到当前实验草稿。', '资源组合已更新');
  }

  function fillExperimentComposition(definition) {
    const world = definition.world;
    window.MapWorkspace?.setExperimentContext({
      experimentId: state.selectedExperimentId,
      world,
      lockVersion: state.draft?.lock_version || 0,
      editable: Boolean(state.draft) && !state.workspaceReadonly,
    }).catch(reportError);
    prepareExperimentBrainChoices().catch(reportError);
    $('saveExperimentComposition').disabled = !state.draft || state.workspaceReadonly;
  }

  function fillDefinitionOverview(definition, revision) {
    if (!definition) return;
    const agents = definition.agents || [];
    const enabled = agents.filter(item => item.enabled).length;
    const tiles = definition.world?.definition?.tiles || [];
    const revisionNo = revision?.revision_no || 0;
    const hash = revision?.definition_hash || '';
    const brainSkill = definition.engine?.brain_skill || '未选择';
    $('overviewAgentLabel').textContent = '参与 Agent';
    $('overviewAgentUnit').textContent = '个角色';
    $('overviewSkillLabel').textContent = 'SKILL 大脑';
    $('overviewSkillFoot').textContent = '运行时从数据库 Revision 物化并随 Run 固化';
    $('overviewWorldUnit').textContent = 'tiles';
    $('overviewLatestUnit').textContent = '步';
    $('overviewDefinitionTitle').textContent = '实验定义';
    $('overviewDefinitionDescription').textContent = '配置虚拟时间、运行步数和随机复现边界。';
    $('overviewSkillCount').textContent = brainSkill;
    $('overviewSkillMeta').textContent = '自然语言技能接力';
    $('overviewTileCount').textContent = tiles.length.toLocaleString('zh-CN');
    $('overviewWorldMeta').textContent = definition.world?.world_name || '世界待配置';
    $('overviewAgentMetaStatus').textContent = enabled ? `${enabled} 个已启用 Agent` : '阻断：至少启用 1 个 Agent';
    const prelimValid = enabled > 0 && tiles.length > 0;
    $('overviewConfigStatus').textContent = prelimValid ? '等待完整校验' : '配置不完整';
    $('overviewConfigStatus').className = `chip ${prelimValid ? 'blue' : 'amber'}`;
    $('overviewBaseRevision').textContent = revision?.base_revision_id ? `revision ${String(Math.max(1, revisionNo - 1)).padStart(3, '0')}` : revisionNo ? `revision ${String(revisionNo).padStart(3, '0')}` : '新实验';
    $('overviewDefinitionHash').textContent = hash ? hash.slice(0, 12) : '草稿未发布';
    $('overviewAlgorithm').textContent = definition.engine?.algorithm_version || '—';
    const updateOptionalText = (id, value) => {
      const element = $(id);
      if (element) element.textContent = value;
    };
    updateOptionalText('overviewChatCheck', `${definition.models.chat.provider} · ${definition.models.chat.resolved_model || definition.models.chat.model}`);
    updateOptionalText('overviewEmbeddingCheck', `${definition.models.embedding.provider} · ${definition.models.embedding.resolved_model || definition.models.embedding.model}`);
    updateOptionalText('overviewAgentCheck', `${enabled} / ${agents.length} 个角色已启用`);
    updateOptionalText('overviewSkillCheck', `${brainSkill} · SKILL.md`);
    updateOptionalText('overviewWorldCheck', `${definition.world?.world_name || '未命名'} · ${tiles.length} tiles`);
    $('overviewSnapshotHash').textContent = hash ? `sha256:${hash.slice(0, 12)}…` : '草稿尚未发布';
    $('overviewSnapshotAgents').textContent = `agents ×${agents.length}`;
    $('overviewSnapshotSkills').textContent = 'skill bundle · immutable';
    const cachedValidation = state.validationReport?.definition_hash === hash
      ? state.validationReport
      : null;
    if (cachedValidation) {
      renderOverviewValidation(cachedValidation);
    } else {
      const errorCount = revision?.validation?.errors?.length || 0;
      $('overviewValidationCount').textContent = errorCount ? `${errorCount} 个阻塞项` : '等待实时校验';
      $('overviewValidationCount').className = `chip ${errorCount ? 'amber' : 'blue'}`;
    }
    $('overviewRevisionChip').textContent = revision?.state === 'PUBLISHED' ? 'Published Revision' : 'Draft Revision';
    $('overviewReleaseDetails').hidden = revision?.state !== 'PUBLISHED' || !state.experiment?.latest_run?.id;
    if (state.runEstimate?.revision_id === revision?.id
      && state.runEstimate?.definition_hash === revision?.definition_hash
      && state.runEstimate?.lock_version === revision?.lock_version) {
      applyRunEstimateToOverview(state.runEstimate);
    }
  }

  function applyRunEstimateToOverview(estimate) {
    state.runEstimate = estimate;
    const scale = estimate?.scale || {};
    if (scale.execution_mode !== 'SKILL_BRAIN') return;
    $('overviewSkillLabel').textContent = 'SKILL 大脑';
    $('overviewSkillCount').textContent = scale.brain_skill || 'stanford-town-brain';
    $('overviewSkillMeta').textContent = '自然语言技能接力';
    $('overviewSkillFoot').textContent = `${estimate.estimate.model_calls.low}–${estimate.estimate.model_calls.high} 次模型调用 · 估算 v${estimate.estimate_version || 1}`;
    $('overviewAgentMetaStatus').textContent = `${scale.agents} 个启用 Agent`;
    $('overviewTileCount').textContent = scale.steps;
    $('overviewWorldUnit').textContent = 'steps';
    const skillCheck = $('overviewSkillCheck');
    if (skillCheck) skillCheck.textContent = `${scale.brain_skill || 'stanford-town-brain'} · Qwen3.8 27B`;
    $('overviewSnapshotSkills').textContent = 'skill bundle · immutable';
  }

  async function refreshRunEstimateOverview(experimentId = state.selectedExperimentId, revisionId = state.revision?.id) {
    if (!experimentId || !revisionId) return;
    const estimate = await api(`/experiments/${experimentId}/run-estimate`);
    if (experimentId !== state.selectedExperimentId
      || revisionId !== state.revision?.id
      || estimate.definition_hash !== state.revision?.definition_hash
      || estimate.lock_version !== state.revision?.lock_version) return;
    applyRunEstimateToOverview(estimate);
  }

  async function fillLatestRunSummary(experiment) {
    const generation = ++state.latestSummaryGeneration;
    const latest = experiment.latest_run;
    if (!latest?.id) {
      if (generation !== state.latestSummaryGeneration || state.selectedExperimentId !== experiment.id) return;
      $('overviewLatestStep').textContent = '—';
      $('overviewLatestMeta').textContent = '尚未运行';
      $('overviewLatestRunCode').textContent = '';
      return;
    }
    const run = await api(`/runs/${latest.id}`);
    if (generation !== state.latestSummaryGeneration
      || state.selectedExperimentId !== experiment.id
      || state.latestRunId !== latest.id) return;
    $('overviewLatestStep').textContent = `${run.completed_steps}/${run.requested_steps}`;
    $('overviewLatestMeta').textContent = statusLabels[run.status] || run.status;
    $('overviewLatestRunCode').textContent = run.run_id.slice(0, 12);
  }

  function setSwitch(id, active) {
    $(id).classList.toggle('on', Boolean(active));
  }

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
    $('chatServiceStatus').textContent = contextWindow
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
    if (state.modelStatus) renderModelStatus(state.modelStatus);
  }

  const modelStatusLabels = {
    UNTESTED: '未检测', CHECKING: '检测中', ONLINE: '在线', OFFLINE: '离线', STALE: '已过期',
  };

  function renderModelStatus(document) {
    state.modelStatus = document;
    document.items.forEach(item => {
      const badge = $(`${item.purpose}ConnectionStatus`);
      const serviceStatus = $(`${item.purpose}ServiceStatus`);
      badge.textContent = modelStatusLabels[item.status] || item.status;
      badge.className = `connection-status ${item.status.toLowerCase()}`;
      const checked = item.checked_at ? `${formatSystemTime(item.checked_at)} ${userTimeZone}` : '从未检测';
      serviceStatus.textContent = item.status === 'ONLINE'
        ? `${item.resolved_model || '模型可用'} · ${item.latency_ms ?? '—'} ms · ${checked}`
        : `${item.reason_message || modelStatusLabels[item.status]} · ${item.suggestion || '请测试连接'}`;
      serviceStatus.title = item.checked_at ? `${new Date(item.checked_at).toISOString()} · 显示时区 ${userTimeZone}` : '';
    });
    const counts = document.counts || {};
    $('modelConnectionSummary').textContent = counts.ONLINE === 2
      ? '2 个服务在线'
      : `${counts.ONLINE || 0} 在线 · ${(counts.OFFLINE || 0) + (counts.STALE || 0) + (counts.UNTESTED || 0)} 待处理`;
    $('modelConnectionSummary').className = `connection-status ${counts.ONLINE === 2 ? 'online' : counts.OFFLINE ? 'offline' : 'stale'}`;
  }

  async function refreshModelStatus() {
    if (!state.selectedExperimentId || !state.draft) return;
    renderModelStatus(await api(`/experiments/${state.selectedExperimentId}/draft/models/status`));
  }

  function renderAgentDraft(agents) {
    const availableKeys = new Set(agents.map(agent => agent.agent_key));
    state.selectedAgentKeys.forEach(key => { if (!availableKeys.has(key)) state.selectedAgentKeys.delete(key); });
    $('agentRows').innerHTML = agents.map(agent => {
      const living = agent.spatial?.address?.living_area || [];
      const location = living.at(-1) || `${agent.coord[0]}, ${agent.coord[1]}`;
      const complete = Boolean(agent.name && agent.scratch?.daily_plan && Array.isArray(agent.coord) && agent.coord.length === 2);
      const model = agent.model_override || state.definition?.models?.chat?.model || '';
      const selected = state.selectedAgentKeys.has(agent.agent_key);
      const search = `${agent.name} ${agent.scratch.innate} ${agent.scratch.learned} ${location} ${model} ${(agent.tags || []).join(' ')}`.toLowerCase();
      const portrait = agent.portrait_asset || `/generative_agents/frontend/static/assets/village/agents/${encodeURIComponent(agent.name)}/portrait.png`;
      return `<div class="agent-row${selected ? ' is-selected' : ''}" data-agent-key="${escapeHtml(agent.agent_key)}" data-search="${escapeHtml(search)}" data-enabled="${agent.enabled}" data-complete="${complete}" data-location="${escapeHtml(location.toLowerCase())}" data-model="${escapeHtml(String(model).toLowerCase())}"><input class="agent-select-check" type="checkbox" ${selected ? 'checked' : ''} ${state.draft ? '' : 'disabled'} aria-label="选择 ${escapeHtml(agent.name)}" /><input class="checkbox agent-check" type="checkbox" ${agent.enabled ? 'checked' : ''} ${state.draft ? '' : 'disabled'} aria-label="启用 ${escapeHtml(agent.name)}" /><div class="agent-person"><div class="avatar"><img src="${portrait}" alt="" onerror="this.hidden=true" /></div><div><strong>${escapeHtml(agent.name)}</strong><span>${escapeHtml(agent.scratch.innate || '未填写特质')} · ${agent.scratch.age} 岁${model ? ` · ${escapeHtml(model)}` : ''}</span></div></div><div class="truncate">${escapeHtml(agent.currently || (agent.goals || [])[0] || '尚未填写当前目标')}</div><div class="location">${escapeHtml(location)}</div><span class="chip ${complete ? 'teal' : 'incomplete'}">${complete ? '定义完整' : '待补充'}</span><button class="row-actions agent-edit-btn" type="button" aria-label="编辑 ${escapeHtml(agent.name)}">⋯</button></div>`;
    }).join('');
    $('agentRows').nextElementSibling.innerHTML = `<span>显示全部 ${agents.length} 个实验角色</span><span>每个定义只属于当前实验 Draft</span>`;
    filterAgentRows();
    updateAgentSelectionControls();
  }

  function visibleAgentRows() {
    return [...document.querySelectorAll('#agentRows .agent-row:not(.is-filtered-out)')];
  }

  function updateAgentSelectionControls() {
    const count = state.selectedAgentKeys.size;
    $('batchAgentCount').textContent = count;
    $('deleteAgentCount').textContent = count;
    $('batchEditAgentsBtn').disabled = !state.draft || count === 0;
    $('deleteSelectedAgentsBtn').disabled = !state.draft || count === 0;
    const visible = visibleAgentRows();
    const checked = visible.filter(row => state.selectedAgentKeys.has(row.dataset.agentKey)).length;
    $('selectAllAgentRows').checked = visible.length > 0 && checked === visible.length;
    $('selectAllAgentRows').indeterminate = checked > 0 && checked < visible.length;
  }

  function filterAgentRows() {
    const query = $('agentSearch').value.trim().toLowerCase();
    const enabled = $('agentEnabledFilter').value;
    const complete = $('agentCompletenessFilter').value;
    const location = $('agentLocationFilter').value.trim().toLowerCase();
    const model = $('agentModelFilter').value.trim().toLowerCase();
    document.querySelectorAll('#agentRows .agent-row').forEach(row => {
      const visible = (!query || row.dataset.search.includes(query))
        && (enabled === 'all' || row.dataset.enabled === String(enabled === 'enabled'))
        && (complete === 'all' || row.dataset.complete === String(complete === 'complete'))
        && (!location || row.dataset.location.includes(location))
        && (!model || row.dataset.model.includes(model));
      row.classList.toggle('is-filtered-out', !visible);
    });
    updateAgentSelectionControls();
  }

  function requestedAgentBatchChanges() {
    const changes = {};
    if ($('batchAgentEnabled').value) changes.enabled = $('batchAgentEnabled').value === 'true';
    if ($('batchAgentModel').value.trim()) changes.model_override = $('batchAgentModel').value.trim();
    const x = $('batchAgentX').value;
    const y = $('batchAgentY').value;
    if (x !== '' || y !== '') {
      if (x === '' || y === '') throw new Error('批量位置必须同时填写 X 和 Y');
      changes.coord = [Number(x), Number(y)];
    }
    if ($('batchAgentGoal').value.trim()) changes.append_goal = $('batchAgentGoal').value.trim();
    const tags = $('batchAgentTags').value.split(/[,，]/).map(item => item.trim()).filter(Boolean);
    if (tags.length) changes.add_tags = tags;
    if (!Object.keys(changes).length) throw new Error('请至少填写一项批量修改');
    return changes;
  }

  function renderAgentBatchPreview(preview) {
    $('batchAgentPreview').innerHTML = `<div class="batch-preview-summary">将影响 ${preview.affected} 个 Agent；下方只列出发生变化的字段。</div>${preview.changes.map(item => {
      const changed = Object.keys(item.after).filter(key => JSON.stringify(item.before[key]) !== JSON.stringify(item.after[key]));
      return `<div class="batch-preview-row"><strong>${escapeHtml(item.name)}</strong><code>${changed.map(key => `${key}: ${JSON.stringify(item.before[key])} → ${JSON.stringify(item.after[key])}`).join('\n') || '无实际变化'}</code></div>`;
    }).join('')}`;
  }

  async function previewAgentBatch() {
    const changes = requestedAgentBatchChanges();
    const preview = await api(`/experiments/${state.selectedExperimentId}/draft/agents/batch`, {
      method: 'POST', body: JSON.stringify({ lock_version: state.draft.lock_version, agent_keys: [...state.selectedAgentKeys], changes, dry_run: true }),
    });
    state.pendingAgentBatch = { changes, lockVersion: state.draft.lock_version };
    renderAgentBatchPreview(preview);
    $('applyBatchAgents').disabled = false;
  }

  async function applyAgentBatch() {
    if (!state.pendingAgentBatch || state.pendingAgentBatch.lockVersion !== state.draft.lock_version) await previewAgentBatch();
    const previousDefinition = structuredClone(state.draft.definition);
    const result = await api(`/experiments/${state.selectedExperimentId}/draft/agents/batch`, {
      method: 'POST', body: JSON.stringify({ lock_version: state.draft.lock_version, agent_keys: [...state.selectedAgentKeys], changes: state.pendingAgentBatch.changes, dry_run: false }),
    });
    state.lastAgentBatchUndo = previousDefinition;
    state.draft = result.draft;
    state.definition = result.draft.definition;
    state.pendingAgentBatch = null;
    fillDraft(state.draft.definition);
    fillDefinitionOverview(state.draft.definition, state.draft);
    $('undoBatchAgents').disabled = false;
    $('applyBatchAgents').disabled = true;
    renderAgentBatchPreview(result);
    clearDirty();
    showToast(`${result.affected} 个 Agent 已批量更新，可在本弹窗中立即撤销。`, '批量修改已应用');
  }

  async function undoAgentBatch() {
    if (!state.lastAgentBatchUndo) return;
    const saved = await api(`/experiments/${state.selectedExperimentId}/draft`, {
      method: 'PUT', body: JSON.stringify({ lock_version: state.draft.lock_version, data: state.lastAgentBatchUndo }),
    });
    state.lastAgentBatchUndo = null;
    state.draft = saved; state.definition = saved.definition;
    fillDraft(saved.definition); fillDefinitionOverview(saved.definition, saved);
    $('undoBatchAgents').disabled = true;
    $('batchAgentPreview').innerHTML = '<div class="batch-preview-summary">上次批量修改已撤销。</div>';
    showToast('已恢复批量修改前的完整 Agent 配置。', '撤销成功');
  }

  function downloadJson(filename, value) {
    const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob); link.download = filename; link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 0);
  }

  function parseCsvRows(text) {
    const rows = []; let row = []; let field = ''; let quoted = false;
    for (let index = 0; index < text.length; index += 1) {
      const char = text[index];
      if (char === '"' && quoted && text[index + 1] === '"') { field += '"'; index += 1; }
      else if (char === '"') quoted = !quoted;
      else if (char === ',' && !quoted) { row.push(field); field = ''; }
      else if ((char === '\n' || char === '\r') && !quoted) {
        if (char === '\r' && text[index + 1] === '\n') index += 1;
        row.push(field); field = ''; if (row.some(value => value.trim())) rows.push(row); row = [];
      } else field += char;
    }
    row.push(field); if (row.some(value => value.trim())) rows.push(row);
    if (rows.length < 2) throw new Error('CSV 至少需要表头和一行数据');
    const headers = rows.shift().map(item => item.trim());
    return rows.map(values => Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ''])));
  }

  function normalizeImportedAgent(raw) {
    const value = { ...raw };
    if (typeof value.coord === 'string') value.coord = value.coord.split(/[,，]/).map(Number);
    if (!value.coord && value.x !== undefined && value.y !== undefined) value.coord = [Number(value.x), Number(value.y)];
    if (typeof value.enabled === 'string') value.enabled = !['false', '0', '否'].includes(value.enabled.toLowerCase());
    ['tags', 'goals'].forEach(key => { if (typeof value[key] === 'string') value[key] = value[key].split(/[|,，]/).map(item => item.trim()).filter(Boolean); });
    ['scratch', 'spatial'].forEach(key => { if (typeof value[key] === 'string' && value[key].trim()) value[key] = JSON.parse(value[key]); });
    delete value.x; delete value.y;
    return value;
  }

  async function stageAgentImport(file) {
    const text = await file.text();
    const parsed = file.name.toLowerCase().endsWith('.csv') ? parseCsvRows(text) : JSON.parse(text);
    const items = (Array.isArray(parsed) ? parsed : parsed.agents).map(normalizeImportedAgent);
    if (!items.length) throw new Error('导入文件中没有 Agent');
    state.pendingAgentImport = { items, filename: file.name };
    $('agentImportPreview').innerHTML = `<div class="batch-preview-summary">${escapeHtml(file.name)} · ${items.length} 个 Agent</div>${items.slice(0, 50).map(item => `<div class="batch-preview-row"><strong>${escapeHtml(item.name || item.agent_key || '未命名')}</strong><code>${escapeHtml(JSON.stringify(item))}</code></div>`).join('')}${items.length > 50 ? `<div class="batch-preview-summary">其余 ${items.length - 50} 项已折叠</div>` : ''}`;
    $('confirmAgentImport').disabled = false;
    openModal('agentImportModal', 'agentImportStrategy');
  }

  async function applyAgentImport() {
    if (!state.pendingAgentImport) return;
    const strategy = $('agentImportStrategy').value;
    const definition = structuredClone(state.draft.definition);
    const byKey = new Map(definition.agents.map((agent, index) => [agent.agent_key, { agent, index }]));
    let added = 0; let updated = 0; let skipped = 0;
    for (const raw of state.pendingAgentImport.items) {
      const key = String(raw.agent_key || '').trim();
      if (!key) throw new Error(`Agent ${raw.name || '未命名'} 缺少 agent_key`);
      const existing = byKey.get(key);
      if (existing && strategy === 'skip') { skipped += 1; continue; }
      if (existing) {
        definition.agents[existing.index] = strategy === 'replace' ? raw : { ...existing.agent, ...raw, scratch: { ...existing.agent.scratch, ...(raw.scratch || {}) }, spatial: { ...existing.agent.spatial, ...(raw.spatial || {}) } };
        updated += 1;
      } else { definition.agents.push(raw); added += 1; }
    }
    const saved = await api(`/experiments/${state.selectedExperimentId}/draft`, {
      method: 'PUT', body: JSON.stringify({ lock_version: state.draft.lock_version, data: definition }),
    });
    state.draft = saved; state.definition = saved.definition; state.pendingAgentImport = null;
    closeModal('agentImportModal'); fillDraft(saved.definition); fillDefinitionOverview(saved.definition, saved);
    showToast(`新增 ${added}、更新 ${updated}、跳过 ${skipped} 个 Agent。`, '导入完成');
  }

  async function refreshRunHistoryList(experimentId, preferredRunId = state.selectedRunId) {
    // 这里主动翻完稳定游标，保证 Run 下拉框不会只显示第一页。
    const generation = ++state.runHistoryGeneration;
    const items = [];
    const known = new Set();
    let cursor = null;
    do {
      const query = cursor ? `?limit=100&cursor=${encodeURIComponent(cursor)}` : '?limit=100';
      const page = await api(`/experiments/${experimentId}/runs${query}`);
      if (generation !== state.runHistoryGeneration || experimentId !== state.selectedExperimentId) return null;
      page.items.forEach(item => {
        if (!known.has(item.run_id)) {
          known.add(item.run_id);
          items.push(item);
        }
      });
      cursor = page.next_cursor;
    } while (cursor);
    state.runHistory = items;
    state.runHistoryExperimentId = experimentId;
    if (preferredRunId && !known.has(preferredRunId)) {
      const selected = await api(`/runs/${preferredRunId}`).catch(() => null);
      if (generation !== state.runHistoryGeneration || experimentId !== state.selectedExperimentId) return null;
      if (selected?.experiment_id === experimentId) state.runHistory.unshift(selected);
    }
    renderRunSelect(preferredRunId);
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
    await loadResults(runId);
  }

  function renderRunSelect(selectedRunId = state.selectedRunId) {
    $('navRunCount').textContent = state.experiment?.run_count ?? state.runHistory.length;
    const select = $('resultRunSelect');
    select.innerHTML = state.runHistory.length ? state.runHistory.map((run, index) => {
      const runNumber = state.runHistory.length - index;
      const status = statusLabels[run.status] || run.status;
      const fingerprint = run.execution_hash ? ` · 执行 ${run.execution_hash.slice(0, 8)}` : '';
      return `<option value="${escapeHtml(run.run_id)}">运行 ${runNumber} · ${escapeHtml(status)} · ${run.completed_steps}/${run.requested_steps} 步${escapeHtml(fingerprint)}</option>`;
    }).join('') : '<option value="">暂无运行记录</option>';
    select.disabled = !state.runHistory.length;
    if (selectedRunId && state.runHistory.some(run => run.run_id === selectedRunId)) select.value = selectedRunId;
    const selectedRun = state.runHistory.find(run => run.run_id === select.value);
    const fingerprint = selectedRun?.execution_hash || '';
    $('resultExecutionHash').hidden = state.workspacePage !== 'results';
    $('resultExecutionHash').textContent = fingerprint
      ? `执行指纹 ${fingerprint.slice(0, 12)}`
      : '执行指纹待生成';
    $('resultExecutionHash').title = fingerprint || 'Run 清单物化后生成';
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
    state.agentDetailSignatures.clear();
    state.agentDetailCache.clear();
    state.agentContentPages.clear();
    state.renderedAgentDetailKey = null;
    state.traceItems = [];
    state.tracePage = 1;
    state.modelUsageItems = [];
    state.modelUsagePage = 1;
    state.operationEvents = [];
    state.eventPage = 1;
    state.checkpointItems = [];
    state.checkpointPage = 1;
    state.selectedRunId = null;
    stopModelTracePolling();
    clearResultDurationTimer();
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

  function resetOperationsWorkspaceForRunSwitch() {
    // Run-scoped diagnostics must never retain either data or selection ownership
    // from the previous Run while the new requests are in flight.
    state.operationFactsGeneration += 1;
    state.logGeneration += 1;
    state.checkpointGeneration += 1;
    stopModelTracePolling();
    closeLogStream();
    state.operationsAbortController?.abort();
    state.operationsAbortController = null;
    state.operationsRunId = null;
    state.selectedAttemptId = null;
    state.selectedTraceAttemptId = null;
    state.logRunId = null;
    state.logAttemptId = null;
    state.logCursor = 0;
    state.logFileId = null;
    state.logRecords = [];
    state.logCarry = '';
    state.logDiscardUntilNewline = false;
    state.operationEvents = [];
    state.eventCursor = 0;
    state.eventPage = 1;
    state.traceItems = [];
    state.traceCursor = null;
    state.traceEof = true;
    state.tracePage = 1;
    state.checkpointItems = [];
    state.checkpointPage = 1;
    state.traceDetailState = null;
    state.checkpointPreviewState = null;
    $('attemptLogSelect').innerHTML = '<option value="">正在加载 Attempt…</option>';
    $('traceAttemptSelect').innerHTML = '<option value="">正在加载 Attempt…</option>';
    $('attemptRows').innerHTML = '<div class="empty-state"><strong>正在加载当前 Run 的执行尝试…</strong></div>';
    $('logViewport').textContent = '等待加载当前 Run 的日志…';
    $('modelTraceRows').innerHTML = '<div class="diagnostic-list-empty">正在加载当前 Run 的模型调用…</div>';
    $('systemEventRows').innerHTML = '<div class="diagnostic-list-empty">正在加载当前 Run 的系统事件…</div>';
    $('checkpointRows').innerHTML = '<div class="diagnostic-list-empty">正在加载当前 Run 的检查点…</div>';
    $('modelTraceDetail').hidden = true;
    $('checkpointDetail').hidden = true;
    $('checkpointPreview').hidden = true;
  }

  async function loadResults(runId) {
    // 切换 Run 时先拆除旧 SSE、日志流和 Phaser 实例，避免跨 Run DOM/网络所有权泄漏。
    const generation = ++state.resultGeneration;
    const experimentId = state.selectedExperimentId;
    teardownReplay();
    state.conversationGeneration += 1;
    state.memoryGeneration += 1;
    resetOperationsWorkspaceForRunSwitch();
    state.selectedRunId = runId;
    renderRunSelect(runId);
    if (state.eventSource) state.eventSource.close();
    clearResultDurationTimer();
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
    ['queue', 'state', 'reconcile', 'progress', 'quality', 'post_processing', 'result_rewound',
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
        refreshResultData(runId, generation, { silent: true }).catch(reportError);
      }
    }, 2000);
  }

  async function refreshResultData(runId, generation = state.resultGeneration, { silent = false } = {}) {
    const requestGeneration = state.resultRequestGeneration = (state.resultRequestGeneration || 0) + 1;
    const [run, timeline, agents, conversations, memories, operations] = await Promise.all([
      api(`/runs/${runId}`), api(`/runs/${runId}/results/timeline?limit=500`),
      api(`/runs/${runId}/results/agents`), api(`/runs/${runId}/results/conversations?limit=50`),
      api(`/runs/${runId}/results/memories?limit=50`), api(`/runs/${runId}/results/operations`),
    ]);
    if (generation !== state.resultGeneration
      || requestGeneration !== state.resultRequestGeneration
      || runId !== state.selectedRunId) return;
    state.currentRun = run;
    const historyIndex = state.runHistory.findIndex(item => item.run_id === runId);
    if (historyIndex >= 0) state.runHistory[historyIndex] = { ...state.runHistory[historyIndex], ...run };
    renderRunSelect(runId);
    startResultDurationTimer(run);
    renderTimeline(timeline);
    renderAgents(agents.items, { silent });
    renderConversations(conversations.items);
    renderMemories(memories.items);
    renderOperations(operations);
    if (run.quality) renderRunQuality(run.quality);
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
    if (typeof syncModelTracePolling === 'function') {
      syncModelTracePolling(runId, generation);
    }
    if (document.querySelector('[data-result-panel="timeline"]')?.classList.contains('active')) {
      ensureReplayPlayer(runId, generation).catch(reportError);
    } else if (state.replayPlayer && state.replayRunId === runId) {
      state.replayPlayer.refreshAvailable().catch(error => {
        if (error.name !== 'AbortError') console.warn('回放边界刷新失败', error);
      });
    }
    syncWorkspaceUrl();
  }

  function renderRunQuality(quality) {
    const banner = $('runQualityBanner');
    if (!banner) return;
    const status = quality?.quality_status || 'PENDING';
    const labels = {
      PASS: '通过', WARNING: '有观察项', UNKNOWN: '评估不可用',
      NOT_EVALUATED: '未评估', PENDING: '待评估',
    };
    if (banner.dataset) banner.dataset.status = status;
    const issues = quality?.issues || [];
    const count = issues.length;
    const detail = count ? `
      <details class="run-quality-details">
        <summary>展开 ${count} 项质量告警</summary>
        <ol>${issues.map((issue, index) => {
          const step = Number(issue?.step_no);
          const target = Number.isInteger(step) && step > 0
            ? `<button type="button" class="quality-step-link" data-quality-step="${step}">定位 Step ${step}</button>`
            : '';
          const scope = [issue?.agent_key ? `Agent ${issue.agent_key}` : '', target].filter(Boolean).join(' · ');
          const evidence = issue?.evidence && Object.keys(issue.evidence).length
            ? `<pre>${escapeHtml(JSON.stringify(issue.evidence, null, 2))}</pre>`
            : '';
          return `<li><div><strong>${index + 1}. ${escapeHtml(issue?.code || 'QUALITY_WARNING')}</strong><span>${escapeHtml(scope)}</span></div><p>${escapeHtml(issue?.message || '行为质量观察项')}</p>${evidence}</li>`;
        }).join('')}</ol>
      </details>` : '';
    banner.innerHTML = `<div class="run-quality-summary"><strong>行为质量：${escapeHtml(labels[status] || status)}</strong><span>${escapeHtml(quality?.summary || '')}${count ? ` · ${count} 项告警` : ''}（不改变运行完成状态）</span></div>${detail}`;
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
        if (full && selectedRunId) tasks.push(refreshResultData(selectedRunId, resultGeneration, { silent: true }));
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
    const remove = $('deleteRunBtn');
    const continueRun = $('runContinueBtn');
    const canContinue = isRunRecoverable(run);
    pauseResume.hidden = run.status !== 'RUNNING';
    pauseResume.textContent = '暂停运行';
    cancel.hidden = !['QUEUED', 'RUNNING', 'PAUSE_REQUESTED', 'PAUSED'].includes(run.status);
    remove.hidden = ['QUEUED', 'STARTING', 'RUNNING', 'PAUSE_REQUESTED', 'PAUSED', 'CANCEL_REQUESTED'].includes(run.status);
    continueRun.hidden = !canContinue;
    continueRun.textContent = canContinue ? `继续执行 · Step ${run.recoverable_step}` : '继续执行';
    $('resultRunControls').hidden = state.workspacePage !== 'results' || (pauseResume.hidden && cancel.hidden && remove.hidden);
  }

  function renderAgents(items, { silent = false } = {}) {
    state.agentResults = [...items].sort((a, b) => String(a.display_name || a.agent_key).localeCompare(String(b.display_name || b.agent_key), 'zh-CN'));
    if (!state.replayAgentDefinitions.length) {
      state.replayAgentDefinitions = state.agentResults.map(item => ({
        agent_key: item.agent_key,
        display_name: item.display_name || item.agent_key,
      }));
    }
    renderReplayAgentRoster();
    const options = '<option value="all">全部 Agent</option>' + items.map(item => `<option value="${escapeHtml(item.agent_key)}">${escapeHtml(item.display_name || item.agent_key)}</option>`).join('');
    if ($('conversationAgentFilter').innerHTML !== options) $('conversationAgentFilter').innerHTML = options;
    if ($('memoryAgentFilter').innerHTML !== options) $('memoryAgentFilter').innerHTML = options;
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
    showAgentDetail(state.selectedAgentKey, { silent }).catch(reportError);
  }

  function createAgentResultTab(item) {
    const template = document.createElement('template');
    template.innerHTML = '<button type="button" role="tab" class="agent-result-tab" aria-controls="resultAgentDetail"><span class="agent-tab-avatar-fallback" aria-hidden="true"></span><img class="agent-tab-portrait" alt=""/><span class="agent-tab-copy"><strong><i class="agent-tab-status" aria-hidden="true"></i></strong><small></small></span></button>';
    const tab = template.content.firstElementChild;
    const image = tab.querySelector('.agent-tab-portrait');
    image.addEventListener('error', () => {
      image.hidden = true;
      image.previousElementSibling.style.display = 'grid';
    });
    return tab;
  }

  function updateAgentResultTab(tab, item, active) {
    const name = item.display_name || item.agent_key;
    const terminal = ['COMPLETED', 'FAILED', 'CANCELLED'].includes(item.run_status || state.currentRun?.status);
    const statusText = terminal ? '已结束' : ({ CHAT: '对话中', MOVING: '移动中', REST: '休息中', OTHER: '活动中' }[item.latest_activity_kind] || item.latest_activity_kind);
    tab.dataset.agentKey = item.agent_key;
    tab.dataset.agentStatus = item.latest_activity_kind;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', String(active));
    tab.tabIndex = active ? 0 : -1;
    tab.querySelector('.agent-tab-avatar-fallback').textContent = name.slice(0, 1);
    const image = tab.querySelector('.agent-tab-portrait');
    const portraitUrl = item.portrait_url || '';
    if (image.getAttribute('src') !== portraitUrl) {
      image.hidden = false;
      image.previousElementSibling.style.display = '';
      image.setAttribute('src', portraitUrl);
    }
    const strong = tab.querySelector('.agent-tab-copy strong');
    strong.replaceChildren(strong.querySelector('.agent-tab-status'), document.createTextNode(name));
    tab.querySelector('.agent-tab-copy small').textContent = `${statusText} · 计划 ${item.plan_count || 0} · 事件 ${item.event_count || 0}`;
  }

  function renderAgentTabs() {
    const strip = $('resultAgentButtons');
    const previousScrollLeft = strip.scrollLeft;
    const focusedAgentKey = strip.contains(document.activeElement)
      ? document.activeElement.closest('.agent-result-tab')?.dataset.agentKey
      : null;
    const query = $('resultAgentSearch').value.trim().toLowerCase();
    const status = state.agentStatusFilter;
    const visible = state.agentResults.filter(item => {
      const searchable = [item.display_name, item.agent_key, item.address, item.currently,
        item.latest_action, item.definition?.daily_plan, item.definition?.learned].join(' ').toLowerCase();
      return (status === 'all' || item.latest_activity_kind === status) && (!query || searchable.includes(query));
    });
    if (!visible.length) {
      strip.innerHTML = '<div class="empty-state"><strong>没有符合条件的 Agent</strong><span>尝试清除搜索词或切换状态筛选。</span></div>';
      $('resultAgentDetail').innerHTML = '<div class="empty-state"><strong>没有可显示的 Agent 内容</strong><span>调整上方筛选后继续查看。</span></div>';
      $('resultAgentDetail').dataset.agentKey = '';
      return;
    }
    if (!visible.some(item => item.agent_key === state.selectedAgentKey)) state.selectedAgentKey = visible[0].agent_key;
    const existingTabs = new Map([...strip.querySelectorAll('.agent-result-tab')].map(tab => [tab.dataset.agentKey, tab]));
    const fragment = document.createDocumentFragment();
    visible.forEach(item => {
      const active = item.agent_key === state.selectedAgentKey;
      const tab = existingTabs.get(item.agent_key) || createAgentResultTab(item);
      updateAgentResultTab(tab, item, active);
      fragment.append(tab);
    });
    strip.replaceChildren(fragment);
    strip.scrollLeft = previousScrollLeft;
    if (focusedAgentKey) {
      strip.querySelector(`[data-agent-key="${CSS.escape(focusedAgentKey)}"]`)?.focus({ preventScroll: true });
    }
  }

  function ensureAgentTabVisible(tab) {
    const strip = $('resultAgentButtons');
    if (!tab || !strip) return;
    const left = tab.offsetLeft;
    const right = left + tab.offsetWidth;
    if (left < strip.scrollLeft) strip.scrollTo({ left: Math.max(0, left - 8), behavior: 'smooth' });
    else if (right > strip.scrollLeft + strip.clientWidth) strip.scrollTo({ left: right - strip.clientWidth + 8, behavior: 'smooth' });
  }

  async function showAgentDetail(agentKey, { silent = false } = {}) {
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
    if (!silent) ensureAgentTabVisible(activeTab);
    const panel = $('resultAgentDetail');
    panel.dataset.agentKey = agentKey;
    if (!silent) panel.innerHTML = '<div class="agent-result-loading">正在读取 Agent 结构化内容…</div>';
    const detail = await api(`/runs/${runId}/results/agents/${encodeURIComponent(agentKey)}`);
    if (generation !== state.agentDetailGeneration
      || detail.run_id !== state.selectedRunId
      || runId !== state.selectedRunId
      || agentKey !== state.selectedAgentKey) return;
    if (panel.dataset.agentKey !== agentKey) return;
    const signature = JSON.stringify(detail);
    state.agentDetailCache.set(`${runId}:${agentKey}`, detail);
    if (silent
      && state.renderedAgentDetailKey === agentKey
      && state.agentDetailSignatures.get(agentKey) === signature) return;
    const focusedContent = panel.contains(document.activeElement)
      ? document.activeElement.closest('[data-agent-content]')?.dataset.agentContent
      : null;
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    panel.innerHTML = `<div class="agent-result-body">${renderAgentDetail(detail)}</div>`;
    state.agentDetailSignatures.set(agentKey, signature);
    state.renderedAgentDetailKey = agentKey;
    if (focusedContent) {
      panel.querySelector(`[data-agent-content="${CSS.escape(focusedContent)}"]`)?.focus({ preventScroll: true });
    }
    if (silent) window.scrollTo(scrollX, scrollY);
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

  const AGENT_CONTENT_PAGE_SIZE = 5;

  function agentContentPageKey(kind) {
    return `${state.selectedRunId || ''}:${state.selectedAgentKey || ''}:${kind}`;
  }

  function paginationPageNumbers(page, totalPages) {
    if (totalPages <= 7) return Array.from({ length: totalPages }, (_, index) => index + 1);
    const pages = new Set([1, totalPages]);
    for (let candidate = page - 2; candidate <= page + 2; candidate += 1) {
      if (candidate > 1 && candidate < totalPages) pages.add(candidate);
    }
    return [...pages].sort((a, b) => a - b);
  }

  function agentContentPager(kind, totalItems) {
    const totalPages = Math.max(1, Math.ceil(totalItems / AGENT_CONTENT_PAGE_SIZE));
    const key = agentContentPageKey(kind);
    const page = Math.min(totalPages, Math.max(1, Number(state.agentContentPages.get(key)) || 1));
    state.agentContentPages.set(key, page);
    const pages = paginationPageNumbers(page, totalPages);
    let previous = 0;
    const pageButtons = pages.map(pageNumber => {
      const gap = previous && pageNumber - previous > 1 ? '<span class="agent-page-gap">…</span>' : '';
      previous = pageNumber;
      return `${gap}<button type="button" class="page-button${pageNumber === page ? ' active' : ''}" data-agent-page-kind="${kind}" data-agent-page="${pageNumber}"${pageNumber === page ? ' aria-current="page"' : ''}>${pageNumber}</button>`;
    }).join('');
    const label = { plan: '计划', event: '事件', action: '行动', conversation: '对话', memory: '记忆', state: '状态变化' }[kind] || '内容';
    return {
      itemsFrom: (page - 1) * AGENT_CONTENT_PAGE_SIZE,
      itemsTo: page * AGENT_CONTENT_PAGE_SIZE,
      html: `<nav class="agent-content-pagination" aria-label="${label}分页"><span>第 ${page} / ${totalPages} 页 · 共 ${totalItems} 条</span><div class="agent-content-page-buttons"><button type="button" class="page-button" aria-label="上一页" data-agent-page-kind="${kind}" data-agent-page="${page - 1}"${page <= 1 ? ' disabled' : ''}>‹</button>${pageButtons}<button type="button" class="page-button" aria-label="下一页" data-agent-page-kind="${kind}" data-agent-page="${page + 1}"${page >= totalPages ? ' disabled' : ''}>›</button></div></nav>`,
    };
  }

  function agentRecord(time, title, detail, tag, tagClass = '') {
    return `<div class="agent-record"><time>${escapeHtml(time || '—')}</time><span class="agent-record-copy"><strong>${escapeHtml(title || '未命名记录')}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ''}</span><span class="agent-record-tag ${tagClass}">${escapeHtml(tag || '记录')}</span></div>`;
  }

  function renderAgentPlanSection(detail, currentPlan) {
    const definition = detail.agent.definition || {};
    const revisions = detail.plan_revisions || [];
    const pagination = agentContentPager('plan', revisions.length);
    const records = revisions.slice(pagination.itemsFrom, pagination.itemsTo).map(item => {
      const plan = item.items?.length ? agentPlanText(item.items[0]) : '日程内容未记录';
      return agentRecord(`Step ${item.effective_step}`, plan, item.reason || '日程修订', `修订 ${item.revision_no}`);
    }).join('');
    const empty = records || '<div class="agent-section-empty">本次运行尚未产生计划修订；这里显示已发布角色的初始计划。</div>';
    return agentSection('plan','▤','计划','初始目标、日程与计划修订',revisions.length,
      `<div class="agent-current-plan"><small>当前计划</small><strong>${escapeHtml(currentPlan)}</strong><p>${escapeHtml(definition.daily_plan || definition.lifestyle || '未记录日常计划')}</p></div><div class="agent-record-list">${empty}</div>${pagination.html}`);
  }

  function renderAgentEventSection(events) {
    const pagination = agentContentPager('event', events.length);
    const rows = events.slice(pagination.itemsFrom, pagination.itemsTo).map(event => {
      const payload = event.payload || {};
      const title = event.title && event.title !== event.event_type ? event.title : agentEventTitle(event.event_type, payload);
      const detail = event.detail || agentEventDetail(event.event_type, payload) || event.location || '';
      return agentRecord(`Step ${event.step_no}`, title, detail, agentEventLabel(event.event_type), 'event');
    }).join('');
    return agentSection('event','✦','事件','产生、感知与参与的领域事件',events.length,
      `<div class="agent-record-list">${rows || '<div class="agent-section-empty">当前 Agent 尚未产生可归属的领域事件。</div>'}</div>${pagination.html}`);
  }

  function renderAgentActionSection(actions) {
    const pagination = agentContentPager('action', actions.length);
    const rows = actions.slice(pagination.itemsFrom, pagination.itemsTo).map(action => {
      const context = action.decision_context || {};
      const perceptions = context.perceptions?.length || 0;
      const schedule = Object.keys(context.schedule || {});
      const evidence = (perceptions || schedule.length)
        ? `<div class="agent-decision-context">感知 ${perceptions} 条${schedule.length ? ` · 当步计划：${escapeHtml(schedule[0])}` : ''}</div>` : '';
      return `<div class="agent-record"><time>Step ${action.step_no}</time><span class="agent-record-copy"><strong>${escapeHtml(action.action || '未记录行动')}</strong><span>${escapeHtml(action.address || '位置未记录')} · ${escapeHtml(formatTime(action.virtual_time))}</span>${evidence}</span><span class="agent-record-tag">${escapeHtml(agentActivityLabel(action.activity_kind))}</span></div>`;
    }).join('');
    return agentSection('action','➜','行动','执行动作、移动与当步决策上下文',actions.length,
      `<div class="agent-record-list">${rows || '<div class="agent-section-empty">尚无已提交行动。</div>'}</div>${pagination.html}`);
  }

  function renderAgentConversationSection(items) {
    const pagination = agentContentPager('conversation', items.length);
    const rows = items.slice(pagination.itemsFrom, pagination.itemsTo).map(item => agentRecord(`Step ${item.start_step}`,
      (item.participant_names || item.participants || []).join(' ↔ '),
      item.summary || `${item.message_count} 条消息 · ${item.location || '位置未记录'}`,
      `${item.message_count} 条`, '')) .join('');
    return agentSection('conversation','◌','对话','与其他 Agent 的实际交流',items.length,
      `<div class="agent-record-list">${rows || '<div class="agent-section-empty">当前 Agent 尚未产生对话。相邻的计划、事件和行动仍可用于定位原因。</div>'}</div>${pagination.html}`);
  }

  function renderAgentMemorySection(items) {
    const pagination = agentContentPager('memory', items.length);
    const rows = items.slice(pagination.itemsFrom, pagination.itemsTo).map(item => agentRecord(`Step ${item.created_step ?? '—'}`,
      item.description || item.memory_id,
      `重要度 ${item.poignancy ?? '—'} · ${item.state || 'UNKNOWN'}`,
      item.type || '记忆', 'memory')).join('');
    return agentSection('memory','◇','记忆','新增、访问与淘汰的记忆',items.length,
      `<div class="agent-record-list">${rows || '<div class="agent-section-empty">当前 Agent 尚未提交记忆变化。</div>'}</div>${pagination.html}`);
  }

  function renderAgentStateSection(items) {
    const pagination = agentContentPager('state', items.length);
    const rows = items.slice(pagination.itemsFrom, pagination.itemsTo).map(item => agentRecord(`Step ${item.step_no}`,
      `${item.title}发生变化`, `${item.before || '—'} → ${item.after || '—'}`, item.kind)).join('');
    return agentSection('state','↕','状态变化','位置、当前状态与行动切换',items.length,
      `<div class="agent-record-list">${rows || '<div class="agent-section-empty">当前采样窗口内没有状态变化。</div>'}</div>${pagination.html}`);
  }

  function agentPlanText(item) {
    if (!item || typeof item !== 'object') return String(item || '未记录计划');
    return item.description || item.activity || item.describe || item.task || item.plan || JSON.stringify(item);
  }

  function agentActivityLabel(kind) {
    return { CHAT: '对话', MOVING: '移动', REST: '休息', OTHER: '行动' }[kind] || kind || '行动';
  }

  function agentEventLabel(kind) {
    return {
      AGENT_MOVED: '移动',
      AGENT_ACTED: '行动',
      AGENT_WAITED: '等待',
      AGENT_SPOKE: '对话',
      GAME_OBJECT_INTERACTED: '交互',
      GAME_OBJECT_STATE_CHANGED: '对象状态',
      CONVERSATION: '参与',
      MEMORY: '记忆',
      SCHEDULE: '计划',
    }[kind] || kind || '事件';
  }

  function agentEventTitle(kind, payload) {
    const fact = payload.structured_payload || payload;
    const semanticTitle = fact.description
      || payload.title
      || [payload.subject, payload.predicate, payload.object].filter(Boolean).join('');
    if (semanticTitle && semanticTitle !== kind) return semanticTitle;
    if (kind === 'AGENT_MOVED') return 'Agent 移动到新的位置';
    if (kind === 'CONVERSATION') return 'Agent 参与了一次对话';
    return payload.title || kind || '领域事件';
  }

  function agentEventDetail(kind, payload) {
    if (kind === 'AGENT_MOVED') {
      const fact = payload.structured_payload || payload;
      return `${JSON.stringify(fact.from_coord || [])} → ${JSON.stringify(fact.to_coord || [])}`;
    }
    if (kind === 'GAME_OBJECT_STATE_CHANGED') {
      const fact = payload.structured_payload || {};
      return `${fact.object_key || ''} → ${JSON.stringify(fact.after || {})}`;
    }
    if (kind === 'CONVERSATION') return `${payload.message_count || 0} 条消息`;
    return payload.detail
      || [payload.subject, payload.predicate, payload.object].filter(Boolean).join(' / ');
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
    $('memoryRows').innerHTML = items.map(item => {
      const lifecycle = item.superseded_by_memory_id
        ? ` → ${item.superseded_by_memory_id}`
        : item.supersedes_memory_id
          ? ` ← ${item.supersedes_memory_id}`
          : item.invalidated_reason
            ? ` · ${item.invalidated_reason}`
            : '';
      return `<tr data-memory-agent="${escapeHtml(item.agent_key)}" data-memory-type="${escapeHtml(item.type)}"><td><span class="memory-type ${escapeHtml(item.type)}">${escapeHtml(item.type)}</span></td><td>${escapeHtml(item.agent_name || item.agent_key)}</td><td class="memory-desc">${escapeHtml(item.description || '—')}</td><td><span class="chip">${escapeHtml(item.state || 'ACTIVE')}</span><small>${escapeHtml(lifecycle)}</small></td><td>${item.poignancy ?? '—'}</td><td>${item.created_step} / ${item.last_accessed_step ?? '—'}</td><td><code>${escapeHtml(item.memory_id)}</code></td></tr>`;
    }).join('');
  }

  function renderTimeline(timeline) {
    timeline.steps ||= [];
    timeline.events ||= [];
    timeline.agent_steps ||= [];
    timeline.requested_steps ||= state.currentRun?.requested_steps || 0;
    state.timeline = timeline;
    const slider = $('timelineRange');
    slider.min = timeline.steps.length ? timeline.steps[0].step_no : 0;
    slider.max = Math.max(0, timeline.available_step);
    const replayOwnsRun = state.replayPlayer && state.replayRunId === state.selectedRunId;
    const firstStep = timeline.available_step > 0 ? Math.max(1, Number(slider.min) || 1) : 0;
    const preservedStep = replayOwnsRun
      ? Number(state.replayPlayer.pendingStep ?? state.replayPlayer.currentStep ?? firstStep)
      : firstStep;
    slider.value = Math.max(firstStep, Math.min(timeline.available_step, preservedStep));
    updateTimelineStep(Number(slider.value), { seekReplay: false });
  }

  function updateTimelineStep(stepNo, { seekReplay = true } = {}) {
    const timeline = state.timeline;
    if (!timeline) return;
    const step = [...timeline.steps].reverse().find(item => item.step_no <= stepNo);
    $('timelineStep').textContent = `Step ${String(stepNo).padStart(3, '0')} / ${timeline.requested_steps || 0}`;
    $('timelineTime').textContent = step ? formatTime(step.virtual_time) : '等待结果';
    $('mapTimeLabel').textContent = `${step ? formatTime(step.virtual_time) : '—'} · Step ${String(stepNo).padStart(3, '0')}`;
    if (seekReplay && state.replayPlayer && state.replayRunId === state.selectedRunId) {
      state.replayPlayer.seek(stepNo).catch(reportError);
    }
    const events = timeline.events.filter(item => Math.abs(item.step_no - stepNo) <= 1);
    $('timelineStreamMeta').textContent = `Step ${stepNo} 附近 · ${events.length} 条`;
    $('timelineStreamItems').innerHTML = events.length ? events.map(event => `<div class="stream-item"><time class="stream-time">${formatTime(event.virtual_time)}</time><div class="stream-copy"><strong>${escapeHtml(event.title)}</strong><span>${escapeHtml(event.detail || event.location || '')}</span></div></div>`).join('') : '<div class="empty-state"><strong>当前窗口没有领域事件</strong></div>';
  }

  function syncReplayControls() {
    if (!$('timelineRange')) return;
    const player = state.replayPlayer;
    const availableStep = Number(player?.availableStep || $('timelineRange').max || 0);
    const currentStep = Number(player?.pendingStep ?? player?.currentStep ?? $('timelineRange').value ?? 0);
    const ready = Boolean(state.replayReady && player && availableStep > 0);
    const atEnd = ready && currentStep >= availableStep;
    $('timelineRange').disabled = !ready;
    $('timelinePrev').disabled = !ready || currentStep <= 1;
    $('timelineNext').disabled = !ready || atEnd;
    $('timelinePlay').disabled = !ready;
    $('timelinePlay').textContent = state.replayPlaying ? 'Ⅱ' : atEnd ? '↻' : '▶';
    $('timelinePlay').ariaLabel = state.replayPlaying ? '暂停' : atEnd ? '重新播放' : '播放';
    $('timelinePlay').title = $('timelinePlay').ariaLabel;
  }

  function teardownReplay() {
    state.replayAbortController?.abort();
    state.replayPlayer?.destroy();
    state.replayAbortController = null;
    state.replayPlayer = null;
    state.replayRunId = null;
    state.replayPlaying = false;
    state.replayReady = false;
    state.replayMarkerFacts.clear();
    state.replayAgentDefinitions = [];
    if ($('replayTimelineMarkers')) $('replayTimelineMarkers').innerHTML = '';
    if ($('replayAgentSelect')) $('replayAgentSelect').innerHTML = '<option value="">选择 Agent</option>';
    if ($('replayCameraMode')) $('replayCameraMode').value = 'free';
    if ($('replayCameraState')) $('replayCameraState').textContent = '自由镜头';
    if ($('replayAgentRoster')) $('replayAgentRoster').innerHTML = '<span>正在读取 Agent…</span>';
    clearReplayInspector();
    syncReplayControls();
  }

  async function ensureReplayPlayer(runId, generation) {
    if (state.replayPlayer && state.replayRunId === runId) {
      await state.replayPlayer.refreshAvailable();
      syncReplayControls();
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
        if (status.state === 'LOADING') state.replayReady = false;
        if (status.state === 'READY') state.replayReady = true;
        state.replayPlaying = status.state === 'PLAYING';
        if (Number.isFinite(status.availableStep)) {
          $('timelineRange').max = status.availableStep;
        }
        syncReplayControls();
      },
      onStep: payload => renderReplayStep(payload, runId, generation),
      onAgent: payload => renderReplayInspector(payload, runId, generation),
      onError: error => {
        if (runId !== state.selectedRunId || generation !== state.resultGeneration) return;
        $('replayStatus').textContent = error.code || '回放资源不可用';
        state.replayReady = false;
        state.replayPlaying = false;
        syncReplayControls();
        console.warn('受控回放事实不可用', error);
      },
    });
    state.replayPlayer = replayPlayer;
    await replayPlayer.loadRun(runId, { signal: replayAbortController.signal });
    if (runId !== state.selectedRunId || generation !== state.resultGeneration || replayAbortController.signal.aborted) return null;
    state.replayAgentDefinitions = replayPlayer.manifest.agents;
    const composedReplay = false;
    const rosterTitle = $('replayAgentRosterTitle');
    const rosterHint = $('replayAgentRosterHint');
    const roster = $('replayAgentRoster');
    if (rosterTitle) rosterTitle.textContent = composedReplay ? '场景参与者' : '所有 Agent';
    if (rosterHint) rosterHint.textContent = composedReplay
      ? '只展示运行快照中的物理角色；点击可跟随轨迹'
      : '点击头像或姓名跟随；再次点击恢复自由镜头';
    if (roster?.setAttribute) roster.setAttribute('aria-label', composedReplay ? '选择回放跟随场景参与者' : '选择回放跟随 Agent');
    const replayRoleLabel = agent => ({ DRIVER: '司机', PEDESTRIAN: '行人' })[agent.role] || agent.role || '';
    $('replayAgentSelect').innerHTML = '<option value="">选择 Agent</option>' + replayPlayer.manifest.agents.map(agent => `<option value="${escapeHtml(agent.agent_key)}">${escapeHtml(`${agent.display_name}${agent.role ? `（${replayRoleLabel(agent)}）` : ''}`)}</option>`).join('');
    const restoredAgentKey = GAReplayPlayer.resolveAgentSelection(
      state.selectedReplayAgentKey,
      state.selectedReplayRevisionId,
      state.currentRun?.revision_id,
      replayPlayer.manifest.agents,
    );
    if (restoredAgentKey) {
      applyReplayAgentSelection(restoredAgentKey);
    } else {
      state.selectedReplayAgentKey = null;
      state.selectedReplayRevisionId = null;
      applyReplayAgentSelection(null);
    }
    $('timelineRange').min = replayPlayer.availableStep ? 1 : 0;
    $('timelineRange').max = replayPlayer.availableStep;
    $('timelineRange').value = replayPlayer.currentStep || (replayPlayer.availableStep ? 1 : 0);
    syncReplayControls();
    return replayPlayer;
  }

  function renderReplayAgentRoster() {
    const definitions = state.replayAgentDefinitions || [];
    const resultsByKey = new Map((state.agentResults || []).map(item => [item.agent_key, item]));
    $('replayCameraState').textContent = state.selectedReplayAgentKey
      ? `跟随 · ${definitions.find(item => item.agent_key === state.selectedReplayAgentKey)?.display_name || state.selectedReplayAgentKey}`
      : '自由镜头';
    $('replayAgentRoster').innerHTML = definitions.length ? definitions.map(agent => {
      const active = agent.agent_key === state.selectedReplayAgentKey;
      const result = resultsByKey.get(agent.agent_key);
      const name = agent.display_name || result?.display_name || agent.agent_key;
      const portrait = result?.portrait_url || '';
      const role = ({ DRIVER: '司机', PEDESTRIAN: '行人' })[agent.role] || agent.role || '';
      const tool = agent.active_tool_instance_key ? ` · ${agent.active_tool_instance_key}` : '';
      const semanticName = role ? `${name}（${role}）` : name;
      return `<button type="button" class="replay-agent-choice${active ? ' active' : ''}" data-replay-agent-key="${escapeHtml(agent.agent_key)}" role="option" aria-label="${escapeHtml(semanticName)}" aria-selected="${String(active)}" title="${escapeHtml(active ? `取消跟随 ${semanticName}` : `跟随 ${semanticName}`)}"><span class="replay-agent-fallback" ${portrait ? 'hidden' : ''}>${escapeHtml(name.slice(0, 1))}</span>${portrait ? `<img src="${escapeHtml(portrait)}" alt="" onerror="this.hidden=true;this.previousElementSibling.hidden=false"/>` : ''}<strong>${escapeHtml(name)}</strong>${role ? `<small>${escapeHtml(role + tool)}</small>` : ''}</button>`;
    }).join('') : '<span>暂无可回放的 Agent</span>';
  }

  function applyReplayAgentSelection(agentKey) {
    const definitions = state.replayAgentDefinitions || [];
    const key = definitions.some(item => item.agent_key === agentKey) ? agentKey : null;
    state.selectedReplayAgentKey = key;
    state.selectedReplayRevisionId = key ? state.currentRun?.revision_id || null : null;
    $('replayAgentSelect').value = key || '';
    $('replayCameraMode').value = key ? 'follow' : 'free';
    state.replayPlayer?.selectAgent(key);
    state.replayPlayer?.followAgent(key);
    if (!key) clearReplayInspector();
    renderReplayAgentRoster();
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
    const conversations = step.conversations;
    const events = $('replayLayerKeyEvents').checked ? step.domain_events : [];
    const facts = [
      ...conversations.map(item => ({ type: '对话', text: (item.messages || []).map(message => `${message.speaker_agent_key}: ${message.content}`).join(' · ') })),
      ...events.map(item => ({ type: item.event_type || '事件', text: JSON.stringify(item.payload || {}) })),
    ];
    $('timelineStreamMeta').textContent = `Step ${step.step_no} · ${facts.length} 条`;
    $('timelineStreamItems').innerHTML = facts.length ? facts.map(item => `<div class="stream-item"><time class="stream-time">${escapeHtml(item.type)}</time><div class="stream-copy"><span>${escapeHtml(item.text)}</span></div></div>`).join('') : '<div class="empty-state"><strong>当前步骤没有可见事件</strong></div>';
    syncReplayControls();
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
    if (!payload.selectedAgentKey) {
      state.selectedReplayAgentKey = null;
      state.selectedReplayRevisionId = null;
      $('replayAgentSelect').value = '';
      $('replayCameraMode').value = 'free';
      state.replayPlayer?.followAgent(null);
      clearReplayInspector();
      renderReplayAgentRoster();
      return;
    }
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
    $('replayCameraMode').value = 'follow';
    state.replayPlayer?.followAgent(key);
    renderReplayAgentRoster();
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

  const OPERATION_LIST_PAGE_SIZE = 5;

  function operationListPager(kind, totalItems, requestedPage) {
    const totalPages = Math.max(1, Math.ceil(totalItems / OPERATION_LIST_PAGE_SIZE));
    const page = Math.min(totalPages, Math.max(1, Number(requestedPage) || 1));
    const pages = paginationPageNumbers(page, totalPages);
    let previous = 0;
    const pageButtons = pages.map(pageNumber => {
      const gap = previous && pageNumber - previous > 1 ? '<span class="operation-page-gap">…</span>' : '';
      previous = pageNumber;
      return `${gap}<button type="button" class="page-button${pageNumber === page ? ' active' : ''}" data-operation-list="${kind}" data-operation-page="${pageNumber}"${pageNumber === page ? ' aria-current="page"' : ''}>${pageNumber}</button>`;
    }).join('');
    const label = { usage: '用途汇总', traces: '调用明细', events: '系统事件', checkpoints: '检查点' }[kind] || '列表';
    return {
      page,
      itemsFrom: (page - 1) * OPERATION_LIST_PAGE_SIZE,
      itemsTo: page * OPERATION_LIST_PAGE_SIZE,
      html: `<nav class="operation-list-pagination" aria-label="${label}分页"><span>第 ${page} / ${totalPages} 页 · 共 ${totalItems} 条</span><div><button type="button" class="page-button" aria-label="上一页" data-operation-list="${kind}" data-operation-page="${page - 1}"${page <= 1 ? ' disabled' : ''}>‹</button>${pageButtons}<button type="button" class="page-button" aria-label="下一页" data-operation-list="${kind}" data-operation-page="${page + 1}"${page >= totalPages ? ' disabled' : ''}>›</button></div></nav>`,
    };
  }

  function renderModelUsage() {
    const pagination = operationListPager('usage', state.modelUsageItems.length, state.modelUsagePage);
    state.modelUsagePage = pagination.page;
    const rows = state.modelUsageItems.slice(pagination.itemsFrom, pagination.itemsTo);
    const header = '<div class="usage-row head"><span>用途</span><span>逻辑 / 物理</span><span>最大延迟</span><span>重试</span></div>';
    $('modelUsageRows').innerHTML = header + (rows.length
      ? rows.map(item => `<div class="usage-row"><strong>${escapeHtml(item.purpose)}</strong><code title="逻辑调用 / 物理请求">${item.logical_calls} / ${item.physical_attempts}</code><span>${item.max_latency_ms} ms</span><span>${item.retries}</span></div>`).join('')
      : '<div class="diagnostic-list-empty">暂无用途汇总</div>');
    $('modelUsagePagination').innerHTML = pagination.html;
  }

  function renderOperations(operations) {
    state.modelUsageItems = operations.model_usage || [];
    renderModelUsage();
    const activeJobs = (operations.artifact_jobs || []).filter(item => item.status !== 'SUCCEEDED');
    const jobRows = activeJobs.map(item => `<div class="artifact-result"><span class="artifact-result-icon">◌</span><div><strong>${escapeHtml(item.type)}</strong><span>${escapeHtml(item.status)} · ${Math.round((item.progress || 0) * 100)}%${item.error_summary ? ` · ${escapeHtml(item.error_summary)}` : ''}</span></div><span class="chip ${item.status === 'FAILED' ? 'amber' : 'teal'}">${escapeHtml(item.status)}</span></div>`).join('');
    const artifactRows = operations.artifacts.map(item => `<div class="artifact-result"><span class="artifact-result-icon">▣</span><div><strong>${escapeHtml(item.logical_name)}</strong><span>${Math.ceil(item.size_bytes / 1024)} KB · ${escapeHtml(item.type)} · ${escapeHtml(item.generator_version)} · ${escapeHtml(item.sha256.slice(0, 12))}…</span></div><a class="artifact-action" href="/api/v1/runs/${state.selectedRunId}/artifacts/${item.artifact_id}/download">下载</a></div>`).join('');
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
      ? rows.map(item => `${item.timestamp ? `[${formatLogTime(item.timestamp)}] ` : ''}${item.level || 'INFO'} ${item.message || ''}`).join('\n')
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
    if (runId !== state.selectedRunId) return;
    const generation = ++state.logGeneration;
    closeLogStream();
    state.selectedAttemptId = attemptId;
    state.logRunId = runId;
    state.logAttemptId = attemptId;
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
    const attemptTimeMarkup = typeof systemTimeMarkup === 'function'
      ? systemTimeMarkup
      : value => escapeHtml(typeof formatTime === 'function' ? formatTime(value) : String(value ?? ''));
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
        <div><strong>${escapeHtml(item.stop_reason || item.status)}</strong><span>Step ${item.start_step} → ${item.end_step ?? '运行中'} · 墙钟 ${attemptTimeMarkup(item.started_at)}${item.error_message ? ` · ${escapeHtml(item.error_message)}` : ''}</span></div>
        <span class="chip ${item.status === 'ENDED' ? 'teal' : 'amber'}">${item.log.available ? `${Math.ceil(item.log.size_bytes / 1024)} KB` : '无日志'}</span>
      </div>`).join('') : '<div class="empty-state"><strong>尚未创建执行尝试</strong></div>';
    return selected;
  }

  function renderModelTraces() {
    const pagination = typeof operationListPager === 'function'
      ? operationListPager('traces', state.traceItems.length, state.tracePage)
      : { page: 1, itemsFrom: 0, itemsTo: state.traceItems.length, html: '' };
    state.tracePage = pagination.page;
    const items = state.traceItems.slice(pagination.itemsFrom, pagination.itemsTo);
    const header = '<div class="trace-row head"><span>状态</span><span>物理请求用途 / 模型</span><span>延迟</span><span>重试</span><span>序号</span></div>';
    $('modelTraceRows').innerHTML = header + (items.length ? items.map(item => {
      const statusClass = item.status === 'RUNNING' ? 'blue'
        : ['SUCCEEDED', 'FALLBACK'].includes(item.status) ? 'teal' : 'amber';
      const latency = item.status === 'RUNNING' ? '进行中' : `${item.latency_ms ?? '—'} ms`;
      return `<button type="button" class="trace-row" data-trace-id="${item.trace_id}"><span class="chip ${statusClass}">${escapeHtml(item.status || item.event_type)}</span><strong>${escapeHtml(item.purpose || 'unknown')}<br><code>${escapeHtml(item.resolved_model || item.model || '—')}</code></strong><span>${latency}</span><span>${item.retry ? `#${item.attempt_no}` : '—'}</span><code>${item.event_seq}</code></button>`;
    }).join('') : '<div class="diagnostic-list-empty">该 Attempt 尚无模型调用明细</div>');
    const paginationHost = $('modelTracePagination');
    if (paginationHost) paginationHost.innerHTML = pagination.html;
    $('loadMoreTraces').hidden = state.traceEof;
  }

  async function loadModelTraces(runId, attemptId, signal, { append = false, factsGeneration = null } = {}) {
    if (factsGeneration !== null && factsGeneration !== state.operationFactsGeneration) return;
    if (!attemptId) {
      state.traceItems = [];
      state.tracePage = 1;
      state.traceCursor = null;
      state.traceEof = true;
      renderModelTraces();
      return;
    }
    if (!append) {
      state.traceItems = [];
      state.tracePage = 1;
      state.traceCursor = 0;
      state.traceEof = false;
      $('modelTraceDetail').hidden = true;
      $('tracePayloadMore').hidden = true;
      state.traceDetailState = null;
    }
    const purpose = $('tracePurposeFilter').value.trim();
    const suffix = purpose ? `&purpose=${encodeURIComponent(purpose)}` : '';
    const page = await api(`/runs/${runId}/model-traces?attempt_id=${encodeURIComponent(attemptId)}&event_type=PHYSICAL&cursor=${state.traceCursor ?? 0}&limit=200${suffix}`, { signal });
    if ((factsGeneration !== null && factsGeneration !== state.operationFactsGeneration)
      || runId !== state.selectedRunId || signal.aborted
      || (attemptId !== state.selectedAttemptId && attemptId !== state.selectedTraceAttemptId)) return;
    // PHYSICAL_START and PHYSICAL_ATTEMPT share call_id + attempt_no. Keep one
    // live row and replace it with the later completion event when it arrives.
    const identity = item => item.call_id
      ? `${item.call_id}:${item.attempt_no ?? 0}`
      : item.trace_id;
    const merged = new Map(state.traceItems.map(item => [identity(item), item]));
    page.items.forEach(item => {
      const key = identity(item);
      const current = merged.get(key);
      if (!current || Number(item.event_seq || 0) >= Number(current.event_seq || 0)) {
        merged.set(key, item);
      }
    });
    state.traceItems = [...merged.values()].sort(
      (left, right) => Number(left.event_seq || 0) - Number(right.event_seq || 0)
    );
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

  function stopModelTracePolling() {
    if (state.tracePollTimer) clearTimeout(state.tracePollTimer);
    state.tracePollTimer = null;
    state.tracePollBusy = false;
    state.tracePollTerminalRunId = null;
  }

  function syncModelTracePolling(runId, resultGeneration) {
    if (runId !== state.selectedRunId || resultGeneration !== state.resultGeneration) return;
    const active = ['STARTING', 'RUNNING', 'PAUSE_REQUESTED', 'CANCEL_REQUESTED']
      .includes(state.currentRun?.status);
    if (active) state.tracePollTerminalRunId = null;
    if ((!active && state.tracePollTerminalRunId === runId)
      || state.tracePollTimer || state.tracePollBusy) return;
    state.tracePollTimer = setTimeout(async () => {
      state.tracePollTimer = null;
      if (runId !== state.selectedRunId || resultGeneration !== state.resultGeneration) return;
      const signal = state.operationsAbortController?.signal;
      const attemptId = $('traceAttemptSelect').value || state.selectedTraceAttemptId;
      if (!signal || signal.aborted || !attemptId) {
        syncModelTracePolling(runId, resultGeneration);
        return;
      }
      state.tracePollBusy = true;
      try {
        await loadModelTraces(runId, attemptId, signal, { append: true });
      } catch (error) {
        if (error.name !== 'AbortError') console.warn('模型调用实时刷新失败', error);
      } finally {
        state.tracePollBusy = false;
      }
      if (runId !== state.selectedRunId || resultGeneration !== state.resultGeneration) return;
      const stillActive = ['STARTING', 'RUNNING', 'PAUSE_REQUESTED', 'CANCEL_REQUESTED']
        .includes(state.currentRun?.status);
      if (!stillActive) {
        // The final tail read replaces completed starts. A remaining start was
        // interrupted with its Worker and is no longer an active model call.
        state.traceItems = state.traceItems.map(item => item.status === 'RUNNING'
          ? { ...item, status: 'ABORTED' } : item);
        state.tracePollTerminalRunId = runId;
        renderModelTraces();
        return;
      }
      state.tracePollTimer = setTimeout(
        () => {
          state.tracePollTimer = null;
          syncModelTracePolling(runId, resultGeneration);
        },
        1000,
      );
    }, active ? 750 : 0);
  }

  function renderSystemEvents(items) {
    const merged = new Map(state.operationEvents.map(item => [item.id, item]));
    items.forEach(item => merged.set(item.id, item));
    state.operationEvents = [...merged.values()].sort((left, right) => left.id - right.id);
    const query = $('eventSearch').value.trim().toLowerCase();
    const filtered = state.operationEvents.filter(item => !query || `${item.event_type} ${JSON.stringify(item.payload)}`.toLowerCase().includes(query));
    const pagination = typeof operationListPager === 'function'
      ? operationListPager('events', filtered.length, state.eventPage)
      : { page: 1, itemsFrom: 0, itemsTo: filtered.length, html: '' };
    state.eventPage = pagination.page;
    const visible = filtered.slice(pagination.itemsFrom, pagination.itemsTo);
    const header = '<div class="event-row head"><span>墙钟时间</span><span>事件</span><span>事实</span></div>';
    const timeFormatter = typeof formatSystemTime === 'function' ? formatSystemTime : formatTime;
    const zoneLabel = typeof userTimeZone === 'string' ? userTimeZone : '';
    $('systemEventRows').innerHTML = header + (visible.length ? visible.map(item => {
      const parsedInstant = typeof parseApiInstant === 'function'
        ? parseApiInstant(item.created_at)
        : Date.parse(item.created_at);
      const timestamp = new Date(parsedInstant);
      const timestampTitle = Number.isNaN(timestamp.getTime()) ? String(item.created_at || '') : timestamp.toISOString();
      return `<div class="event-row"><time title="${escapeHtml(timestampTitle)}">${timeFormatter(item.created_at)} ${escapeHtml(zoneLabel)}</time><strong>${escapeHtml(item.event_type)}</strong><code>${escapeHtml(JSON.stringify(item.payload || {}))}</code></div>`;
    }).join('') : '<div class="diagnostic-list-empty">暂无匹配事件</div>');
    const paginationHost = $('systemEventPagination');
    if (paginationHost) paginationHost.innerHTML = pagination.html;
  }

  async function loadSystemEvents(runId, signal, { append = false, factsGeneration = null } = {}) {
    if (factsGeneration !== null && factsGeneration !== state.operationFactsGeneration) return;
    if (!append) {
      state.operationEvents = [];
      state.eventCursor = 0;
      state.eventPage = 1;
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
    state.checkpointItems = document.items || [];
    const pagination = operationListPager('checkpoints', state.checkpointItems.length, state.checkpointPage);
    state.checkpointPage = pagination.page;
    const items = state.checkpointItems.slice(pagination.itemsFrom, pagination.itemsTo);
    const header = '<div class="checkpoint-row head"><span>Step</span><span>状态</span><span>Attempt</span><span>Hash / 虚拟时间</span><span>大小</span><span>校验 / 恢复</span></div>';
    $('checkpointRows').innerHTML = header + (items.length ? items.map(item => `<button class="checkpoint-row" type="button" data-checkpoint-step="${item.step_no}"><code>${item.step_no}</code><span class="chip ${item.validated ? 'teal' : 'amber'}">${escapeHtml(item.status)}</span><code>${escapeHtml((item.attempt_id || '—').slice(0, 8))}</code><span><code>${escapeHtml((item.bundle_sha256 || '—').slice(0, 12))}</code><br>${formatTime(item.virtual_time)}</span><span>${Math.ceil(item.size_bytes / 1024)} KB · ${item.file_count} 文件</span><span>${item.resumable ? '<strong>可恢复</strong>' : escapeHtml(item.validation?.reason || item.validation?.code || '—')}</span></button>`).join('') : '<div class="diagnostic-list-empty">当前 Run 尚无检查点</div>');
    $('checkpointPagination').innerHTML = pagination.html;
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
    // 检查点和 Attempt 属于同一操作快照；任一选择变化都会使本次并行结果失效。
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
    const selectedAttempt = renderAttempts(attempts);
    const selectedMeta = attempts.items.find(item => item.attempt_id === selectedAttempt);
    if (selectedAttempt && selectedMeta?.log.available
      && (state.logRunId !== runId || state.logAttemptId !== selectedAttempt)) {
      selectAttemptLog(runId, selectedAttempt).catch(reportError);
    }
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
    const selectedMeta = attempts.items.find(item => item.attempt_id === selectedAttempt);
    const logRequest = selectedAttempt && selectedMeta?.log.available
      ? selectAttemptLog(runId, selectedAttempt)
      : Promise.resolve().then(() => { $('logViewport').textContent = '该 Attempt 尚未产生可读日志。'; });
    await Promise.all([
      logRequest,
      loadSystemEvents(runId, controller.signal, { factsGeneration }),
      loadModelTraces(runId, selectedAttempt, controller.signal, { factsGeneration }),
    ]);
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

  async function saveDraftUnlocked({ silent = false } = {}) {
    if (!state.draft) return;
    const requestedName = state.experiment.name;
    const definition = structuredClone(state.draft.definition);
    definition.experiment.name = requestedName;
    definition.experiment.timezone = $('timezone').value;
    definition.simulation.start_time = simulationStartTime($('startTime').value, $('timezone').value);
    definition.simulation.stride_minutes = Number($('stride').value);
    definition.simulation.max_steps = Number($('maxSteps').value);
    definition.simulation.random_seed = Number($('seed').value);
    definition.simulation.log_level = 'INFO';
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

    definition.results.agent_step_projection_interval_steps = Number($('projectionInterval').value);
    definition.results.capture_model_payloads = $('capturePayloads').classList.contains('on');
    document.querySelectorAll('#agentRows .agent-row').forEach(row => {
      const agent = definition.agents.find(item => item.agent_key === row.dataset.agentKey);
      if (agent) agent.enabled = row.querySelector('.agent-check').checked;
    });
    const saved = await api(`/experiments/${state.selectedExperimentId}/draft`, {
      method: 'PUT', body: JSON.stringify({ lock_version: state.draft.lock_version, data: definition }),
    });
    await acceptSavedDraft(saved);
    if (!silent) showToast('草稿已保存到当前实验，不影响其他实验。', '保存成功');
    return saved;
  }

  function saveDraft(options = {}) {
    return enqueueDraftMutation(() => saveDraftUnlocked(options));
  }

  async function testModelConnection(purpose) {
    await saveDraft({ silent: true });
    const badge = $(`${purpose}ConnectionStatus`);
    badge.textContent = '检测中';
    badge.className = 'connection-status checking';
    try {
      const result = await api(`/experiments/${state.selectedExperimentId}/draft/models/${purpose}/test`, {
        method: 'POST', body: JSON.stringify({ lock_version: state.draft.lock_version }), transportRetries: 1,
      });
      state.draft = await api(`/experiments/${state.selectedExperimentId}/draft`);
      fillDraft(state.draft.definition);
      if (purpose === 'chat') {
        const contextWindow = result.service?.context_window;
        $('chatServiceStatus').textContent = contextWindow
          ? `服务上下文窗口 ${Number(contextWindow).toLocaleString('zh-CN')} tokens · 本次真实检测`
          : '服务未返回上下文窗口能力';
      }
      scheduleGlobalReconcile({ full: true });
      showToast(`${result.resolved_model} · ${result.latency_ms} ms`, purpose === 'chat' ? '聊天模型可用' : 'Embedding 可用');
      return result;
    } finally {
      await refreshModelStatus().catch(() => {});
      await refreshValidation().catch(() => {});
    }
  }

  async function createExperiment() {
    const brainRevisionId = $('newExperimentBrain').value;
    const brainSkill = $('newExperimentBrain').selectedOptions[0]?.dataset.skillName;
    if (!brainRevisionId || !brainSkill) throw new Error('新仿真必须显式选择一个 Brain Skill Revision');
    const mapRevisionId = $('newExperimentMap').value;
    if (!mapRevisionId) throw new Error('新仿真必须显式选择一个已发布的用户地图');
    const created = await api('/experiments', {
      method: 'POST',
      body: JSON.stringify({
        name: $('newExperimentName').value.trim(),
        goal: $('newExperimentGoal').value.trim(),
        owner: $('newExperimentOwner').value.trim(),
        tags: $('newExperimentTag').value.split(/[,，]/).map(item => item.trim()).filter(Boolean),
        brain_skill: brainSkill,
        brain_revision_id: brainRevisionId,
        map_revision_id: mapRevisionId,
        crowd_revision_ids: window.CrowdWorkspace?.selectedCreateRevisionIds?.() || [],
      }),
    });
    closeModal('createModal', { restoreFocus: false });
    await loadExperiments();
    await openExperiment(created.id);
    showToast('独立实验草稿已创建。', '实验已创建');
  }

  async function duplicateExperiment(experimentId) {
    const created = await api(`/experiments/${experimentId}/duplicate`, {
      method: 'POST', body: JSON.stringify({}),
    });
    await loadExperiments();
    await openExperiment(created.id);
    showToast('来源定义已深复制为新的独立实验草稿。', '实验已复制');
  }

  const splitSpatialPath = value => String(value || '').split(/\s*(?:>|＞|\/)\s*/).map(item => item.trim()).filter(Boolean);
  const splitSpatialObjects = value => String(value || '').split(/[，,\n]/).map(item => item.trim()).filter(Boolean);

  function displaySpatialPurpose(purpose) {
    if (purpose === 'initial_location') return '初始位置';
    if (purpose === 'living_area') return '居住地';
    if (purpose === 'sleeping') return '睡觉';
    return purpose;
  }

  function savedSpatialPurpose(purpose) {
    if (purpose === '初始位置') return 'initial_location';
    if (purpose === '居住地') return 'living_area';
    if (purpose === '睡觉') return 'sleeping';
    return purpose;
  }

  function flattenSpatialTree(tree) {
    const rows = [];
    const visit = (node, path) => {
      if (Array.isArray(node)) {
        rows.push({ path, objects: node.map(item => String(item)) });
        return;
      }
      if (node && typeof node === 'object') {
        const entries = Object.entries(node);
        if (!entries.length && path.length) rows.push({ path, objects: [] });
        entries.forEach(([key, value]) => visit(value, [...path, key]));
        return;
      }
      if (path.length) rows.push({ path, objects: node == null ? [] : [String(node)] });
    };
    visit(tree || {}, []);
    return rows;
  }

  function agentAddressRowMarkup(purpose = '', path = []) {
    return `<div class="spatial-table-row"><input class="control agent-address-purpose" value="${escapeHtml(displaySpatialPurpose(purpose))}" placeholder="例如：居住地" aria-label="地址用途" /><input class="control agent-address-path" value="${escapeHtml(path.join(' > '))}" placeholder="例如：the Ville > 乔治的公寓 > 主人房" aria-label="位置层级" /><button class="spatial-row-remove" type="button" aria-label="删除这条地址">×</button></div>`;
  }

  function agentSpaceRowMarkup(path = [], objects = []) {
    return `<div class="spatial-table-row"><input class="control agent-space-path" value="${escapeHtml(path.join(' > '))}" placeholder="例如：the Ville > 乔治的公寓 > 主人房" aria-label="空间层级" /><input class="control agent-space-objects" value="${escapeHtml(objects.join('，'))}" placeholder="例如：床，书桌，冰箱" aria-label="可交互物件" /><button class="spatial-row-remove" type="button" aria-label="删除这条空间">×</button></div>`;
  }

  function updateSpatialEditorEmptyStates() {
    [['agentAddressRows', '还没有常用地址，点击“添加地址”开始填写。'], ['agentSpaceRows', '还没有可用空间，点击“添加空间”开始填写。']].forEach(([id, message]) => {
      const host = $(id);
      const empty = host.querySelector('.spatial-table-empty');
      if (host.querySelector('.spatial-table-row')) empty?.remove();
      else if (!empty) host.insertAdjacentHTML('beforeend', `<div class="spatial-table-empty">${message}</div>`);
    });
  }

  function renderSpatialEditor(spatial = {}) {
    const addressRows = Object.entries(spatial.address || {}).map(([purpose, path]) => (
      agentAddressRowMarkup(purpose, Array.isArray(path) ? path : [String(path)])
    ));
    const spaceRows = flattenSpatialTree(spatial.tree || {}).map(row => agentSpaceRowMarkup(row.path, row.objects));
    $('agentAddressRows').innerHTML = addressRows.join('');
    $('agentSpaceRows').innerHTML = spaceRows.join('');
    updateSpatialEditorEmptyStates();
    syncAgentInitialLocationPreview();
  }

  function agentCoordTileAddress() {
    if (state.agentEditorContext?.ownerType !== 'experiment') return null;
    const x = Number($('agentEditX').value);
    const y = Number($('agentEditY').value);
    const tiles = state.draft?.definition?.world?.definition?.tiles || [];
    const tile = tiles.find(item => Number(item?.coord?.[0]) === x && Number(item?.coord?.[1]) === y);
    return Array.isArray(tile?.address) ? tile.address.map(String).filter(Boolean) : null;
  }

  function syncAgentInitialLocationPreview() {
    const host = $('agentInitialLocationResolved');
    if (!host) return;
    const address = agentCoordTileAddress();
    host.textContent = address?.length
      ? `当前坐标的地图语义：${address.join(' > ')}`
      : state.agentEditorContext?.ownerType === 'experiment'
        ? '当前坐标没有可解析的地图语义'
        : '公共 Agent 加入实验后校验坐标与初始位置';
    $('useAgentInitialLocation').hidden = !address?.length || state.agentEditorContext?.ownerType !== 'experiment';
  }

  function applyResolvedInitialLocation() {
    const address = agentCoordTileAddress();
    if (!address?.length) throw new Error('当前坐标没有可解析的地图语义');
    const rows = [...document.querySelectorAll('#agentAddressRows .spatial-table-row')];
    let row = rows.find(item => savedSpatialPurpose(item.querySelector('.agent-address-purpose').value.trim()) === 'initial_location');
    if (!row) {
      $('agentAddressRows').querySelector('.spatial-table-empty')?.remove();
      $('agentAddressRows').insertAdjacentHTML('afterbegin', agentAddressRowMarkup('initial_location', address));
      row = $('agentAddressRows').firstElementChild;
    } else {
      row.querySelector('.agent-address-path').value = address.join(' > ');
    }
    showToast('已把坐标对应的地图语义填入“初始位置”。', '初始位置已同步');
  }

  function validateInitialLocationAgainstCoord(spatial) {
    const declared = spatial.address?.initial_location || spatial.address?.['初始位置'];
    const actual = agentCoordTileAddress();
    if (!declared || !actual) return;
    const roots = new Set([
      state.draft?.definition?.world?.world_name,
      state.draft?.definition?.world?.definition?.world,
    ].filter(Boolean));
    const normalize = path => {
      const value = [...path];
      if (roots.has(value[0])) value.shift();
      return JSON.stringify(value);
    };
    if (normalize(declared) !== normalize(actual)) {
      throw new Error(`初始位置“${declared.join(' > ')}”与坐标指向的“${actual.join(' > ')}”不一致`);
    }
  }

  function readSpatialEditor() {
    const address = {};
    document.querySelectorAll('#agentAddressRows .spatial-table-row').forEach((row, index) => {
      const displayedPurpose = row.querySelector('.agent-address-purpose').value.trim();
      const purpose = savedSpatialPurpose(displayedPurpose);
      const path = splitSpatialPath(row.querySelector('.agent-address-path').value);
      if (!purpose || !path.length) throw new Error(`第 ${index + 1} 条常用地址需要填写用途和完整位置`);
      if (Object.prototype.hasOwnProperty.call(address, purpose)) throw new Error(`常用地址用途“${displayedPurpose}”重复了`);
      address[purpose] = path;
    });

    const tree = {};
    const seenPaths = new Set();
    document.querySelectorAll('#agentSpaceRows .spatial-table-row').forEach((row, index) => {
      const path = splitSpatialPath(row.querySelector('.agent-space-path').value);
      const objects = splitSpatialObjects(row.querySelector('.agent-space-objects').value);
      if (!path.length) throw new Error(`第 ${index + 1} 条可用空间需要填写空间层级`);
      const pathKey = JSON.stringify(path);
      if (seenPaths.has(pathKey)) throw new Error(`空间“${path.join(' > ')}”重复了`);
      seenPaths.add(pathKey);
      let branch = tree;
      path.forEach((segment, segmentIndex) => {
        const isLeaf = segmentIndex === path.length - 1;
        if (isLeaf) {
          if (Object.prototype.hasOwnProperty.call(branch, segment)) throw new Error(`空间层级“${path.join(' > ')}”与其他行冲突`);
          branch[segment] = objects;
        } else {
          if (Array.isArray(branch[segment])) throw new Error(`空间层级“${path.slice(0, segmentIndex + 1).join(' > ')}”不能同时作为地点和物件列表`);
          branch[segment] ??= {};
          branch = branch[segment];
        }
      });
    });
    return { address, tree };
  }

  function releaseAgentImageObjectUrls() {
    Object.values(state.agentImageObjectUrls).forEach(url => { if (url) URL.revokeObjectURL(url); });
    state.agentImageObjectUrls = { portrait: null, sprite: null };
  }

  function setAgentImagePreview(kind, url, status, { staged = false } = {}) {
    const prefix = kind === 'portrait' ? 'Portrait' : 'Sprite';
    const image = $(`agent${prefix}Preview`);
    const empty = $(`agent${prefix}Empty`);
    const card = image.closest('.agent-image-card');
    card.classList.toggle('is-staged', staged);
    $(`agent${prefix}Status`).textContent = status;
    image.onerror = () => {
      image.hidden = true;
      empty.hidden = false;
      if (!staged) $(`agent${prefix}Status`).textContent = '当前没有可用图片，请重新选择';
    };
    if (url) {
      image.src = url;
      image.hidden = false;
      empty.hidden = true;
    } else {
      image.removeAttribute('src');
      image.hidden = true;
      empty.hidden = false;
    }
  }

  function renderAgentImageEditor(agent, existing) {
    releaseAgentImageObjectUrls();
    state.agentImageFiles = { portrait: null, sprite: null };
    $('agentPortraitFile').value = '';
    $('agentSpriteFile').value = '';
    $('agentEditPortrait').value = agent.portrait_asset || '';
    $('agentEditSprite').value = agent.sprite_asset || '';
    const builtinRoot = existing && agent.name
      ? `/generative_agents/frontend/static/assets/village/agents/${encodeURIComponent(agent.name)}`
      : '';
    const portraitUrl = agent.portrait_asset || (builtinRoot ? `${builtinRoot}/portrait.png` : '');
    const spriteUrl = agent.sprite_asset || (builtinRoot ? `${builtinRoot}/texture.png` : '');
    setAgentImagePreview('portrait', portraitUrl, agent.portrait_asset ? '已保存到数据库' : existing ? '当前使用内置头像' : '请选择头像');
    setAgentImagePreview('sprite', spriteUrl, agent.sprite_asset ? '已保存到数据库' : existing ? '当前使用内置行走图' : '请选择 4×4 行走图');
  }

  async function stageAgentImage(kind, file) {
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) throw new Error('Agent 图片不能超过 2 MB');
    if (file.type !== 'image/png' && !file.name.toLowerCase().endsWith('.png')) throw new Error('Agent 图片必须是 PNG');
    const objectUrl = URL.createObjectURL(file);
    let dimensions;
    try {
      dimensions = await new Promise((resolve, reject) => {
        const probe = new Image();
        probe.onload = () => resolve([probe.naturalWidth, probe.naturalHeight]);
        probe.onerror = () => reject(new Error('无法读取这张 PNG 图片'));
        probe.src = objectUrl;
      });
      const [width, height] = dimensions;
      if (kind === 'portrait' && (width !== height || width < 32)) throw new Error('头像必须是边长至少 32px 的正方形 PNG');
      if (kind === 'sprite' && (width !== 128 || height !== 128)) throw new Error('4×4 行走图必须是 128×128 PNG（每格 32×32）');
      if (state.agentImageObjectUrls[kind]) URL.revokeObjectURL(state.agentImageObjectUrls[kind]);
      state.agentImageObjectUrls[kind] = objectUrl;
      state.agentImageFiles[kind] = file;
      setAgentImagePreview(kind, objectUrl, `${file.name} · ${width}×${height} · 保存时写入数据库`, { staged: true });
    } catch (error) {
      URL.revokeObjectURL(objectUrl);
      throw error;
    }
  }

  async function uploadStagedAgentImages() {
    const staged = state.agentImageFiles;
    if (!staged.portrait && !staged.sprite) {
      return { portrait: $('agentEditPortrait').value || null, sprite: $('agentEditSprite').value || null };
    }
    const form = new FormData();
    if (staged.portrait) form.append('portrait', staged.portrait, staged.portrait.name);
    if (staged.sprite) form.append('sprite', staged.sprite, staged.sprite.name);
    const response = await fetch('/api/v1/agent-images', { method: 'POST', body: form });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new Error(payload?.error?.message || `Agent 图片上传失败（${response.status}）`);
    }
    const uploaded = await response.json();
    const images = {
      portrait: uploaded.portrait?.content_url || $('agentEditPortrait').value || null,
      sprite: uploaded.sprite?.content_url || $('agentEditSprite').value || null,
    };
    for (const kind of ['portrait', 'sprite']) {
      if (!uploaded[kind]?.content_url) continue;
      const prefix = kind === 'portrait' ? 'Portrait' : 'Sprite';
      $(`agentEdit${prefix}`).value = images[kind];
      setAgentImagePreview(kind, images[kind], '已保存到数据库');
      if (state.agentImageObjectUrls[kind]) URL.revokeObjectURL(state.agentImageObjectUrls[kind]);
      state.agentImageObjectUrls[kind] = null;
      state.agentImageFiles[kind] = null;
    }
    return images;
  }

  function setAgentEditorReadOnly(readonly) {
    const modal = $('agentEditorModal');
    modal.classList.toggle('agent-editor-readonly', readonly);
    modal.querySelectorAll('.content-tab-panel input:not([type="hidden"]), .content-tab-panel textarea, .content-tab-panel select').forEach(control => {
      control.disabled = readonly;
    });
    ['chooseAgentPortrait', 'chooseAgentSprite', 'addAgentAddressRow', 'addAgentSpaceRow'].forEach(id => {
      const control = $(id);
      control.disabled = readonly;
      control.hidden = readonly;
    });
    modal.querySelectorAll('.spatial-row-remove').forEach(control => {
      control.disabled = readonly;
      control.hidden = readonly;
    });
    $('agentPortraitFile').disabled = readonly;
    $('agentSpriteFile').disabled = readonly;
    $('saveAgentEditor').hidden = readonly;
    $('cancelAgentEditor').textContent = readonly ? '关闭' : '取消';
  }

  function fillSharedAgentEditor(agent, hasExisting = true) {
    $('agentEditKey').value = agent.agent_key;
    $('agentEditName').value = agent.name;
    $('agentEditAge').value = agent.scratch.age;
    renderAgentImageEditor(agent, hasExisting);
    $('agentEditX').value = (agent.coord || [0, 0])[0];
    $('agentEditY').value = (agent.coord || [0, 0])[1];
    $('agentEditCurrently').value = agent.currently || '';
    $('agentEditInnate').value = agent.scratch.innate || '';
    $('agentEditLearned').value = agent.scratch.learned || '';
    $('agentEditLifestyle').value = agent.scratch.lifestyle || '';
    $('agentEditDailyPlan').value = agent.scratch.daily_plan || '';
    $('agentEditGoals').value = (agent.goals || []).join('\n');
    $('agentEditVisionRadius').value = agent.perception?.vision_radius ?? 8;
    $('agentEditAttentionBandwidth').value = agent.perception?.attention_bandwidth ?? 8;
    renderSpatialEditor(agent.spatial || { address: {}, tree: {} });
    document.querySelector('[data-content-tab="space"]').hidden = false;
    setContentTab('agent-editor', 'identity', { sync: false });
  }

  function openAgentEditor(agentKey = null) {
    if (!state.draft) throw new Error('已发布 Revision 只读，请先创建新修订');
    const existing = agentKey ? state.draft.definition.agents.find(item => item.agent_key === agentKey) : null;
    const used = new Set(state.draft.definition.agents.map(item => item.agent_key));
    let index = state.draft.definition.agents.length + 1;
    while (used.has(`resident-${String(index).padStart(3, '0')}`)) index += 1;
    const agent = existing || {
      agent_key: `resident-${String(index).padStart(3, '0')}`, enabled: true, name: '', portrait_asset: null,
      sprite_asset: null,
      coord: [0, 0], currently: '', scratch: { age: 30, innate: '', learned: '', lifestyle: '', daily_plan: '' },
      spatial: { address: {}, tree: {} },
      perception: { mode: 'box', vision_radius: 8, attention_bandwidth: 8 },
    };
    state.agentEditorContext = { ownerType: 'experiment' };
    state.editingAgentKey = existing?.agent_key || null;
    $('agentEditorTitle').textContent = existing ? `编辑 ${agent.name}` : '新增 Agent';
    $('agentEditorKeyMeta').textContent = `文件键：${agent.agent_key}`;
    $('agentEditorContextHelp').textContent = '保存到当前实验 Draft；文件键用于历史结果关联，创建后不可修改。';
    document.querySelector('[data-content-tab="space"]').hidden = false;
    $('saveAgentEditor').textContent = '保存 Agent';
    $('agentEditKey').value = agent.agent_key;
    $('agentEditName').value = agent.name; $('agentEditAge').value = agent.scratch.age;
    renderAgentImageEditor(agent, Boolean(existing));
    $('agentEditX').value = agent.coord[0]; $('agentEditY').value = agent.coord[1];
    $('agentEditCurrently').value = agent.currently || ''; $('agentEditInnate').value = agent.scratch.innate || '';
    $('agentEditLearned').value = agent.scratch.learned || ''; $('agentEditLifestyle').value = agent.scratch.lifestyle || '';
    $('agentEditDailyPlan').value = agent.scratch.daily_plan || '';
    $('agentEditGoals').value = (agent.goals || []).join('\n');
    $('agentEditVisionRadius').value = agent.perception?.vision_radius ?? 8;
    $('agentEditAttentionBandwidth').value = agent.perception?.attention_bandwidth ?? 8;
    renderSpatialEditor(agent.spatial || { address: {}, tree: {} });
    setAgentEditorReadOnly(false);
    setContentTab('agent-editor', 'identity', { sync: false });
    const agentEditorReturnFocus = document.activeElement;
    const agentEditorInitialFocus = $('agentEditName');
    openModal('agentEditorModal', agentEditorInitialFocus.id, agentEditorReturnFocus);
    requestAnimationFrame(() => agentEditorInitialFocus.focus());
  }

  async function openPublicAgentEditor({ agentDetail = null, agentDraft = null } = {}) {
    const existing = agentDraft?.definition || null;
    const agent = existing || {
      agent_key: `agent-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      enabled: true,
      name: '',
      portrait_asset: null,
      sprite_asset: null,
      model_override: null,
      tags: [],
      goals: [],
      coord: [0, 0],
      currently: '',
      scratch: { age: 30, innate: '', learned: '', lifestyle: '', daily_plan: '' },
      spatial: { address: {}, tree: {} },
      perception: { mode: 'box', vision_radius: 8, attention_bandwidth: 8 },
    };
    state.agentEditorContext = {
      ownerType: 'public',
      agentDetail,
      agentDraft,
      definition: structuredClone(agent),
    };
    state.editingAgentKey = existing ? agent.agent_key : null;
    $('agentEditorTitle').textContent = existing ? `编辑 ${agent.name}` : '新增 Agent';
    $('agentEditorKeyMeta').textContent = `模板键：${agent.agent_key}`;
    $('agentEditorContextHelp').textContent = '保存为公共 Agent 模板；初始位置与空间会随模板 Revision 保存，加入实验后仍可独立调整。';
    fillSharedAgentEditor(agent, Boolean(existing));
    setAgentEditorReadOnly(false);
    $('saveAgentEditor').textContent = '保存并发布 Agent';
    openModal('agentEditorModal', 'agentEditName');
  }

  async function openPublicAgentReadOnly({ agentDetail, agentRevision } = {}) {
    const agent = agentRevision?.definition;
    if (!agent) throw new Error('无法读取 Agent Revision');
    releaseAgentImageObjectUrls();
    state.agentEditorContext = {
      ownerType: 'public-readonly',
      agentDetail,
      agentRevision,
      definition: structuredClone(agent),
    };
    state.editingAgentKey = null;
    $('agentEditorTitle').textContent = `查看 ${agent.name}`;
    $('agentEditorKeyMeta').textContent = `模板键：${agent.agent_key} · Revision ${String(agentRevision.revision_no).padStart(3, '0')}`;
    $('agentEditorContextHelp').textContent = '当前人群锁定的 Agent Revision；以下字段全部只读。';
    fillSharedAgentEditor(agent);
    setAgentEditorReadOnly(true);
    openModal('agentEditorModal', 'closeAgentEditor');
  }

  function closeSharedAgentEditor({ reopenPublicManager = true } = {}) {
    const ownerType = state.agentEditorContext?.ownerType;
    const reopenManager = ownerType === 'public' && reopenPublicManager;
    releaseAgentImageObjectUrls();
    closeModal('agentEditorModal');
    state.agentEditorContext = { ownerType: 'experiment' };
    setAgentEditorReadOnly(false);
    $('saveAgentEditor').textContent = '保存 Agent';
    document.querySelector('[data-content-tab="space"]').hidden = false;
    if (reopenManager) {
      window.CrowdWorkspace?.reopenAgentManager?.().catch(reportError);
    }
  }

  async function saveAgentEditor() {
    if (state.agentEditorContext?.ownerType === 'public-readonly') return;
    if (state.agentEditorContext?.ownerType === 'public') {
      const context = state.agentEditorContext;
      const hasPortrait = state.agentImageFiles.portrait || $('agentEditPortrait').value;
      const hasSprite = state.agentImageFiles.sprite || $('agentEditSprite').value;
      if (!context.agentDraft && (!hasPortrait || !hasSprite)) {
        throw new Error('新增 Agent 需要同时上传头像和 4×4 行走图');
      }
      const spatial = readSpatialEditor();
      if (!spatial.address.living_area && !spatial.address.sleeping && !spatial.address['睡觉']) {
        throw new Error('请在“初始位置与空间”中配置居住地或睡觉地址');
      }
      if (!Object.keys(spatial.tree).length) {
        throw new Error('请在“初始位置与空间”中配置至少一个可用空间');
      }
      const images = await uploadStagedAgentImages();
      const previous = context.definition || {};
      const definition = {
        agent_key: $('agentEditKey').value.trim(),
        enabled: previous.enabled ?? true,
        name: $('agentEditName').value.trim(),
        portrait_asset: images.portrait,
        sprite_asset: images.sprite,
        model_override: previous.model_override || null,
        tags: previous.tags || [],
        goals: $('agentEditGoals').value.split(/\r?\n/).map(item => item.trim()).filter(Boolean),
        coord: [Number($('agentEditX').value), Number($('agentEditY').value)],
        currently: $('agentEditCurrently').value,
        scratch: {
          age: Number($('agentEditAge').value),
          innate: $('agentEditInnate').value,
          learned: $('agentEditLearned').value,
          lifestyle: $('agentEditLifestyle').value,
          daily_plan: $('agentEditDailyPlan').value,
        },
        spatial,
        perception: {
          mode: 'box',
          vision_radius: Number($('agentEditVisionRadius').value),
          attention_bandwidth: Number($('agentEditAttentionBandwidth').value),
        },
      };
      if (!definition.name) throw new Error('请填写 Agent 名称');
      const published = await window.CrowdWorkspace.saveSharedAgent({
        definition,

        agentDetail: context.agentDetail,
        agentDraft: context.agentDraft,
      });
      closeSharedAgentEditor({ reopenPublicManager: false });
      await window.CrowdWorkspace.afterSharedAgentSaved(published, definition.name);
      return;
    }
    if (!state.draft) throw new Error('当前没有可编辑 Draft');
    const key = $('agentEditKey').value.trim();
    const spatial = readSpatialEditor();
    validateInitialLocationAgainstCoord(spatial);
    const hasPortrait = state.agentImageFiles.portrait || $('agentEditPortrait').value;
    const hasSprite = state.agentImageFiles.sprite || $('agentEditSprite').value;
    if (!state.editingAgentKey && (!hasPortrait || !hasSprite)) {
      throw new Error('新增 Agent 需要同时上传头像和 4×4 行走图');
    }
    const images = await uploadStagedAgentImages();
    const previous = state.editingAgentKey ? state.draft.definition.agents.find(item => item.agent_key === state.editingAgentKey) : null;
    const agent = {
      agent_key: key,
      enabled: previous?.enabled ?? true,
      name: $('agentEditName').value.trim(),
      portrait_asset: images.portrait,
      sprite_asset: images.sprite,
      model_override: previous?.model_override || null,
      tags: previous?.tags || [],
      goals: $('agentEditGoals').value.split(/\r?\n/).map(item => item.trim()).filter(Boolean),
      coord: [Number($('agentEditX').value), Number($('agentEditY').value)],
      currently: $('agentEditCurrently').value,
      scratch: {
        age: Number($('agentEditAge').value), innate: $('agentEditInnate').value,
        learned: $('agentEditLearned').value, lifestyle: $('agentEditLifestyle').value,
        daily_plan: $('agentEditDailyPlan').value,
      },
      spatial,
      perception: {
        mode: 'box',
        vision_radius: Number($('agentEditVisionRadius').value),
        attention_bandwidth: Number($('agentEditAttentionBandwidth').value),
      },
    };
    const saved = await api(`/experiments/${state.selectedExperimentId}/draft/agents/${encodeURIComponent(key)}`, {
      method: 'PUT', body: JSON.stringify({ lock_version: state.draft.lock_version, data: agent }),
    });
    await acceptSavedDraft(saved);
    state.modalReturnFocus = state.editingAgentKey
      ? document.querySelector(`#agentRows .agent-row[data-agent-key="${CSS.escape(key)}"] .agent-edit-btn`)
      : $('addAgentBtn');
    releaseAgentImageObjectUrls();
    closeModal('agentEditorModal');
    showToast('角色定义已保存到当前实验 Draft。', 'Agent 已保存');
  }

  window.SharedAgentEditor = { openPublic: openPublicAgentEditor, openReadOnly: openPublicAgentReadOnly };

  function openDeleteSelectedAgents() {
    if (!state.draft) throw new Error('当前没有可编辑 Draft');
    const selected = state.draft.definition.agents.filter(agent => state.selectedAgentKeys.has(agent.agent_key));
    if (!selected.length) throw new Error('请先在列表中勾选要删除的 Agent');
    state.pendingAgentDeleteKeys = selected.map(agent => agent.agent_key);
    $('deleteAgentsSummary').textContent = `将删除 ${selected.length} 个 Agent`;
    $('deleteAgentsPreview').innerHTML = selected.map(agent => `<div class="delete-agent-item"><strong>${escapeHtml(agent.name)}</strong><code>${escapeHtml(agent.agent_key)}</code></div>`).join('');
    $('confirmDeleteAgents').disabled = false;
    openModal('deleteAgentsModal', 'cancelDeleteAgents', $('deleteSelectedAgentsBtn'));
  }

  async function deleteSelectedAgents() {
    if (!state.draft || !state.pendingAgentDeleteKeys.length) return;
    const requestedKeys = [...state.pendingAgentDeleteKeys];
    let saved = state.draft;
    const deletedKeys = [];
    $('confirmDeleteAgents').disabled = true;
    try {
      for (const key of requestedKeys) {
        saved = await api(`/experiments/${state.selectedExperimentId}/draft/agents/${encodeURIComponent(key)}`, {
          method: 'DELETE', body: JSON.stringify({ lock_version: saved.lock_version, data: {} }),
        });
        deletedKeys.push(key);
      }
    } finally {
      if (deletedKeys.length) {
        state.draft = saved; state.definition = saved.definition;
        deletedKeys.forEach(key => state.selectedAgentKeys.delete(key));
        state.pendingAgentDeleteKeys = requestedKeys.filter(key => !deletedKeys.includes(key));
        fillDraft(saved.definition); fillDefinitionOverview(saved.definition, saved);
        clearDirty(); scheduleGlobalReconcile({ full: true });
      }
      $('confirmDeleteAgents').disabled = false;
    }
    state.pendingAgentDeleteKeys = [];
    state.modalReturnFocus = $('addAgentBtn');
    closeModal('deleteAgentsModal');
    showToast(`已从当前实验草稿中移除 ${deletedKeys.length} 个角色。`, 'Agent 已删除');
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
    const body = action === 'cancel'
      ? JSON.stringify({ force: options.force ?? true })
      : undefined;
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
      action === 'pause' ? '会在当前安全步骤完成后暂停。' : action === 'resume' ? '运行已重新进入本机队列。' : '正在立即终止当前执行；未提交的当前 Step 将被丢弃。',
      action === 'pause' ? '暂停请求已提交' : action === 'resume' ? '继续运行' : '取消运行',
    );
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
    const suggestion = error.details?.suggestion || '检查当前配置后重试；如仍失败，请复制诊断信息交给开发人员。';
    const message = `发生了什么：${error.message || String(error)}；影响：当前操作没有完成；如何修复：${suggestion}`;
    const diagnostic = {
      timestamp: new Date().toISOString(),
      page: state.workspacePage,
      path: error.path || window.location.pathname,
      request_id: error.requestId || null,
      service_error_code: error.code || 'CLIENT_ERROR',
      http_status: error.status || null,
      details: error.details || {},
    };
    showToast(message, '操作失败', { level: 'error', diagnostic });
  }

  function currentListViewDocument() {
    return {
      query: state.query,
      status: state.status,
      owner: state.ownerFilter,
      tag: state.tagFilter,
      model: state.modelFilter,
      map_key: state.mapFilter,
      archived: state.archiveFilter,
      sort: state.sort,
      page_size: state.pageSize,
      view: state.listView,
    };
  }

  function applyListViewDocument(document) {
    state.query = document.query || '';
    state.status = document.status || '';
    state.ownerFilter = document.owner || '';
    state.tagFilter = document.tag || '';
    state.modelFilter = document.model || '';
    state.mapFilter = document.map_key || '';
    state.archiveFilter = document.archived || 'active';
    state.sort = document.sort || '-updated_at';
    state.pageSize = Number(document.page_size || 5);
    state.listView = document.view || 'cards';
    state.page = 1;
    $('experimentSearch').value = state.query;
    $('experimentOwnerFilter').value = state.ownerFilter;
    $('experimentTagFilter').value = state.tagFilter;
    $('experimentModelFilter').value = state.modelFilter;
    $('experimentMapFilter').value = state.mapFilter;
    $('experimentArchiveFilter').value = state.archiveFilter;
    $('experimentSort').value = state.sort;
    $('experimentPageSize').value = String(state.pageSize);
    $('toggleExperimentView').setAttribute('aria-pressed', String(state.listView === 'compact'));
    $('toggleExperimentView').textContent = state.listView === 'compact' ? '卡片模式' : '紧凑表格';
    document.querySelectorAll('.filter-tab[data-filter]').forEach(tab => {
      const filter = tab.dataset.filter;
      const status = filter === 'all' ? '' : filter === 'abnormal' ? 'ABNORMAL' : filter.toUpperCase();
      tab.classList.toggle('active', status === state.status);
    });
  }

  async function loadSavedViews() {
    const document = await api('/experiment-saved-views');
    $('savedExperimentViews').innerHTML = '<option value="">已保存视图</option>' + document.items.map(item => `<option value="${escapeHtml(item.share_key)}" data-view-id="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join('');
    $('deleteSavedExperimentView').disabled = true;
  }

  async function deleteExperimentById(experimentId, name) {
    const confirmed = await confirmResourceDeletion({
      type: '实验', name,
      message: '实验草稿、发布 Revision 和配置将被删除。若仍有 Run，请先在“实验结果”中删除 Run。',
    });
    if (!confirmed) return;
    await api(`/experiments/${encodeURIComponent(experimentId)}`, { method: 'DELETE' });
    state.selectedExperimentIds.delete(experimentId);
    if (state.selectedExperimentId === experimentId) {
      resetResultRuntime();
      state.selectedExperimentId = null;
      state.experiment = null;
      state.draft = null;
      state.revision = null;
      state.definition = null;
      goToPage('experiments');
    }
    await loadExperiments();
    showToast(`实验“${name}”已删除。`, '删除完成');
  }

  async function deleteCurrentRun() {
    const run = state.currentRun || state.runHistory.find(item => item.run_id === state.selectedRunId);
    if (!run) throw new Error('当前没有可删除的 Run');
    const confirmed = await confirmResourceDeletion({
      type: 'Run', name: `Run ${run.run_id.slice(0, 12)}`,
      message: '运行事实、检查点、回放与制品记录将被删除；运行目录会移动到本机可恢复回收站。活动 Run 必须先取消。',
    });
    if (!confirmed) return;
    const experimentId = state.selectedExperimentId;
    await api(`/runs/${encodeURIComponent(run.run_id)}`, { method: 'DELETE' });
    resetResultRuntime();
    await openExperiment(String(experimentId), 'results');
    showToast('Run 已删除，关联文件已移入可恢复回收站。', '删除完成');
  }

  async function deleteSelectedSavedView() {
    const select = $('savedExperimentViews');
    const option = select.selectedOptions?.[0];
    const viewId = option?.dataset.viewId;
    if (!viewId) return;
    const name = option.textContent || '已保存视图';
    const confirmed = await confirmResourceDeletion({ type: '已保存视图', name, message: '只删除这份筛选与排序配置，不影响任何实验。' });
    if (!confirmed) return;
    await api(`/experiment-saved-views/${encodeURIComponent(viewId)}`, { method: 'DELETE' });
    await loadSavedViews();
    showToast(`视图“${name}”已删除。`, '删除完成');
  }

  async function compareSelectedExperiments() {
    const ids = [...state.selectedExperimentIds];
    const comparison = await api('/experiments/compare', {
      method: 'POST', body: JSON.stringify({ experiment_ids: ids }),
    });
    state.currentComparison = comparison;
    $('comparisonSummary').innerHTML = `<strong>${comparison.experiments.map(item => escapeHtml(`${item.name} · rev ${item.revision_no}`)).join(' ↔ ')}</strong><span>${comparison.difference_count} 项差异 · ${comparison.same_field_count} 项一致已折叠</span>`;
    $('comparisonGroups').innerHTML = comparison.groups.map(group => `
      <section class="comparison-group" style="--comparison-columns:${comparison.experiments.length}">
        <h3>${escapeHtml(group.key)} · ${group.differences.length} 项差异</h3>
        ${group.differences.map(item => `<div class="comparison-row"><code>${escapeHtml(item.path)}</code>${item.values.map(value => `<code>${escapeHtml(JSON.stringify(value))}</code>`).join('')}</div>`).join('')}
      </section>`).join('') || '<div class="empty-state"><strong>所选版本完全一致</strong></div>';
    $('comparisonGroupName').value = '';
    openModal('compareExperimentsModal', 'closeComparisonDone');
  }

  async function batchArchiveSelected(action) {
    const ids = [...state.selectedExperimentIds];
    const result = await api('/experiments/batch', {
      method: 'POST', body: JSON.stringify({ experiment_ids: ids, action }),
    });
    state.selectedExperimentIds.clear();
    await loadExperiments();
    showToast(`${result.affected} 个实验已${action === 'ARCHIVE' ? '归档' : '恢复'}，运行结果和 Revision 均保留。`, action === 'ARCHIVE' ? '归档完成' : '恢复完成');
  }

  async function applyExperimentOrganization() {
    const action = state.pendingExperimentOrganizeAction;
    const body = { experiment_ids: [...state.selectedExperimentIds], action };
    if (action === 'SET_OWNER') body.owner = $('organizeOwner').value.trim();
    if (action === 'ADD_TAGS') body.tags = $('organizeTags').value.split(/[,，]/).map(item => item.trim()).filter(Boolean);
    const result = await api('/experiments/batch', { method: 'POST', body: JSON.stringify(body) });
    closeModal('experimentOrganizeModal'); state.selectedExperimentIds.clear();
    await loadExperiments();
    showToast(`${result.affected} 个实验已更新，历史 Revision、Run 与产物均保留。`, '批量整理完成');
  }

  function renderOperationHistory() {
    $('operationHistoryList').innerHTML = state.operationHistory.length ? state.operationHistory.map(item => `
      <article class="operation-history-item ${item.level}">
        <header><strong>${escapeHtml(item.title)}</strong><time title="${escapeHtml(item.timestamp)}">${escapeHtml(formatSystemTime(item.timestamp))} ${escapeHtml(userTimeZone)}</time></header>
        <p>${escapeHtml(item.message)}</p>
        ${item.diagnostic ? `<details><summary>技术详情与请求 ID</summary>${escapeHtml(JSON.stringify(item.diagnostic, null, 2))}</details>` : ''}
      </article>`).join('') : '<div class="empty-state"><strong>暂无操作记录</strong></div>';
  }

  function openWorkspacePage(pageName) {
    if (['experiments', 'maps', 'brains', 'crowds', 'skills'].includes(pageName)) {
      requestGlobalNavigation(pageName);
      return;
    }
    if (!state.selectedExperimentId) {
      showToast('请先从实验列表选择一个实验。', '尚未选择实验');
      return;
    }
    if (!['overview', 'results', 'agents', 'models'].includes(pageName)) {
      pageName = 'overview';
    }
    goToPage(pageName);
    if (pageName === 'results') {
      loadRunHistory(state.selectedExperimentId, state.selectedRunId || state.latestRunId).catch(reportError);
    }
  }

  document.querySelectorAll('.nav-item[data-page]').forEach(item => item.addEventListener('click', () => {
    openWorkspacePage(item.dataset.page);
  }));
  $('sidebarToggle').addEventListener('click', () => {
    setSidebarCollapsed(!document.body.classList.contains('sidebar-collapsed'));
  });
  window.addEventListener('map-workspace:toast', event => {
    showToast(event.detail?.message || '', event.detail?.title || '操作成功');
  });
  window.addEventListener('map-workspace:error', event => {
    reportError(event.detail?.error || new Error('地图操作失败'));
  });
  window.addEventListener('map-workspace:modal', event => {
    const { action, id, focusId } = event.detail || {};
    if (action === 'open') openModal(id, focusId || null);
    else if (action === 'close') closeModal(id);
  });
  window.addEventListener('map-workspace:selection', event => {
    state.selectedMapId = event.detail?.mapId || null;
    syncMapEditorTopbar();
    if (state.workspacePage === 'maps') syncWorkspaceUrl();
  });
  window.addEventListener('map-workspace:experiment-draft', event => {
    const { experimentId, draft } = event.detail || {};
    if (!draft || experimentId !== state.selectedExperimentId) return;
    state.draft = draft;
    state.revision = draft;
    state.definition = draft.definition;
    state.runEstimate = null;
    fillDraft(draft.definition);
    fillDefinitionOverview(draft.definition, draft);
    refreshRunEstimateOverview(experimentId, draft.id).catch(reportError);
    clearDirty();
  });
  window.addEventListener('crowd-workspace:toast', event => {
    showToast(event.detail?.message || '', event.detail?.title || '操作成功');
  });
  window.addEventListener('crowd-workspace:error', event => {
    reportError(event.detail?.error || new Error('人群操作失败'));
  });
  window.addEventListener('crowd-workspace:modal', event => {
    const { action, id, focusId } = event.detail || {};
    if (action === 'open') openModal(id, focusId || null);
    else if (action === 'close') closeModal(id);
  });
  window.addEventListener('crowd-workspace:selection', event => {
    state.selectedCrowdId = event.detail?.crowdId || null;
    syncMapEditorTopbar();
    if (state.workspacePage === 'crowds') syncWorkspaceUrl();
  });
  window.addEventListener('crowd-workspace:create-selection', renderWizardStep);
  $('backToHub').addEventListener('click', () => requestGlobalNavigation('experiments'));
  document.querySelectorAll('[data-goto]').forEach(button => button.addEventListener('click', () => {
    openWorkspacePage(button.dataset.goto);
  }));
  document.querySelectorAll('[data-result-tab]').forEach(tab => tab.addEventListener('click', () => {
    setResultTab(tab.dataset.resultTab, { push: true });
  }));
  $('runQualityBanner').addEventListener('click', event => {
    const target = event.target.closest('[data-quality-step]');
    if (!target) return;
    const step = Number(target.dataset.qualityStep);
    if (!Number.isInteger(step) || step < 1) return;
    setResultTab('timeline', { push: true });
    $('timelineRange').value = step;
    updateTimelineStep(step);
    $('timelineRange').scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
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
  document.addEventListener('click', event => {
    const fix = event.target.closest('[data-fix-page]');
    if (fix) {
      event.preventDefault();
      closeModal('publishModal', { restoreFocus: false });
      goToPage(fix.dataset.fixPage);
      const control = fix.dataset.fixControl ? $(fix.dataset.fixControl) : null;
      if (control) requestAnimationFrame(() => { control.scrollIntoView({ behavior: 'smooth', block: 'center' }); control.focus?.(); });
      return;
    }
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
      const output = $(input.dataset.rangeOutput);
      output.value = input.value;
      output.textContent = input.value;
      markDirty();
    });
  });
  $('saveExperimentComposition').addEventListener('click', () => saveExperimentComposition().catch(reportError));

  $('createExperimentBtn').addEventListener('click', async () => {
    state.wizardStep = 1;
    $('newExperimentName').value = '';
    $('newExperimentGoal').value = '';
    $('newExperimentTag').value = '';
    try {
      await Promise.all([
        prepareExperimentBrainChoices(),
        window.MapWorkspace?.prepareExperimentCreate(),
        window.CrowdWorkspace?.prepareExperimentCreate(),
      ]);
    } catch (error) { reportError(error); }
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
  [$('closeResourceDelete'), $('cancelResourceDelete')].forEach(button => button.addEventListener('click', () => settleResourceDeletion(false)));
  $('confirmResourceDelete').addEventListener('click', () => settleResourceDeletion(true));
  [$('closeResumeRun'), $('cancelResumeRun')].forEach(button => button.addEventListener('click', () => closeModal('resumeRunModal')));
  [$('closeLeaveModal'), $('cancelLeave')].forEach(button => button.addEventListener('click', () => closeModal('leaveModal')));
  $('saveAndLeave').addEventListener('click', () => {
    saveDraft().then(() => {
      closeModal('leaveModal');
      goToPage(state.pendingGlobalPage);
    }).catch(reportError);
  });
  $('discardAndLeave').addEventListener('click', () => {
    const destination = state.pendingGlobalPage;
    api(`/experiments/${state.selectedExperimentId}/draft`).then(draft => {
      state.draft = draft;
      state.revision = draft;
      state.definition = draft.definition;
      fillDraft(draft.definition);
      clearDirty();
      closeModal('leaveModal');
      goToPage(destination);
    }).catch(reportError);
  });
  document.querySelectorAll('.modal-backdrop').forEach(backdrop => backdrop.addEventListener('click', event => {
    if (event.target !== backdrop) return;
    if (backdrop.id === 'agentEditorModal' && state.agentEditorContext?.ownerType?.startsWith('public')) {
      closeSharedAgentEditor();
      return;
    }
    closeModal(backdrop.id);
  }));
  $('agentEditorModal').addEventListener('keydown', event => {
    if (event.key === 'Escape' && state.agentEditorContext?.ownerType?.startsWith('public')) {
      event.preventDefault();
      event.stopPropagation();
      closeSharedAgentEditor();
      return;
    }
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
    if (event.target.closest('.experiment-select')) {
      event.stopImmediatePropagation();
      const checkbox = event.target.closest('.experiment-select');
      if (checkbox.checked) state.selectedExperimentIds.add(card.dataset.id);
      else state.selectedExperimentIds.delete(card.dataset.id);
      card.classList.toggle('is-selected', checkbox.checked);
      updateExperimentSelectionControls();
    } else if (event.target.closest('.api-open-results')) {
      event.stopImmediatePropagation();
      openExperiment(card.dataset.id, 'results').catch(reportError);
    } else if (event.target.closest('.api-open-experiment')) {
      event.stopImmediatePropagation();
      openExperiment(card.dataset.id).catch(reportError);
    }
  }, true);

  let contextExperimentId = null;
  let contextExperimentName = '';
  $('experimentList').addEventListener('click', event => {
    const menu = event.target.closest('.experiment-menu');
    if (!menu) return;
    event.stopPropagation();
    const card = menu.closest('.experiment-card');
    contextExperimentId = card?.dataset.id || null;
    contextExperimentName = card?.querySelector('.experiment-link')?.textContent?.trim() || '当前实验';
    const contextMenu = $('experimentContextMenu');
    const archived = menu.closest('.experiment-card')?.dataset.archived === 'true';
    contextMenu.querySelector('[data-context-action="archive"]').hidden = archived;
    contextMenu.querySelector('[data-context-action="restore"]').hidden = !archived;
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
    else if (action === 'archive' || action === 'restore') {
      api(`/experiments/${contextExperimentId}/${action}`, { method: 'POST', body: '{}' })
        .then(() => loadExperiments()).catch(reportError);
    }
    else if (action === 'delete') deleteExperimentById(contextExperimentId, contextExperimentName).catch(reportError);
  }, true);
  document.addEventListener('click', event => {
    if (!event.target.closest('#experimentContextMenu, .experiment-menu')) $('experimentContextMenu').hidden = true;
  });

  document.querySelectorAll('.filter-tab[data-filter]').forEach(tab => tab.addEventListener('click', event => {
    event.stopImmediatePropagation();
    document.querySelectorAll('.filter-tab[data-filter]').forEach(item => item.classList.remove('active'));
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

  const reloadExperimentFilters = () => {
    state.ownerFilter = $('experimentOwnerFilter').value.trim();
    state.tagFilter = $('experimentTagFilter').value.trim();
    state.modelFilter = $('experimentModelFilter').value.trim();
    state.mapFilter = $('experimentMapFilter').value.trim();
    state.archiveFilter = $('experimentArchiveFilter').value;
    state.sort = $('experimentSort').value;
    state.pageSize = Number($('experimentPageSize').value);
    state.page = 1;
    loadExperiments().catch(reportError);
  };
  let experimentFilterTimer;
  [$('experimentOwnerFilter'), $('experimentTagFilter'), $('experimentModelFilter'), $('experimentMapFilter')].forEach(input => input.addEventListener('input', () => {
    clearTimeout(experimentFilterTimer);
    experimentFilterTimer = setTimeout(reloadExperimentFilters, 250);
  }));
  [$('experimentArchiveFilter'), $('experimentSort'), $('experimentPageSize')].forEach(select => select.addEventListener('change', reloadExperimentFilters));
  $('selectVisibleExperiments').addEventListener('click', () => {
    const allSelected = state.visibleExperimentIds.every(id => state.selectedExperimentIds.has(id));
    state.visibleExperimentIds.forEach(id => allSelected ? state.selectedExperimentIds.delete(id) : state.selectedExperimentIds.add(id));
    loadExperiments().catch(reportError);
  });
  $('compareExperimentsBtn').addEventListener('click', () => compareSelectedExperiments().catch(reportError));
  $('archiveSelectedBtn').addEventListener('click', () => batchArchiveSelected('ARCHIVE').catch(reportError));
  $('restoreSelectedBtn').addEventListener('click', () => batchArchiveSelected('RESTORE').catch(reportError));
  $('tagSelectedBtn').addEventListener('click', () => {
    state.pendingExperimentOrganizeAction = 'ADD_TAGS';
    $('experimentOrganizeTitle').textContent = '批量添加标签';
    $('organizeOwnerField').hidden = true; $('organizeTagsField').hidden = false; $('organizeTags').value = '';
    openModal('experimentOrganizeModal', 'organizeTags');
  });
  $('ownerSelectedBtn').addEventListener('click', () => {
    state.pendingExperimentOrganizeAction = 'SET_OWNER';
    $('experimentOrganizeTitle').textContent = '批量转移负责人';
    $('organizeOwnerField').hidden = false; $('organizeTagsField').hidden = true; $('organizeOwner').value = '';
    openModal('experimentOrganizeModal', 'organizeOwner');
  });
  [$('closeExperimentOrganize'), $('cancelExperimentOrganize')].forEach(button => button.addEventListener('click', () => closeModal('experimentOrganizeModal')));
  $('confirmExperimentOrganize').addEventListener('click', () => applyExperimentOrganization().catch(reportError));
  $('toggleExperimentView').addEventListener('click', () => {
    state.listView = state.listView === 'compact' ? 'cards' : 'compact';
    $('toggleExperimentView').textContent = state.listView === 'compact' ? '卡片模式' : '紧凑表格';
    $('toggleExperimentView').setAttribute('aria-pressed', String(state.listView === 'compact'));
    $('experimentList').classList.toggle('compact-view', state.listView === 'compact');
  });
  $('clearExperimentFilters').addEventListener('click', () => {
    applyListViewDocument({});
    state.selectedExperimentIds.clear();
    loadExperiments().catch(reportError);
  });
  $('saveExperimentView').addEventListener('click', () => {
    $('savedViewName').value = '';
    $('savedViewPreview').textContent = JSON.stringify(currentListViewDocument(), null, 2);
    openModal('saveViewModal', 'savedViewName');
  });
  [$('closeSaveView'), $('cancelSaveView')].forEach(button => button.addEventListener('click', () => closeModal('saveViewModal')));
  $('confirmSaveView').addEventListener('click', () => {
    api('/experiment-saved-views', {
      method: 'POST', body: JSON.stringify({ name: $('savedViewName').value.trim(), query: currentListViewDocument() }),
    }).then(async saved => {
      closeModal('saveViewModal');
      await loadSavedViews();
      const shareUrl = new URL(window.location.href);
      shareUrl.search = '';
      shareUrl.searchParams.set('saved_view', saved.share_key);
      await navigator.clipboard?.writeText(shareUrl.toString());
      showToast('视图已保存，分享链接已复制。', '视图已保存');
    }).catch(reportError);
  });
  $('savedExperimentViews').addEventListener('change', event => {
    $('deleteSavedExperimentView').disabled = !event.target.value;
    if (!event.target.value) return;
    api(`/experiment-saved-views/shared/${encodeURIComponent(event.target.value)}`).then(saved => {
      applyListViewDocument(saved.query);
      return loadExperiments();
    }).catch(reportError);
  });
  $('deleteSavedExperimentView').addEventListener('click', () => deleteSelectedSavedView().catch(reportError));
  [$('closeCompareExperiments'), $('closeComparisonDone')].forEach(button => button.addEventListener('click', () => closeModal('compareExperimentsModal')));
  $('saveComparisonGroup').addEventListener('click', () => {
    const name = $('comparisonGroupName').value.trim();
    if (!name) { $('comparisonGroupName').focus(); return; }
    api('/experiment-comparison-groups', {
      method: 'POST', body: JSON.stringify({ name, experiment_ids: [...state.selectedExperimentIds] }),
    }).then(() => showToast('对照组已保存；实验与历史 Revision 不受影响。', '对照组已保存')).catch(reportError);
  });
  $('operationHistoryBtn').addEventListener('click', () => { renderOperationHistory(); openModal('operationHistoryModal', 'closeOperationHistoryDone'); });
  [$('closeOperationHistory'), $('closeOperationHistoryDone')].forEach(button => button.addEventListener('click', () => closeModal('operationHistoryModal')));
  $('copyLatestDiagnostic').addEventListener('click', () => {
    const item = state.operationHistory.find(entry => entry.diagnostic);
    if (!item) { showToast('当前没有可复制的诊断信息。', '暂无诊断'); return; }
    navigator.clipboard?.writeText(JSON.stringify(item.diagnostic, null, 2))
      .then(() => showToast('最近一次诊断已复制，包含请求 ID 与错误码。', '诊断已复制')).catch(reportError);
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
  $('deleteExperimentBtn').addEventListener('click', () => {
    if (state.selectedExperimentId) deleteExperimentById(state.selectedExperimentId, state.currentExperimentName || '当前实验').catch(reportError);
  });
  $('saveExperimentMetadata')?.addEventListener('click', () => {
    saveExperimentMetadata().catch(reportError);
  });
  $('publishBtn').addEventListener('click', event => {
    if (state.draft && !state.workspaceReadonly) {
      event.stopImmediatePropagation();
      openPublishModal().catch(reportError);
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
    if (event.target.classList.contains('agent-select-check')) {
      const row = event.target.closest('.agent-row');
      if (event.target.checked) state.selectedAgentKeys.add(row.dataset.agentKey);
      else state.selectedAgentKeys.delete(row.dataset.agentKey);
      row.classList.toggle('is-selected', event.target.checked);
      updateAgentSelectionControls();
      return;
    }
    if (event.target.classList.contains('agent-check')) {
      const row = event.target.closest('.agent-row');
      row.dataset.enabled = String(event.target.checked);
      const rows = [...document.querySelectorAll('#agentRows .agent-check')];
      const enabled = rows.filter(input => input.checked).length;
      $('statAgentCount').textContent = enabled;
      $('navAgentCount').textContent = enabled;
      markDirty(); filterAgentRows();
    }
  }, true);
  let agentFilterTimer;
  [$('agentSearch'), $('agentLocationFilter'), $('agentModelFilter')].forEach(input => input.addEventListener('input', () => {
    clearTimeout(agentFilterTimer); agentFilterTimer = setTimeout(filterAgentRows, 150);
  }));
  [$('agentEnabledFilter'), $('agentCompletenessFilter')].forEach(select => select.addEventListener('change', filterAgentRows));
  $('selectAllAgentRows').addEventListener('change', event => {
    visibleAgentRows().forEach(row => {
      const checkbox = row.querySelector('.agent-select-check'); checkbox.checked = event.target.checked;
      row.classList.toggle('is-selected', event.target.checked);
      if (event.target.checked) state.selectedAgentKeys.add(row.dataset.agentKey); else state.selectedAgentKeys.delete(row.dataset.agentKey);
    });
    updateAgentSelectionControls();
  });
  $('batchEditAgentsBtn').addEventListener('click', () => {
    state.pendingAgentBatch = null;
    ['batchAgentEnabled', 'batchAgentModel', 'batchAgentX', 'batchAgentY', 'batchAgentGoal', 'batchAgentTags'].forEach(id => { $(id).value = ''; });
    $('batchAgentMeta').textContent = `${state.selectedAgentKeys.size} 个 Agent 已选择；先预览差异，再一次应用。`;
    $('batchAgentPreview').innerHTML = '<span>填写变更后点击“预览差异”。</span>';
    $('applyBatchAgents').disabled = true; $('undoBatchAgents').disabled = !state.lastAgentBatchUndo;
    openModal('batchAgentModal', 'batchAgentEnabled');
  });
  $('deleteSelectedAgentsBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    try { openDeleteSelectedAgents(); } catch (error) { reportError(error); }
  }, true);
  [$('closeDeleteAgents'), $('cancelDeleteAgents')].forEach(button => button.addEventListener('click', () => {
    state.pendingAgentDeleteKeys = [];
    closeModal('deleteAgentsModal');
  }));
  $('confirmDeleteAgents').addEventListener('click', () => deleteSelectedAgents().catch(reportError));
  [$('closeBatchAgent')].forEach(button => button.addEventListener('click', () => closeModal('batchAgentModal')));
  $('previewBatchAgents').addEventListener('click', () => previewAgentBatch().catch(reportError));
  $('applyBatchAgents').addEventListener('click', () => applyAgentBatch().catch(reportError));
  $('undoBatchAgents').addEventListener('click', () => undoAgentBatch().catch(reportError));
  $('exportAgentsBtn').addEventListener('click', () => downloadJson(`${state.experiment?.experiment_key || 'experiment'}-agents.json`, { schema_version: 1, agents: state.draft?.definition?.agents || [] }));
  $('importAgentsBtn').addEventListener('click', () => $('importAgentsFile').click());
  $('importAgentsFile').addEventListener('change', event => {
    const file = event.target.files?.[0]; event.target.value = '';
    if (file) stageAgentImport(file).catch(reportError);
  });
  [$('closeAgentImport'), $('cancelAgentImport')].forEach(button => button.addEventListener('click', () => closeModal('agentImportModal')));
  $('confirmAgentImport').addEventListener('click', () => applyAgentImport().catch(reportError));
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
  $('chooseAgentPortrait').addEventListener('click', () => $('agentPortraitFile').click());
  $('chooseAgentSprite').addEventListener('click', () => $('agentSpriteFile').click());
  $('agentPortraitFile').addEventListener('change', event => {
    const file = event.target.files?.[0]; event.target.value = '';
    if (file) stageAgentImage('portrait', file).catch(reportError);
  });
  $('agentSpriteFile').addEventListener('change', event => {
    const file = event.target.files?.[0]; event.target.value = '';
    if (file) stageAgentImage('sprite', file).catch(reportError);
  });
  $('addAgentAddressRow').addEventListener('click', () => {
    $('agentAddressRows').querySelector('.spatial-table-empty')?.remove();
    $('agentAddressRows').insertAdjacentHTML('beforeend', agentAddressRowMarkup('', []));
    $('agentAddressRows').lastElementChild.querySelector('.agent-address-purpose').focus();
  });
  $('useAgentInitialLocation').addEventListener('click', () => {
    try { applyResolvedInitialLocation(); } catch (error) { reportError(error); }
  });
  [$('agentEditX'), $('agentEditY')].forEach(control => control.addEventListener('input', syncAgentInitialLocationPreview));
  $('addAgentSpaceRow').addEventListener('click', () => {
    $('agentSpaceRows').querySelector('.spatial-table-empty')?.remove();
    $('agentSpaceRows').insertAdjacentHTML('beforeend', agentSpaceRowMarkup([], []));
    $('agentSpaceRows').lastElementChild.querySelector('.agent-space-path').focus();
  });
  [$('agentAddressRows'), $('agentSpaceRows')].forEach(host => host.addEventListener('click', event => {
    const removeButton = event.target.closest('.spatial-row-remove');
    if (!removeButton) return;
    removeButton.closest('.spatial-table-row').remove();
    updateSpatialEditorEmptyStates();
  }));
  $('saveAgentEditor').addEventListener('click', event => { event.stopImmediatePropagation(); saveAgentEditor().catch(reportError); }, true);
  [$('closeAgentEditor'), $('cancelAgentEditor')].forEach(button => button.addEventListener('click', event => {
    event.stopImmediatePropagation();
    if (state.agentEditorContext?.ownerType?.startsWith('public')) closeSharedAgentEditor();
    else { releaseAgentImageObjectUrls(); closeModal('agentEditorModal'); }
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
    const pageButton = event.target.closest('[data-agent-page-kind]');
    if (pageButton) {
      const kind = pageButton.dataset.agentPageKind;
      const targetPage = Math.max(1, Number(pageButton.dataset.agentPage) || 1);
      const pageKey = agentContentPageKey(kind);
      if (state.agentContentPages.get(pageKey) === targetPage) return;
      state.agentContentPages.set(pageKey, targetPage);
      const detail = state.agentDetailCache.get(`${state.selectedRunId}:${state.selectedAgentKey}`);
      if (!detail) return;
      const panel = $('resultAgentDetail');
      const scrollX = window.scrollX;
      const scrollY = window.scrollY;
      panel.innerHTML = `<div class="agent-result-body">${renderAgentDetail(detail)}</div>`;
      panel.querySelector(`[data-agent-page-kind="${CSS.escape(kind)}"][data-agent-page="${targetPage}"]`)?.focus({ preventScroll: true });
      window.scrollTo(scrollX, scrollY);
      return;
    }
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
    if (state.replayPlayer) {
      if (state.replayPlaying) state.replayPlayer.pause();
      state.replayPlayer.seek(Number(event.target.value)).catch(reportError);
    }
    else updateTimelineStep(Number(event.target.value));
  }, true);
  [$('timelinePrev'), $('timelineNext')].forEach(button => button.addEventListener('click', event => {
    event.stopImmediatePropagation();
    const delta = button === $('timelinePrev') ? -1 : 1;
    if (state.replayPlayer) {
      if (state.replayPlaying) state.replayPlayer.pause();
      state.replayPlayer.stepBy(delta).catch(reportError);
    }
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
    else state.replayPlayer.play().catch(reportError);
  }, true);
  $('replaySpeed').addEventListener('change', event => state.replayPlayer?.setSpeed(Number(event.target.value)));
  $('replayAgentSelect').addEventListener('change', event => {
    applyReplayAgentSelection(event.target.value || null);
  });
  $('replayCameraMode').addEventListener('change', event => {
    applyReplayAgentSelection(event.target.value === 'follow' ? $('replayAgentSelect').value || null : null);
  });
  $('replayAgentRoster').addEventListener('click', event => {
    const choice = event.target.closest('[data-replay-agent-key]');
    if (!choice) return;
    const key = choice.dataset.replayAgentKey;
    applyReplayAgentSelection(state.selectedReplayAgentKey === key ? null : key);
  });
  [
    ['replayLayerTrails', 'trails'],
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
    $('statAgentCount').textContent = shouldEnable ? rows.length : 0;
    $('navAgentCount').textContent = shouldEnable ? rows.length : 0;
    event.currentTarget.textContent = shouldEnable ? '取消全选' : '全部启用';
    markDirty();
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
    if (event.target.value && event.target.value !== state.selectedRunId) loadResults(event.target.value).catch(reportError);
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
  [$('modelUsagePagination'), $('modelTracePagination'), $('systemEventPagination'), $('checkpointPagination')].forEach(container => container.addEventListener('click', event => {
    const button = event.target.closest('[data-operation-list]');
    if (!button || button.disabled) return;
    const kind = button.dataset.operationList;
    const page = Math.max(1, Number(button.dataset.operationPage) || 1);
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    if (kind === 'usage') {
      state.modelUsagePage = page;
      renderModelUsage();
    } else if (kind === 'traces') {
      state.tracePage = page;
      renderModelTraces();
    } else if (kind === 'events') {
      state.eventPage = page;
      renderSystemEvents([]);
    } else {
      state.checkpointPage = page;
      renderCheckpoints({ items: state.checkpointItems }, state.checkpointGeneration);
    }
    const targetContainer = {
      usage: $('modelUsagePagination'),
      traces: $('modelTracePagination'),
      events: $('systemEventPagination'),
      checkpoints: $('checkpointPagination'),
    }[kind];
    targetContainer.querySelector(`[data-operation-list="${kind}"][data-operation-page="${page}"]`)?.focus({ preventScroll: true });
    window.scrollTo(scrollX, scrollY);
  }));
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
  $('logTimeZone').addEventListener('change', event => { state.logTimeZoneMode = event.target.value; renderLogViewport(); });
  $('logExportTimezone').addEventListener('click', () => {
    const displayTimeZone = state.logTimeZoneMode === 'UTC' ? 'UTC' : userTimeZone;
    downloadJson(`run-${state.selectedRunId || 'unknown'}-log-${displayTimeZone.replaceAll('/', '-')}.json`, {
      run_id: state.selectedRunId,
      attempt_id: state.selectedAttemptId,
      original_time_standard: 'UTC',
      display_timezone: displayTimeZone,
      records: state.logRecords.map(record => ({ ...record, original_utc: record.timestamp ? new Date(record.timestamp).toISOString() : null, display_time: formatLogTime(record.timestamp) })),
    });
  });
  $('logAutoFollow').addEventListener('change', renderLogViewport);
  $('logPauseScroll').addEventListener('click', () => {
    state.logStreamPaused = !state.logStreamPaused;
    $('logPauseScroll').textContent = state.logStreamPaused ? '继续流' : '暂停流';
    if (state.logStreamPaused) closeLogStream();
    else if (state.selectedRunId && state.selectedAttemptId) {
      startLogStream(state.selectedRunId, state.selectedAttemptId, state.logGeneration);
    }
  });
  $('eventSearch').addEventListener('input', () => {
    state.eventPage = 1;
    renderSystemEvents(state.operationEvents);
  });
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
  $('runPauseResumeBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    controlRun(state.currentRun?.status === 'PAUSED' ? 'resume' : 'pause').catch(reportError);
  }, true);
  $('runCancelBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    controlRun('cancel').catch(reportError);
  }, true);
  $('deleteRunBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    deleteCurrentRun().catch(reportError);
  }, true);
  $('runContinueBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    try { openResumeRunModal(); } catch (error) { reportError(error); }
  }, true);
  $('wizardNext').addEventListener('click', event => {
    event.stopImmediatePropagation();
    if (state.wizardStep === 1 && !$('newExperimentName').value.trim()) {
      showToast('请填写实验名称。', '无法继续');
      $('newExperimentName').focus();
      return;
    }
    if (state.wizardStep === 2 && !$('newExperimentMap').value) {
      showToast('请选择一个已发布的地图。', '无法继续');
      $('newExperimentMap').focus();
      return;
    }
    if (state.wizardStep === 2 && !$('newExperimentBrain').value) {
      showToast('请选择一个 Brain Skill。', '无法继续');
      $('newExperimentBrain').focus();
      return;
    }
    if (state.wizardStep === 2 && !(window.CrowdWorkspace?.selectedCreateRevisionIds?.().length)) {
      showToast('请至少选择一个已发布人群。', '无法继续');
      $('newExperimentCrowds').querySelector('input')?.focus();
      return;
    }
    if (state.wizardStep < 3) {
      state.wizardStep += 1;
      renderWizardStep();
      return;
    }
    createExperiment().catch(reportError);
  }, true);
  $('newExperimentBrain').addEventListener('change', renderWizardStep);
  $('newExperimentMap').addEventListener('change', renderWizardStep);
  $('confirmPublish').addEventListener('click', event => {
    if (!state.draft) return;
    event.stopImmediatePropagation();
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = '正在自动解析模型并启动…';
    publishAndRun().catch(async error => {
      try {
        state.draft = await api(`/experiments/${state.selectedExperimentId}/draft`);
        state.definition = state.draft.definition;
        const report = await refreshValidation();
        if (report && state.runEstimate) renderPublishValidation(report, state.runEstimate);
      } catch (_) {}
      reportError(error);
    }).finally(() => {
      button.disabled = !state.validationReport?.valid || Boolean(state.runEstimate?.high_scale && !document.getElementById('confirmHighScale')?.checked);
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
    const targetPage = requestedView && ['overview', 'results', 'agents', 'models'].includes(requestedView)
      ? requestedView
      : 'overview';
    const requestedTab = params.get('tab');
    const requestedResultTabParam = params.get('result_tab');
    const requestedResultTab = requestedResultTabParam === 'summary' ? 'timeline' : requestedResultTabParam || 'timeline';

    // Apply deep-link state before loading the experiment. Result renderers use
    // these values while creating Agent panels, so a direct URL must never
    // become stuck on a different nested tab.
    if (experimentId && targetPage === 'results') {
      state.resultTab = requestedResultTab;
      if (requestedResultTab === 'agents' && requestedTab) state.selectedAgentContent = requestedTab;
      if (requestedResultTab === 'operations' && requestedTab) state.operationTab = requestedTab;
    } else if (experimentId && requestedTab && Object.hasOwn(state.contentTabs, targetPage)) {
      state.contentTabs[targetPage] = requestedTab;
    }
    Object.entries(state.contentTabs).forEach(([groupName, tabName]) => {
      setContentTab(groupName, tabName, { sync: false });
    });
    setResultTab(state.resultTab, { sync: false });
    setOperationTab(state.operationTab, { sync: false });
    const savedViewKey = params.get('saved_view');
    if (savedViewKey) {
      const savedView = await api(`/experiment-saved-views/shared/${encodeURIComponent(savedViewKey)}`);
      applyListViewDocument(savedView.query);
    }
    await Promise.all([loadSavedViews(), loadExperiments()]);
    if (!experimentId && ['maps', 'brains', 'crowds', 'skills'].includes(requestedView)) {
      if (requestedView === 'maps') state.selectedMapId = params.get('map_id');
      if (requestedView === 'brains') state.selectedBrainId = params.get('brain_id');
      if (requestedView === 'crowds') state.selectedCrowdId = params.get('crowd_id');
      goToPage(requestedView);
      syncWorkspaceUrl();
    } else if (experimentId) {
      await openExperiment(experimentId, targetPage, params.get('run_id'));
      if (targetPage === 'results') {
        setResultTab(requestedResultTab, { sync: false });
        if (requestedResultTab === 'operations' && requestedTab) {
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
  restoreOperationHistory();
  restoreSidebarPreference();
  bootstrapConsole().catch(reportError);
})();
