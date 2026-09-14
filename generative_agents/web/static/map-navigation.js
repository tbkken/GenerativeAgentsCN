/* Navigation editing uses the existing map canvas, inspector and save history. */
(function () {
  'use strict';
  const copy = value => JSON.parse(JSON.stringify(value));
  class MapNavigationEditor {
    constructor(editor) {
      this.editor = editor; this.active = false; this.tool = 'block'; this.opacity = .5;
      this.generation = 0; this.scopeKey = ''; this.preview = null; this.start = null; this.end = null;
      this.status = '选择两点可检查通路'; this.selected = null;
      const root = editor.root;
      root.querySelector('[data-navigation]').onclick = () => {
        this.active = !this.active; this.preview = null; this.scopeKey = '';
        editor.tool = this.active ? 'navigation' : editor.workspace==='world' ? 'world' : editor.isCanvasEditing() ? editor.mapTool : 'slice';
        editor.materialPan = false;
        if (this.active && editor.workspace === 'materials' && !editor.isCanvasEditing()) {
          editor.materialView = 'slice'; editor.editingSlice = false;
        }
        editor.renderAll(); requestAnimationFrame(() => editor.fit());
      };
      root.addEventListener('map-editor-v2:change', () => {
        if (this.active) this.schedule();
      });
    }
    reset() {
      this.generation++; clearTimeout(this.timer); this.abort?.abort();
      this.active = false; this.preview = null; this.scopeKey = ''; this.start = this.end = this.selected = null;
      this.stroke = null; this.geometries = new Map();
    }
    scope() {
      const e = this.editor;
      if (!e.document) return null;
      if (e.workspace === 'world') {
        return { key: 'world', width: Number(e.document.import_metadata.width), height: Number(e.document.import_metadata.height), slice: null };
      }
      const slice = e.sliceById.get(e.currentMaterialCanvas()?.slice_id || e.selectedSliceId);
      if (!slice || (!e.isCanvasEditing() && e.materialView !== 'slice')) return null;
      return { key: slice.id, width: slice.grid_rect.width, height: slice.grid_rect.height, slice };
    }
    initialize() {
      const e = this.editor;
      if (!e.document || e.document.navigation) return;
      const width = Number(e.document.import_metadata.width);
      e.document.navigation = { base_blocked: (e.world?.definition?.tiles || [])
        .filter(t => t.collision === true).map(t => t.coord[1] * width + t.coord[0]), overrides: {} };
    }
    remapMasks() {
      const e=this.editor; this.geometries ||= new Map();
      if(this.geometryDocument!==e.document){this.geometries.clear();this.geometryDocument=e.document;}
      for(const slice of e.document?.material_slices || []){
        const next=slice.grid_rect,old=this.geometries.get(slice.id);
        if(old && JSON.stringify(old)!==JSON.stringify(next)){
          const cells={};
          for(const [i,value]of Object.entries(slice.collision_cells || {})){
            const x=Number(i)%old.width+old.x-next.x,y=Math.floor(Number(i)/old.width)+old.y-next.y;
            if(x>=0&&y>=0&&x<next.width&&y<next.height)cells[y*next.width+x]=value;
          }
          slice.collision_cells=cells;
          e.undoStack=e.undoStack.filter(entry=>entry.kind!=='navigation'||entry.scope!==slice.id);
          e.redoStack=e.redoStack.filter(entry=>entry.kind!=='navigation'||entry.scope!==slice.id);
        }
        this.geometries.set(slice.id,copy(next));
      }
    }
    values(scope = this.scope()) {
      this.initialize();
      return scope.slice ? (scope.slice.collision_cells ||= {}) : this.editor.document.navigation.overrides;
    }
    synchronize() {
      const e = this.editor, scope = this.scope(); this.initialize();
      const button = e.root.querySelector('[data-navigation]');
      button.textContent = e.workspace === 'world' ? '通路' : '碰撞';
      button.disabled = !scope; button.classList.toggle('active', this.active);
      button.setAttribute('aria-pressed', String(this.active));
      if (!this.active || !scope) return;
      if (scope.key !== this.scopeKey) {
        this.scopeKey = scope.key; this.start = this.end = this.selected = null; this.preview = null;
        this.status = '选择两点可检查通路'; this.schedule();
      }
      e.root.querySelector('[data-map-tools]').hidden = true;
      e.root.querySelector('[data-material-tools]').hidden = true;
      e.root.querySelector('[data-map-history]').hidden = false;
      this.renderInspector();
    }
    renderInspector() {
      const e = this.editor, scope = this.scope(); if (!this.active || !scope) return false;
      e.root.querySelector('[data-inspector-title]').textContent = scope.slice ? '素材碰撞' : '通路配置';
      e.root.querySelector('[data-inspector-kind]').textContent = scope.slice ? '局部格' : '世界';
      e.inspector.innerHTML = `<div class="me2-form-section"><div class="me2-section-title"><strong>编辑工具</strong><span>${scope.width} × ${scope.height} 格</span></div>
        <div class="me2-navigation-tools">${[['block','阻挡'],['walk','可走'],['restore',scope.slice?'恢复素材组合':'恢复素材结果'],['path','路径检查']].map(([key,label]) => `<button class="me2-outline ${this.tool===key?'active':''}" data-nav-tool="${key}" ${e.readonly && key!=='path'?'disabled':''} aria-pressed="${this.tool===key}">${label}</button>`).join('')}</div></div>
        <div class="me2-form-section"><label>叠层透明度 <output data-nav-opacity-label>${Math.round(this.opacity*100)}%</output><input type="range" min="15" max="85" value="${this.opacity*100}" data-nav-opacity></label></div>
        <div class="me2-property-list"><div><span>当前格</span><strong data-nav-cell>—</strong></div><div><span>通行结果</span><strong data-nav-state>—</strong></div><div><span>配置来源</span><strong data-nav-source>—</strong></div></div>
        <div class="me2-form-section"><div class="me2-section-title"><strong>两点路径</strong><span>上下左右</span></div><div class="me2-four-fields"><label>A · 起点<input class="control" data-nav-start readonly value="${this.start?.join(', ') || '点击画布选择'}"></label><label>B · 终点<input class="control" data-nav-end readonly value="${this.end?.join(', ') || '点击画布选择'}"></label></div><p class="me2-inspector-note" data-nav-result role="status"></p></div>
        <div class="me2-inspector-note">${scope.slice?'在未旋转的切片网格上配置；放置时随素材旋转。':'阻挡格用于墙体、湖泊等障碍；可走格可以打开门洞。地图手工配置优先于素材。'}<br>拖动绘制；按住空格拖动画布。</div>
        ${e.readonly?'':'<div class="me2-navigation-save"><button class="me2-save" data-nav-save>保存</button><span data-nav-save-status></span></div>'}`;
      e.inspector.querySelectorAll('[data-nav-tool]').forEach(b => b.onclick = () => {
        this.tool = b.dataset.navTool; this.renderInspector();
      });
      e.inspector.querySelector('[data-nav-opacity]').oninput = event => {
        this.opacity = Number(event.target.value)/100;
        e.inspector.querySelector('[data-nav-opacity-label]').textContent = `${event.target.value}%`; e.renderCanvas();
      };
      e.inspector.querySelector('[data-nav-save]')?.addEventListener('click', () => {
        e.root.dispatchEvent(new CustomEvent('map-editor-v2:save', { bubbles: true }));
      });
      this.details(); return true;
    }
    details() {
      const e = this.editor, scope = this.scope(); if (!scope) return;
      const set = (selector, text) => { const n = e.inspector.querySelector(selector); if(n)n.textContent=text; };
      set('[data-nav-result]', this.status);
      set('[data-nav-save-status]', e.changed ? '有未保存修改' : '已与服务器同步');
      if (!this.selected) return;
      const [x,y] = this.selected, index = y*scope.width+x, values = this.values(scope);
      set('[data-nav-cell]', `${x}, ${y}`);
      set('[data-nav-state]', this.isBlocked(index) ? '阻挡' : '可走');
      set('[data-nav-source]', Object.hasOwn(values,index) ? '手工配置' : '素材结果');
    }
    schedule() {
      this.generation++; this.abort?.abort(); clearTimeout(this.timer);
      if (!this.active || !this.scope()) return;
      if (this.preview) this.preview.path = [];
      this.status = this.start && this.end ? '正在检查当前编辑…' : '点击「路径检查」，再选择 A、B';
      this.details(); this.timer = setTimeout(() => this.refresh(), 140);
    }
    async refresh() {
      const e = this.editor, scope = this.scope(); if (!this.active || !scope) return;
      const generation = ++this.generation, revision = e.changeRevision, document = e.document;
      this.abort?.abort(); this.abort = new AbortController();
      try {
        const response = await fetch('/api/studio/resources/map-editor/navigation', {
          method:'POST', headers:{'Content-Type':'application/json'}, signal:this.abort.signal,
          body:JSON.stringify({world:e.getWorld(),slice_id:scope.slice?.id || null,start:this.start,end:this.end}),
        });
        const value = await response.json();
        if (generation!==this.generation || document!==e.document || revision!==e.changeRevision || scope.key!==this.scope()?.key || !this.active) return;
        if (!response.ok) throw new Error(value.detail?.message || value.detail || '通路校验失败');
        this.preview = {...value, blockedSet:new Set(value.blocked), inheritedSet:new Set(value.inherited_blocked || value.blocked)};
        const messages = {START_BLOCKED:'起点位于阻挡格',END_BLOCKED:'终点位于阻挡格',START_OUT_OF_BOUNDS:'起点超出范围',END_OUT_OF_BOUNDS:'终点超出范围',UNREACHABLE:'不可达：两点不连通'};
        this.status = value.status==='REACHABLE' ? `可达 · ${value.distance_tiles} 格` : messages[value.status] || '点击「路径检查」，再选择 A、B';
        this.details(); e.renderCanvas();
      } catch(error) {
        if (generation!==this.generation || error.name==='AbortError') return;
        this.status = String(error.message); this.preview = null; this.details(); e.renderCanvas();
      }
    }
    isBlocked(index) {
      const values=this.values();
      return Object.hasOwn(values,index) ? values[index] : Boolean(this.preview?.inheritedSet.has(index));
    }
    metrics() {
      const e=this.editor,s=this.scope(); if(!s)return null;
      if (!s.slice || e.isCanvasEditing()) return {x:e.renderTile,y:e.renderTile};
      return {x:s.slice.pixel_rect.width/s.width,y:s.slice.pixel_rect.height/s.height};
    }
    point(point) {
      const e=this.editor,s=this.scope(),m=this.metrics(); if(!s||!m)return null;
      const x=Math.floor((point.x-e.offsetX)/e.zoom/m.x),y=Math.floor((point.y-e.offsetY)/e.zoom/m.y);
      return x>=0&&y>=0&&x<s.width&&y<s.height ? [x,y] : null;
    }
    pointerDown(point) {
      if(!this.active||!this.scope())return false;
      const p=this.point(point); if(!p)return true;
      this.selected=p;
      if(this.tool==='path') {
        if(!this.start||this.end){this.start=p;this.end=null;}else this.end=p;
        this.schedule();this.renderInspector();this.editor.renderCanvas();return true;
      }
      if(this.editor.readonly)return true;
      this.generation++;this.abort?.abort();clearTimeout(this.timer);
      this.stroke={before:copy(this.values()),scope:this.scope().key,last:p};
      this.paint(p);return true;
    }
    paint(p) {
      const e=this.editor,s=this.scope(),v=this.values();
      // Interpolate fast pointer movement so a continuous wall has no gaps.
      const from=this.stroke.last,steps=Math.max(Math.abs(p[0]-from[0]),Math.abs(p[1]-from[1]),1);
      for(let i=0;i<=steps;i++) {
        const x=Math.round(from[0]+(p[0]-from[0])*i/steps),y=Math.round(from[1]+(p[1]-from[1])*i/steps),index=y*s.width+x;
        if(this.tool==='restore')delete v[index];else v[index]=this.tool==='block';
      }
      this.stroke.last=p;this.selected=p;
      if(this.preview)this.preview.path=[];
      this.status='通路已修改，松开后重新检查';this.details();e.renderCanvas();
    }
    pointerMove(point) { if(!this.stroke)return false;const p=this.point(point);if(p)this.paint(p);return true; }
    pointerUp() {
      if(!this.stroke)return false;
      const e=this.editor,entry={kind:'navigation',label:'通路绘制',scope:this.stroke.scope,before:this.stroke.before,after:copy(this.values())};
      this.stroke=null;
      if(JSON.stringify(entry.before)!==JSON.stringify(entry.after)){
        e.undoStack.push(entry);e.redoStack=[];e.changed=true;e.updateMapHistoryControls();
      }
      this.schedule();
      return true;
    }
    applyHistory(entry,side) {
      if(entry.kind!=='navigation')return false;
      const e=this.editor;
      if(entry.scope==='world')e.document.navigation.overrides=copy(entry[side]);
      else {const slice=e.sliceById.get(entry.scope);if(slice)slice.collision_cells=copy(entry[side]);}
      e.changed=true;this.schedule();e.renderAll();return true;
    }
    draw(ctx) {
      const e=this.editor,s=this.scope(),m=this.metrics();if(!this.active||!s||!m)return;
      ctx.save();ctx.translate(e.offsetX,e.offsetY);ctx.scale(e.zoom,e.zoom);
      const x0=Math.max(0,Math.floor(-e.offsetX/e.zoom/m.x)),y0=Math.max(0,Math.floor(-e.offsetY/e.zoom/m.y));
      const x1=Math.min(s.width,Math.ceil((e.viewportWidth-e.offsetX)/e.zoom/m.x)),y1=Math.min(s.height,Math.ceil((e.viewportHeight-e.offsetY)/e.zoom/m.y));
      const values=this.values();
      for(let y=y0;y<y1;y++)for(let x=x0;x<x1;x++){
        const i=y*s.width+x,blocked=this.isBlocked(i);ctx.globalAlpha=this.opacity;
        if(blocked || values[i]===false){ctx.fillStyle=blocked?'#c96a60':'#4baf87';ctx.fillRect(x*m.x,y*m.y,m.x,m.y);}
        ctx.globalAlpha=1;ctx.lineWidth=1/e.zoom;
        if(blocked){ctx.strokeStyle='#99443d';ctx.beginPath();ctx.moveTo((x+.15)*m.x,(y+.85)*m.y);ctx.lineTo((x+.85)*m.x,(y+.15)*m.y);ctx.stroke();}
        if(m.x*e.zoom>6){ctx.strokeStyle='rgba(43,83,67,.2)';ctx.strokeRect(x*m.x,y*m.y,m.x,m.y);}
      }
      const path=this.preview?.path||[];if(path.length){ctx.strokeStyle='#2778bd';ctx.lineWidth=3/e.zoom;ctx.beginPath();path.forEach(([x,y],i)=>i?ctx.lineTo((x+.5)*m.x,(y+.5)*m.y):ctx.moveTo((x+.5)*m.x,(y+.5)*m.y));ctx.stroke();}
      [[this.start,'A'],[this.end,'B']].forEach(([p,label])=>{if(!p)return;ctx.fillStyle='#205c80';ctx.beginPath();ctx.arc((p[0]+.5)*m.x,(p[1]+.5)*m.y,10/e.zoom,0,Math.PI*2);ctx.fill();ctx.fillStyle='#fff';ctx.font=`600 ${12/e.zoom}px sans-serif`;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(label,(p[0]+.5)*m.x,(p[1]+.5)*m.y);});
      if(this.selected){ctx.strokeStyle='#173e35';ctx.lineWidth=2/e.zoom;ctx.strokeRect(this.selected[0]*m.x,this.selected[1]*m.y,m.x,m.y);}
      ctx.restore();
    }
  }
  window.MapNavigationEditor=MapNavigationEditor;
})();
