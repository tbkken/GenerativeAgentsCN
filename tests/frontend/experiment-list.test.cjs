const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/generative_agents/adapters/web/static/shell/console-api.js', 'utf8');
const cut = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const code = cut('  async function loadExperiments(', '  async function openExperiment(')
  + cut("  $('experimentArchiveFilter').addEventListener(", "  $('selectVisibleExperiments').addEventListener(")
  + cut("  $('experimentPagination').addEventListener(", "  $('saveBtn').addEventListener(")
  + cut('  async function bootstrapConsole(', "  window.addEventListener('popstate'");

function pageResult(page = 1, total = 9) {
  return {page, page_size: 5, total, total_pages: Math.ceil(total / 5),
    items: Array.from({length: Math.max(0, Math.min(5, total - (page - 1) * 5))}, (_, index) => ({id: `experiment-${(page - 1) * 5 + index + 1}`}))};
}

function setup(search = '') {
  const nodes = {}, requests = [], errors = [], opened = [], timers = new Map();
  let timerId = 0;
  const node = id => nodes[id] ||= {value: '', dataset: {}, handlers: {}, attributes: {},
    addEventListener(type, fn) { this.handlers[type] = fn; },
    setAttribute(key, value) { this.attributes[key] = value; },
    removeAttribute(key) { delete this.attributes[key]; },
    querySelector(selector) { return node(selector); },
  };
  const state = {page: 1, archiveFilter: 'active', experimentListGeneration: 0, workspacePage: 'experiments',
    selectedExperimentIds: new Set(), contentTabs: {}, resultTab: 'timeline', operationTab: 'overview'};
  const context = {state, $: node, window: {}, URLSearchParams, location: {search},
    AbortController, document: {visibilityState: 'visible'},
    setTimeout(fn, delay) {const id = ++timerId; timers.set(id, {fn, delay}); return id;},
    clearTimeout(id) {timers.delete(id);},
    api: async path => { requests.push(path); return pageResult(Number(new URL('http://test' + path).searchParams.get('page'))); },
    cardTemplate: item => item.id, escapeHtml: String, reportError: error => errors.push(error),
    setContentTab() {}, setResultTab() {}, setOperationTab() {}, syncWorkspaceUrl() {},
    openExperiment: async (...args) => opened.push(args), startGlobalActivityStream: async () => {},
  };
  vm.createContext(context); vm.runInContext(code, context);
  return {context, state, node, nodes, requests, errors, opened, timers};
}

test('experiment cards use fixed five-item pages and next-page navigation preserves archive scope', async () => {
  const {context, state, nodes, requests} = setup();
  await context.loadExperiments();
  assert.equal(requests[0], '/experiments?page=1&page_size=5&archived=active');
  assert.equal(nodes.experimentRange.textContent, '显示 1–5，共 9 条 · 每页 5 条');
  assert.equal(nodes.experimentPrev.disabled, true);
  assert.equal(nodes.experimentNext.disabled, false);
  nodes.experimentNext.closest = () => null;
  nodes.experimentPagination.handlers.click({target: nodes.experimentNext, stopImmediatePropagation() {}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.page, 2);
  assert.equal(requests[1], '/experiments?page=2&page_size=5&archived=active');
  assert.equal(nodes.experimentRange.textContent, '显示 6–9，共 9 条 · 每页 5 条');
  assert.equal(nodes.experimentNext.disabled, true);
});

test('a slow page response cannot replace the subsequently selected page', async () => {
  const {context, state, nodes} = setup();
  let resolveFirst;
  context.api = async () => new Promise(resolve => { resolveFirst = resolve; });
  const first = context.loadExperiments();
  state.page = 2;
  context.api = async () => pageResult(2);
  await context.loadExperiments();
  resolveFirst(pageResult(1));
  await first;
  assert.equal(nodes.experimentRange.textContent, '显示 6–9，共 9 条 · 每页 5 条');
  assert.equal(nodes.experimentList.innerHTML, 'experiment-6experiment-7experiment-8experiment-9');
});

test('unchanged background refresh preserves list and pagination DOM for keyboard focus and open menus', async () => {
  const {context, nodes} = setup();
  await context.loadExperiments();
  let writes = 0;
  for (const id of ['experimentList', 'experimentPages']) {
    let markup = nodes[id].innerHTML;
    Object.defineProperty(nodes[id], 'innerHTML', {get: () => markup, set: value => { writes++; markup = value; }});
  }
  await context.loadExperiments({silent: true});
  assert.equal(writes, 0);
  context.api = async () => pageResult(1, 4);
  await context.loadExperiments({silent: true});
  assert.equal(nodes.experimentList.innerHTML, 'experiment-1experiment-2experiment-3experiment-4');
  assert.ok(writes > 0);
});

test('deleting the last item on the last page returns to the remaining page', async () => {
  const {context, state, nodes, requests} = setup();
  state.page = 2;
  context.api = async path => { requests.push(path); return pageResult(Number(new URL('http://test' + path).searchParams.get('page')), 5); };
  await context.loadExperiments();
  assert.equal(state.page, 1);
  assert.equal(requests.length, 2);
  assert.equal(nodes.experimentRange.textContent, '显示 1–5，共 5 条 · 每页 5 条');
  assert.equal(nodes.experimentPagination.hidden, false);
});

test('switching to archived experiments resets page and selection and exposes restoration', async () => {
  const {state, nodes, requests} = setup();
  state.page = 3;
  state.selectedExperimentIds.add('previous-selection');
  nodes.experimentArchiveFilter.value = 'archived';
  nodes.experimentArchiveFilter.handlers.change();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.page, 1);
  assert.equal(state.selectedExperimentIds.size, 0);
  assert.equal(requests[0], '/experiments?page=1&page_size=5&archived=archived');
  assert.equal(nodes.archiveSelectedBtn.hidden, true);
  assert.equal(nodes.restoreSelectedBtn.hidden, false);
});

test('experiment deep link loads only its requested experiment without any saved-view dependency', async () => {
  const {context, state, requests, opened} = setup('?saved_view=retired&experiment_id=selected&view=overview');
  await context.bootstrapConsole();
  assert.deepEqual(requests, []);
  assert.deepEqual(opened, [['selected', 'overview', null]]);
  assert.equal(state.bootstrapped, true);
});

test('slow requests coalesce and only schedule another poll after completion, stopping on terminal status', async () => {
  const {context, state, nodes, timers} = setup();
  state.bootstrapped = true;
  let resolve, calls = 0;
  context.api = () => {calls++; return new Promise(done => {resolve = done;});};
  const first = context.loadExperiments();
  const repeated = context.loadExperiments({silent: true});
  assert.equal(calls, 1);
  assert.equal(timers.size, 0); // No 3-second timer can overtake a slow request.
  const running = pageResult(); running.items[0].latest_run = {status: 'RUNNING'};
  resolve(running);
  await Promise.all([first, repeated]);
  assert.equal(nodes.experimentRange.textContent, '显示 1–5，共 9 条 · 每页 5 条');
  assert.equal(timers.size, 1);
  const [id, timer] = [...timers][0]; timers.delete(id);
  assert.equal(timer.delay, 3000);
  timer.fn();
  const second = context.loadExperiments({silent: true});
  assert.equal(calls, 2);
  assert.equal(timers.size, 0);
  resolve(pageResult()); await second;
  assert.equal(timers.size, 0);
});

test('leaving or hiding the list cancels the request and rejects a late response without a toast', async () => {
  const {context, state, nodes, errors, timers} = setup();
  state.bootstrapped = true;
  let resolve, signal;
  context.api = (_path, options) => {signal = options.signal; return new Promise(done => {resolve = done;});};
  const pending = context.loadExperiments();
  context.document.visibilityState = 'hidden';
  context.stopExperimentListRefresh();
  assert.equal(signal.aborted, true);
  resolve(pageResult()); await pending;
  assert.equal(nodes.experimentRange, undefined);
  assert.equal(errors.length, 0);
  assert.equal(timers.size, 0);
  context.document.visibilityState = 'visible';
  state.workspacePage = 'maps'; state.experimentListNeedsPolling = true;
  context.scheduleExperimentListPoll();
  assert.equal(timers.size, 0);
});

test('a mutation during a pending read gets one fresh response after it and never paints the old response', async () => {
  const {context, nodes} = setup();
  let resolve, calls = 0;
  context.api = () => {calls++; return calls === 1 ? new Promise(done => {resolve = done;}) : Promise.resolve(pageResult(1, 4));};
  const pending = context.loadExperiments();
  const changed = context.loadExperiments({fresh: true});
  const changedAgain = context.loadExperiments({fresh: true});
  assert.equal(calls, 1);
  resolve(pageResult());
  await Promise.all([pending, changed, changedAgain]);
  assert.equal(calls, 2);
  assert.equal(nodes.experimentRange.textContent, '显示 1–4，共 4 条 · 每页 5 条');
});

test('initial author list load fetches once and completed lists do not start the global interval', async () => {
  const {context, requests, timers} = setup();
  vm.runInContext(cut('  async function startGlobalActivityStream(', '  function isRunRecoverable('), context);
  await context.bootstrapConsole();
  assert.equal(requests.length, 1);
  assert.equal(timers.size, 0);
});
