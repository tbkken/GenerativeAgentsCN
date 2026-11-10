const {test} = require('node:test');
const assert = require('node:assert/strict');
const exchange = require('../../src/generative_agents/adapters/web/static/shell/experiment-exchange.js');

function browser() {
  const nodes = new Map(), requests = [], downloads = [], operations = [], imported = [], revoked = [], timers = [];
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {id, hidden:false, disabled:false, open:false, value:'', files:[], textContent:'', listeners:{}, attributes:{},
      addEventListener(type, fn) {this.listeners[type] = fn;},
      setAttribute(name, value) {this.attributes[name] = value;},
      removeAttribute(name) {delete this.attributes[name]; delete this[name];},
      showModal() {this.open = true;},
      close() {this.open = false; this.listeners.close?.();},
    });
    return nodes.get(id);
  }
  const win = {
    document:{getElementById:node, body:{appendChild() {}}, createElement() {
      return {href:'', download:'', click() {downloads.push({href:this.href, download:this.download});}, remove() {}};
    }},
    fetch(url, options) {return new Promise((resolve, reject) => requests.push({url, options, resolve, reject}));},
    FormData: class {constructor() {this.fields = [];} append(...values) {this.fields.push(values);}},
    AbortController,
    URL:{createObjectURL:() => 'blob:download', revokeObjectURL:url => revoked.push(url)},
    setTimeout(fn) {timers.push(fn);},
  };
  const api = exchange.mount(win, {
    getExperiment:() => ({id:'current', name:'当前实验'}),
    onOperation:(...args) => operations.push(args),
    onImported:result => imported.push(result),
  });
  const submit = () => node('experimentExchangeSubmit').listeners.click();
  return {api, node, requests, downloads, operations, imported, revoked, timers, submit};
}
const reply = data => ({ok:true, json:async () => data});
const download = (filename = 'experiment.zip') => ({ok:true, blob:async () => ({}), headers:{get:() => `attachment; filename="${filename}"`}});

test('experiment export includes all runs by default and can export only the experiment', async () => {
  const b = browser();
  b.api.openExport({id:'experiment-1', name:'测试实验', dirty:true});
  assert.equal(b.node('experimentExchangeIncludeRuns').checked, true);
  assert.match(b.node('experimentExchangeStatus').textContent, /未保存/);
  const first = b.submit();
  await b.submit();
  assert.equal(b.requests.length, 1);
  assert.equal(b.requests[0].url, '/api/studio/experiments/experiment-1/export?include_runs=true');
  assert.equal(b.node('experimentExchangeIncludeRuns').disabled, true);
  b.requests[0].resolve(download());
  await first;
  assert.equal(b.downloads[0].download, 'experiment.zip');
  assert.equal(b.node('experimentExchangeSubmit').disabled, false);
  b.node('experimentExchangeIncludeRuns').checked = false;
  const second = b.submit();
  assert.equal(b.requests[1].url, '/api/studio/experiments/experiment-1/export?include_runs=false');
  b.requests[1].resolve(download('experiment.gaexp'));
  await second;
  assert.equal(b.downloads[1].download, 'experiment.gaexp');
  assert.equal(b.revoked.length, 0);
  b.timers.forEach(fn => fn());
  assert.deepEqual(b.revoked, ['blob:download', 'blob:download']);
});

test('an active-run refusal remains visible and allows retry without starting a download', async () => {
  const b = browser();
  b.api.openExport({id:'experiment-1'});
  const request = b.submit();
  b.requests[0].resolve({ok:false, status:409, json:async () => ({detail:'请先暂停正在执行的运行后再导出'})});
  await request;
  assert.match(b.node('experimentExchangeStatus').textContent, /先暂停/);
  assert.equal(b.node('experimentExchangeStatus').attributes.role, 'alert');
  assert.equal(b.node('experimentExchangeSubmit').disabled, false);
  assert.equal(b.downloads.length, 0);
  assert.equal(b.operations[0][2], 'error');
});

test('switching experiments prevents a late export from downloading or replacing the next dialog', async () => {
  const b = browser();
  b.api.setContext('overview', 'experiment-1');
  b.api.openExport({id:'experiment-1'});
  const first = b.submit();
  b.api.setContext('overview', 'experiment-2');
  assert.equal(b.requests[0].options.signal.aborted, true);
  b.api.openExport({id:'experiment-2', name:'第二个实验'});
  b.requests[0].resolve(download());
  await first;
  assert.equal(b.downloads.length, 0);
  assert.equal(b.node('experimentExchangeName').textContent, '第二个实验');
  assert.equal(b.node('experimentExchangeStatus').textContent, '');
});

test('import preserves package selection, prevents double submission and provides an explicit experiment link', async () => {
  const b = browser();
  b.api.openImport();
  assert.equal(b.node('experimentExchangeSubmit').disabled, true);
  const file = {name:'completed-experiment.zip'};
  b.node('experimentExchangeFile').files = [file];
  b.node('experimentExchangeFile').listeners.change();
  assert.equal(b.node('experimentExchangeSubmit').disabled, false);
  const pending = b.submit();
  await b.submit();
  assert.equal(b.requests.length, 1);
  assert.equal(b.requests[0].url, '/api/studio/experiment-import');
  assert.equal(b.requests[0].options.method, 'POST');
  assert.deepEqual(b.requests[0].options.body.fields, [['file', file]]);
  assert.equal(b.node('experimentExchangeFile').disabled, true);
  const result = {experiment_id:'restored', run_ids:['run-1', 'run-2'], imported_count:3, existing_count:0};
  b.requests[0].resolve(reply(result));
  await pending;
  assert.deepEqual(b.imported, [result]);
  assert.match(b.node('experimentExchangeStatus').textContent, /2 次运行记录/);
  assert.equal(b.node('experimentExchangeOpen').href, '/experiments/restored');
  assert.equal(b.node('experimentExchangeOpen').hidden, false);
  assert.equal(b.node('experimentExchangeSubmit').disabled, true);
  assert.equal(b.node('experimentExchangeFile').disabled, false);
});

test('a late import outcome is retained in history without overwriting a new dialog', async () => {
  const b = browser();
  b.api.setContext('experiments', null);
  b.api.openImport();
  b.node('experimentExchangeFile').files = [{name:'one.garun'}];
  const pending = b.submit();
  b.node('experimentExchangeClose').listeners.click();
  b.api.openExport({id:'second', name:'当前查看的实验'});
  const result = {experiment_id:'restored', run_ids:['run-1'], imported_count:2, existing_count:0};
  b.requests[0].resolve(reply(result));
  await pending;
  assert.equal(b.operations[0][0], '实验导入完成');
  assert.deepEqual(b.imported, [result]);
  assert.equal(b.node('experimentExchangeTitle').textContent, '导出实验资源包');
  assert.equal(b.node('experimentExchangeName').textContent, '当前查看的实验');
  assert.equal(b.node('experimentExchangeOpen').hidden, true);
  assert.equal(b.node('experimentExchangeStatus').textContent, '');
  assert.equal(b.requests[0].options.signal, undefined);
});

test('conflicting imports show the server diagnosis and can be retried with another package', async () => {
  const b = browser();
  b.api.openImport();
  b.node('experimentExchangeFile').files = [{name:'one.gaexp'}];
  const pending = b.submit();
  b.requests[0].resolve({ok:false, status:409, json:async () => ({detail:'同一实验身份的内容不同，不能覆盖'})});
  await pending;
  assert.match(b.node('experimentExchangeStatus').textContent, /不能覆盖/);
  assert.equal(b.node('experimentExchangeStatus').attributes.role, 'alert');
  assert.equal(b.node('experimentExchangeSubmit').disabled, false);
  assert.equal(b.imported.length, 0);
});

test('download names handle UTF-8 headers and discard directory components', () => {
  assert.equal(exchange.downloadName("attachment; filename*=UTF-8''%E5%AE%9E%E9%AA%8C.zip", 'fallback.zip'), '实验.zip');
  assert.equal(exchange.downloadName('attachment; filename="../../experiment.gaexp"', 'fallback.zip'), 'experiment.gaexp');
  assert.equal(exchange.downloadName("attachment; filename*=UTF-8''%E5broken; filename=valid.zip", 'fallback.zip'), 'valid.zip');
  assert.equal(exchange.downloadName(null, 'fallback.zip'), 'fallback.zip');
});
