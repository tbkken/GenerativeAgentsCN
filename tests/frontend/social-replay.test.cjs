const {test} = require('node:test');
const assert = require('node:assert/strict');
const {GASocialReplayPlayer, mergeConversations, forumTree} = require('../../src/generative_agents/adapters/web/static/replay/social-replay.js');

const message = (id, content, extra = {}) => ({message_id:id, content, speaker_agent_key:'a', step_no:1, actor_sequence:1, sequence:1, ...extra});
const conversation = (id, messages, extra = {}) => ({conversation_id:id, visibility:'public', kind:'chat', participant_agent_keys:['a','b'], messages, ...extra});
const response = body => ({ok:true, json:async()=>body});
function player(fetchImpl) {
  const result = new GASocialReplayPlayer({fetchImpl});
  Object.assign(result, {runId:'run', ready:true, availableStep:1, currentStep:1, generation:1,
    manifest:{presentation:'chat', agents:[{agent_key:'a',display_name:'甲'}, {agent_key:'b',display_name:'乙'}, {agent_key:'c',display_name:'丙'}]},
    host:{innerHTML:'', querySelector:()=>null}, abortController:new AbortController()});
  return result;
}

test('committed messages supersede matching previews without duplicate public or private bubbles',()=>{
  const records = mergeConversations([conversation('public',[message('m1','已提交')])], [conversation('public',[message('m1','旧预览'),message('m2','新预览',{actor_sequence:2})])]);
  assert.equal(records[0].messages.length,2);
  assert.equal(records[0].messages[0].content,'已提交');
  assert.equal(records[0].messages[0].pending,false);
  assert.equal(records[0].messages[1].pending,true);
});

test('public rendering excludes private facts and private lists are scoped to selected participants',()=>{
  const p=player();
  p.conversations=[conversation('group',[message('public','公共内容')]),conversation('ab',[message('private','甲乙私聊')],{visibility:'private',kind:'direct'})];
  p.render();
  assert.match(p.host.innerHTML,/公共内容/); assert.doesNotMatch(p.host.innerHTML,/甲乙私聊/);
  p.selectAgent('c'); assert.doesNotMatch(p.host.innerHTML,/甲乙私聊/);
  p.selectAgent('a'); assert.match(p.host.innerHTML,/甲乙私聊/); assert.match(p.host.innerHTML,/data-social-conversation="ab"/);
  p.privateConversationId='ab'; p.render(); assert.match(p.host.innerHTML,/返回会话列表/); assert.match(p.host.innerHTML,/甲乙私聊/);
});

test('forum comments preserve nested reply relationships and quotes cannot expose private messages',()=>{
  const messages=[message('root','主题',{title:'问题'}),message('reply','一级回复',{reply_to_message_id:'root',sequence:2}),message('nested','二级回复',{reply_to_message_id:'reply',sequence:3})];
  const tree=forumTree(messages);
  assert.equal(tree.length,1); assert.equal(tree[0].children[0].children[0].message_id,'nested');
  assert.doesNotThrow(()=>JSON.stringify(forumTree([message('a','',{reply_to_message_id:'b'}),message('b','',{reply_to_message_id:'a'})])));
  const p=player(); p.manifest.presentation='forum';
  p.conversations=[conversation('post',messages,{kind:'forum'}),conversation('secret',[message('hidden','不得引用私聊')],{visibility:'private'})];
  messages[1].quoted_message_id='hidden';
  p.render(); assert.match(p.host.innerHTML,/data-social-branch="reply"/); assert.doesNotMatch(p.host.innerHTML,/不得引用私聊/);
});

test('untrusted social text is escaped in body, title, names and quote preview',()=>{
  const p=player(); p.manifest.agents[0].display_name='<img onerror=bad>';
  p.conversations=[conversation('public',[message('m1','<script>bad</script>',{title:'<svg onload=bad>'}),message('m2','reply',{quoted_message_id:'m1'})])];
  p.render(); assert.doesNotMatch(p.host.innerHTML,/<script>|<img onerror|<svg onload/);
  assert.match(p.host.innerHTML,/&lt;script&gt;/);
});

test('formal playback never requests previews; returning to an old step replaces cumulative content',async()=>{
  const urls=[];
  const p=player(async url=>{
    urls.push(url);
    if(url.includes('availability')) return response({available_step:2});
    if(url.includes('results/social')) return response({run_id:'run',conversations:[conversation('group',[message('m1','第一步')])]});
    if(url.includes('replay/steps')) return response({steps:[{step_no:1,conversations:[],domain_events:[]}]});
    throw Error('unexpected preview request');
  });
  p.live=true; p.preview={conversations:[conversation('group',[message('m2','第二步未提交')])]};
  await p.seek(1); await p.refreshAvailable();
  assert.equal(p.live,false); assert.equal(p.preview,null);
  assert.doesNotMatch(p.host.innerHTML,/第二步未提交/);
  assert.ok(urls.some(url=>url.endsWith('step_no=1')));
  assert.ok(urls.every(url=>!url.includes('runtime-preview')));
});

test('failed or cancelled preview disappears while committed content remains',async()=>{
  let active=true;
  const p=player(async url=>response(url.includes('availability')?{available_step:1}:{run_id:'run',active,uncommitted:active,step_no:2,actor_sequence:1,actor_total:2,conversations:active?[conversation('group',[message('m2','本轮暂存',{step_no:2})])]:[]}));
  p.live=true; p.conversations=[conversation('group',[message('m1','已提交内容')])];
  await p.refreshAvailable(); assert.match(p.host.innerHTML,/本轮暂存/); assert.match(p.host.innerHTML,/未提交/);
  active=false; await p.refreshAvailable(); assert.doesNotMatch(p.host.innerHTML,/本轮暂存/); assert.match(p.host.innerHTML,/已提交内容/);
});

test('late historical seek response cannot replace newer step content',async()=>{
  let finishOld;
  const old=new Promise(resolve=>finishOld=resolve);
  const p=player(async url=>{
    if(url.includes('results/social?step_no=1')) return old;
    if(url.includes('results/social')) return response({run_id:'run',conversations:[conversation('group',[message('new','较新步骤')])]});
    return response({steps:[{step_no:url.includes('from_step=1')?1:2,conversations:[],domain_events:[]}]});
  }); p.availableStep=2;
  const first=p.seek(1); await p.seek(2);
  finishOld(response({run_id:'run',conversations:[conversation('group',[message('old','迟到旧内容')])]})); await first;
  assert.equal(p.currentStep,2); assert.match(p.host.innerHTML,/较新步骤/); assert.doesNotMatch(p.host.innerHTML,/迟到旧内容/);
});

test('preview arriving after switching into formal playback is discarded',async()=>{
  let finish;
  const pending=new Promise(resolve=>finish=resolve);
  const p=player(async url=>url.includes('runtime-preview')?pending:response(url.includes('availability')?{available_step:1}:url.includes('results/social')?{run_id:'run',conversations:[]}:{steps:[]}));
  p.live=true;
  const polling=p.refreshAvailable(); await new Promise(resolve=>setImmediate(resolve));
  await p.seek(1);
  finish(response({run_id:'run',active:true,uncommitted:true,step_no:2,conversations:[conversation('group',[message('late','迟到预览')])]}));
  await polling; assert.equal(p.preview,null); assert.doesNotMatch(p.host.innerHTML,/迟到预览/);
});

test('the shared timeline shows only public social messages and preserves spatial conversations',()=>{
  const fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
  const source=fs.readFileSync(path.join(__dirname,'../../src/generative_agents/adapters/web/static/shell/console-api.js'),'utf8');
  const render=source.slice(source.indexOf('function renderReplayStep('),source.indexOf('function renderReplayMarkers('));
  const nodes=new Map();
  const get=id=>{
    if(!nodes.has(id)) nodes.set(id,{value:'',checked:true,innerHTML:'',textContent:''});
    return nodes.get(id);
  };
  const state={selectedRunId:'run',resultGeneration:1,replayMarkerFacts:new Map(),
    replayPlayer:{isSocial:true,manifest:{},availableStep:1}};
  const context={state,$:get,formatTime:String,escapeHtml:String,syncReplayControls(){},renderReplayMarkers(){}};
  vm.createContext(context); vm.runInContext(render,context);
  const step={step_no:1,virtual_time:'2026-01-01T00:00:00Z',conversations:[
    conversation('public',[message('p','公开消息')]),
    conversation('private',[message('s','私聊秘密')],{visibility:'private',kind:'direct'}),
  ],domain_events:[{event_type:'WAIT',payload:{description:'等待原因'}}]};
  context.renderReplayStep({step},'run',1);
  assert.match(get('timelineStreamItems').innerHTML,/公开消息/);
  assert.doesNotMatch(get('timelineStreamItems').innerHTML,/私聊秘密|等待原因/);
  state.replayPlayer.isSocial=false;
  context.renderReplayStep({step},'run',1);
  assert.match(get('timelineStreamItems').innerHTML,/公开消息/);
  assert.match(get('timelineStreamItems').innerHTML,/私聊秘密/);
});
