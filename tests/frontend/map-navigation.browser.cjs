// Run with navigation_fixture on 127.0.0.1:8876 and Playwright on NODE_PATH.
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1280,height:800}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8876/');
  await page.waitForFunction(()=>window.editor?.document?.navigation);
  await page.locator('[data-navigation]').click();
  await page.waitForFunction(()=>!!editor.navigation.preview);
  const clickTile=async(x,y)=>{const p=await page.evaluate(([x,y])=>{const r=editor.canvas.getBoundingClientRect(),m=editor.navigation.metrics();return {x:r.x+editor.offsetX+(x+.5)*m.x*editor.zoom,y:r.y+editor.offsetY+(y+.5)*m.y*editor.zoom}},[x,y]);await page.mouse.click(p.x,p.y);};
  await page.locator('[data-nav-tool="path"]').click();await clickTile(1,2);await clickTile(5,2);
  await page.waitForFunction(()=>editor.navigation.status.startsWith('不可达'));
  await page.locator('[data-nav-tool="walk"]').click();await clickTile(3,2);
  await page.waitForFunction(()=>editor.navigation.status.startsWith('可达'));
  assert.equal(await page.evaluate(()=>editor.getWorld().definition.editor_v2.navigation.overrides['17']),false);
  await page.locator('[data-map-undo]').click();await page.waitForFunction(()=>editor.navigation.status.startsWith('不可达'));
  await page.locator('[data-map-redo]').click();await page.waitForFunction(()=>editor.navigation.status.startsWith('可达'));
  await page.screenshot({path:'output/navigation-world.png'});
  // Restore server-saved author content into a newly loaded editor.
  await page.evaluate(()=>{const saved=editor.getWorld();editor.setWorld(saved);});
  await page.locator('[data-navigation]').click();await page.waitForFunction(()=>!!editor.navigation.preview);
  assert.equal(await page.evaluate(()=>editor.navigation.isBlocked(17)),false);
  await page.locator('[data-navigation]').click();
  await page.locator('[data-me2-tab="materials"]').click();
  await page.locator('[data-new-canvas]').click();
  await page.waitForFunction(()=>editor.isCanvasEditing());
  await page.screenshot({path:'output/navigation-canvas.png'});
  await page.locator('[data-navigation]').click();await page.waitForFunction(()=>!!editor.navigation.preview);
  await page.locator('[data-nav-tool="block"]').click();await clickTile(0,0);
  await page.waitForFunction(()=>editor.navigation.preview?.blocked.includes(0));
  await clickTile(1,1);
  const oldWidth=await page.evaluate(()=>editor.currentMaterialCanvas().width_tiles);
  // Exiting collision mode restores the normal canvas inspector, including size fields.
  await page.locator('[data-navigation]').click();
  await page.locator('[data-canvas-width]').fill(String(oldWidth+2));
  await page.locator('[data-save-canvas]').click();
  assert.equal(await page.evaluate(()=>editor.sliceById.get(editor.currentMaterialCanvas().slice_id).collision_cells[editor.currentMaterialCanvas().width_tiles+1]),true);
  for(const width of [1280,1024,800,480]){
   await page.setViewportSize({width,height:800});await page.waitForTimeout(50);
   for(const selector of ['[data-canvas-width]','[data-canvas-height]','[data-save-canvas]']){
    const field=page.locator(selector);await field.scrollIntoViewIfNeeded();
    const box=await field.boundingBox();assert(box && box.x>=0 && box.x+box.width<=width+.5,selector+' clipped at '+width);
   }
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  }
  await page.setViewportSize({width:1024,height:800});
  await page.screenshot({path:'output/navigation-canvas-1024.png'});
  // A delayed result from an old document cannot repaint a newly selected map.
  await page.locator('[data-navigation]').click();await page.waitForFunction(()=>!!editor.navigation.preview);
  const staleIgnored=await page.evaluate(async()=>{
   const original=window.fetch;let release;
   window.fetch=()=>new Promise(resolve=>{release=resolve;});
   const pending=editor.navigation.refresh();editor.setWorld(editor.getWorld());
   release(new Response(JSON.stringify({width:999,height:999,blocked:[0],status:'REACHABLE',distance_tiles:999,path:[]})));
   await pending;window.fetch=original;return editor.navigation.preview===null;
  });assert(staleIgnored);
  assert.deepEqual(errors,[]);console.log('PASS: actual editor paints, checks paths, undoes/redoes, reloads, edits canvas collision; fields accessible at 1280/1024/800/480');
 } finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
