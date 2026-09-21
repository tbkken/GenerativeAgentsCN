/**
 * 人群与智能体模板工作区。
 * manager 集中管理目录分页、成员选择和公共资源；所有写操作都先更新服务端，
 * 再以响应重绘页面，避免浏览器局部状态冒充已保存数据。
 */
(function () {
  'use strict';

  const API = window.ResourceScope?.base || '/api/studio/resources';
  const experimentScope = Boolean(window.ResourceScope?.experimentId);
  const byId = id => document.getElementById(id);
  const splitComma = value => String(value || '').split(/[，,]/).map(item => item.trim()).filter(Boolean);
  const splitLines = value => String(value || '').split(/\r?\n/).map(item => item.trim()).filter(Boolean);

  const manager = {
    initialized: false,
    page: 1,
    pageSize: 5,
    status: '',
    query: '',
    crowds: [],
    selectorCrowds: [],
    selectorDetails: new Map(),
    createSelection: new Set(),
    detail: null,
    revision: null,
    revisions: [],
    agents: [],
    agentRevisionDetails: new Map(),
    agentRevisionOwners: new Map(),
    memberSelection: new Set(),
    agentDraft: null,
    agentDetail: null,
    searchTimer: null,
    listGeneration: 0,

    async request(path, options = {}) {
      options = window.ResourceScope?.options(options) || options;
      const response = await fetch(`${API}${path}`, {
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        ...options,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        const detail = body?.detail;
        const error = new Error(detail?.message || (typeof detail === 'string' ? detail : '') || body?.error?.message || `请求失败（${response.status}）`);
        error.code = detail?.code || body?.error?.code;
        error.details = detail?.details || body?.error?.details;
        throw error;
      }
      const result = response.status === 204 ? null : await response.json();
      if (experimentScope && options.method && options.method !== 'GET') window.ResourceScope.saved(result);
      return result;
    },

    escape(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
    },

    notify(message, title = '操作成功') {
      window.dispatchEvent(new CustomEvent('crowd-workspace:toast', { detail: { message, title } }));
    },

    fail(error) {
      console.error(error);
      window.dispatchEvent(new CustomEvent('crowd-workspace:error', { detail: { error } }));
    },

    modal(action, id, focusId = null) {
      window.dispatchEvent(new CustomEvent('crowd-workspace:modal', { detail: { action, id, focusId } }));
    },

    init() {
      if (this.initialized) return;
      this.initialized = true;
      byId('createAgentResourceBtn').addEventListener('click', () => this.openAgentEditor().catch(error => this.fail(error)));
      byId('publicAgentSearch').addEventListener('input', () => {
        window.ResourceList.remember('public-agents', {query: byId('publicAgentSearch').value.trim(), page: 1, scroll: 0});
        this.renderAgentList();
      });
      byId('createCrowdBtn').addEventListener('click', () => this.openCreate());
      byId('backToCrowdsBtn').addEventListener('click', () => this.showCatalog());
      byId('saveCrowdBtn').addEventListener('click', () => this.saveCrowd().catch(error => this.fail(error)));
      byId('publishCrowdBtn').addEventListener('click', () => this.publishOrFork().catch(error => this.fail(error)));
      byId('deleteCrowdBtn').addEventListener('click', () => this.deleteCrowd(this.detail?.id, this.detail?.name).catch(error => this.fail(error)));
      [byId('manageCrowdAgentsBtn'), byId('manageCrowdAgentsInlineBtn')].forEach(button => button.addEventListener('click', () => this.openAgentManager().catch(error => this.fail(error))));
      byId('confirmCreateCrowd').addEventListener('click', () => this.createCrowd().catch(error => this.fail(error)));
      ['closeCreateCrowd', 'cancelCreateCrowd'].forEach(id => byId(id).addEventListener('click', () => this.modal('close', 'createCrowdModal')));
      ['closeCrowdAgentManager', 'cancelCrowdAgentManager'].forEach(id => byId(id).addEventListener('click', () => this.modal('close', 'crowdAgentManagerModal')));
      byId('confirmCrowdAgentManager').addEventListener('click', () => this.applyAgentSelection().catch(error => this.fail(error)));
      byId('createPublicAgentBtn').addEventListener('click', () => this.openAgentEditor().catch(error => this.fail(error)));
      byId('crowdAgentSearch').addEventListener('input', () => this.renderAgentList());
      byId('crowdSearch').addEventListener('input', event => {
        clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => {
          this.query = event.target.value.trim();
          this.page = 1;
          this.loadCrowds().catch(error => this.fail(error));
        }, 250);
      });
      document.querySelectorAll('[data-crowd-filter]').forEach(tab => tab.addEventListener('click', () => {
        document.querySelectorAll('[data-crowd-filter]').forEach(item => item.classList.toggle('active', item === tab));
        this.status = tab.dataset.crowdFilter === 'all' ? '' : tab.dataset.crowdFilter.toUpperCase();
        this.page = 1;
        this.loadCrowds().catch(error => this.fail(error));
      }));
      byId('crowdPagination').addEventListener('click', event => {
        const pageButton = event.target.closest('[data-crowd-page]');
        const totalPages = Number(byId('crowdPagination').dataset.totalPages || 1);
        if (pageButton) this.page = Number(pageButton.dataset.crowdPage);
        else if (event.target.id === 'crowdPrev') this.page = Math.max(1, this.page - 1);
        else if (event.target.id === 'crowdNext') this.page = Math.min(totalPages, this.page + 1);
        else return;
        this.loadCrowds().catch(error => this.fail(error));
      });
    },

    async activate() {
      this.agentCatalogActive = false;
      this.init();
      if (!experimentScope) {
        const saved = window.ResourceList.read('crowds');
        this.page = saved.page;
        this.query = saved.query;
        byId('crowdSearch').value = this.query;
      }
      await this.loadCrowds();
      byId('createCrowdBtn').hidden = experimentScope && !window.ResourceScope.editable;
      const crowdId = new URLSearchParams(location.search).get('crowd_id');
      if (crowdId) await this.openCrowd(crowdId, false);
    },

    async activateAgents() {
      this.init();
      this.agentCatalogActive = true;
      const generation = this.agentListGeneration = (this.agentListGeneration || 0) + 1;
      byId('publicAgentSearch').value = window.ResourceList.read('public-agents').query;
      window.ResourceList.loading(byId('publicAgentList'), '智能体');
      try {
        const result = await this.request('/agents');
        if (generation !== this.agentListGeneration || !this.agentCatalogActive) return;
        this.agents = result.items || [];
        this.agents.forEach(item => this.agentRevisionDetails.set(item.id, item));
        this.renderAgentList();
        window.ResourceList.restore('public-agents');
      } catch (error) {
        if (generation !== this.agentListGeneration) return;
        window.ResourceList.error(byId('publicAgentList'), '智能体', error, () => this.activateAgents());
        byId('publicAgentFooter').hidden = true;
      } finally { if (generation === this.agentListGeneration) byId('publicAgentList').removeAttribute('aria-busy'); }
    },

    async loadCrowds() {
      this.init();
      if (!experimentScope) return this.loadAuthorCrowds();
      const generation = ++this.listGeneration;
      const grid = byId('crowdCatalogGrid');
      grid.setAttribute('aria-busy', 'true');
      const params = new URLSearchParams({ page: String(this.page), page_size: String(this.pageSize) });
      if (this.query) params.set('q', this.query);
      if (this.status) params.set('status', this.status);
      let result;
      let selector;
      try {
        selector = await this.request('/crowds');
        const all = (selector.items || []).map(item => ({
          ...item,
          agent_count: (item.agent_ids || []).length,
          usage_count: 0,
        }));
        const filtered = all.filter(item => !this.query || `${item.name} ${item.crowd_key} ${item.description || ''}`.toLocaleLowerCase().includes(this.query.toLocaleLowerCase()));
        const start = (this.page - 1) * this.pageSize;
        result = {
          items: filtered.slice(start, start + this.pageSize),
          page: this.page,
          page_size: this.pageSize,
          total: filtered.length,
          total_pages: Math.max(1, Math.ceil(filtered.length / this.pageSize)),
          status_counts: { ALL: all.length, DRAFT: all.length, PUBLISHED: 0 },
        };
      } catch (error) {
        if (generation !== this.listGeneration) return;
        grid.innerHTML = `<div class="empty-state resource-load-error" role="alert"><strong>人群列表加载失败</strong><span>${this.escape(error.message || '请稍后重试')}</span><button class="btn btn-sm" type="button" data-retry-crowd-list>重新加载</button></div>`;
        grid.querySelector('[data-retry-crowd-list]')?.addEventListener('click', () => this.loadCrowds().catch(nextError => this.fail(nextError)));
        byId('crowdListFooter').hidden = true;
        throw error;
      } finally {
        if (generation === this.listGeneration) grid.removeAttribute('aria-busy');
      }
      if (generation !== this.listGeneration) return;
      this.crowds = result.items;
      this.selectorCrowds = (selector.items || []).map(item => ({
        ...item,
        agent_count: (item.agent_ids || []).length,
        usage_count: 0,
      }));
      this.renderCatalog(result);
      this.populateCreateSelector();
    },

    async loadAuthorCrowds() {
      const list = window.ResourceList;
      const generation = ++this.listGeneration;
      const grid = byId('crowdCatalogGrid');
      grid.classList.add('resource-rows');
      list.loading(grid, '人群');
      list.remember('crowds', {query: this.query, page: this.page});
      try {
        const result = await this.request('/crowds');
        if (generation !== this.listGeneration) return;
        this.selectorCrowds = result.items || [];
        const filtered = list.sorted(this.selectorCrowds).filter(item => !this.query || `${item.name} ${item.crowd_key} ${item.description || ''}`.toLocaleLowerCase().includes(this.query.toLocaleLowerCase()));
        const data = list.slice(filtered, this.page);
        this.page = data.page;
        this.crowds = data.items;
        grid.innerHTML = data.items.map(item => list.row({name: item.name, description: item.description, icon: '◉',
          meta: [`${(item.agent_ids || []).length} 个智能体`], open: {'data-crowd-id': item.id},
          actions: [{label: '删除人群', danger: true, attributes: {'data-delete-crowd-id': item.id, 'data-delete-crowd-name': item.name}}],
        })).join('') || list.empty('人群', Boolean(this.query));
        grid.querySelectorAll('[data-crowd-id]').forEach(button => button.onclick = () => this.openCrowd(button.dataset.crowdId).catch(error => this.fail(error)));
        grid.querySelectorAll('[data-delete-crowd-id]').forEach(button => button.onclick = () => this.deleteCrowd(button.dataset.deleteCrowdId, button.dataset.deleteCrowdName).catch(error => this.fail(error)));
        list.pager(byId('crowdListFooter'), {...data, onPage: page => {this.page = page; list.remember('crowds', {page, scroll: 0}); this.loadCrowds();}});
        list.remember('crowds', {page: this.page});
        if (!new URLSearchParams(location.search).has('crowd_id')) list.restore('crowds');
      } catch (error) {
        if (generation !== this.listGeneration) return;
        list.error(grid, '人群', error, () => this.loadCrowds());
        byId('crowdListFooter').hidden = true;
      } finally { if (generation === this.listGeneration) grid.removeAttribute('aria-busy'); }
    },

    renderCatalog(result) {
      const grid = byId('crowdCatalogGrid');
      grid.innerHTML = this.crowds.length ? this.crowds.map(item => `
        <article class="resource-card-shell"><button class="crowd-card" data-crowd-id="${item.id}">
          <span class="crowd-card-top"><span class="map-state draft">${experimentScope ? '实验副本' : '基础配置'}</span><code>${this.escape(item.crowd_key)}</code></span>
          <h2>${this.escape(item.name)}</h2><p>${this.escape(item.description || '暂无用途说明')}</p>
          <span class="crowd-card-foot"><span><strong>${item.agent_count}</strong> 个 Agent</span><span>${experimentScope ? '仅当前实验' : `${item.usage_count} 个实验使用`}</span></span>
        </button><button class="resource-card-delete" type="button" aria-label="删除人群" title="删除人群" data-delete-crowd-id="${item.id}" data-delete-crowd-name="${this.escape(item.name)}">删除</button></article>`).join('') : `<div class="empty-state"><strong>没有符合条件的人群</strong><span>${experimentScope ? '新建人群后，从当前实验的智能体列表添加成员。' : '新建人群后，可从基础智能体列表添加成员。'}</span></div>`;
      grid.querySelectorAll('[data-crowd-id]').forEach(card => card.addEventListener('click', () => this.openCrowd(card.dataset.crowdId).catch(error => this.fail(error))));
      grid.querySelectorAll('[data-delete-crowd-id]').forEach(button => button.addEventListener('click', () => this.deleteCrowd(button.dataset.deleteCrowdId, button.dataset.deleteCrowdName).catch(error => this.fail(error))));
      const footer = byId('crowdListFooter');
      footer.hidden = result.total === 0;
      if (result.total) {
        const first = (result.page - 1) * result.page_size + 1;
        const last = Math.min(result.total, first + result.items.length - 1);
        byId('crowdCatalogCount').textContent = `显示 ${first}–${last}，共 ${result.total} 个人群`;
      }
      const totalPages = result.total_pages || 1;
      const pagination = byId('crowdPagination');
      pagination.dataset.totalPages = String(totalPages);
      byId('crowdPages').innerHTML = Array.from({ length: totalPages }, (_, index) => {
        const page = index + 1;
        return `<button class="page-button${page === this.page ? ' active' : ''}" data-crowd-page="${page}">${page}</button>`;
      }).join('');
      byId('crowdPrev').disabled = this.page <= 1;
      byId('crowdNext').disabled = this.page >= totalPages;
      const labels = { all: '全部', draft: '编辑中', published: '已发布' };
      document.querySelectorAll('[data-crowd-filter]').forEach(tab => {
        const key = tab.dataset.crowdFilter;
        const count = key === 'all' ? result.status_counts?.ALL : result.status_counts?.[key.toUpperCase()];
        tab.textContent = Number.isFinite(count) ? `${labels[key]} ${count}` : labels[key];
      });
    },

    async deleteCrowd(crowdId, name = '当前人群') {
      if (!crowdId) return;
      const confirmed = await window.confirmResourceDeletion({ type: '人群', name, message: experimentScope ? '从当前实验删除此人群分组，保留实验智能体；基础配置不受影响。' : '删除基础人群不会影响已有实验。' });
      if (!confirmed) return;
      await this.request(`/crowds/${encodeURIComponent(crowdId)}`, { method: 'DELETE' });
      if (this.detail?.id === crowdId) this.showCatalog();
      await this.loadCrowds();
      this.notify(`人群“${name}”已删除。`, '删除完成');
    },

    async openCrowd(crowdId, push = true) {
      if (!experimentScope && push) window.ResourceList.capture('crowds');
      const generation = this.openGeneration = (this.openGeneration || 0) + 1;
      const detail = await this.request(`/crowds/${crowdId}`);
      if (generation !== this.openGeneration) return;
      this.detail = detail;
      await this.loadAgents();
      if (generation !== this.openGeneration) return;
      const members = (this.detail.agent_ids || []).map(agentId => {
        const agent = this.agents.find(item => item.id === agentId);
        return agent ? { agent_id: agent.id, name: agent.name, agent_key: agent.agent_key, definition: agent.definition } : null;
      }).filter(Boolean);
      this.revision = {
        ...this.detail,
        state: 'DRAFT',
        lock_version: this.detail.row_version,
        agent_count: members.length,
        members,
      };
      byId('crowdCatalogShell').hidden = true;
      byId('crowdEditorShell').hidden = false;
      byId('crowdEditorTitle').textContent = this.detail.name;
      byId('crowdEditorMeta').textContent = `${this.detail.crowd_key} · ${this.revision.agent_count} 个 Agent · 实时可编辑`;
      byId('crowdDefinitionHelp').textContent = experimentScope ? '人群组织当前实验的智能体，修改仅影响本实验。' : '人群组织基础智能体，创建实验时复制完整定义。';
      byId('crowdEditName').value = this.revision.name;
      byId('crowdEditDescription').value = this.revision.description || '';
      byId('crowdEditKey').value = this.revision.crowd_key;
      const editable = this.detail.editable !== false;
      byId('crowdEditorMeta').textContent = `${this.detail.crowd_key} · ${members.length} 个智能体 · ${editable ? '可编辑' : '已封存，只读'}`;
      byId('crowdEditName').disabled = !editable;
      byId('crowdEditDescription').disabled = !editable;
      byId('saveCrowdBtn').disabled = !editable;
      byId('confirmCrowdAgentManager').disabled = !editable;
      const state = byId('crowdEditorState');
      state.textContent = experimentScope ? (editable ? '实验人群' : '已封存 · 只读') : '实时资源';
      state.classList.toggle('draft', editable);
      byId('publishCrowdBtn').textContent = '保存人群';
      byId('deleteCrowdBtn').hidden = !editable;
      byId('publishCrowdBtn').hidden = experimentScope;
      byId('manageCrowdAgentsBtn').disabled = !editable;
      byId('manageCrowdAgentsInlineBtn').disabled = !editable;
      this.renderMembers();
      window.dispatchEvent(new CustomEvent('crowd-workspace:selection', { detail: { crowdId } }));
      if (push) history.pushState({}, '', window.ResourceScope.pageUrl('crowds', { crowd_id: crowdId }));
      if (!experimentScope && push) window.scrollTo({top: 0, behavior: 'instant'});
    },

    showCatalog() {
      this.openGeneration = (this.openGeneration || 0) + 1;
      this.detail = null;
      this.revision = null;
      byId('crowdEditorShell').hidden = true;
      byId('crowdCatalogShell').hidden = false;
      window.dispatchEvent(new CustomEvent('crowd-workspace:selection', { detail: { crowdId: null } }));
      history.pushState({}, '', window.ResourceScope.pageUrl('crowds'));
      if (!experimentScope) this.loadCrowds().then(() => window.ResourceList.restore('crowds')).catch(error => this.fail(error));
    },

    renderMembers() {
      const members = this.revision?.members || [];
      byId('crowdMemberMeta').textContent = `${members.length} 个 Agent · ${experimentScope ? '当前实验的成员分组' : '实验导入时复制完整定义'}`;
      byId('crowdMemberGrid').innerHTML = members.length ? members.map(member => `
        <button type="button" class="crowd-member-card" data-view-crowd-agent="${this.escape(member.agent_id)}" aria-label="查看 ${this.escape(member.name)} 的 Agent 信息"><span class="crowd-member-avatar">${this.escape(member.name.slice(0, 1))}</span><span class="crowd-member-identity"><strong>${this.escape(member.name)}</strong><code>${this.escape(member.agent_key)}</code></span><span class="crowd-member-version">${experimentScope ? '实验智能体' : '基础智能体'}</span></button>
      `).join('') : '<div class="empty-state"><strong>尚未添加 Agent</strong><span>点击“Agent 管理”选择 Agent。</span></div>';
      byId('crowdMemberGrid').querySelectorAll('[data-view-crowd-agent]').forEach(button => button.addEventListener('click', () => {
        this.openAgentViewer(button.dataset.viewCrowdAgent).catch(error => this.fail(error));
      }));
    },

    openCreate() {
      byId('newCrowdName').value = '';
      byId('newCrowdDescription').value = '';
      this.modal('open', 'createCrowdModal', 'newCrowdName');
    },

    async createCrowd() {
      const name = byId('newCrowdName').value.trim();
      if (!name) throw new Error('请填写人群名称');
      const created = await this.request('/crowds', {
        method: 'POST',
        body: JSON.stringify({ name, description: byId('newCrowdDescription').value.trim(), agent_ids: [] }),
      });
      this.modal('close', 'createCrowdModal');
      await this.loadCrowds();
      await this.openCrowd(created.id);
      this.notify(`已创建人群“${name}”，请选择${experimentScope ? '实验' : '基础'}智能体。`);
      await this.openAgentManager();
    },

    async saveCrowd(options = {}) {
      if (!this.detail) return this.revision;
      const result = await this.request(`/crowds/${this.detail.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          row_version: this.detail.row_version,
          name: byId('crowdEditName').value.trim(),
          description: byId('crowdEditDescription').value.trim(),
          agent_ids: (this.revision.members || []).map(item => item.agent_id),
        }),
      });
      this.detail = result;
      this.detail.name = result.name;
      if (!options.silent) this.notify(experimentScope ? '当前实验的人群已保存' : '基础人群已保存');
      await this.openCrowd(this.detail.id, false);
      return this.revision;
    },

    async publishOrFork() {
      if (!this.detail || !this.revision) return;
      await this.saveCrowd({ silent: true });
      this.notify('公共人群已保存；导入实验时会复制当前内容');
      await this.loadCrowds();
      await this.openCrowd(this.detail.id, false);
      await this.prepareExperimentCreate();
    },

    async loadAgents() {
      const result = await this.request('/agents');
      this.agents = result.items || [];
      if (experimentScope) this.agentRevisionDetails.clear();
      this.agents.forEach(item => this.agentRevisionOwners.set(item.id, item.id));
    },

    async loadAgentRevisionDetails() {
      const targets = new Map(this.agents.map(item => [item.id, item.id]));
      (this.revision?.members || []).forEach(member => {
        targets.set(member.agent_id, member.agent_id);
        this.agentRevisionOwners.set(member.agent_id, member.agent_id);
      });
      await Promise.all([...targets].map(async ([resourceId, agentId]) => {
        if (this.agentRevisionDetails.has(resourceId)) return;
        try {
          const detail = await this.request(`/agents/${agentId}`);
          // A save may have supplied the current definition while this read was pending.
          if (this.agentRevisionDetails.has(resourceId)) return;
          this.agentRevisionDetails.set(resourceId, detail);
          this.agentRevisionOwners.set(resourceId, agentId);
        } catch (error) {
          if (this.agentRevisionDetails.has(resourceId)) return;
          console.error(error);
          this.agentRevisionDetails.set(resourceId, { loadError: error.message || '完整定义读取失败' });
        }
      }));
    },

    async openAgentManager() {
      if (!this.revision || this.detail?.editable === false) return;
      this.agentCatalogActive = false;
      byId('createPublicAgentBtn').hidden = experimentScope;
      await this.loadAgents();
      this.memberSelection = new Set((this.revision.members || []).map(item => item.agent_id));
      (this.revision.members || []).forEach(member => this.agentRevisionOwners.set(member.agent_id, member.agent_id));
      byId('crowdAgentSearch').value = '';
      byId('confirmCrowdAgentManager').disabled = false;
      byId('confirmCrowdAgentManager').textContent = '应用到人群';
      this.renderAgentList();
      this.modal('open', 'crowdAgentManagerModal', 'crowdAgentSearch');
      await this.loadAgentRevisionDetails();
      this.renderAgentList();
    },

    selectedRevisionIdForAgent(agentId) {
      return [...this.memberSelection].find(revisionId => this.agentRevisionOwners.get(revisionId) === agentId) || null;
    },

    clearAgentSelection(agentId) {
      [...this.memberSelection].forEach(revisionId => {
        if (this.agentRevisionOwners.get(revisionId) === agentId) this.memberSelection.delete(revisionId);
      });
    },

    agentCardMarkup(item) {
      const latestRevision = { id: item.id, revision_no: item.row_version };
      const selectedRevisionId = this.selectedRevisionIdForAgent(item.id);
      const revision = latestRevision;
      const detail = this.agentRevisionDetails.get(revision.id);
      const checked = Boolean(selectedRevisionId);
      const outdated = false;
      const editableCrowd = true;
      const scopeLabel = experimentScope ? '实验智能体' : '基础智能体';
      const edit = `<button type="button" data-edit-public-agent="${this.escape(item.id)}">编辑</button>`;
      const remove = experimentScope ? '' : `<button type="button" class="crowd-agent-delete" data-delete-public-agent="${this.escape(item.id)}" data-delete-public-agent-name="${this.escape(item.name)}">删除 Agent</button>`;
      const selectionLabel = checked ? '已加入当前人群' : '加入当前人群';
      const versionState = `<span class="crowd-agent-version-current">${experimentScope ? '当前实验定义' : '基础定义'}</span>`;
      const selectionControl = `<label class="crowd-agent-select"><input type="checkbox" data-agent-id="${item.id}" data-latest-revision-id="${latestRevision.id}" ${checked ? 'checked' : ''} ${editableCrowd ? '' : 'disabled'} /><strong data-agent-selection-label>${selectionLabel}</strong></label>`;
      const definition = detail?.definition;
      if (!definition) {
        const error = detail?.loadError;
        return `<article class="crowd-agent-card${checked ? ' selected' : ''}" data-agent-scope="public">
          <div class="crowd-agent-card-head"><div><small>${experimentScope ? 'EXPERIMENT AGENT' : 'PUBLIC AGENT'}</small><h3>${this.escape(item.name)}<code>${this.escape(item.agent_key)}</code></h3><div class="crowd-agent-version-state">${versionState}</div></div><div class="crowd-agent-card-actions"><span class="crowd-agent-scope">${scopeLabel}</span>${edit}${remove}${selectionControl}</div></div>
          <p>${this.escape(item.description || '暂无用途说明')}</p>
          <div class="crowd-agent-definition-state ${error ? 'error' : ''}">${error ? this.escape(error) : '正在读取完整 Agent 定义…'}</div>
        </article>`;
      }
      const scratch = definition.scratch || {};
      const publicAssetUrl = assetId => assetId
        ? `${API}/assets/${encodeURIComponent(assetId)}/content`
        : '';
      const portrait = publicAssetUrl(definition.portrait_asset_id)
        || (experimentScope ? window.ResourceScope.assetUrl(definition.portrait_asset) : definition.portrait_asset);
      const sprite = publicAssetUrl(definition.sprite_asset_id)
        || (experimentScope ? window.ResourceScope.assetUrl(definition.sprite_asset) : definition.sprite_asset);
      const imageCard = (url, title, note, spriteSheet = false) => `<article class="agent-image-card crowd-agent-readonly-image-card">
        <div class="agent-image-preview ${spriteSheet ? 'sprite' : 'portrait'}">${url
          ? `<img src="${this.escape(url)}" alt="${this.escape(definition.name)}${title}" onerror="this.hidden=true;this.nextElementSibling.hidden=false" /><span hidden>暂无${title}</span>`
          : `<span>暂无${title}</span>`}</div>
        <div class="agent-image-copy"><strong>${title}</strong><span>${note}</span><small>随当前${experimentScope ? '实验智能体' : '基础智能体'}保存</small></div>
      </article>`;
      const field = (label, value, wide = false) => `<div class="field crowd-agent-readonly-field${wide ? ' wide' : ''}"><label>${label}</label><div class="control crowd-agent-readonly-value">${this.escape(value ?? '') || '未填写'}</div></div>`;
      return `<article class="crowd-agent-card${checked ? ' selected' : ''}" data-agent-scope="public">
        <div class="crowd-agent-card-head">
          <div class="crowd-agent-identity"><div><small>${experimentScope ? 'EXPERIMENT AGENT' : 'PUBLIC AGENT'}</small><h3>${this.escape(definition.name)}<code>${this.escape(definition.agent_key)}</code></h3><div class="crowd-agent-version-state">${versionState}</div></div></div>
          <div class="crowd-agent-card-actions"><span class="crowd-agent-scope">${scopeLabel}</span>${edit}${remove}${selectionControl}</div>
        </div>
        <div class="content-workspace crowd-agent-readonly-workspace" data-agent-card-definition="${revision.id}">
          <nav class="content-tabs crowd-agent-content-tabs" role="tablist" aria-label="${this.escape(definition.name)}定义内容">
            <button class="content-tab active" type="button" role="tab" aria-selected="true" data-agent-card-tab="identity">身份</button>
            <button class="content-tab" type="button" role="tab" aria-selected="false" data-agent-card-tab="traits">特质与计划</button>
          </nav>
          <div class="content-tab-panel active crowd-agent-readonly-panel" role="tabpanel" data-agent-card-panel="identity">
            <div class="form-grid">
              ${field('显示名称', definition.name)}${field('年龄', scratch.age == null ? '' : `${scratch.age} 岁`)}
              <div class="agent-image-editor crowd-agent-readonly-images">${imageCard(portrait, '头像', '正方形 PNG；用于列表、结果与对话展示。')}${imageCard(sprite, `${definition.sprite_layout || '4x4'} 行走图`, '96×128（4×3）或 128×128（4×4）；四行依次为下、左、右、上。', true)}</div>
              ${field('当前目标', definition.currently, true)}
            </div>
          </div>
          <div class="content-tab-panel crowd-agent-readonly-panel" role="tabpanel" data-agent-card-panel="traits">
            <div class="form-grid">${field('天生特质', scratch.innate)}${field('背景经历', scratch.learned)}${field('生活方式', scratch.lifestyle)}${field('日常计划', scratch.daily_plan)}</div>
          </div>
        </div>
      </article>`;
    },

    renderAgentList() {
      if (this.agentCatalogActive && !experimentScope) return this.renderAuthorAgents();
      const list = byId(this.agentCatalogActive ? 'publicAgentList' : 'crowdAgentList');
      const query = byId(this.agentCatalogActive ? 'publicAgentSearch' : 'crowdAgentSearch').value.trim().toLocaleLowerCase();
      const visible = this.agents.filter(item => {
        if (!query) return true;
        const selectedRevisionId = this.selectedRevisionIdForAgent(item.id);
        const definition = this.agentRevisionDetails.get(selectedRevisionId || item.id)?.definition;
        const searchable = `${item.name} ${item.agent_key} ${item.description || ''} ${definition ? JSON.stringify(definition) : ''}`.toLocaleLowerCase();
        return searchable.includes(query);
      });
      const groupMarkup = (title, subtitle, items) => `<section class="crowd-agent-group"><div class="crowd-agent-group-head"><div><strong>${title}</strong><span>${subtitle}</span></div><b>${items.length}</b></div><div class="crowd-agent-group-grid">${items.map(item => this.agentCardMarkup(item)).join('') || '<div class="crowd-agent-empty">没有符合条件的 Agent。</div>'}</div></section>`;
      list.innerHTML = visible.length
        ? groupMarkup(experimentScope ? '实验智能体' : '基础智能体', experimentScope ? '当前实验内的独立副本' : '可编辑并复制到不同实验', visible)
        : '<div class="empty-state"><strong>没有符合条件的 Agent</strong><span>可按名称、文件键、特质、目标或标签搜索。</span></div>';
      list.querySelectorAll('[data-latest-revision-id][type="checkbox"]').forEach(input => input.addEventListener('change', () => {
        this.clearAgentSelection(input.dataset.agentId);
        if (input.checked) {
          this.memberSelection.add(input.dataset.latestRevisionId);
          this.agentRevisionOwners.set(input.dataset.latestRevisionId, input.dataset.agentId);
        }
        this.renderAgentList();
      }));
      list.querySelectorAll('[data-upgrade-agent-id]').forEach(button => button.addEventListener('click', () => {
        this.clearAgentSelection(button.dataset.upgradeAgentId);
        this.memberSelection.add(button.dataset.latestRevisionId);
        this.agentRevisionOwners.set(button.dataset.latestRevisionId, button.dataset.upgradeAgentId);
        this.renderAgentList();
      }));
      list.querySelectorAll('[data-edit-public-agent]').forEach(button => button.addEventListener('click', () => this.openAgentEditor(button.dataset.editPublicAgent).catch(error => this.fail(error))));
      list.querySelectorAll('[data-delete-public-agent]').forEach(button => button.addEventListener('click', () => this.deleteAgent(button.dataset.deletePublicAgent, button.dataset.deletePublicAgentName).catch(error => this.fail(error))));
      list.querySelectorAll('[data-agent-card-tab]').forEach(button => button.addEventListener('click', () => {
        const workspace = button.closest('[data-agent-card-definition]');
        if (!workspace) return;
        const tab = button.dataset.agentCardTab;
        workspace.querySelectorAll('[data-agent-card-tab]').forEach(item => {
          const active = item === button;
          item.classList.toggle('active', active);
          item.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        workspace.querySelectorAll('[data-agent-card-panel]').forEach(panel => panel.classList.toggle('active', panel.dataset.agentCardPanel === tab));
      }));
      this.updateAgentSelectionCount();
    },

    renderAuthorAgents() {
      const list = window.ResourceList, saved = list.read('public-agents');
      const query = saved.query.toLocaleLowerCase();
      const items = list.sorted(this.agents).filter(item => !query || `${item.name} ${item.agent_key} ${item.description || ''} ${item.definition?.currently || ''}`.toLocaleLowerCase().includes(query));
      const data = list.slice(items, saved.page);
      list.remember('public-agents', {page: data.page});
      const grid = byId('publicAgentList');
      grid.innerHTML = data.items.map(item => {
        const definition = item.definition || {}, age = definition.scratch?.age;
        const portrait = definition.portrait_asset_id ? `${API}/assets/${encodeURIComponent(definition.portrait_asset_id)}/content` : definition.portrait_asset || '';
        return list.row({name: item.name, description: item.description || definition.currently, icon: '♙', image: portrait,
          meta: [age != null ? `${age} 岁` : '', portrait ? '头像已配置' : '暂无头像', definition.sprite_asset_id || definition.sprite_asset ? '行走图已配置' : '暂无行走图'],
          open: {'data-edit-public-agent': item.id}, actions: [{label: '删除智能体', danger: true, attributes: {'data-delete-public-agent': item.id, 'data-delete-public-agent-name': item.name}}],
        });
      }).join('') || list.empty('智能体', Boolean(query));
      grid.querySelectorAll('[data-edit-public-agent]').forEach(button => button.onclick = () => this.openAgentEditor(button.dataset.editPublicAgent).catch(error => this.fail(error)));
      grid.querySelectorAll('[data-delete-public-agent]').forEach(button => button.onclick = () => this.deleteAgent(button.dataset.deletePublicAgent, button.dataset.deletePublicAgentName).catch(error => this.fail(error)));
      list.pager(byId('publicAgentFooter'), {...data, onPage: page => {list.remember('public-agents', {page, scroll: 0}); this.renderAuthorAgents();}});
    },

    async deleteAgent(agentId, name = '当前 Agent') {
      const confirmed = await window.confirmResourceDeletion({ type: 'Agent', name, message: '删除公共 Agent 不会影响已经导入实验的物理副本。' });
      if (!confirmed) return;
      await this.request(`/agents/${encodeURIComponent(agentId)}`, { method: 'DELETE' });
      this.clearAgentSelection(agentId);
      await this.loadAgents();
      this.renderAgentList();
      this.notify(`Agent“${name}”已删除。`, '删除完成');
    },

    updateAgentSelectionCount() {
      byId('crowdAgentSelectionCount').textContent = `已选择 ${this.memberSelection.size} 个 Agent`;
    },

    async applyAgentSelection() {
      if (!this.revision) return;
      const selected = [...this.memberSelection];
      this.detail = await this.request(`/crowds/${this.detail.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          row_version: this.detail.row_version,
          name: byId('crowdEditName').value.trim(),
          description: byId('crowdEditDescription').value.trim(),
          agent_ids: selected,
        }),
      });
      this.modal('close', 'crowdAgentManagerModal');
      await this.openCrowd(this.detail.id, false);
      this.notify(`已更新人群成员，共 ${selected.length} 个 Agent`);
    },

    async openAgentEditor(agentId = null) {
      if (experimentScope) return window.ExperimentAgentEditor.open(agentId);
      if (this.agentCatalogActive) window.ResourceList.capture('public-agents');
      this.agentDraft = null;
      this.agentDetail = null;
      this.modal('close', 'crowdAgentManagerModal');
      if (agentId) {
        this.agentDetail = await this.request(`/agents/${agentId}`);
        this.agentDraft = {
          agent_id: this.agentDetail.id,
          lock_version: this.agentDetail.row_version,
          description: this.agentDetail.description,
          definition: this.agentDetail.definition,
        };
      }
      if (!window.SharedAgentEditor?.openPublic) throw new Error('Agent 编辑器尚未加载');
      await window.SharedAgentEditor.openPublic({
        agentDetail: this.agentDetail,
        agentDraft: this.agentDraft,
      });
    },

    async openAgentViewer(agentId) {
      if (experimentScope) return window.ExperimentAgentEditor.open(agentId);
      const agentDetail = await this.request(`/agents/${agentId}`);
      const agentRevision = { definition: agentDetail.definition };
      if (!window.SharedAgentEditor?.openReadOnly) throw new Error('Agent 查看器尚未加载');
      await window.SharedAgentEditor.openReadOnly({ agentDetail, agentRevision });
    },

    async reopenAgentManager() {
      if (this.agentCatalogActive) return this.activateAgents();
      if (this.revision) await this.openAgentManager();
    },

    async saveSharedAgent({ definition, agentDetail, agentDraft }) {
      const description = agentDraft?.description || agentDetail?.description || '';
      let saved;
      let agentId;
      if (agentDraft) {
        agentId = agentDraft.agent_id;
        saved = await this.request(`/agents/${agentId}`, {
          method: 'PUT', body: JSON.stringify({ row_version: agentDraft.lock_version, definition, description }),
        });
      } else {
        saved = await this.request('/agents', {
          method: 'POST', body: JSON.stringify({ definition, description }),
        });
        agentId = saved.id;
      }
      this.agentRevisionDetails.set(agentId, saved);
      if (agentId) this.clearAgentSelection(agentId);
      this.memberSelection.add(agentId);
      this.agentRevisionOwners.set(agentId, agentId);
      return saved;
    },

    async afterSharedAgentSaved(published, name) {
      if (this.agentCatalogActive) { await this.activateAgents(); this.notify(`智能体“${name}”已保存`); return; }
      await this.loadAgents();
      this.renderAgentList();
      this.modal('open', 'crowdAgentManagerModal', 'crowdAgentSearch');
      await this.loadAgentRevisionDetails();
      this.renderAgentList();
      this.notify(`Agent“${name}”已保存并加入当前选择`);
    },

    async prepareExperimentCreate({ resetSelection = false } = {}) {
      this.init();
      if (resetSelection) {
        this.createSelection.clear();
        this.populateCreateSelector();
      }
      const result = await this.request('/crowds');
      this.selectorCrowds = (result.items || []).map(item => ({ ...item, agent_count: (item.agent_ids || []).length }));
      await Promise.all(this.selectorCrowds.map(async item => {
        if (!this.selectorDetails.has(item.id)) {
          const agents = await this.request('/agents');
          const byAgentId = new Map((agents.items || []).map(agent => [agent.id, agent]));
          const members = (item.agent_ids || []).map(agentId => byAgentId.get(agentId)).filter(Boolean);
          this.selectorDetails.set(item.id, { ...item, members });
        }
      }));
      const available = new Set(this.selectorCrowds.map(item => item.id));
      this.createSelection = new Set([...this.createSelection].filter(id => available.has(id)));
      this.populateCreateSelector();
      return this.getCreationSummary();
    },

    populateCreateSelector() {
      const root = byId('newExperimentCrowds');
      if (!root) return;
      root.innerHTML = this.selectorCrowds.length ? this.selectorCrowds.map(item => (
        `<label class="creation-crowd-option${this.createSelection.has(item.id) ? ' selected' : ''}"><input type="checkbox" value="${item.id}" ${this.createSelection.has(item.id) ? 'checked' : ''} /><span><strong>${this.escape(item.name)}</strong><small>${item.agent_count} 个 Agent · 当前公共定义</small></span></label>`
      )).join('') : '<div class="empty-state"><strong>暂无公共人群</strong><span>请先在人群中心创建人群并添加 Agent。</span></div>';
      root.querySelectorAll('input[type="checkbox"]').forEach(input => input.addEventListener('change', () => {
        if (input.checked) this.createSelection.add(input.value);
        else this.createSelection.delete(input.value);
        this.populateCreateSelector();
        window.dispatchEvent(new CustomEvent('crowd-workspace:create-selection', { detail: this.getCreationSummary() }));
      }));
    },

    selectedCreateRevisionIds() {
      return [...this.createSelection];
    },

    getCreationSummary() {
      const selected = this.selectorCrowds.filter(item => this.createSelection.has(item.id));
      const names = new Set();
      let rawCount = 0;
      selected.forEach(item => {
        const detail = this.selectorDetails.get(item.id);
        (detail?.members || []).forEach(member => {
          rawCount += 1;
          names.add(member.name.normalize('NFKC').trim().toLocaleLowerCase());
        });
      });
      return {
        revisionIds: this.selectedCreateRevisionIds(),
        names: selected.map(item => item.name),
        crowdCount: selected.length,
        agentCount: names.size,
        duplicateCount: Math.max(0, rawCount - names.size),
      };
    },
  };

  window.CrowdWorkspace = manager;
})();
