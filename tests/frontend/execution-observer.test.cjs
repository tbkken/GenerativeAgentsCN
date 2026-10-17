const { test } = require('node:test');
const assert = require('node:assert/strict');
const {
  create, createRequestGate, forest, progressView, stageState,
} = require('../../src/generative_agents/adapters/web/static/shell/execution-observer.js');

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const tick = () => new Promise(resolve => setImmediate(resolve));

test('the three phases never promote a model return or an interface result to a world commit', () => {
  assert.equal(stageState('model', 'SUCCEEDED').label, '已返回');
  assert.equal(stageState('interface', 'SUCCEEDED').label, '执行成功');
  assert.equal(stageState('commit', 'PENDING').label, '未提交');
  assert.equal(stageState('commit', 'DISCARDED').label, '本步已丢弃');
  assert.equal(stageState('commit', 'NOT_APPLICABLE').label, '不涉及世界变化');
  assert.equal(stageState('commit', 'SUCCEEDED').label, '证据不足');
  assert.equal(stageState('interface', 'REQUESTED').label, '已请求，待确认');
});

test('progress separates zero committed steps from observed participants without inventing completions', () => {
  const view = progressView({ current_step: 1, observed_agents: 4, agent_total: 25,
    current_actor: { key: 'yuri', name: '山本百合子' }, current_skill: 'schedule-daily', elapsed_ms: 62000 },
  { committed_step: 0, requested_steps: 120, status: 'RUNNING' });
  assert.equal(view.committed, '0 / 120 步');
  assert.equal(view.stepText, '第 1 步执行中');
  assert.match(view.completed, /已观察 4 \/ 25 人；完成数待确认/);
  assert.equal(view.actorText, '最近处理人物 · 山本百合子');
  assert.equal(view.activity, 'SKILL · schedule-daily');
  assert.equal(view.elapsed, '1 分 2 秒');
});

test('stopped runs show the last execution record rather than claiming the step is running', () => {
  const view = progressView({ current_step: 3, completed_agents: 2, agent_total: 25, phase: 'RECENT_ACTIVITY' }, { status: 'CANCELLED' });
  assert.equal(view.stepText, '第 3 步的最后执行记录');
  assert.equal(view.terminal, true);
});

test('object execution does not claim it is person 26 of 25', () => {
  const view = progressView({ current_step: 1, completed_agents: 25, agent_total: 25,
    current_actor: { key: 'cafe', name: '咖啡机', kind: 'object', ordinal: 26 }, current_interface: 'world-act' }, run);
  assert.equal(view.actorText, '对象 · 咖啡机');
  assert.equal(view.completed, '已确认完成 25 / 25 人');
});

test('cancellation has an explicit phase label while a world effect remains discarded independently', () => {
  assert.equal(stageState('model', 'CANCELLED').label, '已取消');
  assert.equal(stageState('interface', 'CANCELLED').tone, 'warning');
  assert.equal(stageState('commit', 'DISCARDED').label, '本步已丢弃');
});

test('finalization and queued states do not portray the previous Skill as current step execution', () => {
  const finalizing = progressView({ current_step: 120, committed_step: 120, total_steps: 120,
    current_skill: 'previous-skill', phase: 'RECENT_ACTIVITY', run_status: 'FINALIZING' }, run);
  assert.equal(finalizing.stepText, '本步已提交');
  assert.equal(finalizing.activity, '正在生成报告并固化结果');
  assert.equal(finalizing.elapsedLabel, '最后调用耗时');
  const queued = progressView({ current_step: 1, run_status: 'QUEUED' }, run);
  assert.equal(queued.stepText, '排队中');
  assert.equal(queued.activity, '排队中');
});

test('polls coalesce; forced refresh rejects late responses and keeps the newer request active', async () => {
  const gate = createRequestGate();
  const old = deferred(), fresh = deferred(), accepted = [];
  const first = gate.request('run/attempt', () => old.promise, value => accepted.push(value), assert.fail);
  const coalesced = gate.request('run/attempt', () => assert.fail('duplicate poll'), assert.fail, assert.fail);
  assert.equal(first, coalesced);
  await tick();
  const second = gate.request('run/attempt', () => fresh.promise, value => accepted.push(value), assert.fail, { force: true });
  old.resolve('stale'); await first;
  assert.equal(gate.request('run/attempt', () => assert.fail('old cleanup cleared new request'), assert.fail, assert.fail), second);
  fresh.resolve('latest'); await second;
  assert.deepEqual(accepted, ['latest']);
});

test('Run, Attempt and filter scopes reject stale work even when a transport ignores abort', async () => {
  const gate = createRequestGate();
  const a = deferred(), b = deferred(), accepted = [];
  const first = gate.request('run-a/attempt-a/step-1/actor-a', () => a.promise, value => accepted.push(value), assert.fail);
  await tick();
  const second = gate.request('run-b/attempt-b/step-2/actor-b', () => b.promise, value => accepted.push(value), assert.fail);
  a.resolve('old scope'); b.resolve('new scope');
  await Promise.all([first, second]);
  assert.deepEqual(accepted, ['new scope']);
  const c = deferred(); const pending = gate.request('run-b', () => c.promise, assert.fail, assert.fail);
  await tick(); gate.reset(); c.resolve('after reset'); await pending;
});

test('a refresh failure reports the error without clearing the last successful evidence', async () => {
  const gate = createRequestGate(); let snapshot = null; let error = null;
  await gate.request('run', () => ({ rows: [1, 2] }), value => { snapshot = value; }, assert.fail);
  await gate.request('run', () => Promise.reject(new Error('offline')), value => { snapshot = value; }, value => { error = value; });
  assert.deepEqual(snapshot, { rows: [1, 2] });
  assert.equal(error.message, 'offline');
});

test('repeated skill keys retain separate invocation nodes and malformed parent links cannot cycle the DOM', () => {
  const nodes = forest([{ id: 'root', kind: 'skill' }, { id: 'one', parent_id: 'root', skill_key: 'search' },
    { id: 'two', parent_id: 'root', skill_key: 'search' }, { id: 'orphan', parent_id: 'missing' },
    { id: 'cycle-a', parent_id: 'cycle-b' }, { id: 'cycle-b', parent_id: 'cycle-a' }]);
  assert.equal(nodes.filter(node => node.skill_key === 'search').length, 2);
  assert.equal(nodes.find(node => node.id === 'orphan').parent_id, null);
  assert.equal(nodes.find(node => node.id === 'cycle-a').parent_id, null);
  assert.equal(nodes.find(node => node.id === 'cycle-b').parent_id, null);
});

class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.dataset = {}; this.listeners = {}; this.textContent = ''; this.value = ''; this.hidden = false; }
  append(...nodes) { for (const node of nodes) { node.remove(); node.parentElement = this; this.children.push(node); } }
  replaceChildren(...nodes) { for (const node of [...this.children]) node.remove(); this.append(...nodes); }
  remove() { if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1); this.parentElement = null; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  get firstElementChild() { return this.children[0] || null; }
}
function harness() {
  const ids = new Map();
  const node = id => { if (!ids.has(id)) ids.set(id, new Element()); return ids.get(id); };
  const pending = [], traces = [];
  const document = { getElementById: node, createElement: tag => new Element(tag) };
  const observer = create({ document, api: (url, options) => { const d = deferred(); pending.push({ ...d, url, options }); return d.promise; }, onTrace: id => traces.push(id) });
  return { observer, node, pending, traces };
}
const run = { run_id: 'run', active_attempt_id: 'attempt', requested_steps: 120, committed_step: 0, status: 'RUNNING' };
const tree = (nodes, overrides = {}) => ({ run_id: 'run', attempt_id: 'attempt', selected_step: 1, selected_actor_key: 'yuri',
  agents: [{ key: 'yuri', name: '山本百合子' }], tree: { nodes, complete: true }, ...overrides });
const skill = { id: 'root', parent_id: null, kind: 'skill', name: '日程规划', skill_key: 'schedule', status: 'RUNNING', model_status: 'RUNNING', interface_status: 'UNKNOWN', commit_status: 'PENDING' };
async function resolveTree(h, value, options) {
  const refresh = h.observer.refreshTree(run, 'attempt', options); await tick(); h.pending.shift().resolve(value); await refresh;
}

test('tree refresh updates keyed nodes in place, retaining user expansion and preview selection', async () => {
  const h = harness();
  await resolveTree(h, tree([skill, { ...skill, id: 'child', parent_id: 'root', name: '检索' }]));
  const root = h.node('executionTreeNodes').children[0];
  const children = root.children[2];
  const child = children.children[0];
  root.open = false; child.open = true;
  const preview = child.children[1].children[2].children[0]; preview.open = true;
  await resolveTree(h, tree([{ ...skill, status: 'SUCCEEDED', model_status: 'SUCCEEDED' }, { ...skill, id: 'child', parent_id: 'root', name: '检索', input_preview: '<script>source text</script>' }]));
  assert.equal(h.node('executionTreeNodes').children[0], root);
  assert.equal(children.children[0], child);
  assert.equal(root.open, false); assert.equal(child.open, true); assert.equal(preview.open, true);
  assert.equal(preview.children[1].textContent, '<script>source text</script>');
  assert.equal(root.children[1].children[0].children[0].children[1].textContent, '已返回');
  assert.equal(root.children[1].children[0].children[2].children[1].textContent, '未提交');
});

test('new active actor replaces the displayed tree, while a stale historical response is ignored', async () => {
  const h = harness(); await resolveTree(h, tree([skill]));
  const oldRoot = h.node('executionTreeNodes').children[0];
  await resolveTree(h, tree([{ ...skill, id: 'next' }], { selected_actor_key: 'other' }));
  assert.notEqual(h.node('executionTreeNodes').children[0], oldRoot);
  const refresh = h.observer.refreshTree(run, 'attempt', { force: true }); await tick();
  h.pending.shift().resolve(tree([{ ...skill, id: 'bad' }], { attempt_id: 'old-attempt' })); await refresh;
  assert.equal(h.node('executionTreeNodes').children[0].dataset.nodeId, 'next');
});

test('switching Runs with the same 25 people rebuilds the selector and keeps every person selectable', async () => {
  const h = harness();
  const agents = Array.from({ length: 25 }, (_, index) => ({ key: `person-${index + 1}`, name: `人物${index + 1}` }));
  await resolveTree(h, tree([skill], { agents, selected_actor_key: 'person-1' }));
  const select = h.node('executionTreeAgent');
  assert.equal(select.children.length, 26);
  select.value = 'person-25';
  h.observer.reset();
  assert.equal(select.children.length, 1);
  const nextRun = { ...run, run_id: 'another-run', active_attempt_id: 'another-attempt' };
  const refresh = h.observer.refreshTree(nextRun, 'another-attempt');
  await tick();
  h.pending.shift().resolve(tree([{ ...skill, id: 'another-root' }], {
    run_id: nextRun.run_id, attempt_id: nextRun.active_attempt_id, agents, selected_actor_key: 'person-1',
  }));
  await refresh;
  assert.equal(select.children.length, 26);
  assert.deepEqual(select.children.slice(1).map(option => option.value), agents.map(agent => agent.key));
  assert.equal(select.value, '');
  select.value = 'person-25'; select.listeners.change();
  await tick();
  const selected = h.pending.shift();
  assert.match(selected.url, /agent_key=person-25/);
  selected.resolve(tree([{ ...skill, id: 'person-25-root' }], {
    run_id: nextRun.run_id, attempt_id: nextRun.active_attempt_id, agents, selected_actor_key: 'person-25',
  }));
  await tick();
  assert.match(h.node('executionTreeScope').textContent, /人物25/);
  assert.equal(select.children.length, 26);
});

test('raw payload loading is user initiated and tree refresh failures retain the expanded evidence', async () => {
  const h = harness(); await resolveTree(h, tree([{ ...skill, trace_id: 'attempt:3' }]));
  assert.equal(h.pending.length, 0); assert.deepEqual(h.traces, []);
  const root = h.node('executionTreeNodes').children[0];
  const button = root.children[1].children[3];
  button.listeners.click(); assert.deepEqual(h.traces, ['attempt:3']);
  const refresh = h.observer.refreshTree(run, 'attempt'); await tick();
  h.pending.shift().reject(new Error('temporary')); await refresh;
  assert.equal(h.node('executionTreeNodes').children[0], root);
  assert.match(h.node('executionTreeNote').textContent, /保留上次结果/);
});

test('progress remains on the active Attempt while tree filters request a historical Attempt', async () => {
  const h = harness();
  const progress = h.observer.refreshProgress(run); await tick();
  const request = h.pending.shift(); assert.match(request.url, /section=progress$/); assert.doesNotMatch(request.url, /attempt_id/);
  request.resolve({ run_id: 'run', progress: { current_step: 2, agent_total: 25, observed_agents: 3 } }); await progress;
  const historical = h.observer.refreshTree(run, 'old-attempt'); await tick();
  const old = h.pending.shift(); assert.match(old.url, /attempt_id=old-attempt/);
  old.resolve(tree([], { attempt_id: 'old-attempt', selected_step: 1 })); await historical;
  assert.equal(h.node('executionActorCount').textContent, '已观察 3 / 25 人；完成数待确认');
});

test('a delayed progress snapshot uses its terminal Run status instead of the older RUNNING caller', async () => {
  const h = harness();
  const refresh = h.observer.refreshProgress(run); await tick();
  const request = h.pending.shift();
  const coalesced = h.observer.refreshProgress({ ...run, status: 'CANCELLED' });
  assert.equal(refresh, coalesced);
  request.resolve({ run_id: 'run', progress: { current_step: 1, phase: 'RECENT_ACTIVITY', run_status: 'CANCELLED' } });
  await refresh;
  assert.equal(h.node('executionCommitted').textContent, '0 / 120 步');
  assert.equal(h.node('executionProgress').dataset.state, 'terminal');
  const freshView = progressView({ current_step: 2, run_status: 'RUNNING', phase: 'MODEL_RUNNING' }, { ...run, status: 'PAUSED' });
  assert.equal(freshView.stepText, '第 2 步执行中');
});
