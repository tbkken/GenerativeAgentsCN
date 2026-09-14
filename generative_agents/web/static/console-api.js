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
    archiveFilter: 'active',
    selectedExperimentIds: new Set(),
    visibleExperimentIds: [],
    selectedAgentKeys: new Set(),
    lastAgentBatchUndo: null,
    pendingAgentBatch: null,
    pendingAgentImport: null,
    pendingAgentDeleteKeys: [],
    agentImageFiles: { portrait: null, sprite: null },
    agentImageObjectUrls: { portrait: null, sprite: null },
    agentSpriteLayout: '4x4',
    duplicateInProgress: false,
    duplicateExperimentId: null,
    duplicateFeedbackGeneration: 0,
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
    experimentListRequest: null,
    experimentListTimer: null,
    experimentListNeedsPolling: false,
    experimentOpenGeneration: 0,
    selectedExperimentGeneration: 0,
    latestSummaryGeneration: 0,
    conversationGeneration: 0,
    memoryGeneration: 0,
    activityGeneration: 0,
    globalRefreshTimer: null,
    globalPollTimer: null,
    pendingActivityExperimentIds: new Set(),
    forceGlobalRefresh: false,
    formDirty: false,
    resultGeneration: 0,
    resultRequestGeneration: 0,
    resultRefreshTimer: null,
    resultPollTimer: null,
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
    selectedReplayExperimentId: null,
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
  // Preserve the established experiment workspace: configuration stays in
  // Overview / Models, while Results remains a read-only Run and replay view.
  const overviewPanel = document.querySelector('#page-overview .overview-panel');
  const simulationParametersPanel = document.querySelector('[data-result-panel="parameters"]');
  const simulationParametersCard = simulationParametersPanel?.querySelector('.configuration-result-card');
  if (overviewPanel && simulationParametersCard) {
    const section = document.createElement('section');
    section.className = 'overview-section';
    section.innerHTML = '<div class="panel-intro"><div><h2>时间与运行参数</h2><p>配置虚拟时间、步数、随机种子、检查点和审计边界。</p></div></div>';
    section.appendChild(simulationParametersCard);
    overviewPanel.appendChild(section);
    document.querySelector('[data-result-tab="parameters"]')?.remove();
    simulationParametersPanel.remove();
  }
  document.querySelector('[data-result-tab="models"]')?.remove();
  document.querySelector('[data-result-panel="models"]')?.remove();
  const publishAction = $('publishBtn');
  if (overviewPanel && publishAction) {
    const section = document.createElement('section');
    section.className = 'overview-section overview-release';
    publishAction.className = 'btn btn-primary btn-block';
    section.appendChild(publishAction);
    overviewPanel.appendChild(section);
  }
  const resultTabBar = document.querySelector('.result-tab-bar');
  const resultTabs = resultTabBar?.querySelector('.result-tabs');
  if (resultTabBar && resultTabs) {
    resultTabBar.parentNode.insertBefore(resultTabs, resultTabBar);
    resultTabBar.remove();
  }
  const modalFocus = window.ConsoleModalFocus;
  if (!modalFocus) throw new Error('modal-focus.js 未在正式控制台脚本之前加载');
  const escapeHtml = value => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  const statusLabels = {
    DRAFT: '草稿', SEALED: '已封存', QUEUED: '排队中', STARTING: '正在启动', RUNNING: '运行中',
    PAUSE_REQUESTED: '正在暂停', PAUSED: '已暂停', CANCEL_REQUESTED: '正在取消',
    COMPLETED: '已完成', CANCELLED: '已取消', FAILED: '失败', INTERRUPTED: '已中断',
  };
  const statusClasses = {
    DRAFT: 'draft', SEALED: 'sealed', QUEUED: 'queued', RUNNING: 'running', PAUSED: 'paused',
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

  function isGlobalPage(pageName) {
    return pageName === 'experiments' || (!window.ResourceScope?.experimentId && ['maps', 'public-agents', 'brains', 'crowds', 'skills', 'model-catalog'].includes(pageName));
  }

  function workspaceUrl(pageName = state.workspacePage) {
    const url = new URL(window.location.href);
    const editorParams = new URLSearchParams(url.search);
    url.search = '';
    url.hash = '';
    url.pathname = window.ResourceScope?.experimentId && pageName !== 'experiments' ? `/experiments/${encodeURIComponent(window.ResourceScope.experimentId)}` : '/';
    if (isGlobalPage(pageName) && pageName !== 'experiments') {
      url.searchParams.set('view', pageName);
      if (pageName === 'maps' && state.selectedMapId) url.searchParams.set('map_id', state.selectedMapId);
      if (pageName === 'brains' && state.selectedBrainId) url.searchParams.set('brain_id', state.selectedBrainId);
      if (pageName === 'crowds' && state.selectedCrowdId) url.searchParams.set('crowd_id', state.selectedCrowdId);
      if (editorParams.get('view') === pageName) {
        const keys = ['skills', 'brains'].includes(pageName) ? ['skill_key'] : pageName === 'model-catalog' ? ['model_id', 'create'] : [];
        keys.forEach(key => { if (editorParams.has(key)) url.searchParams.set(key, editorParams.get(key)); });
      }
    } else if (pageName !== 'experiments' && state.selectedExperimentId) {
      if (!window.ResourceScope?.experimentId) url.searchParams.set('experiment_id', state.selectedExperimentId);
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
    const isGlobal = isGlobalPage(pageName);
    if (!window.ResourceScope?.experimentId) window.ResourceList?.capture(state.workspacePage);
    if (state.workspacePage !== pageName) {
      stopExperimentListRefresh();
      window.SkillWorkspace?.invalidate?.();
      window.ModelWorkspace?.deactivate?.();
      if (window.MapWorkspace) window.MapWorkspace.openGeneration = (window.MapWorkspace.openGeneration || 0) + 1;
      if (window.CrowdWorkspace) window.CrowdWorkspace.openGeneration = (window.CrowdWorkspace.openGeneration || 0) + 1;
    }
    if (isGlobal) clearDuplicateFeedback();
    window.SkillWorkspace?.deactivateTopbar?.();
    state.workspacePage = pageName;
    document.querySelectorAll('.nav-item[data-page]').forEach(item => {
      item.classList.toggle('active', item.dataset.page === pageName);
    });
    document.querySelectorAll('.page').forEach(page => {
      const active = page === target;
      page.classList.toggle('active', active);
      page.inert = !active;
      page.setAttribute('aria-hidden', String(!active));
    });
    document.body.classList.toggle('hub-mode', isGlobal);
    document.body.classList.toggle('brain-mode', pageName === 'brains');
    if (pageName !== 'brains') document.body.classList.remove('brain-editor-mode');
    const catalogNames = {experiments:'实验', maps:'地图', 'public-agents':'智能体', crowds:'人群', skills:'技能', brains:'大脑', 'model-catalog':'模型'};
    $('topbarTitle').textContent = window.ResourceScope?.experimentId ? state.currentExperimentName : catalogNames[pageName] || state.currentExperimentName || '当前实验';
    $('catalogDescription').hidden = !isGlobal;
    $('catalogDescription').textContent = pageName === 'experiments' ? '打开实验后，进入独立的实验工作区。' : pageName === 'public-agents' ? '基础配置 · 出生位置与空间在实验内设置。' : '基础配置 · 创建实验时复制，后续修改互不影响。';
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
    $('createAgentResourceBtn').hidden = pageName !== 'public-agents';
    $('createModelResourceBtn').hidden = pageName !== 'model-catalog';
    $('createSkillBtn').textContent = '＋ 新建技能';
    $('experimentActions').hidden = isGlobal || ['maps', 'crowds', 'skills', 'brains'].includes(pageName);
    syncMapEditorTopbar();
    $('resultRunSelect').hidden = pageName !== 'results' || !state.runHistory.length;
    $('resultHeaderActions').hidden = pageName !== 'results';
    $('saveBtn').hidden = pageName === 'results';
    $('publishBtn').hidden = pageName !== 'overview' || latestRunHasPendingExecution();
    if (pageName === 'results' && state.currentRun) renderRunActions(state.currentRun);
    else $('resultRunControls').hidden = true;
    if (pageName !== 'results') {
      state.eventSource?.close();
      state.eventSource = null;
      if (state.resultPollTimer) clearInterval(state.resultPollTimer);
      state.resultPollTimer = null;
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
    if (pageName === 'experiments') loadExperiments().catch(reportError);
    if (pageName === 'maps') window.MapWorkspace?.activate().catch(reportError);
    if (pageName === 'brains') window.SkillWorkspace?.activate('brains').catch(reportError);
    if (pageName === 'crowds') window.CrowdWorkspace?.activate().catch(reportError);
    if (pageName === 'public-agents') window.CrowdWorkspace?.activateAgents().catch(reportError);
    if (pageName === 'model-catalog') window.ModelWorkspace.activate().catch(reportError);
    if (pageName === 'models') {
      window.ModelWorkspace.loadChoices($('experimentChatModelChoice'), 'chat');
      window.ModelWorkspace.loadChoices($('experimentEmbeddingModelChoice'), 'embedding');
      $('applyExperimentModelChoices').disabled = !state.draft;
    }
    if (pageName === 'skills') window.SkillWorkspace?.activate('skills').catch(reportError);
    syncWorkspaceUrl();
    if (!isGlobal || !window.ResourceList?.read(pageName).restore) window.scrollTo({ top: 0, behavior: 'instant' });
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

  async function requestGlobalNavigation(pageName) {
    if (state.workspacePage === 'model-catalog' && !await window.ModelWorkspace.mayLeave()) return;
    if (window.ResourceScope?.experimentId && pageName === 'experiments') { window.location.assign('/'); return; }
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
      '已封存': ['#edf2f0', '#dce5e1', '#64766f'],
      '已暂停': ['#edf2ff', '#d5dff8', '#3f6fd9'],
      '已完成': ['#edf2f0', '#dce5e1', '#64766f'],
      '失败': ['#fff0ed', '#edc7bd', '#a53f2b'],
      '已取消': ['#edf2f0', '#dce5e1', '#64766f'],
      '已中断': ['#fff0ed', '#edc7bd', '#a53f2b'],
    };
    const palette = styles[status] || styles['草稿'];
    $('statusPill').textContent = status;
    $('statusPill').setAttribute('aria-label', `实验状态：${status}`);
    $('statusPill').title = '实验生命周期：草稿 → 已封存；仿真执行状态见所选 Run。';
    $('statusPill').style.background = palette[0];
    $('statusPill').style.borderColor = palette[1];
    $('statusPill').style.color = palette[2];
  }

  function experimentHasRun(experiment = state.experiment) {
    return Boolean(experiment?.run_count || experiment?.latest_run?.id);
  }

  function latestRunHasPendingExecution(experiment = state.experiment) {
    return ['QUEUED', 'STARTING', 'RUNNING', 'PAUSE_REQUESTED', 'PAUSED', 'CANCEL_REQUESTED'].includes(experiment?.latest_run?.status);
  }

  function setWorkspaceMode() {
    state.workspaceReadonly = !state.draft;
    document.body.classList.toggle('readonly-mode', state.workspaceReadonly);
    $('workspaceNotice').hidden = !experimentHasRun();
    const workspaceMessage = experimentHasRun()
      ? '实验包已封存；地图、大脑、Agent、参数和模型配置均保持不变。需要调整时请复制为新的独立实验。'
      : '';
    $('workspaceNoticeHelp').dataset.tooltip = workspaceMessage;
    $('workspaceNoticeHelp').setAttribute('aria-label', workspaceMessage || '只读实验说明');
    document.querySelectorAll('.dirty-track, .switch, .agent-check').forEach(control => {
      control.disabled = state.workspaceReadonly;
    });
    ['experimentBrainRevisionSelect'].forEach(id => {
      if ($(id)) $(id).disabled = true;
    });
    $('selectAllBtn').disabled = state.workspaceReadonly;
    $('cloneBtn').textContent = state.duplicateInProgress ? '正在复制…' : '复制实验';
    $('cloneBtn').disabled = state.duplicateInProgress;
    $('saveBtn').textContent = state.draft ? '保存配置' : '查看仿真';
    $('publishBtn').textContent = experimentHasRun() ? '执行下一次仿真' : '执行实验';
    $('saveBtn').hidden = state.workspacePage === 'results';
    $('publishBtn').hidden = state.workspacePage !== 'overview' || latestRunHasPendingExecution();
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
    $('runQualityBanner').hidden = !state.runHistory.length;
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
    const response = window.ResourceScope?.experimentId
      ? { items: [{ name: state.definition?.engine?.brain_skill, resource_id: state.definition?.engine?.brain_skill }] }
      : await api('/skills?kind=brain');
    selector.replaceChildren(new Option('请选择 Brain Skill', ''));
    (response.items || []).forEach(item => {
      const option = new Option(
        `${item.name}${item.description ? ` · ${item.description}` : ''}`,
        item.resource_id,
      );
      option.dataset.skillName = item.name;
      option.dataset.revisionHash = item.revision;
      selector.appendChild(option);
    });
    selector.disabled = !(response.items || []).length;
    if (selector.disabled) selector.options[0].textContent = '暂无可用 Brain Skill';
    const composition = $('experimentBrainRevisionSelect');
    if (composition) {
      const selectedBrain = state.definition?.engine?.brain_skill || '';
      composition.replaceChildren(new Option('未选择大脑', ''));
      (response.items || []).forEach(item => {
        const option = new Option(item.name, item.resource_id);
        option.dataset.skillName = item.name;
        option.dataset.revisionHash = item.revision;
        composition.appendChild(option);
      });
      const selectedOption = [...composition.options].find(option => option.dataset.skillName === selectedBrain);
      composition.value = selectedOption?.value || '';
      composition.disabled = true;
      $('experimentBrainRevisionMeta').textContent = selectedBrain
        ? `${selectedBrain} · 已复制进当前实验`
        : '尚未选择大脑';
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

  function confirmResourceDeletion({ type = '资源', name = '当前资源', message = '', title = `删除${type}`, confirmLabel = '确认删除' } = {}) {
    if (state.pendingResourceDelete) {
      state.pendingResourceDelete.resolve(false);
      state.pendingResourceDelete = null;
    }
    $('resourceDeleteTitle').textContent = title;
    $('confirmResourceDelete').textContent = confirmLabel;
    $('resourceDeleteName').textContent = name;
    $('resourceDeleteMessage').textContent = message || '删除公共资源不会影响已经导入实验的物理副本。';
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
    if (modal.id === 'agentEditorModal' && state.agentSaving && event.key === 'Escape') { event.preventDefault(); return true; }
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
    const summary = `<div class="validation-item model-auto-probe"><span>↻</span><div><strong>模型服务由系统自动检测</strong><small>确认执行后会一次性检测 ${escapeHtml(purposes.join(' + '))}，并将 auto 固化为实际模型。无需逐项操作。</small></div></div>`;
    return summary;
  }

  function renderOverviewValidation(report) {
    state.validationReport = report;
    $('publishBtn').disabled = false;
    $('publishBtn').title = report.valid ? '' : '打开执行确认，查看并处理阻断项';
  }

  async function refreshValidation() {
    if (!state.selectedExperimentId || !state.revision) return null;
    const experimentId = state.selectedExperimentId;
    const definitionHash = (state.draft || state.revision).definition_hash;
    const report = await api(`/experiments/${experimentId}/validate`, { method: 'POST' });
    if (experimentId !== state.selectedExperimentId
      || definitionHash !== (state.draft || state.revision)?.definition_hash
      || report.definition_hash !== (state.draft || state.revision)?.definition_hash) return null;
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
    if (!state.definition) throw new Error('当前实验没有可执行的配置');
    state.launchExperimentId = state.selectedExperimentId;
    $('modalTitle').textContent = state.draft ? '执行实验' : '执行下一次仿真';
    $('publishLaunchStatus').textContent = state.draft ? '正在保存并检查配置…' : '正在检查封存包；确认后从初始状态创建新的 Run，保留旧仿真。';
    if (state.draft) await saveDraft({ silent: true });
    $('confirmPublish').disabled = true;
    $('modalAgentCount').textContent = state.definition.agents.filter(agent => agent.enabled).length;
    $('modalModels').textContent = `${state.definition.models.chat.resolved_model || state.definition.models.chat.model} / ${state.definition.models.embedding.resolved_model || state.definition.models.embedding.model}`;
    $('modalWorld').textContent = state.definition.world.world_name || '世界待配置';
    openModal('publishModal', 'confirmPublish');
    const [report, estimate] = await Promise.all([
      refreshValidation(),
      api(`/experiments/${state.selectedExperimentId}/estimate`),
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
    if (state.launchExperimentId !== state.selectedExperimentId) return;
    renderPublishValidation(report, estimate);
    $('publishLaunchStatus').textContent = report?.valid ? '检查通过，等待确认执行。' : '检查未通过，请处理上方阻断项。';
  }

  function openResumeRunModal(detail = null) {
    const run = state.currentRun;
    if (detail && detail.run_id !== run?.run_id) throw new Error('仿真已切换，请重新打开检查点');
    if (detail ? !detail.resumable : !isRunRecoverable(run)) throw new Error(detail?.recovery_reason || '当前仿真没有可用的恢复点');
    const step = Number(detail?.step_no || run.recoverable_step);
    state.pendingResumeRunId = run.run_id;
    state.pendingResumeStep = step;
    state.pendingResumeAttemptId = detail?.active_attempt_id || run.active_attempt_id;
    $('resumeRunFeedback').textContent = '保持原 Run，创建新 Attempt；从下一步继续，已提交步骤不会重新执行。';
    $('resumeRunIdentity').textContent = run.run_id.slice(0, 12);
    $('resumeRunStep').textContent = `Step ${step}`;
    $('resumeRunNextStep').textContent = `Step ${step + 1}`;
    openModal('resumeRunModal', 'confirmResumeRun');
  }

  async function api(path, options = {}) {
    // Web 只调用 Studio 的文件包与作者资源适配器；Runtime/Replay 不读取数据库。
    const { transportRetries = 0, ...fetchOptions } = options;
    let response;
    for (let attempt = 0; ; attempt += 1) {
      try {
        const endpoint = path.startsWith('/skills')
          ? `/api/studio/resources${path}`
          : `/api/studio${path}`;
        response = await fetch(endpoint, {
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
      const document = payload.error || (typeof payload.detail === 'object' ? payload.detail : {});
      const error = new Error(document.message || (typeof payload.detail === 'string' ? payload.detail : '') || `请求失败（${response.status}）`);
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
    const status = statusLabels[item.status] || item.status;
    const runDetail = run ? statusLabels[run.status] || run.status : '实验配置可继续完善';
    const selected = state.selectedExperimentIds?.has(item.id) || false;
    return window.ResourceList.row({
      name: item.name, description: item.goal || '尚未填写实验目标', icon: '▦',
      badges: [status, item.archived_at ? '已归档' : ''],
      meta: [core.world_name || '世界待配置', `${core.agent_count ?? 0} 个智能体`, run ? `最近运行：${runDetail} · ${completed}/${requested} 步` : '尚未运行'],
      open: {'data-open-experiment': item.id},
      className: `experiment-card${selected ? ' is-selected' : ''}${item.archived_at ? ' archived' : ''}`,
      attributes: {'data-id':item.id, 'data-archived':item.archived_at?'true':'false', 'data-status':statusClasses[item.status] || 'draft'},
      before: `<label class="experiment-select-wrap"><input type="checkbox" class="experiment-select" ${selected?'checked':''} aria-label="选择 ${escapeHtml(item.name)}"><span>选择</span></label>`,
      more: '<button class="experiment-menu" aria-label="更多实验操作" aria-haspopup="true" aria-expanded="false">⋯</button>',
    });
  }

  async function loadExperiments({ silent = false, fresh = false } = {}) {
    clearTimeout(state.experimentListTimer);
    state.experimentListTimer = null;
    const key = `${state.archiveFilter}:${state.page}`;
    const pending = state.experimentListRequest;
    if (pending?.key === key) {
      // Mutations require one fresh read after the pending request settles.
      if (fresh) pending.refreshAfter = true;
      return pending.promise;
    }
    pending?.controller.abort();
    const request = {key, controller: new AbortController(), promise: null};
    state.experimentListRequest = request;
    request.promise = fetchExperimentList(silent, request).finally(() => {
      if (state.experimentListRequest !== request) return;
      state.experimentListRequest = null;
      if (request.refreshAfter) return loadExperiments({silent});
      scheduleExperimentListPoll();
    });
    return request.promise;
  }

  function stopExperimentListRefresh() {
    clearTimeout(state.experimentListTimer);
    state.experimentListTimer = null;
    state.experimentListGeneration += 1;
    state.experimentListRequest?.controller.abort();
    state.experimentListRequest = null;
  }

  function scheduleExperimentListPoll() {
    clearTimeout(state.experimentListTimer);
    state.experimentListTimer = null;
    if (!state.bootstrapped || state.workspacePage !== 'experiments'
      || document.visibilityState === 'hidden' || state.experimentListRequest
      || !state.experimentListNeedsPolling) return;
    state.experimentListTimer = setTimeout(() => {
      state.experimentListTimer = null;
      loadExperiments({silent: true}).catch(reportError);
    }, 3000);
  }

  async function fetchExperimentList(silent, request) {
    // 每次查询获得新的 generation；较慢的旧请求返回后会被下方守卫直接忽略。
    const generation = ++state.experimentListGeneration;
    const requestPage = state.page;
    const requestArchive = state.archiveFilter;
    const isStale = () => request.controller.signal.aborted || request.refreshAfter
      || generation !== state.experimentListGeneration
      || requestPage !== state.page || requestArchive !== state.archiveFilter;
    const params = new URLSearchParams({ page: requestPage, page_size: 5, archived: requestArchive });
    const list = $('experimentList');
    if (!silent) {
      list.setAttribute('aria-busy', 'true');
      window.ResourceList?.loading(list, '实验');
    }
    let data;
    try {
      data = await api(`/experiments?${params}`, {signal: request.controller.signal});
    } catch (error) {
      if (isStale()) return;
      state.experimentListNeedsPolling = false;
      list.catalogMarkup = null;
      state.visibleExperimentIds = [];
      list.innerHTML = `<div class="resource-list-state experiment-list-error" role="alert"><strong>实验列表加载失败</strong><span>${escapeHtml(error.message || '系统服务没有返回可用的实验列表。')}</span><button class="btn btn-sm" id="retryExperimentList" type="button">重新加载</button></div>`;
      list.removeAttribute('aria-busy');
      $('experimentEmpty').hidden = true;
      $('experimentListFooter').hidden = true;
      updateExperimentSelectionControls();
      list.querySelector('#retryExperimentList').addEventListener('click', () => loadExperiments());
      reportError(error);
      return;
    }
    if (isStale()) return;
    state.experimentListNeedsPolling = data.items.some(item =>
      ['CREATED', 'QUEUED', 'RUNNING', 'FINALIZING'].includes(item.latest_run?.status));
    list.removeAttribute('aria-busy');
    const lastPage = Math.max(1, data.total_pages || 1);
    if (state.page > lastPage) {
      state.page = lastPage;
      await loadExperiments();
      return;
    }
    const markup = data.items.map(cardTemplate).join('');
    if (!silent || list.catalogMarkup !== markup) {
      list.innerHTML = markup;
      list.catalogMarkup = markup;
    }
    state.visibleExperimentIds = data.items.map(item => item.id);
    $('experimentEmpty').hidden = data.total !== 0;
    $('experimentListFooter').hidden = false;
    if (data.total) {
      const first = (data.page - 1) * data.page_size + 1;
      const last = Math.min(data.total, first + data.items.length - 1);
      $('experimentRange').textContent = `显示 ${first}–${last}，共 ${data.total} 条 · 每页 5 条`;
      renderPages(data.total_pages || 1);
    } else {
      $('experimentRange').textContent = '显示 0，共 0 条 · 每页 5 条';
      renderPages(1);
    }
    updateExperimentSelectionControls();
    window.ResourceList?.remember('experiments', {page: state.page, archiveFilter: state.archiveFilter});
    window.ResourceList?.restore('experiments');
  }

  function updateExperimentSelectionControls() {
    const count = state.selectedExperimentIds?.size || 0;
    $('archiveSelectedBtn').disabled = count < 1;
    $('restoreSelectedBtn').disabled = count < 1;
    $('archiveSelectedBtn').hidden = state.archiveFilter === 'archived';
    $('restoreSelectedBtn').hidden = state.archiveFilter !== 'archived';
  }

  function renderPages(totalPages) {
    $('experimentPagination').hidden = false;
    const markup = Array.from({ length: totalPages }, (_, index) => {
      const page = index + 1;
      return `<button class="page-button${page === state.page ? ' active' : ''}" data-api-page="${page}"${page === state.page ? ' aria-current="page"' : ''}>${page}</button>`;
    }).join('');
    if ($('experimentPages').catalogMarkup !== markup) {
      $('experimentPages').innerHTML = markup;
      $('experimentPages').catalogMarkup = markup;
    }
    $('experimentPrev').disabled = state.page <= 1;
    $('experimentNext').disabled = state.page >= totalPages;
    $('experimentPagination').dataset.totalPages = totalPages;
  }

  function navigateToExperiment(id, targetPage = 'overview', runId = null) {
    window.ResourceList?.capture('experiments');
    if (window.ResourceScope?.experimentId === String(id)) return false;
    stopExperimentListRefresh();
    const params = new URLSearchParams({ view: targetPage });
    if (runId) params.set('run_id', runId);
    window.location.assign(`/experiments/${encodeURIComponent(id)}?${params}`);
    return true;
  }

  async function openExperiment(id, targetPage = 'overview', preferredRunId = null, { duplicateFeedbackGeneration = null } = {}) {
    // 实验详情直接来自自包含目录或 .gaexp；不存在公共资源回查和 Revision 映射。
    const ownsDuplicateFeedback = () => duplicateFeedbackGeneration === null || duplicateFeedbackGeneration === state.duplicateFeedbackGeneration;
    if (!ownsDuplicateFeedback()) return;
    if (duplicateFeedbackGeneration === null) clearDuplicateFeedback();
    if (navigateToExperiment(id, targetPage, preferredRunId)) return;
    const generation = ++state.experimentOpenGeneration;
    const experiment = await api(`/experiments/${id}`);
    if (generation !== state.experimentOpenGeneration || !ownsDuplicateFeedback()) return;
    const snapshot = {
      id: experiment.experiment_id,
      state: experiment.editable ? 'DRAFT' : 'SEALED',
      lock_version: 1,
      definition_hash: experiment.content_sha256 || '',
      definition: experiment.definition,
    };
    const changingExperiment = id !== state.selectedExperimentId;
    if (changingExperiment || targetPage !== 'results') resetResultRuntime();
    if (changingExperiment) {
      state.runHistory = [];
      state.runHistoryExperimentId = null;
    }
    state.selectedExperimentId = id;
    state.experiment = experiment;
    window.ResourceScope?.setExperiment(experiment);
    state.draft = experiment.editable ? snapshot : null;
    state.definition = experiment.definition || null;
    state.revision = snapshot;
    state.latestRunId = experiment.latest_run?.id || null;
    state.selectedRunId = targetPage === 'results' ? preferredRunId || state.latestRunId : null;
    $('navRunCount').textContent = experiment.run_count || 0;
    state.currentExperimentName = experiment.name;
    state.currentExperimentStatus = statusLabels[experiment.status] || experiment.status;
    $('experimentOwnerMeta').textContent = experiment.owner || '未设置';
    $('experimentTagsMeta').textContent = experiment.tags?.length ? experiment.tags.join(' · ') : '未设置';
    if (state.definition) fillDraft(state.definition);
    $('addAgentBtn').disabled = !experiment.editable;
    fillDefinitionOverview(state.definition, state.revision);
    applyStatusPill(state.currentExperimentStatus);
    setWorkspaceMode();
    goToPage(targetPage);
    if (targetPage === 'results') await loadRunHistory(id, state.selectedRunId);
    fillLatestRunSummary(experiment).catch(reportError);
  }

  function applyExperimentRuntime(experiment) {
    if (!experiment || experiment.id !== state.selectedExperimentId) return;
    state.experiment = experiment;
    window.ResourceScope?.setExperiment(experiment);
    state.latestRunId = experiment.latest_run?.id || null;
    state.currentExperimentName = experiment.name;
    state.currentExperimentStatus = statusLabels[experiment.status] || experiment.status;
    $('navRunCount').textContent = experiment.run_count || 0;
    $('experimentOwnerMeta').textContent = experiment.owner || '未设置';
    $('experimentTagsMeta').textContent = experiment.tags?.length ? experiment.tags.join(' · ') : '未设置';
    if (!isGlobalPage(state.workspacePage)) $('topbarTitle').textContent = experiment.name;
    applyStatusPill(state.currentExperimentStatus);
    setWorkspaceMode();
  }

  async function syncSelectedExperiment({ refreshDefinition = false, refreshOverview = true } = {}) {
    const experimentId = state.selectedExperimentId;
    if (!experimentId) return;
    const generation = ++state.selectedExperimentGeneration;
    const experiment = await api(`/experiments/${experimentId}`);
    if (generation !== state.selectedExperimentGeneration || experimentId !== state.selectedExperimentId) return;
    if ((refreshDefinition || experiment.content_sha256 !== state.revision?.definition_hash) && !state.dirty && !state.agentSaving) {
      const snapshot = {
        id: experiment.experiment_id,
        state: experiment.editable ? 'DRAFT' : 'SEALED',
        lock_version: 1,
        definition_hash: experiment.content_sha256 || '',
        definition: experiment.definition,
      };
      state.draft = experiment.editable ? snapshot : null;
      state.revision = snapshot;
      state.definition = experiment.definition;
      state.remoteConflictKey = null;
      fillDraft(state.definition);
      fillDefinitionOverview(state.definition, state.revision);
    }
    applyExperimentRuntime(experiment);
    if (state.definition) fillDefinitionOverview(state.definition, state.revision);
    if (refreshOverview) await fillLatestRunSummary(experiment);
  }

  function fillDraft(definition) {
    state.definition = definition;
    const definitionFields = $('overviewLegacyDefinitionFields');
    if (definitionFields) definitionFields.dataset.revisionId = state.revision?.id || '';
    const simulation = definition.simulation;
    if ($('experimentNameDraft')) $('experimentNameDraft').value = state.experiment?.name || definition.experiment?.name || '';
    if ($('experimentGoalDraft')) $('experimentGoalDraft').value = state.experiment?.goal ?? definition.experiment?.goal ?? '';
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
    if ($('navAgentCount')) $('navAgentCount').textContent = definition.agents.length;
    if ($('overviewResourceAgents')) $('overviewResourceAgents').textContent = `${definition.agents.filter(item => item.enabled).length} 个 Agent`;
  }

  async function saveExperimentMetadata() {
    if (!state.selectedExperimentId || !state.experiment) {
      throw new Error('请先打开实验');
    }
    const name = $('experimentNameDraft').value.trim();
    const goal = $('experimentGoalDraft').value.trim();
    if (!name) throw new Error('实验名称不能为空');
    if (!state.draft) throw new Error('封存实验不可修改；请先复制为新的独立实验');
    state.experiment.name = name;
    state.experiment.goal = goal;
    state.draft.definition.experiment.name = name;
    state.draft.definition.experiment.goal = goal;
    await saveDraft({ silent: true });
    state.currentExperimentName = name;
    $('topbarTitle').textContent = name;
    scheduleGlobalReconcile({ full: true });
    showToast('实验名称、目标与故事已更新。', '实验定义已保存');
  }

  function enqueueDraftMutation(operation) {
    const queued = state.draftMutation.catch(() => {}).then(operation);
    state.draftMutation = queued.catch(() => {});
    return queued;
  }

  async function acceptSavedDraft(saved, { refreshDerived = false } = {}) {
    state.draft = saved;
    state.revision = saved;
    state.definition = saved.definition;
    if (window.ResourceScope?.experimentId === state.selectedExperimentId) window.ResourceScope.digest = saved.definition_hash;
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

  function fillExperimentComposition(definition) {
    const world = definition.world;
    window.MapWorkspace?.setExperimentContext({
      experimentId: state.selectedExperimentId,
      world,
      lockVersion: state.draft?.lock_version || 0,
      editable: Boolean(state.draft) && !state.workspaceReadonly,
    }).catch(reportError);
    prepareExperimentBrainChoices().catch(reportError);
  }

  function fillDefinitionOverview(definition, revision) {
    if (!definition) return;
    const agents = definition.agents || [];
    const enabled = agents.filter(item => item.enabled).length;
    const enabledAgents = agents.filter(item => item.enabled);
    const agentNames = enabledAgents.slice(0, 5).map(item => item.display_name || item.name || item.agent_key).join('、');
    $('overviewResourceAgents').textContent = `${enabled} 个 Agent${agentNames ? ` · ${agentNames}${enabled > 5 ? '等' : ''}` : ''}`;
    if (state.runEstimate?.experiment_id === revision?.id
      && state.runEstimate?.definition_hash === revision?.definition_hash
      && state.runEstimate?.lock_version === revision?.lock_version) {
      applyRunEstimateToOverview(state.runEstimate);
    }
  }

  function applyRunEstimateToOverview(estimate) {
    state.runEstimate = estimate;
  }

  async function refreshRunEstimateOverview(experimentId = state.selectedExperimentId, revisionId = state.revision?.id) {
    if (!experimentId || !revisionId) return;
    const estimate = await api(`/experiments/${experimentId}/estimate`);
    if (experimentId !== state.selectedExperimentId
      || revisionId !== state.revision?.id
      || estimate.definition_hash !== state.revision?.definition_hash
      || estimate.lock_version !== state.revision?.lock_version) return;
    applyRunEstimateToOverview(estimate);
  }

  async function fillLatestRunSummary(experiment) {
    // 最近一次仿真的状态已经在实验列表和实验结果中展示，概览不重复渲染。
    return experiment;
  }

  function setSwitch(id, active) {
    $(id).classList.toggle('on', Boolean(active));
  }

  function fillModelFields(models) {
    $('experimentModelCopySummary').innerHTML = ['chat', 'embedding'].map(purpose => {
      const model = models[purpose];
      return `<div class="summary-cell"><span>${purpose === 'chat' ? '聊天模型' : 'Embedding'}</span><strong>${escapeHtml(model.model)}</strong><small>${escapeHtml(model.base_url || '')} · ${model.credential_env ? '密钥已关联' : '未配置密钥'}</small></div>`;
    }).join('');
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
      const portrait = agent.portrait_asset || '';
      const portraitMarkup = portrait
        ? `<img src="${escapeHtml(portrait)}" alt="" onerror="this.hidden=true" />`
        : escapeHtml(agent.name.slice(0, 1));
      return `<div class="agent-row${selected ? ' is-selected' : ''}" data-agent-key="${escapeHtml(agent.agent_key)}" data-search="${escapeHtml(search)}" data-enabled="${agent.enabled}" data-complete="${complete}" data-location="${escapeHtml(location.toLowerCase())}" data-model="${escapeHtml(String(model).toLowerCase())}"><input class="agent-select-check" type="checkbox" ${selected ? 'checked' : ''} ${state.draft ? '' : 'disabled'} aria-label="选择 ${escapeHtml(agent.name)}" /><input class="checkbox agent-check" type="checkbox" ${agent.enabled ? 'checked' : ''} ${state.draft ? '' : 'disabled'} aria-label="启用 ${escapeHtml(agent.name)}" /><div class="agent-person"><div class="avatar">${portraitMarkup}</div><div><strong>${escapeHtml(agent.name)}</strong><span>${escapeHtml(agent.scratch.innate || '未填写特质')} · ${agent.scratch.age} 岁${model ? ` · ${escapeHtml(model)}` : ''}</span></div></div><div class="truncate">${escapeHtml(agent.currently || (agent.goals || [])[0] || '尚未填写当前目标')}</div><div class="location">${escapeHtml(location)}</div><span class="chip ${complete ? 'teal' : 'incomplete'}">${complete ? '定义完整' : '待补充'}</span><button class="row-actions agent-edit-btn" type="button" aria-label="${state.draft ? '编辑' : '查看'} ${escapeHtml(agent.name)}">⋯</button></div>`;
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

  function applyRequestedAgentChanges(agent, changes) {
    const updated = structuredClone(agent);
    if (Object.prototype.hasOwnProperty.call(changes, 'enabled')) updated.enabled = changes.enabled;
    if (Object.prototype.hasOwnProperty.call(changes, 'model_override')) updated.model_override = changes.model_override;
    if (Object.prototype.hasOwnProperty.call(changes, 'coord')) updated.coord = [...changes.coord];
    if (changes.append_goal) updated.goals = [...(updated.goals || []), changes.append_goal];
    if (changes.add_tags?.length) updated.tags = [...new Set([...(updated.tags || []), ...changes.add_tags])];
    return updated;
  }

  async function previewAgentBatch() {
    const changes = requestedAgentBatchChanges();
    const selected = state.draft.definition.agents.filter(agent => state.selectedAgentKeys.has(agent.agent_key));
    const preview = {
      affected: selected.length,
      changes: selected.map(agent => ({
        name: agent.name || agent.agent_key,
        before: agent,
        after: applyRequestedAgentChanges(agent, changes),
      })),
    };
    state.pendingAgentBatch = { changes, lockVersion: state.draft.lock_version };
    renderAgentBatchPreview(preview);
    $('applyBatchAgents').disabled = false;
  }

  async function applyAgentBatch() {
    if (!state.pendingAgentBatch || state.pendingAgentBatch.lockVersion !== state.draft.lock_version) await previewAgentBatch();
    const previousDefinition = structuredClone(state.draft.definition);
    const changes = state.pendingAgentBatch.changes;
    state.draft.definition.agents = state.draft.definition.agents.map(agent => (
      state.selectedAgentKeys.has(agent.agent_key) ? applyRequestedAgentChanges(agent, changes) : agent
    ));
    const result = {
      affected: state.selectedAgentKeys.size,
      changes: state.draft.definition.agents
        .filter(agent => state.selectedAgentKeys.has(agent.agent_key))
        .map(agent => ({ name: agent.name || agent.agent_key, before: previousDefinition.agents.find(item => item.agent_key === agent.agent_key), after: agent })),
      draft: await saveDraft({ silent: true }),
    };
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
    state.draft.definition = structuredClone(state.lastAgentBatchUndo);
    const saved = await saveDraft({ silent: true });
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
    const saved = await api(`/experiments/${state.selectedExperimentId}`, {
      method: 'PUT', body: JSON.stringify({ definition, expected_content_sha256: state.draft?.definition_hash }),
    });
    state.draft = saved; state.definition = saved.definition; state.pendingAgentImport = null;
    closeModal('agentImportModal'); fillDraft(saved.definition); fillDefinitionOverview(saved.definition, saved);
    showToast(`新增 ${added}、更新 ${updated}、跳过 ${skipped} 个 Agent。`, '导入完成');
  }

  async function refreshRunHistoryList(experimentId, preferredRunId = state.selectedRunId) {
    // 这里主动翻完稳定游标，保证仿真下拉框不会只显示第一页。
    const generation = ++state.runHistoryGeneration;
    const selectedAtRequest = state.selectedRunId;
    const runAtRequest = state.currentRun;
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
    // A background history read must not undo a later Run selection or live poll.
    if (state.currentRun !== runAtRequest && state.currentRun?.run_id === state.selectedRunId) {
      const index = state.runHistory.findIndex(item => item.run_id === state.selectedRunId);
      if (index >= 0) state.runHistory[index] = { ...state.runHistory[index], ...state.currentRun };
    }
    renderRunSelect(state.selectedRunId !== selectedAtRequest ? state.selectedRunId : preferredRunId);
    return state.runHistory;
  }

  async function reconcileSelectedRunHistory(experimentId, preferredRunId = state.selectedRunId) {
    const runs = await refreshRunHistoryList(experimentId, preferredRunId);
    if (!runs || experimentId !== state.selectedExperimentId) return;
    if (!runs.length) {
      if (state.workspacePage === 'results') {
        $('resultEmpty').hidden = false;
        $('resultWorkspace').hidden = false;
        $('runQualityBanner').hidden = true;
        renderRunQuality(null);
        state.selectedRunId = null;
        setResultTab('parameters', { sync: true });
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
      $('resultWorkspace').hidden = false;
      $('runQualityBanner').hidden = true;
      renderRunQuality(null);
      state.selectedRunId = null;
      setResultTab('parameters', { sync: true });
      return;
    }
    $('resultEmpty').hidden = true;
    $('resultWorkspace').hidden = false;
    $('runQualityBanner').hidden = false;
    const runId = state.runHistory.some(item => item.run_id === preferredRunId) ? preferredRunId : state.runHistory[0].run_id;
    await loadResults(runId);
  }

  function renderRunSelect(selectedRunId = state.selectedRunId) {
    $('navRunCount').textContent = state.experiment?.run_count ?? state.runHistory.length;
    const select = $('resultRunSelect');
    select.hidden = state.workspacePage !== 'results' || !state.runHistory.length;
    select.innerHTML = state.runHistory.length ? state.runHistory.map((run, index) => {
      const runNumber = state.runHistory.length - index;
      const status = statusLabels[run.status] || run.status;
      return `<option value="${escapeHtml(run.run_id)}">仿真 ${runNumber} · ${escapeHtml(status)} · ${run.completed_steps}/${run.requested_steps} 步</option>`;
    }).join('') : '<option value="">暂无仿真记录</option>';
    select.disabled = !state.runHistory.length;
    if (selectedRunId && state.runHistory.some(run => run.run_id === selectedRunId)) select.value = selectedRunId;
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
    if (state.resultPollTimer) clearInterval(state.resultPollTimer);
    state.resultPollTimer = null;
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
    state.modelUsageItems = [];
    state.modelUsagePage = 1;
    $('modelUsageRows').innerHTML = '<div class="diagnostic-list-empty">正在加载当前仿真的已提交用途汇总…</div>';
    if ($('modelUsageWatermark')) {
      $('modelUsageWatermark').textContent = '正在读取当前仿真的 StepResult 水位…';
    }
    $('attemptLogSelect').innerHTML = '<option value="">正在加载 Attempt…</option>';
    $('traceAttemptSelect').innerHTML = '<option value="">正在加载 Attempt…</option>';
    $('attemptRows').innerHTML = '<div class="empty-state"><strong>正在加载当前仿真的执行尝试…</strong></div>';
    $('logViewport').textContent = '等待加载当前仿真的日志…';
    $('modelTraceRows').innerHTML = '<div class="diagnostic-list-empty">正在加载当前仿真的模型调用…</div>';
    $('systemEventRows').innerHTML = '<div class="diagnostic-list-empty">正在加载当前仿真的系统事件…</div>';
    $('checkpointRows').innerHTML = '<div class="diagnostic-list-empty">正在加载当前仿真的检查点…</div>';
    $('modelTraceDetail').hidden = true;
    $('checkpointDetail').hidden = true;
    $('checkpointPreview').hidden = true;
  }

  async function loadResults(runId) {
    // 切换 Run 时先拆除旧 SSE、日志流和 Phaser 实例，避免跨 Run DOM/网络所有权泄漏。
    const generation = ++state.resultGeneration;
    teardownReplay();
    state.conversationGeneration += 1;
    state.memoryGeneration += 1;
    resetOperationsWorkspaceForRunSwitch();
    state.selectedRunId = runId;
    state.currentRun = null;
    $('resultRunControls').hidden = true;
    renderRunSelect(runId);
    if (state.eventSource) state.eventSource.close();
    if (state.resultPollTimer) clearInterval(state.resultPollTimer);
    state.resultPollTimer = null;
    clearResultDurationTimer();
    if (state.resultRefreshTimer) clearTimeout(state.resultRefreshTimer);
    await refreshResultData(runId, generation);
    if (generation !== state.resultGeneration) return;
    // Run 状态和 StepResult 都在目录中；短轮询重读文件，不依赖数据库事件表。
    state.resultPollTimer = setInterval(() => {
      if (generation !== state.resultGeneration || runId !== state.selectedRunId) return;
      scheduleResultRefresh(runId, generation);
    }, 2000);
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

  function refreshResultData(runId, generation = state.resultGeneration, options = {}) {
    const active = state.resultRefreshInFlight;
    if (active?.runId === runId && active.generation === generation) return active.promise;
    const pending = { runId, generation };
    pending.promise = refreshResultDataUnlocked(runId, generation, options).finally(() => {
      if (state.resultRefreshInFlight === pending) state.resultRefreshInFlight = null;
    });
    state.resultRefreshInFlight = pending;
    return pending.promise;
  }

  async function refreshResultDataUnlocked(runId, generation = state.resultGeneration, { silent = false } = {}) {
    if (state.deletingRunId === runId) return;
    const requestGeneration = state.resultRequestGeneration = (state.resultRequestGeneration || 0) + 1;
    const run = await api(`/runs/${runId}`);
    if (generation !== state.resultGeneration || runId !== state.selectedRunId) return;
    if (run.package_error) {
      state.currentRun = run;
      teardownReplay();
      renderRunActions(run);
      $('replayStatus').textContent = run.package_error;
      $('checkpointRows').textContent = run.package_error;
      $('timelineStreamItems').textContent = run.package_error;
      return;
    }
    const [timeline, agents, conversations, memories, operations] = await Promise.all([
      api(`/runs/${runId}/results/timeline?limit=500`),
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
    renderRunQuality(run.quality || null);
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
    const qualityIdentity = JSON.stringify([state.selectedRunId, quality]);
    if (banner._qualityIdentity === qualityIdentity) return;
    const preserveOpen = banner._qualityRunId === state.selectedRunId && Boolean(banner.querySelector?.('.run-quality-details')?.open);
    banner._qualityIdentity = qualityIdentity;
    banner._qualityRunId = state.selectedRunId;
    const skippedWithoutIssues = quality?.evaluator?.status === 'SKIPPED' && !(quality?.issues || []).length;
    const status = skippedWithoutIssues ? 'NOT_EVALUATED' : quality?.quality_status || 'PENDING';
    const summary = skippedWithoutIssues ? '基础诊断未发现告警；本次未执行业务评估。' : quality?.summary || '';
    const labels = {
      PASS: '通过', WARNING: '有观察项', UNKNOWN: '评估不可用',
      NOT_EVALUATED: '未评估', PENDING: '待评估',
    };
    if (banner.dataset) banner.dataset.status = status;
    const issues = quality?.issues || [];
    const count = issues.length;
    const qualityTab = document.querySelector('[data-result-tab="quality"]');
    const qualityCount = $('resultQualityCount');
    if (qualityTab) qualityTab.dataset.qualityStatus = status;
    if (qualityCount) {
      qualityCount.textContent = count;
      qualityCount.hidden = count === 0;
    }
    const detail = count ? `
      <details class="run-quality-details">
        <summary>展开 ${count} 项质量告警</summary>
        <ol>${issues.map((issue, index) => {
          const step = Number(issue?.step_no);
          const target = Number.isInteger(step) && step > 0
            ? `<button type="button" class="quality-step-link" data-quality-step="${step}">定位 Step ${step}</button>`
            : '';
          const actorScope = issue?.object_key ? `Game Object ${issue.object_key}` : issue?.agent_key ? `Agent ${issue.agent_key}` : '';
          const scope = [escapeHtml(actorScope), target].filter(Boolean).join(' · ');
          const evidence = issue?.evidence && Object.keys(issue.evidence).length
            ? `<pre>${escapeHtml(JSON.stringify(issue.evidence, null, 2))}</pre>`
            : '';
          return `<li><div><strong>${index + 1}. ${escapeHtml(issue?.code || 'QUALITY_WARNING')}</strong><span>${scope}</span></div><p>${escapeHtml(issue?.message || '行为质量观察项')}</p>${evidence}</li>`;
        }).join('')}</ol>
      </details>` : '';
    banner.innerHTML = `<div class="run-quality-summary"><strong>行为质量：${escapeHtml(labels[status] || status)}</strong><span>${escapeHtml(summary)}${count ? ` · ${count} 项告警` : ''}（不改变运行完成状态）</span></div>${detail}`;
    const details = banner.querySelector?.('.run-quality-details');
    if (details) details.open = preserveOpen;
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
    const tasks = state.workspacePage === 'experiments' ? [loadExperiments({ silent: true })] : [];
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
    // The author list owns a completion-driven timer. Detail polling is separate.
    if (!window.ResourceScope?.experimentId && !state.selectedExperimentId) {
      scheduleExperimentListPoll();
      return;
    }
    ++state.activityGeneration;
    if (state.activitySource) state.activitySource.close();
    await reconcileGlobalState({ full: true });
    // 文件状态由短轮询重读；这里不维护依赖数据库事件表的全局 SSE。
    if (state.globalPollTimer) clearInterval(state.globalPollTimer);
    state.globalPollTimer = setInterval(() => scheduleGlobalReconcile({ full: true }), 3000);
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
    pauseResume.textContent = '暂停仿真';
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
    const empty = records || '<div class="agent-section-empty">本次仿真尚未产生计划修订；这里显示角色的初始计划。</div>';
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
      $('conversationMeta').textContent = '当前仿真还没有提交对话记录';
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
    // Once Replay owns this Run, only its committed frame can update the
    // timeline/inspector. Availability polling must not replace that frame.
    if (!replayOwnsRun) updateTimelineStep(Number(slider.value), { seekReplay: false });
    else syncReplayControls();
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
    if (state.deletingRunId === runId || (state.currentRun?.run_id === runId && state.currentRun?.package_error)) return null;
    const active = state.replayInitialization;
    if (active?.runId === runId && active.generation === generation) return active.promise;
    const pending = { runId, generation };
    pending.promise = ensureReplayPlayerUnlocked(runId, generation).catch(error => {
      if (runId === state.selectedRunId && generation === state.resultGeneration && error.name !== 'AbortError') {
        teardownReplay();
        $('replayStatus').textContent = `回放加载失败：${error.message}；正在等待重试`;
        $('replayInspectorAction').textContent = '回放加载失败，请查看上方诊断';
        syncReplayControls();
      }
      throw error;
    }).finally(() => {
      if (state.replayInitialization === pending) state.replayInitialization = null;
    });
    state.replayInitialization = pending;
    return pending.promise;
  }

  async function ensureReplayPlayerUnlocked(runId, generation) {
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
        if (!error.nonFatal) { state.replayReady = false; state.replayPlaying = false; }
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
      state.selectedReplayExperimentId,
      state.currentRun?.experiment_id,
      replayPlayer.manifest.agents,
    );
    if (restoredAgentKey) {
      applyReplayAgentSelection(restoredAgentKey);
    } else {
      state.selectedReplayAgentKey = null;
      state.selectedReplayExperimentId = null;
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
    state.selectedReplayExperimentId = key ? state.currentRun?.experiment_id || null : null;
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
    const manifest = state.replayPlayer.manifest || {};
    const totalSteps = Number(manifest.requested_steps || state.timeline?.requested_steps || state.currentRun?.requested_steps || 0);
    const start = new Date(manifest.start_time);
    if (Number.isFinite(start.getTime()) && totalSteps > 0) {
      const end = new Date(start.getTime() + Math.max(0, totalSteps - 1) * Number(manifest.stride_minutes || 1) * 60000);
      const formatter = new Intl.DateTimeFormat('zh-CN', {timeZone: manifest.timezone || 'Asia/Shanghai', month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
      $('timelineStartTime').textContent = formatter.format(start);
      $('timelineEndTime').textContent = formatter.format(end);
    }
    $('timelineRange').max = payload.availableStep || state.replayPlayer.availableStep;
    $('timelineRange').value = step.step_no;
    $('timelineStep').textContent = `Step ${step.step_no} / ${state.timeline?.requested_steps || state.currentRun?.requested_steps || payload.availableStep || state.replayPlayer.availableStep}`;
    $('timelineTime').textContent = formatTime(step.virtual_time);
    $('mapTimeLabel').textContent = `${formatTime(step.virtual_time)} · Step ${step.step_no}`;
    const selectedAgent = $('replayAgentSelect').value;
    if (selectedAgent) state.replayPlayer.selectAgent(selectedAgent);
    const conversations = step.conversations || [];
    const events = $('replayLayerKeyEvents').checked ? (step.domain_events || []) : [];
    const facts = [
      ...conversations.map(item => ({ type: '对话', text: (item.messages || []).map(message => `${message.speaker_agent_key}: ${message.content}`).join(' · ') })),
      ...events.map(item => {
        const data = item.payload || {};
        const structured = data.structured_payload || {};
        const nodes = state.replayPlayer?.manifest?.world?.definition?.editor_v2?.hierarchy_nodes || [];
        const objectName = nodes.find(node => node.id === structured.object_key)?.name || structured.object_key || data.subject || '';
        const text = item.event_type === 'GAME_OBJECT_STATE_CHANGED'
          ? `${objectName}：${structured.after?.state || '默认状态'}`
          : structured.description || data.description || [data.subject, data.predicate, data.object].filter(Boolean).join(' ') || item.event_type || '世界事件';
        return {type: item.event_type === 'GAME_OBJECT_STATE_CHANGED' ? '物品状态' : '世界事件', text, detail: data};
      }),
    ];
    $('timelineStreamMeta').textContent = `Step ${step.step_no} · ${facts.length} 条`;
    $('timelineStreamItems').innerHTML = facts.length ? facts.map(item => `<div class="stream-item"><time class="stream-time">${escapeHtml(item.type)}</time><div class="stream-copy"><span>${escapeHtml(item.text)}</span>${item.detail ? `<details><summary>查看事实详情</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto">${escapeHtml(JSON.stringify(item.detail, null, 2))}</pre></details>` : ''}</div></div>`).join('') : '<div class="empty-state"><strong>当前步骤没有可见事件</strong></div>';
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
      state.selectedReplayExperimentId = null;
      $('replayAgentSelect').value = '';
      $('replayCameraMode').value = 'free';
      state.replayPlayer?.followAgent(null);
      clearReplayInspector();
      renderReplayAgentRoster();
      return;
    }
    if (!fact || !step) {
      const key = payload.selectedAgentKey;
      const definition = payload.definition
        || (state.replayAgentDefinitions || []).find(item => item.agent_key === key);
      state.selectedReplayAgentKey = key;
      state.selectedReplayExperimentId = state.currentRun?.experiment_id || null;
      if ([...$('replayAgentSelect').options].some(option => option.value === key)) {
        $('replayAgentSelect').value = key;
      }
      $('replayCameraMode').value = 'follow';
      state.replayPlayer?.followAgent(key);
      renderReplayAgentRoster();
      $('replayInspectorLocation').textContent = `${definition?.display_name || key} · 正在加载当前 Step 事实…`;
      $('replayInspectorAction').textContent = '等待事实窗口';
      $('replayInspectorCurrently').textContent = '—';
      $('replayInspectorConversation').textContent = '—';
      $('replayInspectorMemories').textContent = '—';
      $('replayInspectorSchedule').textContent = '—';
      return;
    }
    const key = fact.agent_key;
    state.selectedReplayAgentKey = key;
    state.selectedReplayExperimentId = state.currentRun?.experiment_id || null;
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
    const active = ['STARTING', 'RUNNING', 'PAUSE_REQUESTED', 'CANCEL_REQUESTED']
      .includes(state.currentRun?.status);
    $('modelUsageRows').innerHTML = header + (rows.length
      ? rows.map(item => `<div class="usage-row"><strong>${escapeHtml(item.purpose)}</strong><code title="逻辑调用 / 物理请求">${item.logical_calls} / ${item.physical_attempts}</code><span>${item.max_latency_ms} ms</span><span>${item.retries}</span></div>`).join('')
      : `<div class="diagnostic-list-empty">${active ? '当前 Step 尚未提交用途汇总；右侧物理请求是实时数据' : '本次仿真没有已提交的模型调用'}</div>`);
    $('modelUsagePagination').innerHTML = pagination.html;
  }

  function artifactSourceMarkup(item) {
    if (item.provenance_status !== 'RECORDED' || !Number.isInteger(item.source_step)
      || typeof item.partial !== 'boolean' || !item.source_status || !item.generated_at) {
      return '来源未知 · 未记录生成时的 Step、状态与时间';
    }
    const status = statusLabels[item.source_status] || item.source_status;
    return `Step ${item.source_step} / ${item.source_total_steps} · ${item.partial ? '部分结果' : '最终结果'} · 取材时${escapeHtml(status)} · 生成于 ${systemTimeMarkup(item.generated_at)}`;
  }

  function renderOperations(operations) {
    state.modelUsageItems = operations.model_usage || [];
    const usageStep = Number(operations.usage_committed_through_step || 0);
    const usageWatermark = $('modelUsageWatermark');
    if (usageWatermark) {
      usageWatermark.textContent = operations.usage_consistency === 'RUN_TRACE_EVENTS' ? '按 Run 调用轨迹汇总已完成的逻辑调用和物理尝试；进行中的请求见右侧' : `已提交至 Step ${usageStep}；右侧物理请求实时刷新`;
    }
    renderModelUsage();
    const activeJobs = (operations.artifact_jobs || []).filter(item => item.status !== 'SUCCEEDED');
    const jobRows = activeJobs.map(item => `<div class="artifact-result"><span class="artifact-result-icon">◌</span><div><strong>${escapeHtml(item.type)}</strong><span>${escapeHtml(item.status)} · ${Math.round((item.progress || 0) * 100)}%${item.error_summary ? ` · ${escapeHtml(item.error_summary)}` : ''}</span></div><span class="chip ${item.status === 'FAILED' ? 'amber' : 'teal'}">${escapeHtml(item.status)}</span></div>`).join('');
    const artifactRows = operations.artifacts.map(item => `<div class="artifact-result"><span class="artifact-result-icon">▣</span><div><strong>${escapeHtml(item.logical_name)}</strong><span>${Math.ceil(item.size_bytes / 1024)} KB · ${escapeHtml(item.type)} · ${escapeHtml(item.sha256.slice(0, 12))}…</span><span>${artifactSourceMarkup(item)}</span></div><a class="artifact-action" href="/api/studio/runs/${state.selectedRunId}/artifacts/${item.artifact_id}/download">下载</a></div>`).join('');
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
    const source = {
      closed: false,
      timer: null,
      close() {
        this.closed = true;
        if (this.timer) clearTimeout(this.timer);
        this.timer = null;
      },
    };
    state.logSource = source;
    const poll = async () => {
      if (source.closed || generation !== state.logGeneration || runId !== state.selectedRunId || attemptId !== state.selectedAttemptId) return;
      try {
        const page = await api(`/runs/${runId}/attempts/${attemptId}/log?cursor=${state.logCursor}&limit_bytes=65536`);
        if (source.closed || generation !== state.logGeneration || runId !== state.selectedRunId || attemptId !== state.selectedAttemptId) return;
        state.logCursor = page.next_cursor;
        state.logFileId = page.file_id;
        consumeLogPage(page);
        if (page.eof && page.terminal) {
          closeLogStream();
          return;
        }
      } catch (error) {
        if (!source.closed) console.warn('日志文件轮询暂时失败，将继续重试。', error);
      }
      if (!source.closed) source.timer = setTimeout(poll, 1000);
    };
    source.timer = setTimeout(poll, 1000);
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
    $('logDownload').href = `/api/studio/runs/${runId}/attempts/${attemptId}/log/download`;
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
      if (state.traceDetailState?.runId !== runId || state.traceDetailState?.attemptId !== attemptId) {
        $('modelTraceDetail').hidden = true;
        $('tracePayloadMore').hidden = true;
        state.traceDetailState = null;
      }
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
    if (state.traceDetailState !== current || current.runId !== state.selectedRunId) return;
    current.content = append ? current.content + (detail.content || '') : (detail.content || '');
    current.cursor = detail.next_cursor;
    current.fileId = detail.file_id;
    current.trace = detail.trace;
    current.payloadAvailable = detail.payload_available;
    $('modelTraceDetail').hidden = false;
    $('modelTraceDetail').innerHTML = `<strong>${escapeHtml(detail.trace.purpose || detail.trace.event_type || '模型调用')}</strong><pre>${escapeHtml(JSON.stringify(detail.trace, null, 2))}</pre>${detail.payload_available ? `<strong>Payload（已脱敏）</strong><pre>${escapeHtml(current.content)}</pre>` : `<p>${escapeHtml(detail.payload_diagnostic || '该记录未保存 Payload。')}</p>`}`;
    if (detail.iteration_tools?.length) $('modelTraceDetail').innerHTML += `<strong>同 Agent / 同 Step 的 MCP 调用记录（非单次模型请求关联）</strong><pre>${escapeHtml(JSON.stringify(detail.iteration_tools, null, 2))}</pre>`;
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
    $('checkpointRows').innerHTML = header + (items.length ? items.map(item => `<button class="checkpoint-row" type="button" data-checkpoint-step="${item.step_no}"><code>${item.step_no}</code><span class="chip ${item.validated ? 'teal' : 'amber'}">${escapeHtml(item.status)}</span><code>${escapeHtml((item.attempt_id || '—').slice(0, 8))}</code><span><code>${escapeHtml((item.bundle_sha256 || '—').slice(0, 12))}</code><br>${formatTime(item.virtual_time)}</span><span>${Math.ceil(item.size_bytes / 1024)} KB · ${item.file_count} 文件</span><span>${item.resumable ? '<strong>可恢复</strong>' : escapeHtml(item.recovery_reason || item.validation?.reason || item.validation?.code || '—')}</span></button>`).join('') : '<div class="diagnostic-list-empty">当前仿真尚无检查点</div>');
    $('checkpointPagination').innerHTML = pagination.html;
  }

  async function showCheckpointDetail(runId, stepNo) {
    const request = state.checkpointDetailRequest = (state.checkpointDetailRequest || 0) + 1;
    const generation = state.resultGeneration;
    $('checkpointDetail').hidden = false;
    $('checkpointDetailGrid').textContent = `正在读取 Step ${stepNo} 检查点…`;
    $('checkpointDetail').scrollIntoView({ block: 'nearest' });
    let detail;
    try { detail = await api(`/runs/${runId}/checkpoints/${stepNo}`); }
    catch (error) {
      if (request === state.checkpointDetailRequest && runId === state.selectedRunId && generation === state.resultGeneration) $('checkpointDetailGrid').textContent = `检查点读取失败：${error.message}`;
      throw error;
    }
    if (request !== state.checkpointDetailRequest || generation !== state.resultGeneration || runId !== state.selectedRunId || detail.run_id !== runId) return;
    state.selectedCheckpointDetail = detail;
    $('checkpointDetail').hidden = false;
    state.checkpointPreviewState = null;
    $('checkpointPreview').hidden = true;
    $('checkpointPreviewMore').hidden = true;
    const agentRows = detail.agent_state.items.map(item => `<li><strong>${escapeHtml(item.agent_key)}</strong> · 坐标 ${escapeHtml(JSON.stringify(item.coord))} · ${escapeHtml(item.currently || '无当前状态')}<br>${escapeHtml(item.action?.event?.describe || item.action?.description || '无动作')} @ ${escapeHtml((item.action?.event?.address || item.action?.address || []).join(' / ') || '—')} · 日程 ${item.schedule_item_count} 项</li>`).join('') || '<li>无 Agent 状态</li>';
    const conversationRows = detail.conversations.items.map(item => `<li><strong>${escapeHtml((item.participants || []).join(' ↔ '))}</strong><br>${(item.messages || []).map(message => `${escapeHtml(message.speaker || message.speaker_agent_key || '')}: ${escapeHtml(message.content || '')}`).join('<br>') || '无消息'}</li>`).join('') || '<li>无对话</li>';
    const storageRows = detail.storage.groups.map(item => `<li><strong>${escapeHtml(item.agent_key)}</strong> / ${escapeHtml(item.index_type)} · ${item.file_count} 文件 · ${item.size_bytes} bytes</li>`).join('') || '<li>无存储快照</li>';
    const fileRows = detail.files.map(item => `<li><code>${escapeHtml(item.path)}</code> · ${item.size_bytes} bytes · ${escapeHtml((item.sha256 || '').slice(0, 12))}</li>`).join('');
    $('checkpointDetailGrid').innerHTML = `
      <div><strong>恢复操作</strong><p>${escapeHtml(detail.recovery_reason || `将从 Step ${detail.step_no + 1} 继续，保留原 Run 并创建新 Attempt。`)}</p><button class="btn btn-primary" data-checkpoint-resume ${detail.resumable ? '' : 'disabled'}>确认恢复此仿真…</button></div>
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

  function runOperationRefresh(runId, resultGeneration, operation) {
    const active = state.operationRefreshInFlight;
    if (active?.runId === runId && active.generation === resultGeneration) return active.promise;
    const pending = { runId, generation: resultGeneration };
    pending.promise = operation().finally(() => {
      if (state.operationRefreshInFlight === pending) state.operationRefreshInFlight = null;
    });
    state.operationRefreshInFlight = pending;
    return pending.promise;
  }

  function refreshOperationFacts(runId, resultGeneration) {
    return runOperationRefresh(runId, resultGeneration, () => refreshOperationFactsUnlocked(runId, resultGeneration));
  }

  async function refreshOperationFactsUnlocked(runId, resultGeneration) {
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

  function loadOperationsWorkspace(runId, resultGeneration) {
    return runOperationRefresh(runId, resultGeneration, () => loadOperationsWorkspaceUnlocked(runId, resultGeneration));
  }

  async function loadOperationsWorkspaceUnlocked(runId, resultGeneration) {
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
    const formRevisionId = $('overviewLegacyDefinitionFields')?.dataset.revisionId || '';
    if (formRevisionId && formRevisionId !== state.draft.id) {
      throw new Error('实验配置已经更新，请重新载入后再保存');
    }
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
      credential_env: oldChat.credential_env || null,
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
      credential_env: oldEmbedding.credential_env || null,
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
    const saved = await api(`/experiments/${state.selectedExperimentId}`, {
      method: 'PUT', body: JSON.stringify({ definition, expected_content_sha256: state.draft?.definition_hash }),
    });
    await acceptSavedDraft(saved);
    if (!silent) showToast('草稿已保存到当前实验，不影响其他实验。', '保存成功');
    return saved;
  }

  function saveDraft(options = {}) {
    return enqueueDraftMutation(() => saveDraftUnlocked(options));
  }

  $('applyExperimentModelChoices').addEventListener('click', async () => {
    if (!state.draft) return;
    const experimentId = state.selectedExperimentId;
    const chatId = $('experimentChatModelChoice').value;
    const embeddingId = $('experimentEmbeddingModelChoice').value;
    if (!chatId || !embeddingId) { showToast('请选择聊天和 Embedding 模型。', '无法应用'); return; }
    try {
      const [chat, embedding] = await Promise.all([api(`/resources/model-presets/${chatId}`), api(`/resources/model-presets/${embeddingId}`)]);
      if (state.selectedExperimentId !== experimentId || !state.draft) return;
      if (!chat.config.chat || !embedding.config.embedding) throw new Error('所选模型类型不匹配，请重新选择。');
      state.draft.definition.models = {chat: structuredClone(chat.config.chat), embedding: structuredClone(embedding.config.embedding)};
      fillModelFields(state.draft.definition.models);
      await saveDraft();
    } catch (error) { reportError(error); }
  });

  async function createExperiment() {
    const brainResourceId = $('newExperimentBrain').value;
    const brainSkill = $('newExperimentBrain').selectedOptions[0]?.dataset.skillName;
    if (!brainResourceId || !brainSkill) throw new Error('新实验必须选择一个 Brain Skill');
    const mapId = $('newExperimentMap').value;
    if (!mapId) throw new Error('新实验必须选择一张地图');
    const modelPresetId = $('newExperimentChatModel').value;
    const embeddingModelPresetId = $('newExperimentEmbeddingModel').value;
    if (!modelPresetId || !embeddingModelPresetId) throw new Error('请选择已配置的聊天和 Embedding 模型；可在模型中心添加。');
    const created = await api('/experiments', {
      method: 'POST',
      body: JSON.stringify({
        name: $('newExperimentName').value.trim(),
        goal: $('newExperimentGoal').value.trim(),
        owner: $('newExperimentOwner').value.trim(),
        tags: $('newExperimentTag').value.split(/[,，]/).map(item => item.trim()).filter(Boolean),
        map_id: mapId,
        brain_skill_id: brainResourceId,
        model_preset_id: modelPresetId,
        embedding_model_preset_id: embeddingModelPresetId,
        crowd_ids: window.CrowdWorkspace?.selectedCreateRevisionIds?.() || [],
      }),
    });
    closeModal('createModal', { restoreFocus: false });
    await loadExperiments({fresh: true});
    await openExperiment(created.experiment_id);
    showToast('独立实验草稿已创建。', '实验已创建');
  }

  function setDuplicateStatus(message, stateName = 'working') {
    const status = $('duplicateStatus');
    if (!status) return;
    status.hidden = !message;
    status.textContent = message || '';
    status.dataset.state = stateName;
  }

  function clearDuplicateFeedback() {
    state.duplicateFeedbackGeneration += 1;
    state.duplicateExperimentId = null;
    setDuplicateStatus('');
    const retry = $('duplicateRetryBtn');
    if (retry) retry.hidden = true;
  }

  async function duplicateExperiment(experimentId) {
    if (!experimentId || state.duplicateInProgress) return;
    const feedbackGeneration = ++state.duplicateFeedbackGeneration;
    const ownsFeedback = () => feedbackGeneration === state.duplicateFeedbackGeneration;
    state.duplicateInProgress = true;
    state.duplicateExperimentId = experimentId;
    const button = $('cloneBtn');
    const retry = $('duplicateRetryBtn');
    if (button) button.disabled = true;
    if (retry) retry.hidden = true;
    setDuplicateStatus('正在复制实验包，请稍候…');
    showToast('正在复制实验包，请稍候…', '实验复制中', { record: false });
    try {
      await new Promise(resolve => requestAnimationFrame(resolve));
      if (!ownsFeedback()) return;
      setDuplicateStatus('正在复制地图素材…');
      const created = await api(`/experiments/${experimentId}/duplicate`, {
        method: 'POST', body: JSON.stringify({}),
      });
      const name = created.name || '新实验草稿';
      // 已发出的复制仍可完成，但离开原上下文后只保留操作记录，不抢回页面。
      const retainBackgroundResult = () => {
        if (ownsFeedback()) return false;
        recordOperation('实验已复制', `已创建“${name}”草稿`);
        return true;
      };
      if (retainBackgroundResult()) return;
      setDuplicateStatus('正在复制 Agent 与 Skill…');
      await new Promise(resolve => requestAnimationFrame(resolve));
      if (retainBackgroundResult()) return;
      setDuplicateStatus('正在创建实验清单…');
      await loadExperiments({fresh: true});
      if (retainBackgroundResult()) return;
      await openExperiment(created.experiment_id, 'overview', null, { duplicateFeedbackGeneration: feedbackGeneration });
      if (retainBackgroundResult()) return;
      setDuplicateStatus(`已完成：${name}`, 'success');
      showToast(`已创建“${name}”草稿`, '实验已复制');
    } catch (error) {
      if (!ownsFeedback()) {
        recordOperation('实验复制失败', error.message || '复制实验失败，请重试。', 'error');
        return;
      }
      setDuplicateStatus(`复制失败：${error.message || '请稍后重试'}`, 'error');
      if (retry) retry.hidden = false;
      showToast(error.message || '复制实验失败，请重试。', '实验复制失败', { level: 'error' });
      throw error;
    } finally {
      state.duplicateInProgress = false;
      setWorkspaceMode();
    }
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
    state.agentSpriteLayout = agent.sprite_layout || '4x4';
    $('agentPortraitFile').value = '';
    $('agentSpriteFile').value = '';
    $('agentEditPortrait').value = agent.portrait_asset_id || agent.portrait_asset || '';
    $('agentEditSprite').value = agent.sprite_asset_id || agent.sprite_asset || '';
    const publicOwner = state.agentEditorContext?.ownerType?.startsWith('public');
    const imageUrl = (kind, assetId, logicalPath) => {
      if (publicOwner && assetId) return `/api/studio/resources/assets/${encodeURIComponent(assetId)}/content`;
      if (!publicOwner && String(logicalPath || '').startsWith('assets/') && state.selectedExperimentId) {
        return `/api/studio/experiments/${encodeURIComponent(state.selectedExperimentId)}/assets/${logicalPath.slice(7)}`;
      }
      return logicalPath || '';
    };
    const portraitUrl = imageUrl('portrait', agent.portrait_asset_id, agent.portrait_asset);
    const spriteUrl = imageUrl('sprite', agent.sprite_asset_id, agent.sprite_asset);
    const savedLabel = publicOwner ? '已保存到公共 Agent' : '已复制进当前实验';
    setAgentImagePreview('portrait', portraitUrl, portraitUrl ? savedLabel : '请选择头像');
    setAgentImagePreview('sprite', spriteUrl, spriteUrl ? `${savedLabel} · ${state.agentSpriteLayout}` : '请选择 4×3 或 4×4 行走图');
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
      if (kind === 'sprite' && !((width === 96 || width === 128) && height === 128)) throw new Error('行走图必须是 96×128（4×3）或 128×128（4×4）PNG，每格 32×32');
      if (kind === 'sprite') state.agentSpriteLayout = width === 96 ? '4x3' : '4x4';
      if (state.agentImageObjectUrls[kind]) URL.revokeObjectURL(state.agentImageObjectUrls[kind]);
      state.agentImageObjectUrls[kind] = objectUrl;
      state.agentImageFiles[kind] = file;
      const target = state.agentEditorContext?.ownerType?.startsWith('public') ? '公共 Agent' : '当前实验';
      setAgentImagePreview(kind, objectUrl, `${file.name} · ${width}×${height} · 保存时写入${target}`, { staged: true });
    } catch (error) {
      URL.revokeObjectURL(objectUrl);
      throw error;
    }
  }

  async function uploadStagedAgentImages() {
    const staged = state.agentImageFiles;
    if (!staged.portrait && !staged.sprite) {
      return { portrait: $('agentEditPortrait').value || null, sprite: $('agentEditSprite').value || null, sprite_layout: state.agentSpriteLayout };
    }
    const form = new FormData();
    if (staged.portrait) form.append('portrait', staged.portrait, staged.portrait.name);
    if (staged.sprite) form.append('sprite', staged.sprite, staged.sprite.name);
    const publicOwner = state.agentEditorContext?.ownerType?.startsWith('public');
    if (!publicOwner && state.draft?.definition_hash) form.append('expected_content_sha256', state.draft.definition_hash);
    const uploadExperimentId = state.selectedExperimentId;
    const endpoint = publicOwner
      ? '/api/studio/resources/agent-images'
      : `/api/studio/experiments/${encodeURIComponent(state.selectedExperimentId)}/agent-images`;
    const response = await fetch(endpoint, { method: 'POST', body: form });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new Error(payload?.detail || payload?.error?.message || `Agent 图片上传失败（${response.status}）`);
    }
    const uploaded = await response.json();
    if (!publicOwner) {
      if (uploadExperimentId !== state.selectedExperimentId) throw new Error('当前实验已切换，请重新打开 Agent');
      state.draft.definition_hash = uploaded.content_sha256;
    }
    const images = {
      portrait: (publicOwner ? uploaded.portrait?.asset_id : uploaded.portrait?.logical_path) || $('agentEditPortrait').value || null,
      sprite: (publicOwner ? uploaded.sprite?.asset_id : uploaded.sprite?.logical_path) || $('agentEditSprite').value || null,
      sprite_layout: uploaded.sprite ? (uploaded.sprite.width === 96 ? '4x3' : '4x4') : state.agentSpriteLayout,
    };
    state.agentSpriteLayout = images.sprite_layout;
    for (const kind of ['portrait', 'sprite']) {
      if (!uploaded[kind]?.content_url) continue;
      const prefix = kind === 'portrait' ? 'Portrait' : 'Sprite';
      $(`agentEdit${prefix}`).value = images[kind];
      setAgentImagePreview(kind, uploaded[kind].content_url, publicOwner ? '已保存到公共 Agent' : '已复制进当前实验');
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

  function readAgentSpriteDisplayTiles() {
    const raw = String($('agentEditSpriteDisplayTiles')?.value || '').trim();
    if (!raw) return null;
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0.5 || value > 6) {
      throw new Error('地图显示大小必须在 0.5 到 6 格之间，或留空沿用系统默认');
    }
    return Math.round(value * 10) / 10;
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
    $('agentEditSpriteDisplayTiles').value = agent.sprite_display_tiles == null ? '' : String(agent.sprite_display_tiles);
    $('agentEditVisionRadius').value = agent.perception?.vision_radius ?? 8;
    $('agentEditAttentionBandwidth').value = agent.perception?.attention_bandwidth ?? 8;
    renderSpatialEditor(agent.spatial || { address: {}, tree: {} });
    document.querySelector('[data-content-tab="space"]').hidden = false;
    setContentTab('agent-editor', 'identity', { sync: false });
  }

  function openAgentEditor(agentKey = null) {
    if (!state.draft) {
      const agent = state.definition?.agents?.find(item => item.agent_key === agentKey);
      if (!agent) throw new Error('封存实验中没有该 Agent');
      state.agentEditorContext = { ownerType: 'experiment-readonly' };
      $('agentEditorTitle').textContent = `查看 ${agent.name}`;
      $('agentEditorKeyMeta').textContent = `文件键：${agent.agent_key}`;
      $('agentEditorContextHelp').textContent = '当前封存实验的人物原始配置，只读。';
      fillSharedAgentEditor(agent);
      setAgentEditorReadOnly(true);
      openModal('agentEditorModal', 'closeAgentEditor');
      return;
    }
    const existing = agentKey ? state.draft.definition.agents.find(item => item.agent_key === agentKey) : null;
    const used = new Set(state.draft.definition.agents.map(item => item.agent_key));
    let index = state.draft.definition.agents.length + 1;
    while (used.has(`resident-${String(index).padStart(3, '0')}`)) index += 1;
    const agent = existing || {
      agent_key: `resident-${String(index).padStart(3, '0')}`, enabled: true, name: '', portrait_asset: null,
      sprite_asset: null,
      sprite_layout: '4x4',
      sprite_display_tiles: null,
      coord: [0, 0], currently: '', scratch: { age: 30, innate: '', learned: '', lifestyle: '', daily_plan: '' },
      spatial: { address: {}, tree: {} },
      perception: { mode: 'box', vision_radius: 8, attention_bandwidth: 8 },
    };
    state.agentEditorContext = { ownerType: 'experiment' };
    state.editingAgentKey = existing?.agent_key || null;
    $('agentEditorTitle').textContent = existing ? `编辑 ${agent.name}` : '新增 Agent';
    $('agentEditorKeyMeta').textContent = `文件键：${agent.agent_key}`;
    $('agentEditorContextHelp').textContent = '保存到当前实验 Draft；文件键用于历史结果关联，创建后不可修改。';
    $('saveAgentEditor').textContent = '保存 Agent';
    fillSharedAgentEditor(agent, Boolean(existing));
    setAgentEditorReadOnly(false);
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
      portrait_asset_id: null,
      sprite_asset_id: null,
      sprite_layout: '4x4',
      sprite_display_tiles: null,
      model_override: null,
      tags: [],
      goals: [],
      currently: '',
      scratch: { age: 30, innate: '', learned: '', lifestyle: '', daily_plan: '' },
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
    $('agentEditorContextHelp').textContent = '保存为公共 Agent 定义；地图坐标与初始空间只在导入实验时配置。';
    fillSharedAgentEditor(agent, Boolean(existing));
    document.querySelector('[data-content-tab="space"]').hidden = true;
    setAgentEditorReadOnly(false);
    $('saveAgentEditor').textContent = '保存 Agent';
    openModal('agentEditorModal', 'agentEditName');
  }

  async function openPublicAgentReadOnly({ agentDetail, agentRevision } = {}) {
    const agent = agentRevision?.definition;
    if (!agent) throw new Error('无法读取 Agent 定义');
    releaseAgentImageObjectUrls();
    state.agentEditorContext = {
      ownerType: 'public-readonly',
      agentDetail,
      agentRevision,
      definition: structuredClone(agent),
    };
    state.editingAgentKey = null;
    $('agentEditorTitle').textContent = `查看 ${agent.name}`;
    $('agentEditorKeyMeta').textContent = `模板键：${agent.agent_key}`;
    $('agentEditorContextHelp').textContent = '当前公共 Agent 定义；导入实验后使用实验内的物理副本。';
    fillSharedAgentEditor(agent);
    document.querySelector('[data-content-tab="space"]').hidden = true;
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
    if (state.agentEditorContext?.ownerType?.endsWith('readonly')) return;
    if (state.agentEditorContext?.ownerType === 'public') {
      const context = state.agentEditorContext;
      const spriteDisplayTiles = readAgentSpriteDisplayTiles();
      const images = await uploadStagedAgentImages();
      const previous = context.definition || {};
      const definition = {
        agent_key: $('agentEditKey').value.trim(),
        enabled: previous.enabled ?? true,
        name: $('agentEditName').value.trim(),
        portrait_asset_id: previous.portrait_asset_id || images.portrait || null,
        sprite_asset_id: previous.sprite_asset_id || images.sprite || null,
        sprite_layout: images.sprite_layout,
        sprite_display_tiles: spriteDisplayTiles,
        model_override: previous.model_override || null,
        tags: previous.tags || [],
        goals: $('agentEditGoals').value.split(/\r?\n/).map(item => item.trim()).filter(Boolean),
        currently: $('agentEditCurrently').value,
        scratch: {
          age: Number($('agentEditAge').value),
          innate: $('agentEditInnate').value,
          learned: $('agentEditLearned').value,
          lifestyle: $('agentEditLifestyle').value,
          daily_plan: $('agentEditDailyPlan').value,
        },
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
      throw new Error('新增 Agent 需要同时上传头像和 4×3 或 4×4 行走图');
    }
    const spriteDisplayTiles = readAgentSpriteDisplayTiles();
    const images = await uploadStagedAgentImages();
    const previous = state.editingAgentKey ? state.draft.definition.agents.find(item => item.agent_key === state.editingAgentKey) : null;
    const agent = {
      agent_key: key,
      enabled: previous?.enabled ?? true,
      name: $('agentEditName').value.trim(),
      portrait_asset: images.portrait,
      sprite_asset: images.sprite,
      sprite_layout: images.sprite_layout,
      sprite_display_tiles: spriteDisplayTiles,
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
    const agents = state.draft.definition.agents;
    const targetIndex = state.editingAgentKey
      ? agents.findIndex(item => item.agent_key === state.editingAgentKey)
      : -1;
    if (agents.some((item, index) => item.agent_key === key && index !== targetIndex)) {
      throw new Error(`Agent 文件键已存在：${key}`);
    }
    if (targetIndex >= 0) agents[targetIndex] = agent;
    else agents.push(agent);
    if (state.editingAgentKey && state.editingAgentKey !== key) {
      (state.draft.definition.crowds || []).forEach(crowd => {
        crowd.agent_keys = crowd.agent_keys.map(member => member === state.editingAgentKey ? key : member);
      });
    }
    await saveDraft({ silent: true });
    if (state.workspacePage === 'crowds' && window.CrowdWorkspace?.detail?.id) {
      await window.CrowdWorkspace.openCrowd(window.CrowdWorkspace.detail.id, false);
    }
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
      state.draft.definition.agents = state.draft.definition.agents.filter(agent => !requestedKeys.includes(agent.agent_key));
      (state.draft.definition.crowds || []).forEach(crowd => {
        crowd.agent_keys = crowd.agent_keys.filter(key => !requestedKeys.includes(key));
      });
      saved = await saveDraft({ silent: true });
      deletedKeys.push(...requestedKeys);
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
    if (state.launchingRun) return;
    if (state.launchExperimentId && state.launchExperimentId !== state.selectedExperimentId) throw new Error('实验已切换，请重新确认执行');
    state.launchingRun = true;
    const experimentId = state.selectedExperimentId;
    try {
      if (state.draft) {
        await saveDraft({ silent: true });
        await api(`/experiments/${experimentId}/seal`, { method: 'POST' });
      }
      const run = await api(`/experiments/${experimentId}/runs`, { method: 'POST' });
      if (experimentId !== state.selectedExperimentId) {
        showToast(`实验 ${experimentId} 已创建仿真 ${run.run_id}。`, '实验已执行');
        return;
      }
      closeModal('publishModal', { restoreFocus: false });
      state.latestRunId = run.run_id;
      state.selectedRunId = run.run_id;
      state.currentRun = run;
      goToPage('results');
      showToast(`仿真已创建：${run.run_id}，正在刷新运行状态。`, '实验已执行');
      try {
        await syncSelectedExperiment({ refreshDefinition: true, refreshOverview: true });
        await loadRunHistory(state.selectedExperimentId, run.run_id);
      } catch (error) {
        showToast(`仿真 ${run.run_id} 已创建，但状态刷新失败：${error.message}。请重新打开该仿真查看进度。`, '状态刷新失败', { level: 'error' });
      }
    } finally { state.launchingRun = false; }
  }

  async function prepareNextSimulation() {
    return openPublishModal();
  }

  async function createResultBundle() {
    if (!state.selectedRunId) throw new Error('请先选择一次仿真');
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
    if (!state.selectedRunId) throw new Error('请先选择一次仿真');
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
    if (!state.selectedRunId) throw new Error('请先选择一次仿真');
    const runId = state.selectedRunId;
    const experimentId = state.selectedExperimentId;
    const generation = state.resultGeneration;
    const body = action === 'resume' && options.checkpointStep
      ? JSON.stringify({ checkpoint_step: options.checkpointStep, expected_attempt_id: options.attemptId || null })
      : action === 'cancel'
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
      try {
        await Promise.all([
          syncSelectedExperiment({ refreshOverview: true }),
          refreshRunHistoryList(state.selectedExperimentId, state.selectedRunId),
        ]);
      } catch (error) {
        showToast(`操作已提交，但状态刷新失败：${error.message}。请重新打开当前 Run 查看，勿重复提交。`, '状态刷新失败', { level: 'error' });
      }
      scheduleResultRefresh(runId, generation);
    } else {
      scheduleGlobalReconcile({ experimentId });
    }
    showToast(
      action === 'pause' ? '会在当前安全步骤完成后暂停。' : action === 'resume' ? '仿真已重新进入本机队列。' : '正在立即终止当前执行；未提交的当前 Step 将被丢弃。',
      action === 'pause' ? '暂停请求已提交' : action === 'resume' ? '继续仿真' : '取消仿真',
    );
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

  async function deleteExperimentById(experimentId, name) {
    const confirmed = await confirmResourceDeletion({
      type: '实验', name,
      message: '实验配置将被删除。若仍有仿真记录，请先在“实验结果”中逐一删除。',
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
    await loadExperiments({fresh: true});
    showToast(`实验“${name}”已删除。`, '删除完成');
  }

  async function deleteCurrentRun() {
    if (state.deletingRunId) return;
    const run = state.currentRun || state.runHistory.find(item => item.run_id === state.selectedRunId);
    if (!run) throw new Error('当前没有可删除的仿真');
    const confirmed = await confirmResourceDeletion({
      type: '仿真', name: `仿真 ${run.run_id.slice(0, 12)}`,
      message: '仿真事实、检查点、回放与制品记录将被删除；文件会移动到本机可恢复回收站。活动仿真必须先取消。',
    });
    if (!confirmed) return;
    const experimentId = state.selectedExperimentId;
    state.deletingRunId = run.run_id;
    const ownsSelection = state.selectedRunId === run.run_id;
    if (ownsSelection) resetResultRuntime();
    $('deleteRunBtn').disabled = true;
    $('deleteRunBtn').textContent = '正在移入回收站…';
    try {
      await api(`/runs/${encodeURIComponent(run.run_id)}`, { method: 'DELETE' });
    } catch (error) {
      state.deletingRunId = null;
      if (ownsSelection && state.selectedExperimentId === experimentId && !state.selectedRunId) {
        loadResults(run.run_id).catch(reportError);
      }
      throw error;
    } finally {
      state.deletingRunId = null;
      $('deleteRunBtn').disabled = false;
      $('deleteRunBtn').textContent = '删除仿真';
    }
    showToast('仿真已删除，关联文件已移入可恢复回收站。', '删除完成');
    try {
      if (state.selectedExperimentId === experimentId && !state.selectedRunId) await openExperiment(String(experimentId), 'results');
      else await loadExperiments({fresh: true});
    } catch (error) {
      showToast(`删除已完成，但列表刷新失败：${error.message}。请刷新列表。`, '列表刷新失败', { level: 'error' });
    }
  }

  async function batchArchiveSelected(action) {
    const ids = [...state.selectedExperimentIds];
    const result = await api('/experiments/batch', {
      method: 'POST', body: JSON.stringify({ experiment_ids: ids, action }),
    });
    state.selectedExperimentIds.clear();
    await loadExperiments({fresh: true});
    showToast(`${result.affected} 个实验已${action === 'ARCHIVE' ? '归档' : '恢复'}，仿真结果均保留。`, action === 'ARCHIVE' ? '归档完成' : '恢复完成');
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
    if (isGlobalPage(pageName)) {
      requestGlobalNavigation(pageName);
      return;
    }
    if (!state.selectedExperimentId) {
      showToast('请先从实验列表选择一个实验。', '尚未选择实验');
      return;
    }
    if (!['overview', 'results', 'maps', 'agents', 'crowds', 'skills', 'brains', 'models'].includes(pageName)) {
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
  window.addEventListener('experiment-resource:saved', () => {
    syncSelectedExperiment({ refreshDefinition: true }).catch(reportError);
  });
  window.ExperimentAgentEditor = { open: key => { closeModal('crowdAgentManagerModal'); return openAgentEditor(key); } };
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
  $('runQualityBanner').addEventListener('click', async event => {
    const target = event.target.closest('[data-quality-step]');
    if (!target) return;
    const step = Number(target.dataset.qualityStep);
    if (!Number.isInteger(step) || step < 1) return;
    setResultTab('timeline', { push: true });
    const runId = state.selectedRunId, generation = state.resultGeneration;
    try {
      const player = await ensureReplayPlayer(runId, generation);
      if (player && runId === state.selectedRunId && generation === state.resultGeneration) await player.seek(step);
    } catch (error) { reportError(error); }
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

  $('createExperimentBtn').addEventListener('click', async () => {
    state.wizardStep = 1;
    $('newExperimentName').value = '';
    $('newExperimentGoal').value = '';
    $('newExperimentTag').value = '';
    try {
      await Promise.all([
        prepareExperimentBrainChoices(),
        window.ModelWorkspace.loadChoices($('newExperimentChatModel'), 'chat'),
        window.ModelWorkspace.loadChoices($('newExperimentEmbeddingModel'), 'embedding'),
        window.MapWorkspace?.prepareExperimentCreate(),
        window.CrowdWorkspace?.prepareExperimentCreate({ resetSelection: true }),
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
    api(`/experiments/${state.selectedExperimentId}`).then(experiment => {
      const draft = experiment.current_draft;
      if (!draft) throw new Error('当前实验已经封存，不能丢弃到可编辑草稿');
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
    if (backdrop.id === 'agentEditorModal' && state.agentSaving) return;
    if (backdrop.id === 'agentEditorModal' && state.agentEditorContext?.ownerType?.startsWith('public')) {
      closeSharedAgentEditor();
      return;
    }
    closeModal(backdrop.id);
  }));
  $('agentEditorModal').addEventListener('keydown', event => {
    if (state.agentSaving && event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); return; }
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
      closeExperimentMenu();
      return;
    }
    if (event.key === 'Escape') closeExperimentMenu(true);
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
    } else if (event.target.closest('.api-open-experiment, [data-open-experiment]')) {
      event.stopImmediatePropagation();
      openExperiment(card.dataset.id).catch(reportError);
    }
  }, true);

  let contextExperimentId = null;
  let contextExperimentName = '';
  let contextExperimentTrigger = null;
  function closeExperimentMenu(restoreFocus = false) {
    const wasOpen = !$('experimentContextMenu').hidden;
    $('experimentContextMenu').hidden = true;
    contextExperimentTrigger?.setAttribute('aria-expanded', 'false');
    if (restoreFocus && wasOpen) contextExperimentTrigger?.focus();
  }
  $('experimentList').addEventListener('click', event => {
    const deleteButton = event.target.closest('.experiment-delete-button');
    if (deleteButton) {
      event.stopPropagation();
      const card = deleteButton.closest('.experiment-card');
      const experimentId = card?.dataset.id;
      const name = card?.querySelector('.experiment-link')?.textContent?.trim() || '当前实验';
      if (experimentId) deleteExperimentById(experimentId, name).catch(reportError);
      return;
    }
    const menu = event.target.closest('.experiment-menu');
    if (!menu) return;
    event.stopPropagation();
    const wasOpen = contextExperimentTrigger === menu && !$('experimentContextMenu').hidden;
    closeExperimentMenu();
    if (wasOpen) return;
    contextExperimentTrigger = menu;
    const card = menu.closest('.experiment-card');
    contextExperimentId = card?.dataset.id || null;
    contextExperimentName = card?.querySelector('.resource-name')?.textContent?.trim() || '当前实验';
    const contextMenu = $('experimentContextMenu');
    const archived = menu.closest('.experiment-card')?.dataset.archived === 'true';
    contextMenu.querySelector('[data-context-action="archive"]').hidden = archived;
    contextMenu.querySelector('[data-context-action="restore"]').hidden = !archived;
    contextMenu.hidden = !contextExperimentId;
    const rect = menu.getBoundingClientRect();
    contextMenu.style.left = `${Math.max(8, Math.min(rect.right - 180, window.innerWidth - 190))}px`;
    contextMenu.style.top = `${Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - 250))}px`;
    menu.setAttribute('aria-expanded', 'true');
    contextMenu.querySelector('button:not([hidden])')?.focus();
  });
  $('experimentContextMenu').addEventListener('click', event => {
    const action = event.target.dataset.contextAction;
    if (!action || !contextExperimentId) return;
    event.stopImmediatePropagation();
    closeExperimentMenu();
    if (action === 'open') openExperiment(contextExperimentId).catch(reportError);
    else if (action === 'results') openExperiment(contextExperimentId, 'results').catch(reportError);
    else if (action === 'duplicate') duplicateExperiment(contextExperimentId).catch(reportError);
    else if (action === 'archive' || action === 'restore') {
      api(`/experiments/${contextExperimentId}/${action}`, { method: 'POST', body: '{}' })
        .then(() => loadExperiments({fresh: true})).catch(reportError);
    }
    else if (action === 'delete') deleteExperimentById(contextExperimentId, contextExperimentName).catch(reportError);
  }, true);
  document.addEventListener('click', event => {
    if (!event.target.closest('#experimentContextMenu, .experiment-menu')) closeExperimentMenu();
  });

  $('experimentArchiveFilter').addEventListener('change', () => {
    state.archiveFilter = $('experimentArchiveFilter').value;
    state.page = 1;
    state.selectedExperimentIds.clear();
    loadExperiments().catch(reportError);
  });
  $('selectVisibleExperiments').addEventListener('click', () => {
    const allSelected = state.visibleExperimentIds.every(id => state.selectedExperimentIds.has(id));
    state.visibleExperimentIds.forEach(id => allSelected ? state.selectedExperimentIds.delete(id) : state.selectedExperimentIds.add(id));
    loadExperiments().catch(reportError);
  });
  $('archiveSelectedBtn').addEventListener('click', () => batchArchiveSelected('ARCHIVE').catch(reportError));
  $('restoreSelectedBtn').addEventListener('click', () => batchArchiveSelected('RESTORE').catch(reportError));
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
    event.stopImmediatePropagation();
    if (!state.selectedExperimentId || state.launchingRun || state.preparingLaunch) return;
    state.preparingLaunch = true;
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = '正在准备执行…';
    openPublishModal().catch(error => {
      $('publishLaunchStatus').textContent = `准备失败：${error.message}`;
      reportError(error);
    }).finally(() => {
      state.preparingLaunch = false;
      button.disabled = false;
      button.textContent = experimentHasRun() ? '执行下一次仿真' : '执行实验';
    });
  }, true);
  $('cloneBtn').addEventListener('click', event => {
    event.stopImmediatePropagation();
    duplicateExperiment(state.selectedExperimentId).catch(reportError);
  }, true);
  $('duplicateRetryBtn')?.addEventListener('click', event => {
    event.stopImmediatePropagation();
    duplicateExperiment(state.duplicateExperimentId || state.selectedExperimentId).catch(reportError);
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
      if ($('overviewResourceAgents')) $('overviewResourceAgents').textContent = `${enabled} 个 Agent`;
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
  $('saveAgentEditor').addEventListener('click', async event => {
    event.stopImmediatePropagation();
    if (state.agentSaving) return;
    state.agentSaving = true;
    const modal = $('agentEditorModal');
    const controls = [...modal.querySelectorAll('input, textarea, select, button')].map(control => [control, control.disabled]);
    controls.forEach(([control]) => { control.disabled = true; });
    modal.setAttribute('aria-busy', 'true');
    const started = Date.now();
    const progress = () => { $('agentSaveProgress').textContent = `正在保存 Agent，请稍候… 已等待 ${Math.floor((Date.now() - started) / 1000)} 秒`; };
    progress();
    $('saveAgentEditor').textContent = '正在保存…';
    const timer = setInterval(progress, 1000);
    try { await saveAgentEditor(); }
    catch (error) { reportError(error); }
    finally {
      clearInterval(timer);
      state.agentSaving = false;
      controls.forEach(([control, disabled]) => { control.disabled = disabled; });
      modal.removeAttribute('aria-busy');
      $('saveAgentEditor').textContent = '保存 Agent';
      $('agentSaveProgress').textContent = '';
    }
  }, true);
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
    if ($('overviewResourceAgents')) $('overviewResourceAgents').textContent = `${shouldEnable ? rows.length : 0} 个 Agent`;
    event.currentTarget.textContent = shouldEnable ? '取消全选' : '全部启用';
    markDirty();
  }, true);
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
      attemptId: state.selectedTraceAttemptId || state.selectedAttemptId,
      cursor: 0,
      fileId: null,
      content: '',
      generation: state.logGeneration,
    };
    $('modelTraceDetail').hidden = false;
    $('modelTraceDetail').textContent = '正在读取调用详情…';
    loadTraceDetail().catch(error => { $('modelTraceDetail').textContent = `读取失败：${error.message}`; reportError(error); });
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
    if (event.target.closest('[data-checkpoint-resume]')) {
      try { openResumeRunModal(state.selectedCheckpointDetail); } catch (error) { reportError(error); }
      return;
    }
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
      showToast('请选择一张地图。', '无法继续');
      $('newExperimentMap').focus();
      return;
    }
    if (state.wizardStep === 2 && (!$('newExperimentChatModel').value || !$('newExperimentEmbeddingModel').value)) {
      showToast('请选择聊天和 Embedding 模型。', '无法继续');
      return;
    }
    if (state.wizardStep === 2 && !$('newExperimentBrain').value) {
      showToast('请选择一个 Brain Skill。', '无法继续');
      $('newExperimentBrain').focus();
      return;
    }
    if (state.wizardStep === 2 && !(window.CrowdWorkspace?.selectedCreateRevisionIds?.().length)) {
      showToast('请至少选择一个公共人群。', '无法继续');
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
    event.stopImmediatePropagation();
    if (!state.selectedExperimentId) return;
    const button = event.currentTarget;
    button.disabled = true;
    if (state.launchingRun) return;
    button.textContent = '正在创建并启动新仿真…';
    $('publishLaunchStatus').textContent = '正在创建并启动新 Run，请勿重复提交…';
    publishAndRun().catch(async error => {
      $('publishLaunchStatus').textContent = `启动失败：${error.message}`;
      try {
        const experiment = await api(`/experiments/${state.selectedExperimentId}`);
        state.draft = experiment.current_draft;
        if (!state.draft) throw error;
        state.definition = state.draft.definition;
        const report = await refreshValidation();
        if (report && state.runEstimate) renderPublishValidation(report, state.runEstimate);
      } catch (_) {}
      reportError(error);
    }).finally(() => {
      button.disabled = !state.validationReport?.valid || Boolean(state.runEstimate?.high_scale && !document.getElementById('confirmHighScale')?.checked);
      button.textContent = '确认执行';
    });
  }, true);
  $('confirmResumeRun').addEventListener('click', event => {
    event.stopImmediatePropagation();
    const button = event.currentTarget;
    if (!state.pendingResumeRunId || state.pendingResumeRunId !== state.selectedRunId) {
      closeModal('resumeRunModal');
      reportError(new Error('当前选择的仿真已变更，请重新确认'));
      return;
    }
    button.disabled = true;
    button.textContent = '正在恢复…';
    $('resumeRunFeedback').textContent = '正在校验恢复边界并提交新 Attempt，请勿重复点击…';
    controlRun('resume', { checkpointStep: state.pendingResumeStep, attemptId: state.pendingResumeAttemptId }).then(() => {
      closeModal('resumeRunModal');
      state.pendingResumeRunId = null;
      state.pendingResumeStep = 0;
    }).catch(error => {
      $('resumeRunFeedback').textContent = `恢复失败：${error.message}`;
      reportError(error);
    }).finally(() => {
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
    if (state.workspacePage === 'experiments') {
      loadExperiments({silent: true}).catch(reportError);
      return;
    }
    scheduleGlobalReconcile({ full: true });
    if (!state.globalPollTimer) {
      startGlobalActivityStream().catch(error => console.warn('恢复全局状态流失败。', error));
    }
  }
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') reconcileAfterPageResume();
    else stopExperimentListRefresh();
  });
  window.addEventListener('focus', reconcileAfterPageResume);
  window.addEventListener('online', reconcileAfterPageResume);
  window.addEventListener('pageshow', reconcileAfterPageResume);
  window.addEventListener('beforeunload', () => {
    stopExperimentListRefresh();
    state.eventSource?.close();
    state.activitySource?.close();
    if (state.resultPollTimer) clearInterval(state.resultPollTimer);
    if (state.globalPollTimer) clearInterval(state.globalPollTimer);
    closeLogStream();
    state.operationsAbortController?.abort();
    teardownReplay();
  });

  async function bootstrapConsole() {
    const savedList = window.ResourceList?.read('experiments');
    if (savedList) {
      state.page = savedList.page;
      state.archiveFilter = savedList.archiveFilter || 'active';
      $('experimentArchiveFilter').value = state.archiveFilter;
    }
    const params = new URLSearchParams(location.search);
    const experimentId = window.ResourceScope?.experimentId || params.get('experiment_id');
    const requestedView = params.get('view');
    const targetPage = requestedView && ['overview', 'results', 'maps', 'agents', 'crowds', 'skills', 'brains', 'models'].includes(requestedView)
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
    if (!experimentId && (!requestedView || requestedView === 'experiments')) await loadExperiments();
    if (!experimentId && ['maps', 'public-agents', 'brains', 'crowds', 'skills', 'model-catalog'].includes(requestedView)) {
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
