/* Shared author-resource list presentation and per-list navigation memory. */
(function () {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const memory = new Map();
  const key = name => `agent-foundry.list.${window.ResourceScope?.experimentId || 'author'}.${name}`;
  function read(name) {
    if (!memory.has(name)) {
      let saved;
      try { saved = JSON.parse(sessionStorage.getItem(key(name)) || '{}'); } catch (_) { saved = {}; }
      memory.set(name, {page: 1, query: '', kind: '', scroll: 0, ...saved});
    }
    return memory.get(name);
  }
  function remember(name, values = {}) {
    const state = Object.assign(read(name), values);
    try { sessionStorage.setItem(key(name), JSON.stringify(state)); } catch (_) { /* In-memory navigation still works. */ }
    return state;
  }
  function capture(name) {
    const panel = document.getElementById(`page-${name}`);
    if (!panel?.classList.contains('active') || !panel.querySelector('[data-resource-catalog]:not([hidden])')) return;
    remember(name, {scroll: window.scrollY || 0, restore: true});
  }
  function restore(name) {
    const state = read(name);
    if (!state.restore) return;
    requestAnimationFrame(() => {
      const panel = document.getElementById(`page-${name}`);
      if (!panel?.classList.contains('active') || !panel.querySelector('[data-resource-catalog]:not([hidden])')) return;
      window.scrollTo({top: state.scroll || 0, behavior: 'instant'});
      remember(name, {restore: false});
    });
  }
  const attrs = values => Object.entries(values || {}).map(([k,v]) => `${k}="${esc(v)}"`).join(' ');
  function row({name, description, meta = [], badges = [], icon = '◇', image = '', open = {}, actions = [], before = '', more = '', attributes = {}, className = ''}) {
    return `<article class="resource-row ${esc(className)}" ${attrs(attributes)}>${before}<span class="resource-thumb" aria-hidden="true">${image ? `<img src="${esc(image)}" alt="" loading="lazy" onerror="this.hidden=true;this.nextElementSibling.hidden=false"><span hidden>${esc(icon)}</span>` : esc(icon)}</span><div class="resource-row-main"><div class="resource-title-line"><button class="resource-name" ${attrs(open)}>${esc(name)}</button>${badges.filter(Boolean).map(b=>`<span class="resource-badge">${esc(b)}</span>`).join('')}</div><p class="resource-description">${esc(description || '暂无用途说明')}</p><div class="resource-meta">${meta.filter(v=>v != null && v !== '').map(v=>`<span>${esc(v)}</span>`).join('')}</div></div><div class="resource-row-actions"><button class="btn btn-sm resource-open" ${attrs(open)}>打开</button>${more || (actions.length ? `<details class="resource-more"><summary aria-label="${esc(name)}的更多操作">⋯</summary><div class="resource-menu">${actions.map(a=>`<button type="button" class="${a.danger?'resource-danger':''}" ${attrs(a.attributes)}>${esc(a.label)}</button>`).join('')}</div></details>` : '')}</div></article>`;
  }
  function slice(items, page = 1) {
    const total = items.length, totalPages = Math.max(1, Math.ceil(total / 5));
    page = Math.max(1, Math.min(totalPages, Number(page) || 1));
    return {items: items.slice((page - 1) * 5, page * 5), page, total, totalPages};
  }
  function pager(target, {page, total, totalPages = Math.max(1, Math.ceil(total / 5)), onPage}) {
    target.hidden = false;
    const pages = [...new Set([1, page - 1, page, page + 1, totalPages].filter(p=>p>=1 && p<=totalPages))].sort((a,b)=>a-b);
    const range = total ? `${(page-1)*5+1}–${Math.min(total,page*5)}` : '0';
    target.innerHTML = `<span>显示 ${range}，共 ${total} 条 · 每页 5 条</span><nav class="pagination" aria-label="列表分页"><button class="page-button" data-list-page="${page-1}" aria-label="上一页" ${page<=1?'disabled':''}>‹</button>${pages.map((p,i)=>`${i && p-pages[i-1]>1?'<span class="pagination-gap">…</span>':''}<button class="page-button${p===page?' active':''}" data-list-page="${p}" ${p===page?'aria-current="page"':''}>${p}</button>`).join('')}<button class="page-button" data-list-page="${page+1}" aria-label="下一页" ${page>=totalPages?'disabled':''}>›</button></nav>`;
    target.querySelectorAll('[data-list-page]').forEach(button=>button.onclick=()=>onPage(Number(button.dataset.listPage)));
  }
  function empty(name, filtered = false) {
    return `<div class="resource-list-state"><strong>${filtered?'没有符合条件的'+name:'暂无'+name}</strong><span>${filtered?'调整搜索词或筛选后重试。':'点击右上角“新建'+name+'”开始配置。'}</span></div>`;
  }
  function loading(target, name) {
    target.setAttribute('aria-busy', 'true');
    if (!target.querySelector('.resource-row')) target.innerHTML = `<div class="resource-list-state" role="status">正在加载${esc(name)}…</div>`;
  }
  function error(target, name, cause, retry) {
    target.removeAttribute('aria-busy');
    target.innerHTML = `<div class="resource-list-state" role="alert"><strong>${esc(name)}列表加载失败</strong><span>${esc(cause.message || String(cause))}</span><button class="btn btn-sm" data-list-retry>重新加载</button></div>`;
    target.querySelector('[data-list-retry]').onclick = retry;
  }
  function sorted(items) {
    return [...items].sort((a,b)=>(Date.parse(b.updated_at)||0)-(Date.parse(a.updated_at)||0) || String(a.name||'').localeCompare(String(b.name||''), 'zh-CN') || String(a.id||a.name).localeCompare(String(b.id||b.name)));
  }
  function route(name, values = {}) {
    const url = window.ResourceScope.pageUrl(name, values);
    if (`${location.pathname}${location.search}` !== url) history.pushState({}, '', url);
  }
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.resource-toolbar input, #mapSearch, #crowdSearch').forEach(input => {
      input.autocomplete = 'off';
      input.name = `catalog-${input.id}`;
    });
  });
  document.addEventListener('click', event => {
    document.querySelectorAll('.resource-more[open]').forEach(menu => {
      if (!menu.contains(event.target) || event.target.closest('.resource-menu button')) menu.open = false;
    });
  });
  document.addEventListener('toggle', event => {
    if (!event.target.matches?.('.resource-more[open]')) return;
    document.querySelectorAll('.resource-more[open]').forEach(menu => { if (menu !== event.target) menu.open = false; });
  }, true);
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('.resource-more[open]').forEach(menu => { menu.open = false; menu.querySelector('summary').focus(); });
  });
  window.ResourceList = {esc, row, slice, pager, empty, loading, error, sorted, read, remember, capture, restore, route};
})();
