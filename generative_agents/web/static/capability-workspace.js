(function () {
  'use strict';

  const API = '/api/v1';
  const TARGETS = [
    ['AGENT', '人'], ['BRAIN', '大脑'], ['TOOL', '工具'], ['MAP_OBJECT', '地图物件'],
    ['ZONE', '区域'], ['INTERACTION', '交互'], ['WORLD', '世界'],
  ];
  const KIND_LABELS = {
    SENSOR: '感知', DECISION: '决策', ACTION: '行动', CONTROLLER: '控制器',
    OBSERVER: '观测器', ADAPTER: '适配器',
  };
  const manager = {
    initialized: false,
    assetType: 'capability',
    query: '',
    kind: '',
    items: [],
    capabilityCatalog: [],
    bundleCatalog: [],
    toolCatalog: [],
    detail: null,
    revision: null,
    revisions: [],
    selectedType: null,
    dirty: false,
    searchTimer: null,

    $(id) { return document.getElementById(id); },
    escape(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
    },
    async request(path, options = {}) {
      const response = await fetch(`${API}${path}`, {
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        ...options,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        const error = new Error(body?.error?.message || `请求失败（${response.status}）`);
        error.code = body?.error?.code;
        error.details = body?.error?.details;
        throw error;
      }
      return response.status === 204 ? null : response.json();
    },
    notify(message, title = '操作成功') {
      window.dispatchEvent(new CustomEvent('capability-workspace:toast', { detail: { message, title } }));
    },
    fail(error) {
      console.error(error);
      window.dispatchEvent(new CustomEvent('capability-workspace:error', { detail: { error } }));
    },
    setDirty(value = true) {
      this.dirty = value;
      if (!this.revision || this.revision.state !== 'DRAFT') return;
      this.$('saveCapabilityBtn').textContent = value ? '保存草稿 ·' : '保存草稿';
    },

    init() {
      if (this.initialized) return;
      this.initialized = true;
      this.$('createCapabilityBtn').addEventListener('click', () => this.openCreatePanel());
      this.$('cancelCreateCapability').addEventListener('click', () => { this.$('capabilityCreatePanel').hidden = true; });
      this.$('confirmCreateCapability').addEventListener('click', () => this.createAsset().catch(error => this.fail(error)));
      this.$('newCapabilityAssetType').addEventListener('change', event => this.updateCreateType(event.target.value));
      this.$('backToCapabilitiesBtn').addEventListener('click', () => this.showCatalog());
      this.$('saveCapabilityBtn').addEventListener('click', () => this.save().catch(error => this.fail(error)));
      this.$('publishCapabilityBtn').addEventListener('click', () => this.publishOrFork().catch(error => this.fail(error)));
      this.$('capabilitySearch').addEventListener('input', event => {
        clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => { this.query = event.target.value.trim(); this.loadCatalog().catch(error => this.fail(error)); }, 220);
      });
      this.$('capabilityKindFilter').addEventListener('change', event => { this.kind = event.target.value; this.loadCatalog().catch(error => this.fail(error)); });
      document.querySelectorAll('[data-capability-asset]').forEach(tab => tab.addEventListener('click', () => {
        this.assetType = tab.dataset.capabilityAsset;
        this.kind = '';
        document.querySelectorAll('[data-capability-asset]').forEach(item => item.classList.toggle('active', item === tab));
        this.renderKindFilter();
        this.loadCatalog().catch(error => this.fail(error));
      }));
      document.querySelectorAll('[data-capability-section]').forEach(button => button.addEventListener('click', () => this.selectSection(button.dataset.capabilitySection)));
      this.$('addCapabilityInput').addEventListener('click', () => this.addPort('input'));
      this.$('addCapabilityOutput').addEventListener('click', () => this.addPort('output'));
      this.$('addCapabilityParameter').addEventListener('click', () => this.addParameter());
      this.$('capabilityTriggerMode').addEventListener('change', () => { this.renderTriggerFields(); this.setDirty(); });
      this.$('capabilityImplementationKind').addEventListener('change', () => { this.renderImplementationFields(); this.setDirty(); });
      this.$('addBundleInstance').addEventListener('click', () => this.addBundleInstance());
      this.$('addBundleBinding').addEventListener('click', () => this.addBundleBinding());
      this.$('addToolCapabilityAttachment').addEventListener('click', () => this.addToolAttachment());
      document.querySelectorAll('.capability-dirty,.bundle-dirty,.tool-dirty').forEach(control => control.addEventListener('input', () => this.setDirty()));
      this.$('capabilityTargetChecks').innerHTML = TARGETS.map(([value, label]) => `<label><input type="checkbox" value="${value}" class="capability-target capability-dirty" />${label}<code>${value}</code></label>`).join('');
      this.$('capabilityTargetChecks').querySelectorAll('input').forEach(control => control.addEventListener('change', () => this.setDirty()));
      this.renderKindFilter();
    },

    renderKindFilter() {
      const filter = this.$('capabilityKindFilter');
      filter.hidden = this.assetType === 'bundle';
      const capabilityOptions = [['', '全部类型'], ...Object.entries(KIND_LABELS).map(([value, label]) => [value, label])];
      const toolOptions = [['', '全部工具'], ['CAR', '汽车'], ['BICYCLE', '自行车'], ['MOTORCYCLE', '摩托车'], ['ACCESS_CARD', '门禁卡'], ['DEVICE', '设备'], ['OTHER', '其他']];
      const options = this.assetType === 'tool' ? toolOptions : capabilityOptions;
      filter.innerHTML = options.map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
      filter.value = this.kind;
    },

    async activate() {
      this.init();
      await this.loadCatalog();
    },

    async loadCatalog() {
      this.init();
      const commonParams = new URLSearchParams({ page: '1', page_size: '100' });
      if (this.query) commonParams.set('q', this.query);
      if (this.kind && ['capability', 'tool'].includes(this.assetType)) commonParams.set('kind', this.kind);
      const endpoint = this.assetType === 'bundle' ? '/capability-bundles' : this.assetType === 'tool' ? '/tools' : '/capabilities';
      const [result, capabilities, bundles, tools] = await Promise.all([
        this.request(`${endpoint}?${commonParams}`),
        this.request('/capabilities?status=PUBLISHED&page=1&page_size=100'),
        this.request('/capability-bundles?page=1&page_size=100'),
        this.request('/tools?page=1&page_size=100'),
      ]);
      this.items = result.items;
      this.capabilityCatalog = capabilities.items.filter(item => item.current_published);
      this.bundleCatalog = bundles.items.filter(item => item.current_published);
      this.toolCatalog = tools.items.filter(item => item.current_published);
      this.renderCatalog(result);
      this.renderHero(capabilities, bundles, tools);
      this.populateBaseCapabilities();
    },

    renderHero(capabilities, bundles, tools) {
      const capabilityCount = capabilities.total || 0;
      const publishedCount = Number(capabilities.status_counts?.PUBLISHED || 0);
      this.$('capabilityHeroStats').innerHTML = `<span><strong>${capabilityCount}</strong>原子能力</span><span><strong>${bundles.total || 0}</strong>能力包</span><span><strong>${tools.total || 0}</strong>工具资产</span><span><strong>${publishedCount}</strong>可装配版本</span>`;
    },

    renderCatalog(result) {
      const grid = this.$('capabilityCatalogGrid');
      grid.innerHTML = this.items.length ? this.items.map(item => this.assetType === 'bundle' ? this.bundleCard(item) : this.assetType === 'tool' ? this.toolCard(item) : this.capabilityCard(item)).join('') : '<div class="empty-state"><strong>没有符合条件的可复用资产</strong><span>可以清除搜索条件，或创建一个新的资产草稿。</span></div>';
      grid.querySelectorAll('[data-capability-id]').forEach(card => card.addEventListener('click', () => this.openAsset('capability', card.dataset.capabilityId).catch(error => this.fail(error))));
      grid.querySelectorAll('[data-bundle-id]').forEach(card => card.addEventListener('click', () => this.openAsset('bundle', card.dataset.bundleId).catch(error => this.fail(error))));
      grid.querySelectorAll('[data-tool-id]').forEach(card => card.addEventListener('click', () => this.openAsset('tool', card.dataset.toolId).catch(error => this.fail(error))));
      this.$('capabilityListFooter').hidden = result.total === 0;
      const labels = { capability: '原子能力', bundle: '能力包', tool: '工具资产' };
      this.$('capabilityCatalogCount').textContent = `共 ${result.total} 个${labels[this.assetType]}；发布版本可被地图、Agent、大脑、工具与实验引用。`;
    },

    capabilityCard(item) {
      const contract = item.active_contract || {};
      const status = item.current_draft ? '编辑中' : '已发布';
      return `<button class="capability-card" data-kind="${this.escape(contract.kind)}" data-capability-id="${item.id}">
        <span class="capability-card-top"><span class="capability-card-kind">${this.escape(KIND_LABELS[contract.kind] || contract.kind)}</span><span class="map-state ${item.current_draft ? 'draft' : ''}">${status}${item.is_builtin ? ' · 系统' : ''}</span></span>
        <h2>${this.escape(item.name)}</h2><p>${this.escape(contract.summary || item.description || '尚未填写用途说明')}</p>
        <span class="capability-card-targets">${(contract.targets || []).map(target => `<span>${this.escape(target)}</span>`).join('')}</span>
        <span class="capability-card-foot"><code>${this.escape(item.capability_key)}</code><span>${(contract.inputs || []).length} 入 / ${(contract.outputs || []).length} 出</span></span>
      </button>`;
    },

    bundleCard(item) {
      return `<button class="capability-card" data-kind="BUNDLE" data-bundle-id="${item.id}">
        <span class="capability-card-top"><span class="capability-card-kind">能力包</span><span class="map-state ${item.current_draft ? 'draft' : ''}">${item.current_draft ? '编辑中' : '已发布'}</span></span>
        <h2>${this.escape(item.name)}</h2><p>${this.escape(item.description || '将多个原子能力组合为可复用的场景能力。')}</p>
        <span class="capability-card-targets">${(item.targets || []).map(target => `<span>${this.escape(target)}</span>`).join('')}</span>
        <span class="capability-card-foot"><code>${this.escape(item.bundle_key)}</code><span>${item.instance_count} 实例 / ${item.binding_count} 连线</span></span>
      </button>`;
    },

    toolCard(item) {
      const contract = item.active_contract || {};
      const mobility = contract.mobility || {};
      return `<button class="capability-card" data-kind="${this.escape(contract.kind || item.tool_kind)}" data-tool-id="${item.id}">
        <span class="capability-card-top"><span class="capability-card-kind">${this.escape(contract.appearance?.emoji || '🧰')} ${this.escape(contract.kind || item.tool_kind)}</span><span class="map-state ${item.current_draft ? 'draft' : ''}">${item.current_draft ? '编辑中' : '已发布'}${item.is_builtin ? ' · 系统' : ''}</span></span>
        <h2>${this.escape(item.name)}</h2><p>${this.escape(contract.summary || item.description || '可授权给 Agent 使用的工具实体。')}</p>
        <span class="capability-card-targets">${(contract.interfaces || []).map(value => `<span>${this.escape(value)}</span>`).join('')}</span>
        <span class="capability-card-foot"><code>${this.escape(item.tool_key)}</code><span>${mobility.mode === 'NONE' ? '不可移动' : `${this.escape(mobility.mode)} · ${mobility.max_speed_mps || 0}m/s`}</span></span>
      </button>`;
    },

    openCreatePanel() {
      const panel = this.$('capabilityCreatePanel');
      panel.hidden = false;
      this.$('newCapabilityName').value = '';
      this.$('newCapabilityKey').value = '';
      this.updateCreateType(this.assetType);
      this.$('newCapabilityName').focus();
    },

    updateCreateType(type) {
      this.$('newCapabilityAssetType').value = type;
      this.$('newBundleBaseCapability').hidden = type !== 'bundle';
      this.$('newToolKind').hidden = type !== 'tool';
      this.$('newCapabilityKey').placeholder = type === 'bundle' ? '稳定键，例如 crossing-safety' : type === 'tool' ? '稳定键，例如 family-car' : '稳定键，例如 traffic-light-control';
    },

    populateBaseCapabilities() {
      const select = this.$('newBundleBaseCapability');
      select.innerHTML = this.capabilityCatalog.map(item => `<option value="${item.current_published.id}">${this.escape(item.name)} · ${this.escape(KIND_LABELS[item.active_contract?.kind] || item.active_contract?.kind)}</option>`).join('');
    },

    async createAsset() {
      const type = this.$('newCapabilityAssetType').value;
      const name = this.$('newCapabilityName').value.trim();
      const key = this.$('newCapabilityKey').value.trim();
      if (!name) throw new Error('请填写资产名称。');
      let created;
      if (type === 'capability') {
        const body = { name };
        if (key) body.capability_key = key;
        created = await this.request('/capabilities', { method: 'POST', body: JSON.stringify(body) });
      } else if (type === 'bundle') {
        const revisionId = this.$('newBundleBaseCapability').value;
        const source = this.capabilityCatalog.find(item => item.current_published?.id === revisionId);
        if (!source) throw new Error('创建能力包前至少需要一个已发布原子能力。');
        const contract = source.active_contract;
        const trigger = contract.triggers.find(item => item.default) || contract.triggers[0];
        const instanceKey = this.uniqueKey(source.capability_key.replaceAll('-', '_'), []);
        const body = {
          name,
          composition: {
            schema_version: 'ga-capability-bundle/v1', name, summary: '',
            targets: [contract.targets[0]],
            instances: [{ instance_key: instanceKey, capability_revision_id: revisionId, target_ref: `${contract.targets[0].toLowerCase()}:primary`, parameters: this.defaultParameters(contract.parameters_schema), run_policy: this.triggerPolicy(trigger), enabled: true }],
            bindings: [],
            exposed_parameters_schema: { type: 'object', properties: {}, additionalProperties: false },
          },
        };
        if (key) body.bundle_key = key;
        created = await this.request('/capability-bundles', { method: 'POST', body: JSON.stringify(body) });
      } else {
        const body = { name, tool_kind: this.$('newToolKind').value };
        if (key) body.tool_key = key;
        created = await this.request('/tools', { method: 'POST', body: JSON.stringify(body) });
      }
      this.$('capabilityCreatePanel').hidden = true;
      this.assetType = type;
      document.querySelectorAll('[data-capability-asset]').forEach(tab => tab.classList.toggle('active', tab.dataset.capabilityAsset === type));
      this.renderKindFilter();
      this.notify(`${name} 已创建为可编辑草稿。`, '能力资产已创建');
      await this.loadCatalog();
      await this.openAsset(type, created.id);
    },

    async openAsset(type, id) {
      this.selectedType = type;
      const root = type === 'bundle' ? '/capability-bundles' : type === 'tool' ? '/tools' : '/capabilities';
      this.detail = await this.request(`${root}/${id}`);
      this.revisions = (await this.request(`${root}/${id}/revisions`)).items;
      this.revision = this.detail.current_draft
        ? await this.request(`${root}/${id}/draft`)
        : await this.request(`${root}/${id}/revisions/${this.detail.current_published.id}`);
      this.$('capabilityCatalogShell').hidden = true;
      this.$('capabilityEditorShell').hidden = false;
      this.$('atomicCapabilityEditor').hidden = type !== 'capability';
      this.$('capabilityBundleEditor').hidden = type !== 'bundle';
      this.$('capabilityToolEditor').hidden = type !== 'tool';
      this.$('capabilityEditorTitle').textContent = this.detail.name;
      const key = type === 'bundle' ? this.detail.bundle_key : type === 'tool' ? this.detail.tool_key : this.detail.capability_key;
      const labels = { capability: '原子能力', bundle: '能力组合', tool: '工具实体' };
      this.$('capabilityEditorMeta').textContent = `${key} · Revision ${String(this.revision.revision_no).padStart(3, '0')} · ${labels[type]}`;
      this.renderEditorState();
      if (type === 'capability') this.populateAtomic(); else if (type === 'bundle') this.populateBundle(); else this.populateTool();
      this.setDirty(false);
      window.dispatchEvent(new CustomEvent('capability-workspace:selection', { detail: { type, id } }));
    },

    renderEditorState() {
      const editable = this.revision.state === 'DRAFT';
      const state = this.$('capabilityEditorState');
      state.textContent = editable ? '草稿' : this.detail.is_builtin ? '系统基线 · 只读' : '已发布 · 只读';
      state.classList.toggle('draft', editable);
      this.$('saveCapabilityBtn').disabled = !editable;
      this.$('publishCapabilityBtn').textContent = editable ? '发布版本' : this.detail.is_builtin ? '基于此能力创建' : '创建新修订';
      this.$('capabilityEditorShell').querySelectorAll('input,select,textarea,button.capability-row-remove').forEach(control => {
        if (!['backToCapabilitiesBtn'].includes(control.id) && !control.closest('.capability-editor-actions')) control.disabled = !editable;
      });
      ['addCapabilityInput', 'addCapabilityOutput', 'addCapabilityParameter', 'addBundleInstance', 'addBundleBinding', 'addToolCapabilityAttachment'].forEach(id => {
        this.$(id).disabled = !editable;
      });
    },

    showCatalog() {
      if (this.dirty && !window.confirm('当前能力草稿有未保存修改，仍要返回能力中心吗？')) return;
      this.$('capabilityEditorShell').hidden = true;
      this.$('capabilityCatalogShell').hidden = false;
      this.detail = null; this.revision = null; this.revisions = []; this.selectedType = null; this.setDirty(false);
      this.loadCatalog().catch(error => this.fail(error));
      window.dispatchEvent(new CustomEvent('capability-workspace:selection', { detail: { type: null, id: null } }));
    },

    selectSection(section) {
      document.querySelectorAll('[data-capability-section]').forEach(button => button.classList.toggle('active', button.dataset.capabilitySection === section));
      document.querySelectorAll('[data-capability-panel]').forEach(panel => panel.classList.toggle('active', panel.dataset.capabilityPanel === section));
    },

    populateAtomic() {
      const contract = this.revision.contract;
      this.$('capabilityEditName').value = this.detail.name;
      this.$('capabilityEditKey').value = this.detail.capability_key;
      this.$('capabilityEditKind').value = contract.kind;
      this.$('capabilityEditSummary').value = contract.summary || '';
      this.$('capabilityEditInterfaces').value = (contract.interfaces || []).join(', ');
      this.$('capabilityPermissions').value = (contract.permissions || []).join(', ');
      document.querySelectorAll('.capability-target').forEach(check => { check.checked = contract.targets.includes(check.value); });
      const trigger = contract.triggers.find(item => item.default) || contract.triggers[0];
      this.$('capabilityTriggerMode').value = trigger.mode;
      this.$('capabilityIntervalMs').value = trigger.interval_ms || 1000;
      this.$('capabilityEventTypes').value = (trigger.event_types || []).join(', ');
      this.$('capabilityImplementationKind').value = contract.implementation.kind;
      this.$('capabilityEntrypoint').value = contract.implementation.entrypoint || '';
      this.$('capabilitySource').value = contract.implementation.source || '';
      this.$('capabilityRecordInputs').checked = Boolean(contract.observability?.record_inputs);
      this.$('capabilityRecordOutputs').checked = contract.observability?.record_outputs !== false;
      this.$('capabilityRecordState').checked = Boolean(contract.observability?.record_state);
      this.renderPorts('input', contract.inputs || []);
      this.renderPorts('output', contract.outputs || []);
      this.renderParameters(contract.parameters_schema || {});
      this.renderTriggerFields(); this.renderImplementationFields(); this.renderAtomicValidation(); this.renderRevisions('capability');
      this.selectSection('identity');
      this.renderEditorState();
    },

    renderPorts(direction, ports) {
      const list = this.$(direction === 'input' ? 'capabilityInputList' : 'capabilityOutputList');
      list.innerHTML = ports.length ? ports.map(port => this.portRow(direction, port)).join('') : `<div class="empty-state"><span>尚未定义${direction === 'input' ? '输入' : '输出'}端口</span></div>`;
      this.bindRowEvents(list);
    },
    portRow(direction, port = {}) {
      return `<div class="capability-port-row" data-port-direction="${direction}">
        <input class="control capability-port-name" data-field="name" value="${this.escape(port.name || '')}" placeholder="显示名称" />
        <input class="control" data-field="key" value="${this.escape(port.key || '')}" placeholder="稳定端口键" />
        <input class="control" data-field="data_type" value="${this.escape(port.data_type || (direction === 'input' ? 'state/value' : 'event/value'))}" placeholder="state/motion" />
        <label><input type="checkbox" data-field="required" ${port.required ? 'checked' : ''} />必填</label>
        <label><input type="checkbox" data-field="multiple" ${port.multiple ? 'checked' : ''} />多值</label>
        <button type="button" class="capability-row-remove" aria-label="删除端口">×</button>
      </div>`;
    },
    addPort(direction) {
      const list = this.$(direction === 'input' ? 'capabilityInputList' : 'capabilityOutputList');
      if (list.querySelector('.empty-state')) list.innerHTML = '';
      list.insertAdjacentHTML('beforeend', this.portRow(direction));
      this.bindRowEvents(list); this.setDirty();
    },

    renderParameters(schema) {
      const properties = schema.properties || {};
      const required = new Set(schema.required || []);
      const list = this.$('capabilityParameterList');
      list.innerHTML = Object.entries(properties).length ? Object.entries(properties).map(([key, definition]) => this.parameterRow(key, definition, required.has(key))).join('') : '<div class="empty-state"><span>该能力没有实例参数</span></div>';
      this.bindRowEvents(list);
    },
    parameterRow(key = '', definition = {}, required = false) {
      const value = definition.default ?? definition.minimum ?? '';
      return `<div class="capability-parameter-row"><input class="control" data-field="key" value="${this.escape(key)}" placeholder="参数键" /><select class="control" data-field="type"><option value="string" ${definition.type === 'string' ? 'selected' : ''}>文本</option><option value="number" ${definition.type === 'number' ? 'selected' : ''}>小数</option><option value="integer" ${definition.type === 'integer' ? 'selected' : ''}>整数</option><option value="boolean" ${definition.type === 'boolean' ? 'selected' : ''}>布尔</option></select><input class="control" data-field="default" value="${this.escape(value)}" placeholder="默认值" /><label><input type="checkbox" data-field="required" ${required ? 'checked' : ''} />必填</label><button type="button" class="capability-row-remove" aria-label="删除参数">×</button></div>`;
    },
    addParameter() {
      const list = this.$('capabilityParameterList');
      if (list.querySelector('.empty-state')) list.innerHTML = '';
      list.insertAdjacentHTML('beforeend', this.parameterRow());
      this.bindRowEvents(list); this.setDirty();
    },
    bindRowEvents(container) {
      container.querySelectorAll('input,select').forEach(control => { control.oninput = () => this.setDirty(); });
      container.querySelectorAll('.capability-row-remove').forEach(button => { button.onclick = () => { button.parentElement.remove(); this.setDirty(); }; });
    },

    renderTriggerFields() {
      const mode = this.$('capabilityTriggerMode').value;
      this.$('capabilityIntervalField').hidden = mode !== 'FIXED_INTERVAL';
      this.$('capabilityEventTypesField').hidden = mode !== 'EVENT';
    },
    renderImplementationFields() {
      const kind = this.$('capabilityImplementationKind').value;
      this.$('capabilityEntrypointField').hidden = kind !== 'BUILTIN';
      this.$('capabilitySourceField').hidden = !['PYTHON', 'RULES'].includes(kind);
    },

    readPorts(direction) {
      const list = this.$(direction === 'input' ? 'capabilityInputList' : 'capabilityOutputList');
      return [...list.querySelectorAll('.capability-port-row')].map(row => ({
        key: row.querySelector('[data-field="key"]').value.trim(),
        name: row.querySelector('[data-field="name"]').value.trim(),
        data_type: row.querySelector('[data-field="data_type"]').value.trim(),
        description: '',
        required: row.querySelector('[data-field="required"]').checked,
        multiple: row.querySelector('[data-field="multiple"]').checked,
      }));
    },
    readParameterSchema() {
      const properties = {}; const required = [];
      this.$('capabilityParameterList').querySelectorAll('.capability-parameter-row').forEach(row => {
        const key = row.querySelector('[data-field="key"]').value.trim();
        if (!key) return;
        const type = row.querySelector('[data-field="type"]').value;
        const raw = row.querySelector('[data-field="default"]').value.trim();
        const definition = { type };
        if (raw !== '') definition.default = type === 'boolean' ? raw === 'true' : ['number', 'integer'].includes(type) ? Number(raw) : raw;
        properties[key] = definition;
        if (row.querySelector('[data-field="required"]').checked) required.push(key);
      });
      return { type: 'object', properties, required, additionalProperties: false };
    },
    commaValues(id) { return this.$(id).value.split(/[,，]/).map(item => item.trim()).filter(Boolean); },
    buildContract() {
      const original = this.revision.contract;
      const mode = this.$('capabilityTriggerMode').value;
      const trigger = { mode, default: true, event_types: [] };
      if (mode === 'FIXED_INTERVAL') trigger.interval_ms = Number(this.$('capabilityIntervalMs').value);
      if (mode === 'EVENT') trigger.event_types = this.commaValues('capabilityEventTypes');
      const implementationKind = this.$('capabilityImplementationKind').value;
      const implementation = { kind: implementationKind, entrypoint: null, source: null, config: original.implementation?.config || {}, deterministic: original.implementation?.deterministic !== false };
      if (implementationKind === 'BUILTIN') implementation.entrypoint = this.$('capabilityEntrypoint').value.trim();
      if (['PYTHON', 'RULES'].includes(implementationKind)) implementation.source = this.$('capabilitySource').value;
      const permissions = this.commaValues('capabilityPermissions');
      if (implementationKind === 'PYTHON' && !permissions.includes('execute-python')) permissions.push('execute-python');
      const outputs = this.readPorts('output');
      const outputKeys = new Set(outputs.map(item => item.key));
      return {
        schema_version: 'ga-capability/v1',
        name: this.$('capabilityEditName').value.trim(),
        summary: this.$('capabilityEditSummary').value.trim(),
        kind: this.$('capabilityEditKind').value,
        targets: [...document.querySelectorAll('.capability-target:checked')].map(item => item.value),
        interfaces: this.commaValues('capabilityEditInterfaces'),
        parameters_schema: this.readParameterSchema(),
        inputs: this.readPorts('input'), outputs,
        state_schema: original.state_schema || { type: 'object', properties: {}, additionalProperties: false },
        triggers: [trigger], implementation,
        dependencies: original.dependencies || [], permissions,
        observability: {
          record_inputs: this.$('capabilityRecordInputs').checked,
          record_outputs: this.$('capabilityRecordOutputs').checked,
          record_state: this.$('capabilityRecordState').checked,
          metric_outputs: (original.observability?.metric_outputs || []).filter(key => outputKeys.has(key)),
          sensitive_inputs: (original.observability?.sensitive_inputs || []).filter(key => this.readPorts('input').some(port => port.key === key)),
        },
      };
    },

    localContractErrors(contract) {
      const errors = [];
      if (!contract.name) errors.push('名称不能为空。');
      if (!contract.targets.length) errors.push('至少选择一个可挂载目标。');
      const ports = [...contract.inputs, ...contract.outputs];
      const keys = ports.map(item => item.key).filter(Boolean);
      if (keys.length !== new Set(keys).size) errors.push('输入与输出端口键必须全局唯一。');
      ports.forEach((port, index) => { if (!port.key || !port.name || !port.data_type) errors.push(`第 ${index + 1} 个端口的名称、键和类型必须完整。`); });
      if (contract.triggers[0].mode === 'EVENT' && !contract.triggers[0].event_types.length) errors.push('事件触发至少需要一种 event/* 类型。');
      if (contract.implementation.kind === 'BUILTIN' && !contract.implementation.entrypoint) errors.push('系统内置实现需要入口标识。');
      if (contract.implementation.kind === 'PYTHON' && !contract.implementation.source?.trim()) errors.push('Python 实现需要脚本内容。');
      return errors;
    },
    renderAtomicValidation(extraErrors = null) {
      let contract;
      try { contract = this.buildContract(); } catch (error) { extraErrors = [error.message]; }
      const errors = extraErrors || this.localContractErrors(contract);
      this.$('capabilityValidation').innerHTML = errors.length
        ? errors.map(message => `<div class="capability-validation-item error"><strong>!</strong><span>${this.escape(message)}</span></div>`).join('')
        : '<div class="capability-validation-item"><strong>✓</strong><span>本地结构检查通过；发布时还会校验依赖版本和权限。</span></div>';
    },

    renderRevisions(type) {
      const target = this.$(type === 'bundle' ? 'bundleRevisionList' : type === 'tool' ? 'toolRevisionList' : 'capabilityRevisionList');
      target.innerHTML = this.revisions.map(item => `<div class="capability-revision-item"><span><strong>Revision ${String(item.revision_no).padStart(3, '0')}</strong> · ${item.state === 'DRAFT' ? '草稿' : '已发布'}</span><code>${this.escape((item.contract_hash || item.composition_hash || '').slice(0, 12))}</code></div>`).join('');
    },

    async save() {
      if (!this.revision || this.revision.state !== 'DRAFT') return;
      if (this.selectedType === 'capability') {
        const contract = this.buildContract();
        const localErrors = this.localContractErrors(contract);
        if (localErrors.length) { this.renderAtomicValidation(localErrors); this.selectSection('audit'); throw new Error(localErrors[0]); }
        this.revision = await this.request(`/capabilities/${this.detail.id}/draft`, { method: 'PUT', body: JSON.stringify({ lock_version: this.revision.lock_version, name: contract.name, description: contract.summary, contract }) });
        this.detail.name = contract.name; this.detail.description = contract.summary;
        this.$('capabilityEditorTitle').textContent = contract.name;
        this.renderAtomicValidation();
      } else if (this.selectedType === 'bundle') {
        const composition = this.readBundleComposition();
        const errors = this.localBundleErrors(composition);
        if (errors.length) { this.renderBundleValidation(errors); throw new Error(errors[0]); }
        this.revision = await this.request(`/capability-bundles/${this.detail.id}/draft`, { method: 'PUT', body: JSON.stringify({ lock_version: this.revision.lock_version, name: composition.name, description: composition.summary, composition }) });
        this.detail.name = composition.name; this.detail.description = composition.summary;
        this.$('capabilityEditorTitle').textContent = composition.name;
        this.renderBundleValidation();
      } else {
        const contract = this.readToolContract();
        const errors = this.localToolErrors(contract);
        if (errors.length) { this.renderToolValidation(errors); throw new Error(errors[0]); }
        this.revision = await this.request(`/tools/${this.detail.id}/draft`, { method: 'PUT', body: JSON.stringify({ lock_version: this.revision.lock_version, name: contract.name, description: contract.summary, contract }) });
        this.detail.name = contract.name; this.detail.description = contract.summary;
        this.$('capabilityEditorTitle').textContent = contract.name;
        this.renderToolValidation();
      }
      this.setDirty(false);
      this.notify('草稿和装配配置已保存。', '能力资产已保存');
    },

    async publishOrFork() {
      if (this.revision.state === 'DRAFT') {
        if (this.dirty) await this.save();
        const root = this.selectedType === 'bundle' ? '/capability-bundles' : this.selectedType === 'tool' ? '/tools' : '/capabilities';
        try {
          this.revision = await this.request(`${root}/${this.detail.id}/draft/publish`, { method: 'POST', body: JSON.stringify({ draft_revision_id: this.revision.id, lock_version: this.revision.lock_version }) });
        } catch (error) {
          const messages = (error.details?.errors || []).map(item => item.message || item.code);
          if (this.selectedType === 'capability') { this.renderAtomicValidation(messages.length ? messages : [error.message]); this.selectSection('audit'); }
          else if (this.selectedType === 'bundle') this.renderBundleValidation(messages.length ? messages : [error.message]);
          else this.renderToolValidation(messages.length ? messages : [error.message]);
          throw error;
        }
        this.notify('已发布不可变版本，现在可以在地图、Agent、大脑或实验中引用。', '能力资产已发布');
        await this.openAsset(this.selectedType, this.detail.id);
        return;
      }
      if (this.detail.is_builtin) {
        const root = this.selectedType === 'tool' ? '/tools' : '/capabilities';
        const created = await this.request(root, { method: 'POST', body: JSON.stringify({ name: `${this.detail.name}（自定义）`, source_revision_id: this.revision.id }) });
        this.notify('系统基线未被修改，已创建一个独立的自定义草稿。', '已基于系统资产创建');
        await this.openAsset(this.selectedType, created.id);
        return;
      }
      const root = this.selectedType === 'bundle' ? '/capability-bundles' : this.selectedType === 'tool' ? '/tools' : '/capabilities';
      await this.request(`${root}/${this.detail.id}/revisions/${this.revision.id}/fork`, { method: 'POST' });
      this.notify('已从发布版本创建新的修订草稿。', '新修订已创建');
      await this.openAsset(this.selectedType, this.detail.id);
    },

    populateBundle() {
      const composition = structuredClone(this.revision.composition);
      this.revision.composition = composition;
      this.$('bundleEditName').value = this.detail.name;
      this.$('bundleEditKey').value = this.detail.bundle_key;
      this.$('bundleEditSummary').value = composition.summary || '';
      this.renderBundleInstances(); this.renderBundleBindings(); this.renderBundleValidation(); this.renderRevisions('bundle'); this.renderEditorState();
    },
    populateTool() {
      const contract = structuredClone(this.revision.contract);
      this.revision.contract = contract;
      this.$('toolEditName').value = this.detail.name;
      this.$('toolEditKey').value = this.detail.tool_key;
      this.$('toolEditKind').value = contract.kind;
      this.$('toolEditSummary').value = contract.summary || '';
      this.$('toolEditEmoji').value = contract.appearance?.emoji || '🧰';
      this.$('toolEditTags').value = (contract.tags || []).join(', ');
      this.$('toolEditInterfaces').value = (contract.interfaces || []).join(', ');
      this.$('toolMobilityMode').value = contract.mobility?.mode || 'NONE';
      this.$('toolMaxSpeed').value = contract.mobility?.max_speed_mps || 0;
      this.$('toolMaxAcceleration').value = contract.mobility?.max_acceleration_mps2 || 0;
      this.$('toolMaxDeceleration').value = contract.mobility?.max_deceleration_mps2 || 0;
      this.$('toolCapacity').value = contract.mobility?.capacity || 1;
      this.$('toolOperatorRequired').checked = Boolean(contract.mobility?.operator_required);
      this.$('toolInitialState').value = JSON.stringify(contract.initial_state || {}, null, 2);
      this.renderToolAttachments(); this.renderToolValidation(); this.renderRevisions('tool'); this.renderEditorState();
    },
    toolAttachmentOptions() {
      return [
        ...this.capabilityCatalog
          .filter(item => (item.active_contract?.targets || []).some(target => ['TOOL', 'WORLD'].includes(target)))
          .map(item => ({ value: `capability:${item.current_published.id}`, label: `${item.name} · ${KIND_LABELS[item.active_contract.kind] || item.active_contract.kind}` })),
        ...this.bundleCatalog
          .filter(item => (item.targets || []).some(target => ['TOOL', 'WORLD'].includes(target)))
          .map(item => ({ value: `bundle:${item.current_published.id}`, label: `${item.name} · 能力包` })),
      ];
    },
    renderToolAttachments() {
      const attachments = this.revision.contract.capability_attachments || [];
      const options = this.toolAttachmentOptions();
      const list = this.$('toolCapabilityAttachmentList');
      list.innerHTML = attachments.length ? attachments.map((attachment, index) => {
        const selected = attachment.capability_revision_id ? `capability:${attachment.capability_revision_id}` : `bundle:${attachment.capability_bundle_revision_id}`;
        return `<div class="capability-tool-attachment" data-tool-attachment-index="${index}">
          <select class="control" data-tool-attachment-field="revision">${options.map(option => `<option value="${this.escape(option.value)}" ${option.value === selected ? 'selected' : ''}>${this.escape(option.label)}</option>`).join('')}</select>
          <input class="control" data-tool-attachment-field="attachment_key" value="${this.escape(attachment.attachment_key)}" placeholder="挂载键" />
          <input class="control" data-tool-attachment-field="parameters" value="${this.escape(JSON.stringify(attachment.parameters || {}))}" placeholder="参数 JSON" />
          <button type="button" class="capability-row-remove" data-remove-tool-attachment="${index}">×</button>
        </div>`;
      }).join('') : '<div class="empty-state"><span>尚未挂载工具能力；工具仍可作为无主动行为的实体使用。</span></div>';
      list.querySelectorAll('input,select').forEach(control => control.addEventListener('input', () => { this.setDirty(); this.renderToolValidation(); }));
      list.querySelectorAll('[data-remove-tool-attachment]').forEach(button => button.addEventListener('click', () => {
        attachments.splice(Number(button.dataset.removeToolAttachment), 1); this.renderToolAttachments(); this.setDirty(); this.renderToolValidation();
      }));
      this.renderEditorState();
    },
    addToolAttachment() {
      if (this.revision?.state !== 'DRAFT') return;
      const option = this.toolAttachmentOptions()[0];
      if (!option) { this.renderToolValidation(['暂无可挂载到 TOOL/WORLD 的已发布能力，请先发布相关原子能力或能力包。']); return; }
      const [type, revisionId] = option.value.split(':');
      const attachments = this.revision.contract.capability_attachments || (this.revision.contract.capability_attachments = []);
      const existing = new Set(attachments.map(item => item.attachment_key));
      let index = attachments.length + 1; let key = `tool-ability-${index}`;
      while (existing.has(key)) key = `tool-ability-${++index}`;
      attachments.push({ attachment_key: key, capability_revision_id: type === 'capability' ? revisionId : null, capability_bundle_revision_id: type === 'bundle' ? revisionId : null, parameters: {}, enabled: true });
      this.renderToolAttachments(); this.setDirty();
    },
    readToolAttachments() {
      return [...this.$('toolCapabilityAttachmentList').querySelectorAll('[data-tool-attachment-index]')].map(row => {
        const selected = row.querySelector('[data-tool-attachment-field="revision"]').value;
        const [type, revisionId] = selected.split(':');
        let parameters;
        try { parameters = JSON.parse(row.querySelector('[data-tool-attachment-field="parameters"]').value || '{}'); }
        catch (error) { throw new Error(`工具能力参数必须是 JSON：${error.message}`); }
        return {
          attachment_key: row.querySelector('[data-tool-attachment-field="attachment_key"]').value.trim(),
          capability_revision_id: type === 'capability' ? revisionId : null,
          capability_bundle_revision_id: type === 'bundle' ? revisionId : null,
          parameters,
          enabled: true,
        };
      });
    },
    readToolContract() {
      const original = this.revision.contract;
      const mode = this.$('toolMobilityMode').value;
      let initialState;
      try { initialState = JSON.parse(this.$('toolInitialState').value || '{}'); }
      catch (error) { throw new Error(`工具初始状态必须是 JSON：${error.message}`); }
      return {
        schema_version: 'ga-tool/v1',
        name: this.$('toolEditName').value.trim(),
        summary: this.$('toolEditSummary').value.trim(),
        kind: this.$('toolEditKind').value,
        appearance: { mode: 'EMOJI', color: null, emoji: this.$('toolEditEmoji').value.trim() || '🧰', asset_path: null, scale: original.appearance?.scale || 1, rotation_degrees: original.appearance?.rotation_degrees || 0, state_variants: original.appearance?.state_variants || {} },
        mobility: {
          mode,
          max_speed_mps: mode === 'NONE' ? 0 : Number(this.$('toolMaxSpeed').value),
          max_acceleration_mps2: mode === 'NONE' ? 0 : Number(this.$('toolMaxAcceleration').value),
          max_deceleration_mps2: mode === 'NONE' ? 0 : Number(this.$('toolMaxDeceleration').value),
          operator_required: mode !== 'NONE' && this.$('toolOperatorRequired').checked,
          capacity: Number(this.$('toolCapacity').value) || 1,
        },
        tags: this.commaValues('toolEditTags'),
        interfaces: this.commaValues('toolEditInterfaces'),
        initial_state: initialState,
        capability_attachments: this.readToolAttachments(),
      };
    },
    localToolErrors(contract = null) {
      const errors = [];
      try { contract = contract || this.readToolContract(); } catch (error) { return [error.message]; }
      if (!contract.name) errors.push('工具名称不能为空。');
      if (['CAR', 'BICYCLE', 'MOTORCYCLE'].includes(contract.kind) && contract.mobility.mode === 'NONE') errors.push('车辆工具必须选择移动网络。');
      if (!['CAR', 'BICYCLE', 'MOTORCYCLE'].includes(contract.kind) && contract.mobility.mode !== 'NONE') errors.push('只有车辆类工具可以声明移动能力。');
      if (contract.mobility.mode !== 'NONE' && contract.mobility.max_speed_mps <= 0) errors.push('可移动工具必须设置正数最高速度。');
      const keys = contract.capability_attachments.map(item => item.attachment_key);
      if (keys.some(key => !key)) errors.push('工具能力挂载键不能为空。');
      if (keys.length !== new Set(keys).size) errors.push('工具能力挂载键不能重复。');
      return errors;
    },
    renderToolValidation(extraErrors = null) {
      const errors = extraErrors || this.localToolErrors();
      this.$('toolValidation').innerHTML = errors.length
        ? errors.map(message => `<div class="capability-validation-item error"><strong>!</strong><span>${this.escape(message)}</span></div>`).join('')
        : '<div class="capability-validation-item"><strong>✓</strong><span>工具物理约束与本地结构检查通过；发布时还会校验能力目标和参数。</span></div>';
    },
    capabilityByRevision(revisionId) { return this.capabilityCatalog.find(item => item.current_published?.id === revisionId); },
    defaultParameters(schema = {}) {
      const result = {};
      Object.entries(schema.properties || {}).forEach(([key, definition]) => {
        if ('default' in definition) result[key] = definition.default;
        else if (definition.type === 'boolean') result[key] = false;
        else if (definition.type === 'number' || definition.type === 'integer') result[key] = definition.minimum ?? 0;
        else if (definition.type === 'array') result[key] = [];
        else if (definition.type === 'object') result[key] = {};
        else result[key] = '';
      });
      return result;
    },
    triggerPolicy(trigger) {
      const policy = { trigger: trigger.mode, interval_ms: null, event_types: [] };
      if (trigger.mode === 'FIXED_INTERVAL') policy.interval_ms = trigger.interval_ms;
      if (trigger.mode === 'EVENT') policy.event_types = trigger.event_types || [];
      return policy;
    },
    uniqueKey(base, existing) {
      let key = String(base || 'instance').toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/^[^a-z]+/, '') || 'instance';
      let candidate = key; let index = 2;
      while (existing.includes(candidate)) candidate = `${key}_${index++}`;
      return candidate.slice(0, 80);
    },

    renderBundleInstances() {
      const composition = this.revision.composition;
      const list = this.$('bundleInstanceList');
      list.innerHTML = composition.instances.map((instance, index) => {
        const capability = this.capabilityByRevision(instance.capability_revision_id);
        const contract = capability?.active_contract || {};
        return `<article class="bundle-instance-card" data-instance-index="${index}">
          <div class="bundle-row"><input class="control" data-instance-field="instance_key" value="${this.escape(instance.instance_key)}" aria-label="实例键" /><button type="button" class="capability-row-remove" data-remove-instance="${index}">×</button></div>
          <select class="control" data-instance-field="capability_revision_id">${this.capabilityCatalog.map(item => `<option value="${item.current_published.id}" ${item.current_published.id === instance.capability_revision_id ? 'selected' : ''}>${this.escape(item.name)} · ${this.escape(KIND_LABELS[item.active_contract?.kind] || item.active_contract?.kind)}</option>`).join('')}</select>
          <div class="bundle-row"><input class="control" data-instance-field="target_ref" value="${this.escape(instance.target_ref)}" placeholder="目标，例如 tool:car-01" /><input class="control" data-instance-field="interval_ms" type="number" min="1" value="${instance.run_policy.interval_ms || ''}" ${instance.run_policy.trigger !== 'FIXED_INTERVAL' ? 'disabled' : ''} aria-label="运行间隔毫秒" /></div>
          <div class="bundle-instance-parameters">${this.bundleParameterFields(contract.parameters_schema, instance.parameters)}</div>
        </article>`;
      }).join('') || '<div class="empty-state"><strong>尚未添加能力实例</strong></div>';
      list.querySelectorAll('[data-remove-instance]').forEach(button => button.addEventListener('click', () => { composition.instances.splice(Number(button.dataset.removeInstance), 1); this.cleanupBindings(); this.renderBundleInstances(); this.renderBundleBindings(); this.setDirty(); }));
      list.querySelectorAll('[data-instance-field]').forEach(control => control.addEventListener('change', event => this.updateBundleInstance(event)));
      list.querySelectorAll('[data-param-key]').forEach(control => control.addEventListener('input', event => {
        const card = event.target.closest('[data-instance-index]');
        composition.instances[Number(card.dataset.instanceIndex)].parameters[event.target.dataset.paramKey] = this.coerceParameter(event.target.value, event.target.dataset.paramType);
        this.setDirty(); this.renderBundleValidation();
      }));
    },
    bundleParameterFields(schema = {}, parameters = {}) {
      const entries = Object.entries(schema.properties || {});
      return entries.length ? entries.map(([key, definition]) => `<label class="bundle-param-field"><span>${this.escape(key)}${(schema.required || []).includes(key) ? ' *' : ''}</span><input class="control" data-param-key="${this.escape(key)}" data-param-type="${this.escape(definition.type || 'string')}" value="${this.escape(parameters[key] ?? '')}" /></label>`).join('') : '<span>无实例参数</span>';
    },
    coerceParameter(value, type) { if (type === 'boolean') return value === 'true'; if (type === 'number' || type === 'integer') return value === '' ? null : Number(value); return value; },
    updateBundleInstance(event) {
      const card = event.target.closest('[data-instance-index]');
      const instance = this.revision.composition.instances[Number(card.dataset.instanceIndex)];
      const field = event.target.dataset.instanceField;
      if (field === 'interval_ms') instance.run_policy.interval_ms = Number(event.target.value);
      else instance[field] = event.target.value.trim();
      if (field === 'capability_revision_id') {
        const capability = this.capabilityByRevision(instance.capability_revision_id);
        const contract = capability.active_contract;
        const trigger = contract.triggers.find(item => item.default) || contract.triggers[0];
        instance.parameters = this.defaultParameters(contract.parameters_schema);
        instance.run_policy = this.triggerPolicy(trigger);
        this.renderBundleInstances(); this.cleanupBindings(); this.renderBundleBindings();
      }
      this.setDirty(); this.renderBundleValidation();
    },
    addBundleInstance() {
      const composition = this.revision.composition;
      const capability = this.capabilityCatalog[0];
      if (!capability) return;
      const contract = capability.active_contract;
      const trigger = contract.triggers.find(item => item.default) || contract.triggers[0];
      composition.instances.push({ instance_key: this.uniqueKey(capability.capability_key.replaceAll('-', '_'), composition.instances.map(item => item.instance_key)), capability_revision_id: capability.current_published.id, target_ref: `${contract.targets[0].toLowerCase()}:primary`, parameters: this.defaultParameters(contract.parameters_schema), run_policy: this.triggerPolicy(trigger), enabled: true });
      if (!composition.targets.includes(contract.targets[0])) composition.targets.push(contract.targets[0]);
      this.renderBundleInstances(); this.renderBundleBindings(); this.setDirty();
    },

    outputEndpoints() {
      return this.revision.composition.instances.flatMap(instance => {
        const capability = this.capabilityByRevision(instance.capability_revision_id);
        return (capability?.active_contract?.outputs || []).map(port => ({ value: `${instance.instance_key}.${port.key}`, label: `${instance.instance_key}.${port.key} · ${port.data_type}`, type: port.data_type }));
      });
    },
    inputEndpoints() {
      return this.revision.composition.instances.flatMap(instance => {
        const capability = this.capabilityByRevision(instance.capability_revision_id);
        return (capability?.active_contract?.inputs || []).map(port => ({ value: `${instance.instance_key}.${port.key}`, label: `${instance.instance_key}.${port.key} · ${port.data_type}`, type: port.data_type }));
      });
    },
    renderBundleBindings() {
      const composition = this.revision.composition;
      const outputs = this.outputEndpoints(); const inputs = this.inputEndpoints();
      this.$('bundleBindingList').innerHTML = composition.bindings.map((binding, index) => {
        const sourceValue = `${binding.source.instance_key}.${binding.source.port_key}`;
        const targetValue = `${binding.target.instance_key}.${binding.target.port_key}`;
        return `<article class="bundle-binding-row" data-binding-index="${index}"><div class="bundle-row"><input class="control" data-binding-field="binding_key" value="${this.escape(binding.binding_key)}" /><button type="button" class="capability-row-remove" data-remove-binding="${index}">×</button></div><select class="control" data-binding-field="source">${outputs.map(item => `<option value="${this.escape(item.value)}" ${item.value === sourceValue ? 'selected' : ''}>${this.escape(item.label)}</option>`).join('')}</select><select class="control" data-binding-field="target">${inputs.map(item => `<option value="${this.escape(item.value)}" ${item.value === targetValue ? 'selected' : ''}>${this.escape(item.label)}</option>`).join('')}</select><select class="control" data-binding-field="delivery"><option value="LATEST" ${binding.delivery === 'LATEST' ? 'selected' : ''}>LATEST · 最新状态</option><option value="QUEUE" ${binding.delivery === 'QUEUE' ? 'selected' : ''}>QUEUE · 事件队列</option><option value="ACCUMULATE" ${binding.delivery === 'ACCUMULATE' ? 'selected' : ''}>ACCUMULATE · 累积</option></select></article>`;
      }).join('') || '<div class="empty-state"><span>暂无连线；没有必填输入的能力可以独立运行。</span></div>';
      this.$('bundleBindingList').querySelectorAll('[data-remove-binding]').forEach(button => button.addEventListener('click', () => { composition.bindings.splice(Number(button.dataset.removeBinding), 1); this.renderBundleBindings(); this.setDirty(); this.renderBundleValidation(); }));
      this.$('bundleBindingList').querySelectorAll('[data-binding-field]').forEach(control => control.addEventListener('change', event => {
        const row = event.target.closest('[data-binding-index]'); const binding = composition.bindings[Number(row.dataset.bindingIndex)]; const field = event.target.dataset.bindingField;
        if (field === 'source' || field === 'target') { const [instance_key, port_key] = event.target.value.split('.'); binding[field] = { instance_key, port_key }; } else binding[field] = event.target.value.trim();
        this.setDirty(); this.renderBundleValidation();
      }));
      this.$('addBundleBinding').disabled = !outputs.length || !inputs.length || this.revision.state !== 'DRAFT';
    },
    addBundleBinding() {
      const outputs = this.outputEndpoints(); const inputs = this.inputEndpoints();
      if (!outputs.length || !inputs.length) return;
      const [sourceInstance, sourcePort] = outputs[0].value.split('.'); const [targetInstance, targetPort] = inputs[0].value.split('.');
      const existing = this.revision.composition.bindings.map(item => item.binding_key);
      this.revision.composition.bindings.push({ binding_key: this.uniqueKey(`${sourcePort}_to_${targetPort}`, existing), source: { instance_key: sourceInstance, port_key: sourcePort }, target: { instance_key: targetInstance, port_key: targetPort }, delivery: 'LATEST' });
      this.renderBundleBindings(); this.setDirty(); this.renderBundleValidation();
    },
    cleanupBindings() {
      const outputs = new Set(this.outputEndpoints().map(item => item.value)); const inputs = new Set(this.inputEndpoints().map(item => item.value));
      this.revision.composition.bindings = this.revision.composition.bindings.filter(binding => outputs.has(`${binding.source.instance_key}.${binding.source.port_key}`) && inputs.has(`${binding.target.instance_key}.${binding.target.port_key}`));
    },
    readBundleComposition() {
      const composition = structuredClone(this.revision.composition);
      composition.name = this.$('bundleEditName').value.trim();
      composition.summary = this.$('bundleEditSummary').value.trim();
      return composition;
    },
    localBundleErrors(composition = this.readBundleComposition()) {
      const errors = [];
      if (!composition.name) errors.push('能力包名称不能为空。');
      if (!composition.instances.length) errors.push('能力包至少需要一个能力实例。');
      const instanceKeys = composition.instances.map(item => item.instance_key);
      if (instanceKeys.some(key => !/^[a-z][a-z0-9_]*$/.test(key))) errors.push('实例键只能使用小写字母、数字和下划线，并以字母开头。');
      if (instanceKeys.length !== new Set(instanceKeys).size) errors.push('实例键不能重复。');
      const incoming = new Set(composition.bindings.map(item => `${item.target.instance_key}.${item.target.port_key}`));
      composition.instances.forEach(instance => {
        const capability = this.capabilityByRevision(instance.capability_revision_id); const contract = capability?.active_contract;
        (contract?.inputs || []).filter(port => port.required).forEach(port => { if (!incoming.has(`${instance.instance_key}.${port.key}`)) errors.push(`${instance.instance_key}.${port.key} 是必填输入，但尚未连接。`); });
        (contract?.parameters_schema?.required || []).forEach(key => { if (instance.parameters[key] === '' || instance.parameters[key] == null) errors.push(`${instance.instance_key} 缺少必填参数 ${key}。`); });
      });
      const outputMap = new Map(this.outputEndpoints().map(item => [item.value, item.type])); const inputMap = new Map(this.inputEndpoints().map(item => [item.value, item.type]));
      composition.bindings.forEach(binding => { const source = `${binding.source.instance_key}.${binding.source.port_key}`; const target = `${binding.target.instance_key}.${binding.target.port_key}`; const sourceType = outputMap.get(source); const targetType = inputMap.get(target); if (sourceType && targetType && sourceType !== targetType && sourceType !== 'any' && targetType !== 'any') errors.push(`${source}（${sourceType}）不能连接到 ${target}（${targetType}）。`); });
      return [...new Set(errors)];
    },
    renderBundleValidation(extraErrors = null) {
      const errors = extraErrors || this.localBundleErrors();
      this.$('bundleValidation').innerHTML = errors.length ? errors.map(message => `<div class="capability-validation-item error"><strong>!</strong><span>${this.escape(message)}</span></div>`).join('') : '<div class="capability-validation-item"><strong>✓</strong><span>实例参数、必填输入和端口类型的本地检查通过。</span></div>';
    },
  };

  window.CapabilityWorkspace = manager;
})();
