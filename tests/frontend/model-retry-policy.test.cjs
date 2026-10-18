const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function editor(config) {
  const elements = new Map(), requests = [];
  const item = config ? {id:'chat-model', name:'本地模型', row_version:2, config:{chat:config}} : null;
  function element(id) {
    if (!elements.has(id)) {
      const node = {value:'', checked:false, classList:{toggle() {}}, setAttribute() {}};
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
  const context = {Map, document:{getElementById:element, addEventListener() {}},
    window:{ResourceList:{route() {}}, scrollTo() {}},
    fetch: async (url, options = {}) => {
      requests.push({url, ...options});
      const saved = item || {id:'new-model', config:{chat:{}}};
      return {ok:true, status:200, json: async () => !options.method && url.endsWith('/model-services') ? {items:[saved]} : saved};
    }};
  vm.createContext(context);
  const source = fs.readFileSync('src/generative_agents/adapters/web/static/resources/model-workspace.js', 'utf8');
  vm.runInContext(source.replace('  window.ModelWorkspace =', '  window.openTestEditor = openEditor;\n  window.ModelWorkspace ='), context);
  await context.window.openTestEditor(item?.id || null, false);
  return {element, requests, submit: () => element('modelServiceForm').onsubmit({preventDefault() {}})};
}

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
