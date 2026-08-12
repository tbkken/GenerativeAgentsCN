(() => {
  'use strict';

  const FLOW_ORDER = ['schedule', 'memory', 'action', 'social', 'reflection'];
  const KIND_LABELS = {
    start: '开始', end: '结束', llm: '大模型', code: '代码', selector: '选择器',
    variable_assigner: '变量赋值', variable_aggregator: '变量聚合', subflow: '子工作流',
    script: '代码（旧版）', if_else: '选择器（旧版）', switch: '选择器（旧版）',
    loop: '循环（旧版）', parallel: '并行（旧版）', read_state: '读取状态（旧版）',
    write_state: '写入状态（旧版）',
  };
  const BODY_LABELS = {
    llm: 'Prompt', code: 'Function', selector: '条件', variable_assigner: '变量',
    variable_aggregator: '聚合', subflow: '子流程', script: 'Function',
    if_else: '条件', switch: '规则', loop: '循环', read_state: '状态', write_state: '状态',
  };
  const DATA_TYPES = [
    'any', 'string', 'integer', 'number', 'boolean', 'object', 'array',
    'StepContext', 'ScheduleContext', 'MemoryContext', 'ActionContext',
    'SocialContext', 'ReflectionContext', 'AgentState', 'AgentProfileText',
    'PlanMemory[]', 'ThoughtMemory', 'WakeHour', 'ScheduleOutline',
    'DailySchedule', 'PlanContext', 'DecomposedPlan', 'ScheduleResult',
  ];
  const TYPE_PATHS = {
    StepContext: ['agent.name', 'agent.agent_key', 'clock.current_time', 'visible_events', 'memories', 'trigger'],
    ScheduleContext: ['agent.name', 'clock.current_time', 'memories', 'schedule.current_plan', 'trigger'],
    MemoryContext: ['agent.name', 'visible_events', 'memories'],
    ActionContext: ['agent.name', 'position', 'schedule.current_plan', 'spatial.available'],
    SocialContext: ['agent.name', 'nearby_agents', 'conversation.history'],
    ReflectionContext: ['agent.name', 'memories', 'conversation.history'],
    AgentState: ['currently'],
    DailySchedule: ['8:00'],
  };
  const INLINE_FUNCTION_TEMPLATE = `def main(inputs, context):
    value = inputs.get("input")
    return {"result": value}
`;
  const CANVAS_MIN_WIDTH = 3600;
  const CANVAS_MIN_HEIGHT = 2500;
  const NODE_WIDTH = 232;
  const HORIZONTAL_LAYER_GAP = 160;
  const VERTICAL_LAYER_GAP = 140;
  const NODE_STACK_GAP = 56;
  const CANVAS_PADDING_X = 260;
  const CANVAS_PADDING_Y = 180;
  const MIN_CANVAS_ZOOM = .1;
  const MAX_CANVAS_ZOOM = 2;
  const CANVAS_ZOOM_STEP = .1;
  const $ = id => document.getElementById(id);
  const editorState = {
    ownerType: 'experiment',
    ownerId: null,
    experimentId: null,
    draft: null,
    revision: null,
    readonly: true,
    active: false,
    list: [],
    currentKey: 'schedule',
    flows: new Map(),
    selectedNodeId: null,
    panel: 'contract',
    generation: 0,
    drag: null,
    functions: [],
    functionEditingKey: null,
    view: 'flow',
    pendingConnection: null,
    pan: null,
    viewports: new Map(),
    zooms: new Map(),
    layoutHistory: new Map(),
    layoutDirection: 'horizontal',
    searchQuery: '',
  };

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[char]));
  }

  function normalizePromptVariables(value) {
    return String(value || '').replace(/\$\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}/g, '{$1}');
  }

  function promptVariableAnalysis(node, content) {
    const inputNames = new Set(node.inputs.map(port => port.name));
    const unresolved = [];
    String(content).replace(/\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}/g, (_match, path) => {
      const root = path.split('.')[0];
      if (inputNames.has(root)) return _match;
      unresolved.push({ token: `{${path}}`, reason: '根变量未在当前 LLM Node 的输入端口中声明' });
      return _match;
    });
    return { unresolved };
  }

  function dataTypeOptions(value) {
    const values = DATA_TYPES.includes(value) ? DATA_TYPES : [...DATA_TYPES, value];
    return values.map(type => `<option value="${escapeHtml(type)}" ${type === value ? 'selected' : ''}>${escapeHtml(type)}</option>`).join('');
  }

  function readonlyValue(label, value, badge = '系统属性', help = '') {
    return `<div class="workflow-field"><label>${escapeHtml(label)}</label><div class="workflow-readonly-value"><span>${escapeHtml(value || '—')}</span><span class="workflow-meta-badge">${escapeHtml(badge)}</span></div>${help ? `<small class="workflow-field-help">${escapeHtml(help)}</small>` : ''}</div>`;
  }

  function ensureNodeConfig(node) {
    node.config ||= {};
    if (['code', 'script'].includes(node.kind)) {
      node.script_mode ||= node.script_source ? 'inline' : 'shared';
      if (node.script_mode === 'shared') node.operation ||= 'identity';
      if (node.script_mode === 'inline') node.script_source ||= INLINE_FUNCTION_TEMPLATE;
      return;
    }
    if (node.kind !== 'llm') return;
    node.config.response_schema ||= {
      type: 'object',
      properties: { res: { type: 'object', properties: { result: { type: 'string', description: '节点的结构化结果' } }, required: ['result'], additionalProperties: false } },
      required: ['res'],
      additionalProperties: false,
    };
    node.config.retry_policy ||= { max_attempts: 3, retry_on_schema_error: true };
  }

  async function api(path, options = {}) {
    const response = await fetch(`/api/v1${path}`, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const error = new Error(body?.error?.message || `请求失败（${response.status}）`);
      error.code = body?.error?.code;
      error.details = body?.error?.details;
      error.requestId = body?.error?.request_id || response.headers.get('X-Request-ID');
      error.status = response.status;
      error.path = path;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }

  function notify(message, title = '操作成功') {
    window.dispatchEvent(new CustomEvent('workflow-editor:toast', { detail: { message, title } }));
  }

  function report(error) {
    window.dispatchEvent(new CustomEvent('workflow-editor:error', { detail: { error } }));
  }

  function currentFlowState() { return editorState.flows.get(editorState.currentKey); }

  function workflowApiRoot() {
    if (editorState.ownerType === 'brain') {
      if (editorState.draft) return `/brains/${editorState.ownerId}/draft/workflows`;
      return `/brains/${editorState.ownerId}/revisions/${editorState.revision.id}/workflows`;
    }
    if (editorState.draft) {
      return `/experiments/${editorState.experimentId}/draft/workflows`;
    }
    return `/experiments/${editorState.experimentId}/revisions/${editorState.revision.id}/workflows`;
  }

  const canvasControlIds = [
    'workflowLocateStartBtn', 'workflowLocateEndBtn', 'workflowLocateIssueBtn',
    'workflowFitBtn', 'workflowAutoLayoutBtn', 'workflowHorizontalLayoutBtn',
    'workflowUndoLayoutBtn', 'workflowTestRunBtn', 'workflowValidateBtn', 'workflowNodeSearch', 'workflowLayoutScope',
    'workflowMigrateRouterBtn',
    'workflowZoomOutBtn', 'workflowZoomResetBtn', 'workflowZoomInBtn',
  ];

  function setCanvasLoading(loading) {
    canvasControlIds.forEach(id => { if ($(id)) $(id).disabled = loading; });
    if (loading) return;
    $('workflowAutoLayoutBtn').disabled = editorState.readonly;
    $('workflowHorizontalLayoutBtn').disabled = editorState.readonly;
    $('workflowMigrateRouterBtn').disabled = editorState.readonly;
    $('workflowTestRunBtn').disabled = editorState.readonly;
    $('workflowValidateBtn').disabled = editorState.readonly;
    $('workflowUndoLayoutBtn').disabled = editorState.readonly
      || !(editorState.layoutHistory.get(editorState.currentKey) || []).length;
  }

  function renderVersionLabel(flow = currentFlowState()) {
    if (!flow) return;
    const latest = Math.max(1, ...flow.detail.versions.map(item => item.version_no));
    const revisionLabel = editorState.readonly
      ? ` · revision ${String(editorState.revision?.revision_no || '').padStart(3, '0')} · 只读`
      : '';
    $('workflowCanvasVersion').textContent = `flow.${flow.workflow.workflow_key}@v${latest}${revisionLabel}${flow.dirty ? ' · 未保存' : ''}`;
    const mode = flow.workflow.execution_mode || 'legacy_prompt_hook';
    $('workflowExecutionMode').textContent = mode === 'prompt_router'
      ? '真实执行 · Prompt 路由'
      : mode === 'native'
        ? '真实执行 · 原生工作流'
        : '兼容模式 · 后置钩子（不可发布）';
    $('workflowExecutionMode').classList.toggle('legacy', mode === 'legacy_prompt_hook');
    $('workflowMigrateRouterBtn').hidden = editorState.readonly || mode !== 'legacy_prompt_hook';
  }

  function setDirty(value = true) {
    const flow = currentFlowState();
    if (flow) flow.dirty = value;
    renderVersionLabel(flow);
    window.dispatchEvent(new CustomEvent('workflow-editor:dirty', {
      detail: { dirty: [...editorState.flows.values()].some(item => item.dirty) },
    }));
  }

  function updateDraft(draft) {
    editorState.draft = draft;
    editorState.revision = draft;
    window.dispatchEvent(new CustomEvent('workflow-editor:draft', { detail: { draft } }));
  }

  async function setContext({ ownerType = 'experiment', ownerId = null, experimentId, draft, revision = null, readonly = false }) {
    const resolvedOwnerId = ownerId || experimentId || null;
    const changedOwner = ownerType !== editorState.ownerType || resolvedOwnerId !== editorState.ownerId;
    const nextRevision = draft || revision || null;
    const changedRevision = nextRevision?.id !== editorState.revision?.id;
    editorState.ownerType = ownerType;
    editorState.ownerId = resolvedOwnerId;
    editorState.experimentId = experimentId || null;
    editorState.draft = draft || null;
    editorState.revision = nextRevision;
    editorState.readonly = readonly || !draft;
    if (changedOwner || changedRevision) {
      editorState.list = [];
      editorState.flows.clear();
      editorState.currentKey = 'schedule';
      editorState.selectedNodeId = null;
      editorState.pan = null;
      editorState.viewports.clear();
      editorState.zooms.clear();
      editorState.generation += 1;
    }
    toggleEditing();
    if (editorState.active && editorState.ownerId && editorState.revision) await activate();
  }

  async function activate() {
    editorState.active = true;
    if (!editorState.ownerId || !editorState.revision) {
      renderUnavailable(editorState.ownerType === 'brain' ? '当前大脑还没有可查看的编排' : '当前实验还没有可查看的大脑编排');
      return;
    }
    const generation = ++editorState.generation;
    $('workflowShell').setAttribute('aria-busy', 'true');
    setCanvasLoading(true);
    try {
      const [listing, functionCatalog] = await Promise.all([
        api(workflowApiRoot()),
        api('/workflow-functions'),
      ]);
      if (generation !== editorState.generation) return;
      editorState.list = listing.items;
      editorState.functions = functionCatalog.items || [];
      if (editorState.draft) editorState.draft.lock_version = listing.lock_version;
      renderTabs();
      const nextKey = editorState.list.some(item => item.workflow_key === editorState.currentKey)
        ? editorState.currentKey : editorState.list[0]?.workflow_key;
      if (nextKey) await selectFlow(nextKey, { force: !editorState.flows.has(nextKey) });
    } catch (error) {
      if (generation === editorState.generation) renderUnavailable(error.message);
      throw error;
    } finally {
      if (generation === editorState.generation) {
        $('workflowShell').setAttribute('aria-busy', 'false');
        setCanvasLoading(false);
      }
    }
  }

  function renderUnavailable(message) {
    $('workflowBoardEmpty').hidden = false;
    $('workflowBoardEmpty').querySelector('strong').textContent = message;
    $('workflowBoardEmpty').querySelector('span').textContent = '请返回实验概览检查 Revision 状态。';
    $('workflowNodeLayer').innerHTML = '';
    $('workflowWires').innerHTML = '';
  }

  function renderTabs() {
    const byKey = new Map(editorState.list.map(item => [item.workflow_key, item]));
    $('workflowTabs').innerHTML = FLOW_ORDER.filter(key => byKey.has(key)).map(key => {
      const item = byKey.get(key);
      return `<button type="button" class="workflow-tab" role="tab" data-workflow-key="${key}" aria-selected="${editorState.view === 'flow' && key === editorState.currentKey}">${escapeHtml(item.title)}</button>`;
    }).join('');
    $('workflowTabs').querySelectorAll('[data-workflow-key]').forEach((button, index, buttons) => {
      button.addEventListener('click', () => selectFlow(button.dataset.workflowKey).catch(report));
      button.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
          : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
        buttons[nextIndex].focus();
        selectFlow(buttons[nextIndex].dataset.workflowKey).catch(report);
      });
    });
  }

  function functionCardMarkup(item, node, canUse) {
    const scopeLabel = item.scope === 'custom' ? '自定义公共' : '系统公共 · 只读';
    const edit = item.editable && !editorState.readonly ? `<button type="button" data-edit-function="${escapeHtml(item.key)}">编辑</button>` : '';
    return `<article class="workflow-function-card" data-function-scope="${escapeHtml(item.scope || 'system')}">
      <div class="workflow-function-card-head"><h2>${escapeHtml(item.title)}<code>${escapeHtml(item.key)}</code></h2><div class="workflow-function-card-actions"><span class="workflow-function-available">${scopeLabel}</span>${edit}<button type="button" data-use-function="${escapeHtml(item.key)}" ${canUse ? '' : 'disabled'}>${node?.operation === item.key && node?.script_mode === 'shared' ? '当前使用' : '用于当前代码节点'}</button></div></div>
      <p>${escapeHtml(item.description)}</p>
      <div class="workflow-function-contract"><div><span>输入</span><code>${escapeHtml(item.input_type)}</code></div><div><span>输出</span><code>${escapeHtml(item.output_type)}</code></div></div>
      <div class="workflow-function-source-head"><span>完整 Python 源码</span><code>${escapeHtml(item.implementation)}</code><button type="button" data-copy-function="${escapeHtml(item.key)}">复制源码</button></div>
      <pre class="workflow-function-source"><code>${escapeHtml(item.source || '# 服务端未返回源码')}</code></pre>
    </article>`;
  }

  function openFunctionEditor(item = null) {
    editorState.functionEditingKey = item?.key || null;
    const editor = $('workflowFunctionEditor');
    editor.hidden = false;
    editor.innerHTML = `<div class="workflow-function-editor-head"><div><small>${item ? 'EDIT GLOBAL FUNCTION' : 'NEW GLOBAL FUNCTION'}</small><strong>${item ? `编辑 ${escapeHtml(item.title)}` : '新建自定义公共 Function'}</strong></div><button type="button" data-close-function-editor aria-label="关闭">×</button></div>
      <div class="workflow-function-editor-grid">
        <label>Function Key<input data-function-field="function_key" value="${escapeHtml(item?.key || '')}" placeholder="例如 normalize_profile" ${item ? 'readonly' : ''}></label>
        <label>显示名称<input data-function-field="title" value="${escapeHtml(item?.title || '')}" placeholder="说明 Function 的用途"></label>
        <label>输入类型<input data-function-field="input_type" value="${escapeHtml(item?.input_type || 'any')}"></label>
        <label>输出类型<input data-function-field="output_type" value="${escapeHtml(item?.output_type || 'any')}"></label>
        <label class="wide">说明<textarea data-function-field="description" placeholder="供所有实验使用时需要注意什么">${escapeHtml(item?.description || '')}</textarea></label>
        <label class="wide">Python 源码<textarea class="workflow-function-code-editor" data-function-field="source" spellcheck="false">${escapeHtml(item?.source || INLINE_FUNCTION_TEMPLATE)}</textarea></label>
      </div>
      <div class="workflow-function-editor-note">入口固定为 <code>main(inputs, context)</code>。禁止导入模块、访问文件/网络/进程、定义类、异步代码和无限循环。修改后会立即对所有实验可见。</div>
      <div class="workflow-function-editor-actions">${item ? '<button type="button" class="btn danger" data-delete-function>删除</button>' : '<span></span>'}<div><button type="button" class="btn" data-close-function-editor>取消</button><button type="button" class="btn btn-primary" data-save-function>保存公共 Function</button></div></div>`;
    editor.scrollIntoView({ behavior: 'smooth', block: 'start' });
    editor.querySelectorAll('[data-close-function-editor]').forEach(button => button.addEventListener('click', () => { editor.hidden = true; editorState.functionEditingKey = null; }));
    editor.querySelector('[data-save-function]').addEventListener('click', () => saveFunctionEditor(item).catch(report));
    editor.querySelector('[data-delete-function]')?.addEventListener('click', () => deleteFunctionEditor(item).catch(report));
  }

  async function saveFunctionEditor(existing) {
    const editor = $('workflowFunctionEditor');
    const value = name => editor.querySelector(`[data-function-field="${name}"]`).value.trim();
    const functionKey = value('function_key');
    if (!/^[a-z][a-z0-9_]{0,79}$/.test(functionKey)) throw new Error('Function Key 必须以小写字母开头，只能包含小写字母、数字和下划线。');
    if (!value('title')) throw new Error('请填写显示名称。');
    const saved = await api(`/workflow-functions/${encodeURIComponent(functionKey)}`, {
      method: 'PUT',
      body: JSON.stringify({
        row_version: existing?.row_version || null,
        function: {
          function_key: functionKey,
          title: value('title'),
          description: value('description'),
          input_type: value('input_type') || 'any',
          output_type: value('output_type') || 'any',
          source: editor.querySelector('[data-function-field="source"]').value,
        },
      }),
    });
    editorState.functions = saved.items;
    editor.hidden = true;
    editorState.functionEditingKey = null;
    renderFunctionManager();
    notify(`${functionKey} 已保存到全局函数库。`, '自定义公共 Function 已保存');
  }

  async function deleteFunctionEditor(existing) {
    if (!existing || !window.confirm(`确认删除自定义公共 Function「${existing.title}」？`)) return;
    const deleted = await api(`/workflow-functions/${encodeURIComponent(existing.key)}`, {
      method: 'DELETE', body: JSON.stringify({ row_version: existing.row_version }),
    });
    editorState.functions = deleted.items;
    $('workflowFunctionEditor').hidden = true;
    editorState.functionEditingKey = null;
    renderFunctionManager();
    notify(`${existing.key} 已从全局函数库删除。`, '自定义公共 Function 已删除');
  }

  function renderFunctionManager() {
    const items = editorState.functions;
    const node = selectedNode();
    const canUse = ['code', 'script'].includes(node?.kind) && !editorState.readonly;
    const custom = items.filter(item => item.scope === 'custom');
    const system = items.filter(item => item.scope !== 'custom');
    $('workflowFunctionCount').textContent = `${custom.length} 个自定义公共 Function · ${system.length} 个系统公共 Function`;
    $('workflowFunctionGrid').innerHTML = `<section class="workflow-function-group"><div class="workflow-function-group-head"><div><strong>自定义公共 Function</strong><span>数据库保存 · 全局可编辑 · 所有实验复用</span></div><b>${custom.length}</b></div><div class="workflow-function-group-grid">${custom.map(item => functionCardMarkup(item, node, canUse)).join('') || '<div class="workflow-function-empty">还没有自定义公共 Function，可以从上方新建。</div>'}</div></section>
      <section class="workflow-function-group"><div class="workflow-function-group-head"><div><strong>系统公共 Function</strong><span>平台内置 · 全局只读 · 完整源码可见</span></div><b>${system.length}</b></div><div class="workflow-function-group-grid">${system.map(item => functionCardMarkup(item, node, canUse)).join('')}</div></section>`;
    $('workflowFunctionGrid').querySelectorAll('[data-use-function]').forEach(button => button.addEventListener('click', () => {
      const current = selectedNode();
      if (!current || !['code', 'script'].includes(current.kind) || editorState.readonly) return;
      current.script_mode = 'shared';
      current.operation = button.dataset.useFunction;
      setDirty(true);
      editorState.panel = 'config';
      showWorkspaceView('flow');
      renderCurrentFlow();
      notify(`${button.dataset.useFunction} 已用于当前代码节点。`, '公共 Function 已选用');
    }));
    $('workflowFunctionGrid').querySelectorAll('[data-copy-function]').forEach(button => button.addEventListener('click', async () => {
      const item = editorState.functions.find(value => value.key === button.dataset.copyFunction);
      if (!item?.source) return;
      try {
        await navigator.clipboard.writeText(item.source);
        notify(`${item.title} 的完整源码已复制。`, '源码已复制');
      } catch (_error) { notify('浏览器未允许写入剪贴板，请直接在源码区域选择复制。', '无法自动复制'); }
    }));
    $('workflowFunctionGrid').querySelectorAll('[data-edit-function]').forEach(button => button.addEventListener('click', () => {
      openFunctionEditor(editorState.functions.find(item => item.key === button.dataset.editFunction));
    }));
  }

  function showWorkspaceView(view) {
    editorState.view = view;
    const functionView = view === 'functions';
    $('workflowShell').hidden = functionView;
    $('workflowFunctionPage').hidden = !functionView;
    $('workflowFunctionManagerBtn').setAttribute('aria-pressed', String(functionView));
    renderTabs();
    if (functionView) renderFunctionManager();
    else requestAnimationFrame(drawEdges);
  }

  function compactCanvas() {
    return window.matchMedia('(max-width: 900px)').matches;
  }

  function currentCanvasZoom() {
    return compactCanvas() ? 1 : (editorState.zooms.get(editorState.currentKey) || 1);
  }

  function updateZoomControls() {
    const zoom = currentCanvasZoom();
    if ($('workflowZoomLabel')) $('workflowZoomLabel').textContent = `${Math.round(zoom * 100)}%`;
    if ($('workflowZoomOutBtn')) $('workflowZoomOutBtn').disabled = zoom <= MIN_CANVAS_ZOOM + .001;
    if ($('workflowZoomInBtn')) $('workflowZoomInBtn').disabled = zoom >= MAX_CANVAS_ZOOM - .001;
  }

  function syncBoardStage() {
    const board = $('workflowBoard');
    const stage = $('workflowBoardStage');
    if (!board || !stage) return;
    if (compactCanvas()) {
      board.style.transform = '';
      stage.style.width = '';
      stage.style.height = '';
      updateZoomControls();
      return;
    }
    const zoom = currentCanvasZoom();
    board.style.transform = `scale(${zoom})`;
    stage.style.width = `${Math.ceil(board.offsetWidth * zoom)}px`;
    stage.style.height = `${Math.ceil(board.offsetHeight * zoom)}px`;
    updateZoomControls();
  }

  function setCanvasZoom(value, { preserveCenter = true } = {}) {
    if (compactCanvas() || !currentFlowState()) return;
    const scroller = $('workflowCanvasScroller');
    const previousZoom = currentCanvasZoom();
    const nextZoom = Math.max(MIN_CANVAS_ZOOM, Math.min(MAX_CANVAS_ZOOM, Math.round(value * 100) / 100));
    if (Math.abs(nextZoom - previousZoom) < .001) return;
    const logicalCenterX = (scroller.scrollLeft + scroller.clientWidth / 2) / previousZoom;
    const logicalCenterY = (scroller.scrollTop + scroller.clientHeight / 2) / previousZoom;
    editorState.zooms.set(editorState.currentKey, nextZoom);
    syncBoardStage();
    if (preserveCenter) {
      scroller.scrollLeft = Math.max(0, logicalCenterX * nextZoom - scroller.clientWidth / 2);
      scroller.scrollTop = Math.max(0, logicalCenterY * nextZoom - scroller.clientHeight / 2);
    }
    rememberCanvasViewport();
    drawEdges();
    renderMinimap();
  }

  function updateBoardExtent() {
    const board = $('workflowBoard');
    const stage = $('workflowBoardStage');
    const scroller = $('workflowCanvasScroller');
    if (compactCanvas()) {
      board.style.width = '';
      board.style.minHeight = '';
      if (stage) { stage.style.width = ''; stage.style.height = ''; }
      board.style.transform = '';
      return;
    }
    const flow = currentFlowState();
    board.style.width = `${Math.max(flow?.canvasWidth || 0, CANVAS_MIN_WIDTH, Math.round(scroller.clientWidth * 4.8))}px`;
    board.style.minHeight = `${Math.max(flow?.canvasHeight || 0, CANVAS_MIN_HEIGHT, Math.round(scroller.clientHeight * 2))}px`;
    syncBoardStage();
  }

  function renderMinimap() {
    const flow = currentFlowState();
    const minimap = $('workflowMinimap');
    const board = $('workflowBoard');
    const scroller = $('workflowCanvasScroller');
    if (!flow || !minimap || !board.offsetWidth || compactCanvas()) { if (minimap) minimap.hidden = true; return; }
    minimap.hidden = false;
    const zoom = currentCanvasZoom();
    const width = minimap.clientWidth || 184; const height = minimap.clientHeight || 118;
    const scaleX = width / board.offsetWidth; const scaleY = height / board.offsetHeight;
    minimap.innerHTML = flow.workflow.nodes.map(node => `<span class="workflow-minimap-node${node.node_id === editorState.selectedNodeId ? ' selected' : ''}" style="left:${Number(node.position.x) / 100 * board.offsetWidth * scaleX}px;top:${Number(node.position.y) * scaleY}px;width:${Math.max(3, NODE_WIDTH * scaleX)}px;height:${Math.max(3, estimatedNodeHeight(node) * scaleY)}px"></span>`).join('')
      + `<span class="workflow-minimap-viewport" style="left:${scroller.scrollLeft / zoom * scaleX}px;top:${scroller.scrollTop / zoom * scaleY}px;width:${Math.min(width, scroller.clientWidth / zoom * scaleX)}px;height:${Math.min(height, scroller.clientHeight / zoom * scaleY)}px"></span>`;
  }

  function locateNode(nodeOrId) {
    const id = typeof nodeOrId === 'string' ? nodeOrId : nodeOrId?.node_id;
    const element = id ? $('workflowNodeLayer').querySelector(`[data-node-id="${CSS.escape(id)}"]`) : null;
    if (!element) return;
    editorState.selectedNodeId = id;
    renderNodes(); renderInspector();
    const scroller = $('workflowCanvasScroller');
    const zoom = currentCanvasZoom();
    scroller.scrollTo({ left: Math.max(0, (element.offsetLeft + element.offsetWidth / 2) * zoom - scroller.clientWidth / 2), top: Math.max(0, (element.offsetTop + element.offsetHeight / 2) * zoom - scroller.clientHeight / 2), behavior: 'smooth' });
  }

  function workflowIssueNode() {
    const flow = currentFlowState();
    if (!flow) return null;
    const incoming = new Set(flow.workflow.edges.map(edge => edge.target_node_id));
    const outgoing = new Set(flow.workflow.edges.map(edge => edge.source_node_id));
    return flow.workflow.nodes.find(node => (node.kind !== 'start' && !incoming.has(node.node_id)) || (node.kind !== 'end' && !outgoing.has(node.node_id))) || null;
  }

  function fitAllNodes() {
    const flow = currentFlowState();
    if (!flow?.workflow.nodes.length) return;
    requestAnimationFrame(() => {
      const scroller = $('workflowCanvasScroller');
      const elements = [...$('workflowNodeLayer').querySelectorAll('.workflow-node')];
      if (!elements.length) return;
      const left = Math.min(...elements.map(element => element.offsetLeft));
      const right = Math.max(...elements.map(element => element.offsetLeft + element.offsetWidth));
      const top = Math.min(...elements.map(element => element.offsetTop));
      const bottom = Math.max(...elements.map(element => element.offsetTop + element.offsetHeight));
      const targetZoom = Math.max(MIN_CANVAS_ZOOM, Math.min(1, (scroller.clientWidth - 64) / Math.max(1, right - left), (scroller.clientHeight - 64) / Math.max(1, bottom - top)));
      setCanvasZoom(targetZoom, { preserveCenter: false });
      const zoom = currentCanvasZoom();
      scroller.scrollTo({
        left: Math.max(0, (left + right) / 2 * zoom - scroller.clientWidth / 2),
        top: Math.max(0, (top + bottom) / 2 * zoom - scroller.clientHeight / 2),
        behavior: 'smooth',
      });
      rememberCanvasViewport(); renderMinimap();
    });
  }

  function rememberCanvasViewport() {
    if (editorState.view !== 'flow' || !currentFlowState()) return;
    const scroller = $('workflowCanvasScroller');
    editorState.viewports.set(editorState.currentKey, {
      left: scroller.scrollLeft,
      top: scroller.scrollTop,
    });
  }

  function restoreCanvasViewport({ forceFit = false } = {}) {
    requestAnimationFrame(() => {
      if (editorState.view !== 'flow') return;
      const flow = currentFlowState();
      const scroller = $('workflowCanvasScroller');
      if (!flow || compactCanvas()) return;
      const stored = forceFit ? null : editorState.viewports.get(editorState.currentKey);
      if (stored) {
        scroller.scrollLeft = stored.left;
        scroller.scrollTop = stored.top;
        return;
      }
      const elements = [...$('workflowNodeLayer').querySelectorAll('.workflow-node')];
      const left = elements.length ? Math.min(...elements.map(element => element.offsetLeft)) : 0;
      const right = elements.length ? Math.max(...elements.map(element => element.offsetLeft + element.offsetWidth)) : $('workflowBoard').offsetWidth;
      const top = elements.length ? Math.min(...elements.map(element => element.offsetTop)) : 0;
      const bottom = elements.length ? Math.max(...elements.map(element => element.offsetTop + element.offsetHeight)) : $('workflowBoard').offsetHeight;
      const zoom = currentCanvasZoom();
      scroller.scrollLeft = Math.max(0, (left + right) / 2 * zoom - scroller.clientWidth / 2);
      scroller.scrollTop = Math.max(0, (top + bottom) / 2 * zoom - scroller.clientHeight / 2);
      rememberCanvasViewport();
    });
  }

  function estimatedNodeHeight(node) {
    return 88 + (node.inputs.length + node.outputs.length) * 27;
  }

  function branchRank(edge) {
    if (edge?.branch === 'true') return 0;
    if (edge?.branch === 'case') {
      return { new_day: 0, current_plan: 1, interruption: 2 }[edge.case_value] ?? 2;
    }
    if (edge?.branch === 'always') return 1;
    if (edge?.branch === 'false') return 3;
    return 2;
  }

  function buildLayerModel(workflow) {
    const nodes = workflow.nodes;
    const knownIds = new Set(nodes.map(node => node.node_id));
    const incomingEdges = new Map(nodes.map(node => [node.node_id, []]));
    const outgoingEdges = new Map(nodes.map(node => [node.node_id, []]));
    workflow.edges.forEach(edge => {
      if (!knownIds.has(edge.source_node_id) || !knownIds.has(edge.target_node_id)) return;
      incomingEdges.get(edge.target_node_id).push(edge);
      outgoingEdges.get(edge.source_node_id).push(edge);
    });
    const incomingCount = new Map(nodes.map(node => [node.node_id, incomingEdges.get(node.node_id).length]));
    const levels = new Map(nodes.map(node => [node.node_id, 0]));
    const queue = nodes.filter(node => incomingCount.get(node.node_id) === 0)
      .sort((left, right) => (left.kind === 'start' ? -1 : right.kind === 'start' ? 1 : left.position.y - right.position.y))
      .map(node => node.node_id);
    const visited = new Set();
    while (queue.length) {
      const id = queue.shift();
      visited.add(id);
      outgoingEdges.get(id).forEach(edge => {
        levels.set(edge.target_node_id, Math.max(levels.get(edge.target_node_id), levels.get(id) + 1));
        incomingCount.set(edge.target_node_id, incomingCount.get(edge.target_node_id) - 1);
        if (incomingCount.get(edge.target_node_id) === 0) queue.push(edge.target_node_id);
      });
    }
    nodes.filter(node => !visited.has(node.node_id)).forEach((node, index) => {
      levels.set(node.node_id, Math.max(...levels.values(), 0) + index + 1);
    });
    const byLevel = new Map();
    nodes.forEach(node => {
      const level = levels.get(node.node_id);
      if (!byLevel.has(level)) byLevel.set(level, []);
      byLevel.get(level).push(node);
    });
    const levelKeys = [...byLevel.keys()].sort((left, right) => left - right);
    byLevel.forEach(items => items.sort((left, right) => left.position.y - right.position.y || left.title.localeCompare(right.title, 'zh-CN')));

    const order = new Map();
    const refreshOrder = () => byLevel.forEach(items => items.forEach((node, index) => {
      order.set(node.node_id, items.length === 1 ? .5 : index / (items.length - 1));
    }));
    const barycenter = (edges, direction, fallback) => {
      const values = edges.map(edge => order.get(direction === 'incoming' ? edge.source_node_id : edge.target_node_id)).filter(Number.isFinite);
      return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : fallback;
    };
    refreshOrder();
    for (let sweep = 0; sweep < 3; sweep += 1) {
      levelKeys.slice(1).forEach(level => {
        byLevel.get(level).sort((left, right) => {
          const leftEdges = incomingEdges.get(left.node_id);
          const rightEdges = incomingEdges.get(right.node_id);
          const leftScore = barycenter(leftEdges, 'incoming', order.get(left.node_id)) + Math.min(...leftEdges.map(branchRank), 1) * .035;
          const rightScore = barycenter(rightEdges, 'incoming', order.get(right.node_id)) + Math.min(...rightEdges.map(branchRank), 1) * .035;
          return leftScore - rightScore || left.position.y - right.position.y;
        });
        refreshOrder();
      });
      levelKeys.slice(0, -1).reverse().forEach(level => {
        byLevel.get(level).sort((left, right) => barycenter(outgoingEdges.get(left.node_id), 'outgoing', order.get(left.node_id))
          - barycenter(outgoingEdges.get(right.node_id), 'outgoing', order.get(right.node_id)) || left.position.y - right.position.y);
        refreshOrder();
      });
    }
    return { byLevel, levels, levelKeys, incomingEdges, outgoingEdges };
  }

  function applyReadableLayout(flow, direction = 'horizontal', { scope = 'all', mark = false } = {}) {
    const model = buildLayerModel(flow.workflow);
    const levelCount = Math.max(1, model.levelKeys.length);
    if (direction === 'horizontal') {
      const contentWidth = levelCount * NODE_WIDTH + Math.max(0, levelCount - 1) * HORIZONTAL_LAYER_GAP;
      flow.canvasWidth = Math.max(CANVAS_MIN_WIDTH, contentWidth + CANVAS_PADDING_X * 2);
      const stackHeights = model.levelKeys.map(level => {
        const items = model.byLevel.get(level);
        return items.reduce((sum, node) => sum + estimatedNodeHeight(node), 0) + Math.max(0, items.length - 1) * NODE_STACK_GAP;
      });
      flow.canvasHeight = Math.max(CANVAS_MIN_HEIGHT, Math.max(...stackHeights, 0) + CANVAS_PADDING_Y * 2);
      const startX = (flow.canvasWidth - contentWidth) / 2;
      model.levelKeys.forEach((level, levelIndex) => {
        const items = model.byLevel.get(level);
        const stackHeight = stackHeights[levelIndex];
        let y = (flow.canvasHeight - stackHeight) / 2;
        items.forEach(node => {
          if (!(scope === 'unplaced' && node.config?.manual_positioned)) {
            node.position.x = ((startX + levelIndex * (NODE_WIDTH + HORIZONTAL_LAYER_GAP)) / flow.canvasWidth) * 100;
            node.position.y = y;
            if (mark) node.config = { ...(node.config || {}), auto_laid_out: true, layout_version: 2 };
          }
          y += estimatedNodeHeight(node) + NODE_STACK_GAP;
        });
      });
    } else {
      const levelHeights = model.levelKeys.map(level => Math.max(...model.byLevel.get(level).map(estimatedNodeHeight), 88));
      const contentHeight = levelHeights.reduce((sum, height) => sum + height, 0) + Math.max(0, levelCount - 1) * VERTICAL_LAYER_GAP;
      const widestLevel = Math.max(...model.levelKeys.map(level => model.byLevel.get(level).length * NODE_WIDTH
        + Math.max(0, model.byLevel.get(level).length - 1) * NODE_STACK_GAP), NODE_WIDTH);
      flow.canvasWidth = Math.max(CANVAS_MIN_WIDTH, widestLevel + CANVAS_PADDING_X * 2);
      flow.canvasHeight = Math.max(CANVAS_MIN_HEIGHT, contentHeight + CANVAS_PADDING_Y * 2);
      let y = (flow.canvasHeight - contentHeight) / 2;
      model.levelKeys.forEach((level, levelIndex) => {
        const items = model.byLevel.get(level);
        const rowWidth = items.length * NODE_WIDTH + Math.max(0, items.length - 1) * NODE_STACK_GAP;
        let x = (flow.canvasWidth - rowWidth) / 2;
        items.forEach(node => {
          if (!(scope === 'unplaced' && node.config?.manual_positioned)) {
            node.position.x = (x / flow.canvasWidth) * 100;
            node.position.y = y;
            if (mark) node.config = { ...(node.config || {}), auto_laid_out: true, layout_version: 2 };
          }
          x += NODE_WIDTH + NODE_STACK_GAP;
        });
        y += levelHeights[levelIndex] + VERTICAL_LAYER_GAP;
      });
    }
    if (mark) flow.workflow.nodes.forEach(node => {
      node.config = {
        ...(node.config || {}),
        auto_laid_out: true,
        layout_version: 2,
        layout_direction: direction,
        layout_canvas_width: flow.canvasWidth,
        layout_canvas_height: flow.canvasHeight,
      };
    });
    flow.layoutModel = model;
  }

  async function selectFlow(key, { force = false } = {}) {
    if (!key || !editorState.ownerId) return;
    cancelConnection();
    showWorkspaceView('flow');
    editorState.currentKey = key;
    renderTabs();
    let flow = editorState.flows.get(key);
    if (!flow || force) {
      const generation = editorState.generation;
      const detail = await api(`${workflowApiRoot()}/${key}`);
      if (generation !== editorState.generation || key !== editorState.currentKey) return;
      flow = {
        detail,
        workflow: structuredClone(detail.workflow),
        prompts: Object.fromEntries(Object.entries(detail.prompts).map(([promptKey, value]) => [promptKey, normalizePromptVariables(value.content)])),
        dirty: false,
      };
      flow.workflow.nodes.forEach(ensureNodeConfig);
      const savedLayout = flow.workflow.nodes.find(node => node.config?.layout_version === 2);
      if (!flow.workflow.nodes.some(node => node.config?.manual_positioned) && !savedLayout) {
        applyReadableLayout(flow, 'horizontal');
        flow.readableLayoutApplied = true;
      } else {
        const extent = flow.workflow.nodes.find(node => node.config?.layout_canvas_width);
        flow.canvasWidth = Number(extent?.config?.layout_canvas_width) || CANVAS_MIN_WIDTH;
        flow.canvasHeight = Number(extent?.config?.layout_canvas_height) || CANVAS_MIN_HEIGHT;
        flow.layoutModel = buildLayerModel(flow.workflow);
      }
      editorState.flows.set(key, flow);
      if (editorState.draft) editorState.draft.lock_version = detail.lock_version;
    }
    editorState.selectedNodeId = flow.workflow.nodes.find(node => node.kind === 'llm')?.node_id
      || flow.workflow.nodes[0]?.node_id || null;
    $('workflowUndoLayoutBtn').disabled = !(editorState.layoutHistory.get(key) || []).length;
    editorState.panel = 'contract';
    renderCurrentFlow();
  }

  function renderCurrentFlow() {
    const flow = currentFlowState();
    if (!flow) return;
    $('workflowCanvasTitle').textContent = flow.workflow.title;
    renderVersionLabel(flow);
    $('workflowTabs').querySelectorAll('[data-workflow-key]').forEach(button => {
      button.setAttribute('aria-selected', String(editorState.view === 'flow' && button.dataset.workflowKey === editorState.currentKey));
    });
    renderNodes();
    renderInspector();
    toggleEditing();
    restoreCanvasViewport();
  }

  function nodeSummary(node) {
    if (node.kind === 'llm') return `prompt · ${node.prompt_key}`;
    if (['code', 'script'].includes(node.kind)) return node.script_mode === 'inline' ? 'inline function · main' : `shared function · ${node.operation}`;
    if (node.kind === 'subflow') return `subflow · ${node.subflow_key}`;
    if (node.kind === 'variable_assigner') return `变量赋值 · ${node.state_path || 'agent.state'}`;
    if (node.kind === 'variable_aggregator') return '聚合首个非空变量';
    if (node.kind === 'read_state' || node.kind === 'write_state') return node.state_path || 'agent.state';
    return `${node.kind} · ${node.node_id}`;
  }

  function portMarkup(node, port, direction) {
    const label = `${direction === 'output' ? '从' : '连接到'} ${node.title} 的 ${port.name}`;
    return `<span class="workflow-node-port ${direction}" data-port-direction="${direction}" data-port-name="${escapeHtml(port.name)}" data-port-type="${escapeHtml(port.data_type)}" title="${escapeHtml(port.description || label)}"><b>${direction === 'output' ? '输出' : '输入'}</b><code>${escapeHtml(port.name)}</code><em>${escapeHtml(port.data_type)}</em><button type="button" class="workflow-port-handle" data-connect-direction="${direction}" data-node-id="${escapeHtml(node.node_id)}" data-port-name="${escapeHtml(port.name)}" aria-label="${escapeHtml(label)}"></button></span>`;
  }

  function nodeMarkup(node) {
    const ports = [
      ...node.inputs.map(port => portMarkup(node, port, 'input')),
      ...node.outputs.map(port => portMarkup(node, port, 'output')),
    ].join('');
    const flow = currentFlowState();
    const hasIncoming = flow.workflow.edges.some(edge => edge.target_node_id === node.node_id);
    const hasOutgoing = flow.workflow.edges.some(edge => edge.source_node_id === node.node_id);
    const unconnected = (node.kind !== 'start' && !hasIncoming) || (node.kind !== 'end' && !hasOutgoing);
    const match = editorState.searchQuery && `${node.title} ${node.node_id} ${node.kind}`.toLowerCase().includes(editorState.searchQuery);
    return `<div role="button" tabindex="0" class="workflow-node${node.node_id === editorState.selectedNodeId ? ' selected' : ''}${unconnected ? ' unconnected' : ''}${match ? ' search-match' : ''}" data-node-id="${escapeHtml(node.node_id)}" data-kind="${node.kind}" style="left:${node.position.x}%;top:${node.position.y}px"><span class="workflow-node-head"><span class="workflow-node-kind">${escapeHtml(KIND_LABELS[node.kind] || node.kind)}</span></span><span class="workflow-node-copy"><strong>${escapeHtml(node.title)}</strong><small>${escapeHtml(nodeSummary(node))}</small></span><span class="workflow-node-ports">${ports}</span></div>`;
  }

  function renderNodes() {
    const flow = currentFlowState();
    updateBoardExtent();
    $('workflowNodeLayer').innerHTML = flow.workflow.nodes.map(nodeMarkup).join('');
    $('workflowBoardEmpty').hidden = flow.workflow.nodes.length > 0;
    const maxY = Math.max(CANVAS_MIN_HEIGHT, flow.canvasHeight || 0, ...flow.workflow.nodes.map(node => Number(node.position.y) + estimatedNodeHeight(node) + CANVAS_PADDING_Y));
    $('workflowBoard').style.minHeight = `${maxY}px`;
    syncBoardStage();
    $('workflowNodeLayer').querySelectorAll('.workflow-node').forEach(button => {
      button.addEventListener('click', event => {
        if (button.dataset.dragged === '1' || event.target.closest('[data-connect-direction]')) return;
        editorState.selectedNodeId = button.dataset.nodeId;
        renderNodes();
        renderInspector();
      });
      button.addEventListener('keydown', event => {
        if (!['Enter', ' '].includes(event.key) || event.target.closest('[data-connect-direction]')) return;
        event.preventDefault();
        editorState.selectedNodeId = button.dataset.nodeId;
        renderNodes();
        renderInspector();
      });
      if (!editorState.readonly) enableDrag(button, flow.workflow.nodes.find(node => node.node_id === button.dataset.nodeId));
    });
    $('workflowNodeLayer').querySelectorAll('[data-connect-direction]').forEach(handle => {
      handle.addEventListener('click', event => {
        event.stopPropagation();
        handleConnectionClick(handle);
      });
    });
    applyConnectionHighlights();
    requestAnimationFrame(() => { drawEdges(); renderMinimap(); });
  }

  function enableDrag(button, node) {
    button.addEventListener('pointerdown', event => {
      if (event.button !== 0 || event.target.closest('button,input,select,textarea')) return;
      const board = $('workflowBoard');
      const boardRect = board.getBoundingClientRect();
      const nodeRect = button.getBoundingClientRect();
      const zoom = currentCanvasZoom();
      editorState.drag = {
        pointerId: event.pointerId,
        node,
        button,
        board,
        boardRect,
        startX: event.clientX,
        startY: event.clientY,
        zoom,
        left: (nodeRect.left - boardRect.left) / zoom,
        top: (nodeRect.top - boardRect.top) / zoom,
        moved: false,
      };
      editorState.selectedNodeId = node.node_id;
      button.classList.add('dragging', 'selected');
      button.setPointerCapture(event.pointerId);
      renderInspector();
    });
    button.addEventListener('pointermove', event => {
      const drag = editorState.drag;
      if (!drag || drag.pointerId !== event.pointerId || drag.button !== button) return;
      const dx = (event.clientX - drag.startX) / drag.zoom;
      const dy = (event.clientY - drag.startY) / drag.zoom;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      const left = Math.max(0, Math.min(drag.board.offsetWidth - button.offsetWidth, drag.left + dx));
      const top = Math.max(0, drag.top + dy);
      node.position.x = drag.board.offsetWidth ? (left / drag.board.offsetWidth) * 100 : 0;
      node.position.y = top;
      button.style.left = `${node.position.x}%`;
      button.style.top = `${top}px`;
      if (top + button.offsetHeight + CANVAS_PADDING_Y > drag.board.clientHeight) {
        currentFlowState().canvasHeight = top + button.offsetHeight + CANVAS_PADDING_Y;
        drag.board.style.minHeight = `${currentFlowState().canvasHeight}px`;
        syncBoardStage();
      }
      drawEdges();
    });
    const finish = event => {
      const drag = editorState.drag;
      if (!drag || drag.pointerId !== event.pointerId || drag.button !== button) return;
      button.dataset.dragged = drag.moved ? '1' : '0';
      button.classList.remove('dragging');
      if (button.hasPointerCapture(event.pointerId)) button.releasePointerCapture(event.pointerId);
      if (drag.moved) setDirty(true);
      if (drag.moved) node.config = {
        ...(node.config || {}),
        manual_positioned: true,
        layout_canvas_width: currentFlowState().canvasWidth || drag.board.offsetWidth,
        layout_canvas_height: currentFlowState().canvasHeight || drag.board.offsetHeight,
      };
      editorState.drag = null;
      setTimeout(() => { button.dataset.dragged = '0'; }, 0);
    };
    button.addEventListener('pointerup', finish);
    button.addEventListener('pointercancel', finish);
  }

  function enableCanvasPan() {
    const board = $('workflowBoard');
    const scroller = $('workflowCanvasScroller');
    board.addEventListener('pointerdown', event => {
      if (compactCanvas() || ![0, 1].includes(event.button)) return;
      if (event.target.closest('.workflow-node,button,input,select,textarea,a')) return;
      editorState.pan = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        scrollLeft: scroller.scrollLeft,
        scrollTop: scroller.scrollTop,
        moved: false,
      };
      board.classList.add('panning');
      board.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    board.addEventListener('pointermove', event => {
      const pan = editorState.pan;
      if (!pan || pan.pointerId !== event.pointerId) return;
      const dx = event.clientX - pan.startX;
      const dy = event.clientY - pan.startY;
      if (Math.abs(dx) + Math.abs(dy) > 3) pan.moved = true;
      scroller.scrollLeft = pan.scrollLeft - dx;
      scroller.scrollTop = pan.scrollTop - dy;
      event.preventDefault();
    });
    const finish = event => {
      const pan = editorState.pan;
      if (!pan || pan.pointerId !== event.pointerId) return;
      if (board.hasPointerCapture(event.pointerId)) board.releasePointerCapture(event.pointerId);
      board.classList.remove('panning');
      editorState.pan = null;
      rememberCanvasViewport();
      if (!pan.moved && editorState.pendingConnection) cancelConnection();
    };
    board.addEventListener('pointerup', finish);
    board.addEventListener('pointercancel', finish);
  }

  function compatibleTypes(sourceType, targetType) {
    return sourceType === 'any' || targetType === 'any' || sourceType === targetType;
  }

  function cancelConnection() {
    editorState.pendingConnection = null;
    if ($('workflowConnectHint')) {
      $('workflowConnectHint').classList.remove('active');
      $('workflowConnectHint').querySelector('span').textContent = '空白处按住左键拖动画布；点击输出端口连接节点。';
    }
    applyConnectionHighlights();
  }

  function applyConnectionHighlights() {
    const pending = editorState.pendingConnection;
    $('workflowNodeLayer')?.querySelectorAll('.workflow-node-port').forEach(row => {
      row.classList.remove('connect-source', 'connect-compatible', 'connect-incompatible');
      if (!pending) return;
      if (row.dataset.portDirection === 'output') {
        if (row.closest('[data-node-id]')?.dataset.nodeId === pending.nodeId && row.dataset.portName === pending.portName) row.classList.add('connect-source');
        return;
      }
      row.classList.add(compatibleTypes(pending.dataType, row.dataset.portType) ? 'connect-compatible' : 'connect-incompatible');
    });
  }

  function handleConnectionClick(handle) {
    if (editorState.readonly) return;
    const flow = currentFlowState();
    if (!flow) return;
    const node = flow.workflow.nodes.find(item => item.node_id === handle.dataset.nodeId);
    const direction = handle.dataset.connectDirection;
    const ports = direction === 'output' ? node?.outputs : node?.inputs;
    const port = ports?.find(item => item.name === handle.dataset.portName);
    if (!node || !port) return;

    if (direction === 'output') {
      editorState.pendingConnection = { nodeId: node.node_id, portName: port.name, dataType: port.data_type };
      $('workflowConnectHint').classList.add('active');
      $('workflowConnectHint').querySelector('span').textContent = `已选择 ${node.title}.${port.name}，请选择兼容的输入端口。`;
      applyConnectionHighlights();
      return;
    }

    const source = editorState.pendingConnection;
    if (!source) {
      notify('请先点击一个节点右侧的输出端口，再选择目标输入端口。', '如何连接节点');
      return;
    }
    if (source.nodeId === node.node_id) {
      notify('同一节点不能连接到自己。', '无法创建连线');
      return;
    }
    if (!compatibleTypes(source.dataType, port.data_type)) {
      notify(`${source.dataType} 不能连接到 ${port.data_type}，请先调整端口类型。`, '数据类型不兼容');
      return;
    }
    const duplicate = flow.workflow.edges.some(edge => edge.source_node_id === source.nodeId
      && edge.source_port === source.portName && edge.target_node_id === node.node_id && edge.target_port === port.name);
    if (duplicate) {
      cancelConnection();
      notify('这两个端口已经连接。', '无需重复连接');
      return;
    }
    const sourceNode = flow.workflow.nodes.find(item => item.node_id === source.nodeId);
    let branch = 'always';
    let caseValue = null;
    if (['selector', 'if_else'].includes(sourceNode?.kind) && (sourceNode?.config?.selector_mode || 'boolean') === 'boolean') branch = /false|no|without|unchanged|reject/.test(source.portName) ? 'false' : 'true';
    if (sourceNode?.kind === 'switch' || (sourceNode?.kind === 'selector' && sourceNode?.config?.selector_mode === 'case')) { branch = 'case'; caseValue = source.portName; }
    flow.workflow.edges.push({
      source_node_id: source.nodeId,
      source_port: source.portName,
      target_node_id: node.node_id,
      target_port: port.name,
      branch,
      case_value: caseValue,
    });
    editorState.selectedNodeId = node.node_id;
    cancelConnection();
    setDirty(true);
    renderCurrentFlow();
    notify('连线已创建；保存草稿后会进入新版本。', '节点已连接');
  }

  function roundedOrthogonalPath(points, radius = 9) {
    const compact = points.filter((point, index) => index === 0 || point[0] !== points[index - 1][0] || point[1] !== points[index - 1][1]);
    if (compact.length < 2) return '';
    let path = `M ${compact[0][0].toFixed(1)} ${compact[0][1].toFixed(1)}`;
    for (let index = 1; index < compact.length - 1; index += 1) {
      const previous = compact[index - 1];
      const current = compact[index];
      const next = compact[index + 1];
      const beforeDistance = Math.hypot(current[0] - previous[0], current[1] - previous[1]);
      const afterDistance = Math.hypot(next[0] - current[0], next[1] - current[1]);
      const turnRadius = Math.min(radius, beforeDistance / 2, afterDistance / 2);
      const before = [
        current[0] - ((current[0] - previous[0]) / (beforeDistance || 1)) * turnRadius,
        current[1] - ((current[1] - previous[1]) / (beforeDistance || 1)) * turnRadius,
      ];
      const after = [
        current[0] + ((next[0] - current[0]) / (afterDistance || 1)) * turnRadius,
        current[1] + ((next[1] - current[1]) / (afterDistance || 1)) * turnRadius,
      ];
      path += ` L ${before[0].toFixed(1)} ${before[1].toFixed(1)} Q ${current[0].toFixed(1)} ${current[1].toFixed(1)} ${after[0].toFixed(1)} ${after[1].toFixed(1)}`;
    }
    const last = compact.at(-1);
    return `${path} L ${last[0].toFixed(1)} ${last[1].toFixed(1)}`;
  }

  function drawEdges() {
    const flow = currentFlowState();
    if (!flow) return;
    const board = $('workflowBoard');
    const boardRect = board.getBoundingClientRect();
    if (!boardRect.width) return;
    const zoom = currentCanvasZoom();
    const boardWidth = board.offsetWidth;
    const boardHeight = board.offsetHeight;
    const svg = $('workflowWires');
    const nodeLayer = $('workflowNodeLayer');
    svg.setAttribute('viewBox', `0 0 ${boardWidth} ${boardHeight}`);
    const nodeRects = [...nodeLayer.querySelectorAll('.workflow-node')].map(element => {
      const rect = element.getBoundingClientRect();
      return { top: (rect.top - boardRect.top) / zoom, bottom: (rect.bottom - boardRect.top) / zoom };
    });
    const graphTop = nodeRects.length ? Math.min(...nodeRects.map(rect => rect.top)) : CANVAS_PADDING_Y;
    const graphBottom = nodeRects.length ? Math.max(...nodeRects.map(rect => rect.bottom)) : boardHeight - CANVAS_PADDING_Y;
    const layoutModel = buildLayerModel(flow.workflow);
    flow.layoutModel = layoutModel;
    let topLane = 0;
    let bottomLane = 0;
    const paths = [`<defs><marker id="workflowArrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" fill="#91a69f"></path></marker></defs>`];
    flow.workflow.edges.forEach((edge, edgeIndex) => {
      const source = nodeLayer.querySelector(`[data-node-id="${CSS.escape(edge.source_node_id)}"]`);
      const target = nodeLayer.querySelector(`[data-node-id="${CSS.escape(edge.target_node_id)}"]`);
      if (!source || !target) return;
      const sourcePort = source.querySelector(`.workflow-node-port.output[data-port-name="${CSS.escape(edge.source_port)}"] .workflow-port-handle`);
      const targetPort = target.querySelector(`.workflow-node-port.input[data-port-name="${CSS.escape(edge.target_port)}"] .workflow-port-handle`);
      if (!sourcePort || !targetPort) return;
      const s = sourcePort.getBoundingClientRect();
      const t = targetPort.getBoundingClientRect();
      const sx = (s.left - boardRect.left + s.width / 2) / zoom;
      const sy = (s.top - boardRect.top + s.height / 2) / zoom;
      const tx = (t.left - boardRect.left + t.width / 2) / zoom;
      const ty = (t.top - boardRect.top + t.height / 2) / zoom;
      const sourceLevel = layoutModel.levels.get(edge.source_node_id) ?? 0;
      const targetLevel = layoutModel.levels.get(edge.target_node_id) ?? sourceLevel + 1;
      const crossesLayers = targetLevel - sourceLevel > 1;
      const reversesDirection = tx <= sx + 52 || targetLevel <= sourceLevel;
      let points;
      if (crossesLayers || reversesDirection) {
        const prefersBottom = edge.branch === 'false' || edge.case_value === 'current_plan' || edge.case_value === 'interruption' || (edgeIndex % 2 === 1 && edge.branch === 'always');
        const routeY = prefersBottom
          ? Math.min(boardHeight - 28, graphBottom + 54 + bottomLane++ * 22)
          : Math.max(28, graphTop - 54 - topLane++ * 22);
        const sourceStub = sx + 30;
        const targetStub = tx - 30;
        points = [[sx, sy], [sourceStub, sy], [sourceStub, routeY], [targetStub, routeY], [targetStub, ty], [tx, ty]];
      } else {
        const centerX = (sx + tx) / 2 + ((edgeIndex % 3) - 1) * 7;
        points = [[sx, sy], [centerX, sy], [centerX, ty], [tx, ty]];
      }
      const related = [edge.source_node_id, edge.target_node_id].includes(editorState.selectedNodeId) ? ' related' : '';
      paths.push(`<path class="workflow-wire ${edge.branch || ''}${related}" d="${roundedOrthogonalPath(points)}" marker-end="url(#workflowArrow)"></path>`);
    });
    svg.innerHTML = paths.join('');
    renderMinimap();
  }

  function selectedNode() {
    return currentFlowState()?.workflow.nodes.find(node => node.node_id === editorState.selectedNodeId) || null;
  }

  function renderInspector() {
    const node = selectedNode();
    $('workflowInspectorKind').textContent = node ? `${KIND_LABELS[node.kind] || node.kind} NODE` : 'NODE';
    $('workflowInspectorTitle').textContent = node?.title || '选择一个节点';
    $('workflowInspectorId').textContent = node?.node_id || '—';
    $('workflowBodyTab').textContent = BODY_LABELS[node?.kind] || '节点';
    $('workflowDeleteNode').hidden = !node || ['start', 'end'].includes(node.kind);
    $('workflowInspectorBody').innerHTML = node ? inspectorMarkup(node) : '<div class="empty-state"><strong>尚未选择节点</strong></div>';
    document.querySelectorAll('[data-workflow-panel]').forEach(button => button.setAttribute('aria-selected', String(button.dataset.workflowPanel === editorState.panel)));
    bindInspector(node);
  }

  function portConnectionText(node, port, direction) {
    const edges = currentFlowState().workflow.edges.filter(edge => direction === 'inputs'
      ? edge.target_node_id === node.node_id && edge.target_port === port.name
      : edge.source_node_id === node.node_id && edge.source_port === port.name);
    if (!edges.length) return '尚未连接';
    return `${edges.length} 条连线 · ${edges.map(edge => direction === 'inputs' ? edge.source_node_id : edge.target_node_id).join('、')}`;
  }

  function contractRows(node, ports, direction) {
    if (!ports.length) return `<div class="workflow-inspector-note">该节点没有${direction === 'inputs' ? '输入' : '输出'}变量。</div>`;
    const immutable = editorState.readonly || ['start', 'end'].includes(node.kind);
    return ports.map((port, index) => {
      const metadata = `${port.required ? '必需' : '可选'} · ${portConnectionText(node, port, direction)}`;
      if (immutable) return `<div class="workflow-contract-row" data-port-direction="${direction}" data-port-index="${index}"><div class="workflow-contract-main is-readonly"><div class="workflow-readonly-value"><span>${escapeHtml(port.name)}</span><span class="workflow-meta-badge">变量名</span></div><div class="workflow-readonly-value"><span>${escapeHtml(port.data_type)}</span><span class="workflow-meta-badge">类型</span></div></div><span class="workflow-contract-meta">${escapeHtml(port.description || metadata)}<br>${escapeHtml(metadata)}</span></div>`;
      return `<div class="workflow-contract-row" data-port-direction="${direction}" data-port-index="${index}"><div class="workflow-contract-main"><input data-port-name value="${escapeHtml(port.name)}" aria-label="变量名"><select data-port-type aria-label="变量类型">${dataTypeOptions(port.data_type)}</select><button type="button" data-remove-port aria-label="删除变量">×</button></div><input class="workflow-contract-description" data-port-description value="${escapeHtml(port.description || '')}" placeholder="说明变量含义与来源"><span class="workflow-contract-meta">${escapeHtml(metadata)}</span></div>`;
    }).join('');
  }

  function promptVariableTokens(node, content) {
    const tokens = [];
    node.inputs.forEach(port => {
      tokens.push(`{${port.name}}`);
      (TYPE_PATHS[port.data_type] || []).forEach(path => tokens.push(`{${port.name}.${path}}`));
    });
    const inputs = new Set(node.inputs.map(port => port.name));
    String(content).replace(/\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}/g, (_match, path) => {
      if (inputs.has(path.split('.')[0])) tokens.push(`{${path}}`);
      return _match;
    });
    return [...new Set(tokens)];
  }

  function variablePickerMarkup(node, content) {
    const tokens = promptVariableTokens(node, content);
    const analysis = promptVariableAnalysis(node, content);
    const unresolved = analysis.unresolved.length ? `<div class="workflow-schema-error">${analysis.unresolved.map(item => `${escapeHtml(item.token)}：${escapeHtml(item.reason)}`).join('<br>')}</div>` : '';
    const hint = editorState.readonly ? '当前节点可达变量' : '当前节点可达变量 · 点击插入 Prompt';
    return `<div class="workflow-variable-box"><span>${hint}</span><div class="workflow-variable-list">${tokens.map(token => `<button type="button" class="workflow-variable-chip" data-insert-variable="${escapeHtml(token)}" ${editorState.readonly ? 'disabled' : ''}>${escapeHtml(token)}</button>`).join('') || '<small>请先在“输入输出”中声明输入变量</small>'}</div>${unresolved}</div>`;
  }

  function connectionsMarkup(node) {
    const items = currentFlowState().workflow.edges.map((edge, index) => ({ edge, index })).filter(item => item.edge.source_node_id === node.node_id || item.edge.target_node_id === node.node_id);
    if (!items.length) return '<div class="workflow-inspector-note">该节点还没有连线。请在画布点击输出端口，再点击目标输入端口。</div>';
    return `<div class="workflow-edge-list">${items.map(({ edge, index }) => `<div class="workflow-edge-item"><code>${escapeHtml(edge.source_node_id)}.${escapeHtml(edge.source_port)} → ${escapeHtml(edge.target_node_id)}.${escapeHtml(edge.target_port)}${edge.branch !== 'always' ? ` · ${escapeHtml(edge.branch)}` : ''}</code><button type="button" data-remove-edge="${index}" ${editorState.readonly ? 'disabled' : ''}>断开</button></div>`).join('')}</div>`;
  }

  function normalizedSchemaType(schema) {
    const value = Array.isArray(schema?.type) ? schema.type.find(type => type !== 'null') : schema?.type;
    return ['string', 'number', 'integer', 'boolean', 'object', 'array'].includes(value) ? value : 'string';
  }

  function flattenResponseSchema(schema) {
    const rows = [];
    const visit = (value, path, required = false) => {
      const type = normalizedSchemaType(value);
      rows.push({
        path, type, required,
        description: value?.description || '',
        enum: Array.isArray(value?.enum) ? value.enum.join(', ') : '',
        default: value && Object.hasOwn(value, 'default') ? JSON.stringify(value.default) : '',
      });
      if (type === 'object') Object.entries(value.properties || {}).forEach(([key, child]) => visit(child, `${path}.${key}`, (value.required || []).includes(key)));
      if (type === 'array' && normalizedSchemaType(value.items) === 'object') {
        Object.entries(value.items?.properties || {}).forEach(([key, child]) => visit(child, `${path}[].${key}`, (value.items?.required || []).includes(key)));
      }
    };
    visit(schema?.properties?.res || { type: 'object', properties: {} }, 'res', true);
    return rows;
  }

  function schemaRowMarkup(row, index) {
    const root = row.path === 'res';
    const disabled = editorState.readonly ? 'disabled' : '';
    return `<div class="workflow-schema-row" data-schema-row="${index}">
      <input data-schema-path value="${escapeHtml(row.path)}" ${root || editorState.readonly ? 'readonly' : ''} aria-label="字段路径" title="使用点号定义对象嵌套，使用 [] 定义对象数组，例如 res.items[].name">
      <select data-schema-type ${disabled}>${['string', 'number', 'integer', 'boolean', 'object', 'array'].map(type => `<option value="${type}" ${type === row.type ? 'selected' : ''}>${type}</option>`).join('')}</select>
      <label><input data-schema-required type="checkbox" ${row.required ? 'checked' : ''} ${root || editorState.readonly ? 'disabled' : ''}>必填</label>
      <input data-schema-description value="${escapeHtml(row.description)}" placeholder="描述" ${disabled}>
      <input data-schema-enum value="${escapeHtml(row.enum)}" placeholder="枚举，逗号分隔" ${disabled}>
      <input data-schema-default value="${escapeHtml(row.default)}" placeholder="默认值" ${disabled}>
      <button type="button" data-remove-schema-row ${root || editorState.readonly ? 'disabled' : ''} aria-label="删除字段">×</button>
    </div>`;
  }

  function schemaBuilderMarkup(schema) {
    const rows = flattenResponseSchema(schema);
    return `<div class="workflow-schema-toolbar"><strong>结构化输出字段</strong><div><button type="button" class="workflow-mini-btn" data-add-schema-field ${editorState.readonly ? 'disabled' : ''}>＋ 添加字段</button><button type="button" class="workflow-mini-btn" data-toggle-schema-expert>专家 JSON</button></div></div>
      <div class="workflow-schema-builder" id="workflowSchemaBuilder">${rows.map(schemaRowMarkup).join('')}</div>
      <div class="workflow-schema-error" id="workflowSchemaError" hidden></div>
      <pre class="workflow-schema-example" id="workflowSchemaExample"></pre>`;
  }

  function parseSchemaLiteral(value) {
    const text = String(value || '').trim();
    if (!text) return undefined;
    try { return JSON.parse(text); } catch (_error) { return text; }
  }

  function schemaFromBuilder() {
    const rows = [...document.getElementById('workflowSchemaBuilder').querySelectorAll('[data-schema-row]')].map(row => ({
      path: row.querySelector('[data-schema-path]').value.trim(),
      type: row.querySelector('[data-schema-type]').value,
      required: row.querySelector('[data-schema-required]').checked || row.querySelector('[data-schema-path]').value.trim() === 'res',
      description: row.querySelector('[data-schema-description]').value.trim(),
      enum: row.querySelector('[data-schema-enum]').value.split(/[,，]/).map(item => item.trim()).filter(Boolean),
      default: parseSchemaLiteral(row.querySelector('[data-schema-default]').value),
    }));
    const paths = new Set();
    rows.forEach(row => {
      if (!/^res(?:\[\])?(?:\.[a-z][a-z0-9_]*(?:\[\])?)*$/.test(row.path)) throw new Error(`字段路径 ${row.path || '（空）'} 无效`);
      if (paths.has(row.path)) throw new Error(`字段路径 ${row.path} 重复`);
      paths.add(row.path);
    });
    if (!paths.has('res')) throw new Error('必须保留根输出 res');
    const schema = { type: 'object', properties: {}, required: ['res'], additionalProperties: false };
    rows.sort((left, right) => left.path.split('.').length - right.path.split('.').length).forEach(row => {
      const segments = row.path.split('.');
      let container = schema;
      segments.forEach((segment, index) => {
        const array = segment.endsWith('[]');
        const key = array ? segment.slice(0, -2) : segment;
        container.type = 'object'; container.properties ||= {}; container.required ||= [];
        const node = container.properties[key] ||= {};
        if (index === segments.length - 1) {
          node.type = row.type;
          if (row.description) node.description = row.description; else delete node.description;
          if (row.enum.length) node.enum = row.enum; else delete node.enum;
          if (row.default !== undefined) node.default = row.default; else delete node.default;
          if (row.type === 'object') { node.properties ||= {}; node.required ||= []; node.additionalProperties = false; }
          if (row.type === 'array') node.items ||= { type: 'string' };
        }
        if (row.required && !container.required.includes(key)) container.required.push(key);
        if (array) {
          node.type = 'array'; node.items ||= { type: 'object', properties: {}, required: [], additionalProperties: false };
          if (index < segments.length - 1) { node.items.type = 'object'; node.items.properties ||= {}; node.items.required ||= []; node.items.additionalProperties = false; }
          container = node.items;
        } else if (index < segments.length - 1) {
          node.type = 'object'; node.properties ||= {}; node.required ||= []; node.additionalProperties = false;
          container = node;
        }
      });
    });
    return schema;
  }

  function schemaExample(value) {
    const type = normalizedSchemaType(value);
    if (value?.default !== undefined) return value.default;
    if (Array.isArray(value?.enum) && value.enum.length) return value.enum[0];
    if (type === 'object') return Object.fromEntries(Object.entries(value.properties || {}).map(([key, child]) => [key, schemaExample(child)]));
    if (type === 'array') return [schemaExample(value.items || { type: 'string' })];
    if (type === 'number') return 1.5;
    if (type === 'integer') return 1;
    if (type === 'boolean') return true;
    return '示例文本';
  }

  function syncSchemaBuilder() {
    const error = document.getElementById('workflowSchemaError');
    try {
      const schema = schemaFromBuilder();
      $('workflowResponseSchema').value = JSON.stringify(schema, null, 2);
      document.getElementById('workflowSchemaExample').textContent = JSON.stringify(schemaExample(schema), null, 2);
      error.hidden = true; error.textContent = '';
      setDirty(true);
      return schema;
    } catch (reason) {
      error.hidden = false; error.textContent = reason.message;
      return null;
    }
  }

  function inspectorMarkup(node) {
    ensureNodeConfig(node);
    if (editorState.panel === 'config') {
      let extra = '';
      if (node.kind === 'llm') extra = readonlyValue('Prompt Key', node.prompt_key, '稳定引用', '由流程版本锁定；Prompt 正文可在“Prompt”页签编辑。');
      if (['code', 'script'].includes(node.kind)) {
        const customOptions = editorState.functions.filter(item => item.scope === 'custom').map(item => `<option value="${escapeHtml(item.key)}" ${item.key === node.operation ? 'selected' : ''}>${escapeHtml(item.title)} · ${escapeHtml(item.key)}</option>`).join('');
        const systemOptions = editorState.functions.filter(item => item.scope !== 'custom').map(item => `<option value="${escapeHtml(item.key)}" ${item.key === node.operation ? 'selected' : ''}>${escapeHtml(item.title)} · ${escapeHtml(item.key)}</option>`).join('');
        const options = `${customOptions ? `<optgroup label="自定义公共 Function">${customOptions}</optgroup>` : ''}<optgroup label="系统公共 Function（只读）">${systemOptions}</optgroup>`;
        const modeContent = node.script_mode === 'inline'
          ? '<div class="workflow-inspector-note">源码只属于当前节点，请在“Function”页签中直接编辑；保存流程后随版本锁定。</div>'
          : `<div class="workflow-field"><label>公共 Function</label><select id="workflowNodeOperation" ${editorState.readonly ? 'disabled' : ''}>${options}</select><small class="workflow-field-help">自定义公共 Function 全局可编辑；系统公共 Function 只读，但两者都能被所有实验复用。</small><button type="button" class="workflow-mini-btn" data-open-function-manager>打开 Function 管理并查看源码</button></div>`;
        extra = `<div class="workflow-field"><label>执行方式</label><div class="workflow-script-mode"><label><input type="radio" name="workflow-script-mode" data-script-mode="shared" ${node.script_mode === 'shared' ? 'checked' : ''} ${editorState.readonly ? 'disabled' : ''}><span><strong>使用公共 Function</strong><small>系统或自定义，全局复用</small></span></label><label><input type="radio" name="workflow-script-mode" data-script-mode="inline" ${node.script_mode === 'inline' ? 'checked' : ''} ${editorState.readonly ? 'disabled' : ''}><span><strong>节点内联 Function</strong><small>仅当前节点使用</small></span></label></div></div>${modeContent}`;
      }
      if (node.kind === 'subflow') extra = `<div class="workflow-field"><label>引用流程</label><select id="workflowNodeSubflow" ${editorState.readonly ? 'disabled' : ''}>${FLOW_ORDER.filter(key => key !== editorState.currentKey).map(key => `<option value="${key}" ${key === node.subflow_key ? 'selected' : ''}>${key}</option>`).join('')}</select></div>`;
      if (node.kind === 'selector') extra = `<div class="workflow-field"><label>选择方式</label><select id="workflowSelectorMode" ${editorState.readonly ? 'disabled' : ''}><option value="boolean" ${(node.config.selector_mode || 'boolean') === 'boolean' ? 'selected' : ''}>布尔条件</option><option value="case" ${node.config.selector_mode === 'case' ? 'selected' : ''}>多分支匹配</option></select></div>`;
      const title = editorState.readonly ? readonlyValue('节点名称', node.title, '只读') : `<div class="workflow-field"><label>节点名称</label><input id="workflowNodeTitle" value="${escapeHtml(node.title)}"></div>`;
      return `${title}${readonlyValue('节点 ID', node.node_id, '稳定 ID', '供连线、版本差异与运行追踪使用，不是用户输入。')}${readonlyValue('节点类型', KIND_LABELS[node.kind] || node.kind)}${extra}`;
    }
    if (editorState.panel === 'contract') {
      const boundary = node.kind === 'start' ? `<div class="workflow-boundary-explainer"><strong>step_context 从哪里来？</strong><p>Start 由仿真运行时触发，没有上游节点。系统会把当前 Agent、虚拟时钟、位置、可见事件、记忆和触发原因封装成 <code>StepContext</code>，再通过 <code>step_context</code> 注入流程。</p><div class="workflow-path-examples"><code>{step_context.agent.name}</code><code>{step_context.clock.current_time}</code><code>{step_context.visible_events}</code></div></div>` : '';
      const canEditPorts = !editorState.readonly && !['start', 'end'].includes(node.kind);
      return `${boundary}<div class="workflow-section-title">输入变量</div><div id="workflowInputPorts">${contractRows(node, node.inputs, 'inputs')}</div>${canEditPorts ? '<button class="workflow-mini-btn" type="button" data-add-port="inputs">＋ 添加输入</button>' : ''}<div class="workflow-section-title" style="margin-top:17px">输出变量</div><div id="workflowOutputPorts">${contractRows(node, node.outputs, 'outputs')}</div>${canEditPorts ? '<button class="workflow-mini-btn" type="button" data-add-port="outputs">＋ 添加输出</button>' : ''}<div class="workflow-section-title" style="margin-top:18px">当前连线</div>${connectionsMarkup(node)}<div class="workflow-inspector-note">端口类型从下拉框选择。连线会校验类型；变量属性统一写成 <code>{变量名.属性名}</code>。</div>`;
    }
    if (editorState.panel === 'body') {
      if (node.kind === 'llm') {
        const content = currentFlowState().prompts[node.prompt_key] || '';
        const schema = JSON.stringify(node.config.response_schema, null, 2);
        return `${variablePickerMarkup(node, content)}<div class="workflow-field"><label>${escapeHtml(node.prompt_key)} · Prompt</label><textarea id="workflowNodeBody" spellcheck="false" ${editorState.readonly ? 'disabled' : ''}>${escapeHtml(content)}</textarea><small class="workflow-field-help">使用 <code>{输入变量.属性}</code> 引用数据；根变量名对应“输入输出”中声明的输入。</small></div>${schemaBuilderMarkup(node.config.response_schema)}<div class="workflow-field" id="workflowSchemaExpert" hidden><label>结构化输出 JSON Schema（专家模式）</label><textarea class="workflow-schema-editor" id="workflowResponseSchema" spellcheck="false" ${editorState.readonly ? 'disabled' : ''}>${escapeHtml(schema)}</textarea></div><div class="workflow-schema-status">模型输出必须包含 <code>res</code>，校验失败会按异常策略自动重试</div><div class="workflow-inspector-note">保存流程时，Prompt、输出 Schema 与画布一起形成不可变版本。</div>`;
      }
      if (['selector', 'if_else', 'switch', 'loop'].includes(node.kind)) return `<div class="workflow-field"><label>受限表达式</label><textarea id="workflowNodeBody" spellcheck="false" ${editorState.readonly ? 'disabled' : ''}>${escapeHtml(node.expression || '')}</textarea></div><div class="workflow-inspector-note">表达式只读取节点输入，不允许访问文件、网络或进程状态。</div>`;
      if (node.kind === 'variable_assigner') return `<div class="workflow-field"><label>变量路径</label><input id="workflowNodeBody" value="${escapeHtml(node.state_path || 'agent.state')}" ${editorState.readonly ? 'disabled' : ''}></div><div class="workflow-inspector-note">把输入写入本次 Agent 工作流状态；Run 间不共享。</div>`;
      if (node.kind === 'variable_aggregator') return `<div class="workflow-inspector-note">按输入端口顺序聚合第一个非空值；下游只引用统一输出端口。</div>`;
      if (['read_state', 'write_state'].includes(node.kind)) return `<div class="workflow-field"><label>状态路径</label><input id="workflowNodeBody" value="${escapeHtml(node.state_path || 'agent.state')}" ${editorState.readonly ? 'disabled' : ''}></div>`;
      if (['code', 'script'].includes(node.kind)) {
        if (node.script_mode === 'inline') return `<div class="workflow-field"><label>节点内联 Function</label><textarea class="workflow-script-code-editor" id="workflowNodeBody" spellcheck="false" ${editorState.readonly ? 'disabled' : ''}>${escapeHtml(node.script_source || INLINE_FUNCTION_TEMPLATE)}</textarea><small class="workflow-field-help">入口必须为 <code>def main(inputs, context)</code>，返回对象中的 key 应与输出端口对应。</small></div><div class="workflow-inspector-note">该源码只属于当前代码节点，随流程版本保存。禁止导入、文件/网络/进程访问、类、异步代码和无限循环。</div>`;
        const selectedFunction = editorState.functions.find(item => item.key === node.operation);
        return `${readonlyValue('公共 Function', node.operation, selectedFunction?.scope === 'custom' ? '自定义公共' : '系统公共')}${selectedFunction ? `<div class="workflow-function-source-head compact"><span>完整 Python 源码</span><code>${escapeHtml(selectedFunction.implementation)}</code></div><pre class="workflow-function-source inspector"><code>${escapeHtml(selectedFunction.source || '# 服务端未返回源码')}</code></pre>` : '<div class="workflow-schema-error">当前公共 Function 不存在，请重新选择。</div>'}<button type="button" class="workflow-mini-btn" data-open-function-manager>打开 Function 管理</button>`;
      }
      return `<div class="workflow-inspector-note">${escapeHtml(nodeSummary(node))}</div>`;
    }
    const retry = node.config.retry_policy || { max_attempts: 3, retry_on_schema_error: true };
    const retryFields = node.kind === 'llm' ? `<div class="workflow-field"><label>最大尝试次数</label><select id="workflowRetryAttempts" ${editorState.readonly ? 'disabled' : ''}>${[1, 2, 3, 4, 5].map(value => `<option value="${value}" ${value === retry.max_attempts ? 'selected' : ''}>${value} 次</option>`).join('')}</select></div><label class="workflow-check-row"><input type="checkbox" id="workflowRetrySchema" ${retry.retry_on_schema_error ? 'checked' : ''} ${editorState.readonly ? 'disabled' : ''}>结构化输出不符合 JSON Schema 时自动重试</label>` : '';
    return `<div class="workflow-field"><label>超时</label><select id="workflowTimeout" ${editorState.readonly ? 'disabled' : ''}>${[30, 60, 120].map(value => `<option value="${value}" ${value === (node.config.timeout_seconds || 30) ? 'selected' : ''}>${value} 秒</option>`).join('')}</select></div>${retryFields}<div class="workflow-field"><label>最终失败策略</label><select id="workflowFailurePolicy" ${editorState.readonly ? 'disabled' : ''}><option value="stop">停止当前流程</option><option value="error">进入错误分支</option></select></div><div class="workflow-inspector-note">Schema 校验发生在模型调用的重试循环内；达到最大次数后才执行最终失败策略。</div>`;
  }

  function bindInspector(node) {
    if (!node) return;
    $('workflowInspectorBody').querySelectorAll('[data-add-port]').forEach(button => button.addEventListener('click', () => {
      const prefix = button.dataset.addPort === 'inputs' ? 'new_input' : 'new_output';
      let name = prefix;
      let suffix = 2;
      while (node[button.dataset.addPort].some(port => port.name === name)) name = `${prefix}_${suffix++}`;
      node[button.dataset.addPort].push({ name, data_type: 'object', required: true, description: '' });
      setDirty(true); renderInspector(); renderNodes();
    }));
    $('workflowInspectorBody').querySelectorAll('[data-remove-port]').forEach(button => button.addEventListener('click', () => {
      const row = button.closest('[data-port-direction]');
      const direction = row.dataset.portDirection;
      const port = node[direction][Number(row.dataset.portIndex)];
      const flow = currentFlowState();
      flow.workflow.edges = flow.workflow.edges.filter(edge => direction === 'inputs'
        ? !(edge.target_node_id === node.node_id && edge.target_port === port.name)
        : !(edge.source_node_id === node.node_id && edge.source_port === port.name));
      node[direction].splice(Number(row.dataset.portIndex), 1);
      setDirty(true); renderInspector(); renderNodes();
    }));
    $('workflowInspectorBody').querySelectorAll('[data-remove-edge]').forEach(button => button.addEventListener('click', () => {
      currentFlowState().workflow.edges.splice(Number(button.dataset.removeEdge), 1);
      cancelConnection();
      setDirty(true); renderInspector(); renderNodes();
    }));
    $('workflowInspectorBody').querySelectorAll('[data-open-function-manager]').forEach(button => button.addEventListener('click', () => showWorkspaceView('functions')));
    $('workflowInspectorBody').querySelectorAll('[data-script-mode]').forEach(control => control.addEventListener('change', () => {
      if (!control.checked || editorState.readonly) return;
      node.script_mode = control.dataset.scriptMode;
      if (node.script_mode === 'inline') node.script_source ||= INLINE_FUNCTION_TEMPLATE;
      if (node.script_mode === 'shared' && !editorState.functions.some(item => item.key === node.operation)) node.operation = editorState.functions[0]?.key || 'identity';
      setDirty(true);
      renderInspector();
      renderNodes();
    }));
    $('workflowInspectorBody').querySelectorAll('[data-insert-variable]').forEach(button => button.addEventListener('click', () => {
      const textarea = $('workflowNodeBody');
      if (!textarea || textarea.disabled) return;
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      textarea.value = `${textarea.value.slice(0, start)}${button.dataset.insertVariable}${textarea.value.slice(end)}`;
      textarea.focus();
      textarea.setSelectionRange(start + button.dataset.insertVariable.length, start + button.dataset.insertVariable.length);
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
    }));
    $('workflowNodeBody')?.addEventListener('input', event => {
      if (editorState.readonly) return;
      if (node.kind === 'llm') currentFlowState().prompts[node.prompt_key] = event.target.value;
      else if (['code', 'script'].includes(node.kind) && node.script_mode === 'inline') node.script_source = event.target.value;
      else if (['selector', 'if_else', 'switch', 'loop'].includes(node.kind)) node.expression = event.target.value;
      else if (['variable_assigner', 'read_state', 'write_state'].includes(node.kind)) node.state_path = event.target.value;
      setDirty(true);
    });
    if (document.getElementById('workflowSchemaBuilder')) {
      document.getElementById('workflowSchemaExample').textContent = JSON.stringify(schemaExample(node.config.response_schema), null, 2);
      document.getElementById('workflowSchemaBuilder').addEventListener('input', syncSchemaBuilder);
      document.getElementById('workflowSchemaBuilder').addEventListener('change', syncSchemaBuilder);
      document.getElementById('workflowSchemaBuilder').addEventListener('click', event => {
        const remove = event.target.closest('[data-remove-schema-row]');
        if (!remove) return;
        remove.closest('[data-schema-row]').remove(); syncSchemaBuilder();
      });
      $('workflowInspectorBody').querySelector('[data-add-schema-field]')?.addEventListener('click', () => {
        let index = 1;
        const used = new Set([...document.getElementById('workflowSchemaBuilder').querySelectorAll('[data-schema-path]')].map(input => input.value));
        while (used.has(`res.field_${index}`)) index += 1;
        document.getElementById('workflowSchemaBuilder').insertAdjacentHTML('beforeend', schemaRowMarkup({ path: `res.field_${index}`, type: 'string', required: false, description: '', enum: '', default: '' }, Date.now()));
        syncSchemaBuilder(); document.getElementById('workflowSchemaBuilder').lastElementChild.querySelector('[data-schema-path]').focus();
      });
      $('workflowInspectorBody').querySelector('[data-toggle-schema-expert]')?.addEventListener('click', event => {
        const expert = document.getElementById('workflowSchemaExpert');
        if (editorState.readonly) {
          expert.hidden = !expert.hidden;
          event.currentTarget.textContent = expert.hidden ? '专家 JSON' : '返回可视化';
          return;
        }
        if (expert.hidden) {
          const schema = syncSchemaBuilder(); if (!schema) return;
          node.config.response_schema = schema; expert.hidden = false; event.currentTarget.textContent = '返回可视化';
        } else {
          try {
            const schema = JSON.parse($('workflowResponseSchema').value);
            if (schema?.type !== 'object' || !schema.properties?.res || !schema.required?.includes('res')) throw new Error('根节点必须包含必填字段 res');
            node.config.response_schema = schema; setDirty(true); renderInspector();
          } catch (reason) { report(new Error(`输出 JSON Schema 无效：${reason.message}`)); }
        }
      });
    }
    $('workflowResponseSchema')?.addEventListener('input', event => {
      setDirty(true);
      try {
        const schema = JSON.parse(event.target.value); document.getElementById('workflowSchemaExample').textContent = JSON.stringify(schemaExample(schema), null, 2);
        document.getElementById('workflowSchemaError').hidden = true;
      } catch (reason) { document.getElementById('workflowSchemaError').hidden = false; document.getElementById('workflowSchemaError').textContent = reason.message; }
    });
  }

  function applyInspector() {
    const node = selectedNode();
    if (!node || editorState.readonly) return;
    const title = $('workflowNodeTitle');
    if (title) node.title = title.value.trim() || node.title;
    const operation = $('workflowNodeOperation');
    if (operation) node.operation = operation.value;
    const subflow = $('workflowNodeSubflow');
    if (subflow) node.subflow_key = subflow.value;
    const selectorMode = $('workflowSelectorMode');
    if (selectorMode) node.config.selector_mode = selectorMode.value;
    const responseSchema = $('workflowResponseSchema');
    if (responseSchema) {
      try {
        const schema = JSON.parse(responseSchema.value);
        if (schema?.type !== 'object' || !schema.properties?.res || !schema.required?.includes('res')) {
          throw new Error('根节点必须是 object，并在 properties 与 required 中声明 res。');
        }
        node.config.response_schema = schema;
      } catch (error) {
        report(new Error(`输出 JSON Schema 无效：${error.message}`));
        responseSchema.focus();
        return;
      }
    }
    if (node.kind === 'llm') {
      const analysis = promptVariableAnalysis(node, currentFlowState().prompts[node.prompt_key] || '');
      if (analysis.unresolved.length) {
        report(new Error('Prompt 变量必须从当前 LLM Node 已声明的输入端口开始，例如 {context.daily_plan}。'));
        editorState.panel = 'body'; renderInspector();
        return;
      }
      if (!responseSchema && document.getElementById('workflowSchemaBuilder')) {
        const schema = syncSchemaBuilder();
        if (!schema) return;
        node.config.response_schema = schema;
      }
    }
    const retryAttempts = $('workflowRetryAttempts');
    if (retryAttempts) node.config.retry_policy.max_attempts = Number(retryAttempts.value);
    const retrySchema = $('workflowRetrySchema');
    if (retrySchema) node.config.retry_policy.retry_on_schema_error = retrySchema.checked;
    const timeout = $('workflowTimeout');
    if (timeout) node.config.timeout_seconds = Number(timeout.value);
    const failure = $('workflowFailurePolicy');
    if (failure) node.config.failure_policy = failure.value;
    let invalidPort = null;
    $('workflowInspectorBody').querySelectorAll('[data-port-direction]').forEach(row => {
      if (invalidPort) return;
      const port = node[row.dataset.portDirection][Number(row.dataset.portIndex)];
      const nameControl = row.querySelector('[data-port-name]');
      if (!nameControl) return;
      const nextName = nameControl.value.trim();
      if (!/^[a-z][a-z0-9_]{0,79}$/.test(nextName)) {
        invalidPort = { nameControl, nextName };
        return;
      }
      const previousName = port.name;
      port.name = nextName;
      port.data_type = row.querySelector('[data-port-type]').value || 'any';
      port.description = row.querySelector('[data-port-description]')?.value.trim() || '';
      currentFlowState().workflow.edges.forEach(edge => {
        if (row.dataset.portDirection === 'inputs' && edge.target_node_id === node.node_id && edge.target_port === previousName) edge.target_port = nextName;
        if (row.dataset.portDirection === 'outputs' && edge.source_node_id === node.node_id && edge.source_port === previousName) edge.source_port = nextName;
      });
    });
    if (invalidPort) {
      report(new Error(`变量名 ${invalidPort.nextName || '（空）'} 无效：请使用小写字母、数字和下划线，并以字母开头。`));
      invalidPort.nameControl.focus();
      return;
    }
    setDirty(true);
    renderCurrentFlow();
    notify('节点配置已应用到当前草稿；保存后生成新版本。', '节点已更新');
  }

  function addNode(kind) {
    const flow = currentFlowState();
    if (!flow || editorState.readonly) return;
    if (kind === 'start' || kind === 'end') {
      const existing = flow.workflow.nodes.find(node => node.kind === kind);
      if (existing) {
        editorState.selectedNodeId = existing.node_id;
        renderNodes(); renderInspector();
        notify(`每个流程只能有一个 ${KIND_LABELS[kind]} 节点。`, '已定位现有节点');
        return;
      }
    }
    const suffix = Date.now().toString(36).slice(-6);
    const nodeId = `custom_${kind}_${suffix}`;
    const end = flow.workflow.nodes.find(node => node.kind === 'end');
    const node = {
      node_id: nodeId,
      kind,
      title: `自定义 ${KIND_LABELS[kind] || kind}`,
      inputs: [{ name: 'input', data_type: 'any', required: true, description: '' }],
      outputs: [{ name: 'result', data_type: 'any', required: true, description: '' }],
      position: { x: 36, y: Math.max(180, Number(end?.position.y || 800) - 130) },
      prompt_key: null, operation: null, script_mode: null, script_source: null, expression: null, state_path: null, subflow_key: null, config: {},
    };
    if (kind === 'llm') {
      node.prompt_key = `custom_prompt_${suffix}`;
      flow.prompts[node.prompt_key] = '请根据输入上下文完成任务，并返回符合输出契约的结果。';
    }
    if (kind === 'code') { node.operation = 'identity'; node.script_mode = 'shared'; }
    if (kind === 'selector') { node.expression = 'bool(input)'; node.config.selector_mode = 'boolean'; node.outputs = [{ name: 'true_result', data_type: 'any', required: true, description: '' }, { name: 'false_result', data_type: 'any', required: true, description: '' }]; }
    if (kind === 'variable_assigner') node.state_path = 'agent.state';
    if (kind === 'variable_aggregator') node.config.groups = { result: ['input'] };
    if (kind === 'subflow') node.subflow_key = FLOW_ORDER.find(key => key !== editorState.currentKey);
    ensureNodeConfig(node);

    if (end) {
      const incoming = flow.workflow.edges.find(edge => edge.target_node_id === end.node_id);
      if (incoming) {
        incoming.target_node_id = node.node_id;
        incoming.target_port = node.inputs[0].name;
        flow.workflow.edges.push({
          source_node_id: node.node_id, source_port: node.outputs[0].name,
          target_node_id: end.node_id, target_port: end.inputs[0].name,
          branch: 'always', case_value: null,
        });
        end.position.y = Number(end.position.y) + 170;
      }
    }
    flow.workflow.nodes.push(node);
    editorState.selectedNodeId = node.node_id;
    editorState.panel = kind === 'llm' ? 'body' : 'contract';
    setDirty(true);
    renderCurrentFlow();
  }

  function deleteSelectedNode() {
    const flow = currentFlowState();
    const node = selectedNode();
    if (!flow || !node || editorState.readonly) return;
    if (node.kind === 'start' || node.kind === 'end') {
      notify('Start 与 End 是流程边界，不能删除。', '保留流程边界');
      return;
    }
    const incoming = flow.workflow.edges.filter(edge => edge.target_node_id === node.node_id);
    const outgoing = flow.workflow.edges.filter(edge => edge.source_node_id === node.node_id);
    flow.workflow.edges = flow.workflow.edges.filter(edge => edge.source_node_id !== node.node_id && edge.target_node_id !== node.node_id);
    if (incoming.length === 1 && outgoing.length === 1) {
      flow.workflow.edges.push({
        source_node_id: incoming[0].source_node_id,
        source_port: incoming[0].source_port,
        target_node_id: outgoing[0].target_node_id,
        target_port: outgoing[0].target_port,
        branch: incoming[0].branch,
        case_value: incoming[0].case_value,
      });
    }
    flow.workflow.nodes = flow.workflow.nodes.filter(item => item.node_id !== node.node_id);
    editorState.selectedNodeId = flow.workflow.nodes.find(item => item.kind === 'llm')?.node_id || flow.workflow.nodes[0]?.node_id;
    setDirty(true);
    renderCurrentFlow();
  }

  function autoLayout(direction = 'vertical') {
    const flow = currentFlowState();
    if (!flow || editorState.readonly) return;
    const history = editorState.layoutHistory.get(editorState.currentKey) || [];
    history.push({
      positions: Object.fromEntries(flow.workflow.nodes.map(node => [node.node_id, structuredClone(node.position)])),
      canvasWidth: flow.canvasWidth || null,
      canvasHeight: flow.canvasHeight || null,
    });
    editorState.layoutHistory.set(editorState.currentKey, history.slice(-10));
    $('workflowUndoLayoutBtn').disabled = false;
    editorState.layoutDirection = direction;
    const scope = $('workflowLayoutScope')?.value || 'all';
    applyReadableLayout(flow, direction, { scope, mark: true });
    setDirty(true);
    editorState.viewports.delete(editorState.currentKey);
    renderCurrentFlow();
    requestAnimationFrame(fitAllNodes);
  }

  function undoAutoLayout() {
    const flow = currentFlowState();
    const history = editorState.layoutHistory.get(editorState.currentKey) || [];
    const snapshot = history.pop();
    if (!flow || !snapshot) return;
    flow.workflow.nodes.forEach(node => { if (snapshot.positions[node.node_id]) node.position = snapshot.positions[node.node_id]; });
    flow.canvasWidth = snapshot.canvasWidth;
    flow.canvasHeight = snapshot.canvasHeight;
    flow.layoutModel = buildLayerModel(flow.workflow);
    editorState.layoutHistory.set(editorState.currentKey, history);
    $('workflowUndoLayoutBtn').disabled = history.length === 0;
    setDirty(true); renderCurrentFlow(); fitAllNodes();
  }

  function renderVersions() {
    const flow = currentFlowState();
    if (!flow) return;
    $('workflowVersionTitle').textContent = `${flow.workflow.title} · 版本记录`;
    const current = editorState.readonly
      ? `<div class="workflow-version-item"><div><strong>已发布 Revision ${String(editorState.revision?.revision_no || '').padStart(3, '0')}</strong><small>运行绑定快照，只读查看</small></div><span class="chip blue">只读</span></div>`
      : `<div class="workflow-version-item"><div><strong>当前草稿</strong><small>${flow.dirty ? '有未保存修改' : '与最新保存版本一致'}</small></div><span class="chip teal">当前</span></div>`;
    $('workflowVersionList').innerHTML = current + flow.detail.versions.map(version => {
      const label = version.is_default ? '' : ` · ${escapeHtml(version.label)}`;
      const badge = version.is_default ? '<span class="workflow-default-badge">默认流程</span>' : '';
      return `<div class="workflow-version-item"><div><strong>版本 ${version.version_no}${label}${badge}</strong><small>${new Date(version.created_at).toLocaleString('zh-CN')}</small></div><button type="button" data-restore-version="${version.id}" ${editorState.readonly ? 'disabled' : ''}>一键恢复</button></div>`;
    }).join('');
    $('workflowVersionList').querySelectorAll('[data-restore-version]').forEach(button => button.addEventListener('click', () => restoreVersion(button.dataset.restoreVersion).catch(report)));
  }

  function toggleVersionPopover(open) {
    $('workflowVersionPopover').hidden = !open;
    if (open) renderVersions();
  }

  async function restoreVersion(versionId) {
    const flow = currentFlowState();
    if (!flow || editorState.readonly) return;
    const restored = await api(`/experiments/${editorState.experimentId}/draft/workflows/${editorState.currentKey}/versions/${versionId}/restore`, {
      method: 'POST', body: JSON.stringify({ lock_version: editorState.draft.lock_version }),
    });
    flow.detail = restored;
    flow.workflow = structuredClone(restored.workflow);
    flow.workflow.nodes.forEach(ensureNodeConfig);
    if (!flow.workflow.nodes.some(node => node.config?.manual_positioned || node.config?.layout_version === 2)) {
      applyReadableLayout(flow, 'horizontal');
    } else {
      flow.layoutModel = buildLayerModel(flow.workflow);
    }
    flow.prompts = Object.fromEntries(Object.entries(restored.prompts).map(([key, value]) => [key, normalizePromptVariables(value.content)]));
    flow.dirty = false;
    editorState.draft = await api(`/experiments/${editorState.experimentId}/draft`);
    updateDraft(editorState.draft);
    editorState.selectedNodeId = flow.workflow.nodes.find(node => node.kind === 'llm')?.node_id || flow.workflow.nodes[0]?.node_id;
    toggleVersionPopover(false);
    renderCurrentFlow();
    setDirty(false);
    notify(`已一键恢复并生成版本 V${restored.restored_as_version_no}；原历史版本保持不变。`, '流程已恢复');
  }

  async function validateCurrent() {
    if (editorState.ownerType === 'brain') {
      notify('大脑会在发布时统一校验 5 个流程与 Prompt。', '发布校验');
      return;
    }
    const result = await api(`/experiments/${editorState.experimentId}/draft/workflows/${editorState.currentKey}/validate`, { method: 'POST' });
    if (!result.valid) throw new Error(result.errors.map(item => item.message).join('；'));
    notify('节点结构、端口类型、Prompt 放置和代码函数注册均有效。', '流程验证通过');
  }

  function defaultTrialInputs() {
    const promptKey = currentFlowState()?.workflow.nodes.find(node => node.kind === 'llm')?.prompt_key || null;
    return {
      step_context: {
        trigger: editorState.currentKey === 'schedule' ? 'new_day' : 'step',
        agent: { key: 'trial-agent', name: '试运行 Agent' },
        clock: new Date().toISOString(),
        memories: [],
        visible_events: [],
        prompt_key: promptKey,
        prompt_request: { prompt: '工作流试运行', failsafe: null, retry: 1 },
      },
    };
  }

  function closeWorkflowTrial() {
    $('workflowTrialModal').classList.remove('open');
  }

  function openWorkflowTrial() {
    if (editorState.readonly || !currentFlowState()) return;
    $('workflowTrialInputs').value = JSON.stringify(defaultTrialInputs(), null, 2);
    $('workflowTrialLlmOutputs').value = '{}';
    $('workflowTrialResult').innerHTML = '<p>点击“执行试运行”后，这里会显示每个节点的成功、跳过或失败状态。</p>';
    $('workflowTrialModal').classList.add('open');
    requestAnimationFrame(() => $('workflowTrialInputs').focus());
  }

  function renderTrialResult(result, error = null) {
    const trace = result?.trace || error?.details?.trace || [];
    const traceMarkup = trace.map(item => {
      const skipped = item.status === 'SKIPPED' || item.status === 'BOUNDARY';
      return `<div class="workflow-trial-node"><b class="${skipped ? 'skipped' : ''}">${escapeHtml(item.status)}</b><code>${escapeHtml(item.node_id)} · ${escapeHtml(KIND_LABELS[item.node_kind] || item.node_kind)}</code><span>${escapeHtml((item.input_ports || []).join(', ') || '无输入')} → ${escapeHtml((item.output_ports || []).join(', ') || '无输出')}</span></div>`;
    }).join('');
    const status = error ? 'FAILED' : result.status;
    const output = error ? (error.message || String(error)) : JSON.stringify(result.output, null, 2);
    $('workflowTrialResult').innerHTML = `<div class="workflow-trial-summary"><strong>${escapeHtml(status)}</strong><span>${trace.length} 条节点轨迹</span></div><pre class="workflow-trial-output">${escapeHtml(output)}</pre><div class="workflow-trial-trace">${traceMarkup || '<p>没有产生节点轨迹。</p>'}</div>`;
  }

  async function testRunCurrent() {
    const flow = currentFlowState();
    if (!flow || editorState.readonly) return;
    let inputs;
    let llmOutputs;
    try {
      inputs = JSON.parse($('workflowTrialInputs').value || '{}');
      llmOutputs = JSON.parse($('workflowTrialLlmOutputs').value || '{}');
    } catch (error) {
      renderTrialResult(null, new Error(`测试输入不是有效 JSON：${error.message}`));
      return;
    }
    $('workflowTrialExecute').disabled = true;
    $('workflowTrialResult').innerHTML = '<p>正在执行真实数据流……</p>';
    try {
      const result = await api(`${workflowApiRoot()}/${editorState.currentKey}/test-run`, {
        method: 'POST',
        body: JSON.stringify({ workflow: flow.workflow, inputs, llm_outputs: llmOutputs }),
      });
      renderTrialResult(result);
    } catch (error) {
      renderTrialResult(null, error);
    } finally {
      $('workflowTrialExecute').disabled = false;
    }
  }

  async function migrateLegacyRouter() {
    const flow = currentFlowState();
    if (!flow || editorState.readonly || flow.workflow.execution_mode === 'prompt_router') return;
    const accepted = window.confirm('迁移会用真实 Prompt 路由图替换当前旧版画布；实验流程会保留历史版本。是否继续？');
    if (!accepted) return;
    const saved = await api(`${workflowApiRoot()}/${editorState.currentKey}/migrate-router`, {
      method: 'POST',
      body: JSON.stringify({ lock_version: editorState.draft.lock_version }),
    });
    flow.detail = saved;
    flow.workflow = structuredClone(saved.workflow);
    flow.workflow.nodes.forEach(ensureNodeConfig);
    flow.prompts = Object.fromEntries(Object.entries(saved.prompts).map(([key, value]) => [key, normalizePromptVariables(value.content)]));
    flow.dirty = false;
    editorState.draft.lock_version = saved.lock_version;
    if (editorState.ownerType === 'experiment') {
      updateDraft(await api(`/experiments/${editorState.experimentId}/draft`));
    }
    editorState.selectedNodeId = null;
    renderCurrentFlow();
    notify('旧版后置钩子已替换为真实 Prompt 路由图；原实验流程仍可从版本记录恢复。', '迁移完成');
  }

  async function save({ silent = false } = {}) {
    if (!editorState.ownerId || !editorState.draft || editorState.readonly) return editorState.draft;
    const dirtyFlows = FLOW_ORDER.map(key => editorState.flows.get(key)).filter(flow => flow?.dirty);
    for (const flow of dirtyFlows) {
      const invalidPrompts = flow.workflow.nodes.filter(node => node.kind === 'llm').map(node => ({ node, analysis: promptVariableAnalysis(node, flow.prompts[node.prompt_key] || '') })).filter(item => item.analysis.unresolved.length);
      if (invalidPrompts.length) {
        editorState.currentKey = flow.workflow.workflow_key;
        editorState.selectedNodeId = invalidPrompts[0].node.node_id;
        editorState.panel = 'body'; renderCurrentFlow();
        throw new Error(`Prompt 变量必须使用明确的“输入变量.属性路径”；${invalidPrompts.length} 个 LLM 节点仍引用了未声明的根变量。`);
      }
      const savePath = editorState.ownerType === 'brain'
        ? `/brains/${editorState.ownerId}/draft/workflows/${flow.workflow.workflow_key}`
        : `/experiments/${editorState.experimentId}/draft/workflows/${flow.workflow.workflow_key}`;
      const saved = await api(savePath, {
        method: 'PUT',
        body: JSON.stringify({
          lock_version: editorState.draft.lock_version,
          workflow: flow.workflow,
          prompts: flow.prompts,
          label: '手动保存',
        }),
      });
      flow.detail = saved;
      flow.workflow = structuredClone(saved.workflow);
      flow.workflow.nodes.forEach(ensureNodeConfig);
      flow.prompts = Object.fromEntries(Object.entries(saved.prompts).map(([key, value]) => [key, normalizePromptVariables(value.content)]));
      flow.dirty = false;
      editorState.draft.lock_version = saved.lock_version;
    }
    if (dirtyFlows.length) {
      editorState.draft = editorState.ownerType === 'brain'
        ? { ...editorState.draft, lock_version: editorState.draft.lock_version }
        : await api(`/experiments/${editorState.experimentId}/draft`);
      if (editorState.ownerType === 'experiment') updateDraft(editorState.draft);
      renderCurrentFlow();
      setDirty(false);
      if (!silent) notify(`${dirtyFlows.length} 个流程已保存并生成新版本。`, '流程已保存');
    }
    return editorState.draft;
  }

  function discard() {
    editorState.flows.clear();
    editorState.viewports.clear();
    editorState.layoutHistory.clear();
    editorState.pan = null;
    editorState.selectedNodeId = null;
    editorState.generation += 1;
    window.dispatchEvent(new CustomEvent('workflow-editor:dirty', {
      detail: { dirty: false },
    }));
  }

  function toggleEditing() {
    document.querySelectorAll('[data-workflow-add],#workflowApplyNode,#workflowDeleteNode').forEach(control => { control.disabled = editorState.readonly; });
    document.querySelectorAll('[data-connect-direction]').forEach(control => { control.disabled = editorState.readonly; });
    $('workflowShell').classList.toggle('is-readonly', editorState.readonly);
    $('workflowReadonlyNotice').hidden = !editorState.readonly;
    if (editorState.readonly) {
      $('workflowReadonlyNotice').textContent = `已发布 Revision ${String(editorState.revision?.revision_no || '').padStart(3, '0')} · 只读查看`;
    }
    const connectHint = $('workflowConnectHint')?.querySelector('span');
    if (connectHint) {
      connectHint.textContent = editorState.readonly
        ? '点击节点查看配置与源码；空白处拖动画布，右下角可缩放和快速定位。'
        : '空白处按住左键拖动画布；点击输出端口连接节点。';
    }
    $('workflowAutoLayoutBtn').disabled = editorState.readonly;
    $('workflowHorizontalLayoutBtn').disabled = editorState.readonly;
    $('workflowMigrateRouterBtn').disabled = editorState.readonly;
    $('workflowTestRunBtn').disabled = editorState.readonly;
    $('workflowValidateBtn').disabled = editorState.readonly;
    $('workflowFunctionCreateBtn').disabled = editorState.readonly;
    $('workflowUndoLayoutBtn').disabled = editorState.readonly
      || !(editorState.layoutHistory.get(editorState.currentKey) || []).length;
  }

  document.addEventListener('DOMContentLoaded', () => {
    enableCanvasPan();
    document.querySelectorAll('[data-workflow-add]').forEach(button => button.addEventListener('click', () => addNode(button.dataset.workflowAdd)));
    document.querySelectorAll('[data-workflow-panel]').forEach(button => button.addEventListener('click', () => {
      editorState.panel = button.dataset.workflowPanel;
      renderInspector();
    }));
    $('workflowApplyNode').addEventListener('click', applyInspector);
    $('workflowDeleteNode').addEventListener('click', deleteSelectedNode);
    $('workflowAutoLayoutBtn').addEventListener('click', () => autoLayout('vertical'));
    $('workflowHorizontalLayoutBtn').addEventListener('click', () => autoLayout('horizontal'));
    $('workflowUndoLayoutBtn').addEventListener('click', undoAutoLayout);
    $('workflowFitBtn').addEventListener('click', fitAllNodes);
    $('workflowZoomOutBtn').addEventListener('click', () => setCanvasZoom(currentCanvasZoom() - CANVAS_ZOOM_STEP));
    $('workflowZoomResetBtn').addEventListener('click', () => setCanvasZoom(1));
    $('workflowZoomInBtn').addEventListener('click', () => setCanvasZoom(currentCanvasZoom() + CANVAS_ZOOM_STEP));
    $('workflowLocateStartBtn').addEventListener('click', () => locateNode(currentFlowState()?.workflow.nodes.find(node => node.kind === 'start')));
    $('workflowLocateEndBtn').addEventListener('click', () => locateNode(currentFlowState()?.workflow.nodes.find(node => node.kind === 'end')));
    $('workflowLocateIssueBtn').addEventListener('click', () => {
      const node = workflowIssueNode();
      if (node) locateNode(node); else notify('没有发现断线节点；可继续执行完整流程校验。', '未发现画布异常');
    });
    $('workflowNodeSearch').addEventListener('input', event => {
      editorState.searchQuery = event.target.value.trim().toLowerCase(); renderNodes();
    });
    $('workflowNodeSearch').addEventListener('keydown', event => {
      if (event.key !== 'Enter') return;
      const query = event.currentTarget.value.trim().toLowerCase();
      const node = currentFlowState()?.workflow.nodes.find(item => `${item.title} ${item.node_id} ${item.kind}`.toLowerCase().includes(query));
      if (node) locateNode(node); else notify('没有找到匹配节点。', '搜索结果');
    });
    $('workflowTestRunBtn').addEventListener('click', openWorkflowTrial);
    $('workflowMigrateRouterBtn').addEventListener('click', () => migrateLegacyRouter().catch(report));
    $('workflowTrialClose').addEventListener('click', closeWorkflowTrial);
    $('workflowTrialCancel').addEventListener('click', closeWorkflowTrial);
    $('workflowTrialExecute').addEventListener('click', () => testRunCurrent().catch(report));
    $('workflowTrialModal').addEventListener('click', event => {
      if (event.target === event.currentTarget) closeWorkflowTrial();
    });
    $('workflowValidateBtn').addEventListener('click', () => validateCurrent().catch(report));
    $('workflowVersionBtn').addEventListener('click', event => { event.stopPropagation(); toggleVersionPopover($('workflowVersionPopover').hidden); });
    $('workflowVersionClose').addEventListener('click', () => toggleVersionPopover(false));
    $('workflowFunctionManagerBtn').addEventListener('click', () => showWorkspaceView('functions'));
    $('workflowFunctionCreateBtn').addEventListener('click', () => openFunctionEditor());
    $('workflowFunctionBackBtn').addEventListener('click', () => showWorkspaceView('flow'));
    $('workflowVersionPopover').addEventListener('click', event => event.stopPropagation());
    document.addEventListener('click', () => toggleVersionPopover(false));
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && editorState.pendingConnection) cancelConnection();
      if (event.key === 'Escape' && $('workflowTrialModal').classList.contains('open')) closeWorkflowTrial();
    });
    $('workflowCanvasScroller').addEventListener('scroll', () => { rememberCanvasViewport(); renderMinimap(); }, { passive: true });
    $('workflowCanvasScroller').addEventListener('wheel', event => {
      if (!(event.ctrlKey || event.metaKey) || compactCanvas()) return;
      event.preventDefault();
      setCanvasZoom(currentCanvasZoom() + (event.deltaY < 0 ? CANVAS_ZOOM_STEP : -CANVAS_ZOOM_STEP));
    }, { passive: false });
    $('workflowMinimap').addEventListener('click', event => {
      const rect = event.currentTarget.getBoundingClientRect();
      const board = $('workflowBoard'); const scroller = $('workflowCanvasScroller');
      const targetX = (event.clientX - rect.left) / rect.width * board.offsetWidth;
      const targetY = (event.clientY - rect.top) / rect.height * board.offsetHeight;
      const zoom = currentCanvasZoom();
      scroller.scrollTo({ left: Math.max(0, targetX * zoom - scroller.clientWidth / 2), top: Math.max(0, targetY * zoom - scroller.clientHeight / 2), behavior: 'smooth' });
    });
    window.addEventListener('resize', () => {
      updateBoardExtent();
      syncBoardStage();
      drawEdges();
    });
  });

  window.WorkflowEditor = {
    setContext,
    activate,
    save,
    discard,
    isDirty: () => [...editorState.flows.values()].some(flow => flow.dirty),
    currentWorkflowKey: () => editorState.currentKey,
  };
})();
