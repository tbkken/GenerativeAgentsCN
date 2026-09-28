const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const base = 'src/generative_agents/adapters/web/static/';
const source = fs.readFileSync(base + 'shell/console-api.js', 'utf8');
const cut = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const run = (text, context) => {vm.createContext(context); vm.runInContext(text, context); return context;};

test('initial shell does not fetch optional editors or Phaser; loader coalesces shared dependencies', async () => {
  const html = fs.readFileSync(base + 'shell/experiment-console.html', 'utf8');
  assert.doesNotMatch(html, /<script[^>]+(?:phaser|replay-player|map-editor-v2|map-workspace|skill-workspace|model-workspace|crowd-workspace)\.js/);
  const scripts = [], links = [];
  const context = run(fs.readFileSync(base + 'shell/workspace-loader.js', 'utf8'), {
    window: {}, document: {
      createElement: type => ({type, dataset:{}, remove(){}}),
      querySelector: selector => links.find(link => selector.includes(link.dataset.workspaceStyle)),
      head: {appendChild(node) {if(node.type === 'script') {scripts.push(node.src); queueMicrotask(() => node.onload());} else links.push(node);}},
    },
  });
  assert.equal(scripts.length, 0);
  await Promise.all([context.window.WorkspaceLoader.load('map-editor'), context.window.WorkspaceLoader.load('replay'), context.window.WorkspaceLoader.load('replay')]);
  assert.equal(scripts.filter(path => path.endsWith('render-materials.js')).length, 1);
  assert.equal(scripts.filter(path => path.endsWith('phaser.min.js')).length, 1);
  assert.ok(scripts.indexOf('/static/console/vendor/phaser.min.js') < scripts.indexOf('/static/console/replay/replay-player.js'));
});

test('material closure includes visible tiles, state appearances and nested canvas sources but skips unused library assets', () => {
  const {window} = run(fs.readFileSync(base+'resources/render-materials.js', 'utf8'), {window:{}});
  const document = {
    material_slices: [
      {id:'background',source_id:'bg'}, {id:'state',source_id:'state-image'}, {id:'canvas',source_id:'canvas-source'},
      {id:'nested',source_id:'nested-image'}, {id:'tile',source_id:'tileset',indexed_gid:4}, {id:'unused',source_id:'unused-image',indexed_gid:5},
    ],
    hierarchy_nodes: [{material_slice_id:'background',state_appearance:{cases:[{material_slice_id:'state'}]}}],
    material_canvases:[{source_id:'canvas-source',cells:{0:[{slice_id:'nested'},{slice_id:'canvas'}]}}],
    visual_layers:[{visible:true,raw_gids:[0,4,0x80000004]},{visible:false,raw_gids:[5]}],
    tile_override_layers:{0:[{slice_id:'canvas'}]},
  };
  assert.deepEqual([...window.RenderMaterials.sourceIds(document)].sort(), ['bg','canvas-source','nested-image','state-image','tileset']);
  assert.ok(window.RenderMaterials.sourceIds(document,['unused-image']).has('unused-image'));
});

function resultFixture(tab) {
  const requests = [], renders = [];
  const context = run(cut('  async function refreshResultDataUnlocked(', '  function resultPageControls('), {
    state: {selectedRunId:'run', resultGeneration:1, runHistory:[], resultTab:tab, operationTab:'traces'},
    document:{visibilityState:'visible'}, $:()=>({}),
    api:async path => {requests.push(path); return path === '/runs/run' ? {run_id:'run',status:'COMPLETED',committed_step:8} : {items:[],steps:[],artifacts:[],issues:[]};},
    renderRunSelect(){}, renderRunActions(){}, startResultDurationTimer(){}, renderTimeline(){renders.push('timeline');},
    renderAgents(){renders.push('agents');}, renderRunQuality(){renders.push('quality');}, renderOperations(){renders.push('artifacts');},
    ensureReplayPlayer:async () => renders.push('replay'), loadOperationsWorkspace:async () => renders.push('operations'),
    resultPageControls(){}, syncWorkspaceUrl(){},
  });
  return {context, requests, renders};
}
for (const [tab, endpoint] of [['timeline','timeline'],['agents','agents'],['quality','quality'],['artifacts','operations?section=artifacts']]) {
  test(`results ${tab} loads only the selected panel and a lightweight Run status`, async () => {
    const f = resultFixture(tab);
    await f.context.refreshResultDataUnlocked('run',1);
    assert.equal(f.requests.length,2);
    assert.equal(f.requests[0],'/runs/run');
    assert.ok(f.requests[1].includes(endpoint));
    if (tab !== 'artifacts') {
      await f.context.refreshResultDataUnlocked('run',1);
      assert.equal(f.requests.length,3,'unchanged commit should only request Run status');
    }
    f.context.document.visibilityState='hidden';
    const count=f.requests.length;
    await f.context.refreshResultDataUnlocked('run',1);
    assert.equal(f.requests.length,count);
  });
}

test('detail polling stops for terminal Runs and hidden tabs, but tracks a queued artifact job', () => {
  const context=run(cut('  function needsDetailPoll(', '  function scheduleDetailPoll('),{
    state:{bootstrapped:true,workspacePage:'results',currentRun:{status:'RUNNING'}},document:{visibilityState:'visible'},
  });
  assert.equal(context.needsDetailPoll(),true);
  for(const status of ['COMPLETED','FAILED','CANCELLED','PAUSED']) {context.state.currentRun.status=status;assert.equal(context.needsDetailPoll(),false);}
  context.state.activeArtifactJobs=true;
  assert.equal(context.needsDetailPoll(),true);
  context.document.visibilityState='hidden';
  assert.equal(context.needsDetailPoll(),false);
});

test('sparse experiment saves preserve loaded sections, long operations poll their own status and ordinary saves stay synchronous', async () => {
  const requests=[];
  const context=run(cut('  async function api(', '  function formatTime('),{
    state:{definition:{world:{name:'map'},agents:[{agent_key:'a'}],simulation:{steps:3}}},
    setTimeout: callback => {queueMicrotask(callback);return 1;},clearTimeout(){},
    fetch:async (path,options) => {requests.push({path,options});
      if(path === '/api/studio/experiments/exp/validate') return {ok:true,status:202,json:async()=>({operation_id:'op',status_url:'/api/studio/operations/op'})};
      if(path === '/api/studio/operations/op') return {ok:true,status:200,json:async()=>({status:'SUCCEEDED',result:{valid:true}})};
      return {ok:true,status:200,json:async()=>({id:'exp',definition:{simulation:{steps:5}}})};
    },
  });
  const saved=await context.api('/experiments/exp',{method:'PATCH',body:JSON.stringify({sections:{simulation:{steps:5}}})});
  assert.equal(saved.definition.world.name,'map');assert.equal(saved.definition.agents.length,1);assert.equal(saved.definition.simulation.steps,5);
  assert.equal(requests[0].options.headers.Prefer,undefined);
  const checked=await context.api('/experiments/exp/validate',{method:'POST',body:'{}'});
  assert.equal(checked.valid,true);
  assert.equal(requests[1].options.headers.Prefer,'respond-async');
  assert.equal(requests[1].options.headers['Content-Type'],'application/json');
  assert.equal(requests[2].path,'/api/studio/operations/op');
});

test('run history reads one requested page even when older pages exist', async () => {
  const requests=[];
  const context=run(cut('  async function refreshRunHistoryList(', '  async function reconcileSelectedRunHistory('),{
    state:{selectedExperimentId:'exp',selectedRunId:'run',runHistoryGeneration:0,runHistoryPage:3},
    api:async path => {requests.push(path);return {items:[{run_id:'run'}],total_pages:8,next_cursor:'4'};},renderRunSelect(){},
  });
  await context.refreshRunHistoryList('exp','run');
  assert.deepEqual(requests,['/experiments/exp/runs?page=3&page_size=20']);
  assert.equal(context.state.runHistoryHasMore,true);
});

test('full definition fetches coalesce and an abandoned experiment cannot receive the late payload', async () => {
  const requests=[];let resolve;
  const context=run(cut('  async function ensureExperimentDefinition(', '  function renderDirtyState('),{
    state:{selectedExperimentId:'first',definition:{simulation:{steps:3}},draft:{}},
    api:path=>{requests.push(path);return new Promise(done=>resolve=done);},fillDraft(){throw Error('stale detail reached form');},
  });
  const first=context.ensureExperimentDefinition();
  const joined=context.ensureExperimentDefinition();
  assert.deepEqual(requests,['/experiments/first?view=definition']);
  context.state.selectedExperimentId='second';
  resolve({definition:{world:{},agents:[]},content_sha256:'hash'});
  assert.equal(await first,null);assert.equal(await joined,null);
  assert.equal(context.state.definition.world,undefined);
});

test('Agent content tabs update selection and fetch the selected section; pagination fetches the selected page', async () => {
  const handlers={};const calls=[];
  const tab={dataset:{agentContent:'event'},classList:{toggle(){}},setAttribute(){}};
  const section={dataset:{agentContentSection:'event'},hidden:true};
  const host={addEventListener(type,handler){handlers[type]=handler;},querySelectorAll:selector=>selector==='[data-agent-content]'?[tab]:[section]};
  const context=run(cut("  $('resultAgentDetail').addEventListener('click'", "  $('resultAgentDetail').addEventListener('keydown'"),{
    state:{selectedAgentContent:'plan',selectedAgentKey:'a',agentContentPages:new Map()},
    $:()=>host,showAgentDetail:async key=>calls.push(key),reportError:error=>{throw error;},
    syncWorkspaceUrl:options=>calls.push(options.push),agentContentPageKey:kind=>'run:a:'+kind,
  });
  handlers.click({target:{closest:selector=>selector==='[data-agent-content]'?tab:null}});
  assert.equal(context.state.selectedAgentContent,'event');
  assert.deepEqual(calls,['a',true]);assert.equal(section.hidden,false);
  handlers.click({target:{closest:selector=>selector==='[data-agent-page-kind]'?{dataset:{agentPageKind:'event',agentPage:3}}:null}});
  assert.equal(context.state.agentContentPages.get('run:a:event'),3);assert.equal(calls.at(-1),'a');
});

test('model catalog sends no empty enum for all purposes and preserves valid purpose filters', async () => {
  const requests=[], errors=[], nodes={};
  const saved={page:1,query:'',kind:''};
  const context=run(fs.readFileSync(base+'resources/model-workspace.js','utf8'),{
    window:{ResourceList:{read:()=>saved,remember:()=>{},loading(){},restore(){},empty:()=>'',pager(){},error:(host,name,error)=>errors.push(error)}},
    document:{getElementById:id=>nodes[id] ||= {querySelectorAll:()=>[],removeAttribute(){}}},
    location:{search:''},URLSearchParams,
    fetch:async path=>{requests.push(path);const query=new URL(path,'http://localhost').searchParams;
      assert.ok(!query.has('purpose') || ['chat','embedding'].includes(query.get('purpose')));
      return {ok:true,status:200,json:async()=>({items:[],page:1,page_size:5,total:0,total_pages:1})};},
  });
  await context.window.ModelWorkspace.activate();
  assert.equal(requests[0],'/api/studio/resources/model-services?page=1&page_size=5&q=');
  for(const kind of ['chat','embedding']) {
    saved.kind=kind;await context.window.ModelWorkspace.activate();
    assert.equal(new URL(requests.at(-1),'http://localhost').searchParams.get('purpose'),kind);
  }
  assert.deepEqual(errors,[]);
});

test('diagnostic logs render lightweight attempts without eager log metadata and read only the selected attempt', async () => {
  const requests=[], selected=[], nodes={};
  const attempt={attempt_id:'attempt-1',attempt_no:1,status:'COMPLETED',start_step:1,end_step:21,started_at:'2026-09-27T08:00:00+08:00',stop_reason:'COMPLETED',error_message:null};
  const context=run(
    cut('  function renderAttempts(', '  function renderModelTraces(') +
    cut('  async function loadOperationsWorkspaceUnlocked(', '  function simulationStartTime('),{
      state:{resultTab:'operations',operationTab:'logs',resultGeneration:1,selectedRunId:'run'},
      document:{visibilityState:'visible'},AbortController,
      $:id=>nodes[id] ||= {}, escapeHtml:String, formatTime:String,
      api:async path=>{requests.push(path);return {run_id:'run',items:[attempt],default_attempt_id:'attempt-1'};},
      selectAttemptLog:async (runId,attemptId)=>selected.push([runId,attemptId]),
    });
  await context.loadOperationsWorkspaceUnlocked('run',1);
  assert.deepEqual(requests,['/runs/run/attempts']);
  assert.deepEqual(selected,[['run','attempt-1']]);
  assert.match(nodes.attemptRows.innerHTML,/data-attempt-id="attempt-1"/);
  assert.match(nodes.attemptRows.innerHTML,/查看日志/);
  assert.doesNotMatch(nodes.attemptRows.innerHTML,/无日志/);
  assert.equal(context.state.selectedAttemptId,'attempt-1');
});
