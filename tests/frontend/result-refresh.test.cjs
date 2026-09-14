const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('generative_agents/web/static/console-api.js','utf8');
const wrapper=source.slice(source.indexOf('  function refreshResultData('),source.indexOf('  async function refreshResultDataUnlocked('));
const operationWrapper=source.slice(source.indexOf('  function runOperationRefresh('),source.indexOf('  function refreshOperationFacts('));
test('overlapping polls coalesce while a different Run retains independent ownership',async()=>{
 const pending=[];
 const context={state:{resultGeneration:1},refreshResultDataUnlocked:()=>new Promise(resolve=>pending.push(resolve))};
 vm.createContext(context);vm.runInContext(wrapper,context);
 const first=context.refreshResultData('one',1);
 assert.equal(context.refreshResultData('one',1),first);
 assert.equal(pending.length,1);
 const other=context.refreshResultData('two',2);
 pending[0]();await first;
 assert.equal(context.refreshResultData('two',2),other);
 pending[1]();await other;
 assert.equal(context.state.resultRefreshInFlight,null);
});
test('initial diagnostics loading is not invalidated by the next poll',async()=>{
 const context={state:{}};vm.createContext(context);vm.runInContext(operationWrapper,context);
 let resolve;const first=context.runOperationRefresh('run',1,()=>new Promise(r=>resolve=r));
 const second=context.runOperationRefresh('run',1,()=>{throw Error('duplicate diagnostics load')});
 assert.equal(first,second);resolve();await first;
 assert.equal(context.state.operationRefreshInFlight,null);
});
