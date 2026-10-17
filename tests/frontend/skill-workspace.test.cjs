const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/generative_agents/adapters/web/static/resources/skill-workspace.js', 'utf8');
const listSource = fs.readFileSync('src/generative_agents/adapters/web/static/resources/resource-list.js', 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const reply = value => ({ok: true, json: async () => value});
const deferred = () => {let resolve; const promise = new Promise(done => {resolve = done;}); return {promise, resolve};};
const tick = () => new Promise(resolve => setImmediate(resolve));
const documentFor = (name, markdown = '') => ({name, markdown, kind: 'skill', description: name, script_sources: {}, content_hash: name});
const dependenciesFor = (skill, extra = {}) => ({skill, scripts: [], skills: [], interfaces: [], interface_catalog: [
  {name: 'world-perceive', description: '读取当前位置的空间和活动', input_schema: {type: 'object'}},
  {name: 'world-act', description: '提交已校验的动作'},
], ...extra});

function setup({scope} = {}) {
  const nodes = {}, handlers = new Map(), timers = new Map(), classes = new Set(), requests = [], notices = [], storage = new Map();
  let nextTimer = 0;
  function element(id) {
    if (nodes[id]) return nodes[id];
    let markup = '';
    const value = nodes[id] = {id, hidden: false, value: '', dataset: {}, disabled: false, selectionStart: 0, selectionEnd: 0, scrollTop: 0, isConnected: true, options: [],
      addEventListener(type, callback) {handlers.set(`${id}:${type}`, callback);},
      focus() {context.document.activeElement = this;},
      setSelectionRange(start, end) {this.selectionStart = start; this.selectionEnd = end;},
      showModal() {this.open = true;}, close() {this.open = false;},
      setAttribute(name, content) {this[name] = content;},
      removeAttribute(name) {delete this[name];},
      querySelector(selector) {return selector.startsWith('#') ? element(selector.slice(1)) : null;},
      querySelectorAll() {return [];}, replaceChildren(...children) {this.innerHTML = ''; this.options = children; this.value = children[0]?.value || '';},
    };
    Object.defineProperty(value, 'innerHTML', {get: () => markup, set(html) {
      markup = html;
      for (const textarea of html.matchAll(/<textarea id="([^"]+)"[^>]*>([\s\S]*?)<\/textarea>/g)) element(textarea[1]).value = textarea[2].replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
    }});
    return value;
  }
  const context = {console, URLSearchParams, Map, Option: function(label, value) {this.textContent = label; this.value = value;}, location: {search: ''},
    setTimeout(callback) {const id = ++nextTimer; timers.set(id, callback); return id;}, clearTimeout(id) {timers.delete(id);},
    document: {getElementById: element, addEventListener() {}, body: {classList: {
      add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value),
    }}},
    window: {ResourceScope: scope, scrollTo() {}, showToast: message => notices.push(message), confirmResourceDeletion: async () => true,
      ModelWorkspace: {async loadChoices(select, kind, selected) {select.replaceChildren(new context.Option('请选择', ''), new context.Option('生成式模型一', 'model-1'), new context.Option('生成式模型二', 'model-2')); select.value = selected || 'model-1';}}},
    sessionStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
    fetch: async () => {throw new Error('unexpected fetch');},
  };
  vm.createContext(context);
  vm.runInContext(listSource, context);
  context.window.ResourceList.capture = () => {};
  context.window.ResourceList.route = () => {};
  vm.runInContext(source.replace('  window.SkillWorkspace =', `  window.testSkill = {state, references, resetRelations, cacheRelations,
    renderOutline, renderRelation, renderInterfaceHelp, loadDependencies, refreshRelations, bindPanel, saveSkill,
    syncEditorTopbar, deactivateTopbar, renderEditor, bindOutline, openInterfacePicker, closeInterfacePicker,
    updateInterfacePicker, insertInterface, renderPanel, trialDraft, trialInterfaces, loadTrialModels, loadTrialEnvironment,
    loadTrialAgents, selectTrialExperiment, selectTrialModel, invalidateTrialResult, runSkill, runOutputHtml};\n  window.SkillWorkspace =`), context);
  const api = context.window.testSkill;
  const f = {api, context, nodes, handlers, timers, classes, requests, notices, element, workspace: context.window.SkillWorkspace,
    setFetch(callback) {context.fetch = async (url, options) => {requests.push({url, options}); return callback(url, options);};},
    select(item) {api.state.current = item; api.state.editorGeneration++; api.resetRelations(item); api.state.activeTab = 'definition'; api.state.activeFile = 'SKILL.md'; element('skillSource').value = item.markdown;},
    async fireTimers() {const pending = [...timers.values()]; timers.clear(); for (const callback of pending) await callback();},
  };
  return f;
}

test('skill catalogs discard obsolete type filters while preserving search, pagination and child summaries', async () => {
  for (const oldKind of ['atomic', 'pack']) {
    const f = setup();
    f.context.window.ResourceList.remember('skills', {page: 3, query: 'observe', kind: oldKind});
    f.setFetch(async () => reply({page: 3, total: 12, items: [
      {...documentFor('observe-leaf'), children: [], scripts: []},
      {...documentFor('observe-compose'), children: ['observe-leaf'], scripts: []},
    ]}));
    await f.workspace.activate('skills');
    const params = new URLSearchParams(f.requests[0].url.split('?')[1]);
    assert.equal(params.get('kind'), 'skill');
    assert.equal(params.get('q'), 'observe');
    assert.equal(params.get('page'), '3');
    assert.equal(params.get('page_size'), '5');
    assert.equal(f.context.window.ResourceList.read('skills').kind, '');
    assert.doesNotMatch(f.nodes.skillWorkspace.innerHTML, /skillKindFilter|<select/);
    assert.match(f.nodes.authorSkillRows.innerHTML, /Observe Leaf/);
    assert.match(f.nodes.authorSkillRows.innerHTML, /Observe Compose/);
    assert.match(f.nodes.authorSkillRows.innerHTML, /1 个子技能/);
    assert.doesNotMatch(f.nodes.authorSkillRows.innerHTML, /单个技能|技能包|原子|resource-badge/);
    assert.match(f.nodes.authorSkillFooter.innerHTML, /共 12 条/);
  }
});

test('creating a skill or brain has no type selector and submits only its page kind', async () => {
  for (const kind of ['skill', 'brain']) {
    const f = setup(), created = {...documentFor(`new-${kind}`), kind};
    f.setFetch(async (url, options) => reply(options.method === 'POST' ? created : dependenciesFor(created.name)));
    if (kind === 'skill') f.workspace.showCreate();
    else f.workspace.showCreate('brain');
    const host = f.nodes[kind === 'brain' ? 'brainSkillWorkspace' : 'skillWorkspace'];
    assert.doesNotMatch(host.innerHTML, /skillCreateKind|<select|技能包|单个技能/);
    f.element('skillCreateName').value = created.name;
    f.element('skillCreateDisplayName').value = '规划日常活动';
    f.element('skillCreateDescription').value = '编排或直接执行自然语言步骤';
    await f.handlers.get('skillCreateConfirm:click')();
    assert.deepEqual(JSON.parse(f.requests[0].options.body), {
      name: created.name, display_name: '规划日常活动', description: '编排或直接执行自然语言步骤', kind,
    });
    assert.equal(f.api.state.current.kind, kind);
    assert.equal(f.api.state.current.name, created.name);
    assert.equal(f.nodes.skillDelete.textContent, kind === 'brain' ? '删除大脑' : '删除技能');
  }
});

test('switching to brains uses its own kind and rejects a late skill catalog response', async () => {
  const f = setup(), old = deferred();
  f.setFetch(url => new URLSearchParams(url.split('?')[1]).get('kind') === 'skill'
    ? old.promise : reply({page: 1, total: 1, items: [{...documentFor('main-brain'), kind: 'brain'}]}));
  const first = f.workspace.activate('skills');
  await tick();
  await f.workspace.activate('brains');
  old.resolve(reply({page: 1, total: 1, items: [documentFor('late-skill')]}));
  await first;
  assert.equal(f.api.state.page, 'brains');
  assert.equal(f.api.state.kind, 'brain');
  assert.match(f.nodes.authorSkillRows.innerHTML, /Main Brain/);
  assert.doesNotMatch(f.nodes.authorSkillRows.innerHTML, /Late Skill/);
});

test('only explicit body references are parsed, preserving full unknown interface names', () => {
  const {api} = setup();
  const parsed = api.references('---\nname: root\ndescription: "$header @world-act"\n---\n'
    + '调用$child-skill，再调用 `$child-skill`；使用@world-perceive。\n'
    + 'world-act $bad_key x$joined path/$folder user@world-act @@duplicate @World-act @world_act @unknown');
  assert.deepEqual(plain(parsed), {skills: ['child-skill'], interfaces: ['world-perceive', 'World-act', 'world_act', 'unknown']});
});

test('details hide resource tabs and leaving restores them, with only definition and trial tabs', () => {
  const f = setup(); f.select(documentFor('root'));
  f.api.renderEditor();
  assert.equal(f.nodes.resourceTabs.hidden, true);
  assert.equal(f.classes.has('skill-editor-mode'), true);
  assert.deepEqual([...f.nodes.skillWorkspace.innerHTML.matchAll(/data-skill-tab="([^"]+)"/g)].map(match => match[1]), ['definition', 'run']);
  f.api.deactivateTopbar();
  assert.equal(f.nodes.resourceTabs.hidden, false);
  assert.equal(f.classes.has('skill-editor-mode'), false);
});

test('the outline shows actual files, nested interfaces and visible missing/cycle diagnoses as text', () => {
  const f = setup(); f.select(documentFor('root', '$child @world-perceive @unknown'));
  f.api.state.current.script_sources = {'scripts/private.py': 'return 1'};
  f.api.state.dependencies = dependenciesFor('root');
  f.api.cacheRelations([{name: 'child', description: '<img onerror="bad()">', scripts: ['scripts/nested.py'], interfaces: ['world-act'], skills: [
    {name: 'absent', missing: true}, {name: 'root', cycle: true},
  ]}]);
  const html = f.api.renderOutline();
  assert.match(html, /scripts\/private.py/);
  assert.match(html, /scripts\/nested.py/);
  assert.match(html, /@world-act/);
  assert.match(html, /缺失子 Skill/);
  assert.match(html, /循环引用：root → child → root/);
  assert.match(html, /未知接口/);
  assert.ok(!html.includes('<img'));
  assert.match(html, /&lt;img/);
  f.api.state.selectedInterface = 'world-perceive';
  const help = f.api.renderInterfaceHelp();
  assert.match(help, /读取当前位置/);
  assert.match(help, /输入参数/);
});

test('typing updates root references without saving or replacing the source control', async () => {
  const f = setup(); f.select(documentFor('root', '@world-perceive'));
  f.api.state.dependencies = dependenciesFor('root');
  f.api.bindPanel();
  const input = f.element('skillSource');
  input.value = '改为 $new-child 和 @world-act';
  f.handlers.get('skillSource:input')();
  assert.equal(f.api.state.current.markdown, input.value);
  assert.match(f.nodes.skillFileOutline.innerHTML, /尚未保存/);
  assert.match(f.nodes.skillFileOutline.innerHTML, /\$new-child/);
  assert.equal(f.element('skillSource'), input);
  assert.equal(f.requests.length, 0);
  f.setFetch(async url => reply(dependenciesFor('new-child', {interfaces: ['world-perceive']})));
  await f.fireTimers();
  assert.equal(f.requests.length, 1);
  assert.match(f.requests[0].url, /new-child\/dependencies$/);
  assert.match(f.nodes.skillFileOutline.innerHTML, /@world-perceive/);
});

test('interface help distinguishes Agent and object parameters without claiming default calls are declared', () => {
  const f = setup(); f.select(documentFor('root', '@world-act'));
  f.api.state.dependencies = dependenciesFor('root', {interface_catalog: [{name: 'world-act', description: '提交动作',
    input_schema: {properties: {action: {type: 'string'}}}, object_description: '只能修改自身状态并回复真实请求',
    object_input_schema: {properties: {responses: {type: 'array'}, target_agent_key: {type: 'string'}, evidence_ids: {type: 'array'}, idempotency_key: {type: 'string'}}},
  }]});
  f.api.state.selectedInterface = 'world-act';
  const help = f.api.renderInterfaceHelp();
  assert.match(help, /Agent 输入参数/);
  assert.match(help, /对象输入参数/);
  assert.match(help, /只能修改自身状态/);
  for (const field of ['responses', 'target_agent_key', 'evidence_ids', 'idempotency_key']) assert.ok(help.includes(field));
  assert.match(help, /仅展示正文显式引用/);
});

test('adding and deleting a script changes actual file contents without changing skill references', async () => {
  const f = setup(); f.select(documentFor('root', '$child'));
  const add = f.element('addScript'), remove = f.element('removeScript'), outline = f.element('skillFileOutline');
  outline.querySelector = selector => selector === '[data-add-skill-script]' ? add : null;
  f.element('skillWorkspace').querySelector = selector => selector === '[data-delete-skill-script]' ? remove : selector.startsWith('#') ? f.element(selector.slice(1)) : null;
  f.api.bindPanel();
  f.handlers.get('addScript:click')();
  assert.equal(f.api.state.activeFile, 'scripts/main.py');
  assert.ok(f.api.state.current.script_sources['scripts/main.py'].includes('def run'));
  assert.equal(f.api.state.current.markdown, '$child');
  await f.handlers.get('removeScript:click')();
  assert.equal(f.api.state.activeFile, 'SKILL.md');
  assert.deepEqual(Object.keys(f.api.state.current.script_sources), []);
  assert.equal(f.api.state.current.markdown, '$child');
});

test('switching to a script and typing does not cancel pending child relation loading', async () => {
  const f = setup(); f.select(documentFor('root'));
  f.api.state.current.script_sources = {'scripts/main.py': 'return 1'};
  f.api.state.dependencies = dependenciesFor('root');
  const file = f.element('scriptFile'); file.dataset.skillFile = 'scripts/main.py';
  f.element('skillFileOutline').querySelectorAll = selector => selector === '[data-skill-file]' ? [file] : [];
  f.api.bindPanel();
  f.nodes.skillSource.value = '$new-child';
  f.handlers.get('skillSource:input')();
  f.handlers.get('scriptFile:click')();
  f.nodes.skillSource.value = 'return 2';
  f.handlers.get('skillSource:input')();
  f.setFetch(async () => reply(dependenciesFor('new-child', {interfaces: ['world-act']})));
  await f.fireTimers();
  assert.equal(f.requests.length, 1);
  assert.equal(f.api.state.current.markdown, '$new-child');
  assert.equal(f.api.state.current.script_sources['scripts/main.py'], 'return 2');
  assert.match(f.nodes.skillFileOutline.innerHTML, /@world-act/);
  assert.ok(!f.nodes.skillFileOutline.innerHTML.includes('读取调用关系中'));
});

test('late dependency responses cannot replace the selected skill or its interface catalog', async () => {
  const f = setup(), old = deferred();
  f.setFetch(async url => {
    if (url.endsWith('/one')) return reply(documentFor('one'));
    if (url.endsWith('/one/dependencies')) return old.promise;
    if (url.endsWith('/two')) return reply(documentFor('two', '@world-act'));
    return reply(dependenciesFor('two'));
  });
  const first = f.workspace.openSkill('one', false); await tick();
  await f.workspace.openSkill('two', false);
  old.resolve(reply(dependenciesFor('one', {interface_catalog: [{name: 'stale-interface'}]})));
  await first;
  assert.equal(f.api.state.current.name, 'two');
  assert.equal(f.api.state.dependencies.skill, 'two');
  assert.ok(!f.nodes.skillFileOutline.innerHTML.includes('stale-interface'));
});

test('opening a skill loads real dependency responses and diagnoses a newly typed self-reference', async () => {
  const f = setup();
  f.setFetch(async url => reply(url.endsWith('/dependencies') ? dependenciesFor('root', {
    skills: [{name: 'saved-child', interfaces: ['world-act'], skills: []}],
  }) : documentFor('root', '$saved-child')));
  await f.workspace.openSkill('root', false);
  assert.equal(f.api.state.dependencies.skill, 'root');
  assert.equal(Number.isFinite(f.api.state.dependencyGeneration), true);
  assert.match(f.nodes.skillFileOutline.innerHTML, /\$saved-child/);
  assert.match(f.nodes.skillFileOutline.innerHTML, /@world-act/);
  f.nodes.skillSource.value = '$root @world-perceive';
  f.handlers.get('skillSource:input')();
  assert.match(f.nodes.skillFileOutline.innerHTML, /循环引用：root → root/);
  assert.match(f.nodes.skillFileOutline.innerHTML, /尚未保存/);
});

test('dependency failure is recoverable and does not mislabel interfaces as unknown', async () => {
  const f = setup(); f.select(documentFor('root', '@world-act'));
  f.setFetch(async () => ({ok: false, status: 503, json: async () => ({detail: '暂时不可用'})}));
  await f.api.loadDependencies();
  assert.match(f.nodes.skillFileOutline.innerHTML, /调用关系读取失败/);
  assert.match(f.nodes.skillFileOutline.innerHTML, /重新读取/);
  assert.match(f.nodes.skillFileOutline.innerHTML, /说明暂不可用/);
  assert.ok(!f.nodes.skillFileOutline.innerHTML.includes('未知接口'));
  f.setFetch(async () => reply(dependenciesFor('root')));
  await f.api.loadDependencies();
  assert.equal(f.api.state.relationError, '');
  assert.ok(!f.nodes.skillFileOutline.innerHTML.includes('说明暂不可用'));
});

test('a delayed child lookup cannot overwrite newer edited references or a newer lookup', async () => {
  const f = setup(), old = deferred(); f.select(documentFor('root', '$child'));
  f.api.state.dependencies = dependenciesFor('root');
  f.setFetch(() => old.promise);
  const first = f.api.refreshRelations();
  f.api.state.current.markdown = '$replacement';
  f.setFetch(async () => reply(dependenciesFor('replacement')));
  await f.api.refreshRelations();
  old.resolve(reply(dependenciesFor('child', {interfaces: ['stale-interface']})));
  await first;
  assert.equal(f.api.state.relationNodes.has('child'), false);
  assert.ok(!f.nodes.skillFileOutline.innerHTML.includes('stale-interface'));
  assert.match(f.nodes.skillFileOutline.innerHTML, /\$replacement/);
});

test('saving keeps edits made during the request and records the actual saved baseline', async () => {
  const f = setup(), saved = deferred(); f.select(documentFor('root', '@world-perceive'));
  f.setFetch(async (url, options) => options.method === 'PUT' ? saved.promise : reply(dependenciesFor('root')));
  const pending = f.api.saveSkill();
  f.nodes.skillSource.value = '@world-act';
  saved.resolve(reply(documentFor('root', '@world-perceive')));
  await pending;
  assert.equal(f.api.state.current.markdown, '@world-act');
  assert.equal(f.api.state.savedMarkdown, '@world-perceive');
  assert.match(f.nodes.skillFileOutline.innerHTML, /尚未保存/);
  assert.equal(JSON.parse(f.requests[0].options.body).markdown, '@world-perceive');
});

test('a saved response arriving after selection changes cannot replace the new editor', async () => {
  const f = setup(), saved = deferred(); f.select(documentFor('first', '@world-perceive'));
  f.setFetch(() => saved.promise);
  const pending = f.api.saveSkill();
  f.select(documentFor('second', '@world-act'));
  saved.resolve(reply(documentFor('first', '@world-perceive')));
  await pending;
  assert.equal(f.api.state.current.name, 'second');
  assert.equal(f.api.state.current.markdown, '@world-act');
  assert.equal(f.requests.length, 1);
  assert.equal(f.notices.length, 0);
});

test('Chinese display names appear in catalogs, editor and dependency nodes while stable keys remain references', async () => {
  const f = setup(), item = {...documentFor('daily-plan'), display_name: '制定全天日程'};
  f.setFetch(async () => reply({page: 1, total: 1, items: [item]}));
  await f.workspace.activate('skills');
  assert.match(f.nodes.authorSkillRows.innerHTML, /制定全天日程/);
  assert.match(f.nodes.authorSkillRows.innerHTML, /data-skill-name="daily-plan"/);
  assert.match(f.nodes.authorSkillRows.innerHTML, /daily-plan/);
  f.select(item); f.api.renderEditor();
  assert.equal(f.nodes.skillEditorTitle.textContent, '制定全天日程');
  assert.equal(f.nodes.skillEditorTitle.title, '稳定键：daily-plan');
  const relation = f.api.renderRelation({name: 'choose-object', display_name: '选择活动对象', skills: []});
  assert.match(relation, /选择活动对象/);
  assert.match(relation, /\$choose-object/);
});

test('the interface picker searches dynamic Chinese catalog metadata and escapes its text', () => {
  const f = setup(); f.select(documentFor('root', '接口：'));
  f.api.state.dependencies = dependenciesFor('root', {interface_catalog: [
    {name: 'new-runtime-interface', display_name: '新增能力', description_zh: '只读能力 <script>danger</script>', description: 'English fallback'},
    {name: 'world-act', display_name: '提交动作', description_zh: '由运行时校验'},
  ]});
  f.api.bindPanel(); f.api.openInterfacePicker();
  assert.equal(f.nodes.skillInterfacePicker.open, true);
  assert.equal(f.context.document.activeElement, f.nodes.skillInterfaceSearch);
  assert.match(f.nodes.skillInterfacePickerList.innerHTML, /新增能力/);
  assert.match(f.nodes.skillInterfacePickerList.innerHTML, /@new-runtime-interface/);
  assert.match(f.nodes.skillInterfacePickerList.innerHTML, /&lt;script&gt;/);
  assert.doesNotMatch(f.nodes.skillInterfacePickerList.innerHTML, /English fallback|<script>/);
  f.handlers.get('skillInterfaceSearch:input')({target: {value: '只读能力'}});
  assert.match(f.nodes.skillInterfacePickerList.innerHTML, /new-runtime-interface/);
  assert.doesNotMatch(f.nodes.skillInterfacePickerList.innerHTML, /world-act/);
  f.handlers.get('skillInterfaceSearch:input')({target: {value: '不存在'}});
  assert.match(f.nodes.skillInterfacePickerList.innerHTML, /没有匹配的接口/);
});

test('inserting an interface replaces the body selection, separates ASCII words and updates unsaved relations', () => {
  const f = setup(); f.select(documentFor('root', 'beforeSELECTafter'));
  f.api.state.dependencies = dependenciesFor('root');
  f.api.bindPanel();
  const input = f.nodes.skillSource;
  input.setSelectionRange(6, 12); input.scrollTop = 72;
  f.api.openInterfacePicker();
  f.api.insertInterface('world-act');
  assert.equal(input.value, 'before @world-act after');
  assert.deepEqual(plain(f.api.references(input.value).interfaces), ['world-act']);
  assert.equal(f.api.state.current.markdown, input.value);
  assert.match(f.nodes.skillFileOutline.innerHTML, /尚未保存/);
  assert.match(f.nodes.skillFileOutline.innerHTML, /@world-act/);
  assert.equal(f.nodes.skillInterfacePicker.open, false);
  assert.equal(f.context.document.activeElement, input);
  assert.equal(input.selectionStart, 'before @world-act '.length);
  assert.equal(input.selectionEnd, input.selectionStart);
  assert.equal(input.scrollTop, 72);
  assert.equal(f.requests.length, 0);
});

test('default and header cursors append to the body, and selections crossing the header preserve metadata', () => {
  const header = '---\nname: root\ndisplay_name: 根技能\ndescription: 用途\n---\n';
  for (const [start, end] of [[0, 0], [12, 20], [0, header.length]]) {
    const f = setup(); f.select(documentFor('root', header + '正文内容'));
    f.api.state.dependencies = dependenciesFor('root');
    f.nodes.skillSource.setSelectionRange(start, end);
    f.api.openInterfacePicker(); f.api.insertInterface('world-perceive');
    assert.equal(f.nodes.skillSource.value, header + '正文内容\n\n@world-perceive');
    assert.deepEqual(plain(f.api.references(f.nodes.skillSource.value).interfaces), ['world-perceive']);
  }
  const f = setup(); f.select(documentFor('root', header + '替换保留'));
  f.api.state.dependencies = dependenciesFor('root');
  f.nodes.skillSource.setSelectionRange(0, header.length + 2);
  f.api.openInterfacePicker(); f.api.insertInterface('world-act');
  assert.equal(f.nodes.skillSource.value, header + '@world-act保留');
  const beginning = setup(); beginning.select(documentFor('root', header + '正文开头'));
  beginning.api.state.dependencies = dependenciesFor('root');
  beginning.nodes.skillSource.setSelectionRange(header.length, header.length);
  beginning.api.openInterfacePicker(); beginning.api.insertInterface('world-act');
  assert.equal(beginning.nodes.skillSource.value, header + '@world-act正文开头');
  const empty = setup(); empty.select(documentFor('root', header.trimEnd()));
  empty.api.state.dependencies = dependenciesFor('root');
  empty.api.openInterfacePicker(); empty.api.insertInterface('world-act');
  assert.equal(empty.nodes.skillSource.value, header.trimEnd() + '\n\n@world-act');
});

test('Escape cancels insertion and restores the body selection; script and read-only editors cannot insert', () => {
  const f = setup(); f.select(documentFor('root', '保留原文'));
  f.api.state.dependencies = dependenciesFor('root'); f.api.bindPanel();
  f.nodes.skillSource.setSelectionRange(1, 3);
  f.api.openInterfacePicker();
  let prevented = false;
  f.handlers.get('skillInterfacePicker:cancel')({preventDefault() {prevented = true;}});
  assert.equal(prevented, true);
  assert.equal(f.nodes.skillSource.value, '保留原文');
  assert.equal(f.nodes.skillSource.selectionStart, 1);
  assert.equal(f.nodes.skillSource.selectionEnd, 3);
  assert.equal(f.nodes.skillInterfacePicker.open, false);
  f.api.state.current.script_sources = {'scripts/main.py': 'return 1'};
  f.api.state.activeFile = 'scripts/main.py'; f.api.renderEditor();
  assert.match(f.nodes.skillWorkspace.innerHTML, /id="skillInsertInterface" disabled/);
  f.api.openInterfacePicker();
  assert.equal(f.api.state.interfacePicker, null);
  f.api.state.activeFile = 'SKILL.md'; f.api.state.current.editable = false;
  f.api.renderEditor(); f.api.openInterfacePicker();
  assert.equal(f.api.state.interfacePicker, null);
});

test('interface catalog failures offer retry and late results cannot affect another editor', async () => {
  const f = setup(); f.select(documentFor('root'));
  f.setFetch(async () => ({ok: false, status: 503, json: async () => ({detail: '目录暂时不可用'})}));
  await f.api.openInterfacePicker();
  assert.match(f.nodes.skillInterfacePickerList.innerHTML, /目录暂时不可用/);
  assert.match(f.nodes.skillInterfacePickerList.innerHTML, /重新读取接口目录/);
  f.setFetch(async () => reply(dependenciesFor('root')));
  f.handlers.get('skillInterfacePickerRetry:click')();
  await tick();
  assert.match(f.nodes.skillInterfacePickerList.innerHTML, /@world-act/);
  f.api.closeInterfacePicker();
  const old = deferred(); f.select(documentFor('first'));
  f.setFetch(() => old.promise);
  const pending = f.api.openInterfacePicker();
  f.select(documentFor('second', '新文档'));
  old.resolve(reply(dependenciesFor('first', {interface_catalog: [{name: 'old-interface'}]})));
  await pending;
  assert.equal(f.api.state.current.name, 'second');
  assert.equal(f.api.state.interfacePicker, null);
  assert.equal(f.api.state.dependencies, null);
  assert.equal(f.nodes.skillInterfacePicker.open, false);
  assert.equal(f.nodes.skillSource.value, '新文档');
});

test('the saved frontmatter is the display-name source and reopening uses the server result', async () => {
  const f = setup(), markdown = '---\nname: daily-plan\ndisplay_name: 新的中文名称\ndescription: 中文用途\n---\n调用 @world-perceive';
  const saved = {...documentFor('daily-plan', markdown), display_name: '新的中文名称'};
  f.select({...documentFor('daily-plan', markdown), display_name: '旧名称'});
  f.setFetch(async url => reply(url.endsWith('/dependencies') ? dependenciesFor('daily-plan') : saved));
  await f.api.saveSkill();
  assert.equal(f.nodes.skillEditorTitle.textContent, '新的中文名称');
  assert.deepEqual(JSON.parse(f.requests[0].options.body), {markdown, scripts: {}});
  await f.workspace.openSkill('daily-plan', false);
  assert.equal(f.nodes.skillEditorTitle.textContent, '新的中文名称');
  assert.equal(f.nodes.skillSource.value, markdown);
  assert.match(f.nodes.skillFileOutline.innerHTML, /已保存内容/);
});

function prepareTrial(f, {name = 'describe-scene', interfaces = ['world-perceive'], input = '描述当前位置。'} = {}) {
  f.select({...documentFor(name), example_input: input});
  f.api.state.dependencies = dependenciesFor(name, {interfaces});
  f.api.state.relationsLoading = false;
  f.api.state.activeTab = 'run';
  f.element('skillRunInput').value = input;
  f.element('skillRunModel').value = 'model-1';
  f.api.state.trialOptions = {loading: false, agentsLoading: false, agents: [], experiments: []};
  return f.api.trialDraft();
}

test('trial UI uses natural input and experiment/Agent selection without an editable runtime context', async () => {
  const f = setup(); prepareTrial(f);
  const html = f.api.renderPanel();
  assert.match(html, /id="skillRunExperiment"/);
  assert.match(html, /id="skillRunAgent"/);
  assert.match(html, /生成式模型/);
  assert.match(html, /输入原文将直接交给 SKILL/);
  assert.doesNotMatch(html, /skillRunContext|可选运行时上下文|聊天模型|自动整理|解析情境/);
  await f.api.loadTrialModels();
  assert.equal(f.nodes.skillRunModel.value, 'model-1');
  f.api.trialDraft().modelId = 'model-2';
  await f.api.loadTrialModels();
  assert.equal(f.nodes.skillRunModel.value, 'model-2');
});

test('environment interfaces require a chosen experiment and Agent and unsaved definitions cannot be tested', async () => {
  const f = setup(), draft = prepareTrial(f, {interfaces: []});
  f.api.state.dependencies.skills = [{name: 'child', interfaces: ['memory-stream-search']}];
  await f.api.runSkill();
  assert.match(f.notices.at(-1), /需要实验环境/);
  assert.equal(f.requests.length, 0);
  draft.experimentId = 'experiment-a';
  await f.api.runSkill();
  assert.match(f.notices.at(-1), /选择实验和其中的 Agent/);
  draft.agentKey = 'alice';
  f.api.state.trialOptions.agents = [{key: 'alice', name: '艾丽丝'}];
  f.api.state.current.markdown = '未保存修改';
  await f.api.runSkill();
  assert.match(f.notices.at(-1), /先保存内容/);
  assert.equal(f.requests.length, 0);
});

test('switching experiments clears Agent and results immediately and rejects stale Agent responses', async () => {
  const f = setup(), draft = prepareTrial(f), old = deferred(), latest = deferred();
  f.setFetch(url => url.includes('/experiment-a/') ? old.promise : latest.promise);
  draft.agentKey = 'old-agent'; f.api.state.run = {status: 'done', result: {output_text: '旧结果'}};
  f.api.selectTrialExperiment('experiment-a');
  assert.equal(draft.agentKey, '');
  assert.equal(f.api.state.run, null);
  assert.equal(f.nodes.skillRunAgent.disabled, true);
  assert.equal(f.nodes.skillRunButton.disabled, true);
  f.api.selectTrialExperiment('experiment-b');
  latest.resolve(reply({items: [{key: 'bob', name: '鲍勃'}]})); await tick();
  old.resolve(reply({items: [{key: 'alice', name: '艾丽丝'}]})); await tick();
  assert.equal(draft.experimentId, 'experiment-b');
  assert.equal(draft.agentKey, '');
  assert.deepEqual(f.nodes.skillRunAgent.options.map(option => option.value), ['', 'bob']);
  assert.equal(f.nodes.skillRunAgent.disabled, false);
  assert.equal(f.nodes.skillRunButton.disabled, false);
});

test('trial preserves natural input and exposes execution events and actual model usage read-only', async () => {
  const input = '  描述当前位置。\n保留观察中的原话。\n', f = setup(), draft = prepareTrial(f, {input});
  draft.experimentId = 'experiment-a'; draft.agentKey = 'alice';
  f.api.state.trialOptions.agents = [{key: 'alice', name: '艾丽丝'}];
  f.setFetch(async () => reply({input_text: input, output_text: '这里是咖啡馆。', model_usage: {logical_calls: 2, physical_attempts: 3}, world_commit: 'NOT_COMMITTED', environment: {experiment_id: 'experiment-a'}, trace: [
    {event: 'skill.start', skill: 'describe-scene', user_prompt: input},
    {event: 'interface.call', tool: 'world-perceive', output_text: '{"place":"咖啡馆"}'},
    {event: 'skill.result', skill: 'describe-scene', output_text: '这里是咖啡馆。'},
    {event: 'trial.complete', status: 'SUCCEEDED'},
  ]}));
  await f.api.runSkill();
  assert.equal(f.requests[0].url, '/api/studio/resources/skills/describe-scene/run');
  assert.deepEqual(JSON.parse(f.requests[0].options.body), {input_text: input, model_preset_id: 'model-1', experiment_id: 'experiment-a', agent_key: 'alice'});
  assert.equal(f.api.state.run.status, 'done');
  const output = f.api.runOutputHtml();
  assert.match(output, /开始技能/);
  assert.match(output, /技能返回/);
  assert.match(output, /试运行结束/);
  assert.match(output, /逻辑调用 2 次 · 物理尝试 3 次/);
  assert.match(output, /事件数量不等于模型调用次数/);
  assert.doesNotMatch(output, /系统解析情境|trial.context/);
  assert.match(output, /<details class="skill-trace-detail">/);
  assert.match(output, /world-perceive/);
  assert.match(output, /世界提交：不适用，本次未产生世界动作/);
  assert.doesNotMatch(output, /<textarea|<input/);
  f.api.state.run.result.planned_action = {action: 'MOVE'};
  assert.match(f.api.runOutputHtml(), /世界动作：接口已接受，未提交到世界/);
});

test('a standalone trial sends original text directly and the running state has no parsing phase', async () => {
  const f = setup(), pending = deferred(), input = '\n  写一段简短说明。  \n';
  prepareTrial(f, {interfaces: [], input});
  f.setFetch(() => pending.promise);
  const running = f.api.runSkill();
  assert.match(f.api.runOutputHtml(), /正在执行 SKILL/);
  assert.match(f.api.runOutputHtml(), /已将输入原文交给技能/);
  assert.doesNotMatch(f.api.runOutputHtml(), /解析|整理输入/);
  assert.deepEqual(JSON.parse(f.requests[0].options.body), {input_text: input, model_preset_id: 'model-1', experiment_id: null, agent_key: null});
  pending.resolve(reply({input_text: input, output_text: '说明', model_usage: {logical_calls: 1, physical_attempts: 1}, trace: [
    {event: 'skill.start', skill: 'describe-scene', user_prompt: input},
    {event: 'skill.result', skill: 'describe-scene', output_text: '说明'},
    {event: 'trial.complete', status: 'SUCCEEDED'},
  ]}));
  await running;
  assert.match(f.api.runOutputHtml(), /逻辑调用 1 次 · 物理尝试 1 次/);
  f.element('skillRunInput').value = ' \n\t ';
  await f.api.runSkill();
  assert.equal(f.requests.length, 1);
  assert.match(f.notices.at(-1), /请先描述一个要测试的情境/);
});

test('trial shows original-result handoff audit and numbers only the available model prompts', () => {
  const f = setup(); prepareTrial(f, {interfaces: []});
  f.api.state.run = {status: 'done', result: {output_text: '完成', trace: [
    {event: 'skill.start', skill: 'main', system_prompt: '父技能', user_prompt: '处理材料'},
    {event: 'skill.call', skill: 'main', child: 'collect', tool_call_id: 'call-collect', input_text: '收集材料'},
    {event: 'skill.result', skill: 'collect', output_text: '  原始材料\n保留细节。\n'},
    {event: 'skill.call', skill: 'main', child: 'summarize', tool_call_id: 'call-summarize', input_from: 'call-collect', input_text: '  原始材料\n保留细节。\n'},
    {event: 'skill.start', skill: 'summarize', system_prompt: '子技能', user_prompt: '  原始材料\n保留细节。\n'},
    {event: 'skill.result', skill: 'summarize', output_text: '完成'},
    {event: 'trial.complete', status: 'SUCCEEDED'},
  ]}};
  const html = f.api.runOutputHtml();
  assert.match(html, /输入：已完成技能的结果原文/);
  assert.match(html, /input_from/);
  assert.match(html, /call-collect/);
  assert.match(html, /tool_call_id/);
  assert.match(html, /1\/2 · system \+ user/);
  assert.match(html, /2\/2 · system \+ user/);
  assert.doesNotMatch(html, /5\/2|逻辑调用 \d+ 次/);
});

test('failed trial renders rejected handoffs and tool calls with escaped source details', () => {
  const f = setup(); prepareTrial(f, {interfaces: []});
  f.api.state.run = {status: 'error', message: '调用参数不合法：<missing-source>', trace: [
    {event: 'skill.call.rejected', skill: 'main', child: '<child>', tool: 'call_skill', tool_call_id: 'call-next', input_from: '<missing-source>', error: '原文来源不存在：<missing-source>'},
    {event: 'tool.call.rejected', skill: 'main', tool: '<unknown-tool>', tool_call_id: 'call-tool', arguments: {input_text: '<script>alert(1)</script>'}, error: '未知工具'},
  ]};
  const html = f.api.runOutputHtml();
  assert.match(html, /子技能调用被拒绝/);
  assert.match(html, /工具调用被拒绝/);
  assert.match(html, /main → &lt;child&gt;/);
  assert.match(html, /&lt;unknown-tool&gt;/);
  assert.match(html, /input_from/);
  assert.match(html, /&lt;missing-source&gt;/);
  assert.match(html, /call-next/);
  assert.match(html, /call-tool/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>|<missing-source>|<child>|<unknown-tool>/);
});

test('late trial success and failure do not replace another input, experiment, Agent, or skill result', async () => {
  for (const change of ['input', 'experiment', 'agent', 'skill']) {
    for (const fails of [false, true]) {
      const f = setup(), draft = prepareTrial(f), late = deferred();
      draft.experimentId = 'experiment-a'; draft.agentKey = 'alice';
      f.api.state.trialOptions.agents = [{key: 'alice'}];
      f.setFetch(() => late.promise);
      const pending = f.api.runSkill();
      if (change === 'skill') prepareTrial(f, {name: 'another-skill'});
      else {if (change === 'agent') draft.agentKey = 'bob'; else if (change === 'input') draft.input = '新的情境'; else draft.experimentId = 'experiment-b'; f.api.invalidateTrialResult();}
      f.api.state.run = {status: 'done', result: {output_text: '当前选择的结果'}};
      late.resolve(fails ? {ok: false, status: 400, json: async () => ({detail: '旧试运行失败'})} : reply({output_text: '旧试运行结果'}));
      await pending;
      assert.equal(f.api.state.run.result.output_text, '当前选择的结果');
      assert.equal(f.notices.length, 0);
    }
  }
});

test('natural input and valid choices reopen per Skill without leaking a previous Skill draft', () => {
  const f = setup(), first = prepareTrial(f);
  first.input = '请重点描述附近的人物。'; first.experimentId = 'experiment-a'; first.agentKey = 'alice';
  const second = prepareTrial(f, {name: 'daily-plan', input: '安排今天的活动。'});
  assert.equal(second.experimentId, ''); assert.equal(second.agentKey, '');
  assert.match(f.api.renderPanel(), /安排今天的活动/);
  prepareTrial(f);
  assert.equal(f.api.trialDraft(), first);
  assert.match(f.api.renderPanel(), /请重点描述附近的人物/);
});

test('experiment Skill trial preselects its environment, explicitly asks for Agent and uses embedded model', async () => {
  const f = setup({scope: {experimentId: 'experiment-a', base: '/api/studio/experiments/experiment-a/resources', options: value => value}});
  const draft = prepareTrial(f);
  f.setFetch(async url => reply(url.endsWith('/agents') ? {items: [{key: 'alice', name: '艾丽丝'}]} : {items: [{id: 'experiment-a', name: '小镇'}]}));
  await f.api.loadTrialEnvironment(); await f.api.loadTrialModels();
  assert.equal(draft.experimentId, 'experiment-a');
  assert.equal(draft.agentKey, '');
  assert.equal(f.nodes.skillRunExperiment.disabled, true);
  assert.equal(f.nodes.skillRunAgent.value, '');
  assert.equal(f.nodes.skillRunModel.disabled, true);
  assert.match(f.nodes.skillRunModel.options[0].textContent, /实验的生成式模型/);
  assert.ok(f.requests.every(request => request.url.startsWith('/api/studio/resources/skill-trial/')));
});

test('late option and model choices cannot update a different Skill draft', async () => {
  const f = setup(), old = deferred(), model = deferred(); prepareTrial(f);
  f.setFetch(() => old.promise);
  f.context.window.ModelWorkspace.loadChoices = async select => {await model.promise; select.value = 'stale-model';};
  const environments = f.api.loadTrialEnvironment(), models = f.api.loadTrialModels();
  const current = prepareTrial(f, {name: 'another-skill'});
  current.modelId = 'new-model';
  old.resolve(reply({items: [{id: 'old-experiment', name: '旧实验'}]})); model.resolve();
  await Promise.all([environments, models]);
  assert.equal(current.modelId, 'new-model');
  assert.equal(current.experimentId, '');
  assert.deepEqual(f.api.state.trialOptions.experiments, []);
});

test('an explicit later-page model stays selected until its detail is resolved and is submitted unchanged', async () => {
  const f = setup(), draft = prepareTrial(f, {interfaces: []}), detail = deferred();
  draft.modelId = 'model-page-two';
  f.context.window.ModelWorkspace.loadChoices = async select => {
    select.replaceChildren(new f.context.Option('请选择', ''), new f.context.Option('第一页模型', 'model-1'));
    await detail.promise;
    select.replaceChildren(...select.options, new f.context.Option('第二页模型', 'model-page-two'));
    select.value = 'model-page-two';
  };
  const pending = f.api.loadTrialModels();
  assert.equal(draft.modelId, 'model-page-two');
  assert.equal(f.nodes.skillRunButton.disabled, true);
  detail.resolve(); await pending;
  assert.equal(draft.modelId, 'model-page-two');
  assert.equal(f.nodes.skillRunModel.value, 'model-page-two');
  f.setFetch(async () => reply({output_text: '完成'}));
  await f.api.runSkill();
  assert.equal(JSON.parse(f.requests[0].options.body).model_preset_id, 'model-page-two');
});

test('an unavailable explicit model remains visible as an error and cannot silently fall back to experiment model', async () => {
  const f = setup(), draft = prepareTrial(f);
  draft.modelId = 'deleted-model'; draft.experimentId = 'experiment-a'; draft.agentKey = 'alice';
  f.api.state.trialOptions.agents = [{key: 'alice'}];
  f.context.window.ModelWorkspace.loadChoices = async select => {
    select.replaceChildren(new f.context.Option('请选择', ''), new f.context.Option('可用模型', 'model-1'));
    throw new Error('所选模型已被删除');
  };
  await f.api.loadTrialModels();
  assert.equal(draft.modelId, 'deleted-model');
  assert.match(f.nodes.skillTrialModelHint.textContent, /所选模型已被删除/);
  await f.api.runSkill();
  assert.equal(f.requests.length, 0);
  assert.match(f.notices.at(-1), /请重新选择可用模型/);
  f.api.selectTrialModel('');
  await f.api.runSkill();
  assert.equal(f.requests.length, 0);
  f.nodes.skillRunModel.value = 'model-1'; f.api.selectTrialModel('model-1');
  f.setFetch(async () => reply({output_text: '完成'}));
  await f.api.runSkill();
  assert.equal(JSON.parse(f.requests[0].options.body).model_preset_id, 'model-1');
});
