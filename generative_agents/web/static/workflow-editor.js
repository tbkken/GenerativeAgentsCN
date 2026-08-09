(() => {
  'use strict';

  const FLOW_ORDER = ['schedule', 'memory', 'action', 'social', 'reflection'];
  const KIND_LABELS = {
    start: 'Start', end: 'End', script: 'Script', llm: 'LLM', if_else: 'If / Else',
    switch: 'Switch', loop: 'Loop', parallel: 'Parallel / Join', read_state: 'Read State',
    write_state: 'Write State', subflow: 'Subflow',
  };
  const BODY_LABELS = {
    llm: 'Prompt', script: 'Function', if_else: '条件', switch: '规则', loop: '循环',
    read_state: '状态', write_state: '状态', subflow: '子流程',
  };
  const SCRIPT_OPERATIONS = [
    'identity', 'merge_context', 'select_fields', 'normalize_list',
    'schedule_prepare_context', 'memory_prepare_context', 'action_prepare_context',
    'social_prepare_context', 'reflection_prepare_context',
  ];
  const $ = id => document.getElementById(id);
  const editorState = {
    experimentId: null,
    draft: null,
    readonly: true,
    active: false,
    list: [],
    currentKey: 'schedule',
    flows: new Map(),
    selectedNodeId: null,
    panel: 'contract',
    generation: 0,
    drag: null,
  };

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[char]));
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

  function renderVersionLabel(flow = currentFlowState()) {
    if (!flow) return;
    const latest = Math.max(1, ...flow.detail.versions.map(item => item.version_no));
    $('workflowCanvasVersion').textContent = `flow.${flow.workflow.workflow_key}@v${latest}${flow.dirty ? ' · 未保存' : ''}`;
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
    window.dispatchEvent(new CustomEvent('workflow-editor:draft', { detail: { draft } }));
  }

  async function setContext({ experimentId, draft, readonly = false }) {
    const changedOwner = experimentId !== editorState.experimentId;
    editorState.experimentId = experimentId || null;
    editorState.draft = draft || null;
    editorState.readonly = readonly || !draft;
    if (changedOwner) {
      editorState.list = [];
      editorState.flows.clear();
      editorState.currentKey = 'schedule';
      editorState.selectedNodeId = null;
      editorState.generation += 1;
    }
    toggleEditing();
    if (editorState.active && editorState.experimentId && editorState.draft) await activate();
  }

  async function activate() {
    editorState.active = true;
    if (!editorState.experimentId || !editorState.draft) {
      renderUnavailable('当前实验没有可编辑草稿');
      return;
    }
    const generation = ++editorState.generation;
    $('workflowShell').setAttribute('aria-busy', 'true');
    try {
      const listing = await api(`/experiments/${editorState.experimentId}/draft/workflows`);
      if (generation !== editorState.generation) return;
      editorState.list = listing.items;
      editorState.draft.lock_version = listing.lock_version;
      renderTabs();
      const nextKey = editorState.list.some(item => item.workflow_key === editorState.currentKey)
        ? editorState.currentKey : editorState.list[0]?.workflow_key;
      if (nextKey) await selectFlow(nextKey, { force: !editorState.flows.has(nextKey) });
    } catch (error) {
      if (generation === editorState.generation) renderUnavailable(error.message);
      throw error;
    } finally {
      if (generation === editorState.generation) $('workflowShell').setAttribute('aria-busy', 'false');
    }
  }

  function renderUnavailable(message) {
    $('workflowBoardEmpty').hidden = false;
    $('workflowBoardEmpty').querySelector('strong').textContent = message;
    $('workflowBoardEmpty').querySelector('span').textContent = '请返回实验概览检查 Draft 状态。';
    $('workflowNodeLayer').innerHTML = '';
    $('workflowWires').innerHTML = '';
  }

  function renderTabs() {
    const byKey = new Map(editorState.list.map(item => [item.workflow_key, item]));
    $('workflowTabs').innerHTML = FLOW_ORDER.filter(key => byKey.has(key)).map(key => {
      const item = byKey.get(key);
      return `<button type="button" class="workflow-tab" role="tab" data-workflow-key="${key}" aria-selected="${key === editorState.currentKey}">${escapeHtml(item.title)}</button>`;
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

  async function selectFlow(key, { force = false } = {}) {
    if (!key || !editorState.experimentId) return;
    editorState.currentKey = key;
    renderTabs();
    let flow = editorState.flows.get(key);
    if (!flow || force) {
      const generation = editorState.generation;
      const detail = await api(`/experiments/${editorState.experimentId}/draft/workflows/${key}`);
      if (generation !== editorState.generation || key !== editorState.currentKey) return;
      flow = {
        detail,
        workflow: structuredClone(detail.workflow),
        prompts: Object.fromEntries(Object.entries(detail.prompts).map(([promptKey, value]) => [promptKey, value.content])),
        dirty: false,
      };
      editorState.flows.set(key, flow);
      editorState.draft.lock_version = detail.lock_version;
    }
    editorState.selectedNodeId = flow.workflow.nodes.find(node => node.kind === 'llm')?.node_id
      || flow.workflow.nodes[0]?.node_id || null;
    editorState.panel = 'contract';
    renderCurrentFlow();
  }

  function renderCurrentFlow() {
    const flow = currentFlowState();
    if (!flow) return;
    $('workflowCanvasTitle').textContent = flow.workflow.title;
    renderVersionLabel(flow);
    $('workflowTabs').querySelectorAll('[data-workflow-key]').forEach(button => {
      button.setAttribute('aria-selected', String(button.dataset.workflowKey === editorState.currentKey));
    });
    renderNodes();
    renderInspector();
    toggleEditing();
  }

  function nodeSummary(node) {
    if (node.kind === 'llm') return `prompt · ${node.prompt_key}`;
    if (node.kind === 'script') return `function · ${node.operation}`;
    if (node.kind === 'subflow') return `subflow · ${node.subflow_key}`;
    if (node.kind === 'read_state' || node.kind === 'write_state') return node.state_path || 'agent.state';
    return `${node.kind} · ${node.node_id}`;
  }

  function portMarkup(port, direction) {
    return `<span class="workflow-node-port ${direction}"><b>${direction === 'output' ? '输出' : '输入'}</b><code>${escapeHtml(port.name)}</code><em>${escapeHtml(port.data_type)}</em></span>`;
  }

  function nodeMarkup(node) {
    const firstInput = node.inputs[0] ? portMarkup(node.inputs[0], 'input') : '';
    const firstOutput = node.outputs[0] ? portMarkup(node.outputs[0], 'output') : '';
    const more = Math.max(0, node.inputs.length - 1) + Math.max(0, node.outputs.length - 1);
    return `<button type="button" class="workflow-node${node.node_id === editorState.selectedNodeId ? ' selected' : ''}${node.unconnected ? ' unconnected' : ''}" data-node-id="${escapeHtml(node.node_id)}" data-kind="${node.kind}" style="left:${node.position.x}%;top:${node.position.y}px" ${editorState.readonly ? '' : 'draggable="false"'}><span class="workflow-node-head"><span class="workflow-node-kind">${escapeHtml(KIND_LABELS[node.kind] || node.kind)}</span><span class="workflow-node-menu">•••</span></span><span class="workflow-node-copy"><strong>${escapeHtml(node.title)}</strong><small>${escapeHtml(nodeSummary(node))}</small></span><span class="workflow-node-ports">${firstInput}${firstOutput}${more ? `<span class="workflow-node-more">另有 ${more} 个数据端口</span>` : ''}</span></button>`;
  }

  function renderNodes() {
    const flow = currentFlowState();
    $('workflowNodeLayer').innerHTML = flow.workflow.nodes.map(nodeMarkup).join('');
    $('workflowBoardEmpty').hidden = flow.workflow.nodes.length > 0;
    const maxY = Math.max(900, ...flow.workflow.nodes.map(node => Number(node.position.y) + 210));
    $('workflowBoard').style.minHeight = `${maxY}px`;
    $('workflowNodeLayer').querySelectorAll('.workflow-node').forEach(button => {
      button.addEventListener('click', () => {
        if (button.dataset.dragged === '1') return;
        editorState.selectedNodeId = button.dataset.nodeId;
        renderNodes();
        renderInspector();
      });
      if (!editorState.readonly) enableDrag(button, flow.workflow.nodes.find(node => node.node_id === button.dataset.nodeId));
    });
    requestAnimationFrame(drawEdges);
  }

  function enableDrag(button, node) {
    button.addEventListener('pointerdown', event => {
      if (event.button !== 0 || event.target.closest('input,select,textarea')) return;
      const board = $('workflowBoard');
      const boardRect = board.getBoundingClientRect();
      const nodeRect = button.getBoundingClientRect();
      editorState.drag = {
        pointerId: event.pointerId,
        node,
        button,
        board,
        boardRect,
        startX: event.clientX,
        startY: event.clientY,
        left: nodeRect.left - boardRect.left,
        top: nodeRect.top - boardRect.top,
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
      const dx = event.clientX - drag.startX;
      const dy = event.clientY - drag.startY;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      const left = Math.max(0, Math.min(drag.boardRect.width - button.offsetWidth, drag.left + dx));
      const top = Math.max(0, drag.top + dy);
      node.position.x = drag.boardRect.width ? (left / drag.boardRect.width) * 100 : 0;
      node.position.y = top;
      button.style.left = `${node.position.x}%`;
      button.style.top = `${top}px`;
      if (top + button.offsetHeight + 90 > drag.board.clientHeight) drag.board.style.minHeight = `${top + button.offsetHeight + 90}px`;
      drawEdges();
    });
    const finish = event => {
      const drag = editorState.drag;
      if (!drag || drag.pointerId !== event.pointerId || drag.button !== button) return;
      button.dataset.dragged = drag.moved ? '1' : '0';
      button.classList.remove('dragging');
      if (button.hasPointerCapture(event.pointerId)) button.releasePointerCapture(event.pointerId);
      if (drag.moved) setDirty(true);
      editorState.drag = null;
      setTimeout(() => { button.dataset.dragged = '0'; }, 0);
    };
    button.addEventListener('pointerup', finish);
    button.addEventListener('pointercancel', finish);
  }

  function drawEdges() {
    const flow = currentFlowState();
    if (!flow) return;
    const board = $('workflowBoard');
    const boardRect = board.getBoundingClientRect();
    if (!boardRect.width) return;
    const svg = $('workflowWires');
    svg.setAttribute('viewBox', `0 0 ${boardRect.width} ${boardRect.height}`);
    const paths = [`<defs><marker id="workflowArrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" fill="#a9bbb5"></path></marker></defs>`];
    flow.workflow.edges.forEach(edge => {
      const source = $('workflowNodeLayer').querySelector(`[data-node-id="${CSS.escape(edge.source_node_id)}"]`);
      const target = $('workflowNodeLayer').querySelector(`[data-node-id="${CSS.escape(edge.target_node_id)}"]`);
      if (!source || !target) return;
      const s = source.getBoundingClientRect();
      const t = target.getBoundingClientRect();
      const sx = s.left - boardRect.left + s.width / 2;
      const sy = s.bottom - boardRect.top;
      const tx = t.left - boardRect.left + t.width / 2;
      const ty = t.top - boardRect.top;
      const mid = sy + Math.max(20, (ty - sy) / 2);
      const d = Math.abs(tx - sx) < 2 ? `M ${sx} ${sy} L ${tx} ${ty}` : `M ${sx} ${sy} L ${sx} ${mid} L ${tx} ${mid} L ${tx} ${ty}`;
      paths.push(`<path class="workflow-wire ${edge.branch || ''}" d="${d}" marker-end="url(#workflowArrow)"></path>`);
    });
    svg.innerHTML = paths.join('');
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
    $('workflowDeleteNode').hidden = false;
    $('workflowInspectorBody').innerHTML = node ? inspectorMarkup(node) : '<div class="empty-state"><strong>尚未选择节点</strong></div>';
    document.querySelectorAll('[data-workflow-panel]').forEach(button => button.setAttribute('aria-selected', String(button.dataset.workflowPanel === editorState.panel)));
    bindInspector(node);
  }

  function contractRows(ports, direction) {
    if (!ports.length) return `<div class="workflow-inspector-note">该节点没有${direction === 'inputs' ? '输入' : '输出'}变量。</div>`;
    return ports.map((port, index) => `<div class="workflow-contract-row" data-port-direction="${direction}" data-port-index="${index}"><input data-port-name value="${escapeHtml(port.name)}" aria-label="变量名"><input data-port-type value="${escapeHtml(port.data_type)}" aria-label="变量类型"><button type="button" data-remove-port aria-label="删除变量">×</button></div>`).join('');
  }

  function inspectorMarkup(node) {
    if (editorState.panel === 'config') {
      let extra = '';
      if (node.kind === 'llm') extra = `<div class="workflow-field"><label>Prompt Key</label><input id="workflowNodePromptKey" value="${escapeHtml(node.prompt_key)}" readonly></div>`;
      if (node.kind === 'script') extra = `<div class="workflow-field"><label>注册 Function</label><select id="workflowNodeOperation">${SCRIPT_OPERATIONS.map(value => `<option ${value === node.operation ? 'selected' : ''}>${value}</option>`).join('')}</select></div>`;
      if (node.kind === 'subflow') extra = `<div class="workflow-field"><label>引用流程</label><select id="workflowNodeSubflow">${FLOW_ORDER.filter(key => key !== editorState.currentKey).map(key => `<option value="${key}" ${key === node.subflow_key ? 'selected' : ''}>${key}</option>`).join('')}</select></div>`;
      return `<div class="workflow-field"><label>节点名称</label><input id="workflowNodeTitle" value="${escapeHtml(node.title)}"></div><div class="workflow-field"><label>节点 ID</label><input value="${escapeHtml(node.node_id)}" readonly></div><div class="workflow-field"><label>节点类型</label><input value="${escapeHtml(KIND_LABELS[node.kind] || node.kind)}" readonly></div>${extra}`;
    }
    if (editorState.panel === 'contract') {
      return `<div class="workflow-section-title">输入变量</div><div id="workflowInputPorts">${contractRows(node.inputs, 'inputs')}</div><button class="workflow-mini-btn" type="button" data-add-port="inputs">＋ 添加输入</button><div class="workflow-section-title" style="margin-top:17px">输出变量</div><div id="workflowOutputPorts">${contractRows(node.outputs, 'outputs')}</div><button class="workflow-mini-btn" type="button" data-add-port="outputs">＋ 添加输出</button><div class="workflow-inspector-note">连线按变量类型校验；下游节点只能消费已声明的输出。</div>`;
    }
    if (editorState.panel === 'body') {
      if (node.kind === 'llm') {
        const content = currentFlowState().prompts[node.prompt_key] || '';
        return `<div class="workflow-field"><label>${escapeHtml(node.prompt_key)} · Prompt</label><textarea id="workflowNodeBody" spellcheck="false">${escapeHtml(content)}</textarea></div><div class="workflow-inspector-note">保存流程时，Prompt 正文与画布一起形成不可变版本。</div>`;
      }
      if (['if_else', 'switch', 'loop'].includes(node.kind)) return `<div class="workflow-field"><label>受限表达式</label><textarea id="workflowNodeBody" spellcheck="false">${escapeHtml(node.expression || '')}</textarea></div><div class="workflow-inspector-note">表达式只读取节点输入，不允许访问文件、网络或进程状态。</div>`;
      if (['read_state', 'write_state'].includes(node.kind)) return `<div class="workflow-field"><label>状态路径</label><input id="workflowNodeBody" value="${escapeHtml(node.state_path || 'agent.state')}"></div>`;
      if (node.kind === 'script') return `<div class="workflow-field"><label>Function</label><input value="${escapeHtml(node.operation)}" readonly></div><div class="workflow-inspector-note">Script 是已注册的确定性 Function，输入输出由数据契约固定；浏览器不会执行任意 Python 源码。</div>`;
      return `<div class="workflow-inspector-note">${escapeHtml(nodeSummary(node))}</div>`;
    }
    return `<div class="workflow-field"><label>超时</label><select><option>30 秒</option><option>60 秒</option><option>120 秒</option></select></div><div class="workflow-field"><label>失败策略</label><select><option>停止当前流程</option><option>重试 1 次</option><option>进入错误分支</option></select></div><div class="workflow-inspector-note">失败策略将在发布校验时确认是否与节点输出契约兼容。</div>`;
  }

  function bindInspector(node) {
    if (!node) return;
    $('workflowInspectorBody').querySelectorAll('[data-add-port]').forEach(button => button.addEventListener('click', () => {
      node[button.dataset.addPort].push({ name: button.dataset.addPort === 'inputs' ? 'new_input' : 'new_output', data_type: 'any', required: true, description: '' });
      setDirty(true); renderInspector(); renderNodes();
    }));
    $('workflowInspectorBody').querySelectorAll('[data-remove-port]').forEach(button => button.addEventListener('click', () => {
      const row = button.closest('[data-port-direction]');
      node[row.dataset.portDirection].splice(Number(row.dataset.portIndex), 1);
      setDirty(true); renderInspector(); renderNodes();
    }));
    $('workflowNodeBody')?.addEventListener('input', event => {
      if (node.kind === 'llm') currentFlowState().prompts[node.prompt_key] = event.target.value;
      else if (['if_else', 'switch', 'loop'].includes(node.kind)) node.expression = event.target.value;
      else if (['read_state', 'write_state'].includes(node.kind)) node.state_path = event.target.value;
      setDirty(true);
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
    $('workflowInspectorBody').querySelectorAll('[data-port-direction]').forEach(row => {
      const port = node[row.dataset.portDirection][Number(row.dataset.portIndex)];
      port.name = row.querySelector('[data-port-name]').value.trim();
      port.data_type = row.querySelector('[data-port-type]').value.trim() || 'any';
    });
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
      prompt_key: null, operation: null, expression: null, state_path: null, subflow_key: null, config: {},
    };
    if (kind === 'llm') {
      node.prompt_key = `custom_prompt_${suffix}`;
      flow.prompts[node.prompt_key] = '请根据输入上下文完成任务，并返回符合输出契约的结果。';
    }
    if (kind === 'script') node.operation = 'identity';
    if (['if_else', 'switch', 'loop'].includes(kind)) node.expression = 'bool(input)';
    if (['read_state', 'write_state'].includes(kind)) node.state_path = 'agent.state';
    if (kind === 'subflow') node.subflow_key = FLOW_ORDER.find(key => key !== editorState.currentKey);
    if (kind === 'start') node.inputs = [];
    if (kind === 'end') node.outputs = [];

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

  function autoLayout() {
    const flow = currentFlowState();
    if (!flow || editorState.readonly) return;
    const incoming = new Map(flow.workflow.nodes.map(node => [node.node_id, 0]));
    const next = new Map(flow.workflow.nodes.map(node => [node.node_id, []]));
    flow.workflow.edges.forEach(edge => {
      incoming.set(edge.target_node_id, (incoming.get(edge.target_node_id) || 0) + 1);
      next.get(edge.source_node_id)?.push(edge.target_node_id);
    });
    const queue = flow.workflow.nodes.filter(node => incoming.get(node.node_id) === 0).map(node => node.node_id);
    const levels = new Map();
    while (queue.length) {
      const id = queue.shift();
      const level = levels.get(id) || 0;
      next.get(id)?.forEach(target => {
        levels.set(target, Math.max(levels.get(target) || 0, level + 1));
        incoming.set(target, incoming.get(target) - 1);
        if (incoming.get(target) === 0) queue.push(target);
      });
    }
    const byLevel = new Map();
    flow.workflow.nodes.forEach(node => {
      const level = levels.get(node.node_id) || 0;
      if (!byLevel.has(level)) byLevel.set(level, []);
      byLevel.get(level).push(node);
    });
    byLevel.forEach((nodes, level) => nodes.forEach((node, index) => {
      node.position.y = 24 + level * 180;
      node.position.x = nodes.length === 1 ? 36 : 4 + index * (64 / Math.max(1, nodes.length - 1));
    }));
    setDirty(true); renderCurrentFlow();
  }

  function renderVersions() {
    const flow = currentFlowState();
    if (!flow) return;
    $('workflowVersionTitle').textContent = `${flow.workflow.title} · 版本记录`;
    const current = `<div class="workflow-version-item"><div><strong>当前草稿</strong><small>${flow.dirty ? '有未保存修改' : '与最新保存版本一致'}</small></div><span class="chip teal">当前</span></div>`;
    $('workflowVersionList').innerHTML = current + flow.detail.versions.map(version => {
      const label = version.is_default ? '' : ` · ${escapeHtml(version.label)}`;
      const badge = version.is_default ? '<span class="workflow-default-badge">默认流程</span>' : '';
      return `<div class="workflow-version-item"><div><strong>版本 ${version.version_no}${label}${badge}</strong><small>${new Date(version.created_at).toLocaleString('zh-CN')}</small></div><button type="button" data-restore-version="${version.id}">一键恢复</button></div>`;
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
    flow.prompts = Object.fromEntries(Object.entries(restored.prompts).map(([key, value]) => [key, value.content]));
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
    const result = await api(`/experiments/${editorState.experimentId}/draft/workflows/${editorState.currentKey}/validate`, { method: 'POST' });
    if (!result.valid) throw new Error(result.errors.map(item => item.message).join('；'));
    notify('节点结构、端口类型、Prompt 放置和 Script 注册均有效。', '流程验证通过');
  }

  async function save({ silent = false } = {}) {
    if (!editorState.experimentId || !editorState.draft || editorState.readonly) return editorState.draft;
    const dirtyFlows = FLOW_ORDER.map(key => editorState.flows.get(key)).filter(flow => flow?.dirty);
    for (const flow of dirtyFlows) {
      const saved = await api(`/experiments/${editorState.experimentId}/draft/workflows/${flow.workflow.workflow_key}`, {
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
      flow.prompts = Object.fromEntries(Object.entries(saved.prompts).map(([key, value]) => [key, value.content]));
      flow.dirty = false;
      editorState.draft.lock_version = saved.lock_version;
    }
    if (dirtyFlows.length) {
      editorState.draft = await api(`/experiments/${editorState.experimentId}/draft`);
      updateDraft(editorState.draft);
      renderCurrentFlow();
      setDirty(false);
      if (!silent) notify(`${dirtyFlows.length} 个流程已保存并生成新版本。`, '流程已保存');
    }
    return editorState.draft;
  }

  function discard() {
    editorState.flows.clear();
    editorState.selectedNodeId = null;
    editorState.generation += 1;
    window.dispatchEvent(new CustomEvent('workflow-editor:dirty', {
      detail: { dirty: false },
    }));
  }

  function toggleEditing() {
    document.querySelectorAll('[data-workflow-add],#workflowApplyNode,#workflowDeleteNode').forEach(control => { control.disabled = editorState.readonly; });
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-workflow-add]').forEach(button => button.addEventListener('click', () => addNode(button.dataset.workflowAdd)));
    document.querySelectorAll('[data-workflow-panel]').forEach(button => button.addEventListener('click', () => {
      editorState.panel = button.dataset.workflowPanel;
      renderInspector();
    }));
    $('workflowApplyNode').addEventListener('click', applyInspector);
    $('workflowDeleteNode').addEventListener('click', deleteSelectedNode);
    $('workflowAutoLayoutBtn').addEventListener('click', autoLayout);
    $('workflowValidateBtn').addEventListener('click', () => validateCurrent().catch(report));
    $('workflowVersionBtn').addEventListener('click', event => { event.stopPropagation(); toggleVersionPopover($('workflowVersionPopover').hidden); });
    $('workflowVersionClose').addEventListener('click', () => toggleVersionPopover(false));
    $('workflowVersionPopover').addEventListener('click', event => event.stopPropagation());
    document.addEventListener('click', () => toggleVersionPopover(false));
    window.addEventListener('resize', drawEdges);
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
