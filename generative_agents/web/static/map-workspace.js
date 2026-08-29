/**
 * 公共地图工作区：负责目录、草稿、自动保存、本地恢复、发布和 MapEditorV2 挂载。
 *
 * manager 是页面级状态机。服务器草稿始终带 lock_version，
 * localStorage 只保存尚未同步的恢复副本，不能覆盖服务端已经更新的权威 Revision。
 */
(() => {
  'use strict';

  const API = '/api/v1';
  const MAP_AUTO_SAVE_DELAY_MS = 1200;
  const MAP_RECOVERY_WRITE_DELAY_MS = 180;
  const MAP_RECOVERY_SCHEMA = 'ga-map-draft-recovery/v1';
  const deepClone = value => JSON.parse(JSON.stringify(value));
  const escapeHtml = value => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);

  async function request(path, options = {}) {
    // 地图端点统一使用 /api/v1 前缀和业务错误信封。
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

  const manager = {
    maps: [],
    selectorMaps: [],
    blueprints: [],
    selectedMapId: null,
    detail: null,
    draft: null,
    revisions: [],
    experiment: null,
    publicEditor: null,
    status: '',
    query: '',
    listGeneration: 0,
    searchTimer: null,
    autoSaveTimer: null,
    recoveryTimer: null,
    savePromise: null,
    lastSavedAt: null,
    recoveryStorageWarningShown: false,
    editTransitionPromise: null,
    initialized: false,

    init() {
      if (this.initialized) return;
      this.initialized = true;
      const publicEditorRoot = document.getElementById('publicMapEditor');
      this.publicEditor = new window.MapEditorV2(publicEditorRoot);
      document.getElementById('createMapBtn').addEventListener('click', () => this.openCreate());
      document.getElementById('backToMapsBtn').addEventListener('click', () => this.showCatalog().catch(error => this.fail(error)));
      document.getElementById('saveMapBtn').addEventListener('click', () => this.savePublic({ manual: true }).catch(error => this.fail(error)));
      document.getElementById('publishMapBtn').addEventListener('click', () => this.publishOrFork().catch(error => this.fail(error)));
      publicEditorRoot.addEventListener('map-editor-v2:change', () => this.handlePublicEditorChange());
      publicEditorRoot.addEventListener('map-editor-v2:request-edit', event => this.handlePublicEditorEditRequest(event).catch(error => this.fail(error)));
      publicEditorRoot.addEventListener('map-editor-v2:apply-blueprint-step', () => this.applyBlueprintStep().catch(error => this.fail(error)));
      window.addEventListener('beforeunload', event => this.handleBeforeUnload(event));
      window.addEventListener('pagehide', () => this.persistLocalRecovery());
      window.addEventListener('online', () => {
        if (this.publicEditor?.changed) this.scheduleAutoSave(0);
      });
      document.getElementById('mapSearch').addEventListener('input', event => {
        clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => {
          this.query = event.target.value.trim();
          this.loadMaps().catch(error => this.fail(error));
        }, 250);
      });
      document.querySelectorAll('[data-map-filter]').forEach(tab => tab.addEventListener('click', () => {
        document.querySelectorAll('[data-map-filter]').forEach(item => item.classList.toggle('active', item === tab));
        this.status = tab.dataset.mapFilter === 'all' ? '' : tab.dataset.mapFilter.toUpperCase();
        this.loadMaps().catch(error => this.fail(error));
      }));
      document.getElementById('confirmCreateMap').addEventListener('click', () => this.create().catch(error => this.fail(error)));
      ['closeCreateMap', 'cancelCreateMap'].forEach(id => document.getElementById(id).addEventListener('click', () => modal('close', 'createMapModal')));
      document.getElementById('newMapBlueprint').addEventListener('change', () => this.updateCreateMode('blueprint'));
      document.getElementById('newMapSource').addEventListener('change', () => this.updateCreateMode('source'));
      document.querySelectorAll('[data-map-tab]').forEach(tab => tab.addEventListener('click', () => this.setTab(tab.dataset.mapTab)));
      window.addEventListener('spatial-asset-workspace:add-to-map', event => this.addSpatialAsset(event.detail?.asset));
    },

    async activate() {
      this.init();
      this.query = document.getElementById('mapSearch').value.trim();
      await Promise.all([this.loadMaps(), this.loadBlueprints()]);
      const mapId = new URLSearchParams(location.search).get('map_id');
      if (mapId && mapId !== this.selectedMapId) await this.openMap(mapId, false);
    },

    async loadBlueprints() {
      const result = await request('/map-blueprints');
      this.blueprints = result.items || [];
      const select = document.getElementById('newMapBlueprint');
      const current = select.value;
      select.innerHTML = '<option value="">空白地图（自由绘制）</option>' + this.blueprints
        .map(item => `<option value="${escapeHtml(item.key)}">构建向导 · ${escapeHtml(item.name)} · ${item.steps.length} 步</option>`)
        .join('');
      select.value = this.blueprints.some(item => item.key === current) ? current : '';
    },

    async loadMaps() {
      this.init();
      const generation = ++this.listGeneration;
      const requestState = { status: this.status, query: this.query };
      const params = new URLSearchParams({ page: '1', page_size: '100' });
      if (this.query) params.set('q', this.query);
      if (this.status) params.set('status', this.status);
      const selectorParams = new URLSearchParams({ page: '1', page_size: '100' });
      const [result, selectorResult] = await Promise.all([
        request(`/maps?${params}`),
        request(`/maps?${selectorParams}`),
      ]);
      if (generation !== this.listGeneration
        || requestState.status !== this.status
        || requestState.query !== this.query) return;
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
      if (result.total) document.getElementById('mapCatalogCount').textContent = `共 ${result.total} 张地图`;
      this.updateMapStatusCounts(result.status_counts || {});
      this.populateMapSelectors();
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
      source.innerHTML = '<option value="">不复制</option>' + published
        .map(item => `<option value="${item.current_published.id}">${escapeHtml(item.name)} · v${item.current_published.revision_no}</option>`).join('');
      source.value = currentSource;
      const experimentCreateSelect = document.getElementById('newExperimentMap');
      if (experimentCreateSelect) {
        const previousCreateValue = experimentCreateSelect.value;
        experimentCreateSelect.innerHTML = '<option value="">请选择已发布地图</option>' + published
          .map(item => `<option value="${item.current_published.id}">${escapeHtml(item.name)} · v${item.current_published.revision_no}</option>`).join('');
        experimentCreateSelect.value = published.some(item => item.current_published.id === previousCreateValue)
          ? previousCreateValue
          : '';
      }
      const compositionSelect = document.getElementById('experimentMapRevisionSelect');
      if (compositionSelect) {
        const selectedRevision = this.experiment?.world?.map_revision_id || compositionSelect.value;
        compositionSelect.innerHTML = '<option value="">请选择已发布地图 Revision</option>' + published
          .map(item => `<option value="${item.current_published.id}" data-map-id="${item.id}">${escapeHtml(item.name)} · v${item.current_published.revision_no}</option>`).join('');
        compositionSelect.value = selectedRevision || '';
      }
    },

    async prepareExperimentCreate() {
      this.init();
      if (!this.selectorMaps.length) await this.loadMaps();
      this.populateMapSelectors();
    },

    recoveryKey(mapId = this.selectedMapId, draftId = this.draft?.id) {
      return mapId && draftId ? `ga:map-draft-recovery:${mapId}:${draftId}` : '';
    },

    setAutoSaveStatus(state, detail = '') {
      const status = document.getElementById('mapAutosaveStatus');
      const saveButton = document.getElementById('saveMapBtn');
      const editable = this.draft?.state === 'DRAFT' && !this.publicEditor?.readonly;
      if (saveButton) saveButton.disabled = !editable || state === 'saving';
      if (!status) return;
      const labels = {
        saved: detail ? `已自动保存 ${detail}` : '草稿已保存',
        dirty: '未保存',
        saving: '保存中…',
        error: '自动保存失败',
        recovered: '已恢复本地内容 · 待保存',
        readonly: '只读版本',
      };
      status.dataset.state = state;
      status.textContent = labels[state] || detail;
      status.title = state === 'error'
        ? `${detail || '网络或版本冲突'}；点击“保存草稿”重试。`
        : (detail || status.textContent);
    },

    formatSaveTime(value = Date.now()) {
      const date = value instanceof Date ? value : new Date(value);
      if (Number.isNaN(date.getTime())) return '';
      return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    },

    handlePublicEditorChange() {
      if (!this.selectedMapId || this.draft?.state !== 'DRAFT') return;
      this.setAutoSaveStatus(this.savePromise ? 'saving' : 'dirty');
      this.scheduleLocalRecovery();
      this.scheduleAutoSave();
    },

    scheduleAutoSave(delay = MAP_AUTO_SAVE_DELAY_MS) {
      clearTimeout(this.autoSaveTimer);
      this.autoSaveTimer = setTimeout(() => {
        this.autoSaveTimer = null;
        this.savePublic({ manual: false }).catch(error => {
          console.error('地图自动保存失败', error);
          this.persistLocalRecovery();
          this.setAutoSaveStatus('error', error.message || '请检查网络连接');
        });
      }, delay);
    },

    scheduleLocalRecovery() {
      clearTimeout(this.recoveryTimer);
      this.recoveryTimer = setTimeout(() => {
        this.recoveryTimer = null;
        this.persistLocalRecovery();
      }, MAP_RECOVERY_WRITE_DELAY_MS);
    },

    persistLocalRecovery() {
      // 恢复副本绑定 mapId、draftId 和基础 lockVersion，不能跨草稿自动套用。
      if (!this.publicEditor?.changed || this.draft?.state !== 'DRAFT') return;
      const key = this.recoveryKey();
      if (!key) return;
      try {
        localStorage.setItem(key, JSON.stringify({
          schema: MAP_RECOVERY_SCHEMA,
          mapId: this.selectedMapId,
          draftId: this.draft.id,
          baseLockVersion: this.draft.lock_version,
          changedAt: new Date().toISOString(),
          world: this.publicEditor.getWorld(),
        }));
      } catch (error) {
        if (!this.recoveryStorageWarningShown) {
          this.recoveryStorageWarningShown = true;
          console.warn('地图本地恢复副本写入失败', error);
        }
      }
    },

    clearLocalRecovery(mapId = this.selectedMapId, draftId = this.draft?.id) {
      const key = this.recoveryKey(mapId, draftId);
      if (!key) return;
      try { localStorage.removeItem(key); } catch (_error) { /* 浏览器禁用存储时忽略。 */ }
    },

    restoreLocalRecovery(mapId, draft) {
      // 只有服务器仍处于同一乐观锁版本时才自动恢复，冲突内容保留但不覆盖。
      const key = this.recoveryKey(mapId, draft?.id);
      if (!key || draft?.state !== 'DRAFT') return false;
      let recovery;
      try {
        recovery = JSON.parse(localStorage.getItem(key) || 'null');
      } catch (_error) {
        this.clearLocalRecovery(mapId, draft.id);
        return false;
      }
      if (!recovery || recovery.schema !== MAP_RECOVERY_SCHEMA || !recovery.world) {
        this.clearLocalRecovery(mapId, draft.id);
        return false;
      }
      if (same(recovery.world, draft.world)) {
        this.clearLocalRecovery(mapId, draft.id);
        return false;
      }
      if (Number(recovery.baseLockVersion) !== Number(draft.lock_version)) {
        notify('服务器草稿已发生变化，本地恢复副本已保留，但不会自动覆盖服务器内容。', '检测到草稿版本冲突');
        return false;
      }
      this.publicEditor.setWorld(recovery.world);
      this.publicEditor.changed = true;
      this.setAutoSaveStatus('recovered');
      notify('已从浏览器恢复上次未完成的地图修改，并将自动保存。', '已恢复本地草稿');
      return true;
    },

    handleBeforeUnload(event) {
      if (!this.publicEditor?.changed || this.draft?.state !== 'DRAFT') return;
      this.persistLocalRecovery();
      event.preventDefault();
      event.returnValue = '';
    },

    cancelScheduledSaves() {
      clearTimeout(this.autoSaveTimer);
      clearTimeout(this.recoveryTimer);
      this.autoSaveTimer = null;
      this.recoveryTimer = null;
    },

    async openMap(mapId, push = true) {
      this.init();
      if (this.selectedMapId && this.selectedMapId !== mapId && (this.publicEditor.changed || this.savePromise)) {
        await this.savePublic({ manual: false });
      }
      this.cancelScheduledSaves();
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
      document.getElementById('publishMapBtn').textContent = editable ? '发布版本' : '创建新修订';
      this.lastSavedAt = null;
      const recovered = editable && this.restoreLocalRecovery(mapId, this.draft);
      if (!recovered) this.setAutoSaveStatus(editable ? 'saved' : 'readonly');
      this.renderBuildGuide();
      this.renderAudit();
      window.dispatchEvent(new CustomEvent('map-workspace:selection', { detail: { mapId } }));
      if (push) this.replaceMapUrl(mapId);
    },

    async showCatalog() {
      if (this.publicEditor?.changed || this.savePromise) await this.savePublic({ manual: false });
      this.cancelScheduledSaves();
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
        });
        notify(`${asset.name} 已放置在地图中心；保存后会锁定其版本引用。`, '物件已加入地图');
      }
      this.publicEditor.changed = true;
      this.publicEditor.render();
      this.renderAudit();
    },

    renderAudit() {
      if (!this.draft) return;
      if (!document.querySelector('[data-map-audit-cards]') || !document.querySelector('[data-map-revisions]')) return;
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

    renderBuildGuide() {
      const root = document.getElementById('mapBuildGuide');
      if (!root) return;
      const guide = this.draft?.world?.definition?.editor?.build_guide;
      root.hidden = !guide;
      if (!guide) return;
      const current = Number(guide.current_step || 0);
      const total = Number(guide.total_steps || guide.steps?.length || 0);
      document.getElementById('mapBuildGuideTitle').textContent = guide.name || '地图构建向导';
      document.getElementById('mapBuildGuideProgress').textContent = `${current} / ${total}`;
      document.getElementById('mapBuildGuideList').innerHTML = (guide.steps || []).map(item => {
        const status = item.step <= current ? 'done' : item.step === current + 1 ? 'active' : '';
        return `<div class="map-build-guide-step ${status}"><span>${item.step <= current ? '✓' : item.step}</span><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.tool)}工具</small></div></div>`;
      }).join('');
      const button = document.getElementById('applyMapBlueprintStep');
      button.disabled = this.draft.state !== 'DRAFT' || Boolean(guide.complete);
      button.textContent = guide.complete ? '构建完成，可发布' : `应用第 ${current + 1} 步`;
      document.getElementById('mapBuildGuideHint').textContent = guide.complete
        ? '完整地图已经写入 Draft；发布后实验将锁定同一 Revision。'
        : '每一步都由服务器写入当前 Draft，刷新页面不会丢失进度。';
    },

    async applyBlueprintStep() {
      const guide = this.draft?.world?.definition?.editor?.build_guide;
      if (!guide || guide.complete || this.draft.state !== 'DRAFT') return;
      const nextStep = Number(guide.current_step || 0) + 1;
      const button = document.getElementById('applyMapBlueprintStep');
      button.disabled = true;
      button.textContent = `正在应用第 ${nextStep} 步…`;
      try {
        if (this.publicEditor.changed || this.savePromise) await this.savePublic({ manual: false });
        const saved = await request(`/maps/${this.selectedMapId}/draft/blueprint-steps/${nextStep}`, {
          method: 'POST',
          body: JSON.stringify({ lock_version: this.draft.lock_version }),
        });
        this.draft = saved;
        this.publicEditor.setWorld(saved.world);
        this.clearLocalRecovery(this.selectedMapId, saved.id);
        this.lastSavedAt = new Date();
        this.setAutoSaveStatus('saved', this.formatSaveTime(this.lastSavedAt));
        this.renderBuildGuide();
        this.renderAudit();
        const step = saved.world.definition.editor.build_guide.steps[nextStep - 1];
        notify(`${step.name} 已写入地图 Draft。`, `构建进度 ${nextStep} / ${guide.total_steps}`);
      } catch (error) {
        this.renderBuildGuide();
        throw error;
      }
    },

    updateCreateMode(changed) {
      const blueprint = document.getElementById('newMapBlueprint');
      const source = document.getElementById('newMapSource');
      if (changed === 'blueprint' && blueprint.value) source.value = '';
      if (changed === 'source' && source.value) blueprint.value = '';
      const selected = this.blueprints.find(item => item.key === blueprint.value);
      if (selected) {
        document.getElementById('newMapWidth').value = String(selected.width);
        document.getElementById('newMapHeight').value = String(selected.height);
        document.getElementById('newMapTileSize').value = String(selected.tile_size);
        if (!document.getElementById('newMapName').value.trim()) document.getElementById('newMapName').value = selected.name;
        if (!document.getElementById('newMapKey').value.trim()) document.getElementById('newMapKey').value = 'commute-home-office';
      }
      document.getElementById('newMapBlueprintHint').textContent = selected
        ? `${selected.summary} 创建后从第 1 步开始，当前不会直接生成成品图。`
        : '创建一个不继承斯坦福小镇的空白地图。';
      const fixedDimensions = Boolean(selected || source.value);
      ['newMapWidth', 'newMapHeight', 'newMapTileSize'].forEach(id => {
        document.getElementById(id).disabled = fixedDimensions;
      });
    },

    openCreate() {
      this.populateMapSelectors();
      document.getElementById('newMapName').value = '';
      document.getElementById('newMapDescription').value = '';
      document.getElementById('newMapSource').value = '';
      document.getElementById('newMapBlueprint').value = '';
      document.getElementById('newMapKey').value = '';
      document.getElementById('newMapWidth').value = '48';
      document.getElementById('newMapHeight').value = '32';
      document.getElementById('newMapTileSize').value = '32';
      this.updateCreateMode('blueprint');
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
          blueprint_key: document.getElementById('newMapBlueprint').value || null,
          map_key: document.getElementById('newMapKey').value.trim() || null,
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

    async savePublic({ manual = false } = {}) {
      // 同一时间只允许一个保存 Promise；保存期间发生的新编辑会在响应后再次排队。
      if (!this.draft || this.draft.state !== 'DRAFT') return this.draft;
      clearTimeout(this.autoSaveTimer);
      this.autoSaveTimer = null;

      if (this.savePromise) {
        await this.savePromise;
        if (this.publicEditor.changed) return this.savePublic({ manual });
        if (manual) notify('当前地图草稿已经是最新状态。', '草稿已保存');
        return this.draft;
      }
      if (!this.publicEditor.changed) {
        this.setAutoSaveStatus('saved', this.lastSavedAt ? this.formatSaveTime(this.lastSavedAt) : '');
        if (manual) notify('当前地图草稿已经是最新状态。', '草稿已保存');
        return this.draft;
      }

      const mapId = this.selectedMapId;
      const draftId = this.draft.id;
      const lockVersion = this.draft.lock_version;
      const editorRevision = this.publicEditor.changeRevision;
      const world = this.publicEditor.getWorld();
      this.setAutoSaveStatus('saving');

      const operation = (async () => {
        const saved = await request(`/maps/${mapId}/draft`, {
          method: 'PUT', body: JSON.stringify({ lock_version: lockVersion, world }),
        });
        if (this.selectedMapId !== mapId || this.draft?.id !== draftId) return saved;
        this.draft = saved;
        // 仅确认请求发出时的 editorRevision；更晚发生的编辑仍保持 changed=true。
        this.publicEditor.acceptSavedWorld(saved.world, editorRevision);
        const revisionIndex = this.revisions.findIndex(item => item.id === saved.id);
        if (revisionIndex >= 0) this.revisions[revisionIndex] = saved;
        else this.revisions.unshift(saved);
        this.lastSavedAt = new Date();
        if (this.publicEditor.changed) {
          this.persistLocalRecovery();
          this.setAutoSaveStatus('dirty');
          this.scheduleAutoSave(250);
        } else {
          this.clearLocalRecovery(mapId, draftId);
          this.setAutoSaveStatus('saved', this.formatSaveTime(this.lastSavedAt));
        }
        this.renderBuildGuide();
        this.renderAudit();
        return saved;
      })();
      this.savePromise = operation;

      let saved;
      try {
        saved = await operation;
      } catch (error) {
        if (this.selectedMapId === mapId && this.draft?.id === draftId) {
          this.persistLocalRecovery();
          this.setAutoSaveStatus('error', error.message || '请检查网络连接');
        }
        throw error;
      } finally {
        if (this.savePromise === operation) this.savePromise = null;
      }
      if (manual && this.selectedMapId === mapId && this.publicEditor.changed) {
        return this.savePublic({ manual: true });
      }
      if (manual && this.selectedMapId === mapId) {
        notify('地图结构、语义和画块已写入当前 Draft。', '草稿已保存');
      }
      return saved;
    },

    async publishOrFork() {
      if (this.draft.state === 'PUBLISHED') {
        await request(`/maps/${this.selectedMapId}/revisions/${this.draft.id}/fork`, { method: 'POST' });
        await this.openMap(this.selectedMapId, false);
        notify('已从只读版本创建新草稿。', '修订已创建');
        return;
      }
      const publishButton = document.getElementById('publishMapBtn');
      publishButton.disabled = true;
      this.publicEditor.setReadOnly(true);
      try {
        if (this.publicEditor.changed || this.savePromise) await this.savePublic({ manual: false });
        const published = await request(`/maps/${this.selectedMapId}/draft/publish`, {
          method: 'POST', body: JSON.stringify({ draft_revision_id: this.draft.id, lock_version: this.draft.lock_version }),
        });
        this.clearLocalRecovery(this.selectedMapId, this.draft.id);
        await this.loadMaps();
        await this.openMap(this.selectedMapId, false);
        notify(`Revision v${published.revision_no} 已锁定，可被实验引用。`, '地图已发布');
      } catch (error) {
        this.publicEditor.setReadOnly(false);
        if (this.publicEditor.changed) this.setAutoSaveStatus('error', error.message || '发布失败');
        else this.setAutoSaveStatus('saved', this.lastSavedAt ? this.formatSaveTime(this.lastSavedAt) : '');
        throw error;
      } finally {
        publishButton.disabled = false;
      }
    },

    async handlePublicEditorEditRequest(event) {
      if (event.detail?.intent !== 'new-canvas' || !this.draft) return;
      if (this.draft.state === 'DRAFT') {
        this.publicEditor.createMaterialCanvas();
        return;
      }
      if (this.draft.state !== 'PUBLISHED' || this.editTransitionPromise) return;
      const action = (async () => {
        await this.publishOrFork();
        if (this.draft?.state !== 'DRAFT') throw new Error('无法创建地图修订草稿');
        this.publicEditor.createMaterialCanvas();
      })();
      this.editTransitionPromise = action;
      try {
        await action;
      } finally {
        if (this.editTransitionPromise === action) this.editTransitionPromise = null;
      }
    },

    async setExperimentContext(context) {
      this.init();
      this.experiment = context;
      if (!this.selectorMaps.length) await this.loadMaps();
      this.populateMapSelectors();
      const world = context.world || {};
      const map = this.selectorMaps.find(item => item.id === world.map_id);
      const meta = document.getElementById('experimentMapRevisionMeta');
      if (meta) meta.textContent = map
        ? `${map.name} · 已锁定 Revision ${map.current_published?.revision_no || '—'}`
        : '必须选择已发布地图 Revision';
    },

    async selectExperimentMap() {
      if (!this.experiment?.editable) throw new Error('已发布实验不可修改地图');
      const revisionId = document.getElementById('experimentMapRevisionSelect').value;
      if (!revisionId) throw new Error('请先选择一个已发布地图版本');
      const draft = await request(`/experiments/${this.experiment.experimentId}/draft/map`, {
        method: 'PUT', body: JSON.stringify({ lock_version: this.experiment.lockVersion, map_revision_id: revisionId }),
      });
      this.emitExperimentDraft(draft);
      notify('已切换实验引用的地图 Revision。', '地图已应用');
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
    applyBlueprintStep: () => manager.applyBlueprintStep().catch(error => manager.fail(error)),
    setExperimentContext: context => manager.setExperimentContext(context),
    refresh: () => manager.loadMaps(),
    prepareExperimentCreate: () => manager.prepareExperimentCreate(),
  };
})();
