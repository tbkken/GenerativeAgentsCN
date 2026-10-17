/* Read-only execution evidence. A returned model message is never a world commit. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ExecutionObserver = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const stageLabels = {
    model: { RUNNING: '等待返回', SUCCEEDED: '已返回', FAILED: '失败', ABORTED: '已中止', CANCELLED: '已取消', UNKNOWN: '证据不足', NOT_APPLICABLE: '不适用' },
    interface: { REQUESTED: '已请求，待确认', RUNNING: '执行中', SUCCEEDED: '执行成功', FAILED: '执行失败', ABORTED: '已中止', CANCELLED: '已取消', UNKNOWN: '证据不足', NOT_APPLICABLE: '不适用' },
    commit: { COMMITTED: '已提交', PENDING: '未提交', DISCARDED: '本步已丢弃', UNKNOWN: '证据不足', NOT_APPLICABLE: '不涉及世界变化' },
  };
  const stageTitles = { model: '模型返回', interface: '接口执行', commit: '世界提交' };
  const statusLabels = { RUNNING: '执行中', SUCCEEDED: '成功', FAILED: '失败', ABORTED: '已中止', CANCELLED: '已取消', REQUESTED: '已请求', RETURNED: '已返回', UNKNOWN: '证据不足', COMPLETED: '已完成' };
  const phaseLabels = { CREATED: '等待启动', QUEUED: '排队中', STARTING: '正在初始化', RUNNING: '执行中', MODEL: '等待模型返回', MODEL_RUNNING: '等待模型返回', MODEL_REQUEST: '等待模型返回', SKILL: '执行 SKILL', SKILL_RUNNING: '执行 SKILL', INTERFACE: '执行接口', INTERFACE_RUNNING: '执行接口', ACTOR_RUNNING: '人物本轮执行中', STEP_RUNNING: '本步执行中', RECENT_ACTIVITY: '最近执行记录', COMMITTING: '提交本步', FINALIZING: '正在生成报告', PAUSED: '已暂停', CANCELLED: '已取消', FAILED: '已失败', COMPLETED: '已完成', UNKNOWN: '等待执行证据' };

  function stageState(stage, status) {
    const value = String(status || 'UNKNOWN').toUpperCase();
    const label = stageLabels[stage]?.[value] || '证据不足';
    const tone = ['SUCCEEDED', 'COMMITTED'].includes(value) ? 'success'
      : ['FAILED', 'ABORTED', 'CANCELLED', 'DISCARDED'].includes(value) ? 'warning'
        : ['RUNNING', 'REQUESTED', 'PENDING'].includes(value) ? 'pending' : 'muted';
    return { title: stageTitles[stage], label, tone, value };
  }

  function duration(ms) {
    if (ms == null || !Number.isFinite(Number(ms))) return '耗时未记录';
    const seconds = Math.max(0, Math.floor(Number(ms) / 1000));
    if (seconds < 60) return `${seconds} 秒`;
    const minutes = Math.floor(seconds / 60);
    return minutes < 60 ? `${minutes} 分 ${seconds % 60} 秒` : `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
  }

  function progressView(progress = {}, run = {}) {
    const committed = Number(progress.committed_step ?? run.committed_step ?? run.completed_steps ?? 0);
    const total = Number(progress.total_steps ?? run.requested_steps ?? 0);
    const actor = progress.current_actor;
    const current = progress.current_step;
    const statuses = progress.run_status ? [progress.run_status] : [progress.phase, run.status];
    const terminal = statuses.some(status => ['COMPLETED', 'FAILED', 'CANCELLED', 'PAUSED'].includes(status));
    const status = progress.run_status || run.status || progress.phase;
    const finalizing = status === 'FINALIZING';
    const waiting = ['CREATED', 'QUEUED', 'STARTING'].includes(status);
    const stepText = finalizing ? '本步已提交' : waiting ? phaseLabels[status]
      : current == null ? (terminal ? '本次执行已停止' : '等待本步执行证据') : `第 ${current} 步${terminal ? '的最后执行记录' : '执行中'}`;
    const observed = progress.observed_agents == null ? '' : `已观察 ${progress.observed_agents} / ${progress.agent_total ?? '—'} 人`;
    const completed = progress.completed_agents == null ? `${observed}${observed ? '；' : ''}完成数待确认` : `已确认完成 ${progress.completed_agents} / ${progress.agent_total ?? '—'} 人${observed ? `；${observed}` : ''}`;
    const actorText = !actor ? '暂无当前参与者证据' : actor.kind === 'object' ? `对象 · ${actor.name || actor.key}`
      : `${actor.ordinal == null ? '最近处理人物 · ' : `第 ${actor.ordinal} / ${progress.agent_total ?? '—'} 人 · `}${actor.name || actor.key}`;
    const skill = progress.current_skill_name && progress.current_skill_name !== progress.current_skill ? `${progress.current_skill_name}（${progress.current_skill}）` : progress.current_skill;
    const activity = finalizing ? '正在生成报告并固化结果' : waiting ? phaseLabels[status]
      : progress.current_interface ? `接口 · ${progress.current_interface}` : skill ? `SKILL · ${skill}` : (phaseLabels[progress.phase] || '等待执行证据');
    const elapsedLabel = terminal || finalizing ? '最后调用耗时' : ({ MODEL_RUNNING: '模型等待', INTERFACE_RUNNING: '接口耗时', SKILL_RUNNING: 'SKILL 耗时', ACTOR_RUNNING: '人物本轮耗时', STEP_RUNNING: '本步耗时', RECENT_ACTIVITY: '最近调用耗时' })[progress.phase] || '当前调用耗时';
    return { committed: `${committed} / ${total || '—'} 步`, stepText, completed, actorText, activity,
      elapsed: duration(progress.elapsed_ms), elapsedLabel, diagnostic: progress.diagnostic || '', terminal };
  }

  // Scope + identity checks protect both late replies and the promise cleanup of
  // an older refresh. The last successful snapshot survives a transient failure.
  function createRequestGate() {
    let active = null;
    let scope = null;
    let generation = 0;
    return {
      reset() { generation += 1; scope = null; active?.controller.abort(); active = null; },
      request(nextScope, load, accept, reject, { force = false } = {}) {
        if (!force && active?.scope === nextScope) return active.promise;
        if (scope !== nextScope || force) { generation += 1; active?.controller.abort(); }
        scope = nextScope;
        const request = { scope, generation, controller: new AbortController() };
        active = request;
        const current = () => active === request && generation === request.generation && scope === request.scope && !request.controller.signal.aborted;
        request.promise = Promise.resolve().then(() => load(request.controller.signal)).then(value => {
          if (current()) accept(value);
        }).catch(error => {
          if (current() && error?.name !== 'AbortError') reject(error);
        }).finally(() => { if (active === request) active = null; });
        return request.promise;
      },
    };
  }

  function forest(nodes) {
    const unique = new Map((nodes || []).filter(item => item?.id).map(item => [String(item.id), item]));
    return [...unique.values()].map(item => {
      let parent = item.parent_id == null ? null : String(item.parent_id);
      const seen = new Set([String(item.id)]);
      let ancestor = parent;
      while (ancestor && unique.has(ancestor)) {
        if (seen.has(ancestor)) { parent = null; break; }
        seen.add(ancestor);
        ancestor = unique.get(ancestor).parent_id;
      }
      return { ...item, id: String(item.id), parent_id: unique.has(parent) ? parent : null };
    });
  }

  function create({ document, api, onTrace }) {
    const $ = id => document.getElementById(id);
    const progressGate = createRequestGate();
    const treeGate = createRequestGate();
    const elements = new Map();
    let run = null;
    let attemptId = null;
    let selectedStep = '';
    let selectedActor = '';
    let treeScope = null;
    let renderedScope = null;
    let latestProgress = null;
    let latestTree = null;
    const setText = (node, value) => { const text = String(value ?? ''); if (node && node.textContent !== text) node.textContent = text; };
    const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = text; return node; };

    function clearTree(message) {
      elements.clear();
      renderedScope = null;
      $('executionTreeNodes').replaceChildren();
      setText($('executionTreeEmpty'), message);
      $('executionTreeEmpty').hidden = false;
    }

    function reset() {
      progressGate.reset(); treeGate.reset();
      run = null; attemptId = null; treeScope = null; selectedStep = ''; selectedActor = '';
      latestProgress = null; latestTree = null;
      $('executionProgress').hidden = true;
      $('executionTreeStep').value = '';
      $('executionTreeAgent').replaceChildren(el('option', '', '跟随当前参与者'));
      $('executionTreeAgent').firstElementChild.value = '';
      $('executionTreeAgent').value = '';
      delete $('executionTreeAgent').dataset.signature;
      $('executionTreeFollow').disabled = true;
      setText($('executionTreeScope'), '等待执行记录');
      setText($('executionTreeNote'), '调用关系来自执行记录，展开节点可查看三个阶段的独立证据。');
      clearTree('等待选择执行尝试');
    }

    function renderProgress(data, selectedRun) {
      latestProgress = data;
      const progress = data.progress || {};
      const view = progressView(progress, selectedRun);
      $('executionProgress').hidden = false;
      setText($('executionCommitted'), view.committed);
      setText($('executionActorCount'), view.completed);
      setText($('executionProgressError'), '');
      $('executionProgressError').hidden = true;
      $('executionProgress').dataset.state = view.terminal ? 'terminal' : 'active';
    }

    function refreshProgress(selectedRun) {
      if (!selectedRun?.run_id) return Promise.resolve();
      const runId = selectedRun.run_id;
      if (run?.run_id && run.run_id !== runId) reset();
      run = selectedRun;
      const scope = JSON.stringify([runId, selectedRun.active_attempt_id || '']);
      $('executionProgress').hidden = false;
      if (!latestProgress) renderProgress({}, selectedRun);
      return progressGate.request(scope,
        signal => api(`/runs/${encodeURIComponent(runId)}/execution?section=progress`, { signal }),
        data => { if (data.run_id === runId) renderProgress(data, run); },
        error => {
          setText($('executionProgressError'), `进度刷新失败，保留上次结果：${error.message}`);
          $('executionProgressError').hidden = false;
        });
    }

    function createNode(node) {
      const details = el('details', 'execution-node');
      details.dataset.nodeId = node.id;
      details.open = !node.parent_id;
      const summary = el('summary', 'execution-node-summary');
      const heading = el('span', 'execution-node-heading');
      const kind = el('small', 'execution-node-kind');
      const name = el('strong');
      const key = el('code');
      heading.append(kind, name, key);
      const status = el('span', 'execution-node-status');
      const elapsed = el('span', 'execution-node-elapsed');
      summary.append(heading, status, elapsed);
      const body = el('div', 'execution-node-body');
      const stages = el('div', 'execution-stages');
      const badges = {};
      for (const stage of ['model', 'interface', 'commit']) {
        const badge = el('div', 'execution-stage');
        const title = el('span', '', stageTitles[stage]);
        const value = el('strong');
        badge.append(title, value); stages.append(badge); badges[stage] = { badge, value };
      }
      const evidence = el('ul', 'execution-node-evidence');
      const previews = el('div', 'execution-node-previews');
      const previewNodes = {};
      for (const [field, title] of [['input_preview', '输入摘要'], ['output_preview', '返回摘要']]) {
        const panel = el('details');
        const pre = el('pre');
        panel.append(el('summary', '', title), pre); previews.append(panel);
        previewNodes[field] = { panel, pre };
      }
      const payload = el('button', 'btn btn-sm', '查看原始模型记录');
      payload.type = 'button';
      payload.addEventListener('click', () => { if (payload.dataset.traceId) onTrace(payload.dataset.traceId, payload); });
      body.append(stages, evidence, previews, payload);
      const children = el('div', 'execution-node-children');
      details.append(summary, body, children);
      const entry = { details, kind, name, key, status, elapsed, badges, evidence, previewNodes, payload, children };
      elements.set(node.id, entry);
      return entry;
    }

    function updateNode(entry, node) {
      entry.details.dataset.kind = node.kind;
      setText(entry.kind, ({ skill: 'SKILL', model: '模型请求', interface: '系统接口', script: '脚本' })[node.kind] || '执行记录');
      setText(entry.name, node.name || node.skill_key || node.id);
      setText(entry.key, node.skill_key && node.skill_key !== node.name ? node.skill_key : '');
      entry.key.hidden = !entry.key.textContent;
      setText(entry.status, statusLabels[node.status] || '证据不足');
      entry.status.dataset.state = node.status || 'UNKNOWN';
      setText(entry.elapsed, duration(node.elapsed_ms));
      for (const stage of ['model', 'interface', 'commit']) {
        const view = stageState(stage, node[`${stage}_status`]);
        entry.badges[stage].badge.dataset.state = view.tone;
        setText(entry.badges[stage].value, view.label);
      }
      const evidence = (node.evidence || []).map(item => String(item));
      const signature = JSON.stringify(evidence);
      if (entry.evidence.dataset.signature !== signature) {
        entry.evidence.replaceChildren(...evidence.map(text => el('li', '', text)));
        entry.evidence.dataset.signature = signature;
      }
      entry.evidence.hidden = !evidence.length;
      for (const field of ['input_preview', 'output_preview']) {
        const value = node[field];
        entry.previewNodes[field].panel.hidden = value == null || value === '';
        setText(entry.previewNodes[field].pre, typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value, null, 2));
      }
      entry.payload.hidden = !node.trace_id;
      entry.payload.dataset.traceId = node.trace_id || '';
    }

    function renderTree(data) {
      latestTree = data;
      const tree = data.tree || {};
      const actualScope = JSON.stringify([data.run_id, data.attempt_id, data.selected_step ?? data.step_no, data.selected_actor_key ?? data.agent_key]);
      if (renderedScope !== actualScope) { clearTree('正在读取调用树…'); renderedScope = actualScope; }
      const nodes = forest(tree.nodes);
      const ids = new Set(nodes.map(node => node.id));
      for (const [id, entry] of elements) if (!ids.has(id)) { entry.details.remove(); elements.delete(id); }
      for (const node of nodes) updateNode(elements.get(node.id) || createNode(node), node);
      for (const node of nodes) {
        const parent = node.parent_id ? elements.get(node.parent_id)?.children : $('executionTreeNodes');
        const child = elements.get(node.id).details;
        if (parent && child.parentElement !== parent) parent.append(child);
      }
      $('executionTreeEmpty').hidden = nodes.length > 0;
      setText($('executionTreeEmpty'), '当前选择尚无可关联的调用记录。');
      const step = data.selected_step ?? data.step_no;
      const actorKey = data.selected_actor_key ?? data.agent_key;
      const actor = (data.agents || []).find(item => item.key === actorKey);
      setText($('executionTreeScope'), `${step == null ? '等待本步记录' : `第 ${step} 步`} · ${actor?.name || actorKey || '等待人物记录'} · ${nodes.length} 个节点`);
      setText($('executionTreeNote'), (tree.diagnostics || []).join('；') || (tree.complete ? '父子关系和返回结果来自执行记录。' : '部分调用关系尚无充分证据；不会根据时间相邻推定调用成功。'));
      const previous = $('executionTreeAgent').value;
      const options = [{ key: '', name: '跟随当前参与者' }, ...(data.agents || [])];
      const optionSignature = JSON.stringify(options.map(item => [item.key, item.name]));
      if ($('executionTreeAgent').dataset.signature !== optionSignature) {
        $('executionTreeAgent').replaceChildren(...options.map(item => { const option = el('option', '', item.name || item.key); option.value = item.key; return option; }));
        $('executionTreeAgent').dataset.signature = optionSignature;
        $('executionTreeAgent').value = options.some(item => item.key === previous) ? previous : '';
      }
      $('executionTreeStep').placeholder = step == null ? '当前步' : `当前第 ${step} 步`;
      $('executionTreeStep').max = String(run?.requested_steps || Math.max(...(data.steps || [1]), 1));
      $('executionTreeFollow').disabled = !selectedStep && !selectedActor;
    }

    function refreshTree(selectedRun, selectedAttempt, { force = false } = {}) {
      if (!selectedRun?.run_id || !selectedAttempt) return Promise.resolve();
      const runId = selectedRun.run_id;
      if (run?.run_id && run.run_id !== runId) reset();
      run = selectedRun;
      if (attemptId !== selectedAttempt) {
        selectedStep = ''; selectedActor = ''; $('executionTreeStep').value = ''; $('executionTreeAgent').value = '';
      }
      attemptId = selectedAttempt;
      const scope = JSON.stringify([runId, selectedAttempt, selectedStep, selectedActor]);
      if (treeScope !== scope) { treeScope = scope; clearTree('正在读取所选调用树…'); }
      const query = new URLSearchParams({ section: 'tree', attempt_id: selectedAttempt });
      if (selectedStep) query.set('step_no', selectedStep);
      if (selectedActor) query.set('agent_key', selectedActor);
      return treeGate.request(scope, signal => api(`/runs/${encodeURIComponent(runId)}/execution?${query}`, { signal }),
        data => {
          if (data.run_id !== runId || data.attempt_id !== selectedAttempt) return;
          if (selectedStep && Number(data.selected_step ?? data.step_no) !== Number(selectedStep)) return;
          if (selectedActor && (data.selected_actor_key ?? data.agent_key) !== selectedActor) return;
          renderTree(data);
        }, error => setText($('executionTreeNote'), `调用树刷新失败，保留上次结果：${error.message}`), { force });
    }

    $('executionTreeStep').addEventListener('change', () => {
      const value = $('executionTreeStep').value.trim();
      if (value && (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > Number($('executionTreeStep').max))) {
        setText($('executionTreeNote'), '请输入实验步数范围内的正整数。'); return;
      }
      selectedStep = value;
      refreshTree(run, attemptId, { force: true });
    });
    $('executionTreeAgent').addEventListener('change', () => { selectedActor = $('executionTreeAgent').value; refreshTree(run, attemptId, { force: true }); });
    $('executionTreeFollow').addEventListener('click', () => {
      selectedStep = ''; selectedActor = ''; $('executionTreeStep').value = ''; $('executionTreeAgent').value = '';
      refreshTree(run, attemptId, { force: true });
    });
    $('executionTreeExpand').addEventListener('click', () => { for (const entry of elements.values()) entry.details.open = true; });
    $('executionTreeCollapse').addEventListener('click', () => { for (const entry of elements.values()) entry.details.open = false; });
    return { reset, refreshProgress, refreshTree };
  }

  return { create, createRequestGate, duration, forest, progressView, stageState };
});
