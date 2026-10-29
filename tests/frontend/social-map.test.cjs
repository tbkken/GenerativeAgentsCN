const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const workspace=fs.readFileSync('src/generative_agents/adapters/web/static/resources/map-workspace.js','utf8');

test('creating a social map sends its chosen presentation and rules without geometric fields',async()=>{
  const values={newMapName:'讨论区',newMapKey:'',newMapEnvironmentType:'social_1d',newMapPresentation:'forum',newMapRules:'讨论图书',newMapDescription:'读书交流',newMapSource:'',newMapBlueprint:''};
  const submitted=[];
  const context={document:{getElementById:id=>{
    if(!(id in values)) throw Error(`social creation unexpectedly needs ${id}`);
    return {value:values[id]};
  }},normalizeOptionalMapKey:()=>null,request:async(path,options)=>{submitted.push(JSON.parse(options.body));return {id:'map',map_key:'map'};},modal(){},notify(){}};
  vm.createContext(context);
  const method=workspace.slice(workspace.indexOf('    async create()'),workspace.indexOf('    async savePublic('));
  vm.runInContext(`this.manager={${method}}`,context);
  context.manager.loadMaps=async()=>{};context.manager.openMap=async()=>{};
  await context.manager.create();
  assert.equal(submitted[0].environment_type,'social_1d'); assert.equal(submitted[0].presentation,'forum');
  assert.equal(submitted[0].rules,'讨论图书');
  assert.equal('width' in submitted[0],false);assert.equal('height' in submitted[0],false);assert.equal('tile_size' in submitted[0],false);
});

test('social editor preserves later edits across save acknowledgments and does not recreate focused inputs',()=>{
  const nodes=new Map();let renders=0;
  const root={querySelector:id=>{
    if(!nodes.has(id)) nodes.set(id,{addEventListener:(_event,handler)=>{nodes.get(id).input=handler;}});
    return nodes.get(id);
  },dispatchEvent(){},set innerHTML(value){renders++;},replaceChildren(){}};
  const context={window:{},structuredClone,CustomEvent:class{}};
  vm.createContext(context);vm.runInContext(fs.readFileSync('src/generative_agents/adapters/web/static/resources/social-map-editor.js','utf8'),context);
  const editor=new context.window.SocialMapEditor(root);
  editor.setWorld({world_key:'group',world_name:'群聊',definition:{environment_type:'social_1d',presentation:'chat',description:'原说明',rules:''}});
  nodes.get('#socialMapRules').input({target:{value:'第一次编辑'}});
  const saved=editor.getWorld(), revision=editor.changeRevision;
  nodes.get('#socialMapRules').input({target:{value:'保存期间的新编辑'}});
  editor.acceptSavedWorld(saved,revision);
  assert.equal(editor.getWorld().definition.rules,'保存期间的新编辑');assert.equal(editor.changed,true);
  const beforeRenders=renders;
  editor.acceptSavedWorld(editor.getWorld(),editor.changeRevision);
  assert.equal(editor.changed,false);assert.equal(renders,beforeRenders);
  editor.setReadOnly(true);
  nodes.get('#socialMapRules').input({target:{value:'不可写'}});
  assert.equal(editor.getWorld().definition.rules,'保存期间的新编辑');
});
