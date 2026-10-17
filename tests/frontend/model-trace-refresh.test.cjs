const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

const source=fs.readFileSync('src/generative_agents/adapters/web/static/shell/console-api.js','utf8');
const traceFunctions=source.slice(source.indexOf('  function renderModelTraces()'),source.indexOf('  async function loadTraceDetail('));

function harness() {
 const nodes=new Map();
 const pending=[];
 const node=id=>{
  if(!nodes.has(id)) nodes.set(id,{value:'',hidden:false,innerHTML:''});
  return nodes.get(id);
 };
 const state={selectedRunId:'run',selectedAttemptId:'attempt',selectedTraceAttemptId:'attempt',
  operationFactsGeneration:1,currentRun:{status:'CANCELLED'},traceItems:[],traceCursor:null,tracePage:1,traceEof:true};
 const context={state,$:node,escapeHtml:String,
  operationListPager:(_kind,total,page)=>{
   page=Math.max(1,Math.min(page,Math.max(1,Math.ceil(total/10))));
   return {page,itemsFrom:(page-1)*10,itemsTo:page*10,html:`page ${page}; total ${total}`};
  },
  api:url=>new Promise((resolve,reject)=>pending.push({url,resolve,reject})),
 };
 vm.createContext(context);vm.runInContext(traceFunctions,context);
 const controller=new AbortController();
 return {...context,node,pending,controller,
  load:(options={})=>context.loadModelTraces(state.selectedRunId,state.selectedTraceAttemptId,controller.signal,options),
 };
}
const row=(id,seq,status='SUCCEEDED')=>({call_id:id,attempt_no:0,trace_id:`attempt:${seq}`,event_seq:seq,status,purpose:'skill_runtime'});
const records=Array.from({length:25},(_,index)=>row(`call-${index}`,index*3+2));
async function seed(h) {
 const load=h.load();h.pending.shift().resolve({items:records,next_cursor:75,eof:true});await load;
 h.state.tracePage=3;h.renderModelTraces();
}

test('full refresh preserves the 25 existing rows and current page while pending or failed',async()=>{
 const h=harness();await seed(h);
 const refresh=h.load();
 h.renderModelTraces(); // The visible pagination handler can run before the request returns.
 assert.equal(h.state.traceItems.length,25);
 assert.equal(h.state.tracePage,3);
 assert.match(h.node('modelTracePagination').innerHTML,/total 25/);
 h.pending.shift().reject(new Error('temporary network failure'));
 await assert.rejects(refresh,/temporary network failure/);
 assert.equal(h.state.traceItems.length,25);
 assert.equal(h.state.traceCursor,75);
});

test('a delayed empty incremental poll cannot erase a newer full refresh or its cursor',async()=>{
 const h=harness();await seed(h);
 const poll=h.load({append:true});const old=h.pending.shift();
 assert.match(old.url,/cursor=75/);
 const refresh=h.load();const newer=h.pending.shift();
 old.resolve({items:[],next_cursor:75,eof:true});await poll;
 assert.equal(h.state.traceItems.length,25);
 newer.resolve({items:[...records,row('new',80)],next_cursor:81,eof:true});await refresh;
 assert.equal(h.state.traceItems.length,26);
 assert.equal(h.state.traceCursor,81);
 assert.equal(h.state.tracePage,3);
});

test('a late older full response cannot override the latest refresh snapshot',async()=>{
 const h=harness();await seed(h);
 const older=h.load();const old=h.pending.shift();
 const latest=h.load();const fresh=h.pending.shift();
 fresh.resolve({items:[row('latest',90)],next_cursor:91,eof:true});await latest;
 old.resolve({items:records,next_cursor:75,eof:true});await older;
 assert.equal(h.state.traceItems.length,1);
 assert.equal(h.state.traceItems[0].call_id,'latest');
 assert.equal(h.state.traceCursor,91);
});

test('polling and load-more coalesce behind a full refresh without changing its cursor',async()=>{
 const h=harness();await seed(h);
 const refresh=h.load();const request=h.pending.shift();
 assert.match(request.url,/cursor=0/);
 const poll=h.load({append:true});const more=h.load({append:true});
 assert.equal(h.pending.length,0);
 request.resolve({items:records,next_cursor:78,eof:true});
 await Promise.all([refresh,poll,more]);
 assert.equal(h.state.traceItems.length,25);
 assert.equal(h.state.tracePage,3);
 assert.equal(h.state.traceCursor,78);
});

test('a stale incremental completion cannot roll back the cursor after refresh succeeds',async()=>{
 const h=harness();await seed(h);
 const poll=h.load({append:true});const old=h.pending.shift();
 const refresh=h.load();const fresh=h.pending.shift();
 fresh.resolve({items:[...records,row('new',80)],next_cursor:81,eof:true});await refresh;
 old.resolve({items:[],next_cursor:75,eof:true});await poll;
 assert.equal(h.state.traceItems.length,26);
 assert.equal(h.state.traceCursor,81);
});

test('terminal calls stay deduplicated across repeated empty incremental pages',async()=>{
 const h=harness();await seed(h);
 const append=h.load({append:true});const request=h.pending.shift();
 request.resolve({items:[row('call-24',75,'RUNNING'),row('call-24',76,'ABORTED')],next_cursor:77,eof:true});await append;
 for(let index=0;index<3;index++) {
  const poll=h.load({append:true});h.pending.shift().resolve({items:[],next_cursor:77,eof:true});await poll;
 }
 assert.equal(h.state.traceItems.length,25);
 assert.equal(h.state.traceItems.at(-1).status,'ABORTED');
 assert.equal(h.state.traceItems.at(-1).event_seq,76);
 assert.equal(h.state.traceCursor,77);
});

test('purpose changes replace the snapshot, reject stale results, and allow a genuinely empty result',async()=>{
 const h=harness();await seed(h);
 const older=h.load();const old=h.pending.shift();
 h.node('tracePurposeFilter').value='embedding';
 const filtered=h.load({append:true});const request=h.pending.shift();
 assert.match(request.url,/cursor=0/);
 assert.match(request.url,/purpose=embedding/);
 assert.equal(h.state.traceItems.length,0);
 old.resolve({items:records,next_cursor:75,eof:true});await older;
 assert.equal(h.state.traceItems.length,0);
 request.resolve({items:[],next_cursor:80,eof:true});await filtered;
 assert.equal(h.state.traceItems.length,0);
 assert.equal(h.state.traceCursor,80);
 assert.match(h.node('modelTraceRows').innerHTML,/尚无模型调用明细/);
 h.node('tracePurposeFilter').value='';
 const restored=h.load();h.pending.shift().resolve({items:records,next_cursor:80,eof:true});await restored;
 assert.equal(h.state.traceItems.length,25);
});

test('switching the trace Attempt rejects its previous response even if the log Attempt is unchanged',async()=>{
 const h=harness();await seed(h);
 const oldLoad=h.load();const old=h.pending.shift();
 h.state.selectedTraceAttemptId='another-attempt';
 const load=h.load();const request=h.pending.shift();
 assert.match(request.url,/attempt_id=another-attempt/);
 assert.match(request.url,/cursor=0/);
 request.resolve({items:[row('another',2)],next_cursor:3,eof:true});await load;
 old.resolve({items:records,next_cursor:75,eof:true});await oldLoad;
 assert.equal(h.state.selectedAttemptId,'attempt');
 assert.equal(h.state.traceItems.length,1);
 assert.equal(h.state.traceItems[0].call_id,'another');
 assert.equal(h.state.traceCursor,3);
});

test('returning to the same Run and Attempt does not accept an older workspace generation',async()=>{
 const h=harness();await seed(h);
 const oldLoad=h.load();const old=h.pending.shift();
 h.state.selectedRunId='other-run';h.state.operationFactsGeneration++;
 const otherLoad=h.load();const other=h.pending.shift();
 assert.match(other.url,/\/runs\/other-run/);
 h.state.selectedRunId='run';h.state.operationFactsGeneration++;
 const latest=h.load();const request=h.pending.shift();
 request.resolve({items:[row('current',80)],next_cursor:81,eof:true});await latest;
 old.resolve({items:records,next_cursor:75,eof:true});await oldLoad;
 other.resolve({items:[row('wrong-run',100)],next_cursor:101,eof:true});await otherLoad;
 assert.equal(h.state.traceItems.length,1);
 assert.equal(h.state.traceItems[0].call_id,'current');
 assert.equal(h.state.traceCursor,81);
});

test('an aborted refresh retains the snapshot and can be retried with a fresh signal',async()=>{
 const h=harness();await seed(h);
 const refresh=h.load();const request=h.pending.shift();
 h.controller.abort();request.reject(new Error('aborted'));await refresh;
 assert.equal(h.state.traceItems.length,25);
 assert.equal(h.state.traceCursor,75);
 const retry=h.loadModelTraces('run','attempt',new AbortController().signal);
 h.pending.shift().resolve({items:records,next_cursor:80,eof:true});await retry;
 assert.equal(h.state.traceItems.length,25);
 assert.equal(h.state.traceCursor,80);
});

test('a failed superseded refresh cannot report an error against the new selection',async()=>{
 const h=harness();await seed(h);
 const oldLoad=h.load();const old=h.pending.shift();
 h.node('tracePurposeFilter').value='new-purpose';
 const latest=h.load();const request=h.pending.shift();
 request.resolve({items:[row('new-purpose',80)],next_cursor:81,eof:true});await latest;
 old.reject(new Error('stale failure'));await oldLoad;
 assert.equal(h.state.traceItems.length,1);
 assert.equal(h.state.traceItems[0].call_id,'new-purpose');
});

test('a successful empty full refresh clears stale rows and clamps the old page',async()=>{
 const h=harness();await seed(h);
 const refresh=h.load();h.pending.shift().resolve({items:[],next_cursor:0,eof:true});await refresh;
 assert.equal(h.state.traceItems.length,0);
 assert.equal(h.state.tracePage,1);
 assert.equal(h.state.traceCursor,0);
 assert.equal(h.node('loadMoreTraces').hidden,true);
});
