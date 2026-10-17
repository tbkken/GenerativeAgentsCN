const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/generative_agents/adapters/web/static/shell/console-api.js', 'utf8');
const cut = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const functions = cut('  function openModal(', '  function confirmResourceDeletion(')
  + cut('  function handleModalKeydown(', '  function validationItemMarkup(')
  + cut('  function openModelTrace(', '  function renderSystemEvents(');

function harness() {
  const nodes = new Map(), pending = [], errors = [];
  const document = { activeElement: null };
  class Element {
    constructor(id) {
      this.id = id; this.hidden = false; this.disabled = false;
      this.innerHTML = ''; this.textContent = ''; this.isConnected = true;
      this.attributes = {}; this.classes = new Set();
      this.classList = { add: name => this.classes.add(name), remove: name => this.classes.delete(name) };
    }
    setAttribute(name, value) { this.attributes[name] = value; }
    focus() { document.activeElement = this; }
  }
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, new Element(id));
    return nodes.get(id);
  };
  document.querySelector = () => [...nodes.values()].find(element => element.classes.has('open')) || null;
  const state = { selectedRunId: 'run', selectedAttemptId: 'attempt', selectedTraceAttemptId: 'attempt', operationFactsGeneration: 1 };
  const context = { state, $: node, document, HTMLElement: Element, AbortController,
    requestAnimationFrame: callback => callback(),
    setBackgroundInert: value => { document.inert = value; },
    modalFocusableElements: () => [node('closeModelTrace'), node('closeModelTraceDone')],
    escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
    api: (url, options) => new Promise((resolve, reject) => pending.push({ url, options, resolve, reject })),
    reportError: error => errors.push(error),
  };
  vm.createContext(context); vm.runInContext(functions, context);
  return { ...context, node, pending, errors };
}

const detail = (content = '', next_cursor = null, extra = {}) => ({
  trace: { purpose: 'skill_runtime' }, payload_available: true, content, next_cursor, ...extra,
});

test('opening a raw trace immediately shows the modal and preserves the initiating control for focus', async () => {
  const h = harness(), trigger = h.node('physical-row');
  const opening = h.openModelTrace('attempt:2', trigger);
  assert.equal(h.state.activeModalId, 'modelTraceModal');
  assert.equal(h.document.activeElement, h.node('closeModelTrace'));
  assert.equal(h.document.inert, true);
  assert.match(h.node('modelTraceStatus').textContent, /正在读取/);
  assert.equal(h.node('modelTraceBody').attributes['aria-busy'], 'true');
  h.pending.shift().resolve(detail('<script>raw text</script>'));
  await opening;
  assert.match(h.node('modelTraceDetail').innerHTML, /Payload（已脱敏）/);
  assert.match(h.node('modelTraceDetail').innerHTML, /&lt;script&gt;/);
  assert.equal(h.node('modelTraceBody').attributes['aria-busy'], 'false');
  h.closeModal('modelTraceModal');
  assert.equal(h.state.traceDetailState, null);
  assert.equal(h.document.activeElement, trigger);
  assert.equal(h.document.inert, false);
});

test('Escape cancels the pending request and an abort-ignoring response cannot change the closed modal', async () => {
  const h = harness();
  const opening = h.openModelTrace('attempt:2', h.node('tree-button'));
  const request = h.pending.shift();
  let prevented = false;
  h.handleModalKeydown({ key: 'Escape', preventDefault() { prevented = true; } }, h.node('modelTraceModal'));
  assert.equal(prevented, true);
  assert.equal(request.options.signal.aborted, true);
  request.resolve(detail('late payload'));
  await opening;
  assert.equal(h.state.activeModalId, null);
  assert.equal(h.node('modelTraceDetail').hidden, true);
  assert.equal(h.node('modelTraceDetail').innerHTML, '');
  assert.deepEqual(h.errors, []);
});

test('opening another trace rejects an older success and an older failure without touching the new record', async () => {
  const h = harness();
  const oldSuccess = h.openModelTrace('attempt:1', h.node('first'));
  const success = h.pending.shift();
  const oldFailure = h.openModelTrace('attempt:2', h.node('second'));
  const failure = h.pending.shift();
  const latest = h.openModelTrace('attempt:3', h.node('third'));
  const current = h.pending.shift();
  current.resolve(detail('current payload')); await latest;
  success.resolve(detail('stale payload')); failure.reject(new Error('stale error'));
  await Promise.all([oldSuccess, oldFailure]);
  assert.equal(success.options.signal.aborted, true);
  assert.equal(failure.options.signal.aborted, true);
  assert.match(h.node('modelTraceDetail').innerHTML, /current payload/);
  assert.doesNotMatch(h.node('modelTraceDetail').innerHTML, /stale payload/);
  assert.deepEqual(h.errors, []);
});

test('a switched Run, Attempt or returned workspace generation cannot accept pending detail', async () => {
  for (const change of [
    h => { h.state.selectedRunId = 'other-run'; },
    h => { h.state.selectedTraceAttemptId = 'other-attempt'; },
    h => { h.state.operationFactsGeneration += 1; },
  ]) {
    const h = harness();
    const opening = h.openModelTrace('attempt:2', h.node('trigger'));
    change(h);
    h.pending.shift().resolve(detail('wrong selection'));
    await opening;
    assert.equal(h.node('modelTraceDetail').innerHTML, '');
  }
});

test('payload chunks stay on demand and rapid repeated loading cannot duplicate a chunk', async () => {
  const h = harness();
  const opening = h.openModelTrace('attempt:2', h.node('trigger'));
  h.pending.shift().resolve(detail('first ', 6)); await opening;
  assert.equal(h.pending.length, 0);
  assert.equal(h.node('tracePayloadMore').hidden, false);
  const load = h.loadTraceDetail({ append: true });
  const request = h.pending.shift();
  assert.match(request.url, /cursor=6&limit_bytes=16384/);
  assert.equal(h.node('tracePayloadMore').disabled, true);
  await h.loadTraceDetail({ append: true });
  assert.equal(h.pending.length, 0);
  request.resolve(detail('second', null)); await load;
  assert.equal(h.state.traceDetailState.content, 'first second');
  assert.equal(h.node('tracePayloadMore').hidden, true);
  await h.loadTraceDetail({ append: true });
  assert.equal(h.pending.length, 0);
});

test('a failed payload read preserves its content and cursor so retry appends only once', async () => {
  const h = harness();
  const opening = h.openModelTrace('attempt:2', h.node('trigger'));
  h.pending.shift().resolve(detail('first ', 6)); await opening;
  const load = h.loadTraceDetail({ append: true });
  h.pending.shift().reject(new Error('temporary failure')); await load;
  assert.equal(h.state.traceDetailState.content, 'first ');
  assert.equal(h.state.traceDetailState.cursor, 6);
  assert.equal(h.node('tracePayloadMore').disabled, false);
  assert.equal(h.node('tracePayloadMore').textContent, '重试读取');
  assert.match(h.node('modelTraceStatus').textContent, /temporary failure/);
  const retry = h.loadTraceDetail({ append: true });
  const request = h.pending.shift();
  assert.match(request.url, /cursor=6/);
  request.resolve(detail('second')); await retry;
  assert.equal(h.state.traceDetailState.content, 'first second');
  assert.equal(h.node('modelTraceStatus').hidden, true);
});

test('a first-read failure is retryable from cursor zero inside the same modal', async () => {
  const h = harness();
  const opening = h.openModelTrace('attempt:2', h.node('trigger'));
  h.pending.shift().reject(new Error('unavailable')); await opening;
  assert.equal(h.node('tracePayloadMore').hidden, false);
  const retry = h.loadTraceDetail({ append: true });
  const request = h.pending.shift(); assert.match(request.url, /cursor=0/);
  request.resolve(detail('retried')); await retry;
  assert.equal(h.state.traceDetailState.content, 'retried');
});

test('records without payload retain the diagnostic and iteration association warning', async () => {
  const h = harness();
  const opening = h.openModelTrace('attempt:2', h.node('trigger'));
  h.pending.shift().resolve(detail('', null, {
    payload_available: false, payload_diagnostic: '该记录未保存 Payload。',
    iteration_interfaces: [{ name: 'world-perceive' }],
  }));
  await opening;
  assert.match(h.node('modelTraceDetail').innerHTML, /该记录未保存 Payload。/);
  assert.match(h.node('modelTraceDetail').innerHTML, /非单次模型请求关联/);
  assert.doesNotMatch(h.node('modelTraceDetail').innerHTML, /Payload（已脱敏）/);
  assert.equal(h.node('tracePayloadMore').hidden, true);
});
