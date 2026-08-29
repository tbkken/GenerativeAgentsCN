/**
 * 人群与智能体模板工作区。
 * manager 集中管理目录分页、草稿、成员选择和 Revision；所有写操作都先更新服务端，
 * 再以响应重绘页面，避免浏览器局部状态冒充已保存数据。
 */
(function () {
  'use strict';

  const API = '/api/v1';
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
      this.init();
      await this.loadCrowds();
      const crowdId = new URLSearchParams(location.search).get('crowd_id');
      if (crowdId) await this.openCrowd(crowdId, false);
    },

    async loadCrowds() {
      this.init();
      const generation = ++this.listGeneration;
      const grid = byId('crowdCatalogGrid');
      grid.setAttribute('aria-busy', 'true');
      const params = new URLSearchParams({ page: String(this.page), page_size: String(this.pageSize) });
      if (this.query) params.set('q', this.query);
      if (this.status) params.set('status', this.status);
      let result;
      let selector;
      try {
        [result, selector] = await Promise.all([
          this.request(`/crowds?${params}`),
          this.request('/crowds?page=1&page_size=100'),
        ]);
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
      this.selectorCrowds = selector.items;
      this.renderCatalog(result);
      this.populateCreateSelector();
    },

    renderCatalog(result) {
      const grid = byId('crowdCatalogGrid');
      grid.innerHTML = this.crowds.length ? this.crowds.map(item => `
        <article class="resource-card-shell"><button class="crowd-card" data-crowd-id="${item.id}">
          <span class="crowd-card-top"><span class="map-state ${item.current_draft ? 'draft' : ''}">${item.current_draft ? '编辑中' : '已发布'}</span>${item.is_builtin ? '<b class="crowd-builtin">系统人群</b>' : `<code>${this.escape(item.crowd_key)}</code>`}</span>
          <h2>${this.escape(item.name)}</h2><p>${this.escape(item.description || '暂无用途说明')}</p>
          <span class="crowd-card-foot"><span><strong>${item.agent_count}</strong> 个 Agent</span><span>${item.usage_count} 个实验使用</span></span>
        </button>${item.is_builtin ? '' : `<button class="resource-card-delete" type="button" data-delete-crowd-id="${item.id}" data-delete-crowd-name="${this.escape(item.name)}">删除</button>`}</article>`).join('') : '<div class="empty-state"><strong>没有符合条件的人群</strong><span>新建人群后，可从公共 Agent 列表添加成员。</span></div>';
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
      const confirmed = window.confirmResourceDeletion
        ? await window.confirmResourceDeletion({ type: '人群', name, message: '人群及其全部成员快照和 Revision 将被删除。仍被实验 Revision 引用时，系统会拒绝操作。' })
        : window.confirm(`确认删除人群“${name}”？`);
      if (!confirmed) return;
      await this.request(`/crowds/${encodeURIComponent(crowdId)}`, { method: 'DELETE' });
      if (this.detail?.id === crowdId) this.showCatalog();
      await this.loadCrowds();
      this.notify(`人群“${name}”已删除。`, '删除完成');
    },

    async openCrowd(crowdId, push = true) {
      this.detail = await this.request(`/crowds/${crowdId}`);
      this.revisions = (await this.request(`/crowds/${crowdId}/revisions`)).items;
      this.revision = this.detail.current_draft
        ? await this.request(`/crowds/${crowdId}/draft`)
        : await this.request(`/crowds/${crowdId}/revisions/${this.detail.current_published.id}`);
      byId('crowdCatalogShell').hidden = true;
      byId('crowdEditorShell').hidden = false;
      byId('crowdEditorTitle').textContent = this.detail.name;
      byId('crowdEditorMeta').textContent = `${this.detail.crowd_key} · Revision ${String(this.revision.revision_no).padStart(3, '0')} · ${this.revision.agent_count} 个 Agent`;
      byId('crowdEditName').value = this.revision.name;
      byId('crowdEditDescription').value = this.revision.description || '';
      byId('crowdEditKey').value = this.revision.crowd_key;
      const editable = this.revision.state === 'DRAFT';
      byId('crowdEditName').disabled = !editable;
      byId('crowdEditDescription').disabled = !editable;
      byId('saveCrowdBtn').disabled = !editable;
      byId('confirmCrowdAgentManager').disabled = !editable;
      const state = byId('crowdEditorState');
      state.textContent = editable ? '草稿' : this.detail.is_builtin ? '系统人群 · 只读' : '已发布';
      state.classList.toggle('draft', editable);
      byId('publishCrowdBtn').textContent = editable ? '发布版本' : this.detail.is_builtin ? '基于此人群创建' : '创建新修订';
      byId('deleteCrowdBtn').hidden = Boolean(this.detail.is_builtin);
      this.renderMembers();
      window.dispatchEvent(new CustomEvent('crowd-workspace:selection', { detail: { crowdId } }));
      if (push) history.pushState({}, '', `/?view=crowds&crowd_id=${encodeURIComponent(crowdId)}`);
    },

    showCatalog() {
      this.detail = null;
      this.revision = null;
      byId('crowdEditorShell').hidden = true;
      byId('crowdCatalogShell').hidden = false;
      window.dispatchEvent(new CustomEvent('crowd-workspace:selection', { detail: { crowdId: null } }));
      history.pushState({}, '', '/?view=crowds');
    },

    renderMembers() {
      const members = this.revision?.members || [];
      byId('crowdMemberMeta').textContent = `${members.length} 个 Agent · Agent 版本随人群 Revision 锁定`;
      byId('crowdMemberGrid').innerHTML = members.length ? members.map(member => `
        <button type="button" class="crowd-member-card" data-view-crowd-agent="${this.escape(member.agent_id)}" data-agent-revision-id="${this.escape(member.agent_revision.id)}" aria-label="查看 ${this.escape(member.name)} 的 Agent 信息"><span class="crowd-member-avatar">${this.escape(member.name.slice(0, 1))}</span><span class="crowd-member-identity"><strong>${this.escape(member.name)}</strong><code>${this.escape(member.agent_key)}</code></span><span class="crowd-member-version">v${member.agent_revision.revision_no}${member.is_builtin ? ' · 系统' : ''}</span></button>
      `).join('') : '<div class="empty-state"><strong>尚未添加 Agent</strong><span>点击“Agent 管理”选择 Agent。</span></div>';
      byId('crowdMemberGrid').querySelectorAll('[data-view-crowd-agent]').forEach(button => button.addEventListener('click', () => {
        this.openAgentViewer(button.dataset.viewCrowdAgent, button.dataset.agentRevisionId).catch(error => this.fail(error));
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
        body: JSON.stringify({ name, description: byId('newCrowdDescription').value.trim(), agent_revision_ids: [] }),
      });
      this.modal('close', 'createCrowdModal');
      await this.loadCrowds();
      await this.openCrowd(created.id);
      this.notify(`已创建人群“${name}”，请添加 Agent 后发布。`);
      await this.openAgentManager();
    },

    async saveCrowd(options = {}) {
      if (!this.detail || this.revision?.state !== 'DRAFT') return this.revision;
      const result = await this.request(`/crowds/${this.detail.id}/draft`, {
        method: 'PUT',
        body: JSON.stringify({
          lock_version: this.revision.lock_version,
          name: byId('crowdEditName').value.trim(),
          description: byId('crowdEditDescription').value.trim(),
          agent_revision_ids: (this.revision.members || []).map(item => item.agent_revision.id),
        }),
      });
      this.revision = result;
      this.detail.name = result.name;
      if (!options.silent) this.notify('人群草稿已保存');
      await this.openCrowd(this.detail.id, false);
      return this.revision;
    },

    async publishOrFork() {
      if (!this.detail || !this.revision) return;
      if (this.revision.state === 'DRAFT') {
        await this.saveCrowd({ silent: true });
        const published = await this.request(`/crowds/${this.detail.id}/draft/publish`, {
          method: 'POST', body: JSON.stringify({ draft_revision_id: this.revision.id, lock_version: this.revision.lock_version }),
        });
        this.notify(`人群 Revision ${published.revision_no} 已发布`);
        await this.loadCrowds();
        await this.openCrowd(this.detail.id, false);
        await this.prepareExperimentCreate();
        return;
      }
      if (this.detail.is_builtin) {
        const created = await this.request('/crowds', {
          method: 'POST',
          body: JSON.stringify({ name: `${this.detail.name} · 自定义`, description: this.detail.description || '', source_revision_id: this.revision.id }),
        });
        this.notify('已基于系统人群创建可编辑副本');
        await this.loadCrowds();
        await this.openCrowd(created.id, false);
        return;
      }
      await this.request(`/crowds/${this.detail.id}/revisions/${this.revision.id}/fork`, { method: 'POST' });
      this.notify('已创建新的人群修订草稿');
      await this.loadCrowds();
      await this.openCrowd(this.detail.id, false);
    },

    async loadAgents() {
      const result = await this.request('/agent-templates?page=1&page_size=500');
      this.agents = result.items.filter(item => item.current_published);
      this.agents.forEach(item => this.agentRevisionOwners.set(item.current_published.id, item.id));
    },

    async loadAgentRevisionDetails() {
      const targets = new Map(this.agents.map(item => [item.current_published.id, item.id]));
      (this.revision?.members || []).forEach(member => {
        targets.set(member.agent_revision.id, member.agent_id);
        this.agentRevisionOwners.set(member.agent_revision.id, member.agent_id);
      });
      await Promise.all([...targets].map(async ([revisionId, agentId]) => {
        if (this.agentRevisionDetails.has(revisionId)) return;
        try {
          const detail = await this.request(`/agent-templates/${agentId}/revisions/${revisionId}`);
          this.agentRevisionDetails.set(revisionId, detail);
          this.agentRevisionOwners.set(revisionId, agentId);
        } catch (error) {
          console.error(error);
          this.agentRevisionDetails.set(revisionId, { loadError: error.message || '完整定义读取失败' });
        }
      }));
    },

    async openAgentManager() {
      if (!this.revision) return;
      await this.loadAgents();
      this.memberSelection = new Set((this.revision.members || []).map(item => item.agent_revision.id));
      (this.revision.members || []).forEach(member => this.agentRevisionOwners.set(member.agent_revision.id, member.agent_id));
      byId('crowdAgentSearch').value = '';
      byId('confirmCrowdAgentManager').disabled = this.revision.state !== 'DRAFT';
      byId('confirmCrowdAgentManager').textContent = this.revision.state === 'DRAFT' ? '应用到人群草稿' : '当前版本只读';
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
      const latestRevision = item.current_published;
      const selectedRevisionId = this.selectedRevisionIdForAgent(item.id);
      const lockedMember = (this.revision?.members || []).find(member => member.agent_id === item.id && member.agent_revision.id === selectedRevisionId);
      const revision = lockedMember?.agent_revision || latestRevision;
      const detail = this.agentRevisionDetails.get(revision.id);
      const checked = Boolean(selectedRevisionId);
      const outdated = checked && revision.id !== latestRevision.id;
      const editableCrowd = this.revision?.state === 'DRAFT';
      const scopeLabel = item.is_builtin ? '系统公共 · 只读' : '自定义公共';
      const edit = item.is_builtin ? '' : `<button type="button" data-edit-public-agent="${this.escape(item.id)}">编辑</button>`;
      const remove = item.is_builtin ? '' : `<button type="button" class="crowd-agent-delete" data-delete-public-agent="${this.escape(item.id)}" data-delete-public-agent-name="${this.escape(item.name)}">删除</button>`;
      const selectionLabel = checked ? '已加入当前人群' : '加入当前人群';
      const versionState = outdated
        ? `<span class="crowd-agent-version-warning">人群锁定 v${revision.revision_no} · 最新 v${latestRevision.revision_no}</span>${editableCrowd ? `<button type="button" class="crowd-agent-upgrade" data-upgrade-agent-id="${item.id}" data-latest-revision-id="${latestRevision.id}">升级到 v${latestRevision.revision_no}</button>` : '<span class="crowd-agent-version-readonly">创建人群新修订后可升级</span>'}`
        : `<span class="crowd-agent-version-current">当前最新 v${latestRevision.revision_no}</span>`;
      const selectionControl = `<label class="crowd-agent-select"><input type="checkbox" data-agent-id="${item.id}" data-latest-revision-id="${latestRevision.id}" ${checked ? 'checked' : ''} ${editableCrowd ? '' : 'disabled'} /><strong data-agent-selection-label>${selectionLabel}</strong></label>`;
      const definition = detail?.definition;
      if (!definition) {
        const error = detail?.loadError;
        return `<article class="crowd-agent-card${checked ? ' selected' : ''}" data-agent-scope="${item.is_builtin ? 'system' : 'custom'}">
          <div class="crowd-agent-card-head"><div><small>${item.is_builtin ? 'SYSTEM AGENT' : 'CUSTOM AGENT'}</small><h3>${this.escape(item.name)}<code>${this.escape(item.agent_key)} · v${revision.revision_no}</code></h3><div class="crowd-agent-version-state">${versionState}</div></div><div class="crowd-agent-card-actions"><span class="crowd-agent-scope">${scopeLabel}</span>${edit}${remove}${selectionControl}</div></div>
          <p>${this.escape(item.description || '暂无用途说明')}</p>
          <div class="crowd-agent-definition-state ${error ? 'error' : ''}">${error ? this.escape(error) : '正在读取完整 Agent 定义…'}</div>
        </article>`;
      }
      const scratch = definition.scratch || {};
      const coord = definition.coord || [0, 0];
      const addresses = Object.entries(definition.spatial?.address || {});
      const spaces = [];
      const flattenSpaceTree = (node, path = []) => {
        if (Array.isArray(node)) {
          spaces.push({ path, objects: node.map(value => String(value)) });
          return;
        }
        if (!node || typeof node !== 'object') {
          if (path.length) spaces.push({ path, objects: node == null ? [] : [String(node)] });
          return;
        }
        const entries = Object.entries(node);
        if (!entries.length && path.length) spaces.push({ path, objects: [] });
        entries.forEach(([key, value]) => flattenSpaceTree(value, [...path, key]));
      };
      flattenSpaceTree(definition.spatial?.tree || {});
      const builtinRoot = item.is_builtin && definition.name
        ? `/generative_agents/frontend/static/assets/village/agents/${encodeURIComponent(definition.name)}`
        : '';
      const portrait = definition.portrait_asset || (builtinRoot ? `${builtinRoot}/portrait.png` : '');
      const sprite = definition.sprite_asset || (builtinRoot ? `${builtinRoot}/texture.png` : '');
      const imageCard = (url, title, note, spriteSheet = false) => `<article class="agent-image-card crowd-agent-readonly-image-card">
        <div class="agent-image-preview ${spriteSheet ? 'sprite' : 'portrait'}">${url
          ? `<img src="${this.escape(url)}" alt="${this.escape(definition.name)}${title}" onerror="this.hidden=true;this.nextElementSibling.hidden=false" /><span hidden>暂无${title}</span>`
          : `<span>暂无${title}</span>`}</div>
        <div class="agent-image-copy"><strong>${title}</strong><span>${note}</span><small>随当前 Agent Revision 保存</small></div>
      </article>`;
      const field = (label, value, wide = false) => `<div class="field crowd-agent-readonly-field${wide ? ' wide' : ''}"><label>${label}</label><div class="control crowd-agent-readonly-value">${this.escape(value ?? '') || '未填写'}</div></div>`;
      const displayPurpose = purpose => purpose === 'living_area' ? '居住地' : purpose === 'sleeping' ? '睡觉' : purpose;
      const addressRows = addresses.length ? addresses.map(([purpose, path]) => `<div class="spatial-table-row">
        <span class="crowd-agent-spatial-value">${this.escape(displayPurpose(purpose))}</span><span class="crowd-agent-spatial-value">${this.escape(Array.isArray(path) ? path.join(' > ') : path)}</span>
      </div>`).join('') : '<div class="spatial-table-empty">尚未配置常用地址</div>';
      const spaceRows = spaces.length ? spaces.map(row => `<div class="spatial-table-row">
        <span class="crowd-agent-spatial-value">${this.escape(row.path.join(' > '))}</span><span class="crowd-agent-spatial-value">${this.escape(row.objects.join('，') || '暂无可交互物件')}</span>
      </div>`).join('') : '<div class="spatial-table-empty">尚未配置可用空间</div>';
      return `<article class="crowd-agent-card${checked ? ' selected' : ''}" data-agent-scope="${item.is_builtin ? 'system' : 'custom'}">
        <div class="crowd-agent-card-head">
          <div class="crowd-agent-identity"><div><small>${item.is_builtin ? 'SYSTEM AGENT' : 'CUSTOM AGENT'}</small><h3>${this.escape(definition.name)}<code>${this.escape(definition.agent_key)} · Revision ${String(revision.revision_no).padStart(3, '0')}</code></h3><div class="crowd-agent-version-state">${versionState}</div></div></div>
          <div class="crowd-agent-card-actions"><span class="crowd-agent-scope">${scopeLabel}</span>${edit}${remove}${selectionControl}</div>
        </div>
        <div class="content-workspace crowd-agent-readonly-workspace" data-agent-card-definition="${revision.id}">
          <nav class="content-tabs crowd-agent-content-tabs" role="tablist" aria-label="${this.escape(definition.name)}定义内容">
            <button class="content-tab active" type="button" role="tab" aria-selected="true" data-agent-card-tab="identity">身份</button>
            <button class="content-tab" type="button" role="tab" aria-selected="false" data-agent-card-tab="traits">特质与计划</button>
            <button class="content-tab" type="button" role="tab" aria-selected="false" data-agent-card-tab="space">初始位置与空间</button>
          </nav>
          <div class="content-tab-panel active crowd-agent-readonly-panel" role="tabpanel" data-agent-card-panel="identity">
            <div class="form-grid">
              ${field('显示名称', definition.name)}${field('年龄', scratch.age == null ? '' : `${scratch.age} 岁`)}
              <div class="agent-image-editor crowd-agent-readonly-images">${imageCard(portrait, '头像', '正方形 PNG；用于列表、结果与对话展示。')}${imageCard(sprite, '4×4 行走图', '128×128 PNG；四行依次为下、左、右、上。', true)}</div>
              ${field('当前目标', definition.currently, true)}
            </div>
          </div>
          <div class="content-tab-panel crowd-agent-readonly-panel" role="tabpanel" data-agent-card-panel="traits">
            <div class="form-grid">${field('天生特质', scratch.innate)}${field('背景经历', scratch.learned)}${field('生活方式', scratch.lifestyle)}${field('日常计划', scratch.daily_plan)}</div>
          </div>
          <div class="content-tab-panel crowd-agent-readonly-panel" role="tabpanel" data-agent-card-panel="space">
            <div class="form-grid">
              ${field('初始 X', coord[0])}${field('初始 Y', coord[1])}
              <div class="spatial-form-editor crowd-agent-readonly-spatial">
                <section class="spatial-editor-section"><div class="spatial-editor-head"><div><strong>常用地址</strong><span>用途和位置层级与编辑页保持一致。</span></div></div><div class="spatial-table spatial-address-table"><div class="spatial-table-head"><span>用途</span><span>位置层级</span></div>${addressRows}</div></section>
                <section class="spatial-editor-section"><div class="spatial-editor-head"><div><strong>可用空间</strong><span>展示 Agent 已知地点及其可交互物件。</span></div></div><div class="spatial-table spatial-tree-table"><div class="spatial-table-head"><span>空间层级</span><span>可交互物件</span></div>${spaceRows}</div></section>
              </div>
            </div>
          </div>
        </div>
      </article>`;
    },

    renderAgentList() {
      const query = byId('crowdAgentSearch').value.trim().toLocaleLowerCase();
      const visible = this.agents.filter(item => {
        if (!query) return true;
        const selectedRevisionId = this.selectedRevisionIdForAgent(item.id);
        const definition = this.agentRevisionDetails.get(selectedRevisionId || item.current_published.id)?.definition;
        const searchable = `${item.name} ${item.agent_key} ${item.description || ''} ${definition ? JSON.stringify(definition) : ''}`.toLocaleLowerCase();
        return searchable.includes(query);
      });
      const custom = visible.filter(item => !item.is_builtin);
      const system = visible.filter(item => item.is_builtin);
      const groupMarkup = (title, subtitle, items) => `<section class="crowd-agent-group"><div class="crowd-agent-group-head"><div><strong>${title}</strong><span>${subtitle}</span></div><b>${items.length}</b></div><div class="crowd-agent-group-grid">${items.map(item => this.agentCardMarkup(item)).join('') || '<div class="crowd-agent-empty">没有符合条件的 Agent。</div>'}</div></section>`;
      byId('crowdAgentList').innerHTML = visible.length
        ? `${groupMarkup('自定义公共 Agent', '数据库保存 · 全局可编辑 · 可加入多个人群', custom)}${groupMarkup('系统公共 Agent', '平台内置 · 全局只读 · 完整定义可见', system)}`
        : '<div class="empty-state"><strong>没有符合条件的 Agent</strong><span>可按名称、文件键、特质、目标或标签搜索。</span></div>';
      byId('crowdAgentList').querySelectorAll('[data-latest-revision-id][type="checkbox"]').forEach(input => input.addEventListener('change', () => {
        this.clearAgentSelection(input.dataset.agentId);
        if (input.checked) {
          this.memberSelection.add(input.dataset.latestRevisionId);
          this.agentRevisionOwners.set(input.dataset.latestRevisionId, input.dataset.agentId);
        }
        this.renderAgentList();
      }));
      byId('crowdAgentList').querySelectorAll('[data-upgrade-agent-id]').forEach(button => button.addEventListener('click', () => {
        this.clearAgentSelection(button.dataset.upgradeAgentId);
        this.memberSelection.add(button.dataset.latestRevisionId);
        this.agentRevisionOwners.set(button.dataset.latestRevisionId, button.dataset.upgradeAgentId);
        this.renderAgentList();
      }));
      byId('crowdAgentList').querySelectorAll('[data-edit-public-agent]').forEach(button => button.addEventListener('click', () => this.openAgentEditor(button.dataset.editPublicAgent).catch(error => this.fail(error))));
      byId('crowdAgentList').querySelectorAll('[data-delete-public-agent]').forEach(button => button.addEventListener('click', () => this.deleteAgent(button.dataset.deletePublicAgent, button.dataset.deletePublicAgentName).catch(error => this.fail(error))));
      byId('crowdAgentList').querySelectorAll('[data-agent-card-tab]').forEach(button => button.addEventListener('click', () => {
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

    async deleteAgent(agentId, name = '当前 Agent') {
      const confirmed = window.confirmResourceDeletion
        ? await window.confirmResourceDeletion({ type: 'Agent', name, message: 'Agent 的草稿和全部 Revision 将被删除。仍被任何人群 Revision 引用时，系统会拒绝操作。' })
        : window.confirm(`确认删除 Agent“${name}”？`);
      if (!confirmed) return;
      await this.request(`/agent-templates/${encodeURIComponent(agentId)}`, { method: 'DELETE' });
      this.clearAgentSelection(agentId);
      await this.loadAgents();
      this.renderAgentList();
      this.notify(`Agent“${name}”已删除。`, '删除完成');
    },

    updateAgentSelectionCount() {
      byId('crowdAgentSelectionCount').textContent = `已选择 ${this.memberSelection.size} 个 Agent`;
    },

    async applyAgentSelection() {
      if (this.revision?.state !== 'DRAFT') return;
      const selected = [...this.memberSelection];
      this.revision = await this.request(`/crowds/${this.detail.id}/draft`, {
        method: 'PUT',
        body: JSON.stringify({
          lock_version: this.revision.lock_version,
          name: byId('crowdEditName').value.trim(),
          description: byId('crowdEditDescription').value.trim(),
          agent_revision_ids: selected,
        }),
      });
      this.modal('close', 'crowdAgentManagerModal');
      this.renderMembers();
      byId('crowdEditorMeta').textContent = `${this.detail.crowd_key} · Revision ${String(this.revision.revision_no).padStart(3, '0')} · ${this.revision.agent_count} 个 Agent`;
      this.notify(`已更新人群成员，共 ${selected.length} 个 Agent`);
    },

    async openAgentEditor(agentId = null) {
      this.agentDraft = null;
      this.agentDetail = null;
      this.modal('close', 'crowdAgentManagerModal');
      if (agentId) {
        this.agentDetail = await this.request(`/agent-templates/${agentId}`);
        this.agentDraft = this.agentDetail.current_draft
          ? await this.request(`/agent-templates/${agentId}/draft`)
          : await this.request(`/agent-templates/${agentId}/revisions/${this.agentDetail.current_published.id}/fork`, { method: 'POST' });
      }
      if (!window.SharedAgentEditor?.openPublic) throw new Error('Agent 编辑器尚未加载');
      await window.SharedAgentEditor.openPublic({
        agentDetail: this.agentDetail,
        agentDraft: this.agentDraft,
      });
    },

    async openAgentViewer(agentId, revisionId) {
      const [agentDetail, agentRevision] = await Promise.all([
        this.request(`/agent-templates/${agentId}`),
        this.request(`/agent-templates/${agentId}/revisions/${revisionId}`),
      ]);
      if (!window.SharedAgentEditor?.openReadOnly) throw new Error('Agent 查看器尚未加载');
      await window.SharedAgentEditor.openReadOnly({ agentDetail, agentRevision });
    },

    async reopenAgentManager() {
      if (this.revision) await this.openAgentManager();
    },

    async saveSharedAgent({ definition, agentDetail, agentDraft }) {
      const description = agentDraft?.description || agentDetail?.description || '';
      let draft;
      let agentId;
      if (agentDraft) {
        agentId = agentDraft.agent_id;
        draft = await this.request(`/agent-templates/${agentId}/draft`, {
          method: 'PUT', body: JSON.stringify({ lock_version: agentDraft.lock_version, definition, description }),
        });
      } else {
        const created = await this.request('/agent-templates', {
          method: 'POST', body: JSON.stringify({ definition, description, agent_key: definition.agent_key }),
        });
        agentId = created.id;
        draft = await this.request(`/agent-templates/${agentId}/draft`);
      }
      const published = await this.request(`/agent-templates/${agentId}/draft/publish`, {
        method: 'POST', body: JSON.stringify({ draft_revision_id: draft.id, lock_version: draft.lock_version }),
      });
      if (agentId) this.clearAgentSelection(agentId);
      this.memberSelection.add(published.id);
      this.agentRevisionOwners.set(published.id, agentId);
      return published;
    },

    async afterSharedAgentSaved(published, name) {
      await this.loadAgents();
      this.renderAgentList();
      this.modal('open', 'crowdAgentManagerModal', 'crowdAgentSearch');
      await this.loadAgentRevisionDetails();
      this.renderAgentList();
      this.notify(`Agent“${name}”已发布并加入当前选择`);
    },

    async prepareExperimentCreate() {
      this.init();
      const result = await this.request('/crowds?page=1&page_size=100');
      this.selectorCrowds = result.items.filter(item => item.current_published);
      await Promise.all(this.selectorCrowds.map(async item => {
        const revision = item.current_published;
        if (!this.selectorDetails.has(revision.id)) {
          const detail = await this.request(`/crowds/${item.id}/revisions/${revision.id}`);
          this.selectorDetails.set(revision.id, detail);
        }
      }));
      const available = new Set(this.selectorCrowds.map(item => item.current_published.id));
      this.createSelection = new Set([...this.createSelection].filter(id => available.has(id)));
      if (!this.createSelection.size) {
        const initial = this.selectorCrowds.find(item => item.is_builtin) || this.selectorCrowds[0];
        if (initial) this.createSelection.add(initial.current_published.id);
      }
      this.populateCreateSelector();
      return this.getCreationSummary();
    },

    populateCreateSelector() {
      const root = byId('newExperimentCrowds');
      if (!root) return;
      root.innerHTML = this.selectorCrowds.length ? this.selectorCrowds.map(item => {
        const revision = item.current_published;
        return `<label class="creation-crowd-option${this.createSelection.has(revision.id) ? ' selected' : ''}"><input type="checkbox" value="${revision.id}" ${this.createSelection.has(revision.id) ? 'checked' : ''} /><span><strong>${this.escape(item.name)}</strong><small>${item.agent_count} 个 Agent · v${revision.revision_no}${item.is_builtin ? ' · 系统' : ''}</small></span></label>`;
      }).join('') : '<div class="empty-state"><strong>暂无已发布人群</strong><span>请先在人群中心创建并发布人群。</span></div>';
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
      const selected = this.selectorCrowds.filter(item => this.createSelection.has(item.current_published.id));
      const names = new Set();
      let rawCount = 0;
      selected.forEach(item => {
        const detail = this.selectorDetails.get(item.current_published.id);
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
