/* A single resource selector for config, experiment and Run packages. */
(function expose(root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.ResourceExchange = api; api.mount(root); }
}(typeof window !== 'undefined' ? window : globalThis, function createExchange() {
  'use strict';
  const pages = {maps:'map', 'public-agents':'agent', crowds:'crowd', skills:'skill', brains:'brain', 'model-catalog':'model'};
  const labels = {map:'地图', agent:'智能体', crowd:'人群', skill:'技能', brain:'大脑', model:'模型', spatial_asset:'空间素材', evaluator:'评估器'};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const identity = item => `${item.kind}:${item.key}`;
  const isBrain = item => item.kind === 'brain' || item.skill_kind === 'brain' || item.definition?.skill_kind === 'brain';
  function matches(item, kind) {
    if (kind === 'brain') return isBrain(item);
    if (kind === 'skill') return item.kind === 'skill' && !isBrain(item);
    return item.kind === kind;
  }
  const family = kind => ['agent', 'crowd'].includes(kind) ? ['agent', 'crowd'] : ['skill', 'brain'].includes(kind) ? ['skill', 'brain'] : [kind];
  const matchesFamily = (item, kind) => family(kind).some(value => matches(item, value));
  const automaticDependencies = kind => family(kind).length > 1;
  const completeExport = kind => ['crowd', 'skill', 'brain'].includes(kind);
  function defaultSelection(resources, roots, kind) {
    const primary = resources.filter(item => matchesFamily(item, kind));
    const rootKeys = new Set((roots || []).map(identity));
    const declared = primary.filter(item => rootKeys.has(identity(item)));
    if (declared.length) return declared.map(identity);
    const children = new Set(primary.flatMap(item => (item.dependencies || []).map(identity)));
    return primary.filter(item => !children.has(identity(item))).map(identity);
  }
  function summary(resources) {
    const counts = new Map();
    for (const item of resources) {
      const name = labels[isBrain(item) ? 'brain' : item.kind] || item.kind;
      counts.set(name, (counts.get(name) || 0) + 1);
    }
    return [...counts].map(([name, count]) => `${count} 个${name}`).join('、') || '尚未选择资源';
  }
  function selection(resources, keys, withDependencies = false) {
    const selected = new Set(keys), byKey = new Map(resources.map(item => [identity(item), item]));
    if (withDependencies) {
      const queue = [...selected];
      for (let index = 0; index < queue.length; index++) {
        for (const dependency of byKey.get(queue[index])?.dependencies || []) {
          const key = typeof dependency === 'string' ? dependency : identity(dependency);
          if (byKey.has(key) && !selected.has(key)) { selected.add(key); queue.push(key); }
        }
      }
    }
    return resources.filter(item => selected.has(identity(item))).map(({kind, key}) => ({kind, key}));
  }
  // A new file, mode or page invalidates every older read response.
  class RequestScope {
    constructor() { this.generation = 0; }
    next() { return ++this.generation; }
    current(token) { return this.generation === token; }
  }

  function mount(win) {
    const doc = win.document, $ = id => doc.getElementById(id), scope = new RequestScope();
    const API = '/api/studio/resource-exchange';
    let page = null, kind = null, mode = null, preview = null, exportItems = [], busy = false, exportReady = false;
    let activeController = null;
    const importingTokens = new Set();
    const dialog = $('resourceExchangeDialog');
    if (!dialog) return;
    const releasePreview = token => { if (token) request(`/preview/${encodeURIComponent(token)}`, {method:'DELETE'}).catch(() => {}); };
    const invalidate = () => {
      activeController?.abort(); activeController = null;
      if (preview?.token && !importingTokens.has(preview.token)) releasePreview(preview.token);
      preview = null;
      return scope.next();
    };
    const current = token => scope.current(token) && dialog.open;
    const status = (message, error = false) => { const node = $('resourceExchangeStatus'); node.textContent = message; node.setAttribute('role', error ? 'alert' : 'status'); };
    async function request(path, options = {}) {
      const response = await win.fetch(API + path, options);
      if (!response.ok) {
        const value = await response.json().catch(() => ({}));
        throw new Error(typeof value.detail === 'string' ? value.detail : value.detail?.message || `请求失败（${response.status}）`);
      }
      return response;
    }
    const includeImport = () => automaticDependencies(kind) || $('resourceExchangeDependencies').checked;
    const includeExport = () => completeExport(kind) || $('resourceExchangeExportDependencies').checked;
    function checks() { return [...dialog.querySelectorAll('[data-exchange-choice]:checked')].filter(input => input.dataset?.automatic !== 'true').map(input => input.value); }
    function selected() { return selection(preview?.resources || [], checks(), includeImport()); }
    function updateButton() {
      const explicit = new Set(checks()), chosen = selected(), byKey = new Map((preview?.resources || []).map(item => [identity(item), item]));
      const chosenKeys = new Set(chosen.map(identity));
      const conflicts = chosen.filter(item => byKey.get(identity(item))?.status === 'conflict');
      const button = $('resourceExchangeSubmit');
      button.disabled = busy || (mode === 'import' ? !chosen.length || conflicts.length > 0 : !exportReady || !$('resourceExchangeExportChoice').value);
      button.textContent = busy ? '正在处理…' : mode === 'import' ? `导入所选资源${chosen.length ? `（${chosen.length}）` : ''}` : '下载资源包';
      $('resourceExchangeConflict').textContent = conflicts.length ? `所选资源有 ${conflicts.length} 项内容冲突，请取消选择或在基础配置中处理冲突后重新预览。` : '';
      const committing = mode === 'import' && importingTokens.has(preview?.token);
      [$('resourceExchangeFile'), $('resourceExchangeDependencies')].forEach(input => { input.disabled = committing; });
      dialog.querySelectorAll('[data-exchange-choice]').forEach(input => {
        const automatic = chosenKeys.has(input.value) && !explicit.has(input.value);
        input.dataset.automatic = String(automatic);
        input.checked = chosenKeys.has(input.value);
        input.disabled = committing || automatic;
        const row = input.closest('.exchange-resource');
        row.dataset.automatic = String(automatic);
        const parents = chosen.map(item => byKey.get(identity(item))).filter(item => item?.dependencies?.some(dep => identity(dep) === input.value));
        row.querySelector('[data-exchange-reason]').textContent = automatic
          ? `自动加入：${parents.map(item => item.name || item.key).join('、')} 的关联资源`
          : explicit.has(input.value) ? '主动选择' : '';
      });
      const summaryNode = $('resourceExchangeSelectionSummary');
      if (summaryNode && mode === 'import' && preview) {
        const missing = chosen.flatMap(item => (byKey.get(identity(item))?.dependencies || []).filter(dep => !chosenKeys.has(identity(dep))).map(dep => `${item.key} → ${dep.key}`));
        summaryNode.textContent = `本次导入：${summary(chosen.map(item => byKey.get(identity(item))))}${missing.length ? `。关联待绑定：${missing.join('；')}` : ''}`;
      }
      $('resourceExchangeExportChoice').disabled = mode === 'export' && busy;
      $('resourceExchangeExportDependencies').disabled = mode === 'export' && busy;
    }
    function renderPreview() {
      const resources = preview.resources || [], primary = resources.filter(item => matchesFamily(item, kind)), others = resources.filter(item => !matchesFamily(item, kind));
      const defaults = new Set(defaultSelection(resources, preview.roots, kind));
      function row(item, checked) {
        const diagnostics = (item.diagnostics || []).map(value => typeof value === 'string' ? value : value.message || value.code || JSON.stringify(value));
        const badge = {new:'新增', reuse:'复用', conflict:'内容冲突'}[item.status] || item.status || '';
        return `<label class="exchange-resource"><input type="checkbox" data-exchange-choice value="${esc(identity(item))}" ${checked ? 'checked' : ''}><span><strong>${esc(item.name || item.key)}</strong><small>${esc(labels[isBrain(item) ? 'brain' : item.kind] || item.kind)} · ${esc(item.key)} · ${esc(badge)} · ${Number(item.attachment_count || 0)} 个附件</small><small data-exchange-reason></small>${item.description ? `<small>${esc(item.description)}</small>` : ''}${diagnostics.map(text => `<small class="exchange-warning">${esc(text)}</small>`).join('')}</span></label>`;
      }
      const kinds = [kind, ...family(kind).filter(value => value !== kind)];
      const groups = kinds.map(value => {
        const items = primary.filter(item => matches(item, value));
        return items.length ? `<section><h3>${labels[value]}（${items.length}）</h3>${items.map(item => row(item, defaults.has(identity(item)))).join('')}</section>` : '';
      }).join('');
      $('resourceExchangePreview').innerHTML = `<p>来源：${esc({config:'基础资源包', exp:'实验包', experiment:'实验包', run:'Run 包'}[preview.source_kind] || preview.source_kind)}。图片和脚本随所属资源一起导入。</p><p id="resourceExchangeSelectionSummary" class="exchange-selection-summary" role="status"></p>${groups || `<p>这个包中没有${family(kind).map(value => labels[value]).join('或')}。</p>`}${others.length ? `<details class="exchange-other"><summary>包内其他资源（${others.length}，可选）</summary>${others.map(item => row(item, false)).join('')}</details>` : ''}`;
      $('resourceExchangePreview').querySelectorAll('[data-exchange-choice]').forEach(input => input.addEventListener('change', updateButton));
      const diagnostics = preview.diagnostics || [];
      status(diagnostics.map(value => typeof value === 'string' ? value : value.message || value.code || '').filter(Boolean).join('\n') || '预览完成，请确认要导入的资源。');
      updateButton();
    }
    async function upload() {
      const file = $('resourceExchangeFile').files[0];
      const token = invalidate(); preview = null; busy = false;
      $('resourceExchangePreview').replaceChildren();
      updateButton();
      if (!file) { status('选择 config、.gaexp 或 .garun 文件。'); return; }
      busy = true; updateButton(); status('正在上传并校验资源包…');
      const form = new FormData(); form.append('file', file); form.append('kind', kind);
      try {
        // Keep this response observable when the dialog closes: the generated
        // server token must be released, even if this UI no longer needs it.
        const response = await request('/preview', {method:'POST', body:form});
        const result = await response.json();
        if (!current(token)) { releasePreview(result.token); return; }
        preview = result; busy = false; renderPreview();
      } catch (error) { if (current(token) && error.name !== 'AbortError') status(error.message, true); }
      finally { if (current(token)) { busy = false; updateButton(); } }
    }
    async function open(nextMode) {
      if (!kind || win.ResourceScope?.experimentId) return;
      const token = invalidate(); mode = nextMode; preview = null; exportItems = []; busy = false; exportReady = false;
      $('resourceExchangeTitle').textContent = `${mode === 'import' ? '导入' : '导出'}${mode === 'import' ? family(kind).map(value => labels[value]).join(' / ') : labels[kind]}资源包`;
      $('resourceExchangeImport').hidden = mode !== 'import';
      $('resourceExchangeExport').hidden = mode !== 'export';
      $('resourceExchangePreview').replaceChildren(); $('resourceExchangeFile').value = '';
      $('resourceExchangeDependencies').checked = false; $('resourceExchangeExportDependencies').checked = false;
      $('resourceExchangeImportOption').hidden = automaticDependencies(kind);
      $('resourceExchangeImportPolicy').hidden = !automaticDependencies(kind);
      $('resourceExchangeExportOption').hidden = completeExport(kind) || kind === 'agent';
      $('resourceExchangeExportPolicy').hidden = !completeExport(kind);
      $('resourceExchangeExportPolicy').textContent = kind === 'crowd' ? '自动打包全部成员智能体及其图片。' : '自动打包全部子技能依赖及文档、脚本和模板。';
      $('resourceExchangeExportChoice').replaceChildren(new Option('正在加载…', ''));
      $('resourceExchangeConflict').textContent = ''; status('');
      updateButton(); dialog.showModal();
      if (mode === 'import') return;
      busy = true; updateButton(); activeController = new AbortController();
      try {
        const response = await request(`/catalog?kind=${encodeURIComponent(kind)}`, {signal:activeController.signal});
        const result = await response.json();
        if (!current(token)) return;
        exportItems = result.items || [];
        $('resourceExchangeExportChoice').replaceChildren(new Option(exportItems.length ? '请选择要导出的资源' : '当前没有可导出的资源', ''));
        exportItems.forEach(item => $('resourceExchangeExportChoice').add(new Option(item.name || item.key, item.id || item.key)));
        let loadedPage=result.page || 1;
        let more=$('resourceExchangeCatalogMore');
        if (more) more.remove();
        if (loadedPage < (result.total_pages || 1)) {
          more=doc.createElement('button');more.id='resourceExchangeCatalogMore';more.type='button';more.className='btn btn-sm';more.textContent='加载更多资源';
          $('resourceExchangeExportChoice').insertAdjacentElement('afterend',more);
          more.onclick=async()=>{
            more.disabled=true;
            try {
              const response=await request(`/catalog?kind=${encodeURIComponent(kind)}&page=${loadedPage+1}&page_size=20`,{signal:activeController.signal});
              const result=await response.json();if (!current(token)) return;
              loadedPage=result.page;exportItems.push(...result.items);
              result.items.forEach(item=>$('resourceExchangeExportChoice').add(new Option(item.name || item.key,item.id || item.key)));
              more.hidden=loadedPage>=(result.total_pages||1);
            } catch(error) {if(current(token))status(error.message,true);} finally {more.disabled=false;}
          };
        }
      } catch (error) { if (current(token) && error.name !== 'AbortError') status(error.message, true); }
      finally { if (current(token)) { busy = false; updateButton(); } }
    }
    async function refreshCurrent(targetPage) {
      if (page !== targetPage && !family(pages[targetPage]).includes(pages[page])) return;
      if (page === 'maps') await win.MapWorkspace?.refresh();
      else if (page === 'crowds') await win.CrowdWorkspace?.loadCrowds();
      else if (page === 'public-agents') await win.CrowdWorkspace?.activateAgents();
      else if (page === 'skills' || page === 'brains') await win.SkillWorkspace?.refreshCatalog?.();
      else if (page === 'model-catalog') await win.ModelWorkspace?.refreshCatalog?.();
    }
    function completed(targetPage, title, message, error = false) {
      win.dispatchEvent(new CustomEvent('resource-exchange:completed', {detail:{page:targetPage, title, message, level:error ? 'error' : 'success'}}));
    }
    async function previewExport() {
      const token = invalidate(), id = $('resourceExchangeExportChoice').value;
      exportReady = false; busy = false; $('resourceExchangePreview').replaceChildren(); updateButton();
      if (!id) { status('请选择要导出的资源。'); return; }
      busy = true; updateButton(); status('正在核对打包资源与依赖…');
      activeController = new AbortController();
      try {
        const response = await request(`/export-preview/${encodeURIComponent(kind)}/${encodeURIComponent(id)}?include_dependencies=${includeExport()}`, {signal:activeController.signal});
        const result = await response.json();
        if (!current(token)) return;
        $('resourceExchangePreview').innerHTML = `<p class="exchange-selection-summary">本次打包：${esc(summary(result.resources || []))}</p><ul>${(result.resources || []).map(item => `<li>${esc(item.name || item.key)} · ${esc(labels[isBrain(item) ? 'brain' : item.kind])} · ${Number(item.attachment_count || 0)} 个附件</li>`).join('')}</ul>`;
        exportReady = true; status('资源及依赖校验通过，可以下载。');
      } catch (error) { if (current(token) && error.name !== 'AbortError') status(error.message, true); }
      finally { if (current(token)) { busy = false; updateButton(); } }
    }
    async function submit() {
      if (busy || $('resourceExchangeSubmit').disabled) return;
      const token = scope.generation, targetPage = page, targetKind = kind;
      busy = true; updateButton(); status(mode === 'import' ? '正在导入所选资源…' : '正在生成资源包…');
      if (mode === 'import') {
        const importToken = preview.token;
        importingTokens.add(importToken);
        updateButton();
        try {
          const response = await request('/imports', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token:importToken, selected:selected(), include_dependencies:includeImport()})});
          const result = await response.json();
          win.CrowdWorkspace?.resourcesImported?.(result);
          const pending = result.pending_dependencies || [];
          const message = `新增 ${result.imported?.length || 0} 项，复用 ${result.reused?.length || 0} 项${pending.length ? `；${pending.length} 项关联待绑定` : ''}。`;
          completed(targetPage, `${labels[targetKind]}导入完成`, message);
          if (current(token)) {
            preview = null; $('resourceExchangePreview').innerHTML = `<p>${esc(message)}</p>${pending.length ? `<ul>${pending.map(item => `<li>${esc(typeof item === 'string' ? item : item.message || `${labels[item.kind] || item.kind || ''} ${item.key || JSON.stringify(item)}`)}</li>`).join('')}</ul>` : ''}`;
            status('资源已保存到基础配置。');
          }
          try { await refreshCurrent(targetPage); }
          catch (error) { if (current(token)) status(`导入已完成，列表刷新失败：${error.message}。重新打开当前列表即可查看。`, true); }
        } catch (error) {
          completed(targetPage, `${labels[targetKind]}导入失败`, error.message, true);
          if (current(token)) status(error.message, true);
        } finally {
          importingTokens.delete(importToken);
          if (current(token)) { busy = false; updateButton(); }
          else releasePreview(importToken);
        }
        return;
      }
      const id = $('resourceExchangeExportChoice').value;
      const include = includeExport();
      try {
        const response = await request(`/export/${encodeURIComponent(targetKind)}/${encodeURIComponent(id)}?include_dependencies=${include}`);
        const blob = await response.blob();
        if (!current(token)) return;
        const item = exportItems.find(value => (value.id || value.key) === id);
        const url = URL.createObjectURL(blob), link = doc.createElement('a');
        link.href = url; link.download = `${(item?.name || item?.key || targetKind).replace(/[<>:"/\\|?*]/g, '_')}-config.zip`;
        doc.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        status('资源包已生成，下载已开始。');
      } catch (error) { if (current(token)) status(error.message, true); }
      finally { if (current(token)) { busy = false; updateButton(); } }
    }
    $('importResourcePackageBtn').addEventListener('click', () => open('import'));
    $('exportResourcePackageBtn').addEventListener('click', () => open('export'));
    $('resourceExchangeClose').addEventListener('click', () => dialog.close());
    $('resourceExchangeCancel').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => { invalidate(); preview = null; busy = false; });
    $('resourceExchangeFile').addEventListener('change', upload);
    $('resourceExchangeDependencies').addEventListener('change', updateButton);
    $('resourceExchangeExportChoice').addEventListener('change', previewExport);
    $('resourceExchangeExportDependencies').addEventListener('change', previewExport);
    $('resourceExchangeSubmit').addEventListener('click', submit);
    api.setPage = nextPage => {
      if (nextPage !== page) { invalidate(); if (dialog.open) dialog.close(); }
      page = nextPage; kind = pages[page] || null;
      const hidden = !kind || Boolean(win.ResourceScope?.experimentId);
      $('importResourcePackageBtn').hidden = hidden; $('exportResourcePackageBtn').hidden = hidden;
    };
  }
  const api = {mount, matches, matchesFamily, defaultSelection, selection, summary, RequestScope, setPage() {}};
  return api;
}));
