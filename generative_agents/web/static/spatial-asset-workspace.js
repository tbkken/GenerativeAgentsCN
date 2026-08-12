(function () {
  'use strict';

  const API = '/api/v1';
  const KIND_LABELS = { TILE: '画块', OBJECT: '物件', ZONE: '区域', MARKING: '标线', NETWORK: '网络' };
  const manager = {
    initialized: false,
    items: [],
    kind: '',
    query: '',
    detail: null,
    revision: null,
    revisions: [],
    capabilities: [],
    bundles: [],
    dirty: false,
    searchTimer: null,

    $(id) { return document.getElementById(id); },
    escape(value) { return String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character])); },
    async request(path, options = {}) {
      const response = await fetch(`${API}${path}`, { headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(body.error?.message || `请求失败（${response.status}）`);
        error.code = body.error?.code; error.details = body.error?.details;
        throw error;
      }
      return body;
    },
    notify(message, title = '操作成功') { window.dispatchEvent(new CustomEvent('map-workspace:toast', { detail: { message, title } })); },
    fail(error) { window.dispatchEvent(new CustomEvent('map-workspace:error', { detail: { error } })); },
    setDirty(value = true) { this.dirty = value; if (this.revision?.state === 'DRAFT') this.$('saveSpatialAsset').textContent = value ? '保存草稿 ·' : '保存草稿'; },

    init() {
      if (this.initialized) return;
      this.initialized = true;
      this.$('createSpatialAssetBtn').addEventListener('click', () => { this.$('spatialAssetCreate').hidden = false; this.$('newSpatialAssetName').focus(); });
      this.$('cancelCreateSpatialAsset').addEventListener('click', () => { this.$('spatialAssetCreate').hidden = true; });
      this.$('confirmCreateSpatialAsset').addEventListener('click', () => this.create().catch(error => this.fail(error)));
      this.$('backToSpatialAssets').addEventListener('click', () => this.showCatalog());
      this.$('saveSpatialAsset').addEventListener('click', () => this.save().catch(error => this.fail(error)));
      this.$('publishSpatialAsset').addEventListener('click', () => this.publishOrFork().catch(error => this.fail(error)));
      this.$('useSpatialAssetOnMap').addEventListener('click', () => this.useOnMap());
      this.$('addSpatialCapability').addEventListener('click', () => this.addAttachment());
      this.$('addSpatialStateVariant').addEventListener('click', () => this.addStateRow('variant'));
      this.$('addSpatialInitialState').addEventListener('click', () => this.addStateRow('initial'));
      this.$('spatialAppearanceMode').addEventListener('change', () => { this.renderAppearanceFields(); this.setDirty(); });
      this.$('spatialAssetEditKind').addEventListener('change', () => { this.renderAttachments(); this.setDirty(); });
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

    async activate() { this.init(); await this.load(); },
    async load() {
      const params = new URLSearchParams({ page: '1', page_size: '100' });
      if (this.kind) params.set('kind', this.kind); if (this.query) params.set('q', this.query);
      const [assets, capabilities, bundles] = await Promise.all([
        this.request(`/spatial-assets?${params}`),
        this.request('/capabilities?status=PUBLISHED&page=1&page_size=100'),
        this.request('/capability-bundles?status=PUBLISHED&page=1&page_size=100'),
      ]);
      this.items = assets.items; this.capabilities = capabilities.items; this.bundles = bundles.items;
      this.renderCatalog();
    },
    renderCatalog() {
      const grid = this.$('spatialAssetGrid');
      grid.innerHTML = this.items.length ? this.items.map(item => {
        const contract = item.active_contract || {}; const appearance = contract.appearance || {};
        const preview = appearance.mode === 'EMOJI' ? this.escape(appearance.emoji) : '';
        const style = appearance.mode === 'COLOR' ? `background:${this.escape(appearance.color)}` : '';
        return `<button class="spatial-asset-card" data-spatial-id="${item.id}"><span class="spatial-asset-card-top"><span class="spatial-asset-preview" style="${style}">${preview}</span><span class="map-state ${item.current_draft ? 'draft' : ''}">${item.current_draft ? '编辑中' : '已发布'}${item.is_builtin ? ' · 系统' : ''}</span></span><h3>${this.escape(item.name)}</h3><p>${this.escape(contract.summary || item.description || '可复用空间资产')}</p><span class="spatial-asset-card-tags"><span>${this.escape(KIND_LABELS[item.asset_kind])}</span>${(contract.semantics?.tags || []).slice(0, 3).map(tag => `<span>${this.escape(tag)}</span>`).join('')}</span><span class="spatial-asset-card-foot"><code>${this.escape(item.asset_key)}</code><span>${(contract.capability_attachments || []).length} 个能力</span></span></button>`;
      }).join('') : '<div class="empty-state"><strong>没有符合条件的空间资产</strong></div>';
      grid.querySelectorAll('[data-spatial-id]').forEach(card => card.addEventListener('click', () => this.open(card.dataset.spatialId).catch(error => this.fail(error))));
    },

    async create() {
      const name = this.$('newSpatialAssetName').value.trim(); const assetKey = this.$('newSpatialAssetKey').value.trim();
      if (!name) return this.$('newSpatialAssetName').focus();
      const body = { name, asset_kind: this.$('newSpatialAssetKind').value }; if (assetKey) body.asset_key = assetKey;
      const created = await this.request('/spatial-assets', { method: 'POST', body: JSON.stringify(body) });
      this.$('spatialAssetCreate').hidden = true; this.$('newSpatialAssetName').value = ''; this.$('newSpatialAssetKey').value = '';
      await this.load(); await this.open(created.id); this.notify(`${name} 已创建为独立草稿。`, '空间资产已创建');
    },
    async open(id) {
      this.detail = await this.request(`/spatial-assets/${id}`);
      this.revisions = (await this.request(`/spatial-assets/${id}/revisions`)).items;
      this.revision = this.detail.current_draft ? await this.request(`/spatial-assets/${id}/draft`) : await this.request(`/spatial-assets/${id}/revisions/${this.detail.current_published.id}`);
      this.$('spatialAssetGrid').hidden = true; this.$('spatialAssetEditor').hidden = false; this.$('spatialAssetCreate').hidden = true;
      this.populate(); this.setDirty(false);
    },
    showCatalog() {
      if (this.dirty && !window.confirm('当前空间资产有未保存修改，仍要返回吗？')) return;
      this.$('spatialAssetGrid').hidden = false; this.$('spatialAssetEditor').hidden = true; this.detail = null; this.revision = null; this.setDirty(false); this.load().catch(error => this.fail(error));
    },
    populate() {
      const contract = this.revision.contract; const appearance = contract.appearance;
      this.$('spatialAssetEditorTitle').textContent = this.detail.name;
      this.$('spatialAssetEditorMeta').textContent = `${this.detail.asset_key} · Revision ${String(this.revision.revision_no).padStart(3, '0')} · ${KIND_LABELS[contract.kind]}`;
      this.$('spatialAssetEditName').value = this.detail.name; this.$('spatialAssetEditKey').value = this.detail.asset_key; this.$('spatialAssetEditKind').value = contract.kind; this.$('spatialAssetSummary').value = contract.summary || '';
      this.$('spatialAppearanceMode').value = appearance.mode; this.$('spatialAppearanceColor').value = appearance.color || '#dce9df'; this.$('spatialAppearanceEmoji').value = appearance.emoji || ''; this.$('spatialAppearancePath').value = appearance.asset_path || '';
      this.$('spatialCollision').checked = contract.physics.collision; this.$('spatialPresenceEvents').checked = contract.semantics.emits_presence_events; this.$('spatialSurface').value = contract.semantics.surface; this.$('spatialSpeedLimit').value = contract.physics.speed_limit_mps ?? '';
      [...this.$('spatialTraversal').options].forEach(option => { option.selected = contract.physics.traversable_by.includes(option.value); });
      this.renderAppearanceFields(); this.renderStateRows(contract.appearance.state_variants || {}, contract.initial_state || {}); this.renderAttachments(); this.renderState();
    },
    renderState() {
      const editable = this.revision.state === 'DRAFT'; const state = this.$('spatialAssetEditorState');
      state.textContent = editable ? '草稿' : this.detail.is_builtin ? '系统 · 只读' : '已发布 · 只读'; state.classList.toggle('draft', editable);
      this.$('saveSpatialAsset').disabled = !editable; this.$('publishSpatialAsset').textContent = editable ? '发布版本' : this.detail.is_builtin ? '基于此资产创建' : '创建新修订';
      this.$('useSpatialAssetOnMap').disabled = !this.detail.current_published;
      this.$('spatialAssetEditor').querySelectorAll('input,select,textarea,.capability-row-remove').forEach(control => { if (!control.closest('.spatial-asset-editor > header')) control.disabled = !editable; });
      this.$('addSpatialCapability').disabled = !editable; this.$('addSpatialStateVariant').disabled = !editable; this.$('addSpatialInitialState').disabled = !editable;
    },
    renderAppearanceFields() {
      const mode = this.$('spatialAppearanceMode').value;
      this.$('spatialColorField').hidden = mode !== 'COLOR'; this.$('spatialEmojiField').hidden = mode !== 'EMOJI'; this.$('spatialAssetPathField').hidden = !['IMAGE', 'SPRITE'].includes(mode);
    },

    renderStateRows(variants, initial) {
      this.$('spatialStateVariantList').innerHTML = Object.entries(variants).map(([key, visual]) => this.stateRow('variant', key, visual)).join('') || '<div class="spatial-capability-empty">没有状态外观</div>';
      this.$('spatialInitialStateList').innerHTML = Object.entries(initial).map(([key, value]) => this.stateRow('initial', key, value)).join('') || '<div class="spatial-capability-empty">没有初始状态字段</div>';
      this.bindStateRows();
    },
    stateRow(type, key = '', value = '') {
      if (type === 'initial') return `<div class="spatial-state-row" data-state-type="initial"><input class="control" data-state-field="key" value="${this.escape(key)}" placeholder="状态键" /><input class="control" data-state-field="value" value="${this.escape(typeof value === 'object' ? JSON.stringify(value) : value)}" placeholder="初始值" /><button class="capability-row-remove" type="button">×</button></div>`;
      const visualType = value.color ? 'COLOR' : value.emoji ? 'EMOJI' : 'ASSET'; const visualValue = value.color || value.emoji || value.asset_path || '';
      return `<div class="spatial-state-row variant" data-state-type="variant"><input class="control" data-state-field="key" value="${this.escape(key)}" placeholder="状态键" /><select class="control" data-state-field="visual_type"><option value="COLOR" ${visualType === 'COLOR' ? 'selected' : ''}>颜色</option><option value="EMOJI" ${visualType === 'EMOJI' ? 'selected' : ''}>Emoji</option><option value="ASSET" ${visualType === 'ASSET' ? 'selected' : ''}>资源路径</option></select><input class="control" data-state-field="value" value="${this.escape(visualValue)}" placeholder="显示值" /><button class="capability-row-remove" type="button">×</button></div>`;
    },
    addStateRow(type) {
      const list = this.$(type === 'variant' ? 'spatialStateVariantList' : 'spatialInitialStateList'); if (list.querySelector('.spatial-capability-empty')) list.innerHTML = '';
      list.insertAdjacentHTML('beforeend', this.stateRow(type)); this.bindStateRows(); this.setDirty();
    },
    bindStateRows() {
      this.$('spatialAssetEditor').querySelectorAll('.spatial-state-row input,.spatial-state-row select').forEach(control => { control.oninput = () => this.setDirty(); });
      this.$('spatialAssetEditor').querySelectorAll('.spatial-state-row .capability-row-remove').forEach(button => { button.onclick = () => { button.parentElement.remove(); this.setDirty(); }; });
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

    sourceCatalog(type) { return type === 'capability' ? this.capabilities : this.bundles; },
    sourceRevision(item, type) { return type === 'capability' ? item.current_published?.id : item.current_published?.id; },
    sourceName(item) { return item.name; },
    attachmentSchema(type, revisionId) {
      const item = this.sourceCatalog(type).find(entry => entry.current_published?.id === revisionId);
      return type === 'capability' ? item?.active_contract?.parameters_schema : item?.active_composition?.exposed_parameters_schema;
    },
    attachmentTargets(type, item) { return type === 'capability' ? item.active_contract?.targets || [] : item.targets || []; },
    validSources(type) {
      const kind = this.$('spatialAssetEditKind').value; const target = { TILE: 'MAP_OBJECT', OBJECT: 'MAP_OBJECT', MARKING: 'MAP_OBJECT', ZONE: 'ZONE', NETWORK: 'WORLD' }[kind];
      return this.sourceCatalog(type).filter(item => { const targets = this.attachmentTargets(type, item); return targets.includes(target) || targets.includes('WORLD'); });
    },
    renderAttachments() {
      const attachments = this.revision.contract.capability_attachments || []; const list = this.$('spatialCapabilityList');
      list.className = 'spatial-capability-list';
      list.innerHTML = attachments.length ? attachments.map((attachment, index) => this.attachmentRow(attachment, index)).join('') : '<div class="spatial-capability-empty">尚未挂载能力；可添加区域感知、计时器、状态机或已发布能力包。</div>';
      list.querySelectorAll('[data-remove-attachment]').forEach(button => button.addEventListener('click', () => { attachments.splice(Number(button.dataset.removeAttachment), 1); this.renderAttachments(); this.setDirty(); }));
      list.querySelectorAll('[data-attachment-field]').forEach(control => control.addEventListener('change', event => this.updateAttachment(event)));
      list.querySelectorAll('[data-attachment-param]').forEach(control => control.addEventListener('input', event => { const row = event.target.closest('[data-attachment-index]'); const attachment = attachments[Number(row.dataset.attachmentIndex)]; attachment.parameters[event.target.dataset.attachmentParam] = this.coerce(event.target.value, event.target.dataset.paramType); this.setDirty(); }));
    },
    attachmentRow(attachment, index) {
      const type = attachment.capability_revision_id ? 'capability' : 'bundle'; const revisionId = attachment.capability_revision_id || attachment.capability_bundle_revision_id; const sources = this.validSources(type); const schema = this.attachmentSchema(type, revisionId) || { properties: {}, required: [] };
      return `<article class="spatial-capability-row" data-attachment-index="${index}"><input class="control" data-attachment-field="attachment_key" value="${this.escape(attachment.attachment_key)}" placeholder="挂载键" /><select class="control" data-attachment-field="type"><option value="capability" ${type === 'capability' ? 'selected' : ''}>原子能力</option><option value="bundle" ${type === 'bundle' ? 'selected' : ''}>能力包</option></select><select class="control" data-attachment-field="revision_id">${sources.map(item => `<option value="${item.current_published.id}" ${item.current_published.id === revisionId ? 'selected' : ''}>${this.escape(this.sourceName(item))}</option>`).join('')}</select><button class="capability-row-remove" type="button" data-remove-attachment="${index}">×</button><div class="spatial-attachment-params">${this.parameterFields(schema, attachment.parameters)}</div></article>`;
    },
    parameterFields(schema, parameters) {
      const entries = Object.entries(schema?.properties || {}); return entries.length ? entries.map(([key, definition]) => `<label class="spatial-param-field"><span>${this.escape(key)}${(schema.required || []).includes(key) ? ' *' : ''}</span><input class="control" data-attachment-param="${this.escape(key)}" data-param-type="${this.escape(definition.type || 'string')}" value="${this.escape(parameters?.[key] ?? '')}" /></label>`).join('') : '<span>该能力没有实例参数</span>';
    },
    defaultParameters(schema) { const result = {}; Object.entries(schema?.properties || {}).forEach(([key, definition]) => { result[key] = definition.default ?? definition.minimum ?? (definition.type === 'boolean' ? false : definition.type === 'number' || definition.type === 'integer' ? 0 : definition.type === 'array' ? [] : ''); }); return result; },
    coerce(value, type) { if (type === 'boolean') return value === 'true'; if (type === 'number' || type === 'integer') return value === '' ? null : Number(value); if (type === 'array') return value.split(/[,，]/).map(item => item.trim()).filter(Boolean); return value; },
    addAttachment() {
      const sources = this.validSources('capability'); if (!sources.length) return this.notify('没有适合当前挂载目标的已发布能力。', '无法挂载');
      const source = sources[0]; const attachments = this.revision.contract.capability_attachments; const existing = new Set(attachments.map(item => item.attachment_key)); let base = source.capability_key; let key = base; let suffix = 2; while (existing.has(key)) key = `${base}-${suffix++}`;
      attachments.push({ attachment_key: key, capability_revision_id: source.current_published.id, capability_bundle_revision_id: null, parameters: this.defaultParameters(source.active_contract.parameters_schema), enabled: true }); this.renderAttachments(); this.setDirty();
    },
    updateAttachment(event) {
      const row = event.target.closest('[data-attachment-index]'); const attachment = this.revision.contract.capability_attachments[Number(row.dataset.attachmentIndex)]; const field = event.target.dataset.attachmentField;
      if (field === 'attachment_key') attachment.attachment_key = event.target.value.trim();
      else if (field === 'type') {
        const sources = this.validSources(event.target.value); const source = sources[0]; if (!source) return;
        attachment.capability_revision_id = event.target.value === 'capability' ? source.current_published.id : null; attachment.capability_bundle_revision_id = event.target.value === 'bundle' ? source.current_published.id : null; attachment.parameters = this.defaultParameters(this.attachmentSchema(event.target.value, source.current_published.id));
      } else {
        const type = row.querySelector('[data-attachment-field="type"]').value; attachment.capability_revision_id = type === 'capability' ? event.target.value : null; attachment.capability_bundle_revision_id = type === 'bundle' ? event.target.value : null; attachment.parameters = this.defaultParameters(this.attachmentSchema(type, event.target.value));
      }
      this.renderAttachments(); this.setDirty();
    },

    buildContract() {
      const original = this.revision.contract; const mode = this.$('spatialAppearanceMode').value; const appearance = { mode, color: null, emoji: null, asset_path: null, scale: original.appearance.scale || 1, rotation_degrees: original.appearance.rotation_degrees || 0, state_variants: this.readStateRows('variant') };
      if (mode === 'COLOR') appearance.color = this.$('spatialAppearanceColor').value; else if (mode === 'EMOJI') appearance.emoji = this.$('spatialAppearanceEmoji').value.trim(); else appearance.asset_path = this.$('spatialAppearancePath').value.trim();
      let traversal = [...this.$('spatialTraversal').selectedOptions].map(option => option.value); if (traversal.includes('ALL')) traversal = ['ALL']; if (!traversal.length) traversal = ['ALL'];
      const kind = this.$('spatialAssetEditKind').value;
      return { schema_version: 'ga-spatial-asset/v1', name: this.$('spatialAssetEditName').value.trim(), summary: this.$('spatialAssetSummary').value.trim(), kind, appearance, physics: { ...original.physics, collision: ['ZONE', 'MARKING'].includes(kind) ? false : this.$('spatialCollision').checked, traversable_by: traversal, speed_limit_mps: this.$('spatialSpeedLimit').value === '' ? null : Number(this.$('spatialSpeedLimit').value) }, semantics: { ...original.semantics, surface: this.$('spatialSurface').value, emits_presence_events: this.$('spatialPresenceEvents').checked }, initial_state: this.readStateRows('initial'), capability_attachments: this.revision.contract.capability_attachments };
    },
    async save() {
      if (this.revision.state !== 'DRAFT') return; const contract = this.buildContract();
      this.revision = await this.request(`/spatial-assets/${this.detail.id}/draft`, { method: 'PUT', body: JSON.stringify({ lock_version: this.revision.lock_version, name: contract.name, description: contract.summary, contract }) });
      this.detail.name = contract.name; this.detail.description = contract.summary; this.$('spatialAssetEditorTitle').textContent = contract.name; this.setDirty(false); this.notify('外观、语义、状态和能力挂载已保存。', '空间资产已保存');
    },
    async publishOrFork() {
      if (this.revision.state === 'DRAFT') {
        if (this.dirty) await this.save();
        await this.request(`/spatial-assets/${this.detail.id}/draft/publish`, { method: 'POST', body: JSON.stringify({ draft_revision_id: this.revision.id, lock_version: this.revision.lock_version }) });
        this.notify('空间资产已锁定为可复用发布版本。', '发布成功'); await this.load(); await this.open(this.detail.id); return;
      }
      if (this.detail.is_builtin) {
        const created = await this.request('/spatial-assets', { method: 'POST', body: JSON.stringify({ name: `${this.detail.name}（自定义）`, source_revision_id: this.revision.id }) }); this.notify('系统资产保持不变，已创建自定义副本。'); await this.load(); await this.open(created.id); return;
      }
      await this.request(`/spatial-assets/${this.detail.id}/revisions/${this.revision.id}/fork`, { method: 'POST' }); await this.open(this.detail.id); this.notify('已创建新的资产修订草稿。');
    },
    useOnMap() {
      const publishedId = this.detail.current_published?.id; if (!publishedId) return;
      window.dispatchEvent(new CustomEvent('spatial-asset-workspace:add-to-map', { detail: { asset: { ...this.detail, revision_id: publishedId, contract: this.detail.active_contract } } }));
    },
  };

  window.SpatialAssetWorkspace = manager;
})();
