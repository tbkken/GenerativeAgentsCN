const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/generative_agents/adapters/web/static/shell/console-api.js', 'utf8');
const wrapper = source.slice(source.indexOf('  async function deleteCurrentRun('), source.indexOf('  async function batchArchiveSelected('));

function setup(failDelete = false) {
  const calls = [], messages = [], nodes = {};
  const state = {currentRun: {run_id: 'run'}, selectedRunId: 'run', selectedExperimentId: 'experiment'};
  const context = {state, $: id => nodes[id] ||= {}, confirmResourceDeletion: async () => true,
    resetResultRuntime() {calls.push('stop polls'); state.selectedRunId = null;},
    api: async () => {calls.push('delete'); if (failDelete) throw Error('occupied');},
    openExperiment: async () => {throw Error('refresh failed');}, loadExperiments: async () => {},
    loadResults: async id => {calls.push('reload ' + id);}, reportError() {},
    showToast: message => messages.push(message)};
  vm.createContext(context); vm.runInContext(wrapper, context);
  return {context, calls, messages};
}

test('polls stop before deletion and refresh failure does not turn success into delete failure', async () => {
  const {context, calls, messages} = setup();
  await context.deleteCurrentRun();
  assert.deepEqual(calls, ['stop polls', 'delete']);
  assert(messages.some(message => message.includes('删除已完成')));
});

test('failed deletion resumes the selected Run UI and propagates the diagnostic', async () => {
  const {context, calls} = setup(true);
  await assert.rejects(context.deleteCurrentRun(), /occupied/);
  assert.deepEqual(calls, ['stop polls', 'delete', 'reload run']);
  assert.equal(context.state.deletingRunId, null);
});
