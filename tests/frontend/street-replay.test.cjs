const {test} = require('node:test');
const assert = require('node:assert/strict');
const {GAReplayPlayer} = require('../../src/generative_agents/adapters/web/static/replay/replay-player.js');

test('queued 3D Runs open no UE session until the first committed frame, then open once through ordinary polling',async()=>{
  const opens=[], frames=[], nodes=[];
  const element=()=>{
    const node={append(){},remove(){},replaceChildren(){},setAttribute(){}};
    nodes.push(node);
    return node;
  };
  class Viewer {
    setWorld(_world,{sessionBase}){opens.push(sessionBase);}
    setReplayCamera(){}
    cancelReplayFrame(){}
    async presentReplayStep(step,_ms,publish){frames.push(step);publish();return true;}
    leaveScene(){}
    destroy(){}
  }
  const win={GAReplayPlayer,SceneMapViewer:Viewer,GAStreetCamera:{overview:()=>null}};
  const source=require('node:fs').readFileSync(require.resolve('../../src/generative_agents/adapters/web/static/replay/street-replay.js'),'utf8');
  require('node:vm').runInNewContext(source,{window:win,document:{createElement:element}});
  let available=0;
  const manifest={schema_version:2,run_id:'queued',agents:[],available_step:0,world:{definition:{simworld_scene_id:'map-15'}}};
  const player=new win.GAStreetReplayPlayer({canvas:{},container:{classList:{add(){},remove(){}},prepend(){}},
    isTimelineVisible:()=>true,fetchImpl:async url=>Response.json(url.endsWith('/manifest')?manifest:
      url.endsWith('/availability')?{run_id:'queued',available_step:available}:{run_id:'queued',available_step:available,steps:[{step_no:1,agents:[]}]})});
  await player.loadRun('queued');
  assert.ok(nodes.some(node=>node.className==='street-replay-waiting' && /首个 Step 提交后/.test(node.textContent)));
  player.setSceneVisible(false);player.setSceneVisible(true);
  await player.refreshAvailable();
  assert.deepEqual(opens,[]);
  available=1;
  await player.refreshAvailable();
  await player.refreshAvailable();
  assert.deepEqual(opens,['/api/studio/simworld/runs/queued/sessions']);
  assert.deepEqual(frames,[1]);
  player.destroy();
});

test('3D replay opens the referenced online scene for this Run and follows committed poses across seeks', async () => {
  const views = [];
  class Viewer {
    constructor() { this.cameras = []; this.opens = []; this.frames=[]; views.push(this); }
    setWorld(world, options) { this.opens.push({world, options}); }
    setReplayCamera(pose) { this.cameras.push(pose); }
    setOverviewCamera(pose) { this.overview=pose; }
    cancelReplayFrame() {}
    async presentReplayStep(step,ms,onFirstFrame) { this.frames.push([step,ms]);onFirstFrame();return true; }
    leaveScene() { this.closed = true; }
    destroy() { this.destroyed = true; }
  }
  global.window = {GAReplayPlayer, SceneMapViewer:Viewer};
  require('../../src/generative_agents/adapters/web/static/replay/street-camera.js');
  require('../../src/generative_agents/adapters/web/static/replay/street-replay.js');
  global.document = {createElement:() => ({append(){}, remove(){}, replaceChildren(){},setAttribute(){}})};
  const manifest = {schema_version:2, run_id:'history', experiment_id:'experiment', available_step:2,
    world:{world_name:'街区', definition:{environment_type:'spatial_3d',simworld_scene_id:'map-15'}},
    agents:[{agent_key:'person', initial_coord:[80,90,0]}]};
  const steps = [1,2].map(n => ({step_no:n, agents:[{agent_key:'person', coord:[n,4,0], to_yaw_deg:n*10}]}));
  steps[0].agents[0].action = {kind:'MOVE', presentation_clip:'cycle'};
  steps[1].agents[0].action = {kind:'ACT', description:'计划下次骑车'};
  const canvas = {};
  const player = new window.GAStreetReplayPlayer({canvas,
    container:{classList:{add(){}, remove(){}}, prepend(){}}, isTimelineVisible:() => true,
    fetchImpl:async url => Response.json(url.endsWith('/manifest') ? manifest :
      {run_id:'history', available_step:2, result_version:2, steps})});
  try {
    await player.loadRun('history');
    const view = views[0];
    assert.equal(view.opens[0].options.sessionBase,'/api/studio/simworld/runs/history/sessions');
    assert.equal(view.opens[0].world.definition.simworld_scene_id,'map-15');
    assert.equal(view.opens[0].options.replay.runId,'history');
    assert.deepEqual(view.frames,[[1,0]]);
    let facts = [];
    player.factList = {replaceChildren(){facts=[];}, append(row){facts.push(row.textContent);}};
    player.renderFacts();
    assert.ok(facts.some(row=>row.includes('已提交动画 cycle')));
    assert.ok(view.overview.position[2]>=4-1e-8);
    assert.equal(player._playbackDelay(),0);
    player.setSpeed(16);
    await player.play();
    assert.deepEqual(view.frames,[[1,0],[1,600]]);
    player.pause();
    await player.play(); // Pause at Step 1's endpoint resumes with Step 2.
    await new Promise(resolve=>setTimeout(resolve,110));
    assert.deepEqual(view.frames,[[1,0],[1,600],[2,600]]);
    player.renderFacts();
    assert.ok(facts.every(row=>!row.includes('已提交动画')));
    await player.seek(1);
    player.followAgent('person');
    const firstFollow=view.cameras.at(-1);
    assert.ok(firstFollow.position[2]>=4-1e-8);
    await player.seek(2);
    assert.deepEqual(view.frames.at(-1),[2,0]);
    assert.equal(view.cameras.at(-1).yaw_deg,firstFollow.yaw_deg);
    player.setSceneVisible(false);
    assert.equal(view.closed, true);
    player.setSceneVisible(true);
    await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual(view.cameras.at(-1),window.GAStreetCamera.follow(manifest.world.definition,steps[1],'person',firstFollow.yaw_deg));
    player.followAgent(null);
    assert.equal(view.cameras.at(-1), null);
    const requests=[];
    view.presentReplayStep=(step,ms,publish)=>new Promise(resolve=>{requests.push({step,ms,publish,resolve});});
    player.setSpeed(16);
    const playing=player.play();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(requests[0].step,1);
    assert.ok(requests[0].ms>0);
    requests[0].publish();requests[0].resolve(true);
    await playing;
    await new Promise(resolve=>setTimeout(resolve,110));
    assert.deepEqual(requests.map(r=>r.step),[1,2]);
    assert.ok(requests[1].ms>0);
    requests[1].publish();
    await new Promise(resolve=>setTimeout(resolve,100));
    assert.ok(player.timer);
    player.pause();
    await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual([requests[2].step,requests[2].ms],[2,0]);
    requests[2].publish();requests[2].resolve(true);requests[1].resolve(false);
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(player.timer,null);
    view.presentReplayStep=Viewer.prototype.presentReplayStep;
    await player.seek(1);
    player.availableStep=1;
    view.frames=[];
    await player.play();
    assert.deepEqual(view.frames,[[1,600]]);
    assert.equal(player.timer,null);
    await player.play();
    assert.deepEqual(view.frames,[[1,600],[1,0],[1,600]]);
    assert.equal(player.timer,null);
    player.destroy();
    assert.equal(view.destroyed, true);
    assert.equal(canvas.hidden, false);
  } finally {
    delete global.document;
    delete global.window;
  }
});
