const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {GAReplayPlayer} = require('../../src/generative_agents/adapters/web/static/replay/replay-player.js');

test('agent display size keeps legacy defaults and honors bounded author configuration', () => {
  assert.equal(GAReplayPlayer.agentDisplaySize({spatial: true, worldScale: 32}), 52);
  assert.equal(GAReplayPlayer.agentDisplaySize({spatial: false, worldScale: 32}), 32);
  assert.equal(GAReplayPlayer.agentDisplaySize({spatial: true, worldScale: 32, spriteDisplayTiles: 2}), 64);
  assert.equal(GAReplayPlayer.agentDisplaySize({spatial: true, worldScale: 32, spriteDisplayTiles: 99}), 192);
  assert.equal(GAReplayPlayer.agentDisplaySize({spatial: true, worldScale: 32, spriteDisplayTiles: 0.1}), 16);
});

test('availability polling keeps world assets and invalidates the growing tail', async () => {
  const urls = [];
  const player = new GAReplayPlayer({fetchImpl: async url => {
    urls.push(url);
    return {ok: true, json: async () => ({run_id: 'run', available_step: 2, partial: true})};
  }});
  player.runId = 'run'; player.availableStep = 1; player.currentStep = 1;
  player.manifest = {world: {render_asset: {renderer: 'SPATIAL_GRID'}}, agents: []};
  await player.refreshAvailable();
  assert.equal(urls.length, 0, 'initialization must own the first manifest');
  player.ready = true;
  player.windows.set(1, {steps: [{step_no: 1}]});
  await player.refreshAvailable();
  assert.equal(urls[0], '/api/studio/runs/run/replay/availability');
  assert.equal(player.availableStep, 2);
  assert.equal(player.manifest.world.render_asset.renderer, 'SPATIAL_GRID');
  assert.equal(player.windows.has(1), false);
});

test('scene exceptions reject startup instead of leaving it pending', async () => {
  const player = new GAReplayPlayer();
  global.Phaser = {Game: class {
    constructor(config) {
      this.events = {once() {}};
      queueMicrotask(() => config.scene.create.call({}));
    }
  }};
  try {
    await assert.rejects(player._withStartup((resolve, reject) => {
      player._bootScene({scene: {create() {throw Error('bad texture');}}}, reject);
    }), /bad texture/);
  } finally {delete global.Phaser;}
});

test('overlapping initialization coalesces and a failed initialization can retry', async () => {
  const source = fs.readFileSync('src/generative_agents/adapters/web/static/shell/console-api.js', 'utf8');
  const wrapper = source.slice(source.indexOf('  async function ensureReplayPlayer('), source.indexOf('  async function ensureReplayPlayerUnlocked('));
  let reject, calls = 0;
  const nodes = {};
  const context = {state: {selectedRunId: 'run', resultGeneration: 1},
    $: id => nodes[id] ||= {}, teardownReplay() {}, syncReplayControls() {},
    ensureReplayPlayerUnlocked: () => {calls++; return new Promise((_, r) => reject = r);}};
  vm.createContext(context); vm.runInContext(wrapper, context);
  const first = context.ensureReplayPlayer('run', 1);
  const second = context.ensureReplayPlayer('run', 1);
  reject(Error('asset missing'));
  await Promise.all([assert.rejects(first), assert.rejects(second)]);
  assert.equal(calls, 1);
  assert.match(nodes.replayStatus.textContent, /asset missing/);
  const retry = context.ensureReplayPlayer('run', 1);
  reject(Error('retry'));
  await assert.rejects(retry);
  assert.equal(calls, 2);
});


test('first available frame initializes the player before next-step navigation', async () => {
  const player = new GAReplayPlayer({fetchImpl:async()=>({ok:true,json:async()=>({run_id:'run',available_step:2})})});
  player.runId='run'; player.ready=true; player.availableStep=0; player.manifest={};
  const targets=[]; player.seek=async target=>{targets.push(target);player.currentStep=target};
  await player.refreshAvailable(); await player.stepBy(1);
  assert.deepEqual(targets,[1,2]);
});

const replayStep = number => ({step_no: number, agents: [
  {agent_key: 'a', coord: [number, 0]}, {agent_key: 'b', coord: [number, 1]},
]});

const deferred = () => {
  let resolve;
  const promise = new Promise(complete => {resolve = complete;});
  return {promise, resolve};
};

test('selecting an agent keeps the displayed facts after a running tail is invalidated', async () => {
  const requests = [], selections = [];
  const player = new GAReplayPlayer({
    onAgent: payload => selections.push(payload),
    fetchImpl: async url => {
      requests.push(url);
      return {ok: true, json: async () => url.endsWith('/availability')
        ? {run_id: 'run', available_step: 6}
        : {run_id: 'run', available_step: 5, steps: [replayStep(5)]}};
    },
  });
  player.runId = 'run'; player.ready = true; player.availableStep = 5; player.manifest = {};
  await player.seek(5);
  await player.refreshAvailable();
  assert.equal(player.windows.has(1), false);

  player.selectAgent('a');
  assert.equal(selections.at(-1).step.step_no, 5);
  assert.equal(selections.at(-1).fact.agent_key, 'a');
  player.selectAgent('b');
  assert.equal(selections.at(-1).fact.agent_key, 'b');
  assert.deepEqual(selections.at(-1).fact.coord, [5, 1]);
  player.selectAgent(null);
  assert.equal(selections.at(-1).fact, null);
  assert.equal(requests.length, 2, 'already displayed immutable facts do not need another request');
});

test('an older seek response cannot replace the displayed facts or the selected agent', async () => {
  const older = deferred(), newer = deferred(), selections = [];
  let calls = 0;
  const player = new GAReplayPlayer({
    onAgent: payload => selections.push(payload),
    fetchImpl: () => (++calls === 1 ? older : newer).promise,
  });
  player.runId = 'run'; player.availableStep = 3;
  player._renderStep = function () {this.selectAgent(this.selectedAgentKey);};
  const first = player.seek(2);
  player.selectAgent('a');
  const second = player.seek(3);
  player.selectAgent('b');
  newer.resolve({ok: true, json: async () => ({run_id: 'run', available_step: 3, steps: [replayStep(3)]})});
  await second;
  older.resolve({ok: true, json: async () => ({run_id: 'run', available_step: 3, steps: [replayStep(2)]})});
  assert.equal(await first, null);
  player.selectAgent('b');

  assert.equal(player.currentStep, 3);
  assert.equal(selections.at(-1).step.step_no, 3);
  assert.equal(selections.at(-1).fact.agent_key, 'b');
  assert.deepEqual(selections.at(-1).fact.coord, [3, 1]);
});

test('destroy and a new Run isolate displayed facts from the old Run and generation', async () => {
  const oldRequest = deferred(), selections = [];
  const player = new GAReplayPlayer({onAgent: payload => selections.push(payload),
    fetchImpl: url => url.includes('/old/') ? oldRequest.promise : Promise.resolve({
      ok: true, json: async () => ({run_id: 'new', available_step: 1, steps: [replayStep(1)]}),
    }),
  });
  player.runId = 'old'; player.availableStep = 2;
  player.windows.set(1, [replayStep(1)]);
  await player.seek(1);
  const pending = player.seek(2);
  player.destroy();
  player.selectAgent('a');
  assert.equal(selections.at(-1).fact, null);
  player.runId = 'new'; player.generation += 1; player.availableStep = 1;
  await player.seek(1);
  oldRequest.resolve({ok: true, json: async () => ({run_id: 'old', available_step: 2, steps: [replayStep(2)]})});
  await assert.rejects(pending, {name: 'AbortError'});
  player.windows.clear();
  player.selectAgent('b');

  assert.equal(selections.at(-1).step.step_no, 1);
  assert.deepEqual(selections.at(-1).fact.coord, [1, 1]);
  assert.equal(player.runId, 'new');
});

test('selection stays on the displayed step while a new seek is pending', async () => {
  const next = deferred(), selections = [];
  const player = new GAReplayPlayer({onAgent: payload => selections.push(payload), fetchImpl: () => next.promise});
  player.runId = 'run'; player.availableStep = 2;
  player.windows.set(1, [replayStep(1)]);
  player._renderStep = function () {this.selectAgent(this.selectedAgentKey);};
  await player.seek(1);
  player.windows.clear();
  const seeking = player.seek(2);
  player.selectAgent('b');
  assert.equal(selections.at(-1).step.step_no, 1);
  assert.deepEqual(selections.at(-1).fact.coord, [1, 1]);
  next.resolve({ok: true, json: async () => ({run_id: 'run', available_step: 2, steps: [replayStep(2)]})});
  await seeking;
  assert.equal(selections.at(-1).step.step_no, 2);
  assert.equal(selections.at(-1).fact.agent_key, 'b');
});

test('availability shrink does not relabel an old displayed fact as another step', async () => {
  const selections = [];
  const player = new GAReplayPlayer({onAgent: payload => selections.push(payload), fetchImpl: async () => ({
    ok: true, json: async () => ({run_id: 'run', available_step: 1}),
  })});
  player.runId = 'run'; player.ready = true; player.availableStep = 2; player.manifest = {};
  player.windows.set(1, [replayStep(2)]);
  await player.seek(2);
  await player.refreshAvailable();
  player.selectAgent('a');
  assert.equal(player.currentStep, 1);
  assert.equal(selections.at(-1).step, null);
  assert.equal(selections.at(-1).fact, null);
});
