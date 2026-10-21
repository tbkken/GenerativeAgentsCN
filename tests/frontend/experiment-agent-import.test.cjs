const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/generative_agents/adapters/web/static/shell/console-api.js', 'utf8');
const cut = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const code = cut('  async function loadExperimentAgents(', '  async function ensureExperimentDefinition(')
  + cut('  async function acceptSavedDraft(', '  function fillDefinitionOverview(')
  + cut('  function parseCsvRows(', '  async function refreshRunHistoryList(');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function setup() {
  const nodes = {}, requests = [], rendered = [], closed = [], notices = [];
  const agents = Array.from({length: 12}, (_, index) => ({
    agent_key: `agent-${index}`, name: `角色${index}`, currently: '旧目标', enabled: true,
    coord: [0, 0], scratch: {age: 30}, spatial: {address: {initial_location: ['旧位置']}},
  }));
  let stored = {agents};
  const draft = {id: 'hall', definition_hash: 'before', definition: structuredClone(stored)};
  const state = {
    selectedExperimentId: 'hall', experimentOpenGeneration: 1, workspacePage: 'agents',
    draft, revision: draft, definition: draft.definition, agentDraftPageNumber: 2,
    agentDraftPage: {items: agents.slice(5, 10)}, agentDraftGeneration: 0,
  };
  const values = {agentImportStrategy: 'merge', agentSearch: '窗口', agentEnabledFilter: 'enabled',
    agentCompletenessFilter: 'all', agentLocationFilter: '大厅', agentModelFilter: ''};
  const node = id => nodes[id] ||= {value: values[id] || '', disabled: false};
  const context = {
    state, $: node, structuredClone, URLSearchParams, window: {ResourceScope: {experimentId: 'hall'}},
    escapeHtml: String, openModal() {}, closeModal: id => closed.push(id),
    showToast: (message, title) => notices.push({message, title}),
    ensureExperimentDefinition: async () => state.definition,
    fillDraft() {}, fillDefinitionOverview() {}, clearDirty() { state.dirty = false; },
    scheduleGlobalReconcile() {}, renderAgentDraft: items => rendered.push(structuredClone(items)),
    api: async (url, options = {}) => {
      requests.push({url, ...options});
      if (options.method === 'PUT') {
        stored = JSON.parse(options.body).definition;
        return {id: 'hall', definition_hash: 'saved', definition: structuredClone(stored)};
      }
      const page = Number(new URL('http://test' + url).searchParams.get('page'));
      return {items: structuredClone(stored.agents.slice((page - 1) * 5, page * 5)), page, page_size: 5, total: stored.agents.length};
    },
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  const stage = (items, name = 'agents.json') => context.stageAgentImport({
    name, text: async () => JSON.stringify(items),
  });
  return {context, state, nodes, requests, rendered, closed, notices, stage};
}

for (const format of ['json', 'csv']) {
  test(`${format} merge imports refresh the current page and filters before reporting completion`, async () => {
    const {context, state, nodes, requests, rendered, closed, notices} = setup();
    const text = format === 'csv'
      ? 'agent_key,currently,coord,spatial\nagent-5,新目标,"12,8","{""address"":{""initial_location"":[""大厅"",""窗口""]}}"'
      : JSON.stringify([{agent_key: 'agent-5', currently: '新目标', coord: [12, 8],
        spatial: {address: {initial_location: ['大厅', '窗口']}}}]);
    await context.stageAgentImport({name: `agents.${format}`, text: async () => text});
    await context.applyAgentImport();
    assert.equal(state.agentDraftPageNumber, 2);
    const query = new URL('http://test' + requests[1].url).searchParams;
    assert.equal(query.get('page'), '2');
    assert.equal(query.get('q'), '窗口');
    assert.equal(query.get('enabled'), 'enabled');
    assert.equal(query.get('location'), '大厅');
    const row = rendered.at(-1)[0];
    assert.equal(row.currently, '新目标');
    assert.deepEqual(row.coord, [12, 8]);
    assert.deepEqual(row.spatial.address.initial_location, ['大厅', '窗口']);
    assert.equal(row.scratch.age, 30);
    assert.equal(state.revision.definition_hash, 'saved');
    assert.equal(context.window.ResourceScope.digest, 'saved');
    assert.equal(state.pendingAgentImport, null);
    assert.equal(notices.at(-1).title, '导入完成');
    assert.deepEqual(closed, ['agentImportModal']);
    assert.equal(nodes.agentImportStrategy.value, 'merge');
  });
}

test('a list read started before import cannot restore stale rows after the saved page refresh', async () => {
  const {context, state, rendered, stage} = setup();
  const old = deferred(), api = context.api;
  let reads = 0;
  context.api = (url, options) => url.includes('/resources/agents') && ++reads === 1 ? old.promise : api(url, options);
  const previous = context.loadExperimentAgents();
  await stage([{agent_key: 'agent-5', currently: '已导入目标'}]);
  await context.applyAgentImport();
  old.resolve({items: [{agent_key: 'agent-5', currently: '过期目标'}]});
  await previous;
  assert.equal(state.agentDraftPage.items[0].currently, '已导入目标');
  assert.equal(rendered.at(-1)[0].currently, '已导入目标');
});

test('switching experiments during definition loading prevents an import into the new experiment', async () => {
  const {context, state, requests, stage} = setup();
  await stage([{agent_key: 'agent-5', currently: '旧实验目标'}]);
  const definition = deferred();
  context.ensureExperimentDefinition = () => definition.promise;
  const importing = context.applyAgentImport();
  state.selectedExperimentId = 'other';
  state.experimentOpenGeneration++;
  definition.resolve({agents: []});
  await importing;
  assert.equal(requests.length, 0);
});

for (const phase of ['save', 'list']) {
  test(`a late ${phase} response cannot update another experiment or close its import dialog`, async () => {
    const {context, state, rendered, closed, notices, stage} = setup();
    await stage([{agent_key: 'agent-5', currently: '原实验目标'}]);
    const response = deferred(), started = deferred(), api = context.api;
    context.api = async (url, options) => {
      if ((phase === 'save') === (options?.method === 'PUT')) {
        const result = await api(url, options);
        started.resolve();
        await response.promise;
        return result;
      }
      return api(url, options);
    };
    const importing = context.applyAgentImport();
    await started.promise;
    state.selectedExperimentId = 'other';
    state.experimentOpenGeneration++;
    state.draft = {id: 'other', definition: {agents: []}};
    state.definition = state.draft.definition;
    await stage([{agent_key: 'new-agent', name: '新实验角色'}]);
    const newImport = state.pendingAgentImport;
    response.resolve();
    await importing;
    assert.equal(state.draft.id, 'other');
    assert.equal(state.pendingAgentImport, newImport);
    assert.equal(rendered.length, 0);
    assert.equal(closed.length, 0);
    assert.equal(notices.length, 0);
  });
}

for (const phase of ['save', 'list']) {
  test(`a newer import preview remains open when the previous ${phase} response arrives`, async () => {
    const {context, state, nodes, closed, notices, stage} = setup();
    await stage([{agent_key: 'agent-5', currently: '第一次导入'}], 'first.json');
    const response = deferred(), started = deferred(), api = context.api;
    context.api = async (url, options) => {
      if ((phase === 'save') === (options?.method === 'PUT')) {
        const result = await api(url, options);
        started.resolve();
        await response.promise;
        return result;
      }
      return api(url, options);
    };
    const importing = context.applyAgentImport();
    await started.promise;
    await stage([{agent_key: 'agent-6', currently: '第二次导入'}], 'second.json');
    const newImport = state.pendingAgentImport;
    response.resolve();
    await importing;
    assert.equal(state.pendingAgentImport, newImport);
    assert.equal(state.agentDraftPage.items[0].currently, '第一次导入');
    assert.equal(nodes.confirmAgentImport.disabled, false);
    assert.equal(closed.length, 0);
    assert.equal(notices.length, 0);
  });
}

test('failed imports keep their preview retryable and repeated confirmation submits once', async () => {
  const {context, state, nodes, closed, stage} = setup();
  await stage([{agent_key: 'agent-5', currently: '重试目标'}]);
  const saved = deferred(), started = deferred(), api = context.api;
  let writes = 0;
  context.api = async (url, options) => {
    if (options?.method === 'PUT') { writes++; started.resolve(); return saved.promise; }
    return api(url, options);
  };
  const importing = context.applyAgentImport();
  await started.promise;
  await context.applyAgentImport();
  assert.equal(writes, 1);
  assert.equal(nodes.confirmAgentImport.disabled, true);
  saved.reject(new Error('保存失败'));
  await assert.rejects(importing, /保存失败/);
  assert.ok(state.pendingAgentImport);
  assert.equal(nodes.confirmAgentImport.disabled, false);
  assert.equal(closed.length, 0);
  context.api = api;
  await context.applyAgentImport();
  assert.equal(state.agentDraftPage.items[0].currently, '重试目标');
});

test('a slow file read cannot replace a newer import preview or follow an experiment switch', async () => {
  const {context, state, stage} = setup();
  const first = deferred();
  const reading = context.stageAgentImport({name: 'old.json', text: () => first.promise});
  await stage([{agent_key: 'agent-6'}], 'new.json');
  const newer = state.pendingAgentImport;
  first.resolve(JSON.stringify([{agent_key: 'agent-5'}]));
  await reading;
  assert.equal(state.pendingAgentImport, newer);

  const late = deferred();
  const switching = context.stageAgentImport({name: 'late.json', text: () => late.promise});
  state.selectedExperimentId = 'other';
  state.experimentOpenGeneration++;
  late.resolve(JSON.stringify([{agent_key: 'agent-7'}]));
  await switching;
  assert.equal(state.pendingAgentImport, newer);
});
