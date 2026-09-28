const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/generative_agents/adapters/web/static/shell/console-api.js', 'utf8');
const code = source.slice(source.indexOf('  async function publishAndRun()'), source.indexOf('  async function prepareNextSimulation()'));
const readyCode = source.slice(source.indexOf('  async function ensureDraftReadyForRun()'), source.indexOf('  async function acceptSavedDraft('));
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
  vm.createContext(context); vm.runInContext(readyCode + code, context);
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

test('unchanged draft is sealed and launched without uploading its complete map again', async () => {
  const {context, requests} = setup();
  context.state.dirty = false;
  context.saveDraft = async () => { throw Error('unchanged draft was uploaded'); };
  await context.publishAndRun();
  assert.deepEqual(requests, ['/experiments/experiment/seal', '/experiments/experiment/runs']);
});

test('launch waits for pending saves and saves new form changes before sealing', async () => {
  const {context, requests} = setup();
  let release;
  context.state.draftMutation = new Promise(resolve => { release = resolve; });
  context.state.dirty = true;
  context.saveDraft = async () => { requests.push('save'); context.state.dirty = false; };
  const launch = context.publishAndRun();
  assert.deepEqual(requests, []);
  release();
  await launch;
  assert.deepEqual(requests, ['save', '/experiments/experiment/seal', '/experiments/experiment/runs']);
});

for (const dirty of [false, true]) {
  test(`execution preparation validates the package and only saves modified drafts (dirty=${dirty})`, async () => {
    const calls = [];
    const elements = new Map();
    const estimate = {scale:{execution_mode:'SKILL_BRAIN',agents:4,steps:12,brain_skill:'campus'},estimate:{}};
    const context = {
      state: {draft:{}, dirty, selectedExperimentId:'experiment', draftMutation:Promise.resolve(),
        definition:{agents:[{enabled:true}],models:{chat:{model:'chat'},embedding:{model:'embedding'}},world:{world_name:'campus'}}},
      $: id => { if(!elements.has(id)) elements.set(id,{}); return elements.get(id); },
      saveDraft: async () => { calls.push('save'); context.state.dirty = false; },
      refreshValidation: async () => { calls.push('validate'); return {valid:true}; },
      api: async () => { calls.push('estimate'); return estimate; },
      openModal: () => {}, renderPublishValidation: () => {},
      formatRange: () => '', formatSeconds: () => '', formatBytes: () => '',
    };
    vm.createContext(context);
    vm.runInContext(readyCode + source.slice(source.indexOf('  async function openPublishModal()'), source.indexOf('  async function openResumeRunModal(')),context);
    await context.openPublishModal();
    assert.deepEqual(calls, dirty ? ['save','validate','estimate'] : ['validate','estimate']);
    assert.equal(elements.get('publishLaunchStatus').textContent, '检查通过，等待确认执行。');
  });
}
