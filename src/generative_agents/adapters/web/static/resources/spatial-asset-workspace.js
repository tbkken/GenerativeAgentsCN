/**
 * 空间资产工作区：资产始终可编辑，地图按稳定 asset id 读取最新内容。
 */
(function () {
  'use strict';

  const API = window.ResourceScope?.base || '/api/studio/resources';
  const experimentScope = Boolean(window.ResourceScope?.experimentId);
  const KIND_LABELS = { TILE: '画块', OBJECT: '物件', ZONE: '区域', MARKING: '标线', NETWORK: '网络' };
  const manager = {
    initialized: false,
    items: [],
    kind: '',
    query: '',
    detail: null,
    asset: null,
    dirty: false,
    searchTimer: null,
    listGeneration: 0,

    $(id) { return document.getElementById(id); },
    escape(value) { return String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character])); },
    async request(path, options = {}) {
      options = window.ResourceScope?.options(options) || options;
      const response = await fetch(`${API}${path}`, { headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = body.detail;
        const error = new Error(detail?.message || (typeof detail === 'string' ? detail : '') || body.error?.message || `请求失败（${response.status}）`);
        error.code = detail?.code || body.error?.code; error.details = detail?.details || body.error?.details;
        throw error;
      }
      if (experimentScope && options.method && options.method !== 'GET') window.ResourceScope.saved(body);
      return body;
    },
    notify(message, title = '操作成功') { window.dispatchEvent(new CustomEvent('map-workspace:toast', { detail: { message, title } })); },
    fail(error) { window.dispatchEvent(new CustomEvent('map-workspace:error', { detail: { error } })); },
    setDirty(value = true) { this.dirty = value; this.$('saveSpatialAsset').textContent = value ? '保存更改 ·' : '保存更改'; },

    init() {
      if (this.initialized) return;
      this.initialized = true;
      this.$('createSpatialAssetBtn').addEventListener('click', () => { this.$('spatialAssetCreate').hidden = false; this.$('newSpatialAssetName').focus(); });
      this.$('cancelCreateSpatialAsset').addEventListener('click', () => { this.$('spatialAssetCreate').hidden = true; });
      this.$('confirmCreateSpatialAsset').addEventListener('click', () => this.create().catch(error => this.fail(error)));
      this.$('backToSpatialAssets').addEventListener('click', () => this.showCatalog().catch(error => this.fail(error)));
      this.$('saveSpatialAsset').addEventListener('click', () => this.save().catch(error => this.fail(error)));
      this.$('deleteSpatialAsset').addEventListener('click', () => this.deleteAsset(this.detail?.id, this.detail?.name).catch(error => this.fail(error)));
      this.$('useSpatialAssetOnMap').addEventListener('click', () => this.useOnMap());
      this.$('addSpatialStateVariant').addEventListener('click', () => this.addStateRow('variant'));
      this.$('addSpatialInitialState').addEventListener('click', () => this.addStateRow('initial'));
      this.$('spatialAppearanceMode').addEventListener('change', () => { this.renderAppearanceFields(); this.setDirty(); });
      this.$('spatialAssetEditKind').addEventListener('change', () => this.setDirty());
      this.$('spatialAssetSearch').addEventListener('input', event => {
        clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => { this.query = event.target.value.trim(); this.load().catch(error => this.fail(error)); }, 220);
      });
      document.querySelectorAll('[data-spatial-kind]').forEach(button => button.addEventListener('click', () => {
        document.querySelectorAll('[data-spatial-kind]').forEach(item => item.classList.toggle('active', item === button));
        this.kind = button.dataset.spatialKind; this.load().catch(error => this.fail(error));
      }));
      document.querySelectorAll('.spatial-dirty').forEach(control => control.addEventListener('input', () => this.setDirty()));
    },

    async activate() { this.init(); this.$('createSpatialAssetBtn').hidden = experimentScope && !window.ResourceScope.editable; await this.load(); },
    async load() {
      const generation = ++this.listGeneration;
      const grid = this.$('spatialAssetGrid');
      grid.setAttribute('aria-busy', 'true');
      const params = new URLSearchParams({ page: '1', page_size: '100' });
      if (this.kind) params.set('kind', this.kind); if (this.query) params.set('q', this.query);
      let assets;
      try {
        assets = await this.request(`/spatial-assets?${params}`);
      } catch (error) {
        if (generation !== this.listGeneration) return;
        grid.innerHTML = `<div class="empty-state resource-load-error" role="alert"><strong>空间资产加载失败</strong><span>${this.escape(error.message || '请稍后重试')}</span><button class="btn btn-sm" type="button" data-retry-spatial-assets>重新加载</button></div>`;
        grid.querySelector('[data-retry-spatial-assets]')?.addEventListener('click', () => this.load().catch(nextError => this.fail(nextError)));
        throw error;
      } finally {
        if (generation === this.listGeneration) grid.removeAttribute('aria-busy');
      }
      if (generation !== this.listGeneration) return;
      this.items = assets.items;
      this.renderCatalog();
    },
    renderCatalog() {
      const grid = this.$('spatialAssetGrid');
      grid.innerHTML = this.items.length ? this.items.map(item => {
        const contract = item.contract || {}; const appearance = contract.appearance || {};
        const preview = appearance.mode === 'EMOJI' ? this.escape(appearance.emoji) : '';
        const style = appearance.mode === 'COLOR' ? `background:${this.escape(appearance.color)}` : '';
        return `<article class="resource-card-shell"><button class="spatial-asset-card" data-spatial-id="${item.id}"><span class="spatial-asset-card-top"><span class="spatial-asset-preview" style="${style}">${preview}</span><span class="map-state draft">实时素材${item.is_builtin ? ' · 系统' : ''}</span></span><h3>${this.escape(item.name)}</h3><p>${this.escape(contract.summary || item.description || '可复用空间资产')}</p><span class="spatial-asset-card-tags"><span>${this.escape(KIND_LABELS[item.asset_kind])}</span>${(contract.semantics?.tags || []).slice(0, 3).map(tag => `<span>${this.escape(tag)}</span>`).join('')}</span><span class="spatial-asset-card-foot"><code>${this.escape(item.asset_key)}</code><span>${item.usage_count || 0} 张地图</span></span></button><button class="resource-card-delete" type="button" aria-label="删除资产" title="删除资产" data-delete-spatial-id="${item.id}" data-delete-spatial-name="${this.escape(item.name)}">删除</button></article>`;
      }).join('') : '<div class="empty-state"><strong>没有符合条件的空间资产</strong></div>';
      grid.querySelectorAll('[data-spatial-id]').forEach(card => card.addEventListener('click', () => this.open(card.dataset.spatialId).catch(error => this.fail(error))));
      grid.querySelectorAll('[data-delete-spatial-id]').forEach(button => button.addEventListener('click', () => this.deleteAsset(button.dataset.deleteSpatialId, button.dataset.deleteSpatialName).catch(error => this.fail(error))));
    },

    async deleteAsset(assetId, name = '当前空间资产') {
      if (!assetId) return;
      const confirmed = await window.confirmResourceDeletion({ type: '空间资产', name, message: '删除不会影响已发布实验快照；当前地图中的该素材会变为缺失并显示诊断。' });
      if (!confirmed) return;
      await this.request(`/spatial-assets/${encodeURIComponent(assetId)}`, { method: 'DELETE' });
      if (this.detail?.id === assetId) {
        this.$('spatialAssetGrid').hidden = false;
        this.$('spatialAssetEditor').hidden = true;
        this.detail = null;
        this.asset = null;
        this.setDirty(false);
      }
      await this.load();
      this.notify(`空间资产“${name}”已删除。`, '删除完成');
    },

    async create() {
      const name = this.$('newSpatialAssetName').value.trim(); const assetKey = this.$('newSpatialAssetKey').value.trim();
      if (!name) return this.$('newSpatialAssetName').focus();
      const body = { name, asset_kind: this.$('newSpatialAssetKind').value }; if (assetKey) body.asset_key = assetKey;
      const created = await this.request('/spatial-assets', { method: 'POST', body: JSON.stringify(body) });
      this.$('spatialAssetCreate').hidden = true; this.$('newSpatialAssetName').value = ''; this.$('newSpatialAssetKey').value = '';
      await this.load(); await this.open(created.id); this.notify(`${name} 已创建，可直接编辑。`, '空间资产已创建');
    },
    async open(id) {
      this.detail = await this.request(`/spatial-assets/${id}`);
      this.asset = this.detail;
      this.$('spatialAssetGrid').hidden = true; this.$('spatialAssetEditor').hidden = false; this.$('spatialAssetCreate').hidden = true;
      this.populate(); this.setDirty(false);
      const readonly = this.detail.editable === false;
      this.$('spatialAssetEditor').querySelectorAll('input, select, textarea, button').forEach(control => {
        if (control.id !== 'backToSpatialAssets') control.disabled = readonly;
      });
    },
    async showCatalog() {
      if (this.dirty && !await window.confirmResourceDeletion({ title: '放弃未保存修改', name: this.detail?.name || '当前空间资产', message: '返回后将丢弃当前未保存的修改。', confirmLabel: '放弃修改并返回' })) return;
      this.$('spatialAssetGrid').hidden = false; this.$('spatialAssetEditor').hidden = true; this.detail = null; this.asset = null; this.setDirty(false); this.load().catch(error => this.fail(error));
    },
    populate() {
      const contract = this.asset.contract; const appearance = contract.appearance;
      this.$('spatialAssetEditorTitle').textContent = this.detail.name;
      this.$('spatialAssetEditorMeta').textContent = `${this.detail.asset_key} · 实时影响 ${this.detail.usage_count || 0} 张地图 · ${KIND_LABELS[contract.kind]}`;
      this.$('spatialAssetEditName').value = this.detail.name; this.$('spatialAssetEditKey').value = this.detail.asset_key; this.$('spatialAssetEditKind').value = contract.kind; this.$('spatialAssetSummary').value = contract.summary || '';
      this.$('spatialAppearanceMode').value = appearance.mode; this.$('spatialAppearanceColor').value = appearance.color || '#dce9df'; this.$('spatialAppearanceEmoji').value = appearance.emoji || ''; this.$('spatialAppearancePath').value = appearance.asset_path || '';
      this.$('spatialCollision').checked = contract.physics.collision; this.$('spatialPresenceEvents').checked = contract.semantics.emits_presence_events; this.$('spatialSurface').value = contract.semantics.surface; this.$('spatialSpeedLimit').value = contract.physics.speed_limit_mps ?? '';
      [...this.$('spatialTraversal').options].forEach(option => { option.selected = contract.physics.traversable_by.includes(option.value); });
      this.renderAppearanceFields(); this.renderStateRows(contract.appearance.state_variants || {}, contract.initial_state || {}); this.renderState();
    },
    renderState() {
      const editable = true; const state = this.$('spatialAssetEditorState');
      state.textContent = '实时可编辑'; state.classList.add('draft');
      this.$('deleteSpatialAsset').hidden = false;
      this.$('saveSpatialAsset').disabled = false; this.$('publishSpatialAsset').hidden = true;
      this.$('useSpatialAssetOnMap').disabled = false;
      this.$('spatialAssetEditor').querySelectorAll('input,select,textarea,.spatial-row-remove').forEach(control => { if (!control.closest('.spatial-asset-editor > header')) control.disabled = !editable; });
      this.$('addSpatialStateVariant').disabled = !editable; this.$('addSpatialInitialState').disabled = !editable;
    },
    renderAppearanceFields() {
      const mode = this.$('spatialAppearanceMode').value;
      this.$('spatialColorField').hidden = mode !== 'COLOR'; this.$('spatialEmojiField').hidden = mode !== 'EMOJI'; this.$('spatialAssetPathField').hidden = !['IMAGE', 'SPRITE'].includes(mode);
    },

    renderStateRows(variants, initial) {
      this.$('spatialStateVariantList').innerHTML = Object.entries(variants).map(([key, visual]) => this.stateRow('variant', key, visual)).join('') || '<div class="spatial-state-empty">没有状态外观</div>';
      this.$('spatialInitialStateList').innerHTML = Object.entries(initial).map(([key, value]) => this.stateRow('initial', key, value)).join('') || '<div class="spatial-state-empty">没有初始状态字段</div>';
      this.bindStateRows();
    },
    stateRow(type, key = '', value = '') {
      if (type === 'initial') return `<div class="spatial-state-row" data-state-type="initial"><input class="control" data-state-field="key" value="${this.escape(key)}" placeholder="状态键" /><input class="control" data-state-field="value" value="${this.escape(typeof value === 'object' ? JSON.stringify(value) : value)}" placeholder="初始值" /><button class="spatial-row-remove" type="button">×</button></div>`;
      const visualType = value.color ? 'COLOR' : value.emoji ? 'EMOJI' : 'ASSET'; const visualValue = value.color || value.emoji || value.asset_path || '';
      return `<div class="spatial-state-row variant" data-state-type="variant"><input class="control" data-state-field="key" value="${this.escape(key)}" placeholder="状态键" /><select class="control" data-state-field="visual_type"><option value="COLOR" ${visualType === 'COLOR' ? 'selected' : ''}>颜色</option><option value="EMOJI" ${visualType === 'EMOJI' ? 'selected' : ''}>Emoji</option><option value="ASSET" ${visualType === 'ASSET' ? 'selected' : ''}>资源路径</option></select><input class="control" data-state-field="value" value="${this.escape(visualValue)}" placeholder="显示值" /><button class="spatial-row-remove" type="button">×</button></div>`;
    },
    addStateRow(type) {
      const list = this.$(type === 'variant' ? 'spatialStateVariantList' : 'spatialInitialStateList'); if (list.querySelector('.spatial-state-empty')) list.innerHTML = '';
      list.insertAdjacentHTML('beforeend', this.stateRow(type)); this.bindStateRows(); this.setDirty();
    },
    bindStateRows() {
      this.$('spatialAssetEditor').querySelectorAll('.spatial-state-row input,.spatial-state-row select').forEach(control => { control.oninput = () => this.setDirty(); });
      this.$('spatialAssetEditor').querySelectorAll('.spatial-state-row .spatial-row-remove').forEach(button => { button.onclick = () => { button.parentElement.remove(); this.setDirty(); }; });
    },
    readStateRows(type) {
      const result = {}; const list = this.$(type === 'variant' ? 'spatialStateVariantList' : 'spatialInitialStateList');
      list.querySelectorAll(`.spatial-state-row[data-state-type="${type}"]`).forEach(row => {
        const key = row.querySelector('[data-state-field="key"]').value.trim(); if (!key) return;
        const raw = row.querySelector('[data-state-field="value"]').value.trim();
        if (type === 'initial') result[key] = this.parseScalar(raw);
        else { const visualType = row.querySelector('[data-state-field="visual_type"]').value; result[key] = { [visualType === 'COLOR' ? 'color' : visualType === 'EMOJI' ? 'emoji' : 'asset_path']: raw }; }
      });
      return result;
    },
    parseScalar(value) { if (value === 'true') return true; if (value === 'false') return false; if (value !== '' && Number.isFinite(Number(value))) return Number(value); return value; },

    buildContract() {
      const original = this.asset.contract; const mode = this.$('spatialAppearanceMode').value; const appearance = { mode, color: null, emoji: null, asset_path: null, scale: original.appearance.scale || 1, rotation_degrees: original.appearance.rotation_degrees || 0, state_variants: this.readStateRows('variant') };
      if (mode === 'COLOR') appearance.color = this.$('spatialAppearanceColor').value; else if (mode === 'EMOJI') appearance.emoji = this.$('spatialAppearanceEmoji').value.trim(); else appearance.asset_path = this.$('spatialAppearancePath').value.trim();
      let traversal = [...this.$('spatialTraversal').selectedOptions].map(option => option.value); if (traversal.includes('ALL')) traversal = ['ALL']; if (!traversal.length) traversal = ['ALL'];
      const kind = this.$('spatialAssetEditKind').value;
      return { schema_version: 'ga-spatial-asset/v2', name: this.$('spatialAssetEditName').value.trim(), summary: this.$('spatialAssetSummary').value.trim(), kind, appearance, physics: { ...original.physics, collision: ['ZONE', 'MARKING'].includes(kind) ? false : this.$('spatialCollision').checked, traversable_by: traversal, speed_limit_mps: this.$('spatialSpeedLimit').value === '' ? null : Number(this.$('spatialSpeedLimit').value) }, semantics: { ...original.semantics, surface: this.$('spatialSurface').value, emits_presence_events: this.$('spatialPresenceEvents').checked }, initial_state: this.readStateRows('initial') };
    },
    async save() {
      const contract = this.buildContract();
      this.detail = await this.request(`/spatial-assets/${this.detail.id}`, { method: 'PUT', body: JSON.stringify({ row_version: this.detail.row_version, name: contract.name, description: contract.summary, contract }) });
      this.asset = this.detail; this.$('spatialAssetEditorTitle').textContent = contract.name; this.setDirty(false);
      window.dispatchEvent(new CustomEvent('spatial-asset-workspace:updated', { detail: { asset: this.detail } }));
      this.notify(experimentScope ? '已保存到当前实验地图，基础素材不受影响。' : `已更新，并同步影响 ${this.detail.usage_count || 0} 张地图。`, '空间资产已保存');
    },
    useOnMap() {
      window.dispatchEvent(new CustomEvent('spatial-asset-workspace:add-to-map', { detail: { asset: { ...this.detail, contract: this.detail.contract } } }));
    },
  };

  window.SpatialAssetWorkspace = manager;
})();
