(function () {
  'use strict';

  const API = '/api/v1';
  const manager = {
    initialized: false,
    page: 1,
    pageSize: 5,
    status: '',
    query: '',
    brains: [],
    selectorBrains: [],
    selectedBrainId: null,
    detail: null,
    revision: null,
    revisions: [],
    capabilityExtensionRecord: null,
    capabilityBundles: [],
    capabilityDirty: false,
    experiment: null,
    searchTimer: null,
    listGeneration: 0,

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

    escape(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
    },

    notify(message, title = '操作成功') {
      window.dispatchEvent(new CustomEvent('brain-workspace:toast', { detail: { message, title } }));
    },

    fail(error) {
      console.error(error);
      window.dispatchEvent(new CustomEvent('brain-workspace:error', { detail: { error } }));
    },

    modal(action, id, focusId = null) {
      window.dispatchEvent(new CustomEvent('brain-workspace:modal', { detail: { action, id, focusId } }));
    },

    init() {
      if (this.initialized) return;
      this.initialized = true;
      document.getElementById('createBrainBtn').addEventListener('click', () => this.openCreate());
      document.getElementById('backToBrainsBtn').addEventListener('click', () => this.showCatalog());
      document.getElementById('saveBrainBtn').addEventListener('click', () => this.save().catch(error => this.fail(error)));
      document.getElementById('publishBrainBtn').addEventListener('click', () => this.publishOrFork().catch(error => this.fail(error)));
      document.getElementById('confirmCreateBrain').addEventListener('click', () => this.create().catch(error => this.fail(error)));
      ['closeCreateBrain', 'cancelCreateBrain'].forEach(id => document.getElementById(id).addEventListener('click', () => this.modal('close', 'createBrainModal')));
      document.getElementById('saveExperimentBrainTemplateBtn').addEventListener('click', () => this.openSaveExperimentTemplate());
      document.getElementById('confirmSaveBrainTemplate').addEventListener('click', () => this.saveExperimentTemplate().catch(error => this.fail(error)));
      ['closeSaveBrainTemplate', 'cancelSaveBrainTemplate'].forEach(id => document.getElementById(id).addEventListener('click', () => this.modal('close', 'saveBrainTemplateModal')));
      document.getElementById('brainSearch').addEventListener('input', event => {
        clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => {
          this.query = event.target.value.trim(); this.page = 1;
          this.loadBrains().catch(error => this.fail(error));
        }, 250);
      });
      document.querySelectorAll('[data-brain-filter]').forEach(tab => tab.addEventListener('click', () => {
        document.querySelectorAll('[data-brain-filter]').forEach(item => item.classList.toggle('active', item === tab));
        this.status = tab.dataset.brainFilter === 'all' ? '' : tab.dataset.brainFilter.toUpperCase();
        this.page = 1; this.loadBrains().catch(error => this.fail(error));
      }));
      document.getElementById('brainPagination').addEventListener('click', event => {
        const pageButton = event.target.closest('[data-brain-page]');
        const totalPages = Number(document.getElementById('brainPagination').dataset.totalPages || 1);
        if (pageButton) this.page = Number(pageButton.dataset.brainPage);
        else if (event.target.id === 'brainPrev') this.page = Math.max(1, this.page - 1);
        else if (event.target.id === 'brainNext') this.page = Math.min(totalPages, this.page + 1);
        else return;
        this.loadBrains().catch(error => this.fail(error));
      });
      document.getElementById('applyExperimentBrainBtn').addEventListener('click', () => this.applyToExperiment().catch(error => this.fail(error)));
      document.querySelectorAll('[data-brain-editor-tab]').forEach(tab => tab.addEventListener('click', () => this.selectEditorTab(tab.dataset.brainEditorTab)));
      document.getElementById('addBrainCapabilityMount').addEventListener('click', () => this.addCapabilityMount());
      ['brainReasoningInterval', 'brainLegacyAdapter'].forEach(id => document.getElementById(id).addEventListener('input', () => { this.capabilityDirty = true; this.renderCapabilityValidation(); }));
    },

    selectEditorTab(tabName) {
      document.querySelectorAll('[data-brain-editor-tab]').forEach(tab => {
        const active = tab.dataset.brainEditorTab === tabName;
        tab.classList.toggle('active', active); tab.setAttribute('aria-selected', String(active));
      });
      document.querySelectorAll('[data-brain-editor-panel]').forEach(panel => panel.classList.toggle('active', panel.dataset.brainEditorPanel === tabName));
    },

    renderCapabilityExtension() {
      const extension = this.capabilityExtensionRecord?.extension || {
        mounts: [], default_reasoning_interval_ms: 60000, legacy_workflow_adapter_enabled: true,
      };
      document.getElementById('brainReasoningInterval').value = extension.default_reasoning_interval_ms || 60000;
      document.getElementById('brainLegacyAdapter').checked = extension.legacy_workflow_adapter_enabled !== false;
      const list = document.getElementById('brainCapabilityMountList');
      const categories = [
        ['SCHEDULE_STATE', '日程与状态'], ['PERCEPTION_MEMORY', '感知与记忆'], ['ACTION_SPACE', '行动与空间'],
        ['SOCIAL', '社交'], ['REFLECTION', '反思'], ['CUSTOM', '自定义'],
      ];
      list.innerHTML = (extension.mounts || []).length ? extension.mounts.map((mount, index) => `<div class="brain-capability-mount" data-brain-mount-index="${index}">
        <select class="control brain-capability-control" data-brain-mount-field="category">${categories.map(([value, label]) => `<option value="${value}" ${mount.category === value ? 'selected' : ''}>${label}</option>`).join('')}</select>
        <select class="control brain-capability-control" data-brain-mount-field="bundle">${this.capabilityBundles.map(item => `<option value="${item.current_published.id}" ${mount.capability_bundle_revision_id === item.current_published.id ? 'selected' : ''}>${this.escape(item.name)} · v${item.current_published.revision_no}</option>`).join('')}</select>
        <input class="control brain-capability-control" data-brain-mount-field="mount_key" value="${this.escape(mount.mount_key)}" placeholder="稳定挂载键" />
        <button type="button" class="capability-row-remove" data-remove-brain-mount="${index}">×</button>
        <input class="control brain-capability-control brain-mount-parameters" data-brain-mount-field="parameters" value="${this.escape(JSON.stringify(mount.parameters || {}))}" placeholder="能力包参数 JSON" />
      </div>`).join('') : '<div class="empty-state"><strong>尚未挂载能力包</strong><span>当前仍可通过旧流程兼容适配器运行斯坦福小镇大脑。</span></div>';
      list.querySelectorAll('input,select').forEach(control => control.addEventListener('input', () => { this.capabilityDirty = true; this.renderCapabilityValidation(); }));
      list.querySelectorAll('[data-remove-brain-mount]').forEach(button => button.addEventListener('click', () => {
        const current = this.readCapabilityExtension();
        current.mounts.splice(Number(button.dataset.removeBrainMount), 1);
        this.capabilityExtensionRecord.extension = current; this.capabilityDirty = true; this.renderCapabilityExtension();
      }));
      const editable = this.revision?.state === 'DRAFT';
      document.querySelectorAll('.brain-capability-control').forEach(control => { control.disabled = !editable; });
      document.getElementById('addBrainCapabilityMount').disabled = !editable;
      this.renderCapabilityValidation();
    },

    addCapabilityMount() {
      if (this.revision?.state !== 'DRAFT') return;
      const bundle = this.capabilityBundles[0];
      if (!bundle) { this.renderCapabilityValidation(['请先在能力中心发布一个可挂载到 BRAIN 或 AGENT 的能力包。']); return; }
      this.capabilityExtensionRecord.extension = this.readCapabilityExtension();
      const extension = this.capabilityExtensionRecord.extension;
      const used = new Set(extension.mounts.map(item => item.mount_key));
      let index = extension.mounts.length + 1; let key = `brain-capability-${index}`;
      while (used.has(key)) key = `brain-capability-${++index}`;
      extension.mounts.push({ mount_key: key, category: 'CUSTOM', capability_bundle_revision_id: bundle.current_published.id, parameters: {}, enabled: true });
      this.capabilityDirty = true; this.renderCapabilityExtension();
    },

    readCapabilityExtension() {
      const mounts = [...document.querySelectorAll('[data-brain-mount-index]')].map(row => {
        let parameters;
        try { parameters = JSON.parse(row.querySelector('[data-brain-mount-field="parameters"]').value || '{}'); }
        catch (error) { throw new Error(`大脑能力包参数必须是 JSON：${error.message}`); }
        return {
          mount_key: row.querySelector('[data-brain-mount-field="mount_key"]').value.trim(),
          category: row.querySelector('[data-brain-mount-field="category"]').value,
          capability_bundle_revision_id: row.querySelector('[data-brain-mount-field="bundle"]').value,
          parameters,
          enabled: true,
        };
      });
      return {
        schema_version: 'ga-brain-extension/v1', mounts,
        default_reasoning_interval_ms: Number(document.getElementById('brainReasoningInterval').value),
        legacy_workflow_adapter_enabled: document.getElementById('brainLegacyAdapter').checked,
      };
    },

    localCapabilityErrors() {
      let extension;
      try { extension = this.readCapabilityExtension(); } catch (error) { return [error.message]; }
      const errors = [];
      if (!extension.legacy_workflow_adapter_enabled && !extension.mounts.length) errors.push('关闭旧流程适配器前，至少需要挂载一个能力包。');
      const keys = extension.mounts.map(item => item.mount_key);
      if (keys.some(key => !key)) errors.push('能力挂载键不能为空。');
      if (keys.length !== new Set(keys).size) errors.push('能力挂载键不能重复。');
      return errors;
    },

    renderCapabilityValidation(extraErrors = null) {
      const errors = extraErrors || this.localCapabilityErrors();
      document.getElementById('brainCapabilityValidation').innerHTML = errors.length
        ? errors.map(message => `<div class="capability-validation-item error"><strong>!</strong><span>${this.escape(message)}</span></div>`).join('')
        : '<div class="capability-validation-item"><strong>✓</strong><span>能力分类、挂载键和本地参数结构检查通过；发布时会校验能力包 Revision 与目标类型。</span></div>';
    },

    workflowElements() {
      return ['.workflow-workspace-tabs', '#workflowShell', '#workflowFunctionPage']
        .map(selector => document.querySelector(selector)).filter(Boolean);
    },

    mountWorkflowEditor(target) {
      this.workflowElements().forEach(element => target.appendChild(element));
    },

    async restoreExperimentEditor() {
      this.init();
      document.body.classList.remove('brain-editor-mode');
      const promptPage = document.getElementById('page-prompts');
      const source = document.getElementById('experimentBrainSource');
      this.workflowElements().forEach(element => promptPage.appendChild(element));
      if (source) promptPage.insertBefore(source, promptPage.firstChild);
      if (this.experiment?.revision) {
        await window.WorkflowEditor.setContext({
          ownerType: 'experiment',
          ownerId: this.experiment.experimentId,
          experimentId: this.experiment.experimentId,
          draft: this.experiment.editable ? this.experiment.revision : null,
          revision: this.experiment.revision,
          readonly: !this.experiment.editable,
        });
      }
    },

    async activate() {
      this.init();
      await this.loadBrains();
      const brainId = new URLSearchParams(location.search).get('brain_id');
      if (brainId) await this.openBrain(brainId, false);
    },

    async loadBrains() {
      this.init();
      const generation = ++this.listGeneration;
      const params = new URLSearchParams({ page: String(this.page), page_size: String(this.pageSize) });
      if (this.query) params.set('q', this.query);
      if (this.status) params.set('status', this.status);
      const [result, selector] = await Promise.all([
        this.request(`/brains?${params}`),
        this.request('/brains?page=1&page_size=100'),
      ]);
      if (generation !== this.listGeneration) return;
      this.brains = result.items;
      this.selectorBrains = selector.items;
      const grid = document.getElementById('brainCatalogGrid');
      grid.innerHTML = this.brains.length ? this.brains.map(item => `
        <button class="brain-card" data-brain-id="${item.id}">
          <span class="brain-card-top"><span class="map-state ${item.current_draft ? 'draft' : ''}">${item.current_draft ? '编辑中' : '已发布'}</span><span>${item.is_builtin ? '<b class="brain-builtin">系统基准</b>' : `<code>${this.escape(item.brain_key)}</code>`}</span></span>
          <h2>${this.escape(item.name)}</h2><p>${this.escape(item.description || '暂无用途说明')}</p>
          <span class="brain-card-foot"><span>${item.workflow_count} 个流程 · ${item.node_count} 个节点</span><span>${item.usage_count} 个实验使用</span></span>
        </button>`).join('') : '<div class="empty-state"><strong>没有符合条件的大脑</strong><span>可以基于斯坦福小镇基准模板创建一个新的大脑。</span></div>';
      grid.querySelectorAll('[data-brain-id]').forEach(card => card.addEventListener('click', () => this.openBrain(card.dataset.brainId).catch(error => this.fail(error))));
      const footer = document.getElementById('brainListFooter');
      footer.hidden = result.total === 0;
      if (result.total) {
        const first = (result.page - 1) * result.page_size + 1;
        const last = Math.min(result.total, first + result.items.length - 1);
        document.getElementById('brainCatalogCount').textContent = `显示 ${first}–${last}，共 ${result.total} 个大脑`;
      }
      this.renderPages(result.total_pages || 1);
      this.updateCounts(result.status_counts || {});
      this.populateSelectors();
    },

    renderPages(totalPages) {
      const pagination = document.getElementById('brainPagination');
      pagination.hidden = totalPages <= 1; pagination.dataset.totalPages = String(totalPages);
      document.getElementById('brainPages').innerHTML = Array.from({ length: totalPages }, (_, index) => {
        const page = index + 1;
        return `<button class="page-button${page === this.page ? ' active' : ''}" data-brain-page="${page}">${page}</button>`;
      }).join('');
      document.getElementById('brainPrev').disabled = this.page <= 1;
      document.getElementById('brainNext').disabled = this.page >= totalPages;
    },

    updateCounts(counts) {
      const labels = { all: '全部', draft: '编辑中', published: '已发布' };
      document.querySelectorAll('[data-brain-filter]').forEach(tab => {
        const key = tab.dataset.brainFilter;
        const count = key === 'all' ? counts.ALL : counts[key.toUpperCase()];
        tab.textContent = Number.isFinite(count) ? `${labels[key]} ${count}` : labels[key];
      });
    },

    populateSelectors() {
      const published = this.selectorBrains.filter(item => item.current_published);
      const source = document.getElementById('newBrainSource');
      const previous = source.value;
      source.innerHTML = published.map(item => `<option value="${item.current_published.id}">${this.escape(item.name)} · v${item.current_published.revision_no}${item.is_builtin ? ' · 系统基准' : ''}</option>`).join('');
      source.value = published.some(item => item.current_published.id === previous) ? previous : (published.find(item => item.is_builtin)?.current_published.id || published[0]?.current_published.id || '');
      const select = document.getElementById('experimentBrainSelect');
      const selected = this.experiment?.brainRevisionId || select.value;
      select.innerHTML = '<option value="">请选择已发布大脑</option>' + published.map(item => `<option value="${item.current_published.id}">${this.escape(item.name)} · v${item.current_published.revision_no}</option>`).join('');
      select.value = selected || '';
      const experimentCreateSelect = document.getElementById('newExperimentBrain');
      if (experimentCreateSelect) {
        const previousCreateValue = experimentCreateSelect.value;
        experimentCreateSelect.innerHTML = published.map(item => `<option value="${item.current_published.id}">${this.escape(item.name)} · v${item.current_published.revision_no}${item.is_builtin ? ' · 默认' : ''}</option>`).join('');
        experimentCreateSelect.value = published.some(item => item.current_published.id === previousCreateValue)
          ? previousCreateValue
          : (published.find(item => item.is_builtin)?.current_published.id || published[0]?.current_published.id || '');
      }
    },

    async openBrain(brainId, push = true) {
      this.detail = await this.request(`/brains/${brainId}`);
      this.selectedBrainId = brainId;
      this.revisions = (await this.request(`/brains/${brainId}/revisions`)).items;
      this.revision = this.detail.current_draft
        ? await this.request(`/brains/${brainId}/draft`)
        : await this.request(`/brains/${brainId}/revisions/${this.detail.current_published.id}`);
      const [capabilityExtension, bundleCatalog] = await Promise.all([
        this.revision.state === 'DRAFT'
          ? this.request(`/brains/${brainId}/draft/capability-extension`)
          : this.request(`/brains/${brainId}/revisions/${this.revision.id}/capability-extension`),
        this.request('/capability-bundles?page=1&page_size=100'),
      ]);
      this.capabilityExtensionRecord = capabilityExtension;
      this.capabilityBundles = bundleCatalog.items.filter(item => item.current_published && (item.targets || []).some(target => ['BRAIN', 'AGENT'].includes(target)));
      this.capabilityDirty = false;
      document.getElementById('brainCatalogShell').hidden = true;
      document.getElementById('brainEditorShell').hidden = false;
      document.body.classList.add('brain-editor-mode');
      document.getElementById('brainEditorTitle').textContent = this.detail.name;
      document.getElementById('brainEditorMeta').textContent = `${this.detail.brain_key} · ${this.revision.workflow_count} 个流程 · Revision ${String(this.revision.revision_no).padStart(3, '0')}`;
      const editable = this.revision.state === 'DRAFT';
      const state = document.getElementById('brainEditorState');
      state.textContent = editable ? '草稿' : this.detail.is_builtin ? '系统基准 · 只读' : '已发布';
      state.classList.toggle('draft', editable);
      document.getElementById('saveBrainBtn').disabled = !editable;
      document.getElementById('publishBrainBtn').textContent = editable ? '发布版本' : this.detail.is_builtin ? '基于此模板创作' : '创建新修订';
      this.renderCapabilityExtension();
      this.selectEditorTab('capabilities');
      this.mountWorkflowEditor(document.getElementById('brainWorkflowMount'));
      await window.WorkflowEditor.setContext({
        ownerType: 'brain', ownerId: brainId,
        draft: editable ? this.revision : null,
        revision: this.revision, readonly: !editable,
      });
      await window.WorkflowEditor.activate();
      if (push) this.replaceUrl(brainId);
    },

    async showCatalog() {
      this.selectedBrainId = null;
      document.body.classList.remove('brain-editor-mode');
      document.getElementById('brainCatalogShell').hidden = false;
      document.getElementById('brainEditorShell').hidden = true;
      await this.restoreExperimentEditor();
      this.replaceUrl(null);
    },

    replaceUrl(brainId) {
      const url = new URL(location.href); url.search = ''; url.searchParams.set('view', 'brains');
      if (brainId) url.searchParams.set('brain_id', brainId);
      history.replaceState(null, '', `${url.pathname}${url.search}`);
      window.dispatchEvent(new CustomEvent('brain-workspace:selection', { detail: { brainId } }));
    },

    openCreate(sourceRevisionId = null) {
      this.populateSelectors();
      document.getElementById('newBrainName').value = '';
      document.getElementById('newBrainDescription').value = '';
      if (sourceRevisionId) document.getElementById('newBrainSource').value = sourceRevisionId;
      this.modal('open', 'createBrainModal', 'newBrainName');
    },

    async prepareExperimentCreate() {
      this.init();
      if (!this.selectorBrains.length) await this.loadBrains();
      this.populateSelectors();
      const baseline = this.selectorBrains.find(item => item.is_builtin)?.current_published?.id;
      if (baseline) document.getElementById('newExperimentBrain').value = baseline;
    },

    async create() {
      const name = document.getElementById('newBrainName').value.trim();
      if (!name) return document.getElementById('newBrainName').focus();
      const created = await this.request('/brains', {
        method: 'POST', body: JSON.stringify({
          name,
          description: document.getElementById('newBrainDescription').value.trim(),
          source_revision_id: document.getElementById('newBrainSource').value || null,
        }),
      });
      this.modal('close', 'createBrainModal');
      await this.loadBrains(); await this.openBrain(created.id);
      this.notify('已从基准版本创建独立大脑草稿。', '大脑已创建');
    },

    async save() {
      if (!this.revision || this.revision.state !== 'DRAFT') return;
      const errors = this.localCapabilityErrors();
      if (errors.length) { this.renderCapabilityValidation(errors); this.selectEditorTab('capabilities'); throw new Error(errors[0]); }
      await window.WorkflowEditor.save();
      this.revision = await this.request(`/brains/${this.selectedBrainId}/draft`);
      this.capabilityExtensionRecord = await this.request(`/brains/${this.selectedBrainId}/draft/capability-extension`, {
        method: 'PUT', body: JSON.stringify({ lock_version: this.revision.lock_version, extension: this.readCapabilityExtension() }),
      });
      this.revision = await this.request(`/brains/${this.selectedBrainId}/draft`);
      this.capabilityDirty = false;
      this.notify('能力装配、5 个兼容流程与 Prompt 已保存到当前大脑草稿。', '大脑已保存');
    },

    async publishOrFork() {
      if (this.revision.state === 'PUBLISHED') {
        if (this.detail.is_builtin) {
          this.openCreate(this.revision.id);
          return;
        }
        await this.request(`/brains/${this.selectedBrainId}/revisions/${this.revision.id}/fork`, { method: 'POST' });
        await this.openBrain(this.selectedBrainId, false);
        this.notify('已从已发布版本创建新的大脑草稿。', '修订已创建');
        return;
      }
      await this.save();
      const published = await this.request(`/brains/${this.selectedBrainId}/draft/publish`, {
        method: 'POST', body: JSON.stringify({ draft_revision_id: this.revision.id, lock_version: this.revision.lock_version }),
      });
      await this.loadBrains(); await this.openBrain(this.selectedBrainId, false);
      this.notify(`Revision ${String(published.revision_no).padStart(3, '0')} 已锁定，可供所有实验使用。`, '大脑已发布');
    },

    async setExperimentContext(context) {
      this.init(); this.experiment = context;
      if (!this.selectorBrains.length) await this.loadBrains();
      const provenance = context.revision?.provenance || {};
      this.experiment.brainRevisionId = provenance.brain_revision_id || null;
      this.populateSelectors();
      const brain = this.selectorBrains.find(item => item.id === provenance.brain_id);
      document.getElementById('experimentBrainSourceMeta').textContent = brain
        ? `${brain.name} · Revision ${brain.current_published?.revision_no || '—'} · 已复制到当前实验草稿`
        : '当前实验使用自身大脑编排';
      document.getElementById('applyExperimentBrainBtn').disabled = !context.editable;
      document.getElementById('saveExperimentBrainTemplateBtn').disabled = !context.revision;
    },

    async applyToExperiment() {
      if (!this.experiment?.editable) throw new Error('当前实验不可修改大脑编排');
      const revisionId = document.getElementById('experimentBrainSelect').value;
      if (!revisionId) throw new Error('请先选择一个已发布大脑');
      const draft = await this.request(`/experiments/${this.experiment.experimentId}/draft/brain`, {
        method: 'PUT', body: JSON.stringify({ lock_version: this.experiment.lockVersion, brain_revision_id: revisionId }),
      });
      this.experiment.lockVersion = draft.lock_version;
      this.experiment.revision = draft;
      this.experiment.editable = true;
      await this.setExperimentContext(this.experiment);
      await window.WorkflowEditor.setContext({ ownerType: 'experiment', ownerId: this.experiment.experimentId, experimentId: this.experiment.experimentId, draft, revision: draft, readonly: false });
      window.dispatchEvent(new CustomEvent('brain-workspace:experiment-draft', { detail: { experimentId: this.experiment.experimentId, draft } }));
      this.notify('已将大脑的 5 个流程与 Prompt 复制到当前实验草稿。', '大脑已应用');
    },

    openSaveExperimentTemplate() {
      if (!this.experiment?.revision) return;
      document.getElementById('savedBrainTemplateName').value = `${this.experiment.name || '当前实验'} · 大脑`;
      document.getElementById('savedBrainTemplateDescription').value = '';
      this.modal('open', 'saveBrainTemplateModal', 'savedBrainTemplateName');
    },

    async saveExperimentTemplate() {
      if (!this.experiment?.revision) throw new Error('当前实验没有可保存的大脑编排');
      const name = document.getElementById('savedBrainTemplateName').value.trim();
      if (!name) return document.getElementById('savedBrainTemplateName').focus();
      const created = await this.request(`/experiments/${this.experiment.experimentId}/brain-template`, {
        method: 'POST',
        body: JSON.stringify({
          name,
          description: document.getElementById('savedBrainTemplateDescription').value.trim(),
          revision_id: this.experiment.revision.id,
        }),
      });
      this.modal('close', 'saveBrainTemplateModal');
      await this.loadBrains();
      this.notify(`“${created.name}”已保存为独立草稿，可在大脑页面继续编辑和发布。`, '大脑模板已保存');
    },
  };

  window.BrainWorkspace = {
    activate: () => manager.activate(),
    restoreExperimentEditor: () => manager.restoreExperimentEditor(),
    setExperimentContext: context => manager.setExperimentContext(context),
    prepareExperimentCreate: () => manager.prepareExperimentCreate(),
    refresh: () => manager.loadBrains(),
  };
})();
