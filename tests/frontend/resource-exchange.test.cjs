const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = 'src/generative_agents/adapters/web/static/resources/resource-exchange.js';
const exchange = require('../../' + path);

test('the six catalogs extract their own resource kind from a shared package', () => {
  for (const kind of ['map', 'agent', 'crowd', 'model']) {
    assert.equal(exchange.matches({kind, key:'one'}, kind), true);
    assert.equal(exchange.matches({kind:'skill', key:'one'}, kind), false);
  }
  assert.equal(exchange.matches({kind:'skill', skill_kind:'brain'}, 'brain'), true);
  assert.equal(exchange.matches({kind:'skill', skill_kind:'brain'}, 'skill'), false);
  assert.equal(exchange.matches({kind:'skill', skill_kind:'atomic'}, 'skill'), true);
  assert.equal(exchange.matches({kind:'skill', skill_kind:'pack'}, 'skill'), true);
});

test('selecting a map alone leaves skills and agents unselected', () => {
  const resources = [
    {kind:'map', key:'home', dependencies:[{kind:'skill', key:'door'}]},
    {kind:'skill', key:'door'}, {kind:'agent', key:'alice'},
  ];
  assert.deepEqual(exchange.selection(resources, ['map:home']), [{kind:'map', key:'home'}]);
  assert.deepEqual(exchange.selection(resources, ['map:home'], true), [{kind:'map', key:'home'}, {kind:'skill', key:'door'}]);
});

test('dependency selection stays inside the uploaded package and terminates cycles', () => {
  const resources = [
    {kind:'skill', key:'main', dependencies:[{kind:'skill', key:'child'}, {kind:'skill', key:'absent'}]},
    {kind:'skill', key:'child', dependencies:[{kind:'skill', key:'main'}]},
    {kind:'agent', key:'main'},
  ];
  assert.deepEqual(exchange.selection(resources, ['skill:main'], true), [{kind:'skill', key:'main'}, {kind:'skill', key:'child'}]);
});

function browser() {
  const elements = new Map(), requests = [], events = [];
  let choices = [];
  function choice(value, checked) {
    const reason = {textContent:''}, row = {dataset:{}, querySelector:() => reason};
    return {value, checked, dataset:{}, listeners:{}, closest:() => row,
      addEventListener(type, fn) {this.listeners[type] = fn;}, reason};
  }
  function element(id) {
    if (!elements.has(id)) elements.set(id, {id, hidden:false, open:false, value:'', files:[], textContent:'', innerHTML:'', listeners:{}, options:[],
      addEventListener(type, fn) {this.listeners[type] = fn;},
      setAttribute() {}, querySelectorAll() {return [];},
      replaceChildren(...items) {this.options = items; this.value = items[0]?.value || '';},
      add(option) {this.options.push(option);}, showModal() {this.open = true;},
      close() {this.open = false; this.listeners.close?.();},
    });
    const node = elements.get(id);
    if (id === 'resourceExchangePreview' && !node.tracked) {
      node.tracked = true;
      Object.defineProperty(node, 'innerHTML', {get() {return this.markup || '';}, set(value) {
        this.markup = value;
        choices = [...value.matchAll(/<input type="checkbox" data-exchange-choice value="([^"]+)"([^>]*)>/g)].map(match => choice(match[1], match[2].includes('checked')));
      }});
    }
    if (id === 'resourceExchangeDialog' || id === 'resourceExchangePreview') {
      node.querySelectorAll = selector => selector.includes('data-exchange-choice') ? choices.filter(item => !selector.includes(':checked') || item.checked) : [];
    }
    return elements.get(id);
  }
  const win = {ResourceScope:{experimentId:null}, document:{getElementById:element}, dispatchEvent:event => events.push(event),
    fetch(url, options) {return new Promise(resolve => requests.push({url, options, resolve}));},
  };
  const context = {window:win, globalThis:win, FormData, AbortController, URL, Map, Set,
    Option:function(label, value) {this.label = label; this.value = value;},
    CustomEvent:function(type, options) {this.type = type; this.detail = options.detail;},
  };
  vm.createContext(context); vm.runInContext(fs.readFileSync(path, 'utf8'), context);
  return {api:win.ResourceExchange, win, element, requests, events, choices:() => choices};
}
const reply = data => ({ok:true, json:async () => data});

test('late export catalogs cannot populate a new resource page', async () => {
  const {api, element, requests} = browser();
  api.setPage('maps');
  const first = element('exportResourcePackageBtn').listeners.click();
  api.setPage('brains');
  const second = element('exportResourcePackageBtn').listeners.click();
  requests[1].resolve(reply({items:[{id:'brain-id', name:'Brain', key:'brain'}]}));
  await second;
  requests[0].resolve(reply({items:[{id:'map-id', name:'Map', key:'map'}]}));
  await first;
  assert.match(element('resourceExchangeTitle').textContent, /大脑/);
  assert.deepEqual(element('resourceExchangeExportChoice').options.map(item => item.value), ['', 'brain-id']);
});

test('resource exchange controls remain unavailable inside an experiment', async () => {
  const {api, win, element, requests} = browser();
  win.ResourceScope.experimentId = 'experiment-a';
  for (const page of ['maps', 'public-agents', 'crowds', 'skills', 'brains', 'model-catalog']) {
    api.setPage(page);
    assert.equal(element('importResourcePackageBtn').hidden, true);
    assert.equal(element('exportResourcePackageBtn').hidden, true);
    await element('exportResourcePackageBtn').listeners.click();
  }
  assert.equal(requests.length, 0);
});

test('leaving a resource catalog cancels reads and invalidates their responses', async () => {
  const {api, element, requests} = browser();
  api.setPage('maps');
  const pending = element('exportResourcePackageBtn').listeners.click();
  api.setPage('experiments');
  assert.equal(requests[0].options.signal.aborted, true);
  assert.equal(element('resourceExchangeDialog').open, false);
  requests[0].resolve(reply({items:[{id:'late', name:'late'}]}));
  await pending;
  assert.ok(!element('resourceExchangeExportChoice').options.some(item => item.value === 'late'));
});

test('a newly selected upload rejects an older preview even after its response resolves', async () => {
  const {api, element, requests} = browser();
  api.setPage('maps');
  await element('importResourcePackageBtn').listeners.click();
  element('resourceExchangeFile').files = [new Blob(['first'])];
  const first = element('resourceExchangeFile').listeners.change();
  element('resourceExchangeFile').files = [new Blob(['second'])];
  const second = element('resourceExchangeFile').listeners.change();
  requests[1].resolve(reply({token:'second', source_kind:'config', resources:[{kind:'map', key:'second', name:'第二个地图', status:'new'}]}));
  await second;
  requests[0].resolve(reply({token:'first', source_kind:'config', resources:[{kind:'map', key:'first', name:'第一个地图', status:'new'}]}));
  await first;
  assert.match(element('resourceExchangePreview').innerHTML, /第二个地图/);
  assert.doesNotMatch(element('resourceExchangePreview').innerHTML, /第一个地图/);
  assert.equal(requests[2].options.method, 'DELETE');
  assert.match(requests[2].url, /\/preview\/first$/);
});

test('closing a loaded preview releases it and a late uploaded preview is also released', async () => {
  const {api, element, requests} = browser();
  api.setPage('maps');
  await element('importResourcePackageBtn').listeners.click();
  element('resourceExchangeFile').files = [new Blob(['first'])];
  const first = element('resourceExchangeFile').listeners.change();
  requests[0].resolve(reply({token:'first', source_kind:'config', resources:[]}));
  await first;
  element('resourceExchangeClose').listeners.click();
  assert.match(requests[1].url, /\/preview\/first$/);
  assert.equal(requests[1].options.method, 'DELETE');
  await element('importResourcePackageBtn').listeners.click();
  element('resourceExchangeFile').files = [new Blob(['late'])];
  const late = element('resourceExchangeFile').listeners.change();
  element('resourceExchangeClose').listeners.click();
  requests[2].resolve(reply({token:'late', source_kind:'config', resources:[]}));
  await late;
  assert.match(requests[3].url, /\/preview\/late$/);
  assert.equal(requests[3].options.method, 'DELETE');
});

test('late successful imports record their source page without changing the current page', async () => {
  const {api, element, requests, events, win} = browser();
  let refreshes = 0;
  win.MapWorkspace = {refresh() {refreshes++;}};
  api.setPage('maps');
  await element('importResourcePackageBtn').listeners.click();
  element('resourceExchangeFile').files = [new Blob(['archive'])];
  const uploaded = element('resourceExchangeFile').listeners.change();
  requests[0].resolve(reply({token:'preview-token', source_kind:'exp', resources:[{kind:'map', key:'home', name:'住宅', status:'new'}]}));
  await uploaded;
  const imported = element('resourceExchangeSubmit').listeners.click();
  assert.equal(element('resourceExchangeFile').disabled, true, 'a committed upload cannot change underneath its confirmation');
  assert.equal(element('resourceExchangeDependencies').disabled, true);
  await element('resourceExchangeSubmit').listeners.click();
  assert.equal(requests.length, 2, 'double clicks cannot issue a second mutation');
  assert.deepEqual(JSON.parse(requests[1].options.body).selected, [{kind:'map', key:'home'}]);
  api.setPage('skills');
  assert.equal(requests.length, 2, 'closing during an import does not release its active token');
  requests[1].resolve(reply({imported:[{kind:'map', key:'home'}], reused:[], pending_dependencies:[]}));
  await imported;
  assert.equal(refreshes, 0);
  assert.equal(element('resourceExchangeDialog').open, false);
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'resource-exchange:completed');
  assert.equal(events[0].detail.page, 'maps');
  assert.match(events[0].detail.message, /新增 1 项/);
});

test('the shared exchange is wired into the shell and retains saved-content guidance', () => {
  const shell = fs.readFileSync('src/generative_agents/adapters/web/static/shell/experiment-console.html', 'utf8');
  assert.match(shell, /resource-exchange\.js/);
  assert.match(shell, /<dialog id="resourceExchangeDialog"[^>]+aria-labelledby="resourceExchangeTitle"/);
  assert.match(shell, /导出当前已保存的内容/);
  assert.match(shell, /模型密钥不进入资源包/);
});

const crowdBundle = {token:'crowd-token', source_kind:'config', roots:[{kind:'crowd', key:'class'}], resources:[
  {kind:'crowd', key:'class', name:'同学', status:'new', dependencies:[{kind:'agent', key:'alice'}]},
  {kind:'agent', key:'alice', name:'Alice', status:'new'},
]};
async function importPreview(setup, page, bundle) {
  setup.api.setPage(page);
  await setup.element('importResourcePackageBtn').listeners.click();
  setup.element('resourceExchangeFile').files = [new Blob(['archive'])];
  const uploaded = setup.element('resourceExchangeFile').listeners.change();
  setup.requests.at(-1).resolve(reply(bundle));
  await uploaded;
}

test('either Agent Tab imports a crowd with its members and allows extracting one member', async () => {
  for (const page of ['public-agents', 'crowds']) {
    const setup = browser();
    await importPreview(setup, page, crowdBundle);
    const [crowd, agent] = ['crowd:class', 'agent:alice'].map(key => setup.choices().find(item => item.value === key));
    assert.equal(crowd.checked, true);
    assert.equal(agent.checked, true);
    assert.equal(agent.disabled, true);
    assert.match(agent.reason.textContent, /自动加入.*同学/);
    assert.match(setup.element('resourceExchangeSelectionSummary').textContent, /1 个人群.*1 个智能体/);
    assert.equal(setup.element('resourceExchangeImportOption').hidden, true);
    crowd.checked = false; crowd.listeners.change();
    assert.equal(agent.checked, false);
    assert.equal(agent.disabled, false);
    agent.checked = true; agent.listeners.change();
    assert.equal(crowd.checked, false);
    const submitted = setup.element('resourceExchangeSubmit').listeners.click();
    assert.deepEqual(JSON.parse(setup.requests[1].options.body).selected, [{kind:'agent', key:'alice'}]);
    setup.requests[1].resolve(reply({imported:[{kind:'agent', key:'alice'}]}));
    await submitted;
  }
});

test('shared Skill dependencies stay selected until all referring roots are removed', async () => {
  const setup = browser();
  const resources = [
    {kind:'skill', key:'a', skill_kind:'brain', dependencies:[{kind:'skill', key:'shared'}]},
    {kind:'skill', key:'b', skill_kind:'brain', dependencies:[{kind:'skill', key:'shared'}]},
    {kind:'skill', key:'shared', skill_kind:'pack', dependencies:[{kind:'skill', key:'leaf'}]},
    {kind:'skill', key:'leaf', skill_kind:'atomic'},
  ];
  await importPreview(setup, 'skills', {token:'skills', source_kind:'config', resources, roots:resources.slice(0,2)});
  const get = key => setup.choices().find(item => item.value === `skill:${key}`);
  assert.equal(setup.element('resourceExchangeSubmit').textContent, '导入所选资源（4）');
  get('a').checked = false; get('a').listeners.change();
  assert.equal(get('leaf').checked, true);
  assert.equal(setup.element('resourceExchangeSubmit').textContent, '导入所选资源（3）');
  get('b').checked = false; get('b').listeners.change();
  assert.equal(get('shared').disabled, false);
  assert.equal(get('leaf').checked, false);
  get('shared').checked = true; get('shared').listeners.change();
  assert.equal(get('leaf').checked, true);
  assert.equal(setup.element('resourceExchangeSubmit').textContent, '导入所选资源（2）');
});

test('automatically included conflicts block the whole selection and sparse packages show pending keys', async () => {
  const setup = browser();
  const bundle = structuredClone(crowdBundle);
  bundle.resources[1].status = 'conflict';
  await importPreview(setup, 'public-agents', bundle);
  assert.equal(setup.element('resourceExchangeSubmit').disabled, true);
  assert.match(setup.element('resourceExchangeConflict').textContent, /1 项内容冲突/);
  const sparse = browser();
  await importPreview(sparse, 'crowds', {...crowdBundle, resources:[crowdBundle.resources[0]]});
  assert.equal(sparse.element('resourceExchangeSubmit').disabled, false);
  assert.match(sparse.element('resourceExchangeSelectionSummary').textContent, /关联待绑定.*class → alice/);
});

test('a completed import refreshes the currently visible sibling Tab without navigation', async () => {
  const setup = browser();
  let agents = 0, invalidated = 0;
  setup.win.CrowdWorkspace = {activateAgents() {agents++;}, resourcesImported() {invalidated++;}};
  await importPreview(setup, 'crowds', crowdBundle);
  const submitted = setup.element('resourceExchangeSubmit').listeners.click();
  setup.api.setPage('public-agents');
  setup.requests[1].resolve(reply({imported:[{kind:'crowd', key:'class'}, {kind:'agent', key:'alice'}]}));
  await submitted;
  assert.equal(agents, 1);
  assert.equal(invalidated, 1);
  assert.equal(setup.element('resourceExchangeDialog').open, false);
  assert.equal(setup.events[0].detail.page, 'crowds');
});

test('complete exports require a successful dependency preview and reject late preview results', async () => {
  const setup = browser();
  setup.api.setPage('brains');
  const opened = setup.element('exportResourcePackageBtn').listeners.click();
  setup.requests[0].resolve(reply({items:[{id:'main', key:'main'}]})); await opened;
  setup.element('resourceExchangeExportChoice').value = 'main';
  const preview = setup.element('resourceExchangeExportChoice').listeners.change();
  assert.match(setup.requests[1].url, /export-preview\/brain\/main\?include_dependencies=true/);
  assert.equal(setup.element('resourceExchangeSubmit').disabled, true);
  setup.requests[1].resolve(reply({resources:[{kind:'skill', key:'main', skill_kind:'brain'}, {kind:'skill', key:'child'}]}));
  await preview;
  assert.equal(setup.element('resourceExchangeSubmit').disabled, false);
  assert.match(setup.element('resourceExchangePreview').innerHTML, /1 个大脑、1 个技能/);
  assert.equal(setup.element('resourceExchangeExportOption').hidden, true);
  const late = setup.element('resourceExchangeExportChoice').listeners.change();
  setup.api.setPage('crowds');
  setup.requests[2].resolve(reply({resources:[{kind:'skill', key:'stale'}]})); await late;
  assert.doesNotMatch(setup.element('resourceExchangePreview').innerHTML, /stale/);
});
