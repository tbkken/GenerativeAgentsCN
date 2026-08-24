(function () {
  'use strict';

  const VILLAGE_URL = '/generative_agents/frontend/static/assets/village/';
  const LEVEL_ORDER = { MAP: 1, SECTOR: 2, ARENA: 3, GAME_OBJECT: 4 };
  const LEVEL_LABEL = { WORLD: 'World', SECTOR: 'Sector', ARENA: 'Arena', GAME_OBJECT: 'Game Object' };
  const LEVEL_MARK = { WORLD: 'W', SECTOR: 'S', ARENA: 'A', GAME_OBJECT: 'O' };
  const LEVEL_COLOR = { WORLD: '#355f58', SECTOR: '#337f73', ARENA: '#9a6b2f', GAME_OBJECT: '#73558d' };
  const GID_MASK = 0x1fffffff;
  const MATERIAL_GRID_SIZE = 32;
  const deepClone = value => JSON.parse(JSON.stringify(value));
  const escapeHtml = value => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  const uid = prefix => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

  function loadImage(url) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.decoding = 'async';
      image.onload = () => resolve(image);
      image.onerror = reject;
      image.src = url;
    });
  }

  class MapEditorV2 {
    constructor(root) {
      this.root = root;
      this.world = null;
      this.document = null;
      this.readonly = false;
      this._changed = false;
      this._changeRevision = 0;
      this._changeNotificationQueued = false;
      this.images = new Map();
      this.imageUrls = new Map();
      this.sliceTransparency = new Map();
      this.sourceById = new Map();
      this.sliceById = new Map();
      this.sliceByGid = new Map();
      this.nodeById = new Map();
      this.childrenByParent = new Map();
      this.layerUsage = new Map();
      this.passiveSkillCatalog = [];
      this.workspace = 'world';
      this.depth = 4;
      this.semanticVisible = false;
      this.selectedNodeId = '';
      this.nodeMaterialPreview = null;
      this.expandedNodes = new Set();
      this.expandedSources = new Set();
      this.selectedSourceId = '';
      this.selectedSliceId = '';
      this.selectedCanvasId = '';
      this.materialView = 'source';
      this.editingSlice = false;
      this.sliceRotationPreview = null;
      this.selectedPaintSliceId = '';
      this.brushPaletteOpen = false;
      this.brushFilter = '';
      this.expandedBrushSources = new Set();
      this.brushCloseTimer = null;
      this.mapTool = 'brush';
      this.tool = 'brush';
      this.materialPan = false;
      this.undoStack = [];
      this.redoStack = [];
      this.activeMapEdit = null;
      this.zoom = 1;
      this.offsetX = 20;
      this.offsetY = 20;
      this.drag = null;
      this.spaceDown = false;
      this.treeScroll = { world: 0, materials: 0 };
      this.activeScrollKey = 'world';
      this.expandedMaterialGroups = new Set(['canvases', 'sources']);
      this.viewportWidth = 900;
      this.viewportHeight = 620;
      this.renderTile = 16;
      this.buildShell();
      this.bind();
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(this.canvasHost);
      this.ready = this.loadDocument();
    }

    get changed() { return this._changed; }
    get changeRevision() { return this._changeRevision; }

    set changed(value) {
      const next = Boolean(value);
      this._changed = next;
      if (!next) return;
      this._changeRevision = Number(this._changeRevision || 0) + 1;
      if (this._changeNotificationQueued || !this.root?.dispatchEvent || typeof CustomEvent === 'undefined') return;
      this._changeNotificationQueued = true;
      queueMicrotask(() => {
        this._changeNotificationQueued = false;
        if (!this._changed) return;
        this.root.dispatchEvent(new CustomEvent('map-editor-v2:change', {
          bubbles: true,
          detail: { revision: this._changeRevision },
        }));
      });
    }

    buildShell() {
      const legacyTabs = this.root.previousElementSibling;
      if (legacyTabs?.classList.contains('map-editor-tabs')) legacyTabs.hidden = true;
      this.root.className = 'map-editor-v2';
      this.root.innerHTML = `
        <nav class="me2-tabs" aria-label="地图工作区">
          <button class="active" data-me2-tab="world"><span>◎</span>世界</button>
          <button data-me2-tab="materials"><span>◇</span>素材</button>
        </nav>
        <div class="me2-layout">
          <aside class="me2-left">
            <header class="me2-pane-head"><strong data-left-title>地图底图</strong><span data-left-count></span></header>
            <div class="me2-left-tools" data-left-tools></div>
            <div class="me2-tree-scroll" data-left-content><div class="me2-loading">正在构建 Ville…</div></div>
          </aside>
          <section class="me2-stage">
            <div class="me2-toolbar">
              <div class="me2-tool-group" data-map-tools hidden>
                <div class="me2-brush-menu" data-brush-menu>
                  <button class="active" data-me2-tool="brush" data-brush-trigger>✎ 画笔</button>
                  <div class="me2-brush-popover" data-brush-popover hidden></div>
                </div>
                <button data-me2-tool="fill">▨ 填充</button>
                <button data-me2-tool="eraser">⌫ 橡皮</button>
                <button data-me2-tool="pan">✋ 拖动画布</button>
              </div>
              <div class="me2-tool-group me2-history-tools" data-map-history hidden>
                <button data-map-undo title="撤回上一次画布绘制" disabled>↶ 撤回</button>
                <button data-map-redo title="重做上一次画布绘制" disabled>↷ 重做</button>
              </div>
              <div class="me2-tool-group" data-material-tools hidden>
                <button data-material-pan type="button" aria-pressed="false" title="激活后拖动画布">✋ 拖动画布</button>
                <span class="me2-material-grid-hint">32 × 32 px / 格</span>
              </div>
              <span class="me2-spacer"></span>
              <label class="me2-depth"><span>显示至</span><select data-depth>
                <option value="1">第 1 层 · 地图底图</option>
                <option value="2">第 2 层 · Sector</option>
                <option value="3">第 3 层 · Arena</option>
                <option value="4" selected>第 4 层 · Game Object</option>
              </select></label>
              <button data-semantics>⌘ 语义</button>
              <span class="me2-divider"></span>
              <button data-zoom-out>−</button><span class="me2-zoom" data-zoom>100%</span>
              <button data-zoom-in>＋</button><button data-fit>适配</button>
            </div>
            <div class="me2-canvas-host" data-canvas-host>
              <canvas data-canvas></canvas>
              <div class="me2-stage-empty" data-empty hidden><span>◇</span><strong>素材载入中</strong></div>
            </div>
            <div class="me2-stage-status"><span data-context>地图底图</span><span data-pointer>—</span></div>
          </section>
          <aside class="me2-inspector">
            <header class="me2-pane-head"><strong data-inspector-title>对象检查器</strong><span data-inspector-kind></span></header>
            <div class="me2-inspector-scroll" data-inspector><div class="me2-loading">正在读取数据…</div></div>
          </aside>
        </div>
        <input type="file" accept="image/png,image/jpeg,image/webp" data-source-upload hidden />`;
      this.canvas = this.root.querySelector('[data-canvas]');
      this.context = this.canvas.getContext('2d');
      this.canvasHost = this.root.querySelector('[data-canvas-host]');
      this.leftContent = this.root.querySelector('[data-left-content]');
      this.inspector = this.root.querySelector('[data-inspector]');
    }

    bind() {
      this.root.querySelectorAll('[data-me2-tab]').forEach(button => button.addEventListener('click', () => {
        if (this.activeMapEdit) this.commitMapEdit();
        this.clearSliceRotationPreview();
        this.nodeMaterialPreview = null;
        this.treeScroll[this.workspace] = this.leftContent.scrollTop;
        this.workspace = button.dataset.me2Tab;
        this.tool = this.workspace === 'world' ? 'world' : (this.isCanvasEditing() ? this.mapTool : 'slice');
        if (this.workspace === 'materials') this.materialPan = false;
        this.brushPaletteOpen = false;
        this.root.querySelectorAll('[data-me2-tab]').forEach(item => item.classList.toggle('active', item === button));
        this.editingSlice = false;
        this.renderAll();
        requestAnimationFrame(() => this.fit());
      }));
      this.root.querySelectorAll('[data-me2-tool]').forEach(button => button.addEventListener('click', event => {
        this.tool = button.dataset.me2Tool;
        if (this.isCanvasEditing()) this.mapTool = this.tool;
        if (this.tool === 'brush' && this.isCanvasEditing()) {
          clearTimeout(this.brushCloseTimer);
          this.brushPaletteOpen = true;
          this.renderBrushPalette();
        } else {
          this.brushPaletteOpen = false;
          this.renderBrushPalette();
        }
        this.root.querySelectorAll('[data-me2-tool]').forEach(item => item.classList.toggle('active', item === button));
        this.updateCanvasCursor();
        event.stopPropagation();
      }));
      const brushMenu = this.root.querySelector('[data-brush-menu]');
      brushMenu?.addEventListener('mouseenter', () => {
        clearTimeout(this.brushCloseTimer);
        if (!this.isCanvasEditing()) return;
        this.brushPaletteOpen = true;
        this.renderBrushPalette();
      });
      brushMenu?.addEventListener('mouseleave', () => {
        clearTimeout(this.brushCloseTimer);
        this.brushCloseTimer = setTimeout(() => {
          const popover = this.root.querySelector('[data-brush-popover]');
          if (popover?.contains(document.activeElement)) return;
          this.brushPaletteOpen = false;
          this.renderBrushPalette();
        }, 180);
      });
      this.root.addEventListener('click', event => {
        if (event.target.closest('[data-brush-menu]')) return;
        if (!this.brushPaletteOpen) return;
        this.brushPaletteOpen = false;
        this.renderBrushPalette();
      });
      this.root.querySelector('[data-material-pan]').addEventListener('click', event => {
        this.materialPan = !this.materialPan;
        event.currentTarget.classList.toggle('active', this.materialPan);
        event.currentTarget.setAttribute('aria-pressed', String(this.materialPan));
        this.updateCanvasCursor();
      });
      this.root.querySelector('[data-map-undo]').addEventListener('click', () => this.undoMapEdit());
      this.root.querySelector('[data-map-redo]').addEventListener('click', () => this.redoMapEdit());
      this.root.querySelector('[data-depth]').addEventListener('change', event => {
        this.depth = Number(event.target.value);
        this.renderCanvas();
      });
      this.root.querySelector('[data-semantics]').addEventListener('click', event => {
        this.semanticVisible = !this.semanticVisible;
        event.currentTarget.classList.toggle('active', this.semanticVisible);
        this.renderCanvas();
      });
      this.root.querySelector('[data-zoom-in]').addEventListener('click', () => this.changeZoom(1.2));
      this.root.querySelector('[data-zoom-out]').addEventListener('click', () => this.changeZoom(1 / 1.2));
      this.root.querySelector('[data-fit]').addEventListener('click', () => this.fit());
      this.canvas.addEventListener('pointerdown', event => this.pointerDown(event));
      this.canvas.addEventListener('pointermove', event => this.pointerMove(event));
      this.canvas.addEventListener('pointerup', event => this.pointerUp(event));
      this.canvas.addEventListener('pointercancel', event => this.pointerUp(event));
      this.canvas.addEventListener('wheel', event => this.wheel(event), { passive: false });
      this.root.addEventListener('keydown', event => {
        const formControl = event.target.closest?.('input, textarea, select');
        if (!formControl && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
          event.preventDefault();
          if (event.shiftKey) this.redoMapEdit(); else this.undoMapEdit();
          return;
        }
        if (!formControl && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') {
          event.preventDefault(); this.redoMapEdit(); return;
        }
        if (!formControl && event.code === 'Space') { this.spaceDown = true; event.preventDefault(); }
      });
      this.root.addEventListener('keyup', event => { if (event.code === 'Space') this.spaceDown = false; });
      this.root.querySelector('[data-source-upload]').addEventListener('change', event => {
        this.importSourceFile(event.target.files?.[0]);
        event.target.value = '';
      });
      this.root.tabIndex = -1;
    }

    async loadDocument() {
      try {
        const [response, skillResponse] = await Promise.all([
          fetch('/api/v1/map-editor/ville-document'),
          fetch('/api/v1/skills?kind=atomic').catch(() => null),
        ]);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const bundled = await response.json();
        if (skillResponse?.ok) {
          const skillCatalog = await skillResponse.json();
          this.passiveSkillCatalog = (skillCatalog.items || [])
            .filter(item => (item.scripts || []).includes('scripts/main.py'));
        }
        this.bundledDocument = bundled;
        const saved = this.world?.definition?.editor_v2;
        this.document = saved?.schema_version === 'ga-map-editor/v2'
          ? deepClone(saved)
          : this.documentForWorld(this.world, bundled);
        if (!this.world || this.world.world_key === 'the-ville') this.mergeVilleAuthoringState(bundled);
        this.normalizeMaterials();
        this.reindex();
        await this.loadSourceImages();
        this.renderAll();
        requestAnimationFrame(() => this.fit());
      } catch (error) {
        this.root.querySelector('[data-empty]').hidden = false;
        this.root.querySelector('[data-empty]').innerHTML = `<span>!</span><strong>地图编辑器载入失败</strong><small>${escapeHtml(error.message)}</small>`;
      }
    }

    mergeVilleAuthoringState(bundled) {
      if (!this.document) this.document = bundled;
      const knownLayers = new Map((this.document.visual_layers || []).map(item => [item.id, item]));
      for (const layer of bundled.visual_layers || []) if (!knownLayers.has(layer.id)) this.document.visual_layers.push(layer);
      if (!Array.isArray(this.document.material_sources)) this.document.material_sources = bundled.material_sources;
      if (!Array.isArray(this.document.material_slices)) this.document.material_slices = bundled.material_slices;
      if (!Array.isArray(this.document.material_canvases)) this.document.material_canvases = [];
      if (!Array.isArray(this.document.hierarchy_nodes)) this.document.hierarchy_nodes = bundled.hierarchy_nodes;
      this.document.ui_state ||= {};
      this.document.tile_overrides ||= {};
      this.document.tile_override_parts ||= {};
      this.document.tile_override_layers ||= {};
    }

    normalizeMaterials() {
      if (!this.document) return;
      this.document.material_canvases ||= [];
      this.document.tile_overrides ||= {};
      this.document.tile_override_parts ||= {};
      this.normalizeTileOverrideLayers();
      for (const slice of this.document.material_slices || []) {
        const rotation = Number(slice.rotation_degrees || 0);
        slice.rotation_degrees = [0, 90, 180, 270].includes(rotation) ? rotation : 0;
        delete slice.purpose;
      }
      for (const canvas of this.document.material_canvases) {
        canvas.tile_size = Math.max(1, Number(canvas.tile_size || MATERIAL_GRID_SIZE));
        canvas.width_tiles = Math.max(1, Number(canvas.width_tiles || 1));
        canvas.height_tiles = Math.max(1, Number(canvas.height_tiles || 1));
        canvas.cells ||= {};
      }
    }

    normalizeTileOverrideLayers() {
      const overrides = this.document.tile_overrides ||= {};
      const parts = this.document.tile_override_parts ||= {};
      const layers = this.document.tile_override_layers ||= {};
      for (const [index, sliceId] of Object.entries(overrides)) {
        if (Array.isArray(layers[index]) && layers[index].length) continue;
        layers[index] = [{ slice_id: sliceId, part: parts[index] ? deepClone(parts[index]) : null }];
      }
      for (const [index, stack] of Object.entries(layers)) {
        if (!Array.isArray(stack) || !stack.length) { delete layers[index]; continue; }
        const top = stack[stack.length - 1];
        overrides[index] = top.slice_id;
        if (top.part) parts[index] = deepClone(top.part); else delete parts[index];
      }
    }

    documentForWorld(world, bundled = this.bundledDocument) {
      if (!bundled) return null;
      const saved = world?.definition?.editor_v2;
      if (saved?.schema_version === 'ga-map-editor/v2') return deepClone(saved);
      if (!world || world.world_key === 'the-ville') return deepClone(bundled);
      const size = Array.isArray(world.definition?.size) ? world.definition.size : [32, 48];
      const height = Math.max(1, Number(size[0]) || 32);
      const width = Math.max(1, Number(size[1]) || 48);
      const rootId = `world-${String(world.world_key || 'custom-map').slice(0, 100)}`;
      const layer = (id, name, displayLevel, zIndex) => ({
        id: `${id}-${String(world.world_key || 'custom-map').slice(0, 80)}`,
        name, display_level: displayLevel, z_index: zIndex, width, height,
        raw_gids: new Array(width * height).fill(0), cell_overrides: [],
        recipe_placements: [], visible: true, opacity: 1,
      });
      return {
        schema_version: 'ga-map-editor/v2', root_node_id: rootId,
        material_sources: [],
        material_slices: [],
        material_canvases: [],
        render_recipes: [],
        visual_layers: [
          layer('layer-map', '地图底图', 'MAP', 0),
          layer('layer-sector', 'Sector 视觉', 'SECTOR', 10),
          layer('layer-arena', 'Arena 视觉', 'ARENA', 20),
          layer('layer-game-object', 'Game Object 视觉', 'GAME_OBJECT', 30),
        ],
        hierarchy_nodes: [{
          id: rootId, kind: 'WORLD', parent_id: null,
          name: world.world_name || world.definition?.world || '未命名地图', sort_order: 0,
          bounds: { x: 0, y: 0, width, height }, semantic: '',
          material_slice_id: null, render_recipe_id: null, render_mode: 'LAYER_BACKED', skill_bindings: [], extensions: {},
        }],
        import_metadata: {
          importer: 'blank-map/v2', width, height,
          tile_size: Number(world.definition?.tile_size || 32),
          used_gid_count: 0, collision_coords: [], source_sha256: '',
        },
        tile_overrides: {}, tile_override_parts: {}, tile_override_layers: {}, ui_state: {},
      };
    }

    async loadSourceImages() {
      this.images.clear();
      this.imageUrls.clear();
      this.sliceTransparency.clear();
      const jobs = (this.document.material_sources || []).map(async source => {
        if (source.kind === 'CANVAS') return;
        const url = source.asset_id
          ? `/api/v1/assets/${encodeURIComponent(source.asset_id)}/content`
          : (source.bundled_path ? `${VILLAGE_URL}${source.bundled_path}` : '');
        if (!url) return;
        this.imageUrls.set(source.id, url);
        try { this.images.set(source.id, await loadImage(url)); } catch (_) { /* shown as missing */ }
      });
      await Promise.all(jobs);
      this.refreshCanvasImages();
    }

    currentMaterialCanvas() {
      return this.canvasById?.get(this.selectedCanvasId) || null;
    }

    isCanvasEditing() {
      return this.workspace === 'materials' && this.materialView === 'canvas' && Boolean(this.currentMaterialCanvas());
    }

    paintableMaterialSlices() {
      return [...(this.document?.material_slices || [])]
        .filter(slice => this.sourceById?.get(slice.source_id)?.kind !== 'CANVAS')
        .sort((left, right) => left.name.localeCompare(right.name, 'zh') || left.id.localeCompare(right.id));
    }

    refreshCanvasImages() {
      if (typeof document === 'undefined') return;
      const rendered = new Set();
      const rendering = new Set();
      const renderCanvas = canvas => {
        if (!canvas || rendered.has(canvas.id)) return this.images.get(canvas?.source_id);
        if (rendering.has(canvas.id)) return null;
        rendering.add(canvas.id);
        for (const layers of Object.values(canvas.cells || {})) {
          for (const layer of layers || []) {
            const slice = this.sliceById.get(layer.slice_id);
            renderCanvas(this.canvasBySourceId.get(slice?.source_id));
          }
        }
        const tileSize = Math.max(1, Number(canvas.tile_size || MATERIAL_GRID_SIZE));
        const target = document.createElement('canvas');
        target.width = canvas.width_tiles * tileSize;
        target.height = canvas.height_tiles * tileSize;
        const context = target.getContext('2d');
        context.imageSmoothingEnabled = false;
        const indexes = Object.keys(canvas.cells || {}).map(Number).sort((a, b) => a - b);
        for (const index of indexes) {
          const x = (index % canvas.width_tiles) * tileSize;
          const y = Math.floor(index / canvas.width_tiles) * tileSize;
          for (const layer of canvas.cells[index] || []) {
            const slice = this.sliceById.get(layer.slice_id);
            if (!slice) continue;
            if (layer.part) this.drawTilePart(context, slice, layer.part, x, y, tileSize);
            else this.drawTile(context, slice, slice.indexed_gid || 0, x, y, tileSize);
          }
        }
        this.images.set(canvas.source_id, target);
        try { this.imageUrls.set(canvas.source_id, target.toDataURL('image/png')); } catch (_) { /* no thumbnail only */ }
        rendering.delete(canvas.id);
        rendered.add(canvas.id);
        return target;
      };
      for (const canvas of this.document.material_canvases || []) renderCanvas(canvas);
    }

    reindex() {
      this.sourceById = new Map((this.document.material_sources || []).map(item => [item.id, item]));
      this.sliceById = new Map((this.document.material_slices || []).map(item => [item.id, item]));
      this.sliceByGid = new Map((this.document.material_slices || []).filter(item => item.indexed_gid).map(item => [item.indexed_gid, item]));
      this.canvasById = new Map((this.document.material_canvases || []).map(item => [item.id, item]));
      this.canvasBySourceId = new Map((this.document.material_canvases || []).map(item => [item.source_id, item]));
      this.canvasBySliceId = new Map((this.document.material_canvases || []).map(item => [item.slice_id, item]));
      this.nodeById = new Map((this.document.hierarchy_nodes || []).map(item => [item.id, item]));
      this.childrenByParent = new Map();
      for (const node of this.document.hierarchy_nodes || []) {
        if (!Array.isArray(node.skill_bindings)) node.skill_bindings = [];
        const list = this.childrenByParent.get(node.parent_id || '') || [];
        list.push(node); this.childrenByParent.set(node.parent_id || '', list);
      }
      for (const list of this.childrenByParent.values()) list.sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name, 'zh'));
      this.layerUsage = new Map();
      for (const layer of this.document.visual_layers || []) {
        const counts = new Map();
        for (const raw of layer.raw_gids || []) {
          const gid = Number(raw) & GID_MASK;
          if (gid) counts.set(gid, (counts.get(gid) || 0) + 1);
        }
        this.layerUsage.set(layer.id, counts);
      }
      if (!this.selectedNodeId) this.selectedNodeId = this.document.root_node_id;
      this.expandedNodes.add(this.document.root_node_id);
      if (!this.selectedSourceId) this.selectedSourceId = this.document.material_sources?.find(source => source.kind !== 'CANVAS')?.id
        || this.document.material_sources?.[0]?.id || '';
      if (this.selectedSourceId) this.expandedSources.add(this.selectedSourceId);
      const brushSlices = this.paintableMaterialSlices();
      if (!this.selectedPaintSliceId || !brushSlices.some(slice => slice.id === this.selectedPaintSliceId)) {
        this.selectedPaintSliceId = brushSlices[0]?.id || '';
      }
      const brushSourceId = this.sliceById.get(this.selectedPaintSliceId)?.source_id;
      if (brushSourceId && this.expandedBrushSources && !this.expandedBrushSources.size) {
        this.expandedBrushSources.add(brushSourceId);
      }
    }

    setWorld(world) {
      this.world = deepClone(world || {});
      this.world.definition ||= {};
      const saved = this.world.definition.editor_v2;
      if (saved?.schema_version === 'ga-map-editor/v2') {
        this.document = deepClone(saved);
      } else if (this.bundledDocument) {
        this.document = this.documentForWorld(this.world, this.bundledDocument);
        this.world.definition.editor_v2 = deepClone(this.document);
      } else this.document = null;
      this.normalizeMaterials();
      this.workspace = 'world'; this.depth = 4; this.semanticVisible = false;
      this.selectedNodeId = ''; this.nodeMaterialPreview = null; this.selectedSourceId = ''; this.selectedSliceId = '';
      this.selectedCanvasId = '';
      this.expandedNodes.clear(); this.expandedSources.clear(); this.materialView = 'source';
      this.editingSlice = false; this.sliceRotationPreview = null; this.mapTool = 'brush'; this.tool = 'world'; this.materialPan = false; this.treeScroll = { world: 0, materials: 0 };
      this.activeScrollKey = 'world'; this.brushPaletteOpen = false; this.brushFilter = ''; this.expandedBrushSources.clear();
      this.resetMapHistory();
      this.root.querySelectorAll('[data-me2-tab]').forEach(item => item.classList.toggle('active', item.dataset.me2Tab === 'world'));
      this.root.querySelectorAll('[data-me2-tool]').forEach(item => item.classList.toggle('active', item.dataset.me2Tool === 'brush'));
      if (this.document) this.reindex();
      this.loadSourceImages().then(() => { this.renderAll(); requestAnimationFrame(() => this.fit()); });
      this._changed = false;
      this._changeRevision = 0;
      this.renderAll();
    }

    acceptSavedWorld(world, savedRevision) {
      this.world = deepClone(world || {});
      if (Number(savedRevision) === this._changeRevision) this._changed = false;
    }

    getWorld() {
      const world = deepClone(this.world || {});
      world.definition ||= {};
      if (this.document) {
        world.definition.editor_v2 = deepClone(this.document);
        const tiles = Array.isArray(world.definition.tiles) ? world.definition.tiles : [];
        const byIndex = new Map(tiles.map(tile => {
          const coord = tile?.coord || [];
          return [Number(coord[1]) * Number(this.document.import_metadata.width) + Number(coord[0]), tile];
        }));
        for (const [indexText, sliceId] of Object.entries(this.document.tile_overrides || {})) {
          const tile = byIndex.get(Number(indexText));
          if (tile) {
            tile.tile = sliceId; tile.visual_slice_id = sliceId;
            const part = this.document.tile_override_parts?.[indexText];
            if (part) tile.visual_slice_part = deepClone(part); else delete tile.visual_slice_part;
          }
        }
        for (const tile of tiles) {
          const coord = tile?.coord || [];
          const node = this.nodeAt(Number(coord[0]), Number(coord[1]));
          tile.address = node ? this.nodeAddress(node) : [];
        }
      }
      return world;
    }

    get definition() { return (this.world ||= { definition: {} }).definition ||= {}; }
    get editor() { return this.definition.editor ||= { schema_version: 1, palette: [], cells: {}, spatial_assets: {} }; }
    setReadOnly(value) { this.readonly = Boolean(value); this.renderAll(); }
    renderPalette() {}
    render() { this.renderCanvas(); }

    resize() {
      const rect = this.canvasHost.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const ratio = window.devicePixelRatio || 1;
      this.viewportWidth = rect.width; this.viewportHeight = rect.height;
      this.canvas.width = Math.round(rect.width * ratio);
      this.canvas.height = Math.round(rect.height * ratio);
      this.canvas.style.width = '100%'; this.canvas.style.height = '100%';
      this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
      this.renderCanvas();
    }

    fit() {
      if (!this.document || !this.viewportWidth) return;
      let width = Number(this.document.import_metadata?.width || 140) * this.renderTile;
      let height = Number(this.document.import_metadata?.height || 100) * this.renderTile;
      if (this.workspace === 'materials') {
        const canvas = this.currentMaterialCanvas();
        if (this.materialView === 'canvas' && canvas) {
          width = canvas.width_tiles * this.renderTile;
          height = canvas.height_tiles * this.renderTile;
        } else {
        const source = this.sourceById.get(this.selectedSourceId);
        if (!source) return;
        if (this.materialView === 'slice' && this.selectedSliceId && !this.editingSlice) {
          const display = this.sliceDisplaySize(this.sliceById.get(this.selectedSliceId));
          width = display.width; height = display.height;
        } else { width = source.width_px; height = source.height_px; }
        }
      }
      this.zoom = Math.min(1.8, Math.max(.08, Math.min((this.viewportWidth - 80) / width, (this.viewportHeight - 80) / height)));
      this.offsetX = (this.viewportWidth - width * this.zoom) / 2;
      this.offsetY = (this.viewportHeight - height * this.zoom) / 2;
      this.renderCanvas();
    }

    changeZoom(factor, anchorX = this.viewportWidth / 2, anchorY = this.viewportHeight / 2) {
      const old = this.zoom;
      this.zoom = Math.min(8, Math.max(.04, this.zoom * factor));
      this.offsetX = anchorX - (anchorX - this.offsetX) * (this.zoom / old);
      this.offsetY = anchorY - (anchorY - this.offsetY) * (this.zoom / old);
      this.renderCanvas();
    }

    renderAll() {
      if (!this.document) return;
      this.renderLeft();
      this.renderInspector();
      const material = this.workspace === 'materials';
      const canvasEditing = this.isCanvasEditing();
      this.root.querySelector('[data-map-tools]').hidden = !canvasEditing;
      this.root.querySelector('[data-material-tools]').hidden = !material || canvasEditing;
      const materialPan = this.root.querySelector('[data-material-pan]');
      materialPan.classList.toggle('active', material && !canvasEditing && this.materialPan);
      materialPan.setAttribute('aria-pressed', String(material && !canvasEditing && this.materialPan));
      this.root.querySelectorAll('[data-me2-tool]').forEach(button => {
        button.hidden = !canvasEditing;
        button.classList.toggle('active', button.dataset.me2Tool === this.tool);
      });
      this.root.querySelector('[data-map-history]').hidden = !canvasEditing;
      this.root.querySelector('.me2-depth').hidden = material;
      this.root.querySelector('[data-semantics]').hidden = material;
      this.renderBrushPalette();
      this.updateCanvasCursor();
      this.updateMapHistoryControls();
      this.renderCanvas();
    }

    renderLeft() {
      const title = this.root.querySelector('[data-left-title]');
      const count = this.root.querySelector('[data-left-count]');
      const tools = this.root.querySelector('[data-left-tools]');
      const scrollKey = this.workspace;
      if (this.activeScrollKey === scrollKey) this.treeScroll[scrollKey] = this.leftContent.scrollTop;
      this.activeScrollKey = scrollKey;
      if (this.workspace === 'world') {
        title.textContent = '四层地址树'; count.textContent = `${this.document.hierarchy_nodes.length} 个节点`;
        tools.innerHTML = `<div class="me2-search"><span>⌕</span><input data-tree-search placeholder="搜索地址" /></div>`;
        this.leftContent.innerHTML = `<div class="me2-address-tree">${this.nodeTree(this.nodeById.get(this.document.root_node_id), 0)}</div>`;
        this.bindWorldTree();
      } else if (this.workspace === 'materials') {
        title.textContent = '素材'; count.textContent = `${this.document.material_slices.length} 个可用素材`;
        tools.innerHTML = `<div class="me2-left-actions"><button class="me2-primary-soft" data-new-canvas title="${this.readonly ? '点击后自动创建新修订并新建画布' : '新建可绘制画布'}">＋ 新建画布</button><button class="me2-outline" data-upload-source ${this.readonly ? 'disabled' : ''}>＋ 导入原图</button></div>`;
        this.leftContent.innerHTML = `<div class="me2-material-tree">${this.materialTree()}</div>`;
        tools.querySelector('[data-new-canvas]')?.addEventListener('click', () => {
          if (this.readonly) this.requestEditableAction('new-canvas');
          else this.createMaterialCanvas();
        });
        tools.querySelector('[data-upload-source]')?.addEventListener('click', () => this.root.querySelector('[data-source-upload]').click());
        this.bindMaterialTree();
      }
      requestAnimationFrame(() => { this.leftContent.scrollTop = this.treeScroll[scrollKey]; });
    }

    requestEditableAction(intent) {
      this.root.dispatchEvent(new CustomEvent('map-editor-v2:request-edit', {
        bubbles: true,
        detail: { intent },
      }));
    }

    renderBrushPalette() {
      const popover = this.root?.querySelector('[data-brush-popover]');
      if (!popover) return;
      const visible = this.isCanvasEditing() && this.brushPaletteOpen;
      popover.hidden = !visible;
      if (!visible) { popover.innerHTML = ''; return; }
      const query = this.brushFilter.trim().toLocaleLowerCase();
      const all = this.paintableMaterialSlices();
      const groups = (this.document.material_sources || [])
        .filter(source => source.kind !== 'CANVAS')
        .map(source => {
          const sourceSlices = all.filter(slice => slice.source_id === source.id);
          const sourceMatch = !query || source.name.toLocaleLowerCase().includes(query);
          const slices = sourceMatch ? sourceSlices : sourceSlices.filter(slice => slice.name.toLocaleLowerCase().includes(query));
          return { source, sourceSlices, slices };
        })
        .filter(group => group.slices.length)
        .sort((left, right) => left.source.name.localeCompare(right.source.name, 'zh') || left.source.id.localeCompare(right.source.id));
      const matchCount = groups.reduce((total, group) => total + group.slices.length, 0);
      popover.innerHTML = `<div class="me2-brush-head"><strong>选择切片</strong><span>${matchCount} / ${all.length}</span></div>
        <div class="me2-brush-search"><span>⌕</span><input data-brush-search value="${escapeHtml(this.brushFilter)}" placeholder="搜索原图或切片" /></div>
        <div class="me2-brush-tree">${groups.map(group => {
          const expanded = Boolean(query) || this.expandedBrushSources.has(group.source.id);
          const count = query && group.slices.length !== group.sourceSlices.length
            ? `${group.slices.length} / ${group.sourceSlices.length} 个切片`
            : `${group.sourceSlices.length} 个切片`;
          return `<section class="me2-brush-source${expanded ? ' expanded' : ''}" data-brush-source-group="${escapeHtml(group.source.id)}">
            <button class="me2-brush-source-row" type="button" data-brush-source="${escapeHtml(group.source.id)}" aria-expanded="${expanded}">
              <span class="me2-brush-chevron">›</span>${this.sliceThumb(group.sourceSlices[0])}
              <span class="me2-brush-source-copy"><strong>${escapeHtml(group.source.name)}</strong><small>${count}</small></span>
            </button>
            <div class="me2-brush-slices" data-brush-slices ${expanded ? '' : 'hidden'}>${group.slices.map(slice => this.sliceCard(slice)).join('')}</div>
          </section>`;
        }).join('') || '<div class="me2-tree-empty">没有匹配切片</div>'}</div>`;
      const input = popover.querySelector('[data-brush-search]');
      input?.addEventListener('input', event => {
        this.brushFilter = event.target.value;
        this.renderBrushPalette();
        const next = popover.querySelector('[data-brush-search]');
        next?.focus();
        next?.setSelectionRange(next.value.length, next.value.length);
      });
      popover.querySelectorAll('[data-brush-source]').forEach(button => button.addEventListener('click', event => {
        const sourceId = button.dataset.brushSource;
        const expanded = button.getAttribute('aria-expanded') === 'true';
        if (expanded) this.expandedBrushSources.delete(sourceId); else this.expandedBrushSources.add(sourceId);
        button.setAttribute('aria-expanded', String(!expanded));
        const group = button.closest('[data-brush-source-group]');
        group?.classList.toggle('expanded', !expanded);
        const slices = group?.querySelector('[data-brush-slices]');
        if (slices) slices.hidden = expanded;
        event.stopPropagation();
      }));
      popover.querySelectorAll('[data-paint-slice]').forEach(button => button.addEventListener('click', event => {
        this.selectedPaintSliceId = button.dataset.paintSlice;
        this.tool = 'brush'; this.mapTool = 'brush'; this.brushPaletteOpen = false;
        this.root.querySelectorAll('[data-me2-tool]').forEach(item => item.classList.toggle('active', item.dataset.me2Tool === 'brush'));
        this.renderInspector(); this.renderBrushPalette(); this.updateCanvasCursor();
        event.stopPropagation();
      }));
    }

    nodeTree(node, depth) {
      if (!node) return '';
      const children = this.childrenByParent.get(node.id) || [];
      const expanded = this.expandedNodes.has(node.id);
      const selected = node.id === this.selectedNodeId;
      return `<div class="me2-tree-branch" data-depth="${depth}">
        <div class="me2-tree-row${selected ? ' selected' : ''}" data-node-id="${escapeHtml(node.id)}" style="--depth:${depth}">
          <button class="me2-disclosure${expanded ? ' expanded' : ''}" data-toggle-node="${escapeHtml(node.id)}" ${children.length ? '' : 'disabled'}>›</button>
          <span class="me2-node-mark" style="--mark:${LEVEL_COLOR[node.kind] || '#355f58'}">${LEVEL_MARK[node.kind]}</span>
          <span class="me2-node-copy"><strong>${escapeHtml(node.name)}</strong><small>${LEVEL_LABEL[node.kind]}</small></span>
          ${!this.readonly && node.kind !== 'GAME_OBJECT' ? `<button class="me2-node-add" title="新建子节点" data-add-child="${escapeHtml(node.id)}">＋</button>` : ''}
        </div>
        ${expanded && children.length ? `<div class="me2-tree-children">${children.map(child => this.nodeTree(child, depth + 1)).join('')}</div>` : ''}
      </div>`;
    }

    bindWorldTree() {
      this.leftContent.querySelectorAll('[data-toggle-node]').forEach(button => button.addEventListener('click', event => {
        event.stopPropagation(); const id = button.dataset.toggleNode;
        if (this.expandedNodes.has(id)) this.expandedNodes.delete(id); else this.expandedNodes.add(id);
        this.renderLeft();
      }));
      this.leftContent.querySelectorAll('[data-node-id]').forEach(row => row.addEventListener('click', event => {
        if (event.target.closest('[data-toggle-node],[data-add-child]')) return;
        this.selectNode(row.dataset.nodeId, true);
      }));
      this.leftContent.querySelectorAll('[data-add-child]').forEach(button => button.addEventListener('click', event => {
        event.stopPropagation(); this.addChildNode(button.dataset.addChild);
      }));
      const search = this.root.querySelector('[data-tree-search]');
      search?.addEventListener('input', () => {
        const query = search.value.trim().toLocaleLowerCase();
        this.leftContent.querySelectorAll('[data-node-id]').forEach(row => {
          const node = this.nodeById.get(row.dataset.nodeId);
          row.hidden = Boolean(query && !node?.name.toLocaleLowerCase().includes(query));
        });
      });
    }

    materialTree() {
      const canvases = this.document.material_canvases || [];
      const sources = this.document.material_sources.filter(source => source.kind !== 'CANVAS');
      const group = (id, label, detail, icon, children) => {
        const expanded = this.expandedMaterialGroups.has(id);
        return `<div class="me2-material-source">
          <div class="me2-material-root me2-material-folder" data-material-group="${id}">
            <button class="me2-disclosure${expanded ? ' expanded' : ''}" data-toggle-material-group="${id}">›</button>
            <span class="me2-source-icon">${icon}</span><span><strong>${label}</strong><small>${detail}</small></span>
          </div>
          ${expanded ? `<div class="me2-material-children">${children || '<div class="me2-tree-empty">暂无内容</div>'}</div>` : ''}
        </div>`;
      };
      const canvasRows = canvases.map(canvas => {
        const slice = this.sliceById.get(canvas.slice_id);
        const selected = canvas.id === this.selectedCanvasId && this.materialView === 'canvas';
        return `<button class="me2-material-slice${selected ? ' selected' : ''}" data-canvas-id="${escapeHtml(canvas.id)}">
          ${slice ? this.sliceThumb(slice) : '<span class="me2-thumb">▦</span>'}<span><strong>${escapeHtml(canvas.name)}</strong><small>${canvas.width_tiles} × ${canvas.height_tiles} 格 · 可绘制素材</small></span>
        </button>`;
      }).join('');
      const sourceRows = sources.map(source => {
        const slices = this.document.material_slices.filter(item => item.source_id === source.id);
        const expanded = this.expandedSources.has(source.id);
        const active = this.selectedSourceId === source.id && this.materialView === 'source';
        return `<div class="me2-original-source">
          <div class="me2-material-root${active ? ' selected' : ''}" data-source-id="${escapeHtml(source.id)}">
            <button class="me2-disclosure${expanded ? ' expanded' : ''}" data-toggle-source="${escapeHtml(source.id)}">›</button>
            <span class="me2-source-icon">▧</span><span><strong>${escapeHtml(source.name)}</strong><small>${source.width_px} × ${source.height_px}px · ${slices.length} 个切片</small></span>
          </div>
          ${expanded ? `<div class="me2-material-children">${slices.map(slice => `
            <button class="me2-material-slice${slice.id === this.selectedSliceId && this.materialView === 'slice' ? ' selected' : ''}" data-slice-id="${escapeHtml(slice.id)}">
              ${this.sliceThumb(slice)}<span><strong>${escapeHtml(slice.name)}</strong><small>${slice.pixel_rect.x}, ${slice.pixel_rect.y} · ${slice.pixel_rect.width}×${slice.pixel_rect.height}px</small></span>
            </button>`).join('') || '<div class="me2-tree-empty">还没有切片</div>'}</div>` : ''}
        </div>`;
      }).join('');
      return group('canvases', '画布', `${canvases.length} 个可编辑画布`, '▦', canvasRows)
        + group('sources', '原图', `${sources.length} 张导入图片`, '▧', sourceRows);
    }

    bindMaterialTree() {
      this.leftContent.querySelectorAll('[data-toggle-material-group],[data-material-group]').forEach(element => element.addEventListener('click', event => {
        if (event.currentTarget.matches('[data-material-group]') && event.target.closest('[data-toggle-material-group]')) return;
        event.stopPropagation();
        const id = event.currentTarget.dataset.toggleMaterialGroup || event.currentTarget.dataset.materialGroup;
        if (this.expandedMaterialGroups.has(id)) this.expandedMaterialGroups.delete(id); else this.expandedMaterialGroups.add(id);
        this.renderLeft();
      }));
      this.leftContent.querySelectorAll('[data-toggle-source]').forEach(button => button.addEventListener('click', event => {
        event.stopPropagation(); const id = button.dataset.toggleSource;
        if (this.expandedSources.has(id)) this.expandedSources.delete(id); else this.expandedSources.add(id);
        this.renderLeft();
      }));
      this.leftContent.querySelectorAll('[data-source-id]').forEach(row => row.addEventListener('click', event => {
        if (event.target.closest('[data-toggle-source]')) return;
        this.clearSliceRotationPreview();
        this.selectedCanvasId = ''; this.selectedSourceId = row.dataset.sourceId; this.selectedSliceId = ''; this.materialView = 'source'; this.editingSlice = false;
        this.renderAll(); requestAnimationFrame(() => this.fit());
      }));
      this.leftContent.querySelectorAll('[data-canvas-id]').forEach(row => row.addEventListener('click', () => {
        const canvas = this.canvasById.get(row.dataset.canvasId); if (!canvas) return;
        this.clearSliceRotationPreview();
        this.selectedCanvasId = canvas.id; this.selectedSourceId = canvas.source_id; this.selectedSliceId = canvas.slice_id;
        this.materialView = 'canvas'; this.editingSlice = false; this.materialPan = false; this.mapTool = 'brush'; this.tool = 'brush';
        const brushes = this.paintableMaterialSlices();
        if (!brushes.some(slice => slice.id === this.selectedPaintSliceId)) this.selectedPaintSliceId = brushes[0]?.id || '';
        this.resetMapHistory(); this.renderAll(); requestAnimationFrame(() => this.fit());
      }));
      this.leftContent.querySelectorAll('[data-slice-id]').forEach(row => row.addEventListener('click', () => {
        this.clearSliceRotationPreview();
        const slice = this.sliceById.get(row.dataset.sliceId);
        this.selectedSliceId = row.dataset.sliceId; this.selectedSourceId = slice.source_id;
        this.selectedCanvasId = ''; this.materialView = 'slice'; this.editingSlice = false;
        this.renderAll(); requestAnimationFrame(() => this.fit());
      }));
    }

    renderInspector() {
      const title = this.root.querySelector('[data-inspector-title]');
      const kind = this.root.querySelector('[data-inspector-kind]');
      if (this.workspace === 'world') {
        const node = this.nodeById.get(this.selectedNodeId);
        title.textContent = node?.name || '对象检查器'; kind.textContent = node ? LEVEL_LABEL[node.kind] : '';
        this.inspector.innerHTML = node ? this.nodeInspector(node) : '<div class="me2-empty-copy">选择四层地址树中的节点</div>';
        this.bindNodeInspector(node);
      } else if (this.workspace === 'materials') {
        const canvas = this.currentMaterialCanvas();
        if (this.materialView === 'canvas' && canvas) {
          title.textContent = canvas.name; kind.textContent = '画布';
          this.inspector.innerHTML = this.canvasInspector(canvas);
          this.bindCanvasInspector(canvas);
          return;
        }
        const source = this.sourceById.get(this.selectedSourceId);
        const slice = this.sliceById.get(this.selectedSliceId);
        title.textContent = slice && this.materialView === 'slice' ? slice.name : source?.name || '素材检查器';
        kind.textContent = slice && this.materialView === 'slice' ? '切片' : '原图';
        this.inspector.innerHTML = this.materialInspector(source, slice);
        this.bindMaterialInspector(source, slice);
      }
    }

    worldMaterialSlices(node = null) {
      const result = [...(this.document.material_slices || [])];
      const assigned = node?.material_slice_id ? this.sliceById.get(node.material_slice_id) : null;
      if (assigned && !result.some(slice => slice.id === assigned.id)) result.push(assigned);
      return result.sort((left, right) => left.name.localeCompare(right.name, 'zh') || left.id.localeCompare(right.id));
    }

    nodeMaterialSlice(node) {
      const sliceId = this.nodeMaterialPreview?.nodeId === node?.id
        ? this.nodeMaterialPreview.sliceId
        : node?.material_slice_id;
      return sliceId ? this.sliceById.get(sliceId) : null;
    }

    nodeMaterialPreviewHtml(slice) {
      if (!slice) return '<div class="me2-empty-copy compact">当前层不叠加素材</div>';
      const footprint = this.sliceFootprint(slice);
      return `<div class="me2-inspector-preview">${this.sliceThumb(slice, true)}</div>
        <div class="me2-property-list"><div><span>素材</span><strong>${escapeHtml(slice.name)}</strong></div><div><span>占格</span><strong>${footprint.columns} × ${footprint.rows}</strong></div><div><span>旋转</span><strong>${this.sliceRotation(slice)}°</strong></div></div>`;
    }

    nodeInspector(node) {
      const rect = node.bounds;
      const address = this.nodeAddress(node).join(' → ');
      const materials = this.worldMaterialSlices(node);
      const selectedMaterial = this.nodeMaterialSlice(node);
      const selectedMaterialId = selectedMaterial?.id || '';
      const materialOptions = materials.map(slice => {
        const footprint = this.sliceFootprint(slice);
        return `<option value="${escapeHtml(slice.id)}" ${slice.id === selectedMaterialId ? 'selected' : ''}>${escapeHtml(slice.name)} · ${footprint.columns}×${footprint.rows} 格 · ${this.sliceRotation(slice)}°</option>`;
      }).join('');
      const binding = node.kind === 'GAME_OBJECT' ? (node.skill_bindings?.[0] || null) : null;
      const skillNames = new Set(this.passiveSkillCatalog.map(item => item.name));
      if (binding?.skill_name) skillNames.add(binding.skill_name);
      const skillOptions = [...skillNames].sort((a, b) => a.localeCompare(b)).map(name => {
        const item = this.passiveSkillCatalog.find(candidate => candidate.name === name);
        return `<option value="${escapeHtml(name)}" ${binding?.skill_name === name ? 'selected' : ''}>${escapeHtml(name)}${item?.description ? ` · ${escapeHtml(item.description)}` : ''}</option>`;
      }).join('');
      const skillSection = node.kind === 'GAME_OBJECT' ? `<div class="me2-form-section"><div class="me2-section-title"><strong>被动 Skill</strong><span>Agent 主动请求</span></div>
          <label>Skill<select class="control" data-node-skill ${this.readonly ? 'disabled' : ''}><option value="">不提供交互</option>${skillOptions}</select></label>
          <label>交互键<input class="control" data-node-interaction-key value="${escapeHtml(binding?.interaction_key || 'query-state')}" ${this.readonly ? 'disabled' : ''}></label>
          <label>交互说明<input class="control" data-node-interaction-description value="${escapeHtml(binding?.description || '查询对象当前状态')}" ${this.readonly ? 'disabled' : ''}></label>
          <label>交互距离（米）<input class="control" type="number" min="0.1" step="0.1" data-node-interaction-radius value="${Number(binding?.interaction_radius_m || 2)}" ${this.readonly ? 'disabled' : ''}></label>
          <label>默认请求<textarea class="control" rows="3" data-node-interaction-request ${this.readonly ? 'disabled' : ''}>${escapeHtml(binding?.default_request || '请提供当前状态和可执行信息。')}</textarea></label>
          <div class="me2-inspector-note">靠近只会向 Agent 暴露此交互；只有 Agent 明确选择后才执行 Skill。</div></div>` : '';
      return `<div class="me2-address-path">${escapeHtml(address)}</div>
        <div class="me2-form-section"><label>节点名称<input class="control" data-node-name value="${escapeHtml(node.name)}" ${this.readonly ? 'disabled' : ''}></label></div>
        <div class="me2-form-section"><label>显示素材<select class="control" data-node-material ${this.readonly ? 'disabled' : ''}><option value="">不叠加素材</option>${materialOptions}</select></label>
          <div data-node-material-preview>${this.nodeMaterialPreviewHtml(selectedMaterial)}</div>
          ${materials.length ? '' : '<div class="me2-inspector-note">暂无素材；请先到素材页新建画布或导入原图并创建切片。</div>'}</div>
        <div class="me2-form-section"><div class="me2-section-title"><strong>空间范围</strong><span>Tile 坐标</span></div>
          <div class="me2-four-fields">
            <label>X<input class="control" type="number" min="0" data-node-x value="${rect.x}" ${this.readonly ? 'disabled' : ''}></label>
            <label>Y<input class="control" type="number" min="0" data-node-y value="${rect.y}" ${this.readonly ? 'disabled' : ''}></label>
            <label>W<input class="control" type="number" min="1" data-node-w value="${rect.width}" ${this.readonly ? 'disabled' : ''}></label>
            <label>H<input class="control" type="number" min="1" data-node-h value="${rect.height}" ${this.readonly ? 'disabled' : ''}></label>
          </div></div>
        <div class="me2-form-section"><label>空间语义<textarea class="control" rows="5" data-node-semantic ${this.readonly ? 'disabled' : ''}>${escapeHtml(node.semantic || '')}</textarea></label></div>
        ${skillSection}
        ${this.readonly ? '' : (node.kind === 'WORLD'
          ? '<button class="me2-save" data-save-node>保存</button>'
          : '<div class="me2-inline-actions"><button class="me2-danger" data-delete-node>删除节点</button><button class="me2-save" data-save-node>保存</button></div>')}`;
    }

    bindNodeInspector(node) {
      if (!node) return;
      this.inspector.querySelector('[data-node-material]')?.addEventListener('change', event => {
        const sliceId = this.sliceById.has(event.target.value) ? event.target.value : '';
        this.nodeMaterialPreview = { nodeId: node.id, sliceId };
        const preview = this.inspector.querySelector('[data-node-material-preview]');
        if (preview) preview.innerHTML = this.nodeMaterialPreviewHtml(this.nodeMaterialSlice(node));
        this.renderCanvas();
      });
      this.inspector.querySelector('[data-save-node]')?.addEventListener('click', () => {
        const maxW = Number(this.document.import_metadata.width || 140);
        const maxH = Number(this.document.import_metadata.height || 100);
        const x = Math.max(0, Math.min(maxW - 1, Number(this.inspector.querySelector('[data-node-x]').value) || 0));
        const y = Math.max(0, Math.min(maxH - 1, Number(this.inspector.querySelector('[data-node-y]').value) || 0));
        node.name = this.inspector.querySelector('[data-node-name]').value.trim() || node.name;
        node.bounds = { x, y,
          width: Math.max(1, Math.min(maxW - x, Number(this.inspector.querySelector('[data-node-w]').value) || 1)),
          height: Math.max(1, Math.min(maxH - y, Number(this.inspector.querySelector('[data-node-h]').value) || 1)) };
        node.semantic = this.inspector.querySelector('[data-node-semantic]').value.trim();
        const materialId = this.inspector.querySelector('[data-node-material]')?.value || '';
        node.material_slice_id = this.sliceById.has(materialId) ? materialId : null;
        if (node.kind === 'GAME_OBJECT') {
          const skillName = this.inspector.querySelector('[data-node-skill]')?.value || '';
          node.skill_bindings = skillName ? [{
            interaction_key: this.inspector.querySelector('[data-node-interaction-key]').value.trim() || 'query-state',
            skill_name: skillName,
            description: this.inspector.querySelector('[data-node-interaction-description]').value.trim() || '查询对象当前状态',
            interaction_radius_m: Math.max(.1, Number(this.inspector.querySelector('[data-node-interaction-radius]').value) || 2),
            default_request: this.inspector.querySelector('[data-node-interaction-request]').value.trim() || '请提供当前状态和可执行信息。',
          }] : [];
        }
        this.nodeMaterialPreview = null;
        node.extensions ||= {}; delete node.extensions.mask;
        this.changed = true; this.reindex(); this.renderAll();
        this.toast('节点已保存', this.nodeAddress(node).join(' → '));
      });
      this.inspector.querySelector('[data-delete-node]')?.addEventListener('click', () => this.deleteWorldNode(node));
    }

    canvasInspector(canvas) {
      const slice = this.sliceById.get(canvas.slice_id);
      const brush = this.sliceById.get(this.selectedPaintSliceId);
      const applications = slice ? this.sliceApplications(slice) : [];
      return `<div class="me2-inspector-preview">${slice ? this.sliceThumb(slice, true) : ''}</div>
        <div class="me2-form-section"><label>画布名称<input class="control" data-canvas-name value="${escapeHtml(canvas.name)}" ${this.readonly ? 'disabled' : ''}></label></div>
        <div class="me2-form-section"><div class="me2-section-title"><strong>画布尺寸</strong><span>32px / 格</span></div>
          <div class="me2-four-fields"><label>宽度<input class="control" type="number" min="1" max="256" data-canvas-width value="${canvas.width_tiles}" ${this.readonly ? 'disabled' : ''}></label>
          <label>高度<input class="control" type="number" min="1" max="256" data-canvas-height value="${canvas.height_tiles}" ${this.readonly ? 'disabled' : ''}></label></div></div>
        <div class="me2-property-list"><div><span>当前画笔</span><strong>${escapeHtml(brush?.name || '尚未选择')}</strong></div><div><span>已绘制格</span><strong>${Object.keys(canvas.cells || {}).length}</strong></div><div><span>输出素材</span><strong>${canvas.width_tiles * canvas.tile_size} × ${canvas.height_tiles * canvas.tile_size}px</strong></div></div>
        ${this.readonly ? '' : '<div class="me2-inline-actions"><button class="me2-danger" data-delete-canvas>删除画布</button><button class="me2-save" data-save-canvas>保存</button></div>'}
        <div class="me2-section-title me2-app-title"><strong>应用列表</strong><span>${applications.length}</span></div>${this.applicationList(applications)}`;
    }

    bindCanvasInspector(canvas) {
      this.inspector.querySelector('[data-save-canvas]')?.addEventListener('click', () => {
        const oldWidth = canvas.width_tiles; const oldHeight = canvas.height_tiles;
        const width = Math.max(1, Math.min(256, Number(this.inspector.querySelector('[data-canvas-width]').value) || oldWidth));
        const height = Math.max(1, Math.min(256, Number(this.inspector.querySelector('[data-canvas-height]').value) || oldHeight));
        const name = this.inspector.querySelector('[data-canvas-name]').value.trim() || canvas.name;
        if (width !== oldWidth || height !== oldHeight) {
          const invalidPlacements = new Set();
          for (const [indexText, layers] of Object.entries(canvas.cells || {})) {
            const index = Number(indexText); const x = index % oldWidth; const y = Math.floor(index / oldWidth);
            if (x < width && y < height) continue;
            for (const layer of layers || []) if (layer.part?.placement_id) invalidPlacements.add(layer.part.placement_id);
          }
          const nextCells = {};
          for (const [indexText, layers] of Object.entries(canvas.cells || {})) {
            const index = Number(indexText); const x = index % oldWidth; const y = Math.floor(index / oldWidth);
            if (x >= width || y >= height) continue;
            const kept = (layers || []).filter(layer => !invalidPlacements.has(layer.part?.placement_id)).map(layer => {
              const copy = deepClone(layer);
              if (copy.part) {
                const anchorX = copy.part.anchor_index % oldWidth;
                const anchorY = Math.floor(copy.part.anchor_index / oldWidth);
                copy.part.anchor_index = anchorY * width + anchorX;
              }
              return copy;
            });
            if (kept.length) nextCells[y * width + x] = kept;
          }
          canvas.cells = nextCells; canvas.width_tiles = width; canvas.height_tiles = height;
        }
        canvas.name = name;
        const source = this.sourceById.get(canvas.source_id); const slice = this.sliceById.get(canvas.slice_id);
        if (source) {
          source.name = name; source.width_px = width * canvas.tile_size; source.height_px = height * canvas.tile_size;
          source.columns = width; source.rows = height; source.tile_count = width * height;
        }
        if (slice) {
          slice.name = name; slice.pixel_rect = { x: 0, y: 0, width: width * canvas.tile_size, height: height * canvas.tile_size };
        }
        this.changed = true; this.reindex(); this.refreshCanvasImages(); this.renderAll(); requestAnimationFrame(() => this.fit());
        this.toast('画布已保存', `${name} · ${width} × ${height} 格`);
      });
      this.inspector.querySelector('[data-delete-canvas]')?.addEventListener('click', () => this.deleteMaterialCanvas(canvas));
    }

    createMaterialCanvas() {
      if (this.readonly) return;
      const id = uid('canvas'); const sourceId = uid('source'); const sliceId = uid('slice');
      const name = '未命名画布'; const width = 32; const height = 32;
      const source = { id: sourceId, name, kind: 'CANVAS', asset_id: null, asset_hash: null, bundled_path: null,
        generated_color: null, media_type: 'image/png', width_px: width * MATERIAL_GRID_SIZE, height_px: height * MATERIAL_GRID_SIZE,
        tile_width: MATERIAL_GRID_SIZE, tile_height: MATERIAL_GRID_SIZE, columns: width, rows: height,
        tile_count: width * height, margin: 0, spacing: 0, first_gid: null };
      const slice = { id: sliceId, source_id: sourceId, name, kind: 'STAMP', rotation_degrees: 0,
        grid_rect: null, pixel_rect: { x: 0, y: 0, width: source.width_px, height: source.height_px },
        trim_transparent: true, indexed_gid: null, local_tile_id: null, readonly_indexed: false };
      const canvas = { id, source_id: sourceId, slice_id: sliceId, name, width_tiles: width, height_tiles: height, tile_size: MATERIAL_GRID_SIZE, cells: {} };
      this.document.material_sources.push(source); this.document.material_slices.push(slice); this.document.material_canvases.push(canvas);
      this.workspace = 'materials'; this.selectedCanvasId = id; this.selectedSourceId = sourceId; this.selectedSliceId = sliceId; this.materialView = 'canvas';
      this.expandedMaterialGroups.add('canvases'); this.mapTool = 'brush'; this.tool = 'brush'; this.materialPan = false;
      this.brushPaletteOpen = false;
      this.root.querySelectorAll('[data-me2-tab]').forEach(item => item.classList.toggle('active', item.dataset.me2Tab === 'materials'));
      this.changed = true; this.reindex(); this.refreshCanvasImages(); this.resetMapHistory(); this.renderAll(); requestAnimationFrame(() => {
        this.fit(); this.inspector.querySelector('[data-canvas-name]')?.select();
      });
      this.toast('画布已新建', `${width} × ${height} 格 · 从画笔选择素材开始绘制`);
    }

    deleteMaterialCanvas(canvas) {
      if (this.readonly || !canvas || !this.canvasById.has(canvas.id)) return;
      if (!window.confirm(`确定删除画布“${canvas.name}”及其输出素材吗？`)) return;
      this.removeMaterialReferences(new Set([canvas.slice_id]));
      this.document.material_canvases = this.document.material_canvases.filter(item => item.id !== canvas.id);
      this.document.material_slices = this.document.material_slices.filter(item => item.id !== canvas.slice_id);
      this.document.material_sources = this.document.material_sources.filter(item => item.id !== canvas.source_id);
      this.images.delete(canvas.source_id); this.imageUrls.delete(canvas.source_id);
      this.selectedCanvasId = ''; this.selectedSliceId = ''; this.selectedSourceId = this.document.material_sources.find(source => source.kind !== 'CANVAS')?.id || '';
      this.materialView = 'source'; this.changed = true; this.reindex(); this.refreshCanvasImages(); this.renderAll(); requestAnimationFrame(() => this.fit());
      this.toast('画布已删除', canvas.name);
    }

    materialInspector(source, slice) {
      if (!source) return '<div class="me2-empty-copy">选择一张原图</div>';
      if (!slice || this.materialView === 'source') {
        const slices = this.document.material_slices.filter(item => item.source_id === source.id);
        const applications = this.sourceApplications(source.id);
        return `<div class="me2-source-summary"><span class="me2-source-big">▧</span><div><strong>${escapeHtml(source.name)}</strong><small>${source.width_px} × ${source.height_px}px</small></div></div>
          <div class="me2-property-list"><div><span>网格</span><strong>${source.columns} × ${source.rows}</strong></div><div><span>切片</span><strong>${slices.length}</strong></div><div><span>来源</span><strong>导入原图</strong></div></div>
          ${this.readonly ? '' : '<div class="me2-inline-actions"><button class="me2-outline" data-new-slice>＋ 新建切片</button><button class="me2-danger" data-delete-source>删除原图</button></div>'}
          <div class="me2-section-title me2-app-title"><strong>应用列表</strong><span>${applications.length}</span></div>${this.applicationList(applications)}`;
      }
      const applications = this.sliceApplications(slice);
      return `<div class="me2-inspector-preview">${this.sliceThumb(slice, true)}</div>
        <div class="me2-form-section"><label>切片名称<input class="control" data-slice-name value="${escapeHtml(slice.name)}" ${this.readonly ? 'disabled' : ''}></label></div>
        <div class="me2-form-section"><label>旋转<select class="control" data-slice-rotation ${this.readonly ? 'disabled' : ''}><option value="0" ${this.sliceRotation(slice) === 0 ? 'selected' : ''}>0° · 原方向</option><option value="90" ${this.sliceRotation(slice) === 90 ? 'selected' : ''}>90° · 顺时针</option><option value="180" ${this.sliceRotation(slice) === 180 ? 'selected' : ''}>180°</option><option value="270" ${this.sliceRotation(slice) === 270 ? 'selected' : ''}>270° · 顺时针</option></select></label></div>
        <div class="me2-form-section"><div class="me2-section-title"><strong>切片范围</strong><span>像素</span></div>
          <div class="me2-four-fields"><label>X<input class="control" type="number" data-slice-x value="${slice.pixel_rect.x}" ${this.readonly ? 'disabled' : ''}></label>
          <label>Y<input class="control" type="number" data-slice-y value="${slice.pixel_rect.y}" ${this.readonly ? 'disabled' : ''}></label>
          <label>W<input class="control" type="number" min="1" data-slice-w value="${slice.pixel_rect.width}" ${this.readonly ? 'disabled' : ''}></label>
          <label>H<input class="control" type="number" min="1" data-slice-h value="${slice.pixel_rect.height}" ${this.readonly ? 'disabled' : ''}></label></div></div>
        ${this.readonly ? '' : `<div class="me2-inline-actions"><button class="me2-outline" data-edit-crop>${this.editingSlice ? '完成框选' : '在原图中编辑'}</button><button class="me2-save" data-save-slice>保存</button></div><button class="me2-danger me2-danger-block" data-delete-slice>删除切片</button>`}
        <div class="me2-section-title me2-app-title"><strong>应用列表</strong><span>${applications.length}</span></div>${this.applicationList(applications)}`;
    }

    bindMaterialInspector(source, slice) {
      this.inspector.querySelector('[data-new-slice]')?.addEventListener('click', () => {
        this.clearSliceRotationPreview();
        const item = this.newMaterialSlice(source, {
          x: 0, y: 0,
          width: Math.min(MATERIAL_GRID_SIZE, source.width_px),
          height: Math.min(MATERIAL_GRID_SIZE, source.height_px),
        });
        this.document.material_slices.push(item); this.selectedSliceId = item.id; this.materialView = 'slice'; this.editingSlice = true;
        this.changed = true; this.reindex(); this.renderAll(); requestAnimationFrame(() => this.fit());
      });
      this.inspector.querySelector('[data-edit-crop]')?.addEventListener('click', () => {
        this.editingSlice = !this.editingSlice;
        if (this.editingSlice) { this.materialView = 'slice'; requestAnimationFrame(() => this.fit()); }
        this.renderAll();
      });
      this.inspector.querySelector('[data-slice-rotation]')?.addEventListener('change', event => {
        if (!slice) return;
        const rotation = Number(event.target.value);
        this.sliceRotationPreview = {
          sliceId: slice.id,
          rotation: [0, 90, 180, 270].includes(rotation) ? rotation : 0,
        };
        this.renderLeft();
        const preview = this.inspector.querySelector('.me2-inspector-preview');
        if (preview) preview.innerHTML = this.sliceThumb(slice, true);
        requestAnimationFrame(() => this.fit());
      });
      this.inspector.querySelector('[data-save-slice]')?.addEventListener('click', () => {
        if (!slice) return;
        slice.name = this.inspector.querySelector('[data-slice-name]').value.trim() || slice.name;
        const rotation = Number(this.inspector.querySelector('[data-slice-rotation]').value);
        slice.rotation_degrees = [0, 90, 180, 270].includes(rotation) ? rotation : 0;
        this.clearSliceRotationPreview(slice.id);
        const x = Math.max(0, Math.min(source.width_px - 1, Number(this.inspector.querySelector('[data-slice-x]').value) || 0));
        const y = Math.max(0, Math.min(source.height_px - 1, Number(this.inspector.querySelector('[data-slice-y]').value) || 0));
        slice.pixel_rect = { x, y,
          width: Math.max(1, Math.min(source.width_px - x, Number(this.inspector.querySelector('[data-slice-w]').value) || 1)),
          height: Math.max(1, Math.min(source.height_px - y, Number(this.inspector.querySelector('[data-slice-h]').value) || 1)) };
        slice.grid_rect = null; this.changed = true; this.editingSlice = false; this.reindex(); this.renderAll();
        this.toast('切片已保存', `${slice.name} · ${slice.pixel_rect.width}×${slice.pixel_rect.height}px`);
      });
      this.inspector.querySelector('[data-delete-slice]')?.addEventListener('click', () => this.deleteMaterialSlice(slice));
      this.inspector.querySelector('[data-delete-source]')?.addEventListener('click', () => this.deleteMaterialSource(source));
    }

    deleteMaterialSlice(slice) {
      if (this.readonly || !slice || !this.sliceById.has(slice.id)) return;
      const referenceCount = this.sliceApplications(slice).reduce((total, item) => total + item.count, 0);
      const referenceText = referenceCount ? `，并清理 ${referenceCount} 处引用` : '';
      if (!window.confirm(`确定删除切片“${slice.name}”${referenceText}吗？`)) return;
      this.removeMaterialReferences(new Set([slice.id]));
      this.document.material_slices = this.document.material_slices.filter(item => item.id !== slice.id);
      this.clearSliceTransparency(new Set([slice.id]));
      this.clearSliceRotationPreview(slice.id);
      this.selectedSliceId = ''; this.materialView = 'source'; this.editingSlice = false;
      this.changed = true; this.reindex(); this.renderAll(); requestAnimationFrame(() => this.fit());
      this.toast('切片已删除', slice.name);
    }

    deleteMaterialSource(source) {
      if (this.readonly || !source || !this.sourceById.has(source.id)) return;
      const slices = this.document.material_slices.filter(item => item.source_id === source.id);
      const sliceIds = new Set(slices.map(item => item.id));
      const referenceCount = this.sourceApplications(source.id).reduce((total, item) => total + item.count, 0);
      const referenceText = referenceCount ? `，并清理 ${referenceCount} 处引用` : '';
      if (!window.confirm(`确定删除原图“${source.name}”及其 ${slices.length} 个切片${referenceText}吗？`)) return;
      this.removeMaterialReferences(sliceIds);
      this.document.material_slices = this.document.material_slices.filter(item => !sliceIds.has(item.id));
      this.document.material_sources = this.document.material_sources.filter(item => item.id !== source.id);
      this.clearSliceTransparency(sliceIds);
      this.images.delete(source.id); this.imageUrls.delete(source.id); this.expandedSources.delete(source.id);
      this.clearSliceRotationPreview(); this.selectedSliceId = ''; this.materialView = 'source'; this.editingSlice = false;
      this.selectedSourceId = this.document.material_sources.find(item => item.kind !== 'CANVAS')?.id || '';
      this.changed = true; this.reindex(); this.refreshCanvasImages(); this.renderAll(); requestAnimationFrame(() => this.fit());
      this.toast('原图已删除', `${source.name} · ${slices.length} 个切片`);
    }

    removeMaterialReferences(sliceIds) {
      if (!sliceIds?.size) return;
      const gids = new Set(this.document.material_slices
        .filter(slice => sliceIds.has(slice.id) && slice.indexed_gid)
        .map(slice => slice.indexed_gid));
      const occupied = new Set([
        ...Object.keys(this.document.tile_overrides || {}),
        ...Object.keys(this.document.tile_override_layers || {}),
      ]);
      const placementIds = new Set();
      for (const indexText of occupied) {
        for (const layer of this.readMapCellLayers(Number(indexText))) {
          if (sliceIds.has(layer.slice_id) && layer.part?.placement_id) placementIds.add(layer.part.placement_id);
        }
      }
      for (const indexText of occupied) {
        const index = Number(indexText);
        const layers = this.readMapCellLayers(index);
        const remaining = layers.filter(layer =>
          !sliceIds.has(layer.slice_id) && !placementIds.has(layer.part?.placement_id));
        if (remaining.length !== layers.length) this.writeMapCellState(index, remaining);
      }
      for (const layer of this.document.visual_layers || []) {
        layer.raw_gids = (layer.raw_gids || []).map(raw => gids.has(Number(raw) & GID_MASK) ? 0 : raw);
        layer.cell_overrides = (layer.cell_overrides || []).filter(item => !sliceIds.has(item.slice_id));
      }
      for (const recipe of this.document.render_recipes || []) {
        recipe.entries = (recipe.entries || []).filter(entry => !sliceIds.has(entry.slice_id));
      }
      for (const canvas of this.document.material_canvases || []) {
        const placementIds = new Set();
        for (const layers of Object.values(canvas.cells || {})) {
          for (const layer of layers || []) {
            if (sliceIds.has(layer.slice_id) && layer.part?.placement_id) placementIds.add(layer.part.placement_id);
          }
        }
        for (const [index, layers] of Object.entries(canvas.cells || {})) {
          const remaining = (layers || []).filter(layer =>
            !sliceIds.has(layer.slice_id) && !placementIds.has(layer.part?.placement_id));
          if (remaining.length) canvas.cells[index] = remaining; else delete canvas.cells[index];
        }
      }
      for (const node of this.document.hierarchy_nodes || []) {
        if (sliceIds.has(node.material_slice_id)) node.material_slice_id = null;
      }
      if (sliceIds.has(this.selectedPaintSliceId)) this.selectedPaintSliceId = '';
      if (sliceIds.has(this.nodeMaterialPreview?.sliceId)) this.nodeMaterialPreview = null;
    }

    clearSliceTransparency(sliceIds) {
      if (!this.sliceTransparency) return;
      for (const key of this.sliceTransparency.keys()) {
        for (const sliceId of sliceIds) {
          if (key.startsWith(`${sliceId}:`)) { this.sliceTransparency.delete(key); break; }
        }
      }
    }

    newMaterialSlice(source, pixelRect) {
      return { id: uid('slice'), source_id: source.id, name: '未命名切片', kind: 'STAMP',
        rotation_degrees: 0, pixel_rect: pixelRect,
        grid_rect: null, trim_transparent: true, indexed_gid: null, local_tile_id: null, readonly_indexed: false };
    }

    applicationList(items) {
      if (!items.length) return '<div class="me2-empty-copy compact">尚未被地图引用</div>';
      return `<div class="me2-application-list">${items.map(item => `<div><span class="me2-app-dot" style="--dot:${LEVEL_COLOR[item.level] || '#337f73'}"></span><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.detail)}</small></span><em>${item.count.toLocaleString()}</em></div>`).join('')}</div>`;
    }

    sourceApplications(sourceId) {
      const sourceSlices = this.document.material_slices.filter(item => item.source_id === sourceId);
      const gids = new Set(sourceSlices.filter(item => item.indexed_gid).map(item => item.indexed_gid));
      const sliceIds = new Set(sourceSlices.map(item => item.id));
      const layerItems = this.document.visual_layers.map(layer => {
        const counts = this.layerUsage.get(layer.id); let count = 0;
        for (const gid of gids) count += counts?.get(gid) || 0;
        if (layer.display_level === 'MAP') {
          count += Object.values(this.document.tile_override_layers || {}).flat()
            .filter(entry => sliceIds.has(entry.slice_id)).length;
        }
        return { name: layer.name, detail: `${this.levelName(layer.display_level)}视觉层`, level: layer.display_level, count };
      });
      const nodeItems = ['WORLD', 'SECTOR', 'ARENA', 'GAME_OBJECT'].map(level => ({
        name: `${LEVEL_LABEL[level]} 节点素材`, detail: '四层地址树', level,
        count: this.document.hierarchy_nodes.filter(node => node.kind === level && sliceIds.has(node.material_slice_id)).length,
      }));
      const canvasItems = (this.document.material_canvases || []).map(canvas => ({
        name: canvas.name, detail: '画布绘制', level: 'MAP',
        count: Object.values(canvas.cells || {}).flat().filter(entry => sliceIds.has(entry.slice_id)).length,
      }));
      return [...layerItems, ...nodeItems, ...canvasItems].filter(item => item.count);
    }

    sliceApplications(slice) {
      const layerItems = this.document.visual_layers.map(layer => {
        let count = slice.indexed_gid ? (this.layerUsage.get(layer.id)?.get(slice.indexed_gid) || 0) : 0;
        if (layer.display_level === 'MAP') {
          count += Object.values(this.document.tile_override_layers || {}).flat()
            .filter(entry => entry.slice_id === slice.id).length;
        }
        return { name: layer.name, detail: `${this.levelName(layer.display_level)}视觉层`, level: layer.display_level, count };
      });
      const nodeItems = ['WORLD', 'SECTOR', 'ARENA', 'GAME_OBJECT'].map(level => ({
        name: `${LEVEL_LABEL[level]} 节点素材`, detail: '四层地址树', level,
        count: this.document.hierarchy_nodes.filter(node => node.kind === level && node.material_slice_id === slice.id).length,
      }));
      const canvasItems = (this.document.material_canvases || []).map(canvas => ({
        name: canvas.name, detail: '画布绘制', level: 'MAP',
        count: Object.values(canvas.cells || {}).flat().filter(entry => entry.slice_id === slice.id).length,
      }));
      return [...layerItems, ...nodeItems, ...canvasItems].filter(item => item.count);
    }

    levelName(level) { return level === 'MAP' ? '地图' : level === 'GAME_OBJECT' ? 'Game Object' : level[0] + level.slice(1).toLowerCase(); }
    layerFilled(layer) {
      const filled = new Set();
      (layer.raw_gids || []).forEach((raw, index) => { if (Number(raw) & GID_MASK) filled.add(index); });
      if (layer.display_level === 'MAP') Object.keys(this.document.tile_overrides || {}).forEach(index => filled.add(Number(index)));
      return filled.size;
    }

    mapMaterialSlices() {
      const gids = new Set();
      this.document.visual_layers.filter(item => item.display_level === 'MAP').forEach(layer => (layer.raw_gids || []).forEach(raw => { const gid = Number(raw) & GID_MASK; if (gid) gids.add(gid); }));
      const mapSlices = [...this.document.material_slices];
      return mapSlices.sort((left, right) => {
        const leftRank = left.indexed_gid ? (gids.has(left.indexed_gid) ? 1 : 2) : 0;
        const rightRank = right.indexed_gid ? (gids.has(right.indexed_gid) ? 1 : 2) : 0;
        return leftRank - rightRank || left.name.localeCompare(right.name, 'zh');
      });
    }

    sliceCard(slice) {
      return `<button class="me2-slice-card${slice.id === this.selectedPaintSliceId ? ' selected' : ''}" data-paint-slice="${escapeHtml(slice.id)}">${this.sliceThumb(slice)}<span>${escapeHtml(slice.name.split(' · ')[0])}</span></button>`;
    }

    sliceRotation(slice) {
      if (slice && this.sliceRotationPreview?.sliceId === slice.id) return this.sliceRotationPreview.rotation;
      const rotation = Number(slice?.rotation_degrees || 0);
      return [0, 90, 180, 270].includes(rotation) ? rotation : 0;
    }

    clearSliceRotationPreview(sliceId = null) {
      if (sliceId && this.sliceRotationPreview?.sliceId !== sliceId) return;
      this.sliceRotationPreview = null;
    }

    sliceDisplaySize(slice) {
      const rect = slice?.pixel_rect || { width: MATERIAL_GRID_SIZE, height: MATERIAL_GRID_SIZE };
      return this.sliceRotation(slice) % 180 === 90
        ? { width: rect.height, height: rect.width }
        : { width: rect.width, height: rect.height };
    }

    sliceFootprint(slice) {
      const display = this.sliceDisplaySize(slice);
      return {
        columns: Math.max(1, Math.ceil(display.width / MATERIAL_GRID_SIZE)),
        rows: Math.max(1, Math.ceil(display.height / MATERIAL_GRID_SIZE)),
      };
    }

    sliceThumb(slice, large = false) {
      const source = this.sourceById.get(slice.source_id);
      const url = this.imageUrls.get(slice.source_id);
      if (!source || !url) return `<span class="me2-thumb${large ? ' large' : ''}">◇</span>`;
      const rect = slice.pixel_rect;
      const size = large ? 112 : 34;
      const scale = Math.min(size / rect.width, size / rect.height);
      const bgW = source.width_px * scale, bgH = source.height_px * scale;
      return `<span class="me2-thumb${large ? ' large' : ''}"><span class="me2-thumb-image" style="background-image:url('${url.replaceAll("'", '%27')}');background-size:${bgW}px ${bgH}px;background-position:${-rect.x * scale}px ${-rect.y * scale}px;transform:rotate(${this.sliceRotation(slice)}deg)"></span></span>`;
    }

    renderCanvas() {
      if (!this.document || !this.context) return;
      const ctx = this.context;
      ctx.clearRect(0, 0, this.viewportWidth, this.viewportHeight);
      this.drawChecker(ctx);
      if (this.isCanvasEditing()) this.renderMaterialDrawingCanvas(ctx);
      else if (this.workspace === 'materials') this.renderMaterialCanvas(ctx);
      else this.renderMapCanvas(ctx);
      this.root.querySelector('[data-zoom]').textContent = `${Math.round(this.zoom * 100)}%`;
      const context = this.root.querySelector('[data-context]');
      context.textContent = this.workspace === 'materials'
        ? (this.currentMaterialCanvas()?.name || this.sliceById.get(this.selectedSliceId)?.name || this.sourceById.get(this.selectedSourceId)?.name || '素材')
        : this.nodeAddress(this.nodeById.get(this.selectedNodeId)).join(' → ');
    }

    drawChecker(ctx) {
      ctx.fillStyle = '#f4f6f4'; ctx.fillRect(0, 0, this.viewportWidth, this.viewportHeight);
      ctx.fillStyle = '#eef1ee';
      for (let y = 0; y < this.viewportHeight; y += 20) for (let x = 0; x < this.viewportWidth; x += 20) if ((x / 20 + y / 20) % 2 === 0) ctx.fillRect(x, y, 20, 20);
    }

    renderMapCanvas(ctx) {
      const width = Number(this.document.import_metadata.width || 140);
      const height = Number(this.document.import_metadata.height || 100);
      const tile = this.renderTile;
      ctx.save(); ctx.translate(this.offsetX, this.offsetY); ctx.scale(this.zoom, this.zoom);
      ctx.imageSmoothingEnabled = false;
      ctx.fillStyle = '#dfe8d2'; ctx.fillRect(0, 0, width * tile, height * tile);
      for (const layer of this.document.visual_layers) {
        if (!layer.visible || LEVEL_ORDER[layer.display_level] > this.depth) continue;
        ctx.globalAlpha = layer.opacity ?? 1;
        (layer.raw_gids || []).forEach((raw, index) => {
          const gid = Number(raw) & GID_MASK; if (!gid) return;
          const slice = this.sliceByGid.get(gid); if (!slice) return;
          this.drawTile(ctx, slice, raw, (index % width) * tile, Math.floor(index / width) * tile, tile);
        });
      }
      ctx.globalAlpha = 1;
      const overrideIndexes = new Set([
        ...Object.keys(this.document.tile_overrides || {}),
        ...Object.keys(this.document.tile_override_layers || {}),
      ]);
      for (const indexText of overrideIndexes) {
        const index = Number(indexText);
        const dx = (index % width) * tile; const dy = Math.floor(index / width) * tile;
        for (const layer of this.readMapCellLayers(index)) {
          const slice = this.sliceById.get(layer.slice_id);
          if (!slice) continue;
          if (layer.part) this.drawTilePart(ctx, slice, layer.part, dx, dy, tile);
          else this.drawTile(ctx, slice, slice.indexed_gid || 0, dx, dy, tile);
        }
      }
      if (this.workspace === 'world') this.drawWorldMaterials(ctx, tile);
      if (this.semanticVisible) this.drawSemantics(ctx, tile);
      const selected = this.nodeById.get(this.selectedNodeId);
      if (this.workspace === 'world' && selected) {
        ctx.strokeStyle = '#fff'; ctx.lineWidth = 3 / this.zoom; ctx.setLineDash([]);
        ctx.strokeRect(selected.bounds.x * tile, selected.bounds.y * tile, selected.bounds.width * tile, selected.bounds.height * tile);
        ctx.strokeStyle = LEVEL_COLOR[selected.kind] || '#166b5c'; ctx.lineWidth = 1.5 / this.zoom;
        ctx.strokeRect(selected.bounds.x * tile, selected.bounds.y * tile, selected.bounds.width * tile, selected.bounds.height * tile);
      }
      ctx.restore();
    }

    drawTile(ctx, slice, raw, dx, dy, size) {
      this.drawSliceRect(ctx, slice, raw, dx, dy, size, size);
    }

    drawSliceRect(ctx, slice, raw, dx, dy, width, height, rotationOverride = null) {
      const image = this.images.get(slice.source_id); if (!image) return;
      const rect = slice.pixel_rect;
      const h = Boolean(Number(raw) & 0x80000000); const v = Boolean(Number(raw) & 0x40000000); const d = Boolean(Number(raw) & 0x20000000);
      const override = Number(rotationOverride);
      const rotation = rotationOverride === null || ![0, 90, 180, 270].includes(override) ? this.sliceRotation(slice) : override;
      if (!h && !v && !d && !rotation) { ctx.drawImage(image, rect.x, rect.y, rect.width, rect.height, dx, dy, width, height); return; }
      const sourceWidth = rotation % 180 === 90 ? height : width;
      const sourceHeight = rotation % 180 === 90 ? width : height;
      ctx.save(); ctx.translate(dx + width / 2, dy + height / 2);
      if (rotation) ctx.rotate(rotation * Math.PI / 180);
      if (d) ctx.transform(0, 1, 1, 0, 0, 0);
      ctx.scale(h ? -1 : 1, v ? -1 : 1);
      ctx.drawImage(image, rect.x, rect.y, rect.width, rect.height, -sourceWidth / 2, -sourceHeight / 2, sourceWidth, sourceHeight); ctx.restore();
    }

    drawTilePart(ctx, slice, part, dx, dy, size) {
      const columns = Math.max(1, Number(part.columns) || 1); const rows = Math.max(1, Number(part.rows) || 1);
      const column = Math.max(0, Number(part.column) || 0); const row = Math.max(0, Number(part.row) || 0);
      ctx.save(); ctx.beginPath(); ctx.rect(dx, dy, size, size); ctx.clip();
      this.drawSliceRect(ctx, slice, 0, dx - column * size, dy - row * size, columns * size, rows * size, part.rotation_degrees);
      ctx.restore();
    }

    drawWorldMaterials(ctx, tile) {
      const kindDepth = { WORLD: 1, SECTOR: 2, ARENA: 3, GAME_OBJECT: 4 };
      const nodes = (this.document.hierarchy_nodes || [])
        .filter(node => kindDepth[node.kind] <= this.depth && this.nodeMaterialSlice(node))
        .sort((left, right) => kindDepth[left.kind] - kindDepth[right.kind]
          || Number(left.sort_order || 0) - Number(right.sort_order || 0)
          || left.id.localeCompare(right.id));
      for (const node of nodes) {
        const slice = this.nodeMaterialSlice(node);
        const footprint = this.sliceFootprint(slice);
        this.drawSliceRect(
          ctx,
          slice,
          0,
          Number(node.bounds.x || 0) * tile,
          Number(node.bounds.y || 0) * tile,
          footprint.columns * tile,
          footprint.rows * tile,
        );
      }
    }

    sliceHasTransparency(slice) {
      const rect = slice?.pixel_rect;
      const image = slice ? this.images?.get(slice.source_id) : null;
      if (!rect || !image || typeof document === 'undefined') return false;
      const key = `${slice.id}:${rect.x},${rect.y},${rect.width},${rect.height}`;
      this.sliceTransparency ||= new Map();
      if (this.sliceTransparency.has(key)) return this.sliceTransparency.get(key);
      let transparent = false;
      try {
        const scale = Math.min(1, 512 / Math.max(rect.width, rect.height));
        const width = Math.max(1, Math.ceil(rect.width * scale));
        const height = Math.max(1, Math.ceil(rect.height * scale));
        const canvas = document.createElement('canvas');
        canvas.width = width; canvas.height = height;
        const context = canvas.getContext('2d', { willReadFrequently: true });
        context.clearRect(0, 0, width, height);
        context.drawImage(image, rect.x, rect.y, rect.width, rect.height, 0, 0, width, height);
        const pixels = context.getImageData(0, 0, width, height).data;
        for (let offset = 3; offset < pixels.length; offset += 4) {
          if (pixels[offset] < 255) { transparent = true; break; }
        }
      } catch (_) { /* Cross-origin or oversized sources fall back to replacement semantics. */ }
      this.sliceTransparency.set(key, transparent);
      return transparent;
    }

    drawSemantics(ctx, tile) {
      const kindDepth = { WORLD: 1, SECTOR: 2, ARENA: 3, GAME_OBJECT: 4 };
      const visibleKinds = this.depth === 1 ? ['WORLD'] : this.depth === 2 ? ['SECTOR'] : this.depth === 3 ? ['SECTOR', 'ARENA'] : ['SECTOR', 'ARENA', 'GAME_OBJECT'];
      const nodes = this.document.hierarchy_nodes.filter(node => visibleKinds.includes(node.kind) && kindDepth[node.kind] <= this.depth);
      for (const node of nodes) {
        const rect = node.bounds; const color = LEVEL_COLOR[node.kind];
        ctx.fillStyle = `${color}22`; ctx.fillRect(rect.x * tile, rect.y * tile, rect.width * tile, rect.height * tile);
        ctx.strokeStyle = `${color}bb`; ctx.lineWidth = 1 / this.zoom; ctx.setLineDash(node.kind === 'GAME_OBJECT' ? [2 / this.zoom, 2 / this.zoom] : []);
        ctx.strokeRect(rect.x * tile, rect.y * tile, rect.width * tile, rect.height * tile);
        if (rect.width * tile * this.zoom > 54 && rect.height * tile * this.zoom > 18) {
          ctx.setLineDash([]); ctx.font = `600 ${Math.max(8, 10 / this.zoom)}px system-ui`; ctx.fillStyle = '#173e37';
          ctx.fillText(node.name, rect.x * tile + 3 / this.zoom, rect.y * tile + 12 / this.zoom);
        }
      }
      ctx.setLineDash([]);
    }

    renderMaterialDrawingCanvas(ctx) {
      const canvas = this.currentMaterialCanvas(); if (!canvas) return;
      const tile = this.renderTile;
      ctx.save(); ctx.translate(this.offsetX, this.offsetY); ctx.scale(this.zoom, this.zoom);
      ctx.imageSmoothingEnabled = false;
      ctx.fillStyle = 'rgba(255,255,255,.42)';
      ctx.fillRect(0, 0, canvas.width_tiles * tile, canvas.height_tiles * tile);
      const indexes = Object.keys(canvas.cells || {}).map(Number).sort((a, b) => a - b);
      for (const index of indexes) {
        const dx = (index % canvas.width_tiles) * tile;
        const dy = Math.floor(index / canvas.width_tiles) * tile;
        for (const layer of canvas.cells[index] || []) {
          const slice = this.sliceById.get(layer.slice_id); if (!slice) continue;
          if (layer.part) this.drawTilePart(ctx, slice, layer.part, dx, dy, tile);
          else this.drawTile(ctx, slice, slice.indexed_gid || 0, dx, dy, tile);
        }
      }
      if (this.zoom * tile >= 8) {
        ctx.beginPath();
        for (let x = tile; x < canvas.width_tiles * tile; x += tile) { ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height_tiles * tile); }
        for (let y = tile; y < canvas.height_tiles * tile; y += tile) { ctx.moveTo(0, y); ctx.lineTo(canvas.width_tiles * tile, y); }
        ctx.strokeStyle = 'rgba(62,91,83,.13)'; ctx.lineWidth = 1 / this.zoom; ctx.stroke();
      }
      ctx.strokeStyle = '#2b7769'; ctx.lineWidth = 1.5 / this.zoom;
      ctx.strokeRect(0, 0, canvas.width_tiles * tile, canvas.height_tiles * tile);
      ctx.restore();
    }

    renderMaterialCanvas(ctx) {
      const source = this.sourceById.get(this.selectedSourceId); const image = this.images.get(this.selectedSourceId);
      if (!source || !image) return;
      ctx.save(); ctx.translate(this.offsetX, this.offsetY); ctx.scale(this.zoom, this.zoom); ctx.imageSmoothingEnabled = false;
      const slice = this.sliceById.get(this.selectedSliceId);
      if (slice && this.materialView === 'slice' && !this.editingSlice) {
        const rect = slice.pixel_rect; const display = this.sliceDisplaySize(slice);
        ctx.save(); ctx.translate(display.width / 2, display.height / 2); ctx.rotate(this.sliceRotation(slice) * Math.PI / 180);
        ctx.drawImage(image, rect.x, rect.y, rect.width, rect.height, -rect.width / 2, -rect.height / 2, rect.width, rect.height); ctx.restore();
        ctx.strokeStyle = '#1d7061'; ctx.lineWidth = 1 / this.zoom; ctx.strokeRect(0, 0, display.width, display.height);
      } else {
        ctx.drawImage(image, 0, 0, source.width_px, source.height_px);
        this.drawMaterialGrid(ctx, source);
        const slices = this.document.material_slices.filter(item => item.source_id === source.id);
        for (const item of slices) {
          const rect = item.pixel_rect; const selected = item.id === this.selectedSliceId;
          ctx.fillStyle = selected ? '#e99b2f30' : '#2c7e7020'; ctx.fillRect(rect.x, rect.y, rect.width, rect.height);
          ctx.strokeStyle = selected ? '#e28d16' : '#217667aa'; ctx.lineWidth = (selected ? 2 : 1) / this.zoom;
          ctx.strokeRect(rect.x, rect.y, rect.width, rect.height);
        }
        if (slice && this.editingSlice) {
          const rect = slice.pixel_rect; const handle = 12 / this.zoom;
          ctx.fillStyle = '#fff'; ctx.strokeStyle = '#c76f00'; ctx.lineWidth = 2 / this.zoom;
          ctx.fillRect(rect.x + rect.width - handle / 2, rect.y + rect.height - handle / 2, handle, handle);
          ctx.strokeRect(rect.x + rect.width - handle / 2, rect.y + rect.height - handle / 2, handle, handle);
        }
      }
      ctx.restore();
    }

    drawMaterialGrid(ctx, source) {
      if (this.zoom * MATERIAL_GRID_SIZE < 8) return;
      ctx.save(); ctx.beginPath();
      for (let x = MATERIAL_GRID_SIZE; x < source.width_px; x += MATERIAL_GRID_SIZE) {
        ctx.moveTo(x, 0); ctx.lineTo(x, source.height_px);
      }
      for (let y = MATERIAL_GRID_SIZE; y < source.height_px; y += MATERIAL_GRID_SIZE) {
        ctx.moveTo(0, y); ctx.lineTo(source.width_px, y);
      }
      ctx.strokeStyle = 'rgba(255,255,255,.55)'; ctx.lineWidth = 1 / this.zoom; ctx.stroke(); ctx.restore();
    }

    pointerDown(event) {
      this.root.focus(); this.canvas.setPointerCapture(event.pointerId);
      const point = this.localPoint(event);
      const canvasEditing = this.isCanvasEditing();
      const panActive = this.workspace === 'materials' && !canvasEditing ? this.materialPan : this.tool === 'pan';
      if (event.button === 1 || this.spaceDown || panActive) {
        this.drag = { type: 'pan', startX: point.x, startY: point.y, offsetX: this.offsetX, offsetY: this.offsetY };
        this.updateCanvasCursor(point); return;
      }
      if (event.button !== 0) return;
      if (this.workspace === 'materials' && !canvasEditing) { this.materialPointerDown(point); return; }
      if (this.workspace === 'world') {
        const position = this.mapPosition(point);
        const selected = this.nodeById.get(this.selectedNodeId);
        if (!this.readonly && selected?.kind !== 'WORLD' && this.pointInNode(position, selected)) {
          this.drag = {
            type: 'move-node', nodeId: selected.id,
            startMapX: position.x, startMapY: position.y,
            startNodeX: selected.bounds.x, startNodeY: selected.bounds.y,
            moved: false,
          };
        } else {
          const tilePoint = this.mapPoint(point);
          const candidate = this.nodeAt(tilePoint.x, tilePoint.y);
          this.drag = {
            type: 'pan', startX: point.x, startY: point.y,
            offsetX: this.offsetX, offsetY: this.offsetY,
            worldInteraction: true, candidateNodeId: candidate?.id || '', moved: false,
          };
        }
        this.updateCanvasCursor(point); return;
      }
      const tilePoint = this.mapPoint(point);
      if (canvasEditing && this.tool === 'fill' && !this.readonly) {
        this.beginMapEdit('填充画布'); this.fillMap(); this.commitMapEdit(); this.tool = 'brush'; this.mapTool = 'brush';
        this.root.querySelectorAll('[data-me2-tool]').forEach(item => item.classList.toggle('active', item.dataset.me2Tool === 'brush'));
        return;
      }
      if (canvasEditing && this.tool === 'eraser' && !this.readonly) {
        this.beginMapEdit('擦除画布'); this.eraseAt(tilePoint.x, tilePoint.y); this.drag = { type: 'erase' }; return;
      }
      if (canvasEditing && this.tool === 'brush' && !this.readonly) {
        this.beginMapEdit('绘制画布'); this.paintAt(tilePoint.x, tilePoint.y); this.drag = { type: 'paint' }; return;
      }
    }

    pointerMove(event) {
      const point = this.localPoint(event);
      if (this.drag?.type === 'pan') {
        if (this.drag.worldInteraction) {
          const distance = Math.hypot(point.x - this.drag.startX, point.y - this.drag.startY);
          if (!this.drag.moved && distance < 3) { this.updateCanvasCursor(point); return; }
          this.drag.moved = true;
        }
        this.offsetX = this.drag.offsetX + point.x - this.drag.startX;
        this.offsetY = this.drag.offsetY + point.y - this.drag.startY; this.renderCanvas(); this.updateCanvasCursor(point); return;
      }
      if (this.drag?.type === 'move-node') { this.dragWorldNode(point); return; }
      if (this.drag?.type === 'paint') { const p = this.mapPoint(point); this.paintAt(p.x, p.y); return; }
      if (this.drag?.type === 'erase') { const p = this.mapPoint(point); this.eraseAt(p.x, p.y); return; }
      if (this.drag?.type === 'crop-select') { this.dragMaterialSelection(point); return; }
      if (this.drag?.type === 'crop-resize') { this.dragCropResize(point); return; }
      const map = this.workspace === 'materials' && !this.isCanvasEditing() ? this.materialPoint(point) : this.mapPoint(point);
      this.root.querySelector('[data-pointer]').textContent = `${Math.floor(map.x)}, ${Math.floor(map.y)}`;
      this.updateCanvasCursor(point);
    }

    pointerUp(event) {
      if (this.canvas.hasPointerCapture(event.pointerId)) this.canvas.releasePointerCapture(event.pointerId);
      const completedDrag = this.drag;
      if (this.drag?.type === 'paint' || this.drag?.type === 'erase') this.commitMapEdit();
      if (this.drag?.type === 'crop-select' || this.drag?.type === 'crop-resize') this.renderLeft();
      this.drag = null;
      if (completedDrag?.type === 'pan' && completedDrag.worldInteraction && !completedDrag.moved && completedDrag.candidateNodeId) {
        this.selectNode(completedDrag.candidateNodeId, false);
      }
      this.updateCanvasCursor(this.localPoint(event));
    }

    dragWorldNode(point) {
      const node = this.nodeById.get(this.drag?.nodeId); if (!node) return;
      const position = this.mapPosition(point);
      const mapWidth = Number(this.document.import_metadata.width || 140);
      const mapHeight = Number(this.document.import_metadata.height || 100);
      const maxX = Math.max(0, mapWidth - Number(node.bounds.width || 1));
      const maxY = Math.max(0, mapHeight - Number(node.bounds.height || 1));
      const x = Math.max(0, Math.min(maxX, this.drag.startNodeX + Math.round(position.x - this.drag.startMapX)));
      const y = Math.max(0, Math.min(maxY, this.drag.startNodeY + Math.round(position.y - this.drag.startMapY)));
      if (x === node.bounds.x && y === node.bounds.y) { this.updateCanvasCursor(point); return; }
      node.bounds.x = x; node.bounds.y = y;
      node.extensions ||= {}; delete node.extensions.mask;
      this.drag.moved = true;
      const xInput = this.inspector.querySelector('[data-node-x]');
      const yInput = this.inspector.querySelector('[data-node-y]');
      if (xInput) xInput.value = String(x);
      if (yInput) yInput.value = String(y);
      this.changed = true;
      this.renderCanvas(); this.updateCanvasCursor(point);
    }

    materialPointerDown(point) {
      const source = this.sourceById.get(this.selectedSourceId); if (!source) return;
      const p = this.materialPoint(point); const selected = this.sliceById.get(this.selectedSliceId);
      if (this.materialView === 'slice' && !this.editingSlice) return;
      if (selected && this.editingSlice && !this.readonly && this.materialResizeHit(point)) {
        this.drag = { type: 'crop-resize', rect: deepClone(selected.pixel_rect) };
        this.updateCanvasCursor(point); return;
      }
      if (this.readonly || p.x < 0 || p.y < 0 || p.x >= source.width_px || p.y >= source.height_px) return;
      let slice = selected && this.editingSlice && selected.source_id === source.id ? selected : null;
      const pixelRect = this.materialGridRect(source, p, p);
      if (!slice) {
        slice = this.newMaterialSlice(source, pixelRect);
        this.document.material_slices.push(slice); this.selectedSliceId = slice.id; this.materialView = 'slice'; this.editingSlice = true;
        this.reindex(); this.renderAll();
      } else {
        slice.pixel_rect = pixelRect; slice.grid_rect = null; this.renderCanvas(); this.renderInspector();
      }
      this.changed = true;
      this.drag = { type: 'crop-select', start: p, sliceId: slice.id, sourceId: source.id };
    }

    dragMaterialSelection(point) {
      const source = this.sourceById.get(this.drag.sourceId); const slice = this.sliceById.get(this.drag.sliceId); if (!source || !slice) return;
      slice.pixel_rect = this.materialGridRect(source, this.drag.start, this.materialPoint(point));
      slice.grid_rect = null; this.changed = true; this.renderCanvas(); this.renderInspector();
    }

    dragCropResize(point) {
      const source = this.sourceById.get(this.selectedSourceId); const slice = this.sliceById.get(this.selectedSliceId); if (!source || !slice) return;
      const p = this.materialPoint(point); const base = this.drag.rect;
      slice.pixel_rect.width = this.snapMaterialSpan(p.x - base.x, source.width_px - base.x);
      slice.pixel_rect.height = this.snapMaterialSpan(p.y - base.y, source.height_px - base.y);
      slice.grid_rect = null;
      this.changed = true; this.renderCanvas(); this.renderInspector();
    }

    materialGridRect(source, start, end) {
      const maxColumn = Math.max(0, Math.ceil(source.width_px / MATERIAL_GRID_SIZE) - 1);
      const maxRow = Math.max(0, Math.ceil(source.height_px / MATERIAL_GRID_SIZE) - 1);
      const column = point => Math.max(0, Math.min(maxColumn, Math.floor(point.x / MATERIAL_GRID_SIZE)));
      const row = point => Math.max(0, Math.min(maxRow, Math.floor(point.y / MATERIAL_GRID_SIZE)));
      const left = Math.min(column(start), column(end)); const right = Math.max(column(start), column(end));
      const top = Math.min(row(start), row(end)); const bottom = Math.max(row(start), row(end));
      const x = left * MATERIAL_GRID_SIZE; const y = top * MATERIAL_GRID_SIZE;
      return { x, y,
        width: Math.min(source.width_px, (right + 1) * MATERIAL_GRID_SIZE) - x,
        height: Math.min(source.height_px, (bottom + 1) * MATERIAL_GRID_SIZE) - y };
    }

    snapMaterialSpan(value, available) {
      if (available <= MATERIAL_GRID_SIZE) return Math.max(1, available);
      const max = Math.max(MATERIAL_GRID_SIZE, Math.floor(available / MATERIAL_GRID_SIZE) * MATERIAL_GRID_SIZE);
      const snapped = Math.max(1, Math.round(value / MATERIAL_GRID_SIZE)) * MATERIAL_GRID_SIZE;
      return Math.max(MATERIAL_GRID_SIZE, Math.min(max, snapped));
    }

    materialResizeHit(point) {
      if (this.workspace !== 'materials' || this.materialPan || !this.editingSlice || this.readonly) return false;
      const slice = this.sliceById.get(this.selectedSliceId); if (!slice) return false;
      const p = this.materialPoint(point); const rect = slice.pixel_rect; const radius = 10 / this.zoom;
      return Math.abs(p.x - (rect.x + rect.width)) <= radius && Math.abs(p.y - (rect.y + rect.height)) <= radius;
    }

    updateCanvasCursor(point = null) {
      if (!this.canvasHost) return;
      const nodeMoving = this.drag?.type === 'move-node';
      const nodeMove = !nodeMoving && point && this.worldNodeMoveHit(point);
      const panMode = this.workspace === 'materials' && !this.isCanvasEditing()
        ? this.materialPan
        : (this.workspace === 'world' ? !nodeMove && !nodeMoving : this.tool === 'pan');
      const resizing = this.drag?.type === 'crop-resize' || (!this.drag && point && this.materialResizeHit(point));
      this.canvasHost.classList.toggle('is-pan', panMode);
      this.canvasHost.classList.toggle('is-panning', this.drag?.type === 'pan');
      this.canvasHost.classList.toggle('is-node-move', Boolean(nodeMove));
      this.canvasHost.classList.toggle('is-node-moving', nodeMoving);
      this.canvasHost.classList.toggle('is-resize', Boolean(resizing));
    }

    localPoint(event) { const rect = this.canvas.getBoundingClientRect(); return { x: event.clientX - rect.left, y: event.clientY - rect.top }; }
    mapPosition(point) { return { x: (point.x - this.offsetX) / (this.renderTile * this.zoom), y: (point.y - this.offsetY) / (this.renderTile * this.zoom) }; }
    mapPoint(point) { return { x: Math.floor((point.x - this.offsetX) / (this.renderTile * this.zoom)), y: Math.floor((point.y - this.offsetY) / (this.renderTile * this.zoom)) }; }
    materialPoint(point) { return { x: (point.x - this.offsetX) / this.zoom, y: (point.y - this.offsetY) / this.zoom }; }

    pointInNode(position, node) {
      const bounds = node?.bounds; if (!bounds) return false;
      return position.x >= bounds.x && position.x < bounds.x + bounds.width
        && position.y >= bounds.y && position.y < bounds.y + bounds.height;
    }

    worldNodeMoveHit(point) {
      if (this.workspace !== 'world' || this.readonly) return false;
      const selected = this.nodeById.get(this.selectedNodeId);
      return selected?.kind !== 'WORLD' && this.pointInNode(this.mapPosition(point), selected);
    }

    resetMapHistory() {
      this.undoStack = [];
      this.redoStack = [];
      this.activeMapEdit = null;
      this.updateMapHistoryControls();
    }

    beginMapEdit(label) {
      if (this.readonly || !this.isCanvasEditing()) return;
      this.activeMapEdit = { label, changes: new Map(), lastStamp: null };
    }

    readMapCellLayers(index) {
      const overrides = this.document.tile_overrides ||= {};
      const parts = this.document.tile_override_parts ||= {};
      const stacks = this.document.tile_override_layers ||= {};
      if (Array.isArray(stacks[index]) && stacks[index].length) return stacks[index];
      if (!Object.prototype.hasOwnProperty.call(overrides, index)) return [];
      return [{ slice_id: overrides[index], part: parts[index] || null }];
    }

    mapCellState(index) {
      const layers = this.readPaintCellLayers(index);
      return layers.length ? deepClone(layers) : null;
    }

    readPaintCellLayers(index) {
      const canvas = this.currentMaterialCanvas();
      return Array.isArray(canvas?.cells?.[index]) ? canvas.cells[index] : [];
    }

    writePaintCellState(index, state) {
      const canvas = this.currentMaterialCanvas(); if (!canvas) return;
      const layers = Array.isArray(state) ? state.filter(entry => entry?.slice_id) : [];
      if (layers.length) canvas.cells[index] = deepClone(layers); else delete canvas.cells[index];
    }

    paintSurfaceSize() {
      const canvas = this.currentMaterialCanvas();
      return { width: Number(canvas?.width_tiles || 0), height: Number(canvas?.height_tiles || 0) };
    }

    writeMapCellState(index, state) {
      const overrides = this.document.tile_overrides ||= {};
      const parts = this.document.tile_override_parts ||= {};
      const stacks = this.document.tile_override_layers ||= {};
      const layers = Array.isArray(state) ? state.filter(entry => entry?.slice_id) : [];
      if (!layers.length) {
        delete overrides[index]; delete parts[index]; delete stacks[index]; return;
      }
      stacks[index] = deepClone(layers);
      const top = layers[layers.length - 1];
      overrides[index] = top.slice_id;
      if (top.part) parts[index] = deepClone(top.part); else delete parts[index];
    }

    sameMapCellState(left, right) {
      return JSON.stringify(left) === JSON.stringify(right);
    }

    applyMapCellLayers(index, nextLayers) {
      const currentValue = this.mapCellState(index);
      const nextValue = Array.isArray(nextLayers) && nextLayers.length ? deepClone(nextLayers) : null;
      if (this.sameMapCellState(currentValue, nextValue)) return false;
      const existing = this.activeMapEdit?.changes.get(index);
      const before = existing ? existing.before : currentValue;
      this.writePaintCellState(index, nextValue);
      if (this.activeMapEdit) {
        if (this.sameMapCellState(before, nextValue)) this.activeMapEdit.changes.delete(index);
        else this.activeMapEdit.changes.set(index, { index, before, after: nextValue });
      }
      this.changed = true;
      return true;
    }

    applyMapCell(index, nextSliceId, nextPart = null) {
      return this.applyMapCellLayers(index, nextSliceId ? [{
        slice_id: nextSliceId,
        part: nextPart ? deepClone(nextPart) : null,
      }] : null);
    }

    appendMapCellLayer(index, sliceId, part = null) {
      const layers = this.mapCellState(index) || [];
      layers.push({ slice_id: sliceId, part: part ? deepClone(part) : null });
      return this.applyMapCellLayers(index, layers);
    }

    commitMapEdit() {
      const edit = this.activeMapEdit;
      this.activeMapEdit = null;
      if (!edit?.changes.size) { this.updateMapHistoryControls(); return; }
      this.undoStack.push({ label: edit.label, changes: [...edit.changes.values()] });
      if (this.undoStack.length > 50) this.undoStack.shift();
      this.redoStack = [];
      this.refreshCanvasImages();
      if (this.inspector) this.renderInspector();
      this.updateMapHistoryControls();
    }

    applyMapHistoryEntry(entry, valueKey) {
      for (const change of entry.changes) {
        const value = change[valueKey];
        this.writePaintCellState(change.index, value);
      }
      this.changed = true;
      this.refreshCanvasImages();
      this.renderCanvas();
      if (this.inspector) this.renderInspector();
      this.updateMapHistoryControls();
    }

    undoMapEdit() {
      if (!this.isCanvasEditing() || this.readonly || !this.undoStack.length) return;
      const entry = this.undoStack.pop();
      this.applyMapHistoryEntry(entry, 'before');
      this.redoStack.push(entry);
      this.updateMapHistoryControls();
      this.toast('已撤回画布操作', entry.label);
    }

    redoMapEdit() {
      if (!this.isCanvasEditing() || this.readonly || !this.redoStack.length) return;
      const entry = this.redoStack.pop();
      this.applyMapHistoryEntry(entry, 'after');
      this.undoStack.push(entry);
      this.updateMapHistoryControls();
      this.toast('已重做画布操作', entry.label);
    }

    updateMapHistoryControls() {
      const undo = this.root?.querySelector('[data-map-undo]');
      const redo = this.root?.querySelector('[data-map-redo]');
      if (undo) undo.disabled = this.readonly || !this.undoStack.length;
      if (redo) redo.disabled = this.readonly || !this.redoStack.length;
    }

    paintAt(x, y) {
      const { width, height } = this.paintSurfaceSize();
      if (x < 0 || y < 0 || x >= width || y >= height || !this.selectedPaintSliceId) return;
      const slice = this.sliceById.get(this.selectedPaintSliceId); if (!slice) return;
      if (this.sourceById?.get(slice.source_id)?.kind === 'CANVAS') return;
      if (this.stampSliceAt(x, y, slice, width, height, true)) this.renderCanvas();
    }

    eraseAt(x, y) {
      const { width, height } = this.paintSurfaceSize();
      if (x < 0 || y < 0 || x >= width || y >= height) return;
      const index = y * width + x; const layers = this.mapCellState(index) || [];
      const top = layers[layers.length - 1];
      const changed = top?.part?.placement_id
        ? this.removeMapPlacement(top.part.placement_id)
        : this.applyMapCellLayers(index, layers.slice(0, -1));
      if (changed) this.renderCanvas();
    }

    fillMap() {
      if (!this.selectedPaintSliceId) return;
      const { width, height } = this.paintSurfaceSize();
      const slice = this.sliceById.get(this.selectedPaintSliceId); if (!slice) return;
      if (this.sourceById?.get(slice.source_id)?.kind === 'CANVAS') return;
      const occupied = new Set(Object.keys(this.currentMaterialCanvas()?.cells || {}));
      for (const index of occupied) this.applyMapCell(Number(index), null);
      const footprint = this.sliceFootprint(slice);
      for (let y = 0; y + footprint.rows <= height; y += footprint.rows) {
        for (let x = 0; x + footprint.columns <= width; x += footprint.columns) {
          this.stampSliceAt(x, y, slice, width, height, false);
        }
      }
      this.renderCanvas();
      this.toast('画布已填充', `${width} × ${height} 格 · 每枚 ${footprint.columns} × ${footprint.rows}`);
    }

    stampSliceAt(x, y, slice, mapWidth, mapHeight, respectDragSpacing) {
      const footprint = this.sliceFootprint(slice);
      if (x < 0 || y < 0 || x + footprint.columns > mapWidth || y + footprint.rows > mapHeight) return false;
      const nextStamp = { x, y, columns: footprint.columns, rows: footprint.rows };
      const lastStamp = this.activeMapEdit?.lastStamp;
      if (respectDragSpacing && lastStamp && this.mapStampOverlaps(lastStamp, nextStamp)) return false;
      const anchorIndex = y * mapWidth + x;
      const anchorLayers = this.readPaintCellLayers(anchorIndex);
      const anchorTop = anchorLayers[anchorLayers.length - 1];
      const anchorPart = anchorTop?.part;
      if (anchorTop?.slice_id === slice.id
        && Number(anchorPart?.anchor_index) === anchorIndex
        && Number(anchorPart?.columns) === footprint.columns
        && Number(anchorPart?.rows) === footprint.rows) return false;

      const composited = this.sliceHasTransparency(slice);
      let changed = false;
      if (!composited) {
        const overlappingPlacements = new Set();
        for (let row = 0; row < footprint.rows; row++) for (let column = 0; column < footprint.columns; column++) {
          const index = (y + row) * mapWidth + x + column;
          const layers = this.readPaintCellLayers(index); const top = layers[layers.length - 1];
          if (top?.part?.placement_id) overlappingPlacements.add(top.part.placement_id);
        }
        for (const placementId of overlappingPlacements) changed = this.removeMapPlacement(placementId) || changed;
      }

      const placementId = uid('placement');
      for (let row = 0; row < footprint.rows; row++) for (let column = 0; column < footprint.columns; column++) {
        const index = (y + row) * mapWidth + x + column;
        const part = {
          placement_id: placementId, anchor_index: anchorIndex,
          column, row, columns: footprint.columns, rows: footprint.rows,
          rotation_degrees: this.sliceRotation(slice),
        };
        changed = (composited
          ? this.appendMapCellLayer(index, slice.id, part)
          : this.applyMapCell(index, slice.id, part)) || changed;
      }
      if (this.activeMapEdit) this.activeMapEdit.lastStamp = nextStamp;
      return changed;
    }

    removeMapPlacement(placementId) {
      let changed = false;
      const occupied = new Set(Object.keys(this.currentMaterialCanvas()?.cells || {}));
      for (const indexText of occupied) {
        const index = Number(indexText); const layers = this.mapCellState(index) || [];
        const remaining = layers.filter(layer => layer.part?.placement_id !== placementId);
        if (remaining.length !== layers.length) changed = this.applyMapCellLayers(index, remaining) || changed;
      }
      return changed;
    }

    mapStampOverlaps(left, right) {
      return left.x < right.x + right.columns && left.x + left.columns > right.x
        && left.y < right.y + right.rows && left.y + left.rows > right.y;
    }

    nodeAt(x, y) {
      const priority = { GAME_OBJECT: 4, ARENA: 3, SECTOR: 2, WORLD: 1 };
      return this.document.hierarchy_nodes.filter(node => {
        const r = node.bounds; return x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height;
      }).sort((a, b) => priority[b.kind] - priority[a.kind] || a.bounds.width * a.bounds.height - b.bounds.width * b.bounds.height)[0];
    }

    selectNode(id, locate) {
      if (this.nodeMaterialPreview?.nodeId !== id) this.nodeMaterialPreview = null;
      this.selectedNodeId = id; let node = this.nodeById.get(id);
      while (node?.parent_id) { this.expandedNodes.add(node.parent_id); node = this.nodeById.get(node.parent_id); }
      this.renderAll();
      if (locate) this.focusNode(this.nodeById.get(id));
    }

    focusNode(node) {
      if (!node) return; const r = node.bounds; const width = r.width * this.renderTile; const height = r.height * this.renderTile;
      this.zoom = Math.min(3, Math.max(.15, Math.min((this.viewportWidth - 160) / width, (this.viewportHeight - 160) / height)));
      this.offsetX = this.viewportWidth / 2 - (r.x * this.renderTile + width / 2) * this.zoom;
      this.offsetY = this.viewportHeight / 2 - (r.y * this.renderTile + height / 2) * this.zoom; this.renderCanvas();
    }

    nodeAddress(node) {
      if (!node) return [];
      const result = []; let current = node;
      while (current) { result.unshift(current.name); current = current.parent_id ? this.nodeById.get(current.parent_id) : null; }
      return result;
    }

    addChildNode(parentId) {
      const parent = this.nodeById.get(parentId); if (!parent || parent.kind === 'GAME_OBJECT') return;
      const next = { WORLD: 'SECTOR', SECTOR: 'ARENA', ARENA: 'GAME_OBJECT' }[parent.kind];
      const siblings = this.childrenByParent.get(parent.id) || [];
      const node = { id: uid(next.toLowerCase()), kind: next, parent_id: parent.id, name: `未命名 ${LEVEL_LABEL[next]}`, sort_order: siblings.length,
        bounds: { x: parent.bounds.x, y: parent.bounds.y, width: Math.max(1, Math.min(4, parent.bounds.width)), height: Math.max(1, Math.min(4, parent.bounds.height)) },
        semantic: '', material_slice_id: null, render_recipe_id: null, render_mode: 'LAYER_BACKED', skill_bindings: [], extensions: {} };
      this.document.hierarchy_nodes.push(node); this.changed = true; this.expandedNodes.add(parent.id); this.nodeMaterialPreview = null; this.selectedNodeId = node.id; this.reindex(); this.renderAll(); this.focusNode(node);
      requestAnimationFrame(() => this.inspector.querySelector('[data-node-name]')?.select());
    }

    deleteWorldNode(node) {
      if (this.readonly || !node || node.kind === 'WORLD' || !this.nodeById.has(node.id)) return;
      const nodeIds = new Set([node.id]);
      let collecting = true;
      while (collecting) {
        collecting = false;
        for (const candidate of this.document.hierarchy_nodes) {
          if (!nodeIds.has(candidate.id) && nodeIds.has(candidate.parent_id)) {
            nodeIds.add(candidate.id); collecting = true;
          }
        }
      }
      const descendantCount = nodeIds.size - 1;
      const descendantText = descendantCount ? `及其 ${descendantCount} 个下级节点` : '';
      if (!window.confirm(`确定删除节点“${node.name}”${descendantText}吗？`)) return;
      const parentId = node.parent_id || this.document.root_node_id;
      this.document.hierarchy_nodes = this.document.hierarchy_nodes.filter(item => !nodeIds.has(item.id));
      for (const id of nodeIds) this.expandedNodes.delete(id);
      this.nodeMaterialPreview = null; this.selectedNodeId = parentId;
      this.changed = true; this.reindex(); this.renderAll();
      this.toast('节点已删除', descendantCount ? `${node.name} · 含 ${descendantCount} 个下级节点` : node.name);
    }

    wheel(event) { event.preventDefault(); const point = this.localPoint(event); this.changeZoom(event.deltaY < 0 ? 1.12 : 1 / 1.12, point.x, point.y); }

    async importSourceFile(file) {
      if (!file || !file.type.startsWith('image/')) return;
      const dataUrl = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); });
      const image = await loadImage(dataUrl); const id = uid('source');
      const body = new FormData(); body.append('file', file);
      let uploaded;
      try {
        const response = await fetch('/api/v1/assets', { method: 'POST', body });
        const result = await response.json();
        if (!response.ok) throw new Error(result?.message || result?.error?.message || `HTTP ${response.status}`);
        uploaded = result;
      } catch (error) {
        this.toast('原图导入失败', error.message || '素材上传失败'); return;
      }
      const source = { id, name: file.name.replace(/\.[^.]+$/, ''), kind: 'UPLOADED', asset_id: uploaded.asset_id, asset_hash: uploaded.sha256, bundled_path: null,
        generated_color: null, media_type: uploaded.media_type || file.type, width_px: image.naturalWidth, height_px: image.naturalHeight,
        tile_width: 32, tile_height: 32, columns: Math.max(1, Math.floor(image.naturalWidth / 32)), rows: Math.max(1, Math.floor(image.naturalHeight / 32)),
        tile_count: Math.max(1, Math.floor(image.naturalWidth / 32) * Math.floor(image.naturalHeight / 32)), margin: 0, spacing: 0, first_gid: null };
      this.document.material_sources.unshift(source); this.images.set(id, image); this.imageUrls.set(id, `/api/v1/assets/${encodeURIComponent(uploaded.asset_id)}/content`); this.selectedCanvasId = ''; this.selectedSourceId = id; this.selectedSliceId = '';
      this.expandedMaterialGroups.add('sources');
      this.expandedSources.add(id); this.materialView = 'source'; this.changed = true; this.reindex(); this.renderAll(); requestAnimationFrame(() => this.fit());
      this.toast('原图已导入', `${source.name} · ${source.width_px}×${source.height_px}px`);
    }

    toast(title, message) { window.dispatchEvent(new CustomEvent('map-workspace:toast', { detail: { title, message } })); }
  }

  window.MapEditorV2 = MapEditorV2;
})();
