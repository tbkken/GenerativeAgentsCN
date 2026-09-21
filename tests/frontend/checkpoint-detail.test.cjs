const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/generative_agents/adapters/web/static/shell/console-api.js', 'utf8');
const functionSource = source.slice(source.indexOf('  async function showCheckpointDetail('), source.indexOf('  async function loadCheckpointPreview('));

test('background checkpoint polls do not discard an in-flight detail request', async () => {
  const nodes = {};
  let resolve;
  const context = {state: {selectedRunId: 'run', resultGeneration: 1, checkpointGeneration: 1},
    $: id => nodes[id] ||= {scrollIntoView() {}}, escapeHtml: String,
    api: () => new Promise(r => resolve = r)};
  vm.createContext(context); vm.runInContext(functionSource, context);
  const pending = context.showCheckpointDetail('run', 2);
  assert.match(nodes.checkpointDetailGrid.textContent, /正在读取/);
  context.state.checkpointGeneration++;
  resolve({run_id: 'run', step_no: 2, resumable: true, status: 'VALID', files: [],
    agent_state: {items: []}, conversations: {items: []}, storage: {groups: []}});
  await pending;
  assert.match(nodes.checkpointDetailGrid.innerHTML, /data-checkpoint-resume/);
  assert.equal(context.state.selectedCheckpointDetail.step_no, 2);
});

test('detail failures are visible in the detail panel', async () => {
  const nodes = {};
  const context = {state: {selectedRunId: 'run', resultGeneration: 1},
    $: id => nodes[id] ||= {scrollIntoView() {}},
    api: async () => {throw Error('snapshot unavailable');}};
  vm.createContext(context); vm.runInContext(functionSource, context);
  await assert.rejects(context.showCheckpointDetail('run', 2));
  assert.match(nodes.checkpointDetailGrid.textContent, /snapshot unavailable/);
});
