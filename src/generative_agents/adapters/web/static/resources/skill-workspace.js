/**
 * Skill 工作区：浏览技能与大脑，编辑 SKILL.md，查看依赖并手动试运行。
 * state.current 保存当前文档，state.dependencies 保存解析后的调用关系，state.run 保存
 * 最近一次运行及 trace；切换目录时这些状态会被重新装载而不是隐式复用。
 */
(function () {
  'use strict';

  const state = {
    mounted: false,
    page: 'skills',
    kind: 'skill',
    query: '',
    items: [],
    current: null,
    dependencies: null,
    activeTab: 'definition',
    activeFile: 'SKILL.md',
    run: null,
    catalogGeneration: 0,
    editorGeneration: 0,
    selectedModelId: '',
    relationNodes: new Map(),
    relationGeneration: 0,
    dependencyGeneration: 0,
    relationsLoading: false,
    relationError: '',
    selectedInterface: '',
    savedMarkdown: '',
    savedScripts: '{}',
    interfacePicker: null,
    trialDrafts: new Map(),
    trialGeneration: 0,
    trialOptions: null,
    decisionDrafts: new Map(),
  };

  const $ = id => document.getElementById(id);
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]));

  let catalogSearchTimer;
  let relationTimer;
  const experimentScope = Boolean(window.ResourceScope?.experimentId);
  async function api(path, options = {}, scoped = true) {
    if (experimentScope && scoped) path = path.replace('/api/studio/resources', window.ResourceScope.base);
    options = scoped && !path.endsWith('/decision-compile') ? window.ResourceScope?.options(options) || options : options;
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body.detail;
      const error = new Error(detail?.message || (typeof detail === 'string' ? detail : '') || body.error?.message || `请求失败（${response.status}）`);
      error.status = response.status;
      error.trace = detail?.trace || [];
      throw error;
    }
    if (experimentScope && scoped && options.method && options.method !== 'GET' && !path.endsWith('/run') && !path.endsWith('/decision-compile')) window.ResourceScope.saved(body);
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
    $('createSkillBtn')?.addEventListener('click', () => showCreate('skill'));
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
    state.kind = page === 'brains' ? 'brain' : 'skill';
    deactivateTopbar();
    await loadCatalog();
    if (activation !== state.activationGeneration || page !== state.page) return;
    if (!experimentScope) {
      const name = new URLSearchParams(location.search).get('skill_key');
      if (name) await openSkill(name, false);
    }
  }

  async function loadCatalog() {
    return loadAuthorCatalog();
  }

  async function loadAuthorCatalog() {
    const list = window.ResourceList, page = state.page, saved = list.read(page);
    const generation = ++state.catalogGeneration;
    const isBrain = page === 'brains', label = isBrain ? '大脑' : '技能';
    state.items = [];
    state.query = saved.query;
    state.kind = isBrain ? 'brain' : 'skill';
    if (saved.kind) list.remember(page, {kind: ''});
    const target = host();
    target.innerHTML = `<div class="resource-catalog" data-resource-catalog><div class="resource-toolbar"><div class="search"><input class="control" id="skillSearchInput" aria-label="搜索${label}" placeholder="搜索${label}名称或说明…" value="${escapeHtml(state.query)}"></div></div><div class="resource-rows" id="authorSkillRows"></div><div class="resource-list-footer" id="authorSkillFooter"></div></div>`;
    $('skillSearchInput').oninput = event => {state.query = event.target.value; list.remember(page, {query: state.query, page: 1, scroll: 0}); clearTimeout(catalogSearchTimer); catalogSearchTimer=setTimeout(() => loadAuthorCatalog().catch(report), 250);};
    const grid = $('authorSkillRows');
    list.loading(grid, label);
    try {
      const result = await api(`/api/studio/resources/skills?${new URLSearchParams({page:saved.page, page_size:5, q:saved.query, kind:state.kind})}`);
      if (generation !== state.catalogGeneration || page !== state.page) return;
      state.items = result.items || [];
      state.catalogPage = result;
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
    const data = state.catalogPage || {items:state.items, page:1, total:state.items.length}, grid = $('authorSkillRows');
    if (!grid) return;
    list.remember(state.page, {page: data.page});
    grid.innerHTML = data.items.map(item => list.row({name: displayName(item), description: item.description, icon: state.page === 'brains' ? '⌬' : '◇',
      meta: [item.name, (item.children || []).length ? `${item.children.length} 个子技能` : '', (item.scripts || []).length ? `${item.scripts.length} 个脚本` : '文本技能'],
      open: {'data-skill-name': item.name}, actions: [{label: `删除${label}`, danger: true, attributes: {'data-delete-skill': item.name, 'data-delete-skill-label': displayName(item), 'data-delete-skill-kind': item.kind}}],
    })).join('') || list.empty(label, Boolean(query));
    grid.querySelectorAll('[data-skill-name]').forEach(button => button.onclick = () => openSkill(button.dataset.skillName).catch(report));
    grid.querySelectorAll('[data-delete-skill]').forEach(button => button.onclick = () => deleteSkill(button.dataset.deleteSkill, button.dataset.deleteSkillLabel, button.dataset.deleteSkillKind).catch(report));
    list.pager($('authorSkillFooter'), {...data, onPage: page => {list.remember(state.page, {page, scroll: 0}); loadAuthorCatalog().catch(report);}});
  }

  function host() {
    if (state.page === 'brains') return $('brainSkillWorkspace');
    return $('skillWorkspace');
  }

  async function openSkill(name, push = true) {
    state.catalogGeneration += 1;
    if (!experimentScope && push) window.ResourceList.capture(state.page);
    const generation = ++state.editorGeneration;
    const requestedPage = state.page;
    const detail = await api(`/api/studio/resources/skills/${encodeURIComponent(name)}`);
    if (generation !== state.editorGeneration || requestedPage !== state.page) return;
    state.current = detail;
    state.decisionDrafts.delete(detail.name);
    resetRelations(detail);
    state.activeTab = 'definition';
    state.activeFile = 'SKILL.md';
    state.run = null;
    state.trialOptions = null;
    state.trialGeneration += 1;
    renderEditor();
    if (!experimentScope && push) {
      window.ResourceList.route(state.page, {skill_key: name});
      window.scrollTo({top: 0, behavior: 'instant'});
    }
    await loadDependencies();
  }

  function renderEditor() {
    const target = host();
    const item = state.current;
    if (!target || !item) return;
    closeInterfacePicker(false);
    syncEditorTopbar(item);
    target.innerHTML = `
      <nav class="skill-editor-tabs-real">
        ${tabButton('definition', 'SKILL.md')}
        ${tabButton('run', '试运行')}
        <code>${escapeHtml(item.path)}</code>
      </nav>
      <main id="skillEditorPanel">${renderPanel()}</main>
      ${state.activeTab === 'definition' ? interfacePickerHtml() : ''}`;
    target.querySelectorAll('[data-skill-tab]').forEach(button => button.addEventListener('click', async () => {
      if (state.activeTab === 'definition') captureActiveSource();
      state.activeTab = button.dataset.skillTab;
      if (state.activeTab === 'run') await window.WorkspaceLoader.load('models');
      renderEditor();
    }));
    bindPanel();
    if (item.editable === false && state.activeTab === 'definition') {
      target.querySelectorAll('textarea, [data-add-skill-script], [data-delete-skill-script]').forEach(control => { control.disabled = true; });
    }
  }

  function editorKind(item) {
    return item.kind === 'brain' ? 'BRAIN' : 'SKILL';
  }

  function editorRootLabel() {
    if (state.page === 'brains') return '大脑列表';
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
    if ($('resourceTabs')) $('resourceTabs').hidden = true;
    $('defaultTopbarContext').hidden = true;
    $('skillEditorTopbarContext').hidden = false;
    $('skillEditorTopbarActions').hidden = false;
    $('hubActions').hidden = true;
    $('experimentActions').hidden = true;
    $('skillEditorBack').textContent = editorRootLabel();
    $('skillEditorTitle').textContent = displayName(item);
    $('skillEditorTitle').title = `稳定键：${item.name}`;
    $('skillEditorKind').textContent = editorKind(item);
    $('skillEditorDescription').textContent = item.description || '';
    $('skillEditorRevision').textContent = `内容 ${String(item.content_hash || '').slice(0, 12)}`;
    $('skillEditorBack').onclick = backToCatalog;
    $('skillSave').textContent = '保存内容';
    $('skillSave').disabled = item.editable === false;
    $('skillSave').onclick = saveSkill;
    $('skillDelete').hidden = item.editable === false || (experimentScope && item.kind === 'brain');
    $('skillDelete').textContent = item.kind === 'brain' ? '删除大脑' : '删除技能';
    $('skillDelete').onclick = () => deleteSkill(item.name, displayName(item), item.kind).catch(report);
  }

  async function deleteSkill(name, label = name, kind = state.kind) {
    const type = kind === 'brain' ? '大脑' : '技能';
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
    closeInterfacePicker(false);
    document.body.classList.remove('skill-editor-mode');
    if ($('resourceTabs')) $('resourceTabs').hidden = !['skills', 'brains'].includes(state.page);
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

  function skillHeader(item = state.current) {
    return String(item?.markdown || '').match(/^(\uFEFF?---[ \t]*\r?\n)([\s\S]*?)(\r?\n---[ \t]*(?:\r?\n|$))/);
  }

  function skillHeaderValue(name, item = state.current) {
    const header = skillHeader(item)?.[2] || '';
    const raw = header.match(new RegExp(`^[ \\t]*${name}[ \\t]*:[ \\t]*(.*)$`, 'm'))?.[1]?.trim();
    if (raw?.startsWith('"') && raw.endsWith('"')) {
      try { return JSON.parse(raw); } catch (_) { return raw; }
    }
    return raw;
  }

  function setSkillHeaderValue(name, value) {
    const item = state.current;
    if (!item) return;
    const match = skillHeader(item), newline = match?.[1].includes('\r\n') ? '\r\n' : '\n';
    const field = new RegExp(`^[ \\t]*${name}[ \\t]*:`);
    const lines = (match?.[2] || '').split(/\r?\n/).filter(line => !field.test(line));
    if (value !== undefined) lines.push(`${name}: ${value}`);
    const replacement = `${match?.[1] || `---${newline}`}${lines.filter((line, index) => line || index > 0).join(newline)}${match?.[3] || `${newline}---${newline}`}`;
    item.markdown = match ? replacement + item.markdown.slice(match[0].length) : replacement + item.markdown;
  }

  function skillLlmType(item = state.current) {
    return skillHeaderValue('llm_type', item) === 'decision' ? 'decision' : 'generative';
  }

  function skillDecisionQuestions(item = state.current) {
    const raw = skillHeaderValue('decision_questions', item);
    if (!raw) return {};
    let questions;
    try { questions = JSON.parse(raw); }
    catch (_) { throw new Error('自动解析结果格式有误，请重新解析 SKILL 正文。'); }
    if (!questions || Array.isArray(questions) || typeof questions !== 'object') throw new Error('自动解析结果格式有误，请重新解析 SKILL 正文。');
    return questions;
  }

  function decisionBody(markdown = state.current?.markdown) {
    const text = String(markdown || '');
    return text.replace(/^\uFEFF?---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/, '').trim();
  }

  function decisionDraft() {
    const name = state.current?.name;
    if (!state.decisionDrafts.has(name)) {
      let questions;
      try { questions = Object.values(skillDecisionQuestions()); } catch (_) { questions = []; }
      const parsed = Boolean(skillHeaderValue('decision_prompt_sha256') && questions.length
        && questions.every(question => question?.instructions === decisionBody()));
      state.decisionDrafts.set(name, {modelId: '', modelOptions: null, modelsLoading: false, modelError: '',
        compiledBody: parsed ? decisionBody() : null, compiledQuestions: parsed ? skillHeaderValue('decision_questions') : null,
        compiledHash: parsed ? skillHeaderValue('decision_prompt_sha256') : null, status: parsed ? 'done' : 'idle', error: '', request: null});
    }
    return state.decisionDrafts.get(name);
  }

  function decisionIsCurrent() {
    const draft = decisionDraft();
    return draft.compiledBody === decisionBody() && draft.compiledQuestions === skillHeaderValue('decision_questions')
      && draft.compiledHash === skillHeaderValue('decision_prompt_sha256') && Boolean(draft.compiledHash);
  }

  function setSkillLlmType(type) {
    if (!['generative', 'decision'].includes(type)) throw new Error('请选择生成式模型或决策模型。');
    if (type === 'decision' && state.current?.kind === 'brain') throw new Error('Brain 需要编排技能与接口，请使用生成式模型。');
    setSkillHeaderValue('llm_type', type);
    if (type === 'generative') {
      setSkillHeaderValue('decision_questions', undefined);
      setSkillHeaderValue('decision_prompt_sha256', undefined);
      const draft = decisionDraft();
      draft.request = null; draft.compiledBody = null; draft.compiledQuestions = null; draft.compiledHash = null;
      draft.status = 'idle'; draft.error = '';
    }
    if (state.current) state.current.llm_type = type;
  }

  function trialModelPurpose() { return skillLlmType() === 'decision' ? 'decision' : 'chat'; }
  function trialModelLabel() { return skillLlmType() === 'decision' ? '决策模型' : '生成式模型'; }

  function renderModelSettings() {
    const item = state.current, decision = skillLlmType(item) === 'decision', readonly = item.editable === false;
    return `<section class="skill-model-settings"><div class="skill-model-choice"><label for="skillLlmType">LLM 类型</label><select class="control" id="skillLlmType" ${readonly ? 'disabled' : ''}><option value="generative" ${decision ? '' : 'selected'}>生成式模型</option><option value="decision" ${decision ? 'selected' : ''} ${item.kind === 'brain' ? 'disabled' : ''}>决策模型</option></select>${item.kind === 'brain' ? '<p id="skillLlmTypeHint">Brain 使用生成式模型编排子技能与接口。</p>' : ''}</div><div id="skillDecisionSettings" ${decision ? '' : 'hidden'}>${decision ? renderDecisionSettings() : ''}</div></section>`;
  }

  function renderDecisionSettings() {
    const draft = decisionDraft(), readonly = state.current.editable === false;
    const parsed = decisionIsCurrent(), running = draft.status === 'running' && draft.request?.body === decisionBody();
    const model = experimentScope ? '<p class="skill-decision-model-hint">使用当前实验内的生成式模型解析。</p>'
      : `<label class="skill-decision-model" for="skillDecisionCompilerModel">用于解析的生成式模型<select class="control" id="skillDecisionCompilerModel" ${readonly || running || draft.modelsLoading ? 'disabled' : ''}><option value="">正在读取生成式模型…</option></select></label>`;
    return `<header class="skill-decision-heading"><div><strong>自动提取决策配置</strong><p>从 SKILL 正文识别是非或多类别问题及候选项。正文修改后，保存前会重新解析。</p></div><button type="button" class="btn btn-sm" id="skillCompileDecision" ${readonly || running || draft.modelsLoading ? 'disabled' : ''}>${parsed ? '重新解析' : '解析正文'}</button></header>${model}<div id="skillDecisionResult">${renderDecisionResult()}</div>`;
  }

  function renderDecisionResult() {
    const draft = decisionDraft(), parsed = decisionIsCurrent();
    const running = draft.status === 'running' && draft.request?.body === decisionBody();
    const status = running ? '正在解析 SKILL 正文…' : parsed ? '已解析' : draft.error ? '解析失败' : '需要解析';
    let questions;
    try { questions = skillDecisionQuestions(); }
    catch (_) { questions = {}; }
    const rows = parsed ? Object.entries(questions).map(([key, question]) => `<article class="skill-decision-result"><strong>${escapeHtml(key)} · ${escapeHtml({noul:'是 / 否', choice:'多类别', score:'评分'}[question.type] || question.type)}</strong>${question.criteria ? `<pre>${escapeHtml(JSON.stringify(question.criteria, null, 2))}</pre>` : '<p>返回“是”的概率（0～1）。</p>'}<details><summary>完整问题说明</summary><pre>${escapeHtml(question.instructions || '')}</pre></details></article>`).join('') : '';
    return `<p class="skill-decision-status" role="status">${status}</p>${draft.error || draft.modelError ? `<p class="skill-decision-error" role="alert">${escapeHtml(draft.error || draft.modelError)}</p>` : ''}${rows}`;
  }

  function syncModelSettings() {
    const select = $('skillLlmType'), settings = $('skillDecisionSettings');
    if (!select || !settings || state.activeTab !== 'definition') return;
    const decision = skillLlmType() === 'decision';
    select.value = decision ? 'decision' : 'generative';
    settings.hidden = !decision;
    if (!decision) {
      settings.innerHTML = ''; settings.decisionControlsBound = false;
    } else if (!settings.decisionControlsBound) {
      settings.innerHTML = renderDecisionSettings(); bindDecisionSettings();
    } else {
      $('skillDecisionResult').innerHTML = renderDecisionResult();
      const draft = decisionDraft(), button = $('skillCompileDecision');
      button.disabled = state.current.editable === false || draft.modelsLoading || Boolean(draft.request);
      button.textContent = decisionIsCurrent() ? '重新解析' : '解析正文';
      const compilerModel = $('skillDecisionCompilerModel');
      if (compilerModel) compilerModel.disabled = state.current.editable === false || draft.modelsLoading || Boolean(draft.request);
    }
  }

  function publishModelSettings() {
    if (state.activeFile === 'SKILL.md' && $('skillSource')) $('skillSource').value = state.current.markdown;
    invalidateTrialResult();
    updateOutline();
  }

  function bindDecisionSettings() {
    if (skillLlmType() !== 'decision' || state.current?.editable === false) return;
    const settings = $('skillDecisionSettings');
    if (settings.decisionControlsBound) return;
    settings.decisionControlsBound = true;
    $('skillCompileDecision')?.addEventListener('click', () => compileDecision(true));
    $('skillDecisionCompilerModel')?.addEventListener('change', event => {
      const draft = decisionDraft(); draft.modelId = event.target.value; draft.modelError = '';
    });
    loadDecisionModels();
  }

  async function loadDecisionModels() {
    if (experimentScope || skillLlmType() !== 'decision') return true;
    const draft = decisionDraft(), item = state.current, editor = state.editorGeneration, select = $('skillDecisionCompilerModel');
    if (!select?.isConnected) return Boolean(draft.modelsLoaded && draft.modelId && !draft.modelError);
    if (draft.modelSelect === select && draft.modelsPromise) return draft.modelsPromise;
    if (draft.modelSelect === select && draft.modelsLoaded) return true;
    const request = draft.modelRequest = {};
    draft.modelSelect = select; draft.modelsLoaded = false;
    const current = () => state.current === item && state.editorGeneration === editor && draft.modelRequest === request
      && $('skillDecisionCompilerModel') === select && select.isConnected;
    draft.modelsLoading = true;
    draft.modelsPromise = (async () => {
      try {
        await window.WorkspaceLoader?.load('models');
        await window.ModelWorkspace.loadChoices(select, 'chat', draft.modelId, true);
        if (!current()) return false;
        draft.modelId = select.value; draft.modelError = ''; draft.modelsLoaded = true;
        return true;
      } catch (error) {
        if (current()) draft.modelError = `生成式模型读取失败：${error.message}`;
        return false;
      } finally {
        if (draft.modelRequest === request) { draft.modelsLoading = false; draft.modelsPromise = null; }
        if (current()) syncModelSettings();
      }
    })();
    return draft.modelsPromise;
  }

  async function compileDecision(force = false) {
    captureActiveSource();
    if (skillLlmType() !== 'decision') return true;
    if (state.current?.editable === false) return decisionIsCurrent();
    if (!force && decisionIsCurrent()) return true;
    const item = state.current, editor = state.editorGeneration, draft = decisionDraft();
    const markdown = item.markdown, body = decisionBody(markdown);
    const scripts = {...(item.script_sources || {})}, scriptsJson = JSON.stringify(scripts);
    if (draft.request?.markdown === markdown && draft.request?.scriptsJson === scriptsJson) return draft.request.promise;
    const request = {markdown, body, scriptsJson};
    draft.request = request; draft.status = 'running'; draft.error = '';
    const current = () => state.current === item && state.editorGeneration === editor && draft.request === request
      && skillLlmType() === 'decision' && state.current.markdown === markdown
      && JSON.stringify(state.current.script_sources || {}) === scriptsJson;
    syncModelSettings();
    request.promise = (async () => {
      try {
        if (!await loadDecisionModels() || (!experimentScope && !draft.modelId)) throw new Error(draft.modelError || '请先配置并选择用于解析的生成式模型。');
        if (!current()) return false;
        syncModelSettings();
        const result = await api(`/api/studio/resources/skills/${encodeURIComponent(item.name)}/decision-compile`, {
          method: 'POST', body: JSON.stringify({markdown, scripts, ...(experimentScope ? {} : {model_preset_id: draft.modelId})}),
        });
        captureActiveSource();
        if (!current()) return false;
        if (decisionBody(result.markdown) !== body) throw new Error('解析结果改变了 SKILL 正文，请重试。');
        item.markdown = result.markdown; item.decision_questions = result.decision_questions;
        item.decision_prompt_sha256 = result.decision_prompt_sha256;
        draft.compiledBody = body; draft.compiledQuestions = skillHeaderValue('decision_questions');
        draft.compiledHash = skillHeaderValue('decision_prompt_sha256'); draft.status = 'done';
        publishModelSettings();
        return true;
      } catch (error) {
        if (current()) { draft.status = 'error'; draft.error = error.message; }
        return false;
      } finally {
        if (draft.request === request) { draft.request = null; if (draft.status === 'running') draft.status = 'idle'; }
        if (state.current === item && state.editorGeneration === editor) syncModelSettings();
      }
    })();
    syncModelSettings();
    return request.promise;
  }

  function renderPanel() {
    const item = state.current;
    if (state.activeTab === 'definition') {
      const scripts = item.script_sources || {};
      const activeFile = state.activeFile === 'SKILL.md' || Object.hasOwn(scripts, state.activeFile)
        ? state.activeFile : 'SKILL.md';
      state.activeFile = activeFile;
      const source = activeFile === 'SKILL.md' ? item.markdown : scripts[activeFile];
      return `
      <section class="skill-definition-layout">
        <div class="skill-markdown-editor">
          <header><span><i></i>${escapeHtml(activeFile)}</span><span class="skill-source-actions"><small>${activeFile === 'SKILL.md' ? 'Markdown' : 'Python'} · UTF-8</small><button type="button" class="skill-insert-interface" id="skillInsertInterface" ${activeFile !== 'SKILL.md' || item.editable === false ? 'disabled' : ''} title="在 SKILL.md 正文光标处插入规范接口名称">插入接口</button>${activeFile === 'SKILL.md' ? '' : '<button type="button" data-delete-skill-script>删除脚本</button>'}</span></header>
          <textarea id="skillSource" spellcheck="false">${escapeHtml(source)}</textarea>
        </div>
        <aside class="skill-definition-sidebar" aria-label="模型配置与调用关系">
          ${renderModelSettings()}
          <section class="skill-file-outline" id="skillFileOutline" aria-label="文件与调用关系">${renderOutline()}</section>
        </aside>
      </section>`;
    }
    if (state.activeTab === 'run') return `
      <section class="skill-run-layout">
        <div class="skill-run-input"><span class="skill-kicker">NATURAL LANGUAGE TEST</span><h2>直接描述当前情境</h2><p>${skillLlmType() === 'decision' ? '输入作为待判断的情境，完整 SKILL 正文作为问题说明。运行直接使用已解析的配置调用决策模型。' : '描述任务、目标和关注事项，输入原文将直接交给 SKILL。纯文本技能可独立试运行；需要环境接口时，选择实验和 Agent，使用该实验的初始场景。'}</p>
          <label for="skillRunInput">情境输入</label><textarea id="skillRunInput" placeholder="例如：描述当前位置和附近发生的事情。">${escapeHtml(trialDraft().input)}</textarea>
          <div class="skill-trial-environment" ${skillLlmType() === 'decision' ? 'hidden' : ''}><label for="skillRunExperiment">实验</label><select class="control" id="skillRunExperiment" disabled><option value="">正在读取实验…</option></select>
          <label for="skillRunAgent">Agent</label><select class="control" id="skillRunAgent" disabled><option value="">请先选择实验</option></select>
          <p id="skillTrialEnvironmentHint" aria-live="polite">依赖环境接口时，请选择实验和其中的 Agent。</p><button type="button" class="btn btn-sm" id="skillTrialRetry" hidden>重新读取实验</button></div>
          <label for="skillRunModel">${trialModelLabel()}</label><select class="control" id="skillRunModel"><option value="">正在读取模型…</option></select><p id="skillTrialModelHint">默认选中第一个已配置的${trialModelLabel()}，可按需更换。</p>
          <button class="btn btn-primary" id="skillRunButton" ${runRunning() ? 'disabled' : ''}>${runRunning() ? '正在运行…' : '开始试运行'}</button>
        </div>
        <div class="skill-run-output" id="skillRunOutput">${runOutputHtml()}</div>
      </section>`;
    return '';
  }

  function references(markdown) {
    const body = String(markdown || '').replace(/^\uFEFF?---\s*\r?\n[\s\S]*?\r?\n---\s*(?:\r?\n|$)/, '');
    const collect = expression => [...new Set([...body.matchAll(expression)].map(match => match[1]))];
    return {
      skills: collect(/(?<![A-Za-z0-9_$@/-])\$([a-z0-9]+(?:-[a-z0-9]+)*)(?![A-Za-z0-9_-])/g),
      interfaces: collect(/(?<![A-Za-z0-9_@/\-])@([A-Za-z0-9][A-Za-z0-9_-]*)(?![A-Za-z0-9_-])/g),
    };
  }

  function resetRelations(item) {
    closeInterfacePicker(false);
    clearTimeout(relationTimer);
    state.relationGeneration += 1;
    state.dependencyGeneration += 1;
    state.dependencies = null;
    state.relationNodes = new Map();
    state.relationsLoading = true;
    state.relationError = '';
    state.selectedInterface = '';
    state.savedMarkdown = item.markdown;
    state.savedScripts = JSON.stringify(item.script_sources || {});
  }

  function cacheRelations(nodes) {
    for (const node of nodes || []) {
      // A cycle leaf is contextual: do not reuse it as a complete subtree.
      if (!node.cycle) state.relationNodes.set(node.name, node);
    }
  }

  async function loadDependencies() {
    const item = state.current, editor = state.editorGeneration, generation = ++state.dependencyGeneration;
    if (!item) return;
    const current = () => state.current === item && editor === state.editorGeneration && generation === state.dependencyGeneration;
    try {
      const dependencies = await api(`/api/studio/resources/skills/${encodeURIComponent(item.name)}/dependencies`);
      if (!current()) return;
      state.dependencies = dependencies;
      cacheRelations(dependencies.skills);
      state.relationError = '';
      await refreshRelations();
    } catch (error) {
      if (!current()) return;
      state.relationError = `调用关系读取失败：${error.message}`;
      state.relationsLoading = false;
      updateOutline();
    }
  }

  async function refreshRelations() {
    const item = state.current, editor = state.editorGeneration, generation = ++state.relationGeneration;
    if (!item) return;
    const current = () => state.current === item && editor === state.editorGeneration && generation === state.relationGeneration;
    const missing = references(item.markdown).skills.filter(name => name !== item.name && !state.relationNodes.has(name));
    state.relationsLoading = Boolean(missing.length);
    updateOutline();
    await Promise.all(missing.map(async name => {
      try {
        const result = await api(`/api/studio/resources/skills/${encodeURIComponent(name)}/dependencies`);
        if (!current()) return;
        const node = {name, display_name: result.display_name, description: result.description, scripts: result.scripts || [], interfaces: result.interfaces || [], skills: result.skills || []};
        state.relationNodes.set(name, node);
      } catch (error) {
        if (!current()) return;
        state.relationNodes.set(name, error.status === 404 ? {name, missing: true} : {name, error: error.message});
      }
    }));
    if (!current()) return;
    state.relationsLoading = false;
    updateOutline();
  }

  function interfaceButton(name) {
    const entry = (state.dependencies?.interface_catalog || []).find(item => item.name === name);
    const known = Boolean(entry);
    const unresolved = !state.dependencies;
    return `<button type="button" class="skill-interface-reference ${known || unresolved ? '' : 'skill-relation-error'}" data-skill-interface="${escapeHtml(name)}">${entry?.display_name ? `<span>${escapeHtml(entry.display_name)}</span>` : ''}<code>@${escapeHtml(name)}</code>${known ? '' : `<small>${unresolved ? (state.relationError ? '说明暂不可用' : '读取说明中') : '未知接口'}</small>`}</button>`;
  }

  function renderRelation(node, ancestors = []) {
    const name = node.name;
    if (node.missing) return `<li class="skill-relation-error"><code>$${escapeHtml(name)}</code><small>缺失子 Skill</small></li>`;
    if (node.cycle || ancestors.includes(name)) return `<li class="skill-relation-error"><code>$${escapeHtml(name)}</code><small>循环引用：${escapeHtml([...ancestors, name].join(' → '))}</small></li>`;
    if (node.error) return `<li class="skill-relation-error"><code>$${escapeHtml(name)}</code><small>读取失败：${escapeHtml(node.error)}</small></li>`;
    if (node.loading) return `<li><code>$${escapeHtml(name)}</code><small>读取调用关系中…</small></li>`;
    const scripts = (node.scripts || []).map(path => `<li><code>${escapeHtml(path)}</code><small>私有文件</small></li>`).join('');
    const interfaces = (node.interfaces || []).map(interfaceButton).join('');
    const children = (node.skills || []).map(child => renderRelation(child, [...ancestors, name])).join('');
    return `<li><details open class="skill-relation-node"><summary>${node.display_name && node.display_name !== name ? `<span>${escapeHtml(node.display_name)}</span> ` : ''}<code>$${escapeHtml(name)}</code></summary>${node.description ? `<p>${escapeHtml(node.description)}</p>` : ''}${scripts || interfaces || children ? `<ul>${scripts}${interfaces ? `<li>${interfaces}</li>` : ''}${children}</ul>` : '<small>无显式调用</small>'}</details></li>`;
  }

  function renderInterfaceHelp() {
    if (!state.selectedInterface) return '';
    const name = state.selectedInterface;
    const item = (state.dependencies?.interface_catalog || []).find(entry => entry.name === name);
    const schemaHelp = (label, schema) => schema ? `<details><summary>${label}</summary><pre>${escapeHtml(JSON.stringify(schema, null, 2))}</pre></details>` : '';
    const objectHelp = item ? (item.object_input_schema
      ? `<p><strong>对象接口</strong>：${escapeHtml(item.object_description_zh || item.object_description || '对象使用独立身份和参数约束。')}</p>${schemaHelp('对象输入参数', item.object_input_schema)}`
      : '<p>此接口不对对象开放。</p>') : '';
    return `<section class="skill-interface-help" aria-live="polite"><header><strong>${item?.display_name ? `${escapeHtml(item.display_name)} · ` : ''}@${escapeHtml(name)}</strong><button type="button" data-close-interface aria-label="关闭接口说明">×</button></header>
      <p>${escapeHtml(item?.description_zh || item?.description || (state.dependencies ? '未找到此接口。请使用接口目录中的完整名称。' : '接口目录尚未载入，暂时无法核对名称。'))}</p>
      ${schemaHelp('Agent 输入参数', item?.input_schema)}${objectHelp}${schemaHelp('返回结果', item?.output_schema)}
      <p>身份由 Runtime 注入；接口校验参与者权限与每步动作限制。对象运行时可提供默认接口，此处调用关系仅展示正文显式引用。</p></section>`;
  }

  function renderOutline() {
    const item = state.current;
    if (!item) return '';
    const refs = references(item.markdown), scripts = item.script_sources || {};
    const dirty = item.markdown !== state.savedMarkdown || JSON.stringify(scripts) !== state.savedScripts;
    const files = Object.keys(scripts).sort().map(path => `<button type="button" class="${state.activeFile === path ? 'active' : ''}" data-skill-file="${escapeHtml(path)}">${escapeHtml(path)}</button>`).join('');
    const children = refs.skills.map(name => renderRelation(name === item.name ? {name, cycle: true} : state.relationNodes.get(name) || {name, loading: true}, [item.name])).join('');
    const catalog = (state.dependencies?.interface_catalog || []).map(entry => interfaceButton(entry.name)).join('');
    return `<h2>文件与调用关系</h2><p class="skill-relation-status">${dirty ? '当前编辑 · 尚未保存' : '已保存内容'}${state.relationsLoading ? ' · 读取关系中…' : ''}</p>
      <section class="skill-outline-section"><h3>当前 Skill 文件</h3><button type="button" class="${state.activeFile === 'SKILL.md' ? 'active' : ''}" data-skill-file="SKILL.md">SKILL.md</button>${files}<button type="button" class="skill-add-script" data-add-skill-script ${item.editable === false ? 'disabled' : ''}>＋ scripts/main.py</button></section>
      <section class="skill-outline-section"><h3>子 Skill <small>${refs.skills.length}</small></h3>${children ? `<ul class="skill-relation-tree">${children}</ul>` : '<p>正文未引用子 Skill。</p>'}</section>
      <section class="skill-outline-section"><h3>接口调用 <small>${refs.interfaces.length}</small></h3>${refs.interfaces.map(interfaceButton).join('') || '<p>正文未引用接口。</p>'}</section>
      ${state.relationError ? `<p class="skill-relation-error" role="alert">${escapeHtml(state.relationError)}</p><button type="button" data-retry-relations>重新读取</button>` : ''}
      ${renderInterfaceHelp()}<details class="skill-interface-directory"><summary>接口目录与说明</summary>${catalog || '<p>接口目录尚未载入。</p>'}</details>
      <p>引用写法：<code>$child-skill</code>、<code>@world-perceive</code>。当前引用随正文更新；展开的子 Skill 展示其已保存内容。文件列出实际附件。</p>`;
  }

  function updateOutline() {
    const outline = host()?.querySelector('#skillFileOutline');
    if (!outline || state.activeTab !== 'definition') return;
    outline.innerHTML = renderOutline();
    bindOutline(outline);
    updateInterfacePicker();
  }

  function bindOutline(outline) {
    outline.querySelectorAll('[data-skill-file]').forEach(button => button.addEventListener('click', () => {
      captureActiveSource();
      state.activeFile = button.dataset.skillFile;
      renderEditor();
    }));
    outline.querySelector('[data-add-skill-script]')?.addEventListener('click', () => {
      if (state.current.editable === false) return;
      captureActiveSource();
      state.current.script_sources ||= {};
      state.current.script_sources['scripts/main.py'] ||= 'def run(input_text, context):\n    """Return a deterministic Skill result."""\n    return input_text\n';
      state.activeFile = 'scripts/main.py';
      renderEditor();
    });
    outline.querySelectorAll('[data-skill-interface]').forEach(button => button.addEventListener('click', () => {
      state.selectedInterface = button.dataset.skillInterface;
      updateOutline();
      host()?.querySelector('.skill-interface-help')?.scrollIntoView({block: 'nearest'});
    }));
    outline.querySelector('[data-close-interface]')?.addEventListener('click', () => {state.selectedInterface = ''; updateOutline();});
    outline.querySelector('[data-retry-relations]')?.addEventListener('click', () => {
      state.relationNodes.clear();
      loadDependencies();
    });
  }

  function interfacePickerHtml() {
    return `<dialog class="skill-interface-picker" id="skillInterfacePicker" aria-labelledby="skillInterfacePickerTitle" aria-describedby="skillInterfacePickerHint">
      <header><div><h2 id="skillInterfacePickerTitle">插入接口</h2><p id="skillInterfacePickerHint">选择接口，插入正文光标处或替换选中文本；光标位于文档头部时追加到正文末尾。</p></div><button type="button" id="skillInterfacePickerClose" aria-label="关闭接口选择">×</button></header>
      <label class="skill-interface-search">搜索接口<input id="skillInterfaceSearch" type="search" placeholder="按中文名称、用途或接口名称搜索" autocomplete="off"></label>
      <div id="skillInterfacePickerList" class="skill-interface-picker-list" aria-live="polite"></div>
      <footer>插入引用后，请在正文说明调用条件、输入来源和结果用途。接口权限仍由运行时校验。</footer>
    </dialog>`;
  }

  function pickerIsCurrent(picker = state.interfacePicker) {
    return Boolean(picker && picker.item === state.current && picker.editor === state.editorGeneration
      && state.activeTab === 'definition' && state.activeFile === 'SKILL.md');
  }

  function openInterfacePicker() {
    if (!state.current || state.current.editable === false || state.activeTab !== 'definition' || state.activeFile !== 'SKILL.md') return;
    const source = $('skillSource'), dialog = $('skillInterfacePicker');
    if (!source || !dialog) return;
    captureActiveSource();
    state.interfacePicker = {item: state.current, editor: state.editorGeneration,
      start: source.selectionStart ?? source.value.length, end: source.selectionEnd ?? source.value.length,
      scrollTop: source.scrollTop, query: ''};
    $('skillInterfaceSearch').value = '';
    updateInterfacePicker();
    dialog.showModal();
    $('skillInterfaceSearch').focus();
    if (!state.dependencies) {
      state.relationError = '';
      updateInterfacePicker();
      return loadDependencies();
    }
  }

  function closeInterfacePicker(restoreFocus = true) {
    const picker = state.interfacePicker;
    if (!picker) return;
    const current = pickerIsCurrent(picker);
    state.interfacePicker = null;
    const dialog = $('skillInterfacePicker');
    if (dialog?.open) dialog.close();
    if (restoreFocus && current) {
      const source = $('skillSource');
      source?.focus({preventScroll: true});
      source?.setSelectionRange(picker.start, picker.end);
      if (source) source.scrollTop = picker.scrollTop;
    }
  }

  function updateInterfacePicker() {
    if (!pickerIsCurrent()) return;
    const list = $('skillInterfacePickerList');
    if (!list) return;
    if (!state.dependencies) {
      list.innerHTML = state.relationError
        ? `<p role="alert">${escapeHtml(state.relationError)}</p><button type="button" id="skillInterfacePickerRetry">重新读取接口目录</button>`
        : '<p role="status">正在读取接口目录…</p>';
      $('skillInterfacePickerRetry')?.addEventListener('click', () => {
        state.relationError = '';
        updateInterfacePicker();
        loadDependencies();
      });
      return;
    }
    const query = state.interfacePicker.query.toLocaleLowerCase();
    const catalog = state.dependencies.interface_catalog || [];
    const entries = catalog.filter(entry => [entry.name, entry.display_name, entry.description_zh || entry.description].join(' ').toLocaleLowerCase().includes(query));
    list.innerHTML = entries.map(entry => `<button type="button" class="skill-interface-option" data-insert-interface="${escapeHtml(entry.name)}"><span><strong>${escapeHtml(entry.display_name || entry.name)}</strong><code>@${escapeHtml(entry.name)}</code><small>${escapeHtml(entry.description_zh || entry.description || '')}</small>${entry.root_only ? '<small>仅根大脑或对象根技能可提交动作；子技能返回行动建议。</small>' : entry.object_available === false ? '<small>此接口不向对象开放。</small>' : ''}</span><b>插入</b></button>`).join('')
      || `<p>${catalog.length ? '没有匹配的接口，请换个关键词。' : '当前接口目录为空。'}</p>`;
    list.querySelectorAll('[data-insert-interface]').forEach(button => button.addEventListener('click', () => insertInterface(button.dataset.insertInterface)));
  }

  function insertInterface(name) {
    const picker = state.interfacePicker;
    if (!pickerIsCurrent(picker) || state.current.editable === false) return;
    if (!(state.dependencies?.interface_catalog || []).some(entry => entry.name === name)) return;
    const source = $('skillSource'), text = source.value;
    const frontmatter = text.match(/^\uFEFF?---\s*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/);
    const bodyStart = frontmatter ? frontmatter[0].length : 0;
    const inHeader = frontmatter && picker.start < bodyStart && picker.end <= bodyStart;
    const start = inHeader ? text.length : Math.max(bodyStart, Math.min(picker.start, text.length));
    const end = inHeader ? text.length : Math.max(start, Math.min(picker.end, text.length));
    const before = text.slice(0, start), after = text.slice(end);
    const prefix = frontmatter && (inHeader || start === bodyStart) && !before.endsWith('\n') ? '\n\n'
      : /[A-Za-z0-9_$@/\\-]$/.test(before) ? ' ' : '';
    const suffix = /^[A-Za-z0-9_-]/.test(after) ? ' ' : '';
    const inserted = `${prefix}@${name}${suffix}`;
    source.value = before + inserted + after;
    picker.start = picker.end = start + inserted.length;
    sourceChanged();
    closeInterfacePicker();
  }

  function sourceChanged() {
    captureActiveSource();
    syncModelSettings();
    if (state.activeFile === 'SKILL.md') {
      state.relationGeneration += 1;
      clearTimeout(relationTimer);
      relationTimer = setTimeout(() => refreshRelations(), 180);
    }
    updateOutline();
  }

  function bindPanel() {
    if (state.activeTab === 'definition') {
      bindOutline(host().querySelector('#skillFileOutline'));
      bindDecisionSettings();
      $('skillLlmType')?.addEventListener('change', async event => {
        if (state.current?.editable === false) return;
        captureActiveSource();
        try {
          setSkillLlmType(event.target.value); publishModelSettings(); syncModelSettings();
          if (skillLlmType() === 'decision') await compileDecision();
        }
        catch (error) { syncModelSettings(); report(error); }
      });
      $('skillSource')?.addEventListener('input', sourceChanged);
      $('skillInsertInterface')?.addEventListener('click', openInterfacePicker);
      $('skillInterfacePickerClose')?.addEventListener('click', () => closeInterfacePicker());
      $('skillInterfacePicker')?.addEventListener('cancel', event => {event.preventDefault(); closeInterfacePicker();});
      $('skillInterfaceSearch')?.addEventListener('input', event => {
        if (!pickerIsCurrent()) return;
        state.interfacePicker.query = event.target.value;
        updateInterfacePicker();
      });
      host().querySelector('[data-delete-skill-script]')?.addEventListener('click', async () => {
        if (state.activeFile === 'SKILL.md') return;
        const path = state.activeFile, item = state.current, editor = state.editorGeneration;
        if (!await window.confirmResourceDeletion({ type: '私有脚本', name: path, message: '保存内容后生效。' })) return;
        if (state.current !== item || editor !== state.editorGeneration) return;
        delete state.current.script_sources[path];
        state.activeFile = 'SKILL.md';
        renderEditor();
      });
    }
    if (state.activeTab === 'run') {
      $('skillRunButton')?.addEventListener('click', runSkill);
      $('skillRunInput').addEventListener('input', event => { trialDraft().input = event.target.value; invalidateTrialResult(); });
      $('skillRunExperiment').addEventListener('change', event => selectTrialExperiment(event.target.value));
      $('skillRunAgent').addEventListener('change', event => { trialDraft().agentKey = event.target.value; invalidateTrialResult(); });
      $('skillRunModel').addEventListener('change', event => selectTrialModel(event.target.value));
      $('skillTrialRetry').addEventListener('click', () => loadTrialEnvironment());
      loadTrialModels();
      if (skillLlmType() !== 'decision') loadTrialEnvironment();
      else state.trialOptions = null;
    }
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
    const item = state.current;
    if (typeof item?.markdown !== 'string' || item.editable === false) return;
    const generation = state.editorGeneration;
    const button = $('skillSave');
    button.disabled = true;
    button.textContent = skillLlmType() === 'decision' && !decisionIsCurrent() ? '正在解析并保存…' : '正在保存…';
    if (skillLlmType() === 'decision' && !await compileDecision()) {
      if (generation === state.editorGeneration && state.current === item) {
        button.disabled = false; button.textContent = '保存内容';
        toast(decisionDraft().error || '正文已变化，请重新解析后保存。', true);
      }
      return;
    }
    if (generation !== state.editorGeneration || state.current !== item) return;
    const markdown = state.current.markdown;
    const skillName = state.current.name;
    const scripts = {...(state.current.script_sources || {})};
    try {
      const saved = await api(`/api/studio/resources/skills/${encodeURIComponent(skillName)}`, {
        method: 'PUT', body: JSON.stringify({ markdown, scripts, ...(experimentScope ? { row_version: state.current.row_version } : {}) }),
      });
      if (generation !== state.editorGeneration || state.current?.name !== skillName) return;
      captureActiveSource();
      const draft = {markdown: state.current.markdown, scripts: {...(state.current.script_sources || {})}};
      state.current = saved;
      resetRelations(saved);
      if (draft.markdown !== markdown) state.current.markdown = draft.markdown;
      if (JSON.stringify(draft.scripts) !== JSON.stringify(scripts)) state.current.script_sources = draft.scripts;
      renderEditor();
      toast('SKILL.md 与私有 Scripts 已保存');
      await loadDependencies();
    } catch (error) {
      if (generation !== state.editorGeneration || state.current?.name !== skillName) return;
      report(error); button.disabled = false; button.textContent = '保存内容';
    }
  }

  function trialDraft() {
    const name = state.current?.name;
    if (!state.trialDrafts.has(name)) state.trialDrafts.set(name, {
      input: state.current?.example_input || '', experimentId: window.ResourceScope?.experimentId || '',
      agentKey: '', modelId: '',
    });
    const draft = state.trialDrafts.get(name), purpose = trialModelPurpose();
    if (draft.modelPurpose && draft.modelPurpose !== purpose) draft.modelId = '';
    draft.modelPurpose = purpose;
    return draft;
  }

  function trialInterfaces() {
    const names = new Set(references(state.current?.markdown).interfaces);
    const visit = node => { (node?.interfaces || []).forEach(name => names.add(name)); (node?.skills || []).forEach(visit); };
    visit(state.dependencies);
    return [...names];
  }

  function invalidateTrialResult() {
    state.trialGeneration += 1;
    state.run = null;
    if ($('skillRunOutput')) $('skillRunOutput').innerHTML = runOutputHtml();
    updateTrialControls();
  }

  function updateTrialControls() {
    if (state.activeTab !== 'run') return;
    const options = state.trialOptions;
    const button = $('skillRunButton');
    if (button) {
      button.disabled = runRunning() || Boolean(options?.loading || options?.agentsLoading || state.trialModelsLoading);
      button.textContent = runRunning() ? '正在运行…' : '开始试运行';
    }
    const hint = $('skillTrialEnvironmentHint');
    if (hint && !options?.error) hint.textContent = trialDraft().experimentId
      ? '人物身份、位置和时间取自实验初始场景；本次调试使用独立记忆，不改变原实验或已有运行。'
      : trialInterfaces().length ? '此 SKILL 调用链依赖环境接口，请选择实验和其中的 Agent。' : '不依赖环境接口时，可直接独立试运行。';
  }

  async function loadTrialModels() {
    const select = $('skillRunModel'), item = state.current, editor = state.editorGeneration, draft = trialDraft();
    const request = state.trialModelRequest = {}, selectedModelId = draft.modelId;
    const current = () => state.current === item && state.editorGeneration === editor && state.activeTab === 'run'
      && $('skillRunModel') === select && state.trialModelRequest === request;
    state.trialModelError = '';
    if (experimentScope) {
      select.replaceChildren(new Option(`使用当前实验的${trialModelLabel()}`, ''));
      select.disabled = true;
      state.trialModelsLoading = false;
      $('skillTrialModelHint').textContent = `使用实验内已保存的${trialModelLabel()}配置。`;
      return;
    }
    state.trialModelsLoading = true;
    updateTrialControls();
    try {
      await window.ModelWorkspace.loadChoices(select, trialModelPurpose(), selectedModelId, true);
      if (!current()) return;
      if (selectedModelId && select.value !== selectedModelId) throw new Error(`原先选择的${trialModelLabel()}暂不可用，请重新选择。`);
      draft.modelId = select.value;
    } catch (error) {
      if (current()) {
        state.trialModelError = error.message;
        $('skillTrialModelHint').textContent = `模型读取失败：${error.message} 请重新选择可用模型，或检查模型配置后重开试运行。`;
      }
    } finally {
      if (current()) { state.trialModelsLoading = false; updateTrialControls(); }
    }
  }

  function selectTrialModel(modelId) {
    trialDraft().modelId = modelId;
    if (modelId) {
      state.trialModelError = '';
      $('skillTrialModelHint').textContent = `已选择${trialModelLabel()}。`;
    }
    invalidateTrialResult();
  }

  async function loadTrialEnvironment() {
    const item = state.current, editor = state.editorGeneration, draft = trialDraft();
    const options = state.trialOptions = {loading: true, agentsLoading: false, experiments: [], agents: [], error: ''};
    const current = () => state.current === item && state.editorGeneration === editor && state.trialOptions === options && state.activeTab === 'run';
    $('skillRunExperiment').disabled = true;
    $('skillTrialRetry').hidden = true;
    updateTrialControls();
    try {
      const result = await api('/api/studio/resources/skill-trial/experiments', {}, false);
      if (!current()) return;
      options.experiments = result.items || [];
      const selected = options.experiments.find(value => (value.experiment_id || value.id) === draft.experimentId);
      if (draft.experimentId && !selected) { draft.experimentId = ''; draft.agentKey = ''; invalidateTrialResult(); }
      const select = $('skillRunExperiment');
      select.replaceChildren(new Option('独立试运行（不使用实验环境）', ''), ...options.experiments.map(value => new Option(value.name, value.experiment_id || value.id)));
      select.value = draft.experimentId;
      select.disabled = experimentScope;
      options.loading = false;
      if (experimentScope && !selected) throw new Error('当前实验暂不可用，请重新打开实验后重试');
      await loadTrialAgents(options);
    } catch (error) {
      if (!current()) return;
      options.error = error.message;
      $('skillTrialEnvironmentHint').textContent = `实验读取失败：${error.message}`;
      $('skillTrialRetry').hidden = false;
    } finally {
      if (current()) { options.loading = false; updateTrialControls(); }
    }
  }

  function selectTrialExperiment(experimentId) {
    const draft = trialDraft();
    draft.experimentId = experimentId;
    draft.agentKey = '';
    invalidateTrialResult();
    loadTrialAgents(state.trialOptions);
  }

  async function loadTrialAgents(options) {
    if (!options) return;
    const draft = trialDraft(), experimentId = draft.experimentId, item = state.current, editor = state.editorGeneration;
    const request = options.agentRequest = {};
    const current = () => state.current === item && state.editorGeneration === editor && state.trialOptions === options
      && options.agentRequest === request && draft.experimentId === experimentId && state.activeTab === 'run';
    options.agents = [];
    options.error = '';
    options.agentsLoading = Boolean(experimentId);
    const select = $('skillRunAgent');
    select.replaceChildren(new Option(experimentId ? '正在读取 Agent…' : '请先选择实验', ''));
    select.disabled = true;
    $('skillTrialRetry').hidden = true;
    updateTrialControls();
    if (!experimentId) return;
    try {
      const result = await api(`/api/studio/resources/skill-trial/experiments/${encodeURIComponent(experimentId)}/agents`, {}, false);
      if (!current()) return;
      options.agents = result.items || [];
      if (!options.agents.some(value => (value.agent_key || value.key) === draft.agentKey)) draft.agentKey = '';
      select.replaceChildren(new Option(options.agents.length ? '请选择执行此技能的 Agent' : '该实验尚未配置 Agent', ''), ...options.agents.map(value => new Option(value.name, value.agent_key || value.key)));
      select.value = draft.agentKey;
      select.disabled = !options.agents.length;
    } catch (error) {
      if (!current()) return;
      draft.agentKey = '';
      options.error = error.message;
      $('skillTrialEnvironmentHint').textContent = `Agent 读取失败：${error.message}`;
      $('skillTrialRetry').hidden = false;
    } finally {
      if (current()) { options.agentsLoading = false; updateTrialControls(); }
    }
  }

  function runRunning() {
    return state.run?.status === 'running';
  }

  function runOutputHtml() {
    const run = state.run;
    if (run?.status === 'running') return `<div class="skill-running" role="status"><span></span><div><strong>${skillLlmType() === 'decision' ? '决策模型正在回答问题' : '正在执行 SKILL'}</strong><small>${skillLlmType() === 'decision' ? '已发送输入情境和决策问题，请稍候…' : '已将输入原文交给技能，按技能说明执行并按需调用子技能和接口，请稍候…'}</small></div></div>`;
    if (run?.status === 'error') return `<div class="skill-run-error"><strong>运行失败</strong><span>${escapeHtml(run.message)}</span></div>${run.trace?.length ? `<div class="skill-trace"><strong>调用轨迹</strong>${run.trace.map(traceRow).join('')}</div>` : ''}`;
    if (run?.status === 'done') {
      const trace = run.result.trace || [];
      const prompts = trace.filter(item => item.event === 'skill.start' && (item.system_prompt || item.user_prompt || item.decision_request));
      const usage = run.result.model_usage;
      const usageHtml = Number.isInteger(usage?.logical_calls) && usage.logical_calls >= 0
        && Number.isInteger(usage?.physical_attempts) && usage.physical_attempts >= 0
        ? `<p class="skill-trial-result-context">模型调用：逻辑调用 ${usage.logical_calls} 次 · 物理尝试 ${usage.physical_attempts} 次（含重试）</p>` : '';
      const promptHtml = prompts.length
        ? prompts.map((item, position) => `
          <details class="skill-prompt-item" ${position === 0 ? 'open' : ''}>
            <summary><b>${escapeHtml(item.skill)}</b><small>${position + 1}/${prompts.length} · ${item.decision_request ? '决策模型请求' : 'system + user'}</small></summary>
            ${item.decision_request ? `<div class="skill-prompt-block"><span>决策模型请求 · MODEL / STATE / QUESTIONS</span><pre>${escapeHtml(JSON.stringify(item.decision_request, null, 2))}</pre></div>` : `<div class="skill-prompt-block"><span>SYSTEM PROMPT</span><pre>${escapeHtml(item.system_prompt || '（未返回）')}</pre></div>
            <div class="skill-prompt-block"><span>USER PROMPT</span><pre>${escapeHtml(item.user_prompt || '（未返回）')}</pre></div>`}
          </details>`).join('')
        : '<div class="skill-prompt-empty">本次运行结果未包含 Prompt（请重启 web 服务加载新版 Skill 运行时后重试）。</div>';
      return `
        <div class="skill-result-text"><span>试运行结果${run.result.model ? ` · ${escapeHtml(run.result.model)}` : ''}</span><p>${escapeHtml(run.result.output_text)}</p></div>
        ${usageHtml}
        ${run.result.environment ? '<p class="skill-trial-result-context">当前技能 · 实验初始场景 · 隔离试运行</p>' : ''}
        ${run.result.world_commit === 'NOT_COMMITTED' ? `<p class="skill-trial-result-context">${run.result.planned_action ? '世界动作：接口已接受，未提交到世界。' : '世界提交：不适用，本次未产生世界动作。'}本次调试使用独立记忆，不改变原实验或已有运行。</p>` : ''}
        <div class="skill-trace"><strong>调用轨迹</strong><p class="skill-prompt-hint">以下记录执行事件，事件数量不等于模型调用次数。</p>${trace.map(traceRow).join('')}</div>
        <div class="skill-run-prompts"><strong>${prompts.some(item => item.decision_request) ? '发送给模型的请求' : '发送给模型的 Prompt'}</strong><p class="skill-prompt-hint">展开查看实际发送的内容，便于核对情境、问题和模型返回结果。</p>${promptHtml}</div>`;
    }
    return '<div class="skill-run-placeholder"><i>▶</i><strong>等待试运行</strong><span>调用轨迹、最终结果和实际发给模型的 Prompt 会显示在这里。</span></div>';
  }

  async function runSkill() {
    if (runRunning()) return toast('已有试运行正在进行，请稍候', true);
    const name = state.current.name;
    const generation = state.editorGeneration;
    const trialGeneration = state.trialGeneration;
    const draft = trialDraft();
    const input = $('skillRunInput').value;
    const modelPresetId = $('skillRunModel').value;
    const decision = skillLlmType() === 'decision';
    const experimentId = decision && !experimentScope ? '' : draft.experimentId, agentKey = decision ? '' : draft.agentKey;
    draft.input = $('skillRunInput').value;
    if (state.current.markdown !== state.savedMarkdown || JSON.stringify(state.current.script_sources || {}) !== state.savedScripts) return toast('当前 SKILL 有未保存的修改，请先保存内容再试运行。', true);
    if (!input.trim()) return toast('请先描述一个要测试的情境', true);
    if (state.trialOptions?.loading || state.trialOptions?.agentsLoading || state.trialModelsLoading) return toast('正在读取试运行配置，请稍候。', true);
    if (state.trialModelError) return toast(`${trialModelLabel()}尚未就绪：${state.trialModelError} 请重新选择可用模型。`, true);
    if (!state.dependencies || state.relationsLoading) return toast('正在核对 SKILL 调用关系，请稍候；读取失败时请回到 SKILL.md 重试。', true);
    if (!decision && trialInterfaces().length && (!experimentId || !agentKey)) return toast('此 SKILL 调用链需要实验环境，请选择实验和其中的 Agent 后试运行。', true);
    if (!decision && experimentId && !agentKey) return toast('请选择实验中执行此技能的 Agent。', true);
    if (!decision && experimentId && !state.trialOptions?.agents.some(value => (value.agent_key || value.key) === agentKey)) return toast('所选 Agent 不属于当前实验，请重新选择。', true);
    if (!modelPresetId && !experimentScope && !experimentId) return toast(`请先在模型页面配置${trialModelLabel()}。`, true);
    state.run = { status: 'running' };
    const current = () => state.current?.name === name && state.editorGeneration === generation && state.trialGeneration === trialGeneration;
    const live = () => state.activeTab === 'run' && current() && $('skillRunOutput');
    if (live()) {
      $('skillRunButton').disabled = true;
      $('skillRunButton').textContent = '正在运行…';
      $('skillRunOutput').innerHTML = runOutputHtml();
    }
    try {
      const result = await api(`/api/studio/resources/skills/${encodeURIComponent(name)}/run`, {
        method: 'POST', body: JSON.stringify({ input_text: input, model_preset_id: modelPresetId || null, experiment_id: experimentId || null, agent_key: agentKey || null }),
      });
      if (current()) state.run = { status: 'done', result };
    } catch (error) {
      if (current()) state.run = { status: 'error', message: error.message, trace: error.trace };
    }
    if (live()) {
      updateTrialControls();
      $('skillRunOutput').innerHTML = runOutputHtml();
    }
  }

  function traceRow(item, index) {
    const names = {'trial.complete': '试运行结束', 'skill.start': '开始技能', 'skill.call': '调用子技能', 'skill.call.rejected': '子技能调用被拒绝', 'tool.call.rejected': '工具调用被拒绝', 'skill.result': '技能返回', 'interface.call': '接口执行'};
    const detail = item.child ? `${item.skill} → ${item.child}` : item.tool || item.skill || '';
    const source = item.event === 'skill.call' && item.input_from ? '<small>输入：已完成技能的结果原文</small>' : '';
    return `<details class="skill-trace-detail"><summary><em>${String(index + 1).padStart(2, '0')}</em><span><b>${escapeHtml(names[item.event] || item.event)}</b><small>${escapeHtml(detail)}</small>${source}</span></summary><pre>${escapeHtml(JSON.stringify(item, null, 2))}</pre></details>`;
  }

  function showCreate(kind = 'skill') {
    kind = kind === 'brain' ? 'brain' : 'skill';
    mount();
    if (!experimentScope) window.ResourceList.capture(kind === 'brain' ? 'brains' : 'skills');
    state.catalogGeneration += 1;
    state.editorGeneration += 1;
    state.page = kind === 'brain' ? 'brains' : 'skills';
    const inactiveHost = state.page === 'brains' ? $('skillWorkspace') : $('brainSkillWorkspace');
    if (inactiveHost) inactiveHost.replaceChildren();
    deactivateTopbar();
    const target = host();
    target.innerHTML = `<section class="skill-create-screen"><button class="skill-back" id="skillCreateBack">← 返回</button><div class="skill-create-card"><span class="skill-kicker">NEW ${kind === 'brain' ? 'BRAIN' : 'SKILL'}</span><h1>从一句清楚的用途开始</h1><p>${experimentScope ? '创建当前实验专用的技能；不会加入基础配置。' : '创建后编辑基础 Skill，加入实验时复制文档、脚本与递归依赖。'}</p><label>显示名称<input id="skillCreateDisplayName" placeholder="例如：每日回顾" maxlength="160"></label><label>英文稳定键<input id="skillCreateName" placeholder="例如 daily-review" autocapitalize="none" spellcheck="false"><small>用于 $daily-review 等引用；使用小写英文字母、数字和连字符。</small></label><label>用途说明<textarea id="skillCreateDescription" placeholder="说明它能做什么，以及在什么情境下应该使用。"></textarea></label><button class="btn btn-primary" id="skillCreateConfirm">${experimentScope ? '创建实验技能' : kind === 'brain' ? '创建大脑' : '创建技能'}</button></div></section>`;
    $('skillCreateBack').addEventListener('click', backToCatalog);
    $('skillCreateConfirm').addEventListener('click', async () => {
      const generation = state.editorGeneration;
      const name = $('skillCreateName').value.trim();
      const display_name = $('skillCreateDisplayName').value.trim();
      const description = $('skillCreateDescription').value.trim();
      if (!name || !display_name || !description) return toast('请填写显示名称、英文稳定键和用途说明', true);
      if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) return toast('英文稳定键只能使用小写英文字母、数字和单个连字符分隔', true);
      try {
        const created = await api('/api/studio/resources/skills', { method: 'POST', body: JSON.stringify({ name, display_name, description, kind }) });
        if (generation !== state.editorGeneration) return;
        state.current = created;
        resetRelations(created);
        state.activeTab = 'definition';
        state.activeFile = 'SKILL.md';
        renderEditor();
        if (!experimentScope) window.ResourceList.route(state.page, {skill_key: created.name});
        await loadDependencies();
      } catch (error) { report(error); }
    });
  }

  function titleCase(value) { return String(value).split('-').map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' '); }
  function displayName(item) { return item.display_name || titleCase(item.name); }
  function kindName(kind) { return kind === 'brain' ? '大脑' : '技能'; }
  function toast(message, danger = false) {
    if (window.showToast) return window.showToast(message, danger ? '操作失败' : '已完成');
    console[danger ? 'error' : 'info'](message);
  }
  function report(error) { toast(error?.message || String(error), true); }

  window.SkillWorkspace = { activate, openSkill, showCreate, deactivateTopbar, refreshCatalog() { if (!state.current) return loadCatalog(); }, invalidate() {closeInterfacePicker(false); state.catalogGeneration++; state.editorGeneration++; state.activationGeneration = (state.activationGeneration || 0) + 1;} };
  document.addEventListener('DOMContentLoaded', mount);
}());
