const {test} = require('node:test');
const assert = require('node:assert/strict');
const {GAReplayPlayer} = require('../../generative_agents/web/static/replay-player.js');
const node = {material_slice_id: 'default', state_appearance: {cases: [{value: '已整理', material_slice_id: 'tidy'}, {value: '1', material_slice_id: 'one'}]}};
test('visual matching is exact and does not coerce or normalize states', () => {
  assert.equal(GAReplayPlayer.stateMaterial(node, {state:'已整理'}), 'tidy');
  for (const state of [undefined, '', ' 已整理', '已整理 ', 1, true, null, 'unknown']) assert.equal(GAReplayPlayer.stateMaterial(node,{state}), 'default');
  assert.equal(GAReplayPlayer.stateMaterial(node,{state:'1'}), 'one');
});
test('seeking reconstructs appearance from committed facts and reverses to initial state', () => {
  const player = new GAReplayPlayer(); const calls = [];
  player.worldObjects.set('desk', {initialState:{}, updateVisual: state => calls.push(GAReplayPlayer.stateMaterial(node,state))});
  player.windows.set(1,[{step_no:1,domain_events:[]},{step_no:2,domain_events:[{event_type:'GAME_OBJECT_STATE_CHANGED',payload:{structured_payload:{object_key:'desk',after:{state:'已整理'}}}}]}]);
  player._renderWorldState(2); player._renderWorldState(1); player._renderWorldState(2);
  assert.deepEqual(calls,['tidy','default','tidy']);
});
test('editor places visual inside node bounds, independent of material source footprint', () => {
  global.window = {}; require('../../generative_agents/web/static/map-editor-v2.js');
  const editor=Object.create(window.MapEditorV2.prototype); editor.depth=4;
  editor.document={hierarchy_nodes:[{id:'desk',kind:'GAME_OBJECT',bounds:{x:3,y:4,width:2,height:2},material_slice_id:'default'}]};
  editor.nodeMaterialSlice=()=>({id:'default',grid_rect:{width:40,height:40}});
  editor.nodeDisplayBounds=node=>node.bounds; editor.sliceById=new Map(); editor.sliceRotation=()=>0; let args;
  editor.drawSliceRect=(...input)=>args=input; editor.drawWorldMaterials({},16);
  assert.deepEqual(args.slice(3,7),[48,64,32,32]);
});
