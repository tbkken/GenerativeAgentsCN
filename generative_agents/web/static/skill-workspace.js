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
    run: null,
  };

  const $ = id => document.getElementById(id);
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]));

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || body.error?.message || `请求失败（${response.status}）`);
    return body;
  }

  function mount() {
    if (state.mounted) return;
    const skillsPage = $('page-skills');
    const brainsPage = $('page-brains');
    const experimentBrainPage = $('page-experiment-brain');
    if (!skillsPage || !brainsPage || !experimentBrainPage) return;
    skillsPage.innerHTML = '<div id="skillWorkspace" class="skill-workspace"></div>';
    brainsPage.innerHTML = '<div id="brainSkillWorkspace" class="skill-workspace"></div>';
    experimentBrainPage.innerHTML = '<div id="experimentBrainSkillWorkspace" class="skill-workspace"></div>';
    state.mounted = true;
    $('createSkillBtn')?.addEventListener('click', () => showCreate('atomic'));
    $('createBrainBtn')?.addEventListener('click', () => showCreate('brain'));
  }

  async function activate(page = 'skills') {
    mount();
    state.page = page;
    state.current = null;
    state.kind = ['brains', 'experiment-brain'].includes(page) ? 'brain' : (state.kind === 'brain' ? 'atomic' : state.kind);
    deactivateTopbar();
    await loadCatalog();
  }

  async function loadCatalog() {
    const result = await api(`/api/v1/skills?kind=${encodeURIComponent(state.kind)}&q=${encodeURIComponent(state.query)}`);
    state.items = result.items || [];
    state.counts = result.counts || state.counts;
    renderCatalog();
  }

  function host() {
    if (state.page === 'brains') return $('brainSkillWorkspace');
    if (state.page === 'experiment-brain') return $('experimentBrainSkillWorkspace');
    return $('skillWorkspace');
  }

  function renderCatalog() {
    const target = host();
    if (!target) return;
    deactivateTopbar();
    const isBrain = ['brains', 'experiment-brain'].includes(state.page);
    const cards = state.items.map(item => skillCard(item)).join('');
    const experimentBrainSelector = state.page === 'experiment-brain'
      ? renderExperimentBrainSelector()
      : '';
    target.innerHTML = `
      ${experimentBrainSelector}
      <div class="skill-catalog-toolbar">
        <div class="skill-kind-tabs" ${isBrain ? 'hidden' : ''}>
          ${kindButton('atomic', '单个 Skill', state.counts.atomic)}
          ${kindButton('pack', 'Skill 包', state.counts.pack)}
        </div>
        <label class="skill-search"><span>⌕</span><input id="skillSearchInput" value="${escapeHtml(state.query)}" placeholder="搜索名称、用途或执行说明…"></label>
        <button class="btn btn-primary" id="skillCreateInline">＋ 新建${isBrain ? '大脑' : state.kind === 'pack' ? ' Skill 包' : ' Skill'}</button>
      </div>
      <div class="skill-catalog-summary"><strong>${state.items.length}</strong><span>${isBrain ? '个可用大脑' : state.kind === 'pack' ? '个技能包' : '个原子技能'} · 数据库 Revision 是唯一事实源</span></div>
      <section class="skill-card-grid">${cards || '<div class="skill-empty"><strong>没有找到 Skill</strong><span>换一个搜索词，或创建新的数据库 Skill。</span></div>'}</section>`;

    target.querySelectorAll('[data-skill-kind]').forEach(button => button.addEventListener('click', async () => {
      state.kind = button.dataset.skillKind;
      state.query = '';
      await loadCatalog();
    }));
    target.querySelectorAll('[data-skill-name]').forEach(card => card.addEventListener('click', () => openSkill(card.dataset.skillName)));
    $('skillCreateInline')?.addEventListener('click', () => showCreate(isBrain ? 'brain' : state.kind));
    $('experimentBrainApply')?.addEventListener('click', async () => {
      const selected = $('experimentBrainSelect')?.value;
      if (!selected) return;
      try {
        await window.ExperimentBrainBridge?.apply(selected);
        renderCatalog();
      } catch (error) { report(error); }
    });
    let searchTimer;
    $('skillSearchInput')?.addEventListener('input', event => {
      clearTimeout(searchTimer);
      state.query = event.target.value;
      searchTimer = setTimeout(() => loadCatalog().catch(report), 220);
    });
  }

  function renderExperimentBrainSelector() {
    const selected = window.ExperimentBrainBridge?.selected?.() || 'stanford-town-brain';
    const editable = window.ExperimentBrainBridge?.editable?.() !== false;
    const options = state.items.map(item => `<option value="${escapeHtml(item.name)}" ${item.name === selected ? 'selected' : ''}>${escapeHtml(titleCase(item.name))}</option>`).join('');
    const description = state.items.find(item => item.name === selected)?.description || '';
    return `<section class="experiment-brain-selector">
      <div><span>CURRENT EXPERIMENT BRAIN</span><strong>${escapeHtml(titleCase(selected))}</strong><p>${escapeHtml(description)}</p></div>
      <label>实验 Brain<select id="experimentBrainSelect" ${editable ? '' : 'disabled'}>${options}</select></label>
      <button class="btn btn-primary" id="experimentBrainApply" ${editable ? '' : 'disabled'}>应用到当前实验</button>
    </section>`;
  }

  function kindButton(kind, label, count) {
    return `<button class="${state.kind === kind ? 'active' : ''}" data-skill-kind="${kind}">${label}<span>${count || 0}</span></button>`;
  }

  function skillCard(item) {
    const type = item.kind === 'brain' ? 'BRAIN SKILL' : item.kind === 'pack' ? 'SKILL PACK' : 'SKILL';
    const children = item.children || [];
    const scripts = item.scripts || [];
    const flow = children.length
      ? children.slice(0, 4).map(name => `<span>$${escapeHtml(name)}</span>`).join('<i>→</i>')
      : '';
    return `<button class="skill-card-real" data-skill-name="${escapeHtml(item.name)}">
      <span class="skill-card-head"><em>${type}</em><span class="skill-live"><i></i>可用</span></span>
      <h2>${escapeHtml(titleCase(item.name))}</h2>
      <p>${escapeHtml(item.description)}</p>
      ${flow ? `<span class="skill-card-flow-real">${flow}</span>` : ''}
      <span class="skill-card-footer"><code>${escapeHtml(item.storage === 'database' ? `DB Revision #${item.revision_no || 1}` : `skills/${item.kind === 'atomic' ? 'atomic' : `${item.kind}s`}/${item.name}/`)}</code><span>${children.length ? `${children.length} 个子 Skill` : scripts.length ? `${scripts.length} 个 Script` : '文本 Skill'}</span></span>
    </button>`;
  }

  async function openSkill(name) {
    const [detail, dependencies] = await Promise.all([
      api(`/api/v1/skills/${encodeURIComponent(name)}`),
      api(`/api/v1/skills/${encodeURIComponent(name)}/dependencies`),
    ]);
    state.current = detail;
    state.dependencies = dependencies;
    state.activeTab = 'definition';
    state.run = null;
    renderEditor();
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
        ${tabButton('history', '版本')}
        <code>${escapeHtml(item.path)}</code>
      </nav>
      <main id="skillEditorPanel">${renderPanel()}</main>`;
    target.querySelectorAll('[data-skill-tab]').forEach(button => button.addEventListener('click', () => {
      state.activeTab = button.dataset.skillTab;
      renderEditor();
    }));
    bindPanel();
  }

  function editorKind(item) {
    return item.kind === 'brain' ? 'BRAIN SKILL' : item.kind === 'pack' ? 'SKILL PACK' : 'ATOMIC SKILL';
  }

  function editorRootLabel() {
    if (state.page === 'experiment-brain') return '实验大脑';
    if (state.page === 'brains') return '大脑中心';
    return '技能列表';
  }

  function backToCatalog() {
    state.current = null;
    deactivateTopbar();
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
    $('skillEditorRevision').textContent = `REV ${item.revision}`;
    $('skillEditorBack').onclick = backToCatalog;
    $('skillSave').onclick = saveSkill;
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
    if (state.activeTab === 'definition') return `
      <section class="skill-definition-layout">
        <aside class="skill-definition-guide">
          <span>WHY THIS FILE</span><h2>一份说明，就是一项能力</h2>
          <p>Frontmatter 让大脑发现它，正文告诉模型何时使用、如何执行、如何交接结果。</p>
          <dl><dt>名称</dt><dd><code>${escapeHtml(item.name)}</code></dd><dt>类型</dt><dd>${kindName(item.kind)}</dd><dt>结果交接</dt><dd>自然语言</dd></dl>
          <div class="skill-source-truth"><i>✓</i><div><strong>唯一事实源</strong><span>保存后写入数据库新 Revision；运行时物化为标准 SKILL.md 快照。</span></div></div>
        </aside>
        <div class="skill-markdown-editor">
          <header><span><i></i>SKILL.md</span><small>Markdown · UTF-8</small></header>
          <textarea id="skillMarkdown" spellcheck="false">${escapeHtml(item.markdown)}</textarea>
        </div>
        <aside class="skill-file-outline"><span>文件结构</span><button class="active">SKILL.md</button><button>${(item.scripts || []).length ? 'scripts/' : 'scripts/（按需）'}</button><button>agents/openai.yaml</button><p>引用子 Skill 时，在正文中写 <code>$skill-name</code>。</p></aside>
      </section>`;
    if (state.activeTab === 'dependencies') return renderDependencies();
    if (state.activeTab === 'run') return `
      <section class="skill-run-layout">
        <div class="skill-run-input"><span class="skill-kicker">NATURAL LANGUAGE TEST</span><h2>直接描述当前情境</h2><p>输入框已按该 SKILL 定义里的「示例输入」预填贴近真实运行时的内容，可直接修改后运行。</p>
          <textarea id="skillRunInput" placeholder="可直接修改上方示例，再点运行">${escapeHtml(item.example_input || '例如：现在是早上 7 点，简刚刚醒来，她今天上午要去咖啡馆工作，请为她安排接下来的行动。')}</textarea>
          <details><summary>可选运行时上下文</summary><textarea id="skillRunContext" spellcheck="false" placeholder='{"agent_key":"jane","virtual_time":"2026-08-19T07:00:00+08:00"}'></textarea></details>
          <button class="btn btn-primary" id="skillRunButton" ${runRunning() ? 'disabled' : ''}>${runRunning() ? '正在运行…' : '使用 Qwen3.8 27B 运行'}</button>
        </div>
        <div class="skill-run-output" id="skillRunOutput">${runOutputHtml()}</div>
      </section>`;
    return '<section class="skill-history-panel" id="skillHistory"><div class="skill-run-placeholder"><strong>正在读取版本…</strong></div></section>';
  }

  function renderDependencies() {
    const dependencies = state.dependencies || { scripts: [], skills: [], mcp: [] };
    const scripts = dependencies.scripts.map(path => dependencyCard('SCRIPT', path, '技能私有的确定性实现，可独立测试。')).join('');
    const mcp = dependencies.mcp.map(name => dependencyCard('MCP', name, '跨 Skill 共享的持久化公共能力。')).join('');
    const skills = dependencies.skills.map(item => dependencyCard(item.kind === 'pack' ? 'SKILL PACK' : 'SKILL', item.name, item.missing ? '引用缺失，请修正 SKILL.md。' : item.description)).join('');
    return `<section class="skill-dependency-panel">
      <div class="skill-dependency-heading"><div><span class="skill-kicker">RUNTIME DEPENDENCIES</span><h2>需要精确执行时才调用代码</h2><p>SKILL.md 保持可读；持久化、检索和确定性计算放进 Script，通过 MCP 公开复用。</p></div><span class="skill-natural-badge">Skill 间：自然语言</span></div>
      <div class="skill-dependency-columns"><section><header><span>子 Skills</span><strong>${dependencies.skills.length}</strong></header>${skills || emptyDependency('未引用子 Skill')}</section><section><header><span>私有 Scripts</span><strong>${dependencies.scripts.length}</strong></header>${scripts || emptyDependency('暂无私有 Script')}</section><section><header><span>公共 MCP</span><strong>${dependencies.mcp.length}</strong></header>${mcp || emptyDependency('暂无 MCP 依赖')}</section></div>
      <div class="skill-mcp-note"><code>POST /mcp</code><span>已提供 <b>memory-stream-append</b> 与 <b>memory-stream-search</b>；数据持久化到独立 SQLite，不塞进 Skill 文本。</span></div>
    </section>`;
  }

  function dependencyCard(type, name, description) {
    return `<article class="skill-dependency-card"><em>${escapeHtml(type)}</em><strong>${escapeHtml(name)}</strong><p>${escapeHtml(description || '')}</p></article>`;
  }

  function emptyDependency(text) {
    return `<div class="skill-dependency-empty">${escapeHtml(text)}</div>`;
  }

  function bindPanel() {
    if (state.activeTab === 'run') $('skillRunButton')?.addEventListener('click', runSkill);
    if (state.activeTab === 'history') loadHistory().catch(report);
  }

  async function saveSkill() {
    const markdown = $('skillMarkdown')?.value;
    if (typeof markdown !== 'string') return;
    const button = $('skillSave');
    button.disabled = true;
    button.textContent = '正在保存…';
    try {
      state.current = await api(`/api/v1/skills/${encodeURIComponent(state.current.name)}`, {
        method: 'PUT', body: JSON.stringify({ markdown }),
      });
      state.dependencies = await api(`/api/v1/skills/${encodeURIComponent(state.current.name)}/dependencies`);
      renderEditor();
      toast('SKILL.md 已写入文件系统');
    } catch (error) { report(error); button.disabled = false; button.textContent = '保存 SKILL.md'; }
  }

  function runRunning() {
    return state.run?.status === 'running';
  }

  function runOutputHtml() {
    const run = state.run;
    if (run?.status === 'running') return '<div class="skill-running"><span></span><div><strong>Qwen3.8 正在执行 Skill</strong><small>可能会继续调用子 Skill，请稍候…</small></div></div>';
    if (run?.status === 'error') return `<div class="skill-run-error"><strong>运行失败</strong><span>${escapeHtml(run.message)}</span></div>`;
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
        <div class="skill-result-text"><span>FINAL RESULT</span><p>${escapeHtml(run.result.output_text)}</p></div>
        <div class="skill-trace"><strong>调用轨迹</strong>${trace.map(traceRow).join('')}</div>
        <div class="skill-run-prompts"><strong>发送给模型的 Prompt</strong><p class="skill-prompt-hint">这就是本次执行实际发给模型的消息：system 来自 SKILL.md，user 来自试运行输入。对照这里的内容调整 SKILL.md 或示例输入，可以精确定位模型输出不符合预期的原因。</p>${promptHtml}</div>`;
    }
    return '<div class="skill-run-placeholder"><i>▶</i><strong>等待试运行</strong><span>调用轨迹、最终结果和实际发给模型的 Prompt 会显示在这里。</span></div>';
  }

  async function runSkill() {
    if (runRunning()) return toast('已有试运行正在进行，请稍候', true);
    const name = state.current.name;
    const input = $('skillRunInput').value.trim();
    if (!input) return toast('请先描述一个要测试的情境', true);
    let context = {};
    const rawContext = $('skillRunContext').value.trim();
    try { if (rawContext) context = JSON.parse(rawContext); }
    catch { return toast('可选上下文不是有效 JSON', true); }
    state.run = { status: 'running' };
    const live = () => state.activeTab === 'run' && state.current?.name === name && $('skillRunOutput');
    if (live()) {
      $('skillRunButton').disabled = true;
      $('skillRunButton').textContent = '正在运行…';
      $('skillRunOutput').innerHTML = runOutputHtml();
    }
    try {
      const result = await api(`/api/v1/skills/${encodeURIComponent(name)}/run`, {
        method: 'POST', body: JSON.stringify({ input_text: input, context }),
      });
      if (state.current?.name === name) state.run = { status: 'done', result };
    } catch (error) {
      if (state.current?.name === name) state.run = { status: 'error', message: error.message };
    }
    if (live()) {
      $('skillRunButton').disabled = false;
      $('skillRunButton').textContent = '使用 Qwen3.8 27B 运行';
      $('skillRunOutput').innerHTML = runOutputHtml();
    }
  }

  function traceRow(item, index) {
    const detail = item.child ? `${item.skill} → ${item.child}` : item.skill;
    return `<div><em>${String(index + 1).padStart(2, '0')}</em><span><b>${escapeHtml(item.event)}</b><small>${escapeHtml(detail)}</small></span></div>`;
  }

  async function loadHistory() {
    const result = await api(`/api/v1/skills/${encodeURIComponent(state.current.name)}/history`);
    $('skillHistory').innerHTML = `<div class="skill-dependency-heading"><div><span class="skill-kicker">FILE HISTORY</span><h2>SKILL.md 版本记录</h2><p>每次保存前自动快照旧文件；运行时始终读取当前文件。</p></div></div><div class="skill-history-list">${result.items.map((item, index) => `<article><span class="skill-history-dot"></span><div><strong>${index === 0 ? '当前版本' : '历史快照'}</strong><code>${escapeHtml(item.revision)}</code><small>${escapeHtml(item.created_at)}</small></div></article>`).join('')}</div>`;
  }

  function showCreate(kind) {
    mount();
    state.page = kind === 'brain' ? 'brains' : 'skills';
    deactivateTopbar();
    const target = host();
    target.innerHTML = `<section class="skill-create-screen"><button class="skill-back" id="skillCreateBack">← 返回</button><div class="skill-create-card"><span class="skill-kicker">NEW ${kind === 'brain' ? 'BRAIN' : kind === 'pack' ? 'SKILL PACK' : 'SKILL'}</span><h1>从一句清楚的用途开始</h1><p>创建后会生成数据库 Draft Revision；运行时再物化为标准 SKILL.md 快照。你可以继续补充子 Skill、Script 与 MCP。</p><label>稳定名称<input id="skillCreateName" placeholder="例如 daily-review"></label><label>用途说明<textarea id="skillCreateDescription" placeholder="说明它能做什么，以及在什么情境下应该使用。"></textarea></label><button class="btn btn-primary" id="skillCreateConfirm">创建数据库 Skill</button></div></section>`;
    $('skillCreateBack').addEventListener('click', () => activate(state.page).catch(report));
    $('skillCreateConfirm').addEventListener('click', async () => {
      const name = $('skillCreateName').value.trim();
      const description = $('skillCreateDescription').value.trim();
      if (!name || !description) return toast('请填写稳定名称和用途说明', true);
      try {
        const created = await api('/api/v1/skills', { method: 'POST', body: JSON.stringify({ name, description, kind }) });
        state.current = created;
        state.dependencies = await api(`/api/v1/skills/${encodeURIComponent(created.name)}/dependencies`);
        renderEditor();
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

  window.SkillWorkspace = { activate, openSkill, showCreate, deactivateTopbar };
  document.addEventListener('DOMContentLoaded', mount);
}());
