const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
global.window={};
global.document={pointerLockElement:null};
require('../../src/generative_agents/adapters/web/static/resources/scene-map-viewer.js');
require('../../src/generative_agents/adapters/web/static/resources/simworld-scenes.js');

test('camera movement consumes wall time in metres, with normalized diagonal motion',()=>{
  const pose={position:[1,2,3],yaw_deg:90,pitch_deg:0,fov_deg:80};
  window.SceneMapViewer.move(pose,new Set(['KeyW']),.5);
  assert.ok(Math.abs(pose.position[0]-1)<1e-8);
  assert.deepEqual(pose.position.slice(1),[5,3]);
  const before=[...pose.position];
  window.SceneMapViewer.move(pose,new Set(['KeyA','KeyE']),1);
  assert.ok(Math.abs(6-Math.hypot(...pose.position.map((v,i)=>v-before[i])))<1e-8);
});

test('a missing Run record is distinct from unavailable preview capacity',()=>{
  const viewer=new window.SceneMapViewer({});
  Object.assign(viewer,{status:{},feedback:{},retry:{},leaveScene(){}});
  viewer.offline(Object.assign(Error('This provider has no record of the original Run scene; historical UE view unavailable'),{status:409}));
  assert.match(viewer.status.textContent,/没有此 Run 的原始场景记录/);
  assert.doesNotMatch(viewer.status.textContent,/占用/);
  viewer.offline(Object.assign(Error('Requested Run frame is not visible'),{status:409}));
  assert.doesNotMatch(viewer.status.textContent,/占用/);
  viewer.offline(Object.assign(Error('No dedicated preview slot is available'),{status:503}));
  assert.match(viewer.status.textContent,/场景被占用/);
});

for (const family of ['maps','experiments','runs']) test(`${family}: late open releases its scoped session without showing a stale frame`,async()=>{
  const viewer=Object.create(window.SceneMapViewer.prototype);
  Object.assign(viewer,{generation:0,retry:{},keys:new Set(),show(){}});
  const base=`/api/studio/simworld/${family}/first/sessions`;
  viewer.sessionBase=base;
  if (family==='runs')Object.assign(viewer,{replay:{runId:'first'},stage:{getBoundingClientRect:()=>({width:638})}});
  const old=global.fetch, calls=[];
  try {
    let reply;
    global.fetch=(url,opts)=>{
      calls.push([url,opts.method]);
      if(url===base) assert.deepEqual(JSON.parse(opts.body),family==='runs'?{capture_size:{width:640,height:360}}:{});
      return url.endsWith('/close')?Promise.resolve(new Response(null,{status:204})):new Promise(resolve=>{reply=resolve;});
    };
    const opening=viewer.connect();
    viewer.leaveScene(); viewer.sessionBase='/next';
    reply(Response.json({session_id:'late'})); await opening;
    assert.deepEqual(calls,[
      [base,'POST'],
      [base+'/late/close','POST'],
    ]);
    assert.equal(viewer.url,null);
  } finally {global.fetch=old;}
});

test('Run capture follows the displayed width, debounces tier crossings, and resizes inside one session',async()=>{
  const viewer=new window.SceneMapViewer({}), calls=[];
  let width=642, complete;
  const original=global.fetch;
  Object.assign(viewer,{stage:{getBoundingClientRect:()=>({width})},captureWidth:640,url:'/runs/r/sessions/live',
    ready:true,canControl:true,replay:{runId:'r'},status:{}});
  try {
    assert.equal(viewer.replayCaptureWidth(),640);
    width=815;
    assert.equal(viewer.replayCaptureWidth(640),640);
    assert.equal(viewer.replayCaptureWidth(960),960);
    width=980;assert.equal(viewer.replayCaptureWidth(640),960);
    const generation=viewer.generation;
    global.fetch=(url,opts)=>new Promise(resolve=>{
      const body=JSON.parse(opts.body);calls.push([url,body]);
      complete=()=>resolve(Response.json({capture_size:body}));
    });
    viewer.scheduleReplayResize(generation);
    width=1550;viewer.scheduleReplayResize(generation);
    await new Promise(resolve=>setTimeout(resolve,250));
    assert.deepEqual(calls,[['/runs/r/sessions/live/capture-size',{width:1280,height:720}]]);
    width=642;viewer.scheduleReplayResize(generation);
    await new Promise(resolve=>setTimeout(resolve,250)); // the previous request is still in flight
    assert.equal(calls.length,1);
    complete();
    await new Promise(resolve=>setTimeout(resolve,250));
    assert.deepEqual(calls[1],['/runs/r/sessions/live/capture-size',{width:640,height:360}]);
    complete();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(viewer.captureWidth,640);
    width=1200;viewer.scheduleReplayResize(generation);
    viewer.url=null;viewer.leaveScene();
    await new Promise(resolve=>setTimeout(resolve,250));
    assert.equal(calls.length,2);
  } finally {clearTimeout(viewer.captureTimer);global.fetch=original;}
});

test('an experiment distinguishes local maps from SimWorld scene IDs without writing a copy',()=>{
  const old=global.fetch;
  try {
    global.fetch=()=>assert.fail('selecting a scene must not write to the service');
    assert.deepEqual(window.SimWorldScenes.selection('public-map'),{map_id:'public-map'});
    assert.deepEqual(window.SimWorldScenes.selection('simworld:harbor'),{simworld_scene_id:'harbor'});
  } finally {global.fetch=old;}
});

test('the map workspace supplies context to one scope-independent 3D viewer',()=>{
  const source=fs.readFileSync('src/generative_agents/adapters/web/static/resources/map-workspace.js','utf8');
  const viewer=fs.readFileSync('src/generative_agents/adapters/web/static/resources/scene-map-viewer.js','utf8');
  assert.match(source,/spatial_3d: window.SceneMapViewer/);
  assert.match(source,/setWorld\(detail\.world,this\.sceneViewContext\(detail\)\)/);
  assert.doesNotMatch(viewer,/ResourceScope|publicMapId|world-act|city\/generate|preview\/frame|api\/studio\/simworld/);
});

test('catalog selector lists every installed previewable scene and reports an unavailable directory',async()=>{
  const oldFetch=global.fetch,oldOption=global.Option,oldDoc=global.document;
  function node() {return {children:[],dataset:{},isConnected:true,append(...children){this.children.push(...children);},
    replaceChildren(...children){this.children=children;},querySelector(){return null;}};}
  try {
    global.document={createElement:node};
    global.Option=class {constructor(text,value){this.text=text;this.value=value;}};
    global.fetch=async()=>Response.json({scenes:[
      {id:'missing',name:'未安装',availability:'missing',previewable:false},
      {id:'known',name:'第二个已安装场景',availability:'unverified',previewable:true},
      {id:'ready',name:'现有场景',availability:'snapshot_ready',previewable:true},
    ]});
    const select=node(),summary=node();select.selectedOptions=[];
    await window.SimWorldScenes.populate(select,summary);
    assert.deepEqual(select.children[0].children.map(o=>[o.text,o.value]),[['第二个已安装场景','simworld:known'],['现有场景','simworld:ready']]);
    assert.equal(summary.hidden,true);
    assert.equal(summary.textContent,'');
    assert.deepEqual(summary.children,[]);
    global.fetch=async()=>Response.json({scenes:[]});
    await window.SimWorldScenes.populate(node(),summary);
    assert.equal(summary.hidden,false);
    assert.match(summary.textContent,/没有可预览/);
    global.fetch=async()=>{throw Error('offline');};
    await window.SimWorldScenes.populate(node(),summary);
    assert.equal(summary.hidden,false);
    assert.match(summary.textContent,/无法连接/);
  } finally {global.fetch=oldFetch;global.Option=oldOption;global.document=oldDoc;}
});

test('public, draft and sealed remote scene selections use the same viewer without modifying maps',()=>{
  const world={world_name:'港口',definition:{environment_type:'spatial_3d',simworld_scene_id:'harbor'}};
  const before=structuredClone(world);
  for (const [base,editable] of [['/scenes/harbor/sessions',false],['/experiments/draft/sessions',true],['/experiments/sealed/sessions',false]]) {
    const viewer=new window.SceneMapViewer({});
    let opens=0;
    Object.assign(viewer,{render(){},connect(){opens++;}});
    viewer.setWorld(world,{sessionBase:base});
    viewer.setReadOnly(!editable);
    assert.equal(opens,1);
    assert.equal(viewer.sessionBase,base);
    assert.equal(viewer.changed,false);
    assert.deepEqual(viewer.getWorld(),before);
    viewer.setWorld({world_name:'历史地图',definition:{}},{sessionBase:base});
    assert.equal(opens,1);assert.equal(viewer.sessionBase,null);
  }
});

test('starting keeps polling without requesting an empty stream, then an unsupported MJPEG stream falls back to JPEG polling',async()=>{
  const viewer=new window.SceneMapViewer({});
  const old=global.fetch;
  const media={removeAttribute(){delete this.src;}};
  const calls=[];
  Object.assign(viewer,{url:'/snapshot-sessions/test',presets:{},live:media,status:{},retry:{},listeners:new AbortController()});
  try {
    let delivered;
    const seen=new Promise(resolve=>{delivered=resolve;});
    global.fetch=async(url,opts)=>{
      calls.push([url,opts.method||'GET']);
      if(url.endsWith('/snapshot.jpg'))return new Response(new Uint8Array([255,216,255,217]),{headers:{'Content-Type':'image/jpeg'}});
      return new Response(null,{status:204});
    };
    viewer.updateState({state:'starting',frame_seq:0});
    assert.equal(viewer.ready,false);assert.equal(media.src,undefined);assert.equal(viewer.url,'/snapshot-sessions/test');
    viewer.updateState({state:'ready',frame_seq:1});
    assert.equal(media.src,'/snapshot-sessions/test/stream');
    assert.match(viewer.status.textContent,/接收实景/);
    Object.defineProperty(media,'src',{set(value){if(value.startsWith('blob:'))delivered();},configurable:true});
    // Simulate a browser which cannot decode multipart JPEG.
    media.removeAttribute=()=>{};
    media.onerror();
    await seen;
    assert.equal(viewer.snapshots,true);
    assert.equal(calls.filter(c=>c[0].endsWith('/snapshot.jpg')).length,1);
    media.naturalWidth=640;media.naturalHeight=480;
    viewer.presentFrame();
    assert.equal(viewer.hasLiveFrame,true);
    assert.equal(viewer.status.textContent,'');
    viewer.leaveScene();await viewer.closing;
    assert.deepEqual(calls.at(-1),['/snapshot-sessions/test/close','POST']);
    assert.equal(media.hidden,true);
  } finally { viewer.leaveScene();global.fetch=old; }
});

test('first-person selection waits for its own applied sequence and returns to the prior observer position',async()=>{
  const viewer=new window.SceneMapViewer({});
  const observer={position:[-3,4,9],pitch_deg:-4,yaw_deg:65,fov_deg:70};
  const eye={position:[1,2,1.6],pitch_deg:0,yaw_deg:90,fov_deg:90};
  Object.assign(viewer,{currentView:{type:'preset',id:'cross'},ready:true,hasLiveFrame:true,canControl:true,
    pose:structuredClone(observer),url:'/isolated/session',status:{},agentIds:new Set(['south'])});
  const calls=[], original=global.fetch;
  try {
    global.fetch=async(url,opts)=>{
      calls.push([url,JSON.parse(opts.body)]);
      return Response.json({request_seq:calls.length,applied_seq:calls.length-1,pose:viewer.pose});
    };
    viewer.chooseView('agent','south');
    await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual(calls[0],['/isolated/session/agent-view',{agent_key:'south'}]);
    viewer.confirmView({applied_seq:0,pose:observer});
    assert.equal(viewer.currentView.id,'cross');
    viewer.confirmView({applied_seq:1,pose:eye});
    assert.deepEqual(viewer.currentView,{type:'agent',id:'south'});
    assert.deepEqual(viewer.pose,eye);
    viewer.chooseView('free','');
    await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual(calls[1],['/isolated/session/pose',observer]);
    assert.equal(viewer.currentView.type,'agent');
    viewer.confirmView({applied_seq:2,pose:observer});
    assert.equal(viewer.currentView.type,'free');
    assert.equal(viewer.freePose,null);
  } finally {global.fetch=original;}
});

test('an unavailable Agent view reports the selection error without losing the healthy map session',async()=>{
  const viewer=new window.SceneMapViewer({});
  Object.assign(viewer,{url:'/active',currentView:{type:'preset',id:'start'},ready:true,hasLiveFrame:true,canControl:true,
    pose:{position:[0,0,0],pitch_deg:0,yaw_deg:0,fov_deg:70},status:{},agentIds:new Set(['unknown'])});
  const original=global.fetch;
  try {
    global.fetch=async()=>Response.json({detail:'此角色未加入本实验'},{status:404});
    viewer.chooseView('agent','unknown');
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(viewer.url,'/active');
    assert.equal(viewer.currentView.id,'start');
    assert.equal(viewer.waitingView,null);
    assert.match(viewer.status.textContent,/角色未加入/);
  } finally {global.fetch=original;}
});

test('replay sends only the latest committed eye pose when a session opens and when a camera request is in flight',async()=>{
  const viewer=new window.SceneMapViewer({});
  const poseAt=x=>({position:[x,2,1.6],pitch_deg:0,yaw_deg:15,fov_deg:80});
  const observer=poseAt(0),original=global.fetch;
  const commands=[];
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  try {
    global.fetch=(url,options)=>new Promise(resolve=>{
      const body=JSON.parse(options.body),seq=commands.length+1;
      commands.push({url,body,confirm:()=>resolve(Response.json({request_seq:seq,applied_seq:seq,pose:body}))});
    });
    Object.assign(viewer,{url:'/runs/r/sessions/s',pose:observer,status:{},live:{naturalWidth:1280,naturalHeight:720}});
    viewer.setReplayCamera(poseAt(1));
    viewer.setReplayCamera(poseAt(2));
    viewer.updateState({state:'ready',pose:observer,role:'viewer'});
    assert.equal(commands.length,0);
    viewer.updateState({state:'ready',pose:observer,can_control:true});
    assert.deepEqual(commands[0].body,poseAt(2));
    viewer.setReplayCamera(poseAt(3));
    viewer.setReplayCamera(poseAt(4));
    assert.equal(commands.length,1);
    commands[0].confirm();
    await tick();
    assert.equal(viewer.currentView,undefined);
    assert.deepEqual(commands[1].body,poseAt(4));
    commands[1].confirm();
    await tick();
    assert.equal(viewer.currentView.type,'replay');
    viewer.setReplayCamera(poseAt(4));
    viewer.chooseView('preset','map-preset');
    assert.equal(commands.length,2);
    viewer.setReplayCamera(null);
    assert.deepEqual(commands[2].body,observer);
    commands[2].confirm();
    await tick();
    assert.equal(viewer.currentView.type,'free');
    assert.deepEqual(viewer.pose,observer);
    assert.ok(commands.every(command=>command.url==='/runs/r/sessions/s/pose'));
  } finally {global.fetch=original;}
});

test('changing replay world drops the selected pose and ignores late camera responses from the old Run',async()=>{
  const viewer=new window.SceneMapViewer({});
  const pose={position:[1,2,1.6],pitch_deg:0,yaw_deg:90,fov_deg:70};
  const original=global.fetch;
  let finish;
  try {
    global.fetch=url=>url.endsWith('/close')?Promise.resolve(new Response(null,{status:204})):
      new Promise(resolve=>{finish=()=>resolve(Response.json({request_seq:1,applied_seq:1,pose}));});
    Object.assign(viewer,{ready:true,hasLiveFrame:true,canControl:true,pose,url:'/runs/old/sessions/s',status:{},render(){},connect(){}});
    viewer.setReplayCamera(pose);
    viewer.setWorld({definition:{simworld_scene_id:'harbor'}},{sessionBase:'/runs/new/sessions'});
    finish();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(viewer.currentView,null);
    assert.equal(viewer.replayPose,null);
    assert.equal(viewer.pending,null);
  } finally {global.fetch=original;}
});

test('a Run map preset releases third-person follow directly without sending the saved free pose first',()=>{
  const viewer=new window.SceneMapViewer({});
  const pose={position:[6,0,4],pitch_deg:-30,yaw_deg:180,fov_deg:65};
  const commands=[];
  Object.assign(viewer,{hasLiveFrame:true,ready:true,canControl:true,pose,replayPose:pose,sentReplayPose:pose,freePose:pose,
    replay:{leaveFollow:()=>commands.push('leave')},requestView:(type,id)=>commands.push([type,id])});
  viewer.chooseView('preset','crossing');
  assert.equal(viewer.replayPose,null);
  assert.equal(viewer.freePose,null);
  assert.deepEqual(commands,['leave',['preset','crossing']]);
});

test('replay decodes matching intermediate frames in order and waits for the committed endpoint',async()=>{
  const viewer=new window.SceneMapViewer({});
  const original=global.fetch,oldImage=global.Image,images=[],calls=[];
  let nativeWidth=480;
  class Frame {
    constructor(){this.dataset={};this.naturalWidth=nativeWidth;this.naturalHeight=nativeWidth*9/16;}
    async decode(){assert.match(this.src,/^blob:/);}
    replaceWith(frame){images.push(frame);}
  }
  Object.assign(viewer,{sessionBase:'/runs/r/sessions',url:'/runs/r/sessions/s',ready:true,active:true,canControl:true,
    replay:{runId:'r',names:new Map([['b','由里']])},status:{},thumb:{},live:new Frame()});
  let samples=0,firstShown=false;
  try {
    global.Image=Frame;
    global.fetch=async(url,opts)=>{
      calls.push(url);
      if(url.endsWith('/replay')){
        assert.deepEqual(JSON.parse(opts.body),{step_no:7,playback_ms:900});
        return Response.json({replay:{run_id:'r',requested_seq:5,requested_step:7,missing_actors:['b']}});
      }
      assert.equal(url,'/runs/r/sessions/s/snapshot.jpg?replay_seq=5&after_frame_seq='+[-1,-1,2,2,4,5][samples]);
      samples++;
      if(samples===1 || samples===3)return new Response(null,{status:204});
      return new Response(new Uint8Array([255,216,255,217]),{headers:{
        'Content-Type':'image/jpeg','X-Frame-Seq':String(samples===3?2:samples),
        'X-Replay-Seq':'5','X-Replay-Step':'7','X-Replay-Settled':samples>=4?'1':'0'}});
    };
    const arrived=await viewer.presentReplayStep(7,900,()=>{
      assert.equal(images.length,1);
      assert.match(viewer.status.textContent,/已提交路径/);
      firstShown=true;
    });
    assert.equal(arrived,true);
    assert.ok(firstShown);
    assert.equal(images.length,2);
    assert.match(viewer.status.textContent,/已提交位置.*由里/);
    await new Promise(resolve=>setTimeout(resolve,215));
    assert.equal(samples,4,'an endpoint stays on the same captured pose');
    viewer.confirmView({applied_seq:4});
    await new Promise(resolve=>setTimeout(resolve,105));
    assert.equal(samples,5,'a confirmed camera change requests a new view at the same Step');
    viewer.captureWidth=nativeWidth=640;
    viewer.serverCaptureWidth=640;
    await new Promise(resolve=>setTimeout(resolve,185));
    assert.equal(samples,6,'a confirmed capture resize refreshes the frozen view once');
    assert.equal(viewer.live.naturalWidth,640);
  } finally {
    viewer.cancelReplayFrame();URL.revokeObjectURL(viewer.objectUrl);
    global.fetch=original;global.Image=oldImage;
  }
});

test('a late POST is ordered before the new seek, and a mismatched JPEG is rejected before display',async()=>{
  const viewer=new window.SceneMapViewer({}),requests=[];
  Object.assign(viewer,{sessionBase:'/run',url:'/run/session',ready:true,active:true,canControl:true,
    replay:{runId:'r',names:new Map()},status:{}});
  const original=global.fetch,tick=()=>new Promise(resolve=>setImmediate(resolve));
  try {
    global.fetch=(url,opts)=>{
      if(url.endsWith('/replay'))return new Promise(resolve=>{
        const step=JSON.parse(opts.body).step_no;
        requests.push({step,accept:()=>resolve(Response.json({replay:{run_id:'r',requested_seq:step,requested_step:step}}))});
      });
      assert.match(url,/replay_seq=2&after_frame_seq=-1$/);
      return Promise.resolve(new Response('wrong step',{headers:{'Content-Type':'image/jpeg','X-Frame-Seq':'4',
        'X-Replay-Seq':'2','X-Replay-Step':'1','X-Replay-Settled':'1'}}));
    };
    viewer.offline=error=>{viewer.show(error.message);viewer.cancelReplayFrame();};
    const old=viewer.presentReplayStep(9,0,()=>assert.fail('stale frame displayed'));
    await tick();
    const next=viewer.presentReplayStep(2,0,()=>assert.fail('mismatched frame displayed'));
    await tick();
    assert.deepEqual(requests.map(r=>r.step),[9]);
    requests[0].accept();
    await tick();
    assert.deepEqual(requests.map(r=>r.step),[9,2]);
    requests[1].accept();
    assert.equal(await old,false);
    assert.equal(await next,false);
    assert.match(viewer.status.textContent,/步骤不符/);
  } finally {viewer.cancelReplayFrame();global.fetch=original;}
});
