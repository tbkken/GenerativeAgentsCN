const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const read = name => fs.readFileSync(`src/generative_agents/adapters/web/static/${name}`, 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const decisionConfig = () => ({
  provider: 'unsloth', model: 'laya-multilingual', base_url: 'http://127.0.0.1:5003/v1',
  timeout_seconds: 45, retry_attempts: 2, retry_backoff_seconds: 0.5,
});

function modelChoices(loadPage, loadDetail) {
  const requests = [];
  const context = {
    window: {}, document: {addEventListener() {}, createElement: () => ({dataset: {}})},
    fetch: async url => {
      const parsed = new URL(url, 'https://studio.test');
      requests.push(parsed);
      const id = parsed.pathname.split('/model-services/')[1];
      const item = id ? await loadDetail(decodeURIComponent(id)) : await loadPage(parsed.searchParams);
      return {ok: Boolean(item), status: item ? 200 : 404, json: async () => item || {detail: 'Model not found'}};
    },
    Option: function(text, value) {this.textContent = text; this.value = value; this.dataset = {};},
    Map, URLSearchParams, location: {search: ''}, sessionStorage: {getItem() {}},
  };
  vm.createContext(context);
  vm.runInContext(read('resources/resource-list.js'), context);
  vm.runInContext(read('resources/model-workspace.js'), context);
  const select = {
    value: '', options: [], isConnected: true, disabled: false,
    replaceChildren(...options) {this.options = options; this.value = options[0]?.value || '';},
    add(option) {this.options.push(option);},
    insertAdjacentElement(_position, button) {this.nextElementSibling = button; button.matches = () => true;},
  };
  return {workspace: context.window.ModelWorkspace, select, requests};
}

test('decision model choices remain optional and restore an explicit saved choice by ID', async () => {
  const item = {id: 'local-decision', name: '本地决策模型', config: {decision: decisionConfig()}};
  const f = modelChoices(async () => ({items: [], page: 1, total_pages: 1}), async () => item);
  await f.workspace.loadChoices(f.select, 'decision');
  assert.equal(f.select.value, '');
  assert.equal(f.requests[0].searchParams.get('purpose'), 'decision');
  await f.workspace.loadChoices(f.select, 'decision', item.id);
  assert.equal(f.select.value, item.id);
  assert.equal(f.select.options.at(-1).textContent, '本地决策模型 · laya-multilingual');
});

test('a saved decision choice cannot silently resolve to a generative model', async () => {
  const f = modelChoices(async () => ({items: [], page: 1, total_pages: 1}),
    async () => ({id: 'wrong-purpose', config: {chat: {model: 'chat-model'}}}));
  await assert.rejects(f.workspace.loadChoices(f.select, 'decision', 'wrong-purpose'), error => {
    assert.equal(error.code, 'MODEL_SELECTION_UNAVAILABLE');
    assert.match(error.message, /决策模型/);
    return true;
  });
  assert.equal(f.select.value, '');
  assert.equal(f.select.disabled, false);
});

test('saving an experiment retains all decision model settings without requiring credentials', async () => {
  const source = read('shell/console-api.js');
  const code = source.slice(source.indexOf('  async function saveDraftUnlocked('), source.indexOf('  function saveDraft(options'));
  const models = {chat: {provider: 'openai', model: 'chat'}, embedding: {provider: 'openai', model: 'embedding'}, decision: decisionConfig()};
  const definition = {experiment: {name: 'Decision experiment'}, simulation: {}, results: {}, agents: [], crowds: [], models};
  const values = {timezone: 'Asia/Shanghai', startTime: '2026-10-15T08:00', stride: '10', maxSteps: '12', seed: '42',
    checkpointInterval: '1', checkpointRetention: '2', projectionInterval: '1'};
  let saved;
  const context = {
    state: {draft: {id: 'experiment', definition_hash: 'before', definition}, selectedExperimentId: 'experiment', experiment: {name: 'Decision experiment'}},
    $: id => ({value: values[id] || '', dataset: {}, classList: {contains: () => false}}),
    structuredClone, simulationStartTime: value => `${value}:00+08:00`, document: {querySelectorAll: () => []},
    saveSecret: async () => null, showToast() {}, acceptSavedDraft: async () => {},
    api: async (_url, options) => {saved = JSON.parse(options.body); return {definition: {...definition, ...saved.sections}};},
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  await context.saveDraftUnlocked({silent: true});
  assert.deepEqual(saved.sections.models, models);
  assert.equal(Object.hasOwn(saved.sections.models.decision, 'credential_env'), false);
  assert.equal(saved.expected_content_sha256, 'before');
});

function copyExperimentModels({selectedDecision = '', selectedChat = 'chat-new', selectedEmbedding = 'embedding-new', existingDecision, fetchModel} = {}) {
  const source = read('shell/console-api.js');
  const begin = source.indexOf('  async function applyExperimentModelChoices(');
  const end = source.indexOf('  async function createExperiment()', begin);
  assert.ok(begin >= 0 && end > begin, 'experiment model copy handler exists');
  const values = {experimentChatModelChoice: selectedChat, experimentEmbeddingModelChoice: selectedEmbedding, experimentDecisionModelChoice: selectedDecision};
  const models = {chat: {model: 'chat-old'}, embedding: {model: 'embedding-old'}, ...(existingDecision ? {decision: existingDecision} : {})};
  let saveCount = 0;
  const errors = [], requests = [];
  const context = {
    state: {selectedExperimentId: 'experiment', draft: {definition: {models}}}, structuredClone,
    $: id => ({value: values[id] || '', addEventListener() {}}),
    api: async url => {
      requests.push(url);
      if (fetchModel) return fetchModel(url);
      return {config: url.endsWith('/chat-new') ? {chat: {model: 'chat-new'}}
        : url.endsWith('/embedding-new') ? {embedding: {model: 'embedding-new'}} : {decision: decisionConfig()}};
    },
    fillModelFields() {}, saveDraft: async () => {saveCount++;}, showToast: message => errors.push(message), reportError: error => errors.push(error.message),
  };
  vm.createContext(context);
  vm.runInContext(source.slice(begin, end), context);
  return {context, requests, errors, click: () => context.applyExperimentModelChoices(), get saveCount() {return saveCount;}};
}

test('replacing generative and embedding model copies preserves an existing optional decision model', async () => {
  const existing = decisionConfig(), f = copyExperimentModels({existingDecision: existing});
  await f.click();
  assert.deepEqual(plain(f.context.state.draft.definition.models), {
    chat: {model: 'chat-new'}, embedding: {model: 'embedding-new'}, decision: existing,
  });
  assert.equal(f.saveCount, 1);
  assert.deepEqual(f.errors, []);
});

test('copying a selected decision model embeds its configuration with the other model purposes', async () => {
  const f = copyExperimentModels({selectedDecision: 'decision-new'});
  await f.click();
  assert.deepEqual(plain(f.context.state.draft.definition.models.decision), decisionConfig());
  assert.ok(f.requests.includes('/resources/model-presets/decision-new'));
  assert.equal(f.saveCount, 1);
  assert.deepEqual(f.errors, []);
});

test('copying an incompatible decision model leaves every existing experiment model unchanged', async () => {
  const f = copyExperimentModels({selectedDecision: 'wrong-purpose', existingDecision: decisionConfig(), fetchModel: async url => ({config:
    url.endsWith('/chat-new') ? {chat: {model: 'chat-new'}} : {embedding: {model: 'embedding-new'}}})});
  const before = plain(f.context.state.draft.definition.models);
  await f.click();
  assert.deepEqual(plain(f.context.state.draft.definition.models), before);
  assert.equal(f.saveCount, 0);
  assert.equal(f.errors.length, 1);
  assert.match(f.errors[0], /类型|决策/);
});

test('explicitly removing a decision model keeps generative and embedding model copies', async () => {
  const f = copyExperimentModels({selectedDecision: '__remove__', existingDecision: decisionConfig()});
  await f.click();
  assert.deepEqual(plain(f.context.state.draft.definition.models), {
    chat: {model: 'chat-new'}, embedding: {model: 'embedding-new'},
  });
  assert.equal(f.requests.some(url => url.endsWith('/__remove__')), false);
  assert.equal(f.saveCount, 1);
  assert.deepEqual(f.errors, []);
});

function skillEditor(markdown, kind = 'skill', {scope, fetchResponse} = {}) {
  const elements = new Map();
  const requests = [], choices = [], notices = [];
  function node(isConnected = true) {
    const events = new Map();
    return {
      value: '', dataset: {}, hidden: false, disabled: false, validationMessage: '', rows: [], options: [], isConnected,
      addEventListener(type, callback) {events.set(type, callback);},
      fire(type) {return events.get(type)?.({target: this});},
      hasHandler(type) {return events.has(type);},
      querySelectorAll: () => [], querySelector: () => null,
      setAttribute() {}, removeAttribute() {}, classList: {add() {}, remove() {}, contains: () => false},
      setCustomValidity(message) {this.validationMessage = message;},
      checkValidity() {return !this.validationMessage;},
      reportValidity() {this.validityReported = true; return this.checkValidity();},
      replaceChildren(...options) {this.options = options; this.value = options[0]?.value || '';},
    };
  }
  const element = id => {
    if (!elements.has(id)) {
      const control = node();
      let markup = '';
      Object.defineProperty(control, 'innerHTML', {get: () => markup, set(value) {
        markup = value;
        if (id === 'skillDecisionSettings') {
          for (const child of ['skillDecisionCompilerModel', 'skillCompileDecision', 'skillDecisionResult']) {
            if (elements.has(child)) elements.get(child).isConnected = false;
            elements.set(child, node());
          }
        }
      }});
      control.querySelector = selector => id === 'skillWorkspace' && selector.startsWith('#') ? element(selector.slice(1)) : null;
      elements.set(id, control);
    }
    return elements.get(id);
  };
  const context = {
    window: {ResourceScope: scope, ResourceList: {}, showToast: message => notices.push(message),
      WorkspaceLoader: {async load() {}}, ModelWorkspace: {async loadChoices(select, purpose, selected, defaultFirst) {
        choices.push({purpose, selected, defaultFirst});
        if (!select.isConnected) return;
        select.replaceChildren(new context.Option('请选择', ''), new context.Option('本地模型', `${purpose}-model`));
        select.value = selected || `${purpose}-model`;
      }}},
    document: {getElementById: element, createElement: () => node(false), addEventListener() {}, body: element('body')},
    Option: function(label, value) {this.textContent = label; this.value = value;},
    Map, URLSearchParams, setTimeout: () => 1, clearTimeout() {}, location: {search: ''},
    fetch: async (url, options) => {
      requests.push({url, options});
      if (fetchResponse) return fetchResponse(url, options);
      const payload = options?.body ? JSON.parse(options.body) : {};
      const body = url.endsWith('/decision-compile') ? compiledDecision(payload.markdown)
        : options?.method === 'PUT' ? {name: 'decide-next-action', kind, markdown: payload.markdown, script_sources: payload.scripts}
        : url.endsWith('/dependencies') ? {skills: [], interfaces: []}
        : {output_text: '已返回决策', model: 'laya-multilingual', trace: []};
      return {ok: true, json: async () => body};
    },
  };
  vm.createContext(context);
  vm.runInContext(read('resources/skill-workspace.js').replace('  window.SkillWorkspace =',
    `  window.testSkillDecision = {state, skillLlmType, skillDecisionQuestions, setSkillLlmType, decisionDraft, decisionIsCurrent,
      renderPanel, renderModelSettings, renderDecisionSettings, bindDecisionSettings, syncModelSettings, publishModelSettings,
      trialModelPurpose, trialModelLabel, runSkill, loadTrialModels, trialDraft, bindPanel, saveSkill, runOutputHtml, compileDecision, sourceChanged};\n  window.SkillWorkspace =`), context);
  const api = context.window.testSkillDecision;
  api.state.current = {name: 'decide-next-action', kind, markdown, description: '决定下一步', script_sources: {}};
  api.state.savedMarkdown = markdown;
  element('skillSource').value = markdown;
  return {api, context, element, requests, choices, notices,
    mountDefinition() {
      const markup = api.renderModelSettings();
      element('skillLlmType').disabled = /<select\b[^>]*id="skillLlmType"[^>]*\bdisabled/.test(markup);
      element('skillLlmType').value = api.skillLlmType();
      element('skillDecisionSettings').innerHTML = api.skillLlmType() === 'decision' ? api.renderDecisionSettings() : '';
      api.bindPanel();
    },
    readyTrial() {
      api.state.activeTab = 'run';
      api.state.dependencies = {skills: [], interfaces: []};
      api.state.relationsLoading = false;
    },
  };
}

test('skills without an LLM type use the generative model and editing the type preserves their source', () => {
  const body = '# 决定下一步\n\n根据真实感知选择行动；使用 $observe 和 @world-perceive。\n';
  const f = skillEditor(`---\nname: decide-next-action\ndescription: "情境: 日常行动"\n---\n${body}`);
  assert.equal(f.api.skillLlmType(), 'generative');
  assert.deepEqual(plain(f.api.skillDecisionQuestions()), {});
  f.api.setSkillLlmType('decision');
  assert.equal(f.api.skillLlmType(), 'decision');
  assert.match(f.api.state.current.markdown, /description: "情境: 日常行动"/);
  assert.ok(f.api.state.current.markdown.endsWith(body));
  assert.equal(f.api.state.current.markdown.match(/^llm_type:/gm).length, 1);
  f.api.setSkillLlmType('generative');
  assert.equal(f.api.skillLlmType(), 'generative');
  assert.ok(f.api.state.current.markdown.endsWith(body));
});

test('adding an LLM type to a plain Markdown skill retains the complete body', () => {
  const body = '# 新技能\n\n处理输入；不要丢失正文中的 --- 或 decision_questions: 字样。';
  const f = skillEditor(body);
  f.api.setSkillLlmType('decision');
  assert.equal(f.api.skillLlmType(), 'decision');
  assert.ok(f.api.state.current.markdown.endsWith(body));
});

test('a Brain cannot switch to the decision model', () => {
  const markdown = '---\nname: decide-next-action\nkind: brain\n---\n# 调度 SOP\n调用子技能处理本轮。';
  const f = skillEditor(markdown, 'brain');
  assert.equal(f.api.skillLlmType(), 'generative');
  assert.throws(() => f.api.setSkillLlmType('decision'), /Brain|大脑|生成式/);
  assert.equal(f.api.state.current.markdown, markdown);
  assert.equal(f.api.skillLlmType(), 'generative');
});

test('model service URLs accept the supplied full decision endpoint and store a single v1 base', () => {
  const context = {window: {ResourceList: {}}, document: {addEventListener() {}}, Map};
  vm.createContext(context);
  vm.runInContext(read('resources/model-workspace.js').replace('  window.ModelWorkspace =',
    '  window.testModelServiceUrl = modelServiceUrl;\n  window.ModelWorkspace ='), context);
  for (const endpoint of ['http://127.0.0.1:5003/v1/systemone', 'http://127.0.0.1:5003/v1/systemone/', 'http://127.0.0.1:5003/v1']) {
    assert.equal(context.window.testModelServiceUrl(endpoint, 'decision'), 'http://127.0.0.1:5003/v1');
  }
  assert.equal(context.window.testModelServiceUrl('https://chat.example/v1', 'chat'), 'https://chat.example/v1');
});

test('the model center submits and tests a decision service without an API key', async () => {
  const elements = new Map(), requests = [];
  const item = {id: 'local-decision', name: '本地决策模型', row_version: 2, config: {decision: decisionConfig()}};
  function element(id) {
    if (elements.has(id)) return elements.get(id);
    const node = {value: '', checked: false, disabled: false, classList: {toggle() {}}, setAttribute() {}};
    Object.defineProperty(node, 'innerHTML', {set(markup) {
      for (const input of markup.matchAll(/<input\b([^>]+)>/g)) {
        const inputId = input[1].match(/\bid="([^"]+)"/)?.[1];
        if (inputId) element(inputId).value = input[1].match(/\bvalue="([^"]*)"/)?.[1] || '';
      }
    }});
    elements.set(id, node);
    return node;
  }
  const context = {
    window: {ResourceList: {route() {}}, scrollTo() {}}, Map,
    document: {getElementById: element, addEventListener() {}},
    fetch: async (url, options) => {
      requests.push({url, ...options});
      const body = url.endsWith('/test/decision') ? {message: '连接成功'}
        : url.endsWith('/model-services') ? {items: [item]} : item;
      return {ok: true, status: 200, json: async () => body};
    },
  };
  vm.createContext(context);
  vm.runInContext(read('resources/model-workspace.js').replace('  window.ModelWorkspace =',
    '  window.testOpenModelEditor = openEditor;\n  window.ModelWorkspace ='), context);
  await context.window.testOpenModelEditor(item.id, false);
  element('modelServiceUrl').value = 'http://127.0.0.1:5003/v1/systemone';
  element('modelServiceKey').value = '';
  await element('modelServiceForm').onsubmit({preventDefault() {}});
  const saved = requests.find(request => request.method === 'PUT');
  assert.ok(saved, 'the model editor sends a save request');
  assert.deepEqual(JSON.parse(saved.body), {
    name: item.name, purpose: 'decision', model: 'laya-multilingual', base_url: 'http://127.0.0.1:5003/v1',
    api_key: null, clear_api_key: false, row_version: 2, timeout_seconds: 45, max_tokens: 2048, temperature: 0,
  });
  await element('testModelService').onclick();
  assert.equal(requests.at(-1).url, '/api/studio/resources/model-services/local-decision/test/decision');
  assert.equal(requests.at(-1).method, 'POST');
  assert.equal(element('modelServiceStatus').textContent, '连接成功');
});

const decisionSkill = (questions = {ready: {type: 'noul', instructions: '# 判断当前情境\n是否已准备好？'}}) => `---\nname: decide-next-action\nllm_type: decision\ndecision_questions: ${JSON.stringify(questions)}\ndecision_prompt_sha256: source-hash\n---\n# 判断当前情境\n是否已准备好？\n`;

function compiledDecision(markdown) {
  const body = markdown.replace(/^---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/, '').trim();
  const questions = {decision: {type: 'noul', instructions: body}};
  const source = markdown.replace(/^decision_questions:.*\r?\n/gm, '').replace(/^decision_prompt_sha256:.*\r?\n/gm, '');
  return {markdown: source.replace(/\r?\n---/, `\ndecision_questions: ${JSON.stringify(questions)}\ndecision_prompt_sha256: compiled-hash\n---`), decision_questions: questions, decision_prompt_sha256: 'compiled-hash', model: 'chat-model'};
}

const response = body => ({ok: true, json: async () => body});

test('selecting decision automatically compiles the complete natural language once and shows readonly results', async () => {
  const body = '# 决策说明\n\n是否准备好？保留所有细节。\n';
  const f = skillEditor(`---\nname: decide-next-action\n---\n${body}`);
  f.mountDefinition();
  f.element('skillLlmType').value = 'decision';
  await f.element('skillLlmType').fire('change');
  const requests = f.requests.filter(request => request.url.endsWith('/decision-compile'));
  assert.equal(requests.length, 1);
  assert.equal(JSON.parse(requests[0].options.body).model_preset_id, 'chat-model');
  assert.ok(JSON.parse(requests[0].options.body).markdown.endsWith(body));
  assert.ok(f.api.state.current.markdown.endsWith(body));
  assert.equal(f.api.decisionIsCurrent(), true);
  const html = f.api.renderDecisionSettings();
  assert.match(html, /已解析|完整问题说明/);
  assert.match(html, /是否准备好？保留所有细节。/);
  assert.doesNotMatch(html, /data-decision-field|添加问题|删除问题|<textarea/);
  await f.api.compileDecision();
  await f.api.saveSkill();
  assert.equal(f.requests.filter(request => request.url.endsWith('/decision-compile')).length, 1);
  assert.equal(f.requests.filter(request => request.options?.method === 'PUT').length, 1);
});

test('compiler model choices load into the connected select and survive status updates with pagination', async () => {
  const f = skillEditor(decisionSkill());
  let release, loadedSelect;
  const more = {page: 1, onclick() {this.page++;}};
  f.context.window.ModelWorkspace.loadChoices = async select => {
    loadedSelect = select;
    await new Promise(resolve => {release = resolve;});
    if (!select.isConnected) return;
    select.replaceChildren(new f.context.Option('本机默认模型', 'local-chat'));
    select.nextElementSibling = more;
  };
  f.mountDefinition();
  await new Promise(setImmediate);
  assert.equal(loadedSelect, f.element('skillDecisionCompilerModel'));
  assert.equal(loadedSelect.isConnected, true);
  f.element('skillSource').value += '\n补充条件'; f.api.sourceChanged();
  assert.equal(loadedSelect, f.element('skillDecisionCompilerModel'));
  assert.equal(loadedSelect.isConnected, true);
  release(); await new Promise(setImmediate);
  assert.equal(f.element('skillDecisionCompilerModel').value, 'local-chat');
  assert.equal(f.api.decisionDraft().modelId, 'local-chat');
  f.api.syncModelSettings();
  assert.equal(f.element('skillDecisionCompilerModel'), loadedSelect);
  assert.equal(f.element('skillDecisionCompilerModel').nextElementSibling, more);
  more.onclick(); assert.equal(more.page, 2);
});

test('saving a changed prompt from the trial tab uses the previously loaded compiler model', async () => {
  const f = skillEditor(decisionSkill());
  f.mountDefinition();
  await new Promise(setImmediate);
  f.element('skillSource').value += '\n新增判断条件。'; f.api.sourceChanged();
  f.api.state.activeTab = 'run';
  f.element('skillDecisionCompilerModel').isConnected = false;
  await f.api.saveSkill();
  const compilation = f.requests.find(request => request.url.endsWith('/decision-compile'));
  assert.ok(compilation);
  assert.equal(JSON.parse(compilation.options.body).model_preset_id, 'chat-model');
  assert.equal(f.choices.filter(choice => choice.purpose === 'chat').length, 1);
  assert.equal(f.requests.filter(request => request.options?.method === 'PUT').length, 1);
});

test('editing the prompt marks derived parameters stale and saving recompiles before persisting', async () => {
  const f = skillEditor(decisionSkill());
  f.mountDefinition();
  await new Promise(setImmediate);
  assert.equal(f.api.decisionIsCurrent(), true);
  f.element('skillSource').value += '\n仅在所有人员确认后回答是。';
  f.api.sourceChanged();
  assert.equal(f.api.decisionIsCurrent(), false);
  assert.match(f.api.renderDecisionSettings(), /需要解析/);
  await f.api.saveSkill();
  assert.equal(f.requests.filter(request => request.url.endsWith('/decision-compile')).length, 1);
  const saved = f.requests.find(request => request.options?.method === 'PUT');
  assert.ok(saved);
  const source = JSON.parse(saved.options.body).markdown;
  assert.match(source, /仅在所有人员确认后回答是/);
  assert.equal(f.api.decisionIsCurrent(), true);
  await f.api.saveSkill();
  assert.equal(f.requests.filter(request => request.url.endsWith('/decision-compile')).length, 1);
});

test('reopened stale or empty extracted questions require compilation', () => {
  for (const questions of [{}, {ready: {type: 'noul', instructions: '旧问题说明'}}]) {
    const f = skillEditor(decisionSkill(questions));
    assert.equal(f.api.decisionIsCurrent(), false);
    assert.match(f.api.renderDecisionSettings(), /需要解析/);
    assert.doesNotMatch(f.api.renderDecisionSettings(), /旧问题说明/);
  }
});

test('saving during automatic compilation reuses the one pending request', async () => {
  let release;
  const f = skillEditor('---\nname: decide-next-action\nllm_type: decision\n---\n是否继续？', 'skill', {fetchResponse: async (url, options) => {
    const payload = options?.body ? JSON.parse(options.body) : {};
    if (url.endsWith('/decision-compile')) {
      await new Promise(resolve => {release = resolve;});
      return response(compiledDecision(payload.markdown));
    }
    return response(options?.method === 'PUT'
      ? {name: 'decide-next-action', kind: 'skill', markdown: payload.markdown, script_sources: {}}
      : {skills: [], interfaces: []});
  }});
  const pending = f.api.compileDecision();
  await new Promise(setImmediate);
  const saving = f.api.saveSkill();
  release();
  assert.equal(await pending, true);
  await saving;
  assert.equal(f.requests.filter(request => request.url.endsWith('/decision-compile')).length, 1);
  assert.equal(f.requests.filter(request => request.options?.method === 'PUT').length, 1);
});

test('a late compile result cannot overwrite a newer prompt or another selected skill', async () => {
  for (const switchSkill of [false, true]) {
    let release, submitted;
    const f = skillEditor('---\nname: decide-next-action\nllm_type: decision\n---\n是否继续？', 'skill', {fetchResponse: async (url, options) => {
      submitted = JSON.parse(options.body).markdown;
      await new Promise(resolve => {release = resolve;});
      return response(compiledDecision(submitted));
    }});
    const pending = f.api.compileDecision();
    await new Promise(setImmediate);
    if (switchSkill) {
      f.api.state.editorGeneration++;
      f.api.state.current = {name: 'other', kind: 'skill', markdown: '# 其他技能', script_sources: {}};
      f.element('skillSource').value = '# 其他技能';
    } else {
      f.element('skillSource').value += '\n新增条件';
      f.api.sourceChanged();
    }
    const expected = f.api.state.current.markdown;
    release();
    assert.equal(await pending, false);
    assert.equal(f.api.state.current.markdown, expected);
  }
});

test('compile failure blocks saving and leaves the original natural language intact', async () => {
  const markdown = '---\nname: decide-next-action\nllm_type: decision\n---\n请判断情境。';
  const f = skillEditor(markdown, 'skill', {fetchResponse: async () => ({ok: false, status: 422, json: async () => ({detail: '缺少明确的决策问题，请补充正文。'})})});
  await f.api.saveSkill();
  assert.equal(f.requests.length, 1);
  assert.ok(f.requests[0].url.endsWith('/decision-compile'));
  assert.equal(f.api.state.current.markdown, markdown);
  assert.match(f.api.renderDecisionSettings(), /解析失败|缺少明确的决策问题/);
  assert.equal(f.element('skillSave').disabled, false);
});

test('experiment compilation uses its embedded chat model without changing the resource digest', async () => {
  let optionsCount = 0, savedCount = 0;
  const scope = {experimentId: 'experiment-one', base: '/api/studio/experiments/experiment-one/resources',
    options: value => {optionsCount++; return value;}, saved() {savedCount++;}};
  const f = skillEditor('---\nname: decide-next-action\nllm_type: decision\n---\n是否继续？', 'skill', {scope});
  assert.equal(await f.api.compileDecision(), true);
  assert.equal(f.choices.length, 0);
  assert.equal(f.requests[0].url, '/api/studio/experiments/experiment-one/resources/skills/decide-next-action/decision-compile');
  assert.deepEqual(Object.keys(JSON.parse(f.requests[0].options.body)), ['markdown', 'scripts']);
  assert.deepEqual(JSON.parse(f.requests[0].options.body).scripts, {});
  assert.equal(optionsCount, 0);
  assert.equal(savedCount, 0);
});

test('compilation submits current draft scripts and discards a result after scripts change', async () => {
  let release;
  const f = skillEditor('---\nname: decide-next-action\nllm_type: decision\n---\n是否继续？', 'skill', {fetchResponse: async (url, options) => {
    await new Promise(resolve => {release = resolve;});
    return response(compiledDecision(JSON.parse(options.body).markdown));
  }});
  f.api.state.current.script_sources = {'scripts/old.py': 'old'};
  delete f.api.state.current.script_sources['scripts/old.py'];
  const pending = f.api.compileDecision();
  await new Promise(setImmediate);
  assert.deepEqual(JSON.parse(f.requests[0].options.body).scripts, {});
  f.api.state.current.script_sources['scripts/new.py'] = 'new';
  release();
  assert.equal(await pending, false);
  assert.doesNotMatch(f.api.state.current.markdown, /decision_prompt_sha256:/);
  assert.equal(f.api.state.current.script_sources['scripts/new.py'], 'new');
});

test('switching to generative clears both derived fields and invalidates the decision trial', async () => {
  const f = skillEditor(decisionSkill());
  f.api.trialDraft().modelId = 'saved-decision';
  f.api.state.run = {status: 'done', result: {output_text: 'old decision'}};
  f.mountDefinition();
  f.element('skillLlmType').value = 'generative'; await f.element('skillLlmType').fire('change');
  assert.equal(f.api.skillLlmType(), 'generative');
  assert.doesNotMatch(f.api.state.current.markdown, /^decision_(questions|prompt_sha256):/m);
  assert.equal(f.element('skillDecisionSettings').hidden, true);
  assert.equal(f.api.state.run, null);
  assert.equal(f.api.trialDraft().modelId, '');
});

test('readonly decision skills expose no question editing and cannot request compilation', async () => {
  const f = skillEditor(decisionSkill());
  f.api.state.current.editable = false;
  f.mountDefinition();
  assert.equal(f.element('skillLlmType').disabled, true);
  assert.match(f.api.renderDecisionSettings(), /id="skillCompileDecision" disabled/);
  assert.doesNotMatch(f.api.renderDecisionSettings(), /data-decision-field|<textarea/);
  assert.equal(await f.api.compileDecision(true), true);
  assert.equal(f.requests.length, 0);
  const brain = skillEditor('---\nname: decide-next-action\n---\n# Brain', 'brain');
  assert.match(brain.api.renderModelSettings(), /<option value="decision"[^>]*\bdisabled/);
});

test('decision trial loads decision choices and sends natural input without an experiment or Agent', async () => {
  const f = skillEditor(decisionSkill({ready: {type: 'noul', instructions: '是否准备好？'}}));
  f.readyTrial();
  const draft = f.api.trialDraft();
  draft.experimentId = 'old-generative-experiment'; draft.agentKey = 'old-agent';
  assert.equal(f.api.trialModelPurpose(), 'decision');
  assert.equal(f.api.trialModelLabel(), '决策模型');
  assert.match(f.api.renderPanel(), /class="skill-trial-environment" hidden/);
  f.api.bindPanel();
  await new Promise(setImmediate);
  assert.deepEqual(f.choices, [{purpose: 'decision', selected: '', defaultFirst: true}]);
  assert.equal(f.requests.length, 0, 'decision trials do not load experiment or Agent lists');
  assert.equal(f.api.state.trialOptions, null);
  f.element('skillRunInput').value = '  当前已收到全部确认，判断是否可以继续。  ';
  await f.api.runSkill();
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].url, '/api/studio/resources/skills/decide-next-action/run');
  assert.deepEqual(JSON.parse(f.requests[0].options.body), {
    input_text: '  当前已收到全部确认，判断是否可以继续。  ', model_preset_id: 'decision-model', experiment_id: null, agent_key: null,
  });
  assert.equal(f.api.state.run.status, 'done');
  assert.deepEqual(f.notices, []);
});

test('an experiment decision trial uses its embedded model and needs no Agent selection', async () => {
  const scope = {experimentId: 'experiment-one', base: '/api/studio/experiments/experiment-one/resources', options: value => value, saved() {}};
  const f = skillEditor(decisionSkill({ready: {type: 'noul', instructions: '是否准备好？'}}), 'skill', {scope});
  f.readyTrial();
  await f.api.loadTrialModels();
  assert.equal(f.choices.length, 0);
  assert.equal(f.element('skillRunModel').value, '');
  assert.equal(f.element('skillRunModel').disabled, true);
  assert.equal(f.element('skillRunModel').options[0].textContent, '使用当前实验的决策模型');
  assert.match(f.element('skillTrialModelHint').textContent, /实验内已保存的决策模型/);
  f.element('skillRunInput').value = '实验内决策模型判断当前输入。';
  await f.api.runSkill();
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].url, '/api/studio/experiments/experiment-one/resources/skills/decide-next-action/run');
  assert.deepEqual(JSON.parse(f.requests[0].options.body), {
    input_text: '实验内决策模型判断当前输入。', model_preset_id: null, experiment_id: 'experiment-one', agent_key: null,
  });
  assert.equal(f.api.state.run.status, 'done');
  assert.deepEqual(f.notices, []);
});

test('changing only the decision model preserves existing generative and embedding settings', async () => {
  const f = copyExperimentModels({selectedDecision: 'decision-new', selectedChat: '', selectedEmbedding: ''});
  await f.click();
  assert.deepEqual(plain(f.context.state.draft.definition.models), {
    chat: {model: 'chat-old'}, embedding: {model: 'embedding-old'}, decision: decisionConfig(),
  });
  assert.deepEqual(f.requests, ['/resources/model-presets/decision-new']);
  assert.equal(f.saveCount, 1);
  assert.deepEqual(f.errors, []);
});

for (const decisionId of ['', 'chosen-decision']) {
  test(`creating an experiment ${decisionId ? 'includes an explicitly chosen' : 'does not require an optional'} decision model`, async () => {
    const source = read('shell/console-api.js');
    const start = source.indexOf('  async function createExperiment()');
    const code = source.slice(start, source.indexOf('  function setDuplicateStatus(', start));
    const values = {newExperimentBrain: 'brain-resource', newExperimentMap: 'map-resource', newExperimentChatModel: 'chat-resource',
      newExperimentEmbeddingModel: 'embedding-resource', newExperimentDecisionModel: decisionId, newExperimentName: '决策实验',
      newExperimentGoal: '判断并执行', newExperimentOwner: '作者', newExperimentTag: '决策，测试'};
    let submitted;
    const opened = [];
    const context = {
      $: id => ({value: values[id] || '', selectedOptions: [{dataset: {skillName: 'main-brain'}}]}),
      window: {CrowdWorkspace: {selectedCreateRevisionIds: () => ['crowd-resource']}},
      api: async (url, options) => {assert.equal(url, '/experiments'); submitted = JSON.parse(options.body); return {experiment_id: 'created'};},
      closeModal() {}, loadExperiments: async () => {}, openExperiment: async id => opened.push(id), showToast() {},
    };
    vm.createContext(context); vm.runInContext(code, context);
    await context.createExperiment();
    assert.equal(submitted.model_preset_id, 'chat-resource');
    assert.equal(submitted.embedding_model_preset_id, 'embedding-resource');
    assert.equal(submitted.brain_skill_id, 'brain-resource');
    assert.equal(Object.hasOwn(submitted, 'decision_model_preset_id'), Boolean(decisionId));
    if (decisionId) assert.equal(submitted.decision_model_preset_id, decisionId);
    assert.deepEqual(opened, ['created']);
  });
}

test('the new experiment wizard loads decision choices with no default selection', async () => {
  const source = read('shell/console-api.js');
  const start = source.indexOf('  async function prepareWizardResources()');
  const code = source.slice(start, source.indexOf('  async function prepareExperimentBrainChoices()', start));
  const elements = new Map(), choices = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, {id, value: id === 'newExperimentDecisionModel' ? 'previous-decision' : '', options: []});
    return elements.get(id);
  };
  const context = {
    $: element, state: {wizardGeneration: 1, wizardResourcesReady: false}, prepareExperimentBrainChoices: async () => {}, renderWizardStep() {},
    window: {WorkspaceLoader: {load: async () => {}}, MapWorkspace: {prepareExperimentCreate: async () => {}},
      CrowdWorkspace: {prepareExperimentCreate: async () => {}}, ModelWorkspace: {
        loadChoices: async (select, purpose, selected, defaultFirst) => {
          choices.push({id: select.id, purpose, selected, defaultFirst});
          select.options = [{value: '', textContent: '请选择'}, {value: 'model', textContent: '已配置模型'}];
          select.value = defaultFirst === false ? '' : 'model';
        },
      }},
  };
  vm.createContext(context); vm.runInContext(code, context);
  await context.prepareWizardResources();
  assert.deepEqual(choices.find(choice => choice.purpose === 'decision'), {
    id: 'newExperimentDecisionModel', purpose: 'decision', selected: '', defaultFirst: false,
  });
  assert.equal(element('newExperimentDecisionModel').value, '');
  assert.equal(element('newExperimentDecisionModel').options[0].textContent, '不配置决策模型');
  assert.equal(context.state.wizardResourcesReady, true);
});

test('decision trial output displays the real model state and questions request without inventing chat prompts', () => {
  const f = skillEditor(decisionSkill({ready: {type: 'noul', instructions: '是否继续？'}}));
  f.api.state.run = {status: 'done', result: {output_text: '可以继续', trace: [{event: 'skill.start', skill: 'decide-next-action',
    decision_request: {model: 'laya-multilingual', state: '<script>必须转义</script>', questions: {ready: {type: 'noul', instructions: '是否继续？'}}},
  }]}};
  const html = f.api.runOutputHtml();
  assert.match(html, /发送给模型的请求/);
  assert.match(html, /MODEL \/ STATE \/ QUESTIONS/);
  assert.match(html, /laya-multilingual/);
  assert.match(html, /是否继续？/);
  assert.match(html, /&lt;script&gt;必须转义&lt;\/script&gt;/);
  assert.doesNotMatch(html, /SYSTEM PROMPT|USER PROMPT|本次运行结果未包含 Prompt|<script>/);
});
