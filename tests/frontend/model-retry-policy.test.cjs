const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const suggestedUrls = {
  chat:'https://chat.host.test:6060/api/v2', embedding:'http://vector.host.test:6061/v1',
  decision:'http://laya.host.test:6062/v1',
};

async function editor(config) {
  const elements = new Map(), requests = [];
  const item = config ? {id:'chat-model', name:'本地模型', row_version:2, config:{chat:config}} : null;
  function element(id) {
    if (!elements.has(id)) {
      const node = {value:'', checked:false, classList:{toggle() {}}, setAttribute() {}, removeAttribute() {}};
      Object.defineProperty(node, 'innerHTML', {set(markup) {
        this.markup = markup;
        for (const input of markup.matchAll(/<input\b([^>]+)>/g)) {
          const inputId = input[1].match(/\bid="([^"]+)"/)?.[1];
          if (inputId) element(inputId).value = input[1].match(/\bvalue="([^"]*)"/)?.[1] || '';
        }
      }});
      elements.set(id, node);
    }
    return elements.get(id);
  }
  const context = {Map, URLSearchParams, location:{search:item ? '?model_id=chat-model' : '?create=1'},
    document:{getElementById:element, addEventListener() {}},
    window:{ResourceList:{route() {}, loading() {}, read:() => ({page:1, query:'', kind:''})}, scrollTo() {}},
    fetch: async (url, options = {}) => {
      requests.push({url, ...options});
      const saved = item || {id:'new-model', config:{chat:{}}};
      const list = !options.method && url.split('?')[0].endsWith('/model-services');
      return {ok:true, status:200, json: async () => list ? {items:[saved], suggested_urls:suggestedUrls} : saved};
    }};
  vm.createContext(context);
  const source = fs.readFileSync('src/generative_agents/adapters/web/static/resources/model-workspace.js', 'utf8');
  vm.runInContext(source, context);
  await context.window.ModelWorkspace.activate();
  return {element, requests, submit: () => element('modelServiceForm').onsubmit({preventDefault() {}})};
}

test('new model forms use each host suggestion and saved models retain the author URL', async () => {
  const f = await editor();
  for (const purpose of ['chat', 'embedding', 'decision']) {
    await f.element('addModelService-' + purpose).onclick();
    assert.equal(f.element('modelServiceUrl').value, suggestedUrls[purpose]);
  }
  assert.equal(f.requests.length, 1, 'all suggestions arrive with the existing catalog request');
  const customUrl = 'https://provider.example/tooling/v3';
  const saved = await editor({model:'chosen', base_url:customUrl});
  assert.equal(saved.element('modelServiceUrl').value, customUrl);
  await saved.element('addModelService-embedding').onclick();
  assert.equal(saved.element('modelServiceUrl').value, suggestedUrls.embedding);
  await saved.element('addModelService-chat').onclick();
  assert.equal(saved.element('modelServiceUrl').value, customUrl);
});

test('new chat models explain the total budget and use bounded retry defaults', async () => {
  const f = await editor();
  assert.equal(f.element('modelServiceRetryAttempts').value, '3');
  assert.equal(f.element('modelServiceRetryBackoff').value, '5');
  assert.match(f.element('modelServiceEditor').markup, /包含首次请求/);
  assert.match(f.element('modelServiceEditor').markup, /传输请求次数/);
  assert.match(f.element('modelServiceEditor').markup, /最多 2 次纠错/);
  assert.match(f.element('modelServiceEditor').markup, /总时间预算/);
  await f.submit();
  const body = JSON.parse(f.requests.find(request => request.method === 'POST').body);
  assert.equal(body.retry_attempts, 3);
  assert.equal(body.retry_backoff_seconds, 5);
});

test('editing a model preserves an explicit single attempt and zero backoff until changed', async () => {
  const f = await editor({model:'chat', base_url:'http://localhost:8888/v1', timeout_seconds:600,
    retry_attempts:1, retry_backoff_seconds:0, max_tokens:2048, temperature:0});
  assert.equal(f.element('modelServiceRetryAttempts').value, '1');
  assert.equal(f.element('modelServiceRetryBackoff').value, '0');
  await f.submit();
  let body = JSON.parse(f.requests.find(request => request.method === 'PUT').body);
  assert.equal(body.retry_attempts, 1);
  assert.equal(body.retry_backoff_seconds, 0);
  f.element('modelServiceRetryAttempts').value = '4';
  f.element('modelServiceRetryBackoff').value = '0.5';
  await f.submit();
  body = JSON.parse(f.requests.filter(request => request.method === 'PUT').at(-1).body);
  assert.equal(body.retry_attempts, 4);
  assert.equal(body.retry_backoff_seconds, 0.5);
});

test('creating or editing a generative model cannot select the Studio decision compiler', async () => {
  for (const config of [undefined, {model:'chat', base_url:'https://chat.test/v1'}]) {
    const f = await editor(config);
    assert.doesNotMatch(f.element('modelServiceEditor').markup, /modelServiceDecisionCompiler|用于解析决策/);
    await f.submit();
    const request = f.requests.find(item => item.method === (config ? 'PUT' : 'POST'));
    assert.equal(Object.hasOwn(JSON.parse(request.body), 'decision_compiler'), false);
  }
});
