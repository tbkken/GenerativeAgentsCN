const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('generative_agents/web/static/console-api.js', 'utf8');
const code = source.slice(source.indexOf('  async function publishAndRun()'), source.indexOf('  async function prepareNextSimulation()'));
function setup(startFails = false) {
  const requests = [], notices = [];
  const context = {
    state: { draft: {}, selectedExperimentId: 'experiment' },
    saveDraft: async () => {},
    api: async url => { requests.push(url); if (url.endsWith('/runs')) { if(startFails) throw new Error('launch rejected'); return {run_id: 'started-run'}; } },
    closeModal: () => {}, goToPage: () => {},
    syncSelectedExperiment: async () => { throw new Error('refresh unavailable'); },
    loadRunHistory: async () => {},
    showToast: message => notices.push(message),
  };
  vm.createContext(context); vm.runInContext(code, context);
  return { context, requests, notices };
}
test('a refresh failure retains successful launch identity and never repeats launch', async () => {
  const {context,requests,notices}=setup();
  await context.publishAndRun();
  assert.equal(context.state.selectedRunId, 'started-run');
  assert.equal(requests.filter(url=>url.endsWith('/runs')).length, 1);
  assert.ok(notices.some(message=>message.includes('started-run') && message.includes('状态刷新失败')));
});
test('a failed launch still propagates its error', async () => {
  const {context}=setup(true);
  await assert.rejects(context.publishAndRun(), /launch rejected/);
  assert.equal(context.state.selectedRunId, undefined);
});

test('sealed experiment starts a new Run without saving, resealing, or resuming the old Run', async () => {
  const {context,requests}=setup();context.state.draft=null;
  context.state.selectedRunId='old-failed-run';
  context.saveDraft=async()=>{throw Error('sealed package saved')};
  await context.publishAndRun();
  assert.deepEqual(requests,['/experiments/experiment/runs']);
  assert.equal(context.state.selectedRunId,'started-run');
});

test('duplicate confirm clicks share the in-flight launch guard', async()=>{
  const {context,requests}=setup();context.state.draft=null;
  let release;context.api=async url=>{requests.push(url);return new Promise(resolve=>release=resolve)};
  const first=context.publishAndRun();await context.publishAndRun();
  assert.equal(requests.length,1);release({run_id:'new-run'});await first;
  assert.equal(context.state.launchingRun,false);
});
