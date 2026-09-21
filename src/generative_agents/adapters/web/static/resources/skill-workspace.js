/**
 * Skill 工作区：浏览原子 Skill/包/Brain，编辑 SKILL.md，查看依赖并手动试运行。
 * state.current 保存当前文档，state.dependencies 保存解析后的调用关系，state.run 保存
 * 最近一次运行及 trace；切换目录时这些状态会被重新装载而不是隐式复用。
 */
(function () {
  'use strict';

  const state = {
    mounted: false,
    page: 'skills',
    kind: 'atomic',
    query: '',
    items: [],
    counts: { atomic: 0, pack: 0, brain: 0 },
    current: null,
    dependencies: null,
    activeTab: 'definition',
    activeFile: 'SKILL.md',
    run: null,
    catalogGeneration: 0,
    editorGeneration: 0,
    selectedModelId: '',
  };

  const $ = id => document.getElementById(id);
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]));

  const experimentScope = Boolean(window.ResourceScope?.experimentId);
  async function api(path, options = {}) {
    if (experimentScope) path = path.replace('/api/studio/resources', window.ResourceScope.base);
    options = window.ResourceScope?.options(options) || options;
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body.detail;
      const error = new Error(detail?.message || (typeof detail === 'string' ? detail : '') || body.error?.message || `请求失败（${response.status}）`);
      error.trace = detail?.trace || [];
      throw error;
    }
    if (experimentScope && options.method && options.method !== 'GET' && !path.endsWith('/run')) window.ResourceScope.saved(body);
    return body;
  }

  function mount() {
    if (state.mounted) return;
    const skillsPage = $('page-skills');
    const brainsPage = $('page-brains');
    if (!skillsPage || !brainsPage) return;
    skillsPage.innerHTML = '<div id="skillWorkspace" class="skill-workspace"></div>';
    brainsPage.innerHTML = '<div id="brainSkillWorkspace" class="skill-workspace"></div>';
    state.mounted = true;
    $('createSkillBtn')?.addEventListener('click', () => showCreate(state.kind === 'pack' ? 'pack' : 'atomic'));
    $('createBrainBtn')?.addEventListener('click', () => showCreate('brain'));
  }

  async function activate(page = 'skills') {
    mount();
    const activation = state.activationGeneration = (state.activationGeneration || 0) + 1;
    state.editorGeneration += 1;
    state.page = page;
    state.current = null;
    const inactiveHost = page === 'brains' ? $('skillWorkspace') : $('brainSkillWorkspace');
    if (inactiveHost) inactiveHost.replaceChildren();
    state.kind = page === 'brains' ? 'brain' : (state.kind === 'brain' ? 'atomic' : state.kind);
    deactivateTopbar();
    await loadCatalog();
    if (activation !== state.activationGeneration || page !== state.page) return;
    if (!experimentScope) {
      const name = new URLSearchParams(location.search).get('skill_key');
      if (name) await openSkill(name, false);
    }
  }

  async function loadCatalog() {
    if (!experimentScope) return loadAuthorCatalog();
    const generation = ++state.catalogGeneration;
    let result;
    try {
      result = await api(`/api/studio/resources/skills?kind=${encodeURIComponent(state.kind)}&q=${encodeURIComponent(state.query)}`);
    } catch (error) {
      if (generation !== state.catalogGeneration) return;
      const target = host();
      if (target) {
        target.innerHTML = `<div class="skill-empty resource-load-error" role="alert"><strong>Skill 列表加载失败</strong><span>${escapeHtml(error.message || '请稍后重试')}</span><button class="btn btn-sm" id="retrySkillCatalog">重新加载</button></div>`;
        $('retrySkillCatalog')?.addEventListener('click', () => loadCatalog().catch(report));
      }
      throw error;
    }
    if (generation !== state.catalogGeneration) return;
    state.items = result.items || [];
    state.counts = result.counts || state.counts;
    renderCatalog();
  }

  async function loadAuthorCatalog() {
    const list = window.ResourceList, page = state.page, saved = list.read(page);
    const generation = ++state.catalogGeneration;
    const isBrain = page === 'brains', label = isBrain ? '大脑' : '技能';
    state.items = [];
    state.query = saved.query;
    state.kind = isBrain ? 'brain' : saved.kind;
    const target = host();
    target.innerHTML = `<div class="resource-catalog" data-resource-catalog><div class="resource-toolbar"><div class="search"><input class="control" id="skillSearchInput" aria-label="搜索${label}" placeholder="搜索${label}名称或说明…" value="${escapeHtml(state.query)}"></div>${isBrain?'':`<label>类型<select class="control" id="skillKindFilter"><option value="">全部类型</option><option value="atomic">单个技能</option><option value="pack">技能包</option></select></label>`}</div><div class="resource-rows" id="authorSkillRows"></div><div class="resource-list-footer" id="authorSkillFooter"></div></div>`;
    if ($('skillKindFilter')) {
      $('skillKindFilter').value = saved.kind;
      $('skillKindFilter').onchange = event => {state.kind = event.target.value; list.remember(page, {kind: state.kind, page: 1, scroll: 0}); renderAuthorRows();};
    }
    $('skillSearchInput').oninput = event => {state.query = event.target.value; list.remember(page, {query: state.query, page: 1, scroll: 0}); renderAuthorRows();};
    const grid = $('authorSkillRows');
    list.loading(grid, label);
    try {
      const result = await api('/api/studio/resources/skills');
      if (generation !== state.catalogGeneration || page !== state.page) return;
      state.items = (result.items || []).filter(item => isBrain ? item.kind === 'brain' : item.kind !== 'brain');
      renderAuthorRows();
      if (!new URLSearchParams(location.search).has('skill_key')) list.restore(page);
    } catch (error) {
      if (generation !== state.catalogGeneration || page !== state.page) return;
      list.error(grid, label, error, () => loadAuthorCatalog().catch(report));
      $('authorSkillFooter').hidden = true;
    } finally { if (generation === state.catalogGeneration) grid.removeAttribute('aria-busy'); }
  }

  function renderAuthorRows() {
    const list = window.ResourceList, saved = list.read(state.page), query = saved.query.toLocaleLowerCase();
    const label = state.page === 'brains' ? '大脑' : '技能';
    const items = list.sorted(state.items).filter(item => (!saved.kind || item.kind === saved.kind) && (!query || `${item.name} ${item.description || ''}`.toLocaleLowerCase().includes(query)));
    const data = list.slice(items, saved.page), grid = $('authorSkillRows');
    if (!grid) return;
    list.remember(state.page, {page: data.page});
    grid.innerHTML = data.items.map(item => list.row({name: titleCase(item.name), description: item.description, icon: state.page === 'brains' ? '⌬' : '◇',
      badges: state.page === 'skills' ? [item.kind === 'pack' ? '技能包' : '单个技能'] : [],
      meta: [(item.children || []).length ? `${item.children.length} 个子技能` : '', (item.scripts || []).length ? `${item.scripts.length} 个脚本` : '文本技能'],
      open: {'data-skill-name': item.name}, actions: [{label: `删除${label}`, danger: true, attributes: {'data-delete-skill': item.name, 'data-delete-skill-label': titleCase(item.name), 'data-delete-skill-kind': item.kind}}],
    })).join('') || list.empty(label, Boolean(query || saved.kind));
    grid.querySelectorAll('[data-skill-name]').forEach(button => button.onclick = () => openSkill(button.dataset.skillName).catch(report));
    grid.querySelectorAll('[data-delete-skill]').forEach(button => button.onclick = () => deleteSkill(button.dataset.deleteSkill, button.dataset.deleteSkillLabel, button.dataset.deleteSkillKind).catch(report));
    list.pager($('authorSkillFooter'), {...data, onPage: page => {list.remember(state.page, {page, scroll: 0}); renderAuthorRows();}});
  }

  function host() {
    if (state.page === 'brains') return $('brainSkillWorkspace');
    return $('skillWorkspace');
  }

  function renderCatalog() {
    const target = host();
    if (!target) return;
    deactivateTopbar();
    const isBrain = state.page === 'brains';
    const cards = state.items.map(item => skillCard(item)).join('');
    target.innerHTML = `
      <div class="skill-catalog-toolbar">
        <div class="skill-kind-tabs" ${isBrain ? 'hidden' : ''}>
          ${kindButton('atomic', '单个 Skill', state.counts.atomic)}
          ${kindButton('pack', 'Skill 包', state.counts.pack)}
        </div>
        <label class="skill-search"><span>⌕</span><input id="skillSearchInput" value="${escapeHtml(state.query)}" placeholder="搜索名称、用途或执行说明…"></label>
        <button class="btn btn-primary" id="skillCreateInline" ${experimentScope && (isBrain || !window.ResourceScope.editable) ? 'hidden' : ''}>＋ 新建${isBrain ? '大脑' : state.kind === 'pack' ? ' Skill 包' : ' Skill'}</button>
      </div>
      <div class="skill-catalog-summary"><strong>${state.items.length}</strong><span>${isBrain ? '个可用大脑' : state.kind === 'pack' ? '个技能包' : '个原子技能'} · ${experimentScope ? '当前实验的独立副本' : '当前内容可直接编辑，加入实验时物理复制'}</span></div>
      <section class="skill-card-grid">${cards || '<div class="skill-empty"><strong>没有找到 Skill</strong><span>换一个搜索词，或创建新的数据库 Skill。</span></div>'}</section>`;

    target.querySelectorAll('[data-skill-kind]').forEach(button => button.addEventListener('click', async () => {
      state.kind = button.dataset.skillKind;
      state.query = '';
      await loadCatalog();
    }));
    target.querySelectorAll('[data-skill-name]').forEach(card => card.addEventListener('click', () => openSkill(card.dataset.skillName)));
    target.querySelectorAll('[data-delete-skill]').forEach(button => button.addEventListener('click', () => deleteSkill(button.dataset.deleteSkill, button.dataset.deleteSkillLabel, button.dataset.deleteSkillKind).catch(report)));
    $('skillCreateInline')?.addEventListener('click', () => showCreate(isBrain ? 'brain' : state.kind));
    let searchTimer;
    $('skillSearchInput')?.addEventListener('input', event => {
      clearTimeout(searchTimer);
      state.query = event.target.value;
      searchTimer = setTimeout(() => loadCatalog().catch(report), 220);
    });
  }

  function kindButton(kind, label, count) {
    return `<button class="${state.kind === kind ? 'active' : ''}" data-skill-kind="${kind}">${label}<span>${count || 0}</span></button>`;
  }

  function skillCard(item) {
    const type = item.kind === 'brain' ? 'BRAIN SKILL' : item.kind === 'pack' ? 'SKILL PACK' : 'SKILL';
    const deleteLabel = item.kind === 'brain' ? '删除大脑' : item.kind === 'pack' ? '删除技能包' : '删除技能';
    const children = item.children || [];
    const scripts = item.scripts || [];
    const flow = children.length
      ? children.slice(0, 4).map(name => `<span>$${escapeHtml(name)}</span>`).join('<i>→</i>')
      : '';
    return `<article class="resource-card-shell"><button class="skill-card-real" data-skill-name="${escapeHtml(item.name)}">
      <span class="skill-card-head"><em>${type}</em><span class="skill-live"><i></i>可用</span></span>
      <h2>${escapeHtml(titleCase(item.name))}</h2>
      <p>${escapeHtml(item.description)}</p>
      ${flow ? `<span class="skill-card-flow-real">${flow}</span>` : ''}
      <span class="skill-card-footer"><code>${escapeHtml(item.storage === 'experiment' ? `实验技能 · ${String(item.content_hash || '').slice(0, 12)}` : item.storage === 'database' ? `Studio Skill · ${String(item.content_hash || '').slice(0, 12)}` : `skills/${item.kind === 'atomic' ? 'atomic' : `${item.kind}s`}/${item.name}/`)}</code><span>${children.length ? `${children.length} 个子 Skill` : scripts.length ? `${scripts.length} 个 Script` : '文本 Skill'}</span></span>
    </button><button class="resource-card-delete" ${experimentScope && (item.kind === 'brain' || !window.ResourceScope.editable) ? 'hidden' : ''} type="button" aria-label="${deleteLabel}" title="${deleteLabel}" data-delete-skill="${escapeHtml(item.name)}" data-delete-skill-label="${escapeHtml(titleCase(item.name))}" data-delete-skill-kind="${escapeHtml(item.kind)}">删除</button></article>`;
  }

  async function openSkill(name, push = true) {
    state.catalogGeneration += 1;
    if (!experimentScope && push) window.ResourceList.capture(state.page);
    const generation = ++state.editorGeneration;
    const requestedPage = state.page;
    const [detail, dependencies] = await Promise.all([
      api(`/api/studio/resources/skills/${encodeURIComponent(name)}`),
      api(`/api/studio/resources/skills/${encodeURIComponent(name)}/dependencies`),
    ]);
    if (generation !== state.editorGeneration || requestedPage !== state.page) return;
    state.current = detail;
    state.dependencies = dependencies;
    state.activeTab = 'definition';
    state.activeFile = 'SKILL.md';
    state.run = null;
    renderEditor();
    if (!experimentScope && push) {
      window.ResourceList.route(state.page, {skill_key: name});
      window.scrollTo({top: 0, behavior: 'instant'});
    }
  }

  function renderEditor() {
    const target = host();
    const item = state.current;
    if (!target || !item) return;
    syncEditorTopbar(item);
    target.innerHTML = `
      <nav class="skill-editor-tabs-real">
        ${tabButton('definition', 'SKILL.md')}
        ${tabButton('dependencies', `Scripts 与 MCP <span>${dependencyCount()}</span>`)}
        ${tabButton('run', '试运行')}
        ${tabButton('history', '内容')}
        <code>${escapeHtml(item.path)}</code>
      </nav>
      <main id="skillEditorPanel">${renderPanel()}</main>`;
    target.querySelectorAll('[data-skill-tab]').forEach(button => button.addEventListener('click', () => {
      if (state.activeTab === 'definition') captureActiveSource();
      state.activeTab = button.dataset.skillTab;
      renderEditor();
    }));
    bindPanel();
    if (item.editable === false) {
      target.querySelectorAll('textarea, [data-add-skill-script], [data-delete-skill-script]').forEach(control => { control.disabled = true; });
    }
  }

  function editorKind(item) {
    return item.kind === 'brain' ? 'BRAIN SKILL' : item.kind === 'pack' ? 'SKILL PACK' : 'ATOMIC SKILL';
  }

  function editorRootLabel() {
    if (state.page === 'brains') return '大脑中心';
    return '技能列表';
  }

  function backToCatalog() {
    state.editorGeneration += 1;
    state.current = null;
    deactivateTopbar();
    if (!experimentScope) window.ResourceList.route(state.page);
    loadCatalog().catch(report);
  }

  function syncEditorTopbar(item) {
    document.body.classList.add('skill-editor-mode');
    $('defaultTopbarContext').hidden = true;
    $('skillEditorTopbarContext').hidden = false;
    $('skillEditorTopbarActions').hidden = false;
    $('hubActions').hidden = true;
    $('experimentActions').hidden = true;
    $('skillEditorBack').textContent = editorRootLabel();
    $('skillEditorTitle').textContent = titleCase(item.name);
    $('skillEditorKind').textContent = editorKind(item);
    $('skillEditorDescription').textContent = item.description || '';
    $('skillEditorRevision').textContent = `内容 ${String(item.content_hash || '').slice(0, 12)}`;
    $('skillEditorBack').onclick = backToCatalog;
    $('skillSave').textContent = '保存内容';
    $('skillSave').disabled = item.editable === false;
    $('skillSave').onclick = saveSkill;
    $('skillDelete').hidden = item.editable === false || (experimentScope && item.kind === 'brain');
    $('skillDelete').textContent = item.kind === 'brain' ? '删除大脑' : item.kind === 'pack' ? '删除技能包' : '删除技能';
    $('skillDelete').onclick = () => deleteSkill(item.name, titleCase(item.name), item.kind).catch(report);
  }

  async function deleteSkill(name, label = name, kind = state.kind) {
    const type = kind === 'brain' ? '大脑' : kind === 'pack' ? '技能包' : '技能';
    const confirmed = await window.confirmResourceDeletion({ type, name: label, message: experimentScope ? '从当前实验移除此技能。仍被大脑或对象使用的技能不能移除。' : `${type}基础配置将被删除；已有实验不受影响。` });
    if (!confirmed) return;
    await api(`/api/studio/resources/skills/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (state.current?.name === name) state.current = null;
    if (!experimentScope) window.ResourceList.route(state.page);
    deactivateTopbar();
    await loadCatalog();
    toast(`${type}“${label}”已删除`);
  }

  function deactivateTopbar() {
    document.body.classList.remove('skill-editor-mode');
    $('skillEditorTopbarContext')?.setAttribute('hidden', '');
    $('skillEditorTopbarActions')?.setAttribute('hidden', '');
    if ($('defaultTopbarContext')) $('defaultTopbarContext').hidden = false;
    const isGlobal = ['skills', 'brains'].includes(state.page);
    if ($('hubActions')) $('hubActions').hidden = !isGlobal;
    if ($('experimentActions')) $('experimentActions').hidden = isGlobal;
  }

  function tabButton(tab, label) {
    return `<button class="${state.activeTab === tab ? 'active' : ''}" data-skill-tab="${tab}">${label}</button>`;
  }

  function dependencyCount() {
    const dependencies = state.dependencies || { scripts: [], skills: [], mcp: [] };
    return dependencies.scripts.length + dependencies.skills.length + dependencies.mcp.length;
  }

  function renderPanel() {
    const item = state.current;
    if (state.activeTab === 'definition') {
      const scripts = item.script_sources || {};
      const activeFile = state.activeFile === 'SKILL.md' || Object.hasOwn(scripts, state.activeFile)
        ? state.activeFile : 'SKILL.md';
      state.activeFile = activeFile;
      const source = activeFile === 'SKILL.md' ? item.markdown : scripts[activeFile];
      const fileButtons = Object.keys(scripts).sort().map(path => `<button class="${activeFile === path ? 'active' : ''}" data-skill-file="${escapeHtml(path)}">${escapeHtml(path)}</button>`).join('');
      return `
      <section class="skill-definition-layout">
        <aside class="skill-definition-guide">
          <span>WHY THIS FILE</span><h2>一份说明，就是一项能力</h2>
          <p>Frontmatter 让大脑发现它；需要确定性处理时，可在当前 Skill 中加入私有 Python Script。</p>
          <dl><dt>名称</dt><dd><code>${escapeHtml(item.name)}</code></dd><dt>类型</dt><dd>${kindName(item.kind)}</dd><dt>结果交接</dt><dd>自然语言</dd></dl>
          <div class="skill-source-truth"><i>✓</i><div><strong>${experimentScope ? '当前实验的独立副本' : '基础配置'}</strong><span>${experimentScope ? '保存仅修改当前实验内的 SKILL.md 与 scripts/，不会改变基础配置。' : 'SKILL.md 与 scripts/ 直接保存；加入实验时复制当前内容。'}</span></div></div>
        </aside>
        <div class="skill-markdown-editor">
          <header><span><i></i>${escapeHtml(activeFile)}</span><span class="skill-source-actions"><small>${activeFile === 'SKILL.md' ? 'Markdown' : 'Python'} · UTF-8</small>${activeFile === 'SKILL.md' ? '' : '<button type="button" data-delete-skill-script>删除脚本</button>'}</span></header>
          <textarea id="skillSource" spellcheck="false">${escapeHtml(source)}</textarea>
        </div>
        <aside class="skill-file-outline"><span>文件结构</span><button class="${activeFile === 'SKILL.md' ? 'active' : ''}" data-skill-file="SKILL.md">SKILL.md</button>${fileButtons}<button class="skill-add-script" data-add-skill-script>＋ scripts/main.py</button><p>对象 Skill 默认由大模型执行，只需编写 SKILL.md；子 Skill 可选用脚本返回自然语言。</p></aside>
      </section>`;
    }
    if (state.activeTab === 'dependencies') return renderDependencies();
    if (state.activeTab === 'run') return `
      <section class="skill-run-layout">
        <div class="skill-run-input"><span class="skill-kicker">NATURAL LANGUAGE TEST</span><h2>直接描述当前情境</h2><p>输入框已按该 SKILL 定义里的「示例输入」预填贴近真实运行时的内容，可直接修改后运行。</p>
          <textarea id="skillRunInput" placeholder="可直接修改上方示例，再点运行">${escapeHtml(item.example_input || '例如：现在是早上 7 点，简刚刚醒来，她今天上午要去咖啡馆工作，请为她安排接下来的行动。')}</textarea>
          <details><summary>可选运行时上下文</summary><textarea id="skillRunContext" spellcheck="false" placeholder='{"agent_key":"jane","virtual_time":"2026-08-19T07:00:00+08:00"}'></textarea></details>
          <label for="skillRunModel">聊天模型</label><select class="control" id="skillRunModel"><option value="">请选择已配置模型</option></select><p>在左侧“模型”菜单中配置服务地址和 API Key。</p>
          <button class="btn btn-primary" id="skillRunButton" ${runRunning() ? 'disabled' : ''}>${runRunning() ? '正在运行…' : '使用当前模型配置运行'}</button>
        </div>
        <div class="skill-run-output" id="skillRunOutput">${runOutputHtml()}</div>
      </section>`;
    return '<section class="skill-history-panel" id="skillHistory"><div class="skill-run-placeholder"><strong>正在读取当前内容…</strong></div></section>';
  }

  function renderDependencies() {
    const dependencies = state.dependencies || { scripts: [], skills: [], mcp: [] };
    const scripts = dependencies.scripts.map(path => dependencyCard('SCRIPT', path, '技能私有的确定性实现，可独立测试。')).join('');
    const mcp = dependencies.mcp.map(name => dependencyCard('MCP', name, '正文引用的公共工具；由 Runtime 注入当前 Agent 身份并校验调用。')).join('');
    const skills = dependencies.skills.map(item => dependencyCard(item.kind === 'pack' ? 'SKILL PACK' : 'SKILL', item.name, item.missing ? '引用缺失，请修正 SKILL.md。' : item.description)).join('');
    return `<section class="skill-dependency-panel">
      <div class="skill-dependency-heading"><div><span class="skill-kicker">RUNTIME DEPENDENCIES</span><h2>Skill 调用的脚本与公共能力</h2><p>Skill 决定调用顺序；Script 执行确定性计算；感知、寻路、记忆和世界动作由 Runtime 公共 MCP 提供。下方列出正文引用的可用工具。</p></div><span class="skill-natural-badge">Skill 间：自然语言</span></div>
      <div class="skill-dependency-columns"><section><header><span>子 Skills</span><strong>${dependencies.skills.length}</strong></header>${skills || emptyDependency('未引用子 Skill')}</section><section><header><span>私有 Scripts</span><strong>${dependencies.scripts.length}</strong></header>${scripts || emptyDependency('暂无私有 Script')}</section><section><header><span>公共 MCP</span><strong>${dependencies.mcp.length}</strong></header>${mcp || emptyDependency('暂无 MCP 依赖')}</section></div>
      <div class="skill-mcp-note"><code>Runtime MCP</code><span>感知与寻路只读，记忆按 Agent 隔离；世界动作经 <b>world-act</b> 校验与 World Commit 提交。记忆、StepResult、检查点和审计信息保存在 Run 工作目录中，可封存为 <b>.garun</b>；Runtime 不连接 Studio 数据库。</span></div>
    </section>`;
  }

  function dependencyCard(type, name, description) {
    return `<article class="skill-dependency-card"><em>${escapeHtml(type)}</em><strong>${escapeHtml(name)}</strong><p>${escapeHtml(description || '')}</p></article>`;
  }

  function emptyDependency(text) {
    return `<div class="skill-dependency-empty">${escapeHtml(text)}</div>`;
  }

  function bindPanel() {
    if (state.activeTab === 'definition') {
      document.querySelectorAll('[data-skill-file]').forEach(button => button.addEventListener('click', () => {
        captureActiveSource();
        state.activeFile = button.dataset.skillFile;
        renderEditor();
      }));
      document.querySelector('[data-add-skill-script]')?.addEventListener('click', () => {
        captureActiveSource();
        state.current.script_sources ||= {};
        state.current.script_sources['scripts/main.py'] ||= 'def run(input_text, context):\n    """Return passive feedback or a deterministic Skill result."""\n    return input_text\n';
        state.activeFile = 'scripts/main.py';
        renderEditor();
      });
      document.querySelector('[data-delete-skill-script]')?.addEventListener('click', async () => {
        if (state.activeFile === 'SKILL.md') return;
        const path = state.activeFile;
        if (!await window.confirmResourceDeletion({ type: '私有脚本', name: path, message: '保存内容后生效。' })) return;
        delete state.current.script_sources[path];
        state.activeFile = 'SKILL.md';
        renderEditor();
      });
    }
    if (state.activeTab === 'run') {
      $('skillRunButton')?.addEventListener('click', runSkill);
      if (experimentScope) {
        $('skillRunModel').replaceChildren(new Option('使用当前实验的聊天模型', ''));
        $('skillRunModel').disabled = true;
      } else window.ModelWorkspace.loadChoices($('skillRunModel'), 'chat', state.selectedModelId);
      $('skillRunModel').onchange = event => { state.selectedModelId = event.target.value; };
    }
    if (state.activeTab === 'history') loadHistory().catch(report);
  }

  function captureActiveSource() {
    const source = host()?.querySelector('#skillSource');
    if (!source || !state.current) return;
    if (state.activeFile === 'SKILL.md') state.current.markdown = source.value;
    else {
      state.current.script_sources ||= {};
      state.current.script_sources[state.activeFile] = source.value;
    }
  }

  async function saveSkill() {
    captureActiveSource();
    const markdown = state.current?.markdown;
    if (typeof markdown !== 'string') return;
    const skillName = state.current.name;
    const generation = state.editorGeneration;
    const button = $('skillSave');
    button.disabled = true;
    button.textContent = '正在保存…';
    try {
      const saved = await api(`/api/studio/resources/skills/${encodeURIComponent(skillName)}`, {
        method: 'PUT', body: JSON.stringify({ markdown, scripts: state.current.script_sources || {}, ...(experimentScope ? { row_version: state.current.row_version } : {}) }),
      });
      const dependencies = await api(`/api/studio/resources/skills/${encodeURIComponent(skillName)}/dependencies`);
      if (generation !== state.editorGeneration || state.current?.name !== skillName) return;
      state.current = saved;
      state.dependencies = dependencies;
      renderEditor();
      toast('SKILL.md 与私有 Scripts 已保存');
    } catch (error) { report(error); button.disabled = false; button.textContent = '保存内容'; }
  }

  function runRunning() {
    return state.run?.status === 'running';
  }

  function runOutputHtml() {
    const run = state.run;
    if (run?.status === 'running') return '<div class="skill-running"><span></span><div><strong>模型正在执行 Skill</strong><small>可能会继续调用子 Skill，请稍候…</small></div></div>';
    if (run?.status === 'error') return `<div class="skill-run-error"><strong>运行失败</strong><span>${escapeHtml(run.message)}</span></div>${run.trace?.length ? `<div class="skill-trace"><strong>调用轨迹</strong>${run.trace.map(traceRow).join('')}</div>` : ''}`;
    if (run?.status === 'done') {
      const trace = run.result.trace || [];
      const prompts = trace
        .map((item, index) => (item.event === 'skill.start' && (item.system_prompt || item.user_prompt) ? { ...item, index } : null))
        .filter(Boolean);
      const promptHtml = prompts.length
        ? prompts.map((item, position) => `
          <details class="skill-prompt-item" ${position === 0 ? 'open' : ''}>
            <summary><b>${escapeHtml(item.skill)}</b><small>${item.index + 1}/${prompts.length} · system + user</small></summary>
            <div class="skill-prompt-block"><span>SYSTEM PROMPT</span><pre>${escapeHtml(item.system_prompt || '（未返回）')}</pre></div>
            <div class="skill-prompt-block"><span>USER PROMPT</span><pre>${escapeHtml(item.user_prompt || '（未返回）')}</pre></div>
          </details>`).join('')
        : '<div class="skill-prompt-empty">本次运行结果未包含 Prompt（请重启 web 服务加载新版 Skill 运行时后重试）。</div>';
      return `
        <div class="skill-result-text"><span>FINAL RESULT${run.result.model ? ` · ${escapeHtml(run.result.model)}` : ''}</span><p>${escapeHtml(run.result.output_text)}</p></div>
        <div class="skill-trace"><strong>调用轨迹</strong>${trace.map(traceRow).join('')}</div>
        <div class="skill-run-prompts"><strong>发送给模型的 Prompt</strong><p class="skill-prompt-hint">这就是本次执行实际发给模型的消息：system 来自 SKILL.md，user 来自试运行输入。对照这里的内容调整 SKILL.md 或示例输入，可以精确定位模型输出不符合预期的原因。</p>${promptHtml}</div>`;
    }
    return '<div class="skill-run-placeholder"><i>▶</i><strong>等待试运行</strong><span>调用轨迹、最终结果和实际发给模型的 Prompt 会显示在这里。</span></div>';
  }

  async function runSkill() {
    if (runRunning()) return toast('已有试运行正在进行，请稍候', true);
    const name = state.current.name;
    const generation = state.editorGeneration;
    const input = $('skillRunInput').value.trim();
    const modelPresetId = $('skillRunModel').value;
    if (!modelPresetId && !experimentScope) return toast('请选择聊天模型；尚未配置时请前往模型中心添加。', true);
    if (!input) return toast('请先描述一个要测试的情境', true);
    let context = {};
    const rawContext = $('skillRunContext').value.trim();
    try { if (rawContext) context = JSON.parse(rawContext); }
    catch { return toast('可选上下文不是有效 JSON', true); }
    state.run = { status: 'running' };
    const current = () => state.current?.name === name && state.editorGeneration === generation;
    const live = () => state.activeTab === 'run' && current() && $('skillRunOutput');
    if (live()) {
      $('skillRunButton').disabled = true;
      $('skillRunButton').textContent = '正在运行…';
      $('skillRunOutput').innerHTML = runOutputHtml();
    }
    try {
      const result = await api(`/api/studio/resources/skills/${encodeURIComponent(name)}/run`, {
        method: 'POST', body: JSON.stringify({ input_text: input, context, model_preset_id: modelPresetId }),
      });
      if (current()) state.run = { status: 'done', result };
    } catch (error) {
      if (current()) state.run = { status: 'error', message: error.message, trace: error.trace };
    }
    if (live()) {
      $('skillRunButton').disabled = false;
      $('skillRunButton').textContent = '使用当前模型配置运行';
      $('skillRunOutput').innerHTML = runOutputHtml();
    }
  }

  function traceRow(item, index) {
    const detail = item.child ? `${item.skill} → ${item.child}` : item.skill;
    return `<div><em>${String(index + 1).padStart(2, '0')}</em><span><b>${escapeHtml(item.event)}</b><small>${escapeHtml(detail)}</small></span></div>`;
  }

  async function loadHistory() {
    const result = await api(`/api/studio/resources/skills/${encodeURIComponent(state.current.name)}/history`);
    $('skillHistory').innerHTML = `<div class="skill-dependency-heading"><div><span class="skill-kicker">CURRENT CONTENT</span><h2>SKILL.md 当前内容</h2><p>公共 Skill 直接编辑；加入实验时复制完整内容与依赖闭包。</p></div></div><div class="skill-history-list">${result.items.map(item => `<article><span class="skill-history-dot"></span><div><strong>当前内容</strong><code>${escapeHtml(item.content_hash)}</code><small>${escapeHtml(item.updated_at)}</small></div></article>`).join('')}</div>`;
  }

  function showCreate(kind) {
    mount();
    if (!experimentScope) window.ResourceList.capture(kind === 'brain' ? 'brains' : 'skills');
    state.catalogGeneration += 1;
    state.editorGeneration += 1;
    state.page = kind === 'brain' ? 'brains' : 'skills';
    const inactiveHost = state.page === 'brains' ? $('skillWorkspace') : $('brainSkillWorkspace');
    if (inactiveHost) inactiveHost.replaceChildren();
    deactivateTopbar();
    const target = host();
    target.innerHTML = `<section class="skill-create-screen"><button class="skill-back" id="skillCreateBack">← 返回</button><div class="skill-create-card"><span class="skill-kicker">NEW ${kind === 'brain' ? 'BRAIN' : kind === 'pack' ? 'SKILL PACK' : 'SKILL'}</span><h1>从一句清楚的用途开始</h1><p>${experimentScope ? '创建当前实验专用的技能；不会加入基础配置。' : '创建后编辑基础 Skill，加入实验时复制文档、脚本与递归依赖。'}</p><label>稳定名称<input id="skillCreateName" placeholder="例如 daily-review"></label><label>用途说明<textarea id="skillCreateDescription" placeholder="说明它能做什么，以及在什么情境下应该使用。"></textarea></label><button class="btn btn-primary" id="skillCreateConfirm">${experimentScope ? '创建实验技能' : '创建基础技能'}</button></div></section>`;
    if (!experimentScope && kind !== 'brain') {
      $('skillCreateConfirm').insertAdjacentHTML('beforebegin', `<label>技能类型<select class="control" id="skillCreateKind"><option value="atomic">单个技能</option><option value="pack">技能包</option></select></label>`);
      $('skillCreateKind').value = kind;
    }
    $('skillCreateBack').addEventListener('click', backToCatalog);
    $('skillCreateConfirm').addEventListener('click', async () => {
      const name = $('skillCreateName').value.trim();
      const description = $('skillCreateDescription').value.trim();
      if (!name || !description) return toast('请填写稳定名称和用途说明', true);
      try {
        const created = await api('/api/studio/resources/skills', { method: 'POST', body: JSON.stringify({ name, description, kind: $('skillCreateKind')?.value || kind }) });
        state.current = created;
        state.dependencies = await api(`/api/studio/resources/skills/${encodeURIComponent(created.name)}/dependencies`);
        renderEditor();
        if (!experimentScope) window.ResourceList.route(state.page, {skill_key: created.name});
      } catch (error) { report(error); }
    });
  }

  function titleCase(value) { return String(value).split('-').map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' '); }
  function kindName(kind) { return kind === 'brain' ? '大脑入口 Skill' : kind === 'pack' ? 'Skill 包' : '原子 Skill'; }
  function toast(message, danger = false) {
    if (window.showToast) return window.showToast(message, danger ? '操作失败' : '已完成');
    console[danger ? 'error' : 'info'](message);
  }
  function report(error) { toast(error?.message || String(error), true); }

  window.SkillWorkspace = { activate, openSkill, showCreate, deactivateTopbar, invalidate() {state.catalogGeneration++; state.editorGeneration++; state.activationGeneration = (state.activationGeneration || 0) + 1;} };
  document.addEventListener('DOMContentLoaded', mount);
}());
