(() => {
  'use strict';

  const API = '/api/v1';
  const deepClone = value => JSON.parse(JSON.stringify(value));
  const escapeHtml = value => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);

  async function request(path, options = {}) {
    const response = await fetch(`${API}${path}`, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(body.error?.message || `请求失败（${response.status}）`);
      error.code = body.error?.code;
      error.details = body.error?.details;
      error.requestId = body.error?.request_id || response.headers.get('X-Request-ID');
      error.status = response.status;
      error.path = path;
      throw error;
    }
    return body;
  }

  function notify(message, title = '操作成功') {
    window.dispatchEvent(new CustomEvent('map-workspace:toast', { detail: { message, title } }));
  }

  function modal(action, id, focusId = null) {
    window.dispatchEvent(new CustomEvent('map-workspace:modal', { detail: { action, id, focusId } }));
  }

  function defaultPalette() {
    return [
      { id: 'ground', name: '地面', color: '#c9d7bb', collision: false },
      { id: 'path', name: '道路', color: '#d9c7a2', collision: false },
      { id: 'interior', name: '室内', color: '#e8d9c9', collision: false },
      { id: 'water', name: '水域', color: '#82b8c6', collision: true },
      { id: 'wall', name: '墙体', color: '#6f7b78', collision: true },
    ];
  }

  function normalizeWorld(input) {
    const world = deepClone(input || {});
    world.world_key ||= 'custom-map';
    world.world_name ||= '自定义地图';
    world.assets ||= [];
    world.map_id ??= null;
    world.map_revision_id ??= null;
    world.map_revision_hash ??= null;
    world.overlay ||= { definition_patch: {}, asset_additions: [], removed_asset_paths: [] };
    const definition = world.definition ||= {};
    definition.world ||= world.world_name;
    if (!Array.isArray(definition.size) || definition.size.length !== 2) definition.size = [32, 48];
    definition.size = definition.size.map(value => Math.max(1, Math.min(300, Number(value) || 1)));
    definition.tile_size = Math.max(1, Number(definition.tile_size) || 32);
    if (!Array.isArray(definition.tile_address_keys) || !definition.tile_address_keys.length) {
      definition.tile_address_keys = ['world', 'sector', 'arena', 'game_object'];
    }
    if (!Array.isArray(definition.tiles)) definition.tiles = [];
    const editor = definition.editor ||= {};
    editor.schema_version = 1;
    if (!Array.isArray(editor.palette) || !editor.palette.length) editor.palette = defaultPalette();
    if (!editor.cells || typeof editor.cells !== 'object' || Array.isArray(editor.cells)) editor.cells = {};
    if (!editor.spatial_assets || typeof editor.spatial_assets !== 'object' || Array.isArray(editor.spatial_assets)) editor.spatial_assets = {};
    return world;
  }

  function mergePatch(base, target) {
    if (same(base, target)) return undefined;
    if (Array.isArray(base) || Array.isArray(target)) return deepClone(target);
    if (base && target && typeof base === 'object' && typeof target === 'object') {
      const patch = {};
      for (const key of new Set([...Object.keys(base), ...Object.keys(target)])) {
        if (!(key in target)) patch[key] = null;
        else {
          const value = mergePatch(base[key], target[key]);
          if (value !== undefined) patch[key] = value;
        }
      }
      return Object.keys(patch).length ? patch : undefined;
    }
    return deepClone(target);
  }

  class GridEditor {
    constructor(root) {
      this.root = root;
      this.canvas = root.querySelector('[data-map-canvas]');
      this.host = root.querySelector('[data-map-canvas-host]');
      this.context = this.canvas.getContext('2d');
      this.world = null;
      this.tool = 'select';
      this.paletteId = 'ground';
      this.selected = null;
      this.zoom = 1;
      this.offsetX = 20;
      this.offsetY = 20;
      this.drag = null;
      this.spaceDown = false;
      this.history = [];
      this.future = [];
      this.changed = false;
      this.readonly = false;
      this.tools = [
        ['select', '↖', '选择'], ['brush', '✎', '画笔'], ['eraser', '⌫', '橡皮'],
        ['semantic', '⌖', '语义'], ['collision', '▦', '碰撞'], ['pan', '✥', '拖动画布'],
      ];
      this.bind();
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(this.host);
    }

    bind() {
      this.root.querySelector('[data-map-tools]').innerHTML = this.tools.map(([id, icon, name]) =>
        `<button class="map-tool-button${id === this.tool ? ' active' : ''}" data-map-tool="${id}"><span>${icon}</span>${name}</button>`
      ).join('');
      this.root.querySelectorAll('[data-map-tool]').forEach(button => button.addEventListener('click', () => {
        this.tool = button.dataset.mapTool;
        this.root.querySelectorAll('[data-map-tool]').forEach(item => item.classList.toggle('active', item === button));
        this.host.classList.toggle('pan-mode', this.tool === 'pan');
      }));
      this.root.querySelector('[data-map-zoom-in]').addEventListener('click', () => this.changeZoom(1.2));
      this.root.querySelector('[data-map-zoom-out]').addEventListener('click', () => this.changeZoom(1 / 1.2));
      this.root.querySelector('[data-map-fit]').addEventListener('click', () => this.fit());
      this.root.querySelector('[data-map-undo]').addEventListener('click', () => this.undo());
      this.root.querySelector('[data-map-redo]').addEventListener('click', () => this.redo());
      this.root.querySelector('[data-add-palette]').addEventListener('click', event => this.showPaletteForm(event.currentTarget));
      this.root.querySelector('[data-apply-semantics]').addEventListener('click', () => this.applyInspector());
      this.root.querySelector('[data-clear-semantics]').addEventListener('click', () => this.clearSelected());
      this.canvas.addEventListener('pointerdown', event => this.pointerDown(event));
      this.canvas.addEventListener('pointermove', event => this.pointerMove(event));
      this.canvas.addEventListener('pointerup', event => this.pointerUp(event));
      this.canvas.addEventListener('pointercancel', event => this.pointerUp(event));
      this.canvas.addEventListener('wheel', event => this.wheel(event), { passive: false });
      this.root.addEventListener('keydown', event => this.keydown(event));
      this.root.addEventListener('keyup', event => { if (event.code === 'Space') this.spaceDown = false; });
      this.root.tabIndex ||= -1;
    }

    setWorld(world) {
      this.world = normalizeWorld(world);
      this.history = [];
      this.future = [];
      this.changed = false;
      this.selected = null;
      this.renderPalette();
      this.refreshInspector();
      requestAnimationFrame(() => this.fit());
      this.updateHistoryButtons();
    }

    setReadOnly(value) {
      this.readonly = Boolean(value);
      this.root.querySelectorAll('[data-map-tool]').forEach(button => {
        button.disabled = this.readonly && !['select', 'semantic', 'pan'].includes(button.dataset.mapTool);
      });
      this.root.querySelectorAll('[data-palette-id]').forEach(button => { button.disabled = this.readonly; });
      this.root.querySelector('[data-add-palette]').disabled = this.readonly;
      this.refreshInspector();
    }

    getWorld() { return deepClone(this.world); }

    get definition() { return this.world.definition; }
    get editor() { return this.definition.editor; }
    get height() { return this.definition.size[0]; }
    get width() { return this.definition.size[1]; }
    get cellSize() { return 18 * this.zoom; }

    renderPalette() {
      const palette = this.root.querySelector('[data-map-palette]');
      palette.innerHTML = this.editor.palette.map(item => `
        <button class="map-palette-item${item.id === this.paletteId ? ' active' : ''}" data-palette-id="${escapeHtml(item.id)}">
          <span class="map-palette-swatch" style="background:${escapeHtml(item.color || '#eef2ef')}">${escapeHtml(item.emoji || '')}</span>
          <span>${escapeHtml(item.name)}${item.collision ? ' · 碰撞' : ''}</span>
        </button>`).join('');
      palette.querySelectorAll('[data-palette-id]').forEach(button => button.addEventListener('click', () => {
        if (this.readonly) return;
        this.paletteId = button.dataset.paletteId;
        palette.querySelectorAll('[data-palette-id]').forEach(item => item.classList.toggle('active', item === button));
        this.tool = 'brush';
        this.root.querySelectorAll('[data-map-tool]').forEach(item => item.classList.toggle('active', item.dataset.mapTool === 'brush'));
      }));
      this.setReadOnly(this.readonly);
    }

    showPaletteForm(button) {
      if (this.readonly) return;
      const existing = this.root.querySelector('[data-palette-form]');
      if (existing) { existing.remove(); return; }
      const form = document.createElement('div');
      form.dataset.paletteForm = '';
      form.innerHTML = `
        <input class="control" data-palette-name placeholder="画块名称" style="margin-top:8px" />
        <div style="display:flex;gap:7px;margin-top:7px"><input type="color" data-palette-color value="#b8c8a6" style="width:42px"><label class="map-check-row" style="margin:0"><input type="checkbox" data-palette-collision> 碰撞</label></div>
        <button class="btn btn-primary btn-sm" data-palette-confirm style="width:100%;margin-top:7px">添加</button>`;
      button.insertAdjacentElement('beforebegin', form);
      form.querySelector('[data-palette-confirm]').addEventListener('click', () => {
        const name = form.querySelector('[data-palette-name]').value.trim();
        if (!name) return form.querySelector('[data-palette-name]').focus();
        this.snapshot();
        const id = `custom-${Date.now().toString(36)}`;
        this.editor.palette.push({
          id, name,
          color: form.querySelector('[data-palette-color]').value,
          collision: form.querySelector('[data-palette-collision]').checked,
        });
        this.paletteId = id;
        this.changed = true;
        form.remove();
        this.renderPalette();
        this.render();
      });
      form.querySelector('[data-palette-name]').focus();
    }

    resize() {
      const rect = this.host.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const ratio = window.devicePixelRatio || 1;
      this.canvas.width = Math.max(1, Math.round(rect.width * ratio));
      this.canvas.height = Math.max(1, Math.round(rect.height * ratio));
      this.canvas.style.width = `${rect.width}px`;
      this.canvas.style.height = `${rect.height}px`;
      this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
      this.viewportWidth = rect.width;
      this.viewportHeight = rect.height;
      this.render();
    }

    fit() {
      if (!this.world || !this.viewportWidth) return;
      this.zoom = Math.max(.18, Math.min(2.4,
        Math.min((this.viewportWidth - 48) / (this.width * 18), (this.viewportHeight - 48) / (this.height * 18))));
      this.offsetX = (this.viewportWidth - this.width * this.cellSize) / 2;
      this.offsetY = (this.viewportHeight - this.height * this.cellSize) / 2;
      this.render();
    }

    changeZoom(factor, anchor = null) {
      const previous = this.cellSize;
      const point = anchor || { x: this.viewportWidth / 2, y: this.viewportHeight / 2 };
      const mapX = (point.x - this.offsetX) / previous;
      const mapY = (point.y - this.offsetY) / previous;
      this.zoom = Math.max(.15, Math.min(4, this.zoom * factor));
      this.offsetX = point.x - mapX * this.cellSize;
      this.offsetY = point.y - mapY * this.cellSize;
      this.render();
    }

    wheel(event) {
      event.preventDefault();
      const rect = this.canvas.getBoundingClientRect();
      this.changeZoom(event.deltaY < 0 ? 1.12 : 1 / 1.12, { x: event.clientX - rect.left, y: event.clientY - rect.top });
    }

    eventPoint(event) {
      const rect = this.canvas.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    }

    pointCell(point) {
      const x = Math.floor((point.x - this.offsetX) / this.cellSize);
      const y = Math.floor((point.y - this.offsetY) / this.cellSize);
      return x >= 0 && y >= 0 && x < this.width && y < this.height ? { x, y } : null;
    }

    pointerDown(event) {
      this.root.focus({ preventScroll: true });
      this.canvas.setPointerCapture(event.pointerId);
      const point = this.eventPoint(event);
      const cell = this.pointCell(point);
      if (this.tool === 'pan' || this.spaceDown || event.button === 1 || (event.button === 0 && !cell)) {
        this.drag = { mode: 'pan', pointerId: event.pointerId, point, offsetX: this.offsetX, offsetY: this.offsetY };
        this.host.classList.add('dragging');
        return;
      }
      if (!cell) return;
      if (this.readonly && !['select', 'semantic'].includes(this.tool)) return;
      if (event.button === 0 && ['brush', 'eraser', 'collision'].includes(this.tool)) {
        this.drag = { mode: 'pending-edit', pointerId: event.pointerId, point, cell, offsetX: this.offsetX, offsetY: this.offsetY };
        return;
      }
      this.snapshot();
      this.drag = { mode: 'edit', pointerId: event.pointerId, last: null };
      this.applyTool(cell);
    }

    pointerMove(event) {
      if (!this.drag || this.drag.pointerId !== event.pointerId) return;
      const point = this.eventPoint(event);
      if (this.drag.mode === 'pending-edit') {
        const distance = Math.hypot(point.x - this.drag.point.x, point.y - this.drag.point.y);
        if (distance >= 7) {
          this.drag.mode = 'pan';
          this.host.classList.add('dragging');
          this.offsetX = this.drag.offsetX + point.x - this.drag.point.x;
          this.offsetY = this.drag.offsetY + point.y - this.drag.point.y;
          this.render();
        }
      } else if (this.drag.mode === 'pan') {
        this.offsetX = this.drag.offsetX + point.x - this.drag.point.x;
        this.offsetY = this.drag.offsetY + point.y - this.drag.point.y;
        this.render();
      } else if (['brush', 'eraser', 'collision'].includes(this.tool)) {
        const cell = this.pointCell(point);
        if (cell) this.applyTool(cell);
      }
    }

    pointerUp(event) {
      if (!this.drag || this.drag.pointerId !== event.pointerId) return;
      if (this.drag.mode === 'pending-edit') {
        this.snapshot();
        this.applyTool(this.drag.cell);
      }
      if (this.drag.mode === 'edit' && !this.changed) this.history.pop();
      this.drag = null;
      this.host.classList.remove('dragging');
      this.updateHistoryButtons();
    }

    applyTool(cell) {
      const key = `${cell.x},${cell.y}`;
      if (this.drag?.last === key) return;
      if (this.drag) this.drag.last = key;
      this.selected = cell;
      if (this.tool === 'brush') {
        const palette = this.editor.palette.find(item => item.id === this.paletteId) || this.editor.palette[0];
        this.editor.cells[key] = { kind: palette.id };
        this.updateRuntimeTile(cell, { collision: Boolean(palette.collision), tile: palette.id });
        this.changed = true;
      } else if (this.tool === 'eraser') {
        delete this.editor.cells[key];
        this.removeRuntimeTile(cell);
        this.changed = true;
      } else if (this.tool === 'collision') {
        const tile = this.runtimeTile(cell);
        this.updateRuntimeTile(cell, { collision: !Boolean(tile?.collision) });
        this.changed = true;
      }
      this.refreshInspector();
      this.render();
    }

    runtimeTile(cell) {
      return this.definition.tiles.find(tile => tile.coord?.[0] === cell.x && tile.coord?.[1] === cell.y) || null;
    }

    updateRuntimeTile(cell, changes) {
      let tile = this.runtimeTile(cell);
      if (!tile) {
        tile = { coord: [cell.x, cell.y] };
        this.definition.tiles.push(tile);
      }
      Object.assign(tile, changes);
      if (tile.collision === false && !tile.address?.length && !this.editor.cells[`${cell.x},${cell.y}`]) {
        this.removeRuntimeTile(cell);
      }
    }

    removeRuntimeTile(cell) {
      this.definition.tiles = this.definition.tiles.filter(tile => !(tile.coord?.[0] === cell.x && tile.coord?.[1] === cell.y));
    }

    refreshInspector() {
      const coordinate = this.root.querySelector('[data-map-coordinate]');
      const tile = this.selected ? this.runtimeTile(this.selected) : null;
      coordinate.textContent = this.selected ? `x: ${this.selected.x} · y: ${this.selected.y}` : '尚未选择格子';
      this.root.querySelectorAll('[data-map-address]').forEach(input => {
        input.disabled = !this.selected;
        input.value = tile?.address?.[Number(input.dataset.mapAddress)] || '';
      });
      const collision = this.root.querySelector('[data-map-collision]');
      collision.disabled = !this.selected;
      collision.checked = Boolean(tile?.collision);
      this.root.querySelectorAll('[data-map-address]').forEach(input => { input.disabled = !this.selected || this.readonly; });
      collision.disabled = !this.selected || this.readonly;
      this.root.querySelector('[data-apply-semantics]').disabled = !this.selected || this.readonly;
      this.root.querySelector('[data-clear-semantics]').disabled = !this.selected || this.readonly;
    }

    applyInspector() {
      if (!this.selected) return;
      this.snapshot();
      const address = [...this.root.querySelectorAll('[data-map-address]')]
        .map(input => input.value.trim());
      while (address.length && !address.at(-1)) address.pop();
      if (address.some((value, index) => !value && address.slice(index + 1).some(Boolean))) {
        notify('空间语义必须按“区域 → 场所 → 对象”连续填写。', '无法保存');
        this.history.pop();
        return;
      }
      const changes = { collision: this.root.querySelector('[data-map-collision]').checked };
      if (address.length) changes.address = address;
      else changes.address = undefined;
      this.updateRuntimeTile(this.selected, changes);
      const tile = this.runtimeTile(this.selected);
      if (tile && changes.address === undefined) delete tile.address;
      this.changed = true;
      this.render();
      this.updateHistoryButtons();
    }

    clearSelected() {
      if (!this.selected) return;
      this.snapshot();
      const tile = this.runtimeTile(this.selected);
      if (tile) {
        delete tile.address;
        tile.collision = false;
        if (!this.editor.cells[`${this.selected.x},${this.selected.y}`]) this.removeRuntimeTile(this.selected);
      }
      this.changed = true;
      this.refreshInspector();
      this.render();
      this.updateHistoryButtons();
    }

    snapshot() {
      this.history.push(deepClone(this.world));
      if (this.history.length > 30) this.history.shift();
      this.future = [];
      this.changed = false;
    }

    undo() {
      if (!this.history.length) return;
      this.future.push(deepClone(this.world));
      this.world = this.history.pop();
      this.changed = true;
      this.renderPalette(); this.refreshInspector(); this.render(); this.updateHistoryButtons();
    }

    redo() {
      if (!this.future.length) return;
      this.history.push(deepClone(this.world));
      this.world = this.future.pop();
      this.changed = true;
      this.renderPalette(); this.refreshInspector(); this.render(); this.updateHistoryButtons();
    }

    updateHistoryButtons() {
      this.root.querySelector('[data-map-undo]').disabled = !this.history.length;
      this.root.querySelector('[data-map-redo]').disabled = !this.future.length;
    }

    keydown(event) {
      if (event.code === 'Space') { this.spaceDown = true; event.preventDefault(); }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault();
        event.shiftKey ? this.redo() : this.undo();
      }
    }

    render() {
      if (!this.world || !this.viewportWidth) return;
      const context = this.context;
      context.clearRect(0, 0, this.viewportWidth, this.viewportHeight);
      context.fillStyle = '#e9eeeb';
      context.fillRect(0, 0, this.viewportWidth, this.viewportHeight);
      const size = this.cellSize;
      const xStart = Math.max(0, Math.floor(-this.offsetX / size));
      const yStart = Math.max(0, Math.floor(-this.offsetY / size));
      const xEnd = Math.min(this.width, Math.ceil((this.viewportWidth - this.offsetX) / size));
      const yEnd = Math.min(this.height, Math.ceil((this.viewportHeight - this.offsetY) / size));
      const runtime = new Map(this.definition.tiles.map(tile => [`${tile.coord?.[0]},${tile.coord?.[1]}`, tile]));
      const palette = new Map(this.editor.palette.map(item => [item.id, item]));
      for (let y = yStart; y < yEnd; y += 1) {
        for (let x = xStart; x < xEnd; x += 1) {
          const key = `${x},${y}`;
          const tile = runtime.get(key);
          const visual = palette.get(this.editor.cells[key]?.kind);
          let fill = visual?.color || (tile?.address?.length ? '#d8eadf' : '#f8faf8');
          if (tile?.collision && !visual) fill = '#71807c';
          context.fillStyle = fill;
          context.fillRect(this.offsetX + x * size, this.offsetY + y * size, size + .5, size + .5);
          if (tile?.address?.length && size > 8) {
            context.fillStyle = '#16715c';
            context.beginPath();
            context.arc(this.offsetX + (x + .72) * size, this.offsetY + (y + .28) * size, Math.max(1.5, size * .09), 0, Math.PI * 2);
            context.fill();
          }
          if (tile?.collision && size > 9) {
            context.strokeStyle = 'rgba(82, 38, 32, .45)';
            context.lineWidth = 1;
            context.beginPath();
            context.moveTo(this.offsetX + x * size + 3, this.offsetY + y * size + 3);
            context.lineTo(this.offsetX + (x + 1) * size - 3, this.offsetY + (y + 1) * size - 3);
            context.stroke();
          }
        }
      }
      if (size >= 7) {
        context.strokeStyle = 'rgba(45, 67, 59, .13)';
        context.lineWidth = 1;
        context.beginPath();
        for (let x = xStart; x <= xEnd; x += 1) {
          const px = Math.round(this.offsetX + x * size) + .5;
          context.moveTo(px, this.offsetY + yStart * size);
          context.lineTo(px, this.offsetY + yEnd * size);
        }
        for (let y = yStart; y <= yEnd; y += 1) {
          const py = Math.round(this.offsetY + y * size) + .5;
          context.moveTo(this.offsetX + xStart * size, py);
          context.lineTo(this.offsetX + xEnd * size, py);
        }
        context.stroke();
      }
      const scene = this.definition.spatial_scene;
      if (scene?.placements?.length) {
        const metersPerTile = Number(scene.meters_per_tile) || 1;
        scene.placements.forEach(placement => {
          const contract = this.editor.spatial_assets?.[placement.spatial_asset_revision_id];
          if (!contract) return;
          const x = this.offsetX + (placement.x_m / metersPerTile + .5) * size;
          const y = this.offsetY + (placement.y_m / metersPerTile + .5) * size;
          const state = { ...(contract.initial_state || {}), ...(placement.state_overrides || {}) };
          const stateValue = state.phase || state.state || state.mode;
          const variant = contract.appearance?.state_variants?.[stateValue] || {};
          const color = variant.color || contract.appearance?.color || '#78b6a9';
          const emoji = variant.emoji || contract.appearance?.emoji;
          context.save();
          context.translate(x, y);
          context.rotate((Number(placement.rotation_degrees) || 0) * Math.PI / 180);
          if (contract.kind === 'ZONE') {
            context.globalAlpha = .3;
            context.fillStyle = color;
            context.fillRect(-size * .48, -size * .48, size * .96, size * .96);
            context.globalAlpha = .85;
            context.strokeStyle = color;
            context.setLineDash([3, 2]);
            context.strokeRect(-size * .48, -size * .48, size * .96, size * .96);
          } else if (emoji) {
            context.font = `${Math.max(12, size * .78)}px system-ui`;
            context.textAlign = 'center'; context.textBaseline = 'middle';
            context.fillText(emoji, 0, 0);
          } else {
            context.fillStyle = color;
            context.fillRect(-size * .3, -size * .3, size * .6, size * .6);
          }
          context.restore();
        });
      }
      if (this.selected) {
        context.strokeStyle = '#f0a33a';
        context.lineWidth = 2;
        context.strokeRect(this.offsetX + this.selected.x * size + 1, this.offsetY + this.selected.y * size + 1, size - 2, size - 2);
      }
      this.root.querySelector('[data-map-zoom]').textContent = `${Math.round(this.zoom * 100)}%`;
    }
  }

  const manager = {
    maps: [],
    selectorMaps: [],
    selectedMapId: null,
    detail: null,
    draft: null,
    revisions: [],
    experiment: null,
    baseExperimentRevision: null,
    publicEditor: null,
    overlayEditor: null,
    page: 1,
    pageSize: 5,
    status: '',
    query: '',
    listGeneration: 0,
    searchTimer: null,
    initialized: false,

    init() {
      if (this.initialized) return;
      this.initialized = true;
      this.publicEditor = new GridEditor(document.getElementById('publicMapEditor'));
      this.overlayEditor = new GridEditor(document.getElementById('experimentMapEditor'));
      document.getElementById('createMapBtn').addEventListener('click', () => this.openCreate());
      document.getElementById('backToMapsBtn').addEventListener('click', () => this.showCatalog());
      document.getElementById('saveMapBtn').addEventListener('click', () => this.savePublic().catch(error => this.fail(error)));
      document.getElementById('publishMapBtn').addEventListener('click', () => this.publishOrFork().catch(error => this.fail(error)));
      document.getElementById('mapSearch').addEventListener('input', event => {
        clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => {
          this.query = event.target.value.trim();
          this.page = 1;
          this.loadMaps().catch(error => this.fail(error));
        }, 250);
      });
      document.querySelectorAll('[data-map-filter]').forEach(tab => tab.addEventListener('click', () => {
        document.querySelectorAll('[data-map-filter]').forEach(item => item.classList.toggle('active', item === tab));
        this.status = tab.dataset.mapFilter === 'all' ? '' : tab.dataset.mapFilter.toUpperCase();
        this.page = 1;
        this.loadMaps().catch(error => this.fail(error));
      }));
      document.getElementById('mapPagination').addEventListener('click', event => {
        const pageButton = event.target.closest('[data-map-page]');
        const totalPages = Number(document.getElementById('mapPagination').dataset.totalPages || 1);
        if (pageButton) this.page = Number(pageButton.dataset.mapPage);
        else if (event.target === document.getElementById('mapPrev')) this.page = Math.max(1, this.page - 1);
        else if (event.target === document.getElementById('mapNext')) this.page = Math.min(totalPages, this.page + 1);
        else return;
        this.loadMaps().catch(error => this.fail(error));
      });
      document.getElementById('confirmCreateMap').addEventListener('click', () => this.create().catch(error => this.fail(error)));
      ['closeCreateMap', 'cancelCreateMap'].forEach(id => document.getElementById(id).addEventListener('click', () => modal('close', 'createMapModal')));
      document.getElementById('applyExperimentMapBtn').addEventListener('click', () => this.selectExperimentMap().catch(error => this.fail(error)));
      document.getElementById('tuneExperimentMapBtn').addEventListener('click', () => this.openExperimentTuning().catch(error => this.fail(error)));
      document.getElementById('saveExperimentMapOverlay').addEventListener('click', () => this.saveExperimentOverlay().catch(error => this.fail(error)));
      ['closeExperimentMapOverlay', 'cancelExperimentMapOverlay'].forEach(id => document.getElementById(id).addEventListener('click', () => modal('close', 'experimentMapOverlayModal')));
      document.querySelectorAll('[data-map-tab]').forEach(tab => tab.addEventListener('click', () => this.setTab(tab.dataset.mapTab)));
      window.addEventListener('spatial-asset-workspace:add-to-map', event => this.addSpatialAsset(event.detail?.asset));
    },

    async activate() {
      this.init();
      this.query = document.getElementById('mapSearch').value.trim();
      await this.loadMaps();
      const mapId = new URLSearchParams(location.search).get('map_id');
      if (mapId) await this.openMap(mapId, false);
    },

    async loadMaps() {
      this.init();
      const generation = ++this.listGeneration;
      const requestState = { page: this.page, status: this.status, query: this.query };
      const params = new URLSearchParams({ page: String(this.page), page_size: String(this.pageSize) });
      if (this.query) params.set('q', this.query);
      if (this.status) params.set('status', this.status);
      const selectorParams = new URLSearchParams({ page: '1', page_size: '100' });
      const [result, selectorResult] = await Promise.all([
        request(`/maps?${params}`),
        request(`/maps?${selectorParams}`),
      ]);
      if (generation !== this.listGeneration
        || requestState.page !== this.page
        || requestState.status !== this.status
        || requestState.query !== this.query) return;
      const lastPage = Math.max(1, result.total_pages || 1);
      if (this.page > lastPage) {
        this.page = lastPage;
        await this.loadMaps();
        return;
      }
      this.maps = result.items;
      this.selectorMaps = selectorResult.items;
      const grid = document.getElementById('mapCatalogGrid');
      grid.innerHTML = this.maps.length ? this.maps.map(item => `
        <button class="map-card" data-map-id="${item.id}">
          <span class="map-card-top"><span class="map-state ${item.current_draft ? 'draft' : ''}">${item.current_draft ? '编辑中' : '已发布'}</span><code>${escapeHtml(item.map_key)}</code></span>
          <h2>${escapeHtml(item.name)}</h2><p>${escapeHtml(item.description || '暂无用途说明')}</p>
          <span class="map-card-foot"><span>${item.dimensions ? `${item.dimensions[1]} × ${item.dimensions[0]}` : '待设置尺寸'}</span><span>${item.usage_count} 个实验使用</span></span>
        </button>`).join('') : '<div class="empty-state"><strong>没有符合条件的地图</strong><span>可以清除搜索词、切换状态，或新建一张地图。</span></div>';
      grid.querySelectorAll('[data-map-id]').forEach(card => card.addEventListener('click', () => this.openMap(card.dataset.mapId).catch(error => this.fail(error))));
      const footer = document.getElementById('mapListFooter');
      footer.hidden = result.total === 0;
      if (result.total) {
        const first = (result.page - 1) * result.page_size + 1;
        const last = Math.min(result.total, first + result.items.length - 1);
        document.getElementById('mapCatalogCount').textContent = `显示 ${first}–${last}，共 ${result.total} 张地图`;
      }
      this.renderMapPages(result.total_pages || 1);
      this.updateMapStatusCounts(result.status_counts || {});
      this.populateMapSelectors();
    },

    renderMapPages(totalPages) {
      const pagination = document.getElementById('mapPagination');
      pagination.hidden = totalPages <= 1;
      pagination.dataset.totalPages = String(totalPages);
      document.getElementById('mapPages').innerHTML = Array.from({ length: totalPages }, (_, index) => {
        const page = index + 1;
        return `<button class="page-button${page === this.page ? ' active' : ''}" data-map-page="${page}"${page === this.page ? ' aria-current="page"' : ''}>${page}</button>`;
      }).join('');
      document.getElementById('mapPrev').disabled = this.page <= 1;
      document.getElementById('mapNext').disabled = this.page >= totalPages;
    },

    updateMapStatusCounts(counts) {
      const labels = { all: '全部', draft: '编辑中', published: '已发布' };
      document.querySelectorAll('[data-map-filter]').forEach(tab => {
        const key = tab.dataset.mapFilter;
        const count = key === 'all' ? counts.ALL : counts[key.toUpperCase()];
        tab.textContent = Number.isFinite(count) ? `${labels[key]} ${count}` : labels[key];
      });
    },

    populateMapSelectors() {
      const catalog = this.selectorMaps.length ? this.selectorMaps : this.maps;
      const published = catalog.filter(item => item.current_published);
      const source = document.getElementById('newMapSource');
      const currentSource = source.value;
      source.innerHTML = '<option value="">空白地图</option>' + published
        .map(item => `<option value="${item.current_published.id}">${escapeHtml(item.name)} · v${item.current_published.revision_no}</option>`).join('');
      source.value = currentSource;
      const select = document.getElementById('experimentMapSelect');
      const current = this.experiment?.world?.map_revision_id || select.value;
      select.innerHTML = '<option value="">请选择已发布地图</option>' + published
        .map(item => `<option value="${item.current_published.id}">${escapeHtml(item.name)} · v${item.current_published.revision_no}</option>`).join('');
      select.value = current || '';
      const experimentCreateSelect = document.getElementById('newExperimentMap');
      if (experimentCreateSelect) {
        const previousCreateValue = experimentCreateSelect.value;
        experimentCreateSelect.innerHTML = published
          .map(item => `<option value="${item.current_published.id}">${escapeHtml(item.name)} · v${item.current_published.revision_no}${item.map_key === 'the-ville' ? ' · 默认' : ''}</option>`).join('');
        experimentCreateSelect.value = published.some(item => item.current_published.id === previousCreateValue)
          ? previousCreateValue
          : (published.find(item => item.map_key === 'the-ville')?.current_published.id || published[0]?.current_published.id || '');
      }
    },

    async prepareExperimentCreate() {
      this.init();
      if (!this.selectorMaps.length) await this.loadMaps();
      this.populateMapSelectors();
      const baseline = this.selectorMaps.find(item => item.map_key === 'the-ville')?.current_published?.id;
      if (baseline) document.getElementById('newExperimentMap').value = baseline;
    },

    async openMap(mapId, push = true) {
      this.init();
      this.detail = await request(`/maps/${mapId}`);
      this.selectedMapId = mapId;
      this.revisions = (await request(`/maps/${mapId}/revisions`)).items;
      this.draft = this.detail.current_draft
        ? await request(`/maps/${mapId}/draft`)
        : await request(`/maps/${mapId}/revisions/${this.detail.current_published.id}`);
      this.publicEditor.setWorld(this.draft.world);
      document.getElementById('mapCatalogShell').hidden = true;
      document.getElementById('mapEditorShell').hidden = false;
      requestAnimationFrame(() => {
        this.publicEditor.resize();
        this.publicEditor.fit();
      });
      document.getElementById('mapEditorTitle').textContent = this.detail.name;
      document.getElementById('mapEditorMeta').textContent = `${this.detail.map_key} · ${this.draft.world.definition.size[1]} × ${this.draft.world.definition.size[0]} · Revision ${this.draft.revision_no}`;
      const editable = this.draft.state === 'DRAFT';
      this.publicEditor.setReadOnly(!editable);
      const state = document.getElementById('mapEditorState');
      state.textContent = editable ? '草稿' : '已发布';
      state.classList.toggle('draft', editable);
      document.getElementById('saveMapBtn').disabled = !editable;
      document.getElementById('publishMapBtn').textContent = editable ? '发布版本' : '创建新修订';
      this.renderAudit();
      window.dispatchEvent(new CustomEvent('map-workspace:selection', { detail: { mapId } }));
      if (push) this.replaceMapUrl(mapId);
    },

    showCatalog() {
      this.selectedMapId = null;
      document.getElementById('mapCatalogShell').hidden = false;
      document.getElementById('mapEditorShell').hidden = true;
      window.dispatchEvent(new CustomEvent('map-workspace:selection', { detail: { mapId: null } }));
      this.replaceMapUrl(null);
    },

    replaceMapUrl(mapId) {
      const url = new URL(location.href);
      url.search = '';
      url.searchParams.set('view', 'maps');
      if (mapId) url.searchParams.set('map_id', mapId);
      history.replaceState(null, '', `${url.pathname}${url.search}`);
    },

    setTab(name) {
      document.querySelectorAll('[data-map-tab]').forEach(tab => {
        const active = tab.dataset.mapTab === name;
        tab.classList.toggle('active', active);
        tab.setAttribute('aria-selected', String(active));
      });
      document.querySelectorAll('#publicMapEditor [data-map-panel]').forEach(panel => {
        panel.classList.toggle('active', panel.dataset.mapPanel.split(' ').includes(name));
      });
      if (name === 'audit') this.renderAudit();
      else if (name === 'assets') window.SpatialAssetWorkspace?.activate().catch(error => this.fail(error));
      else requestAnimationFrame(() => this.publicEditor.resize());
    },

    addSpatialAsset(asset) {
      if (!asset || !this.draft || this.draft.state !== 'DRAFT') {
        notify('请先打开一个可编辑的地图草稿。', '无法加入地图');
        return;
      }
      const contract = asset.contract;
      const definition = this.publicEditor.definition;
      const scene = definition.spatial_scene ||= {
        schema_version: 'ga-spatial-scene/v1', meters_per_tile: 1,
        palette_refs: {}, placements: [],
      };
      this.publicEditor.editor.spatial_assets[asset.revision_id] = deepClone(contract);
      if (['TILE', 'MARKING'].includes(contract.kind)) {
        scene.palette_refs[asset.asset_key] = asset.revision_id;
        const palette = this.publicEditor.editor.palette;
        const visual = {
          id: asset.asset_key,
          name: asset.name,
          color: contract.appearance.color || '#eef2ef',
          emoji: contract.appearance.emoji || '',
          collision: Boolean(contract.physics.collision),
          spatial_asset_revision_id: asset.revision_id,
        };
        const index = palette.findIndex(item => item.id === asset.asset_key);
        if (index >= 0) palette[index] = visual; else palette.push(visual);
        this.publicEditor.paletteId = asset.asset_key;
        this.publicEditor.renderPalette();
        notify(`${asset.name} 已加入画块面板；选择画笔即可使用。`, '画块已加入地图');
      } else {
        const existing = new Set(scene.placements.map(item => item.instance_key));
        const base = asset.asset_key; let key = base; let suffix = 2;
        while (existing.has(key)) key = `${base}-${suffix++}`;
        scene.placements.push({
          instance_key: key,
          spatial_asset_revision_id: asset.revision_id,
          x_m: (definition.size[1] * scene.meters_per_tile) / 2,
          y_m: (definition.size[0] * scene.meters_per_tile) / 2,
          rotation_degrees: 0,
          state_overrides: {},
          capability_parameter_overrides: {},
        });
        notify(`${asset.name} 已放置在地图中心；保存后会锁定其版本引用。`, '物件已加入地图');
      }
      this.publicEditor.changed = true;
      this.publicEditor.render();
      this.renderAudit();
    },

    renderAudit() {
      if (!this.draft) return;
      const definition = this.draft.world.definition;
      const tiles = definition.tiles || [];
      const collisions = tiles.filter(tile => tile.collision).length;
      const addressed = tiles.filter(tile => tile.address?.length).length;
      const spatialScene = definition.spatial_scene;
      const spatialAssets = spatialScene
        ? Object.keys(spatialScene.palette_refs || {}).length + (spatialScene.placements || []).length
        : 0;
      document.querySelector('[data-map-audit-cards]').innerHTML = `
        <div class="map-audit-card"><span>地图尺寸</span><strong>${definition.size[1]} × ${definition.size[0]}</strong></div>
        <div class="map-audit-card"><span>碰撞 Tile</span><strong>${collisions.toLocaleString('zh-CN')}</strong></div>
        <div class="map-audit-card"><span>语义 Tile</span><strong>${addressed.toLocaleString('zh-CN')}</strong></div>
        <div class="map-audit-card"><span>版本化空间资产</span><strong>${spatialAssets.toLocaleString('zh-CN')}</strong></div>`;
      document.querySelector('[data-map-revisions]').innerHTML = '<h3>版本记录</h3>' + this.revisions.map(item => `
        <div class="map-revision-row"><strong>v${item.revision_no}</strong><code>${item.world_hash.slice(0, 16)}…</code><span>${new Date(item.updated_at).toLocaleString('zh-CN')}</span><span class="map-state ${item.state === 'DRAFT' ? 'draft' : ''}">${item.state === 'DRAFT' ? '草稿' : '已发布'}</span></div>`).join('');
    },

    openCreate() {
      this.populateMapSelectors();
      document.getElementById('newMapName').value = '';
      document.getElementById('newMapDescription').value = '';
      document.getElementById('newMapSource').value = '';
      document.getElementById('newMapWidth').value = '48';
      document.getElementById('newMapHeight').value = '32';
      document.getElementById('newMapTileSize').value = '32';
      modal('open', 'createMapModal', 'newMapName');
    },

    async create() {
      const name = document.getElementById('newMapName').value.trim();
      if (!name) return document.getElementById('newMapName').focus();
      const created = await request('/maps', {
        method: 'POST',
        body: JSON.stringify({
          name,
          description: document.getElementById('newMapDescription').value.trim(),
          source_revision_id: document.getElementById('newMapSource').value || null,
          width: Number(document.getElementById('newMapWidth').value),
          height: Number(document.getElementById('newMapHeight').value),
          tile_size: Number(document.getElementById('newMapTileSize').value),
        }),
      });
      modal('close', 'createMapModal');
      await this.loadMaps();
      await this.openMap(created.id);
      notify('已创建独立地图草稿。', '地图已创建');
    },

    async savePublic() {
      if (!this.draft || this.draft.state !== 'DRAFT') return;
      const saved = await request(`/maps/${this.selectedMapId}/draft`, {
        method: 'PUT', body: JSON.stringify({ lock_version: this.draft.lock_version, world: this.publicEditor.getWorld() }),
      });
      this.draft = saved;
      this.publicEditor.setWorld(saved.world);
      await this.loadMaps();
      this.renderAudit();
      notify('地图结构、语义和画块已写入当前 Draft。', '草稿已保存');
    },

    async publishOrFork() {
      if (this.draft.state === 'PUBLISHED') {
        await request(`/maps/${this.selectedMapId}/revisions/${this.draft.id}/fork`, { method: 'POST' });
        await this.openMap(this.selectedMapId, false);
        notify('已从只读版本创建新草稿。', '修订已创建');
        return;
      }
      if (this.publicEditor.changed) await this.savePublic();
      const published = await request(`/maps/${this.selectedMapId}/draft/publish`, {
        method: 'POST', body: JSON.stringify({ draft_revision_id: this.draft.id, lock_version: this.draft.lock_version }),
      });
      await this.loadMaps();
      await this.openMap(this.selectedMapId, false);
      notify(`Revision v${published.revision_no} 已锁定，可被实验引用。`, '地图已发布');
    },

    async setExperimentContext(context) {
      this.init();
      this.experiment = context;
      if (!this.selectorMaps.length) await this.loadMaps();
      this.populateMapSelectors();
      const world = context.world || {};
      const map = this.selectorMaps.find(item => item.id === world.map_id);
      document.getElementById('experimentMapSourceMeta').textContent = map
        ? `${map.name} · 公共 Revision ${map.current_published?.revision_no || '—'} · 当前实验有独立覆盖层`
        : '当前实验仍使用自身世界定义';
      document.getElementById('applyExperimentMapBtn').disabled = !context.editable;
      document.getElementById('tuneExperimentMapBtn').disabled = !context.editable || !world.map_revision_id;
    },

    async selectExperimentMap() {
      if (!this.experiment?.editable) throw new Error('已发布实验不可修改地图');
      const revisionId = document.getElementById('experimentMapSelect').value;
      if (!revisionId) throw new Error('请先选择一个已发布地图版本');
      const draft = await request(`/experiments/${this.experiment.experimentId}/draft/map`, {
        method: 'PUT', body: JSON.stringify({ lock_version: this.experiment.lockVersion, map_revision_id: revisionId }),
      });
      this.emitExperimentDraft(draft);
      notify('公共地图已复制为当前实验的可追溯世界来源。', '地图已应用');
    },

    async openExperimentTuning() {
      const world = this.experiment?.world;
      if (!world?.map_id || !world.map_revision_id) throw new Error('请先为实验选择公共地图');
      this.baseExperimentRevision = await request(`/maps/${world.map_id}/revisions/${world.map_revision_id}`);
      this.overlayEditor.setWorld(world);
      this.overlayEditor.setReadOnly(false);
      document.getElementById('experimentMapOverlayTitle').textContent = `${this.baseExperimentRevision.map_name} · 实验微调`;
      document.getElementById('experimentMapOverlayMeta').textContent = `公共 v${this.baseExperimentRevision.revision_no} + 当前实验覆盖层`;
      modal('open', 'experimentMapOverlayModal');
      requestAnimationFrame(() => {
        this.overlayEditor.resize();
        this.overlayEditor.fit();
      });
    },

    async saveExperimentOverlay() {
      if (!this.baseExperimentRevision || !this.experiment?.editable) return;
      const target = this.overlayEditor.getWorld();
      const base = normalizeWorld(this.baseExperimentRevision.world);
      const patch = mergePatch(base.definition, target.definition) || {};
      const currentOverlay = this.experiment.world.overlay || {};
      const draft = await request(`/experiments/${this.experiment.experimentId}/draft/map-overlay`, {
        method: 'PUT',
        body: JSON.stringify({
          lock_version: this.experiment.lockVersion,
          overlay: {
            definition_patch: patch,
            asset_additions: currentOverlay.asset_additions || [],
            removed_asset_paths: currentOverlay.removed_asset_paths || [],
          },
        }),
      });
      modal('close', 'experimentMapOverlayModal');
      this.emitExperimentDraft(draft);
      notify('微调已保存到当前实验，公共地图和其他实验未改变。', '实验覆盖层已保存');
    },

    emitExperimentDraft(draft) {
      this.experiment.lockVersion = draft.lock_version;
      this.experiment.world = draft.definition.world;
      window.dispatchEvent(new CustomEvent('map-workspace:experiment-draft', {
        detail: { experimentId: this.experiment.experimentId, draft },
      }));
      this.setExperimentContext(this.experiment).catch(error => this.fail(error));
    },

    fail(error) {
      console.error(error);
      window.dispatchEvent(new CustomEvent('map-workspace:error', { detail: { error } }));
    },
  };

  window.MapWorkspace = {
    activate: () => manager.activate(),
    setExperimentContext: context => manager.setExperimentContext(context),
    refresh: () => manager.loadMaps(),
    prepareExperimentCreate: () => manager.prepareExperimentCreate(),
  };
})();
