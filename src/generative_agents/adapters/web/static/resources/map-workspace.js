/**
 * 公共地图工作区：负责目录、直接编辑、自动保存、本地恢复、校验和 MapEditorV2 挂载。
 *
 * manager 是页面级状态机。服务器地图始终带 row_version，
 * 本地恢复副本只保护尚未同步的编辑，不能覆盖服务端当前地图。
 */
(() => {
  'use strict';

  const API = window.ResourceScope?.base || '/api/studio/resources';
  const experimentScope = Boolean(window.ResourceScope?.experimentId);
  const MAP_AUTO_SAVE_DELAY_MS = 1200;
  const MAP_RECOVERY_WRITE_DELAY_MS = 180;
  const MAP_RECOVERY_SCHEMA = 'ga-map-draft-recovery/v1';
  const MAP_RECOVERY_DB = 'ga-map-recovery';
  const MAP_RECOVERY_STORE = 'drafts';
  let recoveryDatabasePromise = null;
  const deepClone = value => JSON.parse(JSON.stringify(value));
  const escapeHtml = value => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);

  function normalizeOptionalMapKey(value) {
    const raw = String(value ?? '').trim().toLowerCase();
    if (!raw) return null;
    const normalized = raw
      .replace(/[\s_]+/g, '-')
      .replace(/[^a-z0-9-]+/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 64)
      .replace(/-+$/g, '');
    return /^[a-z0-9][a-z0-9-]{1,63}$/.test(normalized) ? normalized : null;
  }

  function recoveryDatabase() {
    if (!('indexedDB' in window)) return Promise.resolve(null);
    if (recoveryDatabasePromise) return recoveryDatabasePromise;
    recoveryDatabasePromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(MAP_RECOVERY_DB, 1);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains(MAP_RECOVERY_STORE)) {
          database.createObjectStore(MAP_RECOVERY_STORE);
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error('无法打开地图恢复存储'));
      request.onblocked = () => reject(new Error('地图恢复存储被其他页面占用'));
    });
    return recoveryDatabasePromise;
  }

  async function recoveryStoreRequest(mode, operation) {
    const database = await recoveryDatabase();
    if (!database) return null;
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(MAP_RECOVERY_STORE, mode);
      const request = operation(transaction.objectStore(MAP_RECOVERY_STORE));
      request.onsuccess = () => resolve(request.result ?? null);
      request.onerror = () => reject(request.error || new Error('地图恢复存储操作失败'));
      transaction.onabort = () => reject(transaction.error || new Error('地图恢复存储事务已中止'));
    });
  }

  async function writeRecovery(key, value) {
    const database = await recoveryDatabase();
    if (database) {
      await recoveryStoreRequest('readwrite', store => store.put(value, key));
      try { localStorage.removeItem(key); } catch (_error) { /* optional migration cleanup */ }
      return;
    }
    localStorage.setItem(key, JSON.stringify(value));
  }

  async function readRecovery(key) {
    const database = await recoveryDatabase();
    if (database) {
      const indexed = await recoveryStoreRequest('readonly', store => store.get(key));
      if (indexed) return indexed;
    }
    return JSON.parse(localStorage.getItem(key) || 'null');
  }

  async function deleteRecovery(key) {
    try { localStorage.removeItem(key); } catch (_error) { /* optional legacy cleanup */ }
    const database = await recoveryDatabase();
    if (database) await recoveryStoreRequest('readwrite', store => store.delete(key));
  }

  async function request(path, options = {}) {
    // 地图是 Studio 公共作者资源；实验创建时复制内容，运行时不回查这里。
    options = window.ResourceScope?.options(options) || options;
      const response = await fetch(`${API}${path}`, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body.detail;
      const error = new Error(detail?.message || (typeof detail === 'string' ? detail : '') || body.error?.message || `请求失败（${response.status}）`);
      error.code = detail?.code || body.error?.code;
      error.details = detail?.details || body.error?.details;
      error.requestId = body.error?.request_id || response.headers.get('X-Request-ID');
      error.status = response.status;
      error.path = path;
      throw error;
    }
    if (experimentScope && options.method && options.method !== 'GET') window.ResourceScope.saved(body);
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
      document.getElementById('publishMapBtn').addEventListener('click', () => this.validateMap().catch(error => this.fail(error)));
      document.getElementById('deleteMapBtn').addEventListener('click', () => this.deleteMap(this.selectedMapId, this.detail?.name).catch(error => this.fail(error)));
      publicEditorRoot.addEventListener('map-editor-v2:change', () => this.handlePublicEditorChange());
      publicEditorRoot.addEventListener('map-editor-v2:save', () => this.savePublic({ manual: true }).catch(error => this.fail(error)));
      publicEditorRoot.addEventListener('map-editor-v2:request-edit', event => this.handlePublicEditorEditRequest(event).catch(error => this.fail(error)));
      publicEditorRoot.addEventListener('map-editor-v2:apply-blueprint-step', () => this.applyBlueprintStep().catch(error => this.fail(error)));
      window.addEventListener('experiment-map:uploaded', event => {
        if (experimentScope && this.draft) this.draft.row_version = event.detail.content_sha256;
      });
      window.addEventListener('beforeunload', event => this.handleBeforeUnload(event));
      window.addEventListener('pagehide', () => { this.persistLocalRecovery().catch(() => {}); });
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
      ['closeCreateCanvas', 'cancelCreateCanvas'].forEach(id => document.getElementById(id).addEventListener('click', () => modal('close', 'createCanvasModal')));
      document.getElementById('confirmCreateCanvas').addEventListener('click', () => this.confirmCreateCanvas().catch(error => this.fail(error)));
      document.getElementById('newMapBlueprint').addEventListener('change', () => this.updateCreateMode('blueprint'));
      document.getElementById('newMapSource').addEventListener('change', () => this.updateCreateMode('source'));
      ['newMapWidth', 'newMapHeight', 'newMapTileSize'].forEach(id => {
        document.getElementById(id).addEventListener('input', () => this.updateCreatePixelSize());
      });
      document.getElementById('experimentMapSelect')?.addEventListener('change', () => {
        this.selectExperimentMap().catch(error => this.fail(error));
      });
      document.querySelectorAll('[data-map-tab]').forEach(tab => tab.addEventListener('click', () => this.setTab(tab.dataset.mapTab)));
      window.addEventListener('spatial-asset-workspace:add-to-map', event => this.addSpatialAsset(event.detail?.asset));
      window.addEventListener('spatial-asset-workspace:updated', () => {
        if (this.selectedMapId && !this.publicEditor?.changed && !this.savePromise) {
          this.openMap(this.selectedMapId, false).catch(error => this.fail(error));
        }
      });
    },

    async activate() {
      this.init();
      const skillCatalogRefresh = this.publicEditor.refreshSkillCatalog();
      if (experimentScope) {
        await this.openMap(window.ResourceScope.experimentId, false);
        await skillCatalogRefresh;
        return;
      }
      const saved = window.ResourceList.read('maps');
      this.query = saved.query;
      this.page = saved.page;
      document.getElementById('mapSearch').value = this.query;
      await Promise.all([this.loadMaps(), this.loadBlueprints(), skillCatalogRefresh]);
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
      if (!experimentScope) return this.loadAuthorMaps();
      const generation = ++this.listGeneration;
      const requestState = { status: this.status, query: this.query };
      const grid = document.getElementById('mapCatalogGrid');
      grid.setAttribute('aria-busy', 'true');
      const params = new URLSearchParams({ page: '1', page_size: '100' });
      if (this.query) params.set('q', this.query);
      if (this.status) params.set('status', this.status);
      const selectorParams = new URLSearchParams({ page: '1', page_size: '100' });
      let result;
      let selectorResult;
      try {
        if (!this.query && !this.status) {
          result = await request(`/maps?${params}`);
          selectorResult = result;
        } else {
          [result, selectorResult] = await Promise.all([
            request(`/maps?${params}`),
            request(`/maps?${selectorParams}`),
          ]);
        }
      } catch (error) {
        if (generation !== this.listGeneration) return;
        grid.innerHTML = `<div class="empty-state resource-load-error" role="alert"><strong>地图列表加载失败</strong><span>${escapeHtml(error.message || '请稍后重试')}</span><button class="btn btn-sm" type="button" data-retry-map-list>重新加载</button></div>`;
        grid.querySelector('[data-retry-map-list]')?.addEventListener('click', () => this.loadMaps().catch(nextError => this.fail(nextError)));
        document.getElementById('mapListFooter').hidden = true;
        this.updateMapStatusCounts({});
        throw error;
      } finally {
        if (generation === this.listGeneration) grid.removeAttribute('aria-busy');
      }
      if (generation !== this.listGeneration
        || requestState.status !== this.status
        || requestState.query !== this.query) return;
      this.maps = result.items;
      this.selectorMaps = selectorResult.items;
      grid.innerHTML = this.maps.length ? this.maps.map(item => `
        <article class="resource-card-shell"><button class="map-card" data-map-id="${item.id}">
          <span class="map-card-top"><span class="map-state draft">实时地图</span><code>${escapeHtml(item.map_key)}</code></span>
          <h2>${escapeHtml(item.name)}</h2><p>${escapeHtml(item.description || '暂无用途说明')}</p>
          <span class="map-card-foot"><span>${item.dimensions ? `${item.dimensions[1]} × ${item.dimensions[0]}` : '待设置尺寸'}</span><span>${item.usage_count} 个实验使用</span></span>
        </button><button class="resource-card-delete" type="button" aria-label="删除地图" title="删除地图" data-delete-map-id="${item.id}" data-delete-map-name="${escapeHtml(item.name)}">删除</button></article>`).join('') : '<div class="empty-state"><strong>没有符合条件的地图</strong><span>可以清除搜索词、切换状态，或新建一张地图。</span></div>';
      grid.querySelectorAll('[data-map-id]').forEach(card => card.addEventListener('click', () => this.openMap(card.dataset.mapId).catch(error => this.fail(error))));
      grid.querySelectorAll('[data-delete-map-id]').forEach(button => button.addEventListener('click', () => this.deleteMap(button.dataset.deleteMapId, button.dataset.deleteMapName).catch(error => this.fail(error))));
      const footer = document.getElementById('mapListFooter');
      footer.hidden = result.total === 0;
      if (result.total) document.getElementById('mapCatalogCount').textContent = `共 ${result.total} 张地图`;
      this.updateMapStatusCounts(result.status_counts || {});
      this.populateMapSelectors();
    },

    async loadAuthorMaps() {
      const list = window.ResourceList;
      const generation = ++this.listGeneration;
      const grid = document.getElementById('mapCatalogGrid');
      grid.classList.add('resource-rows');
      list.loading(grid, '地图');
      const saved = list.read('maps');
      this.page = this.query !== saved.query ? 1 : (this.page || saved.page);
      list.remember('maps', {query: this.query, page: this.page});
      const params = new URLSearchParams({page: this.page, page_size: 5, q: this.query});
      try {
        const [result, selector] = await Promise.all([request(`/maps?${params}`), request('/maps?page=1&page_size=100')]);
        if (generation !== this.listGeneration) return;
        if (this.page > Math.max(1, result.total_pages)) {
          this.page = Math.max(1, result.total_pages);
          return this.loadAuthorMaps();
        }
        this.maps = result.items;
        this.selectorMaps = selector.items;
        for (let page = 2; page <= (selector.total_pages || 1); page++) {
          const next = await request(`/maps?page=${page}&page_size=100`);
          if (generation !== this.listGeneration) return;
          this.selectorMaps.push(...next.items);
        }
        grid.innerHTML = this.maps.map(item => list.row({
          name: item.name, description: item.description, icon: '▧',
          meta: [item.dimensions ? `${item.dimensions[1]} × ${item.dimensions[0]} 格` : '待设置尺寸'],
          open: {'data-map-id': item.id},
          actions: [{label: '删除地图', danger: true, attributes: {'data-delete-map-id': item.id, 'data-delete-map-name': item.name}}],
        })).join('') || list.empty('地图', Boolean(this.query));
        grid.querySelectorAll('[data-map-id]').forEach(button => button.onclick = () => this.openMap(button.dataset.mapId).catch(error => this.fail(error)));
        grid.querySelectorAll('[data-delete-map-id]').forEach(button => button.onclick = () => this.deleteMap(button.dataset.deleteMapId, button.dataset.deleteMapName).catch(error => this.fail(error)));
        list.remember('maps', {page: this.page});
        list.pager(document.getElementById('mapListFooter'), {page: this.page, total: result.total, onPage: page => {
          this.page = page;
          list.remember('maps', {page, scroll: 0});
          this.loadMaps().catch(error => this.fail(error));
        }});
        this.populateMapSelectors();
        if (!new URLSearchParams(location.search).has('map_id')) list.restore('maps');
      } catch (error) {
        if (generation !== this.listGeneration) return;
        list.error(grid, '地图', error, () => this.loadMaps().catch(next => this.fail(next)));
        document.getElementById('mapListFooter').hidden = true;
      } finally { if (generation === this.listGeneration) grid.removeAttribute('aria-busy'); }
    },

    async deleteMap(mapId, name = '当前地图') {
      if (!mapId) return;
      const confirmed = await window.confirmResourceDeletion({ type: '地图', name, message: '删除基础地图不会影响已创建实验中的独立副本。' });
      if (!confirmed) return;
      await request(`/maps/${encodeURIComponent(mapId)}`, { method: 'DELETE' });
      this.clearLocalRecovery(mapId, this.draft?.id);
      if (this.selectedMapId === mapId) {
        this.cancelScheduledSaves();
        this.selectedMapId = null;
        this.detail = null;
        this.draft = null;
        document.getElementById('mapCatalogShell').hidden = false;
        document.getElementById('mapEditorShell').hidden = true;
        window.dispatchEvent(new CustomEvent('map-workspace:selection', { detail: { mapId: null } }));
        this.replaceMapUrl(null);
      }
      await this.loadMaps();
      notify(`地图“${name}”已删除。`, '删除完成');
    },

    updateMapStatusCounts(counts) {
      document.querySelectorAll('[data-map-filter]').forEach(tab => {
        const key = tab.dataset.mapFilter;
        tab.hidden = key !== 'all';
        if (key === 'all') tab.textContent = `全部地图 ${counts.ALL || 0}`;
      });
    },

    populateMapSelectors() {
      const catalog = this.selectorMaps.length ? this.selectorMaps : this.maps;
      const source = document.getElementById('newMapSource');
      const currentSource = source.value;
      source.innerHTML = '<option value="">不复制</option>' + catalog
        .map(item => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('');
      source.value = currentSource;
      const experimentCreateSelect = document.getElementById('newExperimentMap');
      if (experimentCreateSelect) {
        const previousCreateValue = experimentCreateSelect.value;
        experimentCreateSelect.innerHTML = '<option value="">请选择地图</option>' + catalog
          .map(item => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('');
        experimentCreateSelect.value = catalog.some(item => item.id === previousCreateValue)
          ? previousCreateValue
          : '';
      }
      const compositionSelect = document.getElementById('experimentMapSelect');
      if (compositionSelect) {
        if (this.experiment?.world) {
          compositionSelect.innerHTML = `<option value="__embedded__">${escapeHtml(this.experiment.world.world_name || '实验内置世界')}</option>`;
          compositionSelect.value = '__embedded__';
          compositionSelect.disabled = true;
        } else {
          compositionSelect.innerHTML = '<option value="">未选择地图</option>';
          compositionSelect.disabled = true;
        }
      }
    },

    async prepareExperimentCreate() {
      this.init();
      if (experimentScope) {
        const selector = document.getElementById('experimentMapSelect');
        selector.replaceChildren(new Option(context.world?.world_name || '实验地图', 'world'));
        selector.disabled = true;
        document.getElementById('experimentMapMeta').textContent = '当前实验的独立地图副本';
        return;
      }
      if (!this.selectorMaps.length) await this.loadMaps();
      this.populateMapSelectors();
    },

    recoveryKey(mapId = this.selectedMapId) {
      return mapId ? `ga:map-recovery:${experimentScope ? 'experiment:' : 'public:'}${mapId}` : '';
    },

    setAutoSaveStatus(state, detail = '') {
      const status = document.getElementById('mapAutosaveStatus');
      const saveButton = document.getElementById('saveMapBtn');
      const editable = Boolean(this.draft) && !this.publicEditor?.readonly;
      if (saveButton) saveButton.disabled = !editable || state === 'saving';
      if (!status) return;
      const labels = {
        saved: detail ? `已自动保存 ${detail}` : '地图已保存',
        dirty: '未保存',
        saving: '保存中…',
        error: '自动保存失败',
        recovered: '已恢复本地内容 · 待保存',
        readonly: '只读',
      };
      status.dataset.state = state;
      status.textContent = labels[state] || detail;
      status.title = state === 'error'
        ? `${detail || '网络或并发冲突'}；点击“保存地图”重试。`
        : (detail || status.textContent);
    },

    formatSaveTime(value = Date.now()) {
      const date = value instanceof Date ? value : new Date(value);
      if (Number.isNaN(date.getTime())) return '';
      return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    },

    handlePublicEditorChange() {
      if (!this.selectedMapId || !this.draft) return;
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
        this.persistLocalRecovery().catch(error => this.reportRecoveryFailure(error));
      }, MAP_RECOVERY_WRITE_DELAY_MS);
    },

    async persistLocalRecovery() {
      // 恢复副本绑定 mapId、draftId 和基础 lockVersion，不能跨草稿自动套用。
      if (!this.publicEditor?.changed || !this.draft) return;
      const key = this.recoveryKey();
      if (!key) return;
      try {
        await writeRecovery(key, {
          schema: MAP_RECOVERY_SCHEMA,
          mapId: this.selectedMapId,
          baseLockVersion: this.draft.row_version,
          changedAt: new Date().toISOString(),
          world: this.publicEditor.getWorld(),
        });
        this.recoveryStorageWarningShown = false;
      } catch (error) {
        this.reportRecoveryFailure(error);
      }
    },

    reportRecoveryFailure(error) {
      console.warn('地图本地恢复副本写入失败', error);
      if (this.recoveryStorageWarningShown) return;
      this.recoveryStorageWarningShown = true;
      notify(
         '浏览器无法保存本地恢复副本；服务端自动保存不受影响，请在离开页面前确认显示“地图已保存”。',
        '本地恢复暂不可用',
      );
    },

    clearLocalRecovery(mapId = this.selectedMapId) {
      const key = this.recoveryKey(mapId);
      if (!key) return;
      deleteRecovery(key).catch(error => console.warn('清理地图本地恢复副本失败', error));
    },

    async restoreLocalRecovery(mapId, draft) {
      // 只有服务器仍处于同一乐观锁版本时才自动恢复，冲突内容保留但不覆盖。
      const key = this.recoveryKey(mapId);
      if (!key || !draft) return false;
      let recovery;
      try {
        recovery = await readRecovery(key);
      } catch (_error) {
        this.clearLocalRecovery(mapId);
        return false;
      }
      if (!recovery || recovery.schema !== MAP_RECOVERY_SCHEMA || !recovery.world) {
        this.clearLocalRecovery(mapId);
        return false;
      }
      if (same(recovery.world, draft.world)) {
        this.clearLocalRecovery(mapId);
        return false;
      }
      if (String(recovery.baseLockVersion) !== String(draft.row_version)) {
         notify('服务器地图已发生变化，本地恢复副本已保留，但不会自动覆盖服务器内容。', '检测到并发修改');
        return false;
      }
      this.publicEditor.setWorld(recovery.world);
      this.publicEditor.changed = true;
      this.setAutoSaveStatus('recovered');
       notify('已从浏览器恢复上次未完成的地图修改，并将自动保存。', '已恢复本地修改');
      return true;
    },

    handleBeforeUnload(event) {
      if (!this.publicEditor?.changed || !this.draft) return;
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

    requireCompleteMap(detail, operation = '加载') {
      if (!detail?.world || !detail.world.definition || typeof detail.world.definition !== 'object') {
        throw new Error(`地图${operation}响应缺少完整 world；请重启后端服务并确认数据库已升级到当前基线`);
      }
      return detail;
    },

    async openMap(mapId, push = true) {
      this.init();
      if (!experimentScope && push) window.ResourceList.capture('maps');
      const generation = this.openGeneration = (this.openGeneration || 0) + 1;
      if (this.selectedMapId && this.selectedMapId !== mapId && (this.publicEditor.changed || this.savePromise)) {
        await this.savePublic({ manual: false });
      }
      this.cancelScheduledSaves();
      const detail = this.requireCompleteMap(await request(`/maps/${mapId}`), '加载');
      if (generation !== this.openGeneration) return;
      this.detail = detail;
      this.selectedMapId = mapId;
      this.draft = this.detail;
      this.publicEditor.setWorld(this.draft.world);
      document.getElementById('mapCatalogShell').hidden = true;
      document.getElementById('mapEditorShell').hidden = false;
      requestAnimationFrame(() => {
        this.publicEditor.resize();
        this.publicEditor.fit();
      });
      document.getElementById('mapEditorTitle').textContent = this.detail.name;
      document.getElementById('mapEditorMeta').textContent = `${this.detail.map_key} · ${this.draft.world.definition.size[1]} × ${this.draft.world.definition.size[0]} 格 · 实时编辑`;
      const editable = this.detail.editable !== false;
      document.getElementById('mapEditorMeta').textContent = `${this.detail.map_key} · ${this.draft.world.definition.size[1]} × ${this.draft.world.definition.size[0]} 格 · ${editable ? '可编辑' : '只读'}`;
      this.publicEditor.setReadOnly(!editable);
      const state = document.getElementById('mapEditorState');
      state.textContent = experimentScope ? (editable ? '实验地图' : '已封存 · 只读') : '实时地图';
      state.classList.add('draft');
      document.getElementById('publishMapBtn').textContent = '校验地图';
      this.lastSavedAt = null;
      const recovered = editable && await this.restoreLocalRecovery(mapId, this.draft);
      if (!recovered) this.setAutoSaveStatus('saved');
      this.renderBuildGuide();
      this.renderAudit();
      window.dispatchEvent(new CustomEvent('map-workspace:selection', { detail: { mapId } }));
      if (push) this.replaceMapUrl(mapId);
      if (!experimentScope && push) window.scrollTo({top: 0, behavior: 'instant'});
    },

    async showCatalog() {
      if (this.publicEditor?.changed || this.savePromise) await this.savePublic({ manual: false });
      this.cancelScheduledSaves();
      this.selectedMapId = null;
      document.getElementById('mapCatalogShell').hidden = false;
      document.getElementById('mapEditorShell').hidden = true;
      window.dispatchEvent(new CustomEvent('map-workspace:selection', { detail: { mapId: null } }));
      this.replaceMapUrl(null);
      if (!experimentScope) { await this.loadMaps(); window.ResourceList.restore('maps'); }
    },

    replaceMapUrl(mapId) {
      if (!experimentScope) { window.ResourceList.route('maps', mapId ? {map_id: mapId} : {}); return; }
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
      if (!asset || !this.draft) {
        notify('请先打开一张地图。', '无法加入地图');
        return;
      }
      const contract = asset.contract;
      const definition = this.publicEditor.definition;
      const scene = definition.spatial_scene ||= {
        schema_version: 'ga-spatial-scene/v2',
        palette_refs: {}, placements: [],
      };
      this.publicEditor.editor.spatial_assets[asset.id] = deepClone(contract);
      if (['TILE', 'MARKING'].includes(contract.kind)) {
        scene.palette_refs[asset.asset_key] = asset.id;
        const palette = this.publicEditor.editor.palette;
        const visual = {
          id: asset.asset_key,
          name: asset.name,
          color: contract.appearance.color || '#eef2ef',
          emoji: contract.appearance.emoji || '',
          collision: Boolean(contract.physics.collision),
          spatial_asset_id: asset.id,
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
          spatial_asset_id: asset.id,
          x_tiles: (definition.size[1] - 1) / 2,
          y_tiles: (definition.size[0] - 1) / 2,
          rotation_degrees: 0,
          state_overrides: {},
        });
        notify(`${asset.name} 已放置在地图中心；以后素材修改会直接同步。`, '物件已加入地图');
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
        <div class="map-audit-card"><span>地图尺寸（格）</span><strong>${definition.size[1]} × ${definition.size[0]}</strong></div>
        <div class="map-audit-card"><span>渲染尺寸（px）</span><strong>${definition.size[1] * definition.tile_size} × ${definition.size[0] * definition.tile_size}</strong></div>
        <div class="map-audit-card"><span>碰撞 Tile</span><strong>${collisions.toLocaleString('zh-CN')}</strong></div>
        <div class="map-audit-card"><span>语义 Tile</span><strong>${addressed.toLocaleString('zh-CN')}</strong></div>
        <div class="map-audit-card"><span>关联空间素材</span><strong>${spatialAssets.toLocaleString('zh-CN')}</strong></div>`;
      const validationRoot = document.querySelector('[data-map-validation]');
      const validation = this.draft.validation;
      if (validationRoot) {
        const checks = validation?.checks || [];
        const errors = validation?.errors || [];
        const warnings = validation?.warnings || [];
        const issueMessage = issue => typeof issue === 'string' ? issue : (issue.message || issue.code || '未知问题');
        validationRoot.innerHTML = validation ? `
          <div class="map-validation-heading"><div><h3>地图校验明细</h3><p>${validation.valid ? '当前地图已通过校验' : '当前地图未通过校验'}</p></div><span class="map-state ${validation.valid ? '' : 'draft'}">${errors.length} 阻断 · ${warnings.length} 警告</span></div>
          <div class="map-validation-list">
            ${checks.map(item => `<div class="map-validation-row ${item.status === 'PASSED' ? 'passed' : 'failed'}"><b>${item.status === 'PASSED' ? '✓' : '×'}</b><span><strong>${escapeHtml(item.message)}</strong><code>${escapeHtml(item.code)}</code></span></div>`).join('')}
            ${errors.map(item => `<div class="map-validation-row failed"><b>×</b><span><strong>${escapeHtml(issueMessage(item))}</strong><code>${escapeHtml(item.code || item.path || '')}</code></span></div>`).join('')}
            ${warnings.map(item => `<div class="map-validation-row warning"><b>!</b><span><strong>${escapeHtml(issueMessage(item))}</strong><code>${escapeHtml(item.code || item.path || '')}</code></span></div>`).join('')}
          </div>` : '<div class="map-validation-empty"><strong>尚未执行地图校验</strong><span>将地图选入实验时会完整复制地图、空间素材与被动 Skill；实验封存前还会校验包内内容。</span></div>';
      }
      document.querySelector('[data-map-revisions]').innerHTML = `
        <h3>当前内容</h3>
        <div class="map-revision-row"><strong>${experimentScope ? '实验包内当前内容' : `第 ${this.draft.row_version} 次保存`}</strong><code>${escapeHtml((this.draft.world_hash || '').slice(0, 16))}…</code><span>${this.draft.updated_at ? new Date(this.draft.updated_at).toLocaleString('zh-CN') : ''}</span><span class="map-state draft">实时生效</span></div>`;
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
      button.disabled = Boolean(guide.complete);
      button.textContent = guide.complete ? '构建完成' : `应用第 ${current + 1} 步`;
      document.getElementById('mapBuildGuideHint').textContent = guide.complete
        ? '完整地图已经保存；仍可继续编辑，已有实验不受影响。'
        : '每一步都由服务器直接写入当前地图，刷新页面不会丢失进度。';
    },

    async applyBlueprintStep() {
      const guide = this.draft?.world?.definition?.editor?.build_guide;
      if (!guide || guide.complete) return;
      const nextStep = Number(guide.current_step || 0) + 1;
      const button = document.getElementById('applyMapBlueprintStep');
      button.disabled = true;
      button.textContent = `正在应用第 ${nextStep} 步…`;
      try {
        if (this.publicEditor.changed || this.savePromise) await this.savePublic({ manual: false });
        const saved = this.requireCompleteMap(await request(`/maps/${this.selectedMapId}/blueprint-steps/${nextStep}`, {
          method: 'POST',
          body: JSON.stringify({ row_version: this.draft.row_version }),
        }), '构建');
        this.draft = saved;
        this.publicEditor.setWorld(saved.world);
        this.clearLocalRecovery(this.selectedMapId);
        this.lastSavedAt = new Date();
        this.setAutoSaveStatus('saved', this.formatSaveTime(this.lastSavedAt));
        this.renderBuildGuide();
        this.renderAudit();
        const step = saved.world.definition.editor.build_guide.steps[nextStep - 1];
        notify(`${step.name} 已写入当前地图。`, `构建进度 ${nextStep} / ${guide.total_steps}`);
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
      this.updateCreatePixelSize();
    },

    updateCreatePixelSize() {
      const widthTiles = Number(document.getElementById('newMapWidth').value);
      const heightTiles = Number(document.getElementById('newMapHeight').value);
      const tileSizePx = Number(document.getElementById('newMapTileSize').value);
      const preview = document.getElementById('newMapPixelSize');
      if (!preview) return;
      if (![widthTiles, heightTiles, tileSizePx].every(Number.isInteger)
        || widthTiles < 1 || heightTiles < 1 || tileSizePx < 1) {
        preview.textContent = '1 表示 1 个 Tile；请输入正整数格数后由系统计算像素尺寸。';
        return;
      }
      preview.textContent = `1 表示 1 个 Tile；最终像素尺寸：${widthTiles * tileSizePx} × ${heightTiles * tileSizePx} px（系统自动换算）`;
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
      const keyInput = document.getElementById('newMapKey');
      const rawKey = keyInput.value.trim();
      const normalizedKey = normalizeOptionalMapKey(rawKey);
      keyInput.value = normalizedKey || '';
      const dimensions = [
        { id: 'newMapWidth', label: '宽度（格）', min: 1, max: 240 },
        { id: 'newMapHeight', label: '高度（格）', min: 1, max: 240 },
        { id: 'newMapTileSize', label: 'Tile 尺寸', min: 8, max: 128 },
      ];
      for (const dimension of dimensions) {
        const input = document.getElementById(dimension.id);
        const value = Number(input.value);
        input.setCustomValidity('');
        if (!Number.isInteger(value) || value < dimension.min || value > dimension.max) {
          input.setCustomValidity(`${dimension.label}必须是 ${dimension.min}–${dimension.max} 的整数`);
          input.reportValidity();
          input.focus();
          return;
        }
      }
      const created = await request('/maps', {
        method: 'POST',
        body: JSON.stringify({
          name,
          description: document.getElementById('newMapDescription').value.trim(),
          source_map_id: document.getElementById('newMapSource').value || null,
          blueprint_key: document.getElementById('newMapBlueprint').value || null,
          map_key: normalizedKey,
          width: Number(document.getElementById('newMapWidth').value),
          height: Number(document.getElementById('newMapHeight').value),
          tile_size: Number(document.getElementById('newMapTileSize').value),
        }),
      });
      modal('close', 'createMapModal');
      await this.loadMaps();
      await this.openMap(created.id);
      const keyMessage = rawKey && normalizedKey !== rawKey
        ? normalizedKey
          ? `稳定键已规范为 ${normalizedKey}。`
          : `输入的稳定键不符合格式，已自动生成 ${created.map_key}。`
        : '';
      notify(`${keyMessage}已创建独立地图，可直接编辑。`, '地图已创建');
    },

    async savePublic({ manual = false } = {}) {
      // 同一时间只允许一个保存 Promise；保存期间发生的新编辑会在响应后再次排队。
      if (!this.draft || this.publicEditor.readonly) return this.draft;
      clearTimeout(this.autoSaveTimer);
      this.autoSaveTimer = null;

      if (this.savePromise) {
        await this.savePromise;
        if (this.publicEditor.changed) return this.savePublic({ manual });
        if (manual) notify('当前地图已经是最新状态。', '地图已保存');
        return this.draft;
      }
      if (!this.publicEditor.changed) {
        this.setAutoSaveStatus('saved', this.lastSavedAt ? this.formatSaveTime(this.lastSavedAt) : '');
        if (manual) notify('当前地图已经是最新状态。', '地图已保存');
        return this.draft;
      }

      const mapId = this.selectedMapId;
      const draftId = this.draft.id;
      const lockVersion = this.draft.row_version;
      const editorRevision = this.publicEditor.changeRevision;
      const world = this.publicEditor.getWorld();
      this.setAutoSaveStatus('saving');

      const operation = (async () => {
        const saved = this.requireCompleteMap(await request(`/maps/${mapId}`, {
          method: 'PUT', body: JSON.stringify({ row_version: lockVersion, world }),
        }), '保存');
        if (this.selectedMapId !== mapId || this.draft?.id !== draftId) return saved;
        this.draft = saved;
        // 仅确认请求发出时的 editorRevision；更晚发生的编辑仍保持 changed=true。
        this.publicEditor.acceptSavedWorld(saved.world, editorRevision);
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
        notify('地图结构、语义和画块已写入当前地图。', '地图已保存');
      }
      return saved;
    },

    async validateMap() {
      const publishButton = document.getElementById('publishMapBtn');
      publishButton.disabled = true;
      try {
        if (this.publicEditor.changed || this.savePromise) await this.savePublic({ manual: false });
        const validated = await request(`/maps/${this.selectedMapId}/validate?row_version=${encodeURIComponent(this.draft.row_version)}`, {
          method: 'POST', body: JSON.stringify({ row_version: this.draft.row_version }),
        });
        this.draft = validated;
        this.detail = validated;
        this.clearLocalRecovery(this.selectedMapId);
        this.setTab('audit');
        const report = validated.validation || {};
        if (report.valid) {
          notify('当前地图已通过校验。', '地图校验通过');
        } else {
          notify(`${(report.errors || []).length} 个阻断问题、${(report.warnings || []).length} 个警告，请查看校验明细。`, '地图校验未通过');
        }
      } catch (error) {
        if (error.code === 'MAP_VALIDATION_FAILED' && error.details) {
          this.draft.validation = error.details;
          this.renderAudit();
          this.setTab('audit');
          notify(`${(error.details.errors || []).length} 个阻断问题、${(error.details.warnings || []).length} 个警告，请查看校验明细。`, '地图校验失败');
        }
        if (this.publicEditor.changed) this.setAutoSaveStatus('error', error.message || '校验失败');
        else this.setAutoSaveStatus('saved', this.lastSavedAt ? this.formatSaveTime(this.lastSavedAt) : '');
        throw error;
      } finally {
        publishButton.disabled = false;
      }
    },

    async handlePublicEditorEditRequest(event) {
      if (this.publicEditor?.readonly) return;
      if (event.detail?.intent !== 'new-canvas' || !this.draft) return;
      if (this.editTransitionPromise) return;
      const count = this.publicEditor.document?.material_canvases?.length || 0;
      document.getElementById('newCanvasName').value = `画布 ${count + 1}`;
      document.getElementById('newCanvasWidth').value = '32';
      document.getElementById('newCanvasHeight').value = '32';
      modal('open', 'createCanvasModal', 'newCanvasName');
    },

    async confirmCreateCanvas() {
      if (!this.draft || this.editTransitionPromise) return;
      const name = document.getElementById('newCanvasName').value.trim();
      const width = Number(document.getElementById('newCanvasWidth').value);
      const height = Number(document.getElementById('newCanvasHeight').value);
      if (!name) throw new Error('请填写画布名称');
      if (!Number.isInteger(width) || width < 1 || width > 256 || !Number.isInteger(height) || height < 1 || height > 256) {
        throw new Error('画布宽高必须是 1–256 的整数');
      }
      const confirmButton = document.getElementById('confirmCreateCanvas');
      const action = (async () => {
        confirmButton.disabled = true;
        this.publicEditor.createMaterialCanvas({ name, width, height });
        modal('close', 'createCanvasModal');
      })();
      this.editTransitionPromise = action;
      try {
        await action;
      } finally {
        confirmButton.disabled = false;
        if (this.editTransitionPromise === action) this.editTransitionPromise = null;
      }
    },

    async setExperimentContext(context) {
      this.init();
      this.experiment = context;
      if (experimentScope) {
        const selector = document.getElementById('experimentMapSelect');
        selector.replaceChildren(new Option(context.world?.world_name || '实验地图', 'world'));
        selector.disabled = true;
        document.getElementById('experimentMapMeta').textContent = '当前实验的独立地图副本';
        return;
      }
      if (!this.selectorMaps.length) await this.loadMaps();
      this.populateMapSelectors();
      const world = context.world || {};
      const meta = document.getElementById('experimentMapMeta');
      if (meta) meta.textContent = world.world_name
        ? '实验持有完整世界副本；公共地图后续变化不会影响这里'
        : '尚未导入地图';
    },

    async selectExperimentMap() {
      throw new Error('实验中的地图是物理副本；如需更换，请执行一次新的完整地图导入');
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
