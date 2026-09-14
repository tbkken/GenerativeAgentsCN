const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('generative_agents/web/static/console-api.js','utf8');
const escapeHtml=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
function environment(){
 const elements={};const $=id=>elements[id]||=( {dataset:{},checked:true,value:'',textContent:'',innerHTML:''});
 return {elements,$,escapeHtml,document:{querySelector:()=>({dataset:{}})},state:{selectedRunId:'run',resultGeneration:1,timeline:{requested_steps:4},replayPlayer:{availableStep:2,manifest:{world:{definition:{editor_v2:{hierarchy_nodes:[{id:'desk',name:'书桌'}]}}}}}},formatTime:value=>value,syncReplayControls:()=>{},renderReplayMarkers:()=>{}};
}
test('quality target remains a real button while untrusted Agent names are escaped',()=>{
 const context=environment();vm.createContext(context);vm.runInContext(source.slice(source.indexOf('  function renderRunQuality('),source.indexOf('  function applyRunActivity(')),context);
 context.renderRunQuality({issues:[{step_no:2,agent_key:'<bad>',code:'WARN'}]});
 const html=context.$('runQualityBanner').innerHTML;
 assert.match(html,/<button type="button" class="quality-step-link" data-quality-step="2">/);
 assert.match(html,/&lt;bad&gt;/);
});
test('Replay renders a stable total and human state summary with expandable evidence',()=>{
 const context=environment();context.state.replayMarkerFacts=new Map();
 vm.createContext(context);vm.runInContext(source.slice(source.indexOf('  function renderReplayStep('),source.indexOf('  function renderReplayMarkers(')),context);
 context.renderReplayStep({availableStep:2,step:{step_no:2,virtual_time:'09:01',conversations:[],domain_events:[{event_type:'GAME_OBJECT_STATE_CHANGED',payload:{structured_payload:{object_key:'desk',after:{state:'已整理'}}}}]}},'run',1);
 assert.equal(context.$('timelineStep').textContent,'Step 2 / 4');
 assert.match(context.$('timelineStreamItems').innerHTML,/<span>书桌：已整理<\/span>/);
 assert.match(context.$('timelineStreamItems').innerHTML,/<details><summary>查看事实详情<\/summary>/);
});


test('skipped business evaluation is not displayed as unavailable or passed',()=>{
 const context=environment();vm.createContext(context);vm.runInContext(source.slice(source.indexOf('  function renderRunQuality('),source.indexOf('  function applyRunActivity(')),context);
 context.renderRunQuality({quality_status:'UNKNOWN',summary:'质量评估不可用',issues:[],evaluator:{status:'SKIPPED'}});
 const html=context.$('runQualityBanner').innerHTML;
 assert.match(html,/基础诊断未发现告警；本次未执行业务评估/);
 assert.doesNotMatch(html,/评估不可用|行为质量：通过/);
});
