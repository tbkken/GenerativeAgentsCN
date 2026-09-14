const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('generative_agents/web/static/resource-list.js', 'utf8');

function setup(storage = new Map(), experimentId = null) {
  const handlers = {}, nodes = {}, scrolls = [], frames = [];
  const context = {window: {ResourceScope: {experimentId}, scrollY: 350, scrollTo: value => scrolls.push(value)},
    document: {addEventListener: (event, handler) => handlers[event] = handler,
      getElementById: id => nodes[id], querySelectorAll: () => []},
    sessionStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key,value)},
    requestAnimationFrame: fn => frames.push(fn), Map, URLSearchParams, location: {pathname:'/', search:''}, history:{pushState() {}},
  };
  vm.createContext(context); vm.runInContext(source, context);
  return {list:context.window.ResourceList, context, storage, nodes, scrolls, frames};
}

test('every catalog page contains at most five distinct items and shrinking catalogs clamp the last page', () => {
  const {list} = setup(), items = Array.from({length:13}, (_,i)=>i);
  assert.deepEqual([...list.slice(items,1).items], [0,1,2,3,4]);
  assert.deepEqual([...list.slice(items,2).items], [5,6,7,8,9]);
  assert.deepEqual([...list.slice(items,3).items], [10,11,12]);
  assert.equal(list.slice(items.slice(0,5),3).page, 1);
  assert.equal(list.slice([],2).page, 1);
});

test('list search, type and page survive a document navigation without crossing resources or experiment scopes', () => {
  const {list, storage} = setup();
  list.remember('skills', {page:2, query:'observe', kind:'pack', scroll:400});
  list.remember('brains', {page:3, query:'morning'});
  const reloaded = setup(storage).list;
  assert.equal(reloaded.read('skills').page, 2);
  assert.equal(reloaded.read('skills').kind, 'pack');
  assert.equal(reloaded.read('brains').query, 'morning');
  assert.equal(reloaded.read('maps').page, 1);
  assert.equal(setup(storage, 'experiment-a').list.read('skills').query, '');
});

test('late rendering cannot restore scroll on a different page, and restoration is consumed once', () => {
  const {list, nodes, scrolls, frames} = setup();
  let active = true;
  nodes['page-maps'] = {classList:{contains:()=>active}, querySelector:()=>({})};
  list.capture('maps');
  list.restore('maps');
  active = false; frames.shift()();
  assert.equal(scrolls.length,0);
  active = true; list.restore('maps'); frames.shift()();
  assert.equal(scrolls[0].top,350);
  list.restore('maps'); assert.equal(frames.length,0);
});

test('untrusted names, attributes and descriptions remain text in shared rows', () => {
  const {list} = setup();
  const html = list.row({name:'<img src=x onerror=alert(1)>', description:'</p><script>bad()</script>',
    open:{'data-item':'" autofocus onfocus="bad()'}, actions:[{label:'删除',danger:true,attributes:{'data-delete':'id'}}]});
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;img'));
  assert.ok(html.includes('&quot; autofocus'));
  assert.equal((html.match(/data-item=/g)||[]).length,2);
  assert.ok(html.includes('<details class="resource-more">'));
});

test('a model with chat and embedding capabilities has one row and one deletion target', () => {
  const {list, context} = setup(), nodes = {};
  context.list = list;
  context.$ = id => nodes[id] ||= {innerHTML:'',querySelectorAll:()=>[]};
  context.items = [{id:'combined',name:'模型配置',config:{chat:{model:'chat-a'},embedding:{model:'embed-a'}},credential_configured:{chat:true}}];
  context.openEditor = () => {}; context.deleteModel = () => {}; context.report = () => {};
  const models = fs.readFileSync('generative_agents/web/static/model-workspace.js','utf8');
  vm.runInContext(models.slice(models.indexOf('  function renderList()'), models.indexOf('  function report(')),context);
  context.renderList();
  const html = nodes.modelServiceList.innerHTML;
  assert.equal((html.match(/<article/g)||[]).length,1);
  assert.ok(html.includes('chat-a') && html.includes('embed-a'));
  assert.equal((html.match(/data-delete-model=/g)||[]).length,1);
  list.remember('model-catalog',{kind:'embedding'});
  context.renderList();
  assert.equal((nodes.modelServiceList.innerHTML.match(/<article/g)||[]).length,1);
});
