const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(loadPage, loadDetail = async id => ({id, name:id, config:{chat:{model:id}}})) {
  const context = {
    window: {}, document: {addEventListener() {}, createElement: () => ({dataset:{}})},
    fetch: async url => {
      const parsed = new URL(url, 'https://studio.test');
      const id = parsed.pathname.split('/model-services/')[1];
      if (id) {
        const item = await loadDetail(decodeURIComponent(id));
        return {ok:Boolean(item), status:item ? 200 : 404, json:async () => item || {detail:'Model not found'}};
      }
      return {ok:true, status:200, json:() => loadPage(parsed.searchParams)};
    },
    Option: function(text, value) { this.textContent = text; this.value = value; this.dataset = {}; },
    Map, URLSearchParams, location:{search:''}, sessionStorage:{getItem() {}},
  };
  vm.createContext(context);
  for (const name of ['resource-list', 'model-workspace']) {
    vm.runInContext(fs.readFileSync(`src/generative_agents/adapters/web/static/resources/${name}.js`, 'utf8'), context);
  }
  function select(value = '') {
    let selectedValue = value;
    return {options:[], isConnected:true, disabled:false,
      get value() { return selectedValue; },
      set value(next) { selectedValue = this.options.some(option => option.value === next) ? next : ''; },
      replaceChildren(...options) { this.options = options; selectedValue = options[0]?.value || ''; },
      add(option) { this.options.push(option); },
      insertAdjacentElement(_position, button) { this.nextElementSibling = button; button.matches = () => true; },
    };
  }
  return {workspace:context.window.ModelWorkspace, select};
}

const model = id => ({id, name:id, purposes:[{purpose:'chat', model:id}, {purpose:'embedding', model:id}]});
const page = (ids, current = 1, pages = 1) => ({items:ids.map(model), page:current, total_pages:pages});

test('empty generative-model choices select the first available catalog result without changing embedding choices', async () => {
  const {workspace, select} = setup(async () => page(['first', 'second']));
  const chat = select(), embedding = select();
  await workspace.loadChoices(chat, 'chat');
  await workspace.loadChoices(embedding, 'embedding');
  assert.equal(chat.value, 'first');
  assert.equal(embedding.value, '');
});

test('loading models preserves an existing explicit selection instead of substituting the first model', async () => {
  const {workspace, select} = setup(async () => page(['first', 'chosen']));
  const selector = select('chosen');
  await workspace.loadChoices(selector, 'chat');
  assert.equal(selector.value, 'chosen');
});

test('a saved selection on a later catalog page is fetched by ID and remains selected when that page loads', async () => {
  const details = [];
  const {workspace, select} = setup(async query => Number(query.get('page')) === 1
    ? page(['first'], 1, 2) : page(['chosen', 'another'], 2, 2), async id => {
      details.push(id);
      return {id, name:'已选配置', config:{chat:{model:'selected-model'}}};
    });
  const selector = select();
  await workspace.loadChoices(selector, 'chat', 'chosen');
  assert.equal(selector.value, 'chosen');
  assert.deepEqual(details, ['chosen']);
  assert.equal(selector.options.find(option => option.value === 'chosen').textContent, '已选配置 · selected-model');
  assert.equal(selector.nextElementSibling.hidden, false);
  selector.nextElementSibling.onclick();
  await new Promise(setImmediate);
  assert.equal(selector.value, 'chosen');
  assert.equal(selector.options.filter(option => option.value === 'chosen').length, 1);
  assert.ok(selector.options.some(option => option.value === 'another'));
});

test('a missing or incompatible saved model is explicitly rejected instead of silently selecting another model', async () => {
  for (const detail of [null, {id:'chosen', config:{embedding:{model:'vector-only'}}}]) {
    const {workspace, select} = setup(async () => page(['first']), async () => detail);
    const selector = select();
    await assert.rejects(workspace.loadChoices(selector, 'chat', 'chosen'), error => error.code === 'MODEL_SELECTION_UNAVAILABLE');
    assert.equal(selector.value, '');
    assert.equal(selector.disabled, false);
  }
});

test('an empty model catalog stays unselected and default selection may be explicitly disabled', async () => {
  const empty = setup(async () => page([])), emptySelector = empty.select();
  await empty.workspace.loadChoices(emptySelector, 'chat');
  assert.equal(emptySelector.value, '');
  const filled = setup(async () => page(['first'])), filledSelector = filled.select();
  await filled.workspace.loadChoices(filledSelector, 'chat', '', false);
  assert.equal(filledSelector.value, '');
});

test('a late generative-model load cannot select a model in a newer choice request', async () => {
  let resolveChat;
  const {workspace, select} = setup(query => query.get('purpose') === 'chat'
    ? new Promise(resolve => { resolveChat = resolve; }) : Promise.resolve(page(['vector'])));
  const selector = select();
  const oldRequest = workspace.loadChoices(selector, 'chat');
  await workspace.loadChoices(selector, 'embedding');
  resolveChat(page(['old-chat']));
  await oldRequest;
  assert.equal(selector.value, '');
  assert.deepEqual(selector.options.map(option => option.value), ['', 'vector']);
});

test('a late selected-model detail cannot change newer model choices', async () => {
  let resolveDetail;
  const {workspace, select} = setup(async query => page([query.get('purpose') === 'chat' ? 'first' : 'vector']),
    () => new Promise(resolve => { resolveDetail = resolve; }));
  const selector = select();
  const oldRequest = workspace.loadChoices(selector, 'chat', 'chosen');
  await new Promise(setImmediate);
  await workspace.loadChoices(selector, 'embedding', '');
  resolveDetail({id:'chosen', name:'chosen', config:{chat:{model:'chosen'}}});
  await oldRequest;
  assert.equal(selector.value, '');
  assert.deepEqual(selector.options.map(option => option.value), ['', 'vector']);
  assert.equal(selector.disabled, false);
});
