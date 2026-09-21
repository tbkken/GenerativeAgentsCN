const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const crowdSource = fs.readFileSync('src/generative_agents/adapters/web/static/resources/crowd-workspace.js', 'utf8');
const consoleSource = fs.readFileSync('src/generative_agents/adapters/web/static/shell/console-api.js', 'utf8');
const cut = (start, end) => consoleSource.slice(consoleSource.indexOf(start), consoleSource.indexOf(end, consoleSource.indexOf(start)));
const wizardSource = cut('  function renderWizardStep()', '  async function prepareExperimentBrainChoices()')
  + cut("  $('createExperimentBtn').addEventListener(", "  $('closeModal').addEventListener(")
  + cut("  $('wizardNext').addEventListener(", "  $('confirmPublish').addEventListener(");

function setup() {
  const nodes = {}, notices = [], errors = [], events = [], listeners = {};
  const element = () => ({value: '', textContent: '', handlers: {}, selectedOptions: [],
    addEventListener(type, callback) { this.handlers[type] = callback; }, focus() {},
  });
  const node = id => nodes[id] ||= element();
  const root = node('newExperimentCrowds');
  let markup = '', inputs = [];
  Object.defineProperty(root, 'innerHTML', {get: () => markup, set(value) {
    markup = value;
    inputs = [...value.matchAll(/<input type="checkbox" value="([^"]+)"([^>]*)>/g)].map(([, id, attributes]) => ({
      ...element(), value: id, checked: /\bchecked\b/.test(attributes),
    }));
  }});
  root.querySelectorAll = () => inputs;
  root.querySelector = () => inputs[0];
  const window = {
    dispatchEvent(event) { events.push(event); listeners[event.type]?.(event); },
    ModelWorkspace: {loadChoices: async () => {}}, MapWorkspace: {prepareExperimentCreate: async () => {}},
  };
  const context = {window, document: {getElementById: node, querySelectorAll: () => []},
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    state: {wizardStep: 1}, $: node, prepareExperimentBrainChoices: async () => {},
    reportError: error => errors.push(error), showToast: message => notices.push(message),
    openModal() {}, closeModal() {}, createExperiment: async () => {},
  };
  vm.createContext(context);
  vm.runInContext(crowdSource, context);
  const manager = window.CrowdWorkspace;
  manager.initialized = true;
  let catalog = [{id: 'unrelated-crowd', name: '其他案例人群', agent_ids: []},
    {id: 'traffic-crowd', name: '交通实验人群', agent_ids: ['student']}];
  manager.request = async path => path === '/crowds' ? {items: catalog} : {items: [{id: 'student', name: '学生'}]};
  vm.runInContext(wizardSource, context);
  listeners['crowd-workspace:create-selection'] = context.renderWizardStep;
  const click = id => node(id).handlers.click({stopImmediatePropagation() {}});
  const select = (id, checked) => {
    const input = inputs.find(item => item.value === id);
    input.checked = checked;
    input.handlers.change();
  };
  const open = async () => {
    await click('createExperimentBtn');
    node('newExperimentName').value = '交通治理实验';
    for (const id of ['newExperimentBrain', 'newExperimentMap', 'newExperimentChatModel', 'newExperimentEmbeddingModel']) {
      node(id).value = id;
      node(id).selectedOptions = [{textContent: id}];
    }
    await click('wizardNext');
  };
  return {context, manager, node, click, open, select, notices, errors, events,
    inputs: () => inputs, setCatalog: items => {catalog = items;}};
}

test('new experiment requires a deliberate crowd choice even when public crowds exist', async () => {
  const {context, manager, open, click, inputs, notices, node, errors} = setup();
  await open();
  assert.equal(context.state.wizardStep, 2);
  assert.equal(inputs().length, 2);
  assert.ok(inputs().every(input => !input.checked));
  assert.equal(manager.selectedCreateRevisionIds().length, 0);
  assert.equal(node('createSummaryCrowds').textContent, '未选择');
  await click('wizardNext');
  assert.equal(context.state.wizardStep, 2);
  assert.equal(notices.length, 1);
  assert.match(notices[0], /至少选择一个/);
  assert.equal(errors.length, 0);
});

test('wizard navigation keeps an explicit choice and opening a new wizard clears it', async () => {
  const {context, manager, open, click, select, node, inputs} = setup();
  await open();
  select('traffic-crowd', true);
  await click('wizardNext');
  assert.equal(context.state.wizardStep, 3);
  assert.equal(node('createSummaryCrowds').textContent, '交通实验人群');
  await click('wizardBack');
  await click('wizardBack');
  await click('wizardNext');
  assert.equal(context.state.wizardStep, 2);
  assert.deepEqual([...manager.selectedCreateRevisionIds()], ['traffic-crowd']);
  assert.ok(inputs().find(input => input.value === 'traffic-crowd').checked);
  await click('closeCreateModal');
  await open();
  assert.equal(manager.selectedCreateRevisionIds().length, 0);
  assert.ok(inputs().every(input => !input.checked));
  assert.equal(node('createSummaryCrowds').textContent, '未选择');
});

test('refresh preserves deliberate choices and never substitutes a crowd after clearing or removal', async () => {
  const {manager, open, select, inputs, setCatalog} = setup();
  await open();
  select('traffic-crowd', true);
  await manager.prepareExperimentCreate();
  assert.deepEqual([...manager.selectedCreateRevisionIds()], ['traffic-crowd']);
  select('traffic-crowd', false);
  await manager.prepareExperimentCreate();
  assert.equal(manager.selectedCreateRevisionIds().length, 0);
  assert.ok(inputs().every(input => !input.checked));
  select('traffic-crowd', true);
  setCatalog([{id: 'unrelated-crowd', name: '其他案例人群', agent_ids: []}]);
  await manager.prepareExperimentCreate();
  assert.equal(manager.selectedCreateRevisionIds().length, 0);
  assert.ok(inputs().every(input => !input.checked));
});
