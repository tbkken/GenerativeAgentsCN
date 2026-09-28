const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/generative_agents/adapters/web/static/shell/console-api.js', 'utf8');
const queueCode = source.slice(source.indexOf('  function enqueueDraftMutation('), source.indexOf('  async function acceptSavedDraft('));
const saveCode = source.slice(source.indexOf('  function saveDraft(options'), source.indexOf("  $('applyExperimentModelChoices').addEventListener"));
const launchCode = source.slice(source.indexOf('  async function publishAndRun()'), source.indexOf('  async function prepareNextSimulation()'));
const prepareCode = source.slice(source.indexOf('  async function openPublishModal()'), source.indexOf('  async function openResumeRunModal('));

function setup() {
  const calls = [];
  const c = {
    state:{selectedExperimentId:'experiment',experimentOpenGeneration:1,draft:{definition:{models:{chat:{model:'new-model'}}}},
      definition:{},draftMutation:Promise.resolve(),dirty:false,draftSaveFailure:null},
    markDirty:()=>{c.state.dirty=true;},
    saveDraftUnlocked:async()=>{calls.push('save'); throw Error('save rejected');},
    api:async url=>{calls.push(url); return {run_id:'run'};},
    $:()=>({}), closeModal:()=>{},goToPage:()=>{},showToast:()=>{},
    syncSelectedExperiment:async()=>{},loadRunHistory:async()=>{},
  };
  vm.createContext(c); vm.runInContext(queueCode+saveCode+launchCode+prepareCode,c);
  return {c,calls};
}

test('failed programmatic save stays dirty and blocks both preflight and launch until an explicit successful save',async()=>{
  const {c,calls}=setup();
  await assert.rejects(c.saveDraft(),/save rejected/);
  assert.equal(c.state.dirty,true);
  assert.equal(c.state.draft.definition.models.chat.model,'new-model');
  // Even an unrelated renderer clearing dirty must not erase the failure.
  c.state.dirty=false;
  await assert.rejects(c.openPublishModal(),/上次保存失败/);
  await assert.rejects(c.publishAndRun(),/上次保存失败/);
  assert.deepEqual(calls,['save']);
  c.saveDraftUnlocked=async()=>{calls.push('retry');c.state.dirty=false;return c.state.draft;};
  await c.saveDraft();
  assert.equal(c.state.draftSaveFailure,null);
  await c.publishAndRun();
  assert.deepEqual(calls,['save','retry','/experiments/experiment/seal','/experiments/experiment/runs']);
});

test('launch waits for a pending save and refuses to seal when that save fails',async()=>{
  const {c,calls}=setup();let reject;
  c.saveDraftUnlocked=()=>new Promise((_,fail)=>{reject=fail;});
  const saving=c.saveDraft();const savedResult=assert.rejects(saving,/pending failed/);
  await Promise.resolve();await Promise.resolve();
  const launching=c.publishAndRun();const launchResult=assert.rejects(launching,/上次保存失败/);
  reject(Error('pending failed'));
  await Promise.all([savedResult,launchResult]);
  assert.deepEqual(calls,[]);
});

test('a save failure does not poison a later queued retry',async()=>{
  const {c}=setup();let attempts=0;
  c.saveDraftUnlocked=async()=>{if(++attempts===1)throw Error('first failed');c.state.dirty=false;};
  const first=c.saveDraft();const second=c.saveDraft();
  await assert.rejects(first,/first failed/);await second;
  assert.equal(c.state.draftSaveFailure,null);
  await c.ensureDraftReadyForRun();
});

test('late failures and queued saves cannot affect a newly selected experiment',async()=>{
  const {c}=setup();let reject;
  c.saveDraftUnlocked=()=>new Promise((_,fail)=>{reject=fail;});
  const saving=c.saveDraft();const result=assert.rejects(saving,/old request failed/);
  await Promise.resolve();await Promise.resolve();
  const queued=c.saveDraft();const queuedResult=assert.rejects(queued,/实验已切换/);
  c.state.selectedExperimentId='other';c.state.experimentOpenGeneration++;
  c.state.dirty=false;c.state.draftSaveFailure=null;
  reject(Error('old request failed'));
  await Promise.all([result,queuedResult]);
  assert.equal(c.state.draftSaveFailure,null);
  assert.equal(c.state.dirty,false);
});

test('readiness includes a save added while an earlier save is pending',async()=>{
  const {c,calls}=setup();let release;
  c.saveDraftUnlocked=()=>new Promise(resolve=>{release=()=>{c.state.dirty=false;resolve();};});
  const first=c.saveDraft();await Promise.resolve();await Promise.resolve();
  const ready=c.ensureDraftReadyForRun();
  const second=c.saveDraft();const secondResult=assert.rejects(second,/second failed/);
  c.saveDraftUnlocked=async()=>{throw Error('second failed');};
  release();await first;
  await assert.rejects(ready,/上次保存失败/);await secondResult;
  assert.deepEqual(calls,[]);
});

test('launch rechecks saves queued during its own automatic save before sealing',async()=>{
  const {c,calls}=setup();let releaseAuto,rejectLater,started;
  const autoStarted=new Promise(resolve=>{started=resolve;});
  c.state.dirty=true;
  c.saveDraftUnlocked=()=>new Promise(resolve=>{
    releaseAuto=()=>{c.state.dirty=false;resolve();};started();
  });
  const launch=c.publishAndRun();const launchResult=assert.rejects(launch,/上次保存失败/);
  await autoStarted;
  let laterStarted;
  const laterPending=new Promise(resolve=>{laterStarted=resolve;});
  c.saveDraftUnlocked=()=>new Promise((_,reject)=>{rejectLater=reject;laterStarted();});
  const later=c.saveDraft();const laterResult=assert.rejects(later,/later save failed/);
  releaseAuto();await laterPending;
  assert.deepEqual(calls,[]);
  rejectLater(Error('later save failed'));
  await Promise.all([launchResult,laterResult]);
  assert.deepEqual(calls,[]);
});
